"""Virtual ECU network runtime. All actuator decisions execute in the C control tick."""
import argparse
import asyncio
import contextlib
from dataclasses import dataclass
import ipaddress
import json
from pathlib import Path
import secrets
import signal
import socket
import struct
import sys

from .core import Core, now_ms, OWNERS, ROUTINES
from .protocol import (CAN_ID, FAN_SERVICE, TELEMETRY_SERVICE, Endpoint, IdleTimeout,
                       ProtocolError, SDEntry, SomeIP, doip, parse_doip, parse_sd,
                       parse_signal, read_frame, sd_packet)


@dataclass
class Config:
    host: str = '127.0.0.1'
    doip_port: int = 13400
    signal_port: int = 32001
    fan_port: int = 30511
    telemetry_port: int = 30501
    event_port: int = 30512
    telemetry_event_port: int = 30502
    sd_port: int = 30490
    test_enable: bool = False
    stale_ms: int = 300
    s3_ms: int = 5000
    frame_ms: int = 1000
    alive_ms: int = 10000
    fault: int = 0
    trace: str | None = None
    can_interface: str | None = None


class Trace:
    def __init__(self, path, boot_id):
        self.path = Path(path) if path else Path('artifacts/v3') / f'ecu-{boot_id:08x}.jsonl'
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = self.path.open('x', encoding='utf-8')
        self.boot_id, self.sequence = boot_id, 0

    def next(self):
        self.sequence += 1
        return self.sequence

    def emit(self, stage, trace=0, **fields):
        record = dict(t_ms=now_ms(), boot_id=self.boot_id, trace=trace, stage=stage, **fields)
        self.file.write(json.dumps(record, ensure_ascii=False) + '\n')
        self.file.flush()


class Datagram(asyncio.DatagramProtocol):
    def __init__(self, callback):
        self.callback = callback

    def datagram_received(self, data, peer):
        self.callback(data, peer)


class VirtualECU:
    VIN = b'VIRTUALFAN0000001'
    LOGICAL = 1

    def __init__(self, config):
        ipaddress.IPv4Address(config.host)
        if config.host == '0.0.0.0':
            raise ValueError('Use a specific local IPv4 address so advertised endpoints are reachable')
        for value in (config.stale_ms, config.s3_ms, config.frame_ms, config.alive_ms):
            if not 50 <= value <= 600000:
                raise ValueError('Timer values must be 50..600000 ms')
        self.config = config
        self.boot_id = secrets.randbits(32) or 1
        self.core = Core(config.test_enable, config.s3_ms, config.stale_ms)
        try:
            self.trace = Trace(config.trace, self.boot_id)
        except Exception:
            self.core.close()
            raise
        self.core.fault(config.fault)
        self.ports, self.servers, self.transports = {}, [], {}
        self.writers, self.handlers = set(), set()
        self.sources, self.subscriptions = {}, {}
        self.connection_id = 0
        self.sd_session, self.sd_reboot = 1, True
        self.tasks = []
        self.signal_trace = self.service_trace = self.diag_trace = 0
        self.last_snapshot, self.last_event_ms = None, 0
        self.can_socket = None

    async def start(self):
        try:
            await self._tcp('doip', self.config.doip_port, self.handle_doip)
            await self._udp('doip', self.ports['doip'], self.discovery)
            await self._tcp('fan', self.config.fan_port,
                            lambda r, w: self.handle_someip(r, w, FAN_SERVICE))
            await self._tcp('telemetry', self.config.telemetry_port,
                            lambda r, w: self.handle_someip(r, w, TELEMETRY_SERVICE))
            await self._udp('signal', self.config.signal_port, self.receive_signal)
            await self._udp('event', self.config.event_port, self.ignore_event_input)
            await self._udp('telemetry_event', self.config.telemetry_event_port, self.ignore_event_input)
            await self._udp('sd', self.config.sd_port, self.receive_sd)
            if self.config.can_interface:
                if not hasattr(socket, 'AF_CAN'):
                    raise RuntimeError('SocketCAN requires a Linux runtime')
                self.can_socket = socket.socket(socket.AF_CAN, socket.SOCK_RAW, socket.CAN_RAW)
                self.can_socket.bind((self.config.can_interface,))
                self.can_socket.setblocking(False)
                self.tasks.append(asyncio.create_task(self.read_can()))
            self.tasks.append(asyncio.create_task(self.control_loop()))
            self.trace.emit('READY', ports=self.ports, host=self.config.host)
            return dict(host=self.config.host, ports=self.ports, boot_id=self.boot_id,
                        trace=str(self.trace.path.resolve()))
        except Exception:
            await self.close()
            raise

    async def _tcp(self, name, port, callback):
        server = await asyncio.start_server(callback, self.config.host, port, limit=8192)
        self.servers.append(server)
        self.ports[name] = server.sockets[0].getsockname()[1]

    async def _udp(self, name, port, callback):
        transport, _ = await asyncio.get_running_loop().create_datagram_endpoint(
            lambda: Datagram(callback), local_addr=(self.config.host, port), family=socket.AF_INET)
        self.transports[name] = transport
        self.ports.setdefault(name, transport.get_extra_info('sockname')[1])

    async def close(self):
        for server in self.servers:
            server.close()
        for server in self.servers:
            await server.wait_closed()
        for task in self.tasks:
            task.cancel()
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)
        for writer in tuple(self.writers):
            writer.close()
        pending = [task for task in self.handlers if task is not asyncio.current_task()]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        if self.can_socket:
            self.can_socket.close()
        for transport in self.transports.values():
            transport.close()
        if self.core.handle:
            self.core.tick(now_ms())
            self.trace.emit('SHUTDOWN', state=self.core.state(now_ms()))
            self.core.close()
        self.trace.file.close()

    async def send(self, writer, data):
        writer.write(data)
        await asyncio.wait_for(writer.drain(), 0.5)

    def receive_signal(self, data, peer):
        try:
            self.accept_signal(parse_signal(data), str(peer), data)
        except ProtocolError as error:
            self.trace.emit('REJECT_SIGNAL', error=str(error), peer=str(peer))

    def accept_signal(self, payload, source, wire=None):
        trace = self.trace.next()
        self.trace.emit('RX_SIGNAL', trace, source=source, wire=(wire or payload).hex())
        result = self.core.signal(payload, now_ms())
        raw, speed, counter, valid, _ = struct.unpack('<HHBBH', payload)
        self.trace.emit('UNPACK', trace, temperature_raw=raw & 4095, speed_raw=speed,
                        counter=counter, valid=valid, result=result)
        if result == 0:
            self.signal_trace = trace
            state = self.core.state(now_ms())
            self.trace.emit('DECODE', trace, temperature_centi_c=state['temperature_centi_c'],
                            speed_raw=state['speed_raw'], quality=state['quality'])

    async def read_can(self):
        loop = asyncio.get_running_loop()
        while True:
            packet = await loop.sock_recv(self.can_socket, 16)
            if len(packet) == 16:
                can_id, length, data = struct.unpack('=IB3x8s', packet)
                if can_id == CAN_ID and length == 8:
                    self.accept_signal(data, self.config.can_interface)

    def ignore_event_input(self, data, peer):
        self.trace.emit('REJECT_EVENT_INPUT', peer=str(peer), bytes=len(data))

    def discovery(self, data, peer):
        try:
            version, kind, payload = parse_doip(data, discovery=True)
            if kind in (1, 2, 3):
                if ((kind == 1 and payload) or (kind == 2 and payload != b'ELNFAN') or
                        (kind == 3 and payload != self.VIN)):
                    return
                body = self.VIN + struct.pack('!H', self.LOGICAL) + b'ELNFAN' + b'ELNLAB' + b'\x00'
                self.transports['doip'].sendto(doip(4, body, 3 if version == 255 else version), peer)
            elif kind == 0x4001 and not payload:
                body = struct.pack('!BBBI', 1, 8, len(self.sources), 4096)
                self.transports['doip'].sendto(doip(0x4002, body, 3 if version == 255 else version), peer)
        except (ProtocolError, ValueError) as error:
            self.trace.emit('REJECT_DISCOVERY', peer=str(peer), error=str(error))

    async def handle_doip(self, reader, writer):
        task = asyncio.current_task()
        self.handlers.add(task)
        self.connection_id += 1
        connection, source, version, awaiting_alive = self.connection_id, None, 3, False
        if len(self.writers) >= 24:
            writer.close()
            self.handlers.discard(task)
            return
        self.writers.add(writer)
        try:
            while True:
                try:
                    data = await read_frame(reader, 'doip', 2 if awaiting_alive else self.config.alive_ms / 1000,
                                            self.config.frame_ms / 1000)
                except IdleTimeout:
                    if awaiting_alive or source is None:
                        break
                    await self.send(writer, doip(7, version=version))
                    awaiting_alive = True
                    continue
                version, kind, payload = parse_doip(data)
                if awaiting_alive and kind == 8:
                    if payload != struct.pack('!H', source):
                        break
                    awaiting_alive = False
                    continue
                trace = self.trace.next()
                self.trace.emit('RX_DOIP', trace, connection=connection, kind=kind, wire=data.hex())
                if kind == 5:
                    if len(payload) not in (7, 11) or payload[3:7] != b'\x00' * 4:
                        raise ProtocolError('Routing activation length/reserved')
                    candidate, activation = struct.unpack_from('!HB', payload)
                    code = 0x10
                    if not 0x0e00 <= candidate <= 0x0fff:
                        code = 0
                    elif source is not None and candidate != source:
                        code = 2
                    elif candidate in self.sources and self.sources[candidate] != connection:
                        code = 3
                    elif activation != 0:
                        code = 6
                    elif source is None and self.core.open(connection, now_ms()) < 0:
                        code = 1
                    if code == 0x10:
                        source = candidate
                        self.sources[source] = connection
                    response = struct.pack('!HHB4x', candidate, self.LOGICAL, code)
                    await self.send(writer, doip(6, response, version))
                    if code != 0x10:
                        break
                elif kind == 0x8001:
                    if len(payload) < 5:
                        raise ProtocolError('Diagnostic payload must contain SA, TA and SID')
                    sa, target = struct.unpack_from('!HH', payload)
                    if source is None or sa != source:
                        await self.send(writer, doip(0x8003, struct.pack('!HHB', target, sa, 2), version))
                        break
                    if target != self.LOGICAL:
                        await self.send(writer, doip(0x8003, struct.pack('!HHB', target, sa, 3), version))
                        continue
                    await self.send(writer, doip(0x8002, struct.pack('!HHB', self.LOGICAL, sa, 0), version))
                    response = self.core.uds(connection, payload[4:], now_ms())
                    self.trace.emit('UDS', trace, connection=connection, request=payload[4:].hex(),
                                    response=response.hex(), session=self.core.session(connection))
                    if response[:4] == b'\x71\x01\xf0\x01':
                        self.diag_trace = trace
                        self.trace.emit('ADMISSION', trace, result='ALLOW', state=self.core.state(now_ms()))
                    elif response[:2] == b'\x7f\x31':
                        self.trace.emit('ADMISSION', trace, result='REJECT', nrc=response[2])
                    if response:
                        await self.send(writer, doip(0x8001, struct.pack('!HH', self.LOGICAL, sa) + response, version))
                elif kind == 8 and payload == struct.pack('!H', source or 0):
                    pass
                else:
                    await self.send(writer, doip(0, b'\x01', version))
        except ProtocolError as error:
            self.trace.emit('REJECT_DOIP', connection=connection, error=str(error))
            with contextlib.suppress(ConnectionError, asyncio.TimeoutError):
                await self.send(writer, doip(0, bytes([error.nack]), version))
        except (asyncio.IncompleteReadError, ConnectionError, asyncio.TimeoutError):
            pass
        finally:
            if source is not None and self.sources.get(source) == connection:
                del self.sources[source]
                self.core.disconnect(connection)
            writer.close()
            with contextlib.suppress(ConnectionError, asyncio.CancelledError):
                await writer.wait_closed()
            self.writers.discard(writer)
            self.handlers.discard(task)

    async def handle_someip(self, reader, writer, service):
        task = asyncio.current_task()
        self.handlers.add(task)
        if len(self.writers) >= 24:
            writer.close()
            self.handlers.discard(task)
            return
        self.writers.add(writer)
        peer = writer.get_extra_info('peername')
        try:
            while True:
                data = await read_frame(reader, 'someip', frame_seconds=self.config.frame_ms / 1000)
                request = SomeIP.decode(data)
                trace = self.trace.next()
                self.trace.emit('RX_SOMEIP', trace, service=request.service, method=request.method, wire=data.hex())
                error, payload = 0, b''
                if request.protocol != 1: error = 7
                elif request.interface != 1: error = 8
                elif request.service != service: error = 2
                elif request.message_type != 0 or request.return_code: error = 10
                elif not request.client or not request.session: error = 9
                elif request.method == 1 and service == FAN_SERVICE:
                    if len(request.payload) != 8:
                        error = 9
                    else:
                        command_id, duty, lease = struct.unpack('!IHH', request.payload)
                        identity = (int(ipaddress.IPv4Address(peer[0])) << 16) | request.client
                        result = self.core.command(identity, command_id, duty, lease, now_ms())
                        payload = struct.pack('!IBB', command_id, result, 0)
                        if not result:
                            self.service_trace = trace
                        self.trace.emit('SERVICE_COMMAND', trace, command_id=command_id, duty=duty,
                                        lease=lease, result=result)
                elif request.method == 2 and service == FAN_SERVICE:
                    if request.payload: error = 9
                    else: payload = self.core.fan_status()
                elif request.method == 1 and service == TELEMETRY_SERVICE:
                    if request.payload: error = 9
                    else: payload = self.core.telemetry(self.boot_id, now_ms())
                else:
                    error = 3
                response = SomeIP(request.service, request.method, request.client, request.session,
                                  message_type=0x81 if error else 0x80, return_code=error, payload=payload)
                await self.send(writer, response.encode())
        except (ProtocolError, IdleTimeout, asyncio.IncompleteReadError, ConnectionError, asyncio.TimeoutError) as error:
            self.trace.emit('CLOSE_SOMEIP', reason=type(error).__name__)
        finally:
            writer.close()
            with contextlib.suppress(ConnectionError, asyncio.CancelledError):
                await writer.wait_closed()
            self.writers.discard(writer)
            self.handlers.discard(task)

    def send_sd(self, entries, options, peer):
        self.transports['sd'].sendto(sd_packet(entries, options, self.sd_session, self.sd_reboot), peer)
        self.sd_session += 1
        if self.sd_session > 65535:
            self.sd_session, self.sd_reboot = 1, False

    def receive_sd(self, data, peer):
        try:
            _, entries, options = parse_sd(data)
            if peer[1] != self.ports['sd']:
                raise ProtocolError('Lab SD peers must use the same configured SD port on different IPs')
            for entry in entries:
                if entry.kind == 0:
                    for service, tcp, udp in ((FAN_SERVICE, 'fan', 'event'),
                                              (TELEMETRY_SERVICE, 'telemetry', 'telemetry_event')):
                        if (entry.service in (service, 0xffff) and entry.instance in (1, 0xffff)
                                and entry.major in (1, 255) and entry.value in (0, 0xffffffff)):
                            endpoints = [Endpoint(self.config.host, self.ports[tcp], 6),
                                         Endpoint(self.config.host, self.ports[udp], 17)]
                            self.send_sd([SDEntry(1, service, count1=2)], endpoints, peer)
                elif entry.kind == 6:
                    selected = entry.selected_options(options)
                    endpoints = [o for o in selected if isinstance(o, Endpoint) and o.protocol == 17
                                 and o.address == peer[0] and o.port]
                    valid = (entry.service in (FAN_SERVICE, TELEMETRY_SERVICE) and entry.instance == 1
                             and entry.major == 1 and entry.value & 0xfff00000 == 0
                             and entry.value & 0xffff == 1 and len(endpoints) == 1)
                    ttl = min(entry.ttl, 3) if valid else 0
                    endpoint = endpoints[0] if endpoints else None
                    key = (peer[0], entry.service, endpoint.port if endpoint else 0, (entry.value >> 16) & 15)
                    if valid and entry.ttl == 0:
                        self.subscriptions.pop(key, None)
                        continue
                    if ttl and (key in self.subscriptions or len(self.subscriptions) < 16):
                        self.subscriptions[key] = (now_ms() + ttl * 1000, endpoint)
                    else:
                        ttl = 0
                    self.send_sd([SDEntry(7, entry.service, ttl=ttl, value=entry.value)], [], peer)
                    self.trace.emit('SUBSCRIPTION', service=entry.service, ttl=ttl, peer=str(peer))
                    if ttl:
                        self.publish(entry.service, endpoint)
        except (ProtocolError, ValueError, struct.error) as error:
            self.trace.emit('REJECT_SD', error=str(error), peer=str(peer))

    def publish(self, service, endpoint):
        if service == FAN_SERVICE:
            payload, transport = self.core.fan_status(), 'event'
        else:
            payload, transport = self.core.telemetry(self.boot_id, now_ms()), 'telemetry_event'
        notification = SomeIP(service, 0x8001, message_type=2, payload=payload)
        self.transports[transport].sendto(notification.encode(), (endpoint.address, endpoint.port))

    async def control_loop(self):
        while True:
            now = now_ms()
            self.core.tick(now)
            state = self.core.state(now)
            snapshot = tuple(state[key] for key in ('target_raw', 'applied_raw', 'actual_rpm', 'owner', 'routine', 'reason'))
            if snapshot != self.last_snapshot:
                routine_changed = self.last_snapshot is not None and snapshot[4] != self.last_snapshot[4]
                trace = self.diag_trace if state['owner'] == 3 or routine_changed else (
                    self.service_trace if state['owner'] == 2 else self.signal_trace)
                self.trace.emit('ARBITER', trace, owner=OWNERS[state['owner']], target_raw=state['target_raw'])
                self.trace.emit('APPLY', trace, applied_raw=state['applied_raw'])
                self.trace.emit('FEEDBACK', trace, actual_rpm=state['actual_rpm'], fault=state['fault'])
                self.trace.emit('ROUTINE', trace, state=ROUTINES[state['routine']], reason=state['reason'],
                                result_rpm=state['result_rpm'])
                self.last_snapshot = snapshot
            for key, (expires, _) in list(self.subscriptions.items()):
                if now >= expires:
                    del self.subscriptions[key]
                    self.trace.emit('SUBSCRIPTION_EXPIRED', service=key[1])
            if now - self.last_event_ms >= 100:
                for key, (_, endpoint) in self.subscriptions.items():
                    self.publish(key[1], endpoint)
                self.last_event_ms = now
            await asyncio.sleep(0.01)


async def run(args):
    config = Config(**{name: getattr(args, name) for name in Config.__dataclass_fields__})
    ecu = VirtualECU(config)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for number in (signal.SIGINT, signal.SIGTERM):
        signal.signal(number, lambda *_: loop.call_soon_threadsafe(stop.set))
    ready = await ecu.start()
    print(json.dumps(dict(event='READY', **ready)), flush=True)
    helpers = []
    if args.run_seconds:
        async def timer():
            await asyncio.sleep(args.run_seconds)
            stop.set()
        helpers.append(asyncio.create_task(timer()))
    if args.control_stdin:
        async def stdin_stop():
            await asyncio.to_thread(sys.stdin.buffer.read, 1)
            stop.set()
        helpers.append(asyncio.create_task(stdin_stop()))
    try:
        await stop.wait()
    finally:
        for helper in helpers:
            helper.cancel()
        await ecu.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name, default in Config.__dataclass_fields__.items():
        option = '--' + name.replace('_', '-')
        value = default.default
        if isinstance(value, bool): parser.add_argument(option, action='store_true')
        else: parser.add_argument(option, default=value, type=int if isinstance(value, int) else str)
    parser.add_argument('--run-seconds', type=float)
    parser.add_argument('--control-stdin', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        asyncio.run(run(args))
    except (ValueError, OSError, RuntimeError) as error:
        parser.exit(1, f'ECU startup/runtime error: {error}\n')


if __name__ == '__main__':
    main()
