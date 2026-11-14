"""Small tester/consumer tools: real sockets, decoded responses, no actuator shortcuts."""
import contextlib
import socket
import struct
import threading
import time

from .core import decode_status
from .protocol import (Endpoint, FAN_SERVICE, SDEntry, SomeIP, doip, parse_doip,
                       parse_sd, sd_packet, signal_datagram, signal_payload)


def exact(sock, size):
    data = bytearray()
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            raise ConnectionError('Peer closed before the complete message')
        data.extend(chunk)
    return bytes(data)


class DoIPClient:
    def __init__(self, host='127.0.0.1', port=13400, source=0x0e00, target=1, version=3):
        self.source, self.target, self.version = source, target, version
        self.socket = socket.create_connection((host, port), timeout=3)
        self.socket.sendall(doip(5, struct.pack('!HB4x', source, 0), version))
        kind, body = self.receive()
        if kind != 6 or len(body) < 9 or struct.unpack_from('!HH', body) != (source, target) or body[4] != 0x10:
            self.close()
            raise ValueError(f'Routing activation rejected: {kind:04x} {body.hex()}')

    @staticmethod
    def identify(host='127.0.0.1', port=13400):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(3)
            sock.sendto(doip(1), (host, port))
            data, _ = sock.recvfrom(4096)
        _, kind, body = parse_doip(data)
        if kind != 4 or len(body) not in (32, 33):
            raise ValueError('Invalid identification response')
        return dict(vin=body[:17].decode('ascii'), logical_address=struct.unpack_from('!H', body, 17)[0])

    def receive(self):
        while True:
            header = exact(self.socket, 8)
            length = struct.unpack_from('!I', header, 4)[0]
            if length > 4096:
                raise ValueError('DoIP reply too large')
            _, kind, body = parse_doip(header + exact(self.socket, length))
            if kind == 7 and not body:
                self.socket.sendall(doip(8, struct.pack('!H', self.source), self.version))
                continue
            return kind, body

    def request(self, uds):
        self.socket.sendall(doip(0x8001, struct.pack('!HH', self.source, self.target) + uds, self.version))
        suppressed = len(uds) >= 2 and uds[0] in (0x10, 0x3e, 0x31) and uds[1] & 0x80
        previous_timeout = self.socket.gettimeout()
        acknowledged = False
        try:
            while True:
                try:
                    kind, body = self.receive()
                except socket.timeout:
                    if suppressed and acknowledged:
                        return b''  # Lab P2 is 50 ms; negative responses still have to be read.
                    raise
                if kind in (0, 0x8003):
                    raise ValueError(f'DoIP NACK: {body.hex()}')
                if kind == 0x8002:
                    if body[:5] != struct.pack('!HHB', self.target, self.source, 0):
                        raise ValueError('Invalid diagnostic ACK')
                    acknowledged = True
                    if suppressed:
                        self.socket.settimeout(0.15)
                    continue
                if kind == 0x8001:
                    if body[:4] != struct.pack('!HH', self.target, self.source):
                        raise ValueError('Response address mismatch')
                    return body[4:]
        finally:
            self.socket.settimeout(previous_timeout)

    def close(self):
        self.socket.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class SomeIPClient:
    def __init__(self, host='127.0.0.1', port=30511, service=FAN_SERVICE, client=0x1002):
        self.socket = socket.create_connection((host, port), timeout=3)
        self.service, self.client, self.session = service, client, 0

    def request(self, method, payload=b''):
        self.session = self.session % 65535 + 1
        request = SomeIP(self.service, method, self.client, self.session, payload=payload)
        self.socket.sendall(request.encode())
        header = exact(self.socket, 16)
        length = struct.unpack_from('!I', header, 4)[0]
        if not 8 <= length <= 4104:
            raise ValueError('Invalid SOME/IP response length')
        response = SomeIP.decode(header + exact(self.socket, length - 8))
        if (response.service, response.method, response.client, response.session) != (
                self.service, method, self.client, self.session):
            raise ValueError('SOME/IP response context mismatch')
        if response.return_code or response.message_type != 0x80:
            raise ValueError(f'SOME/IP protocol error {response.return_code}')
        return response.payload

    def status(self):
        return decode_status(self.request(2))

    def set_duty(self, command_id, duty_percent, lease_ms=1000):
        payload = struct.pack('!IHH', command_id, round(duty_percent * 100), lease_ms)
        echoed, result, reserved = struct.unpack('!IBB', self.request(1, payload))
        if echoed != command_id or reserved:
            raise ValueError('Command response mismatch')
        return result

    def close(self):
        self.socket.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class SignalPublisher:
    """UDP CAN-PDU tunnel for hosts without SocketCAN. Refreshes freshness at 10 Hz."""
    def __init__(self, host='127.0.0.1', port=32001, temperature=70.0, speed=0.0, period=0.1):
        self.destination = (host, port)
        self.temperature, self.speed, self.period = temperature, speed, period
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.stop = threading.Event()
        self.error = None

    def run(self):
        counter = 0
        try:
            while not self.stop.is_set():
                payload = signal_payload(self.temperature, self.speed, counter)
                self.socket.sendto(signal_datagram(payload), self.destination)
                counter = (counter + 1) & 255
                self.stop.wait(self.period)
        except Exception as error:
            self.error = error

    def __enter__(self):
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.stop.set()
        self.thread.join(timeout=2)
        self.socket.close()
        if self.error and args[0] is None:
            raise self.error


class EventSubscription:
    """Unicast Find/Offer/Subscribe, separate UDP event endpoint, automatic renewal."""
    def __init__(self, host='127.0.0.1', port=30490, local_ip='127.0.0.2', service=FAN_SERVICE):
        self.peer, self.service, self.session = (host, port), service, 0
        self.sd = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.events = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self.sd.bind((local_ip, port))
            self.events.bind((local_ip, 0))
            self.sd.settimeout(3)
            self.events.settimeout(3)
            self.endpoint = Endpoint(local_ip, self.events.getsockname()[1])
            self.send([SDEntry(0, service, ttl=0)])
            _, entries, options = parse_sd(self.sd.recvfrom(4096)[0])
            offers = [e for e in entries if e.kind == 1 and e.service == service and e.ttl]
            if not offers:
                raise ValueError('No matching OfferService')
            self.offered = offers[0].selected_options(options)
            self.renew()
        except Exception:
            self.sd.close()
            self.events.close()
            raise

    def send(self, entries, options=()):
        self.session = self.session % 65535 + 1
        self.sd.sendto(sd_packet(entries, options, session=self.session), self.peer)

    def renew(self):
        self.send([SDEntry(6, self.service, value=1, count1=1)], [self.endpoint])
        _, entries, _ = parse_sd(self.sd.recvfrom(4096)[0])
        if not any(e.kind == 7 and e.service == self.service and e.ttl for e in entries):
            raise ValueError('Subscription NACK')
        self.renewed_at = time.monotonic()

    def receive(self):
        if time.monotonic() - self.renewed_at >= 1:
            self.renew()
        message = SomeIP.decode(self.events.recvfrom(4096)[0])
        if (message.service, message.method, message.message_type) != (self.service, 0x8001, 2):
            raise ValueError('Unexpected notification')
        return message.payload

    def close(self):
        with contextlib.suppress(OSError):
            self.send([SDEntry(6, self.service, ttl=0, value=1, count1=1)], [self.endpoint])
        self.sd.close()
        self.events.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
