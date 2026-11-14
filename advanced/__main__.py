"""Command-line entry points for the V3 learning lab."""
import argparse
import json
from pathlib import Path
import secrets
import socket
import struct
import sys
import time

from .client import DoIPClient, EventSubscription, SignalPublisher, SomeIPClient
from .core import decode_status
from .protocol import TELEMETRY_SERVICE, signal_payload


def show(stage, **data):
    print(json.dumps(dict(stage=stage, **data)), flush=True)


def wait_for(callback, predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = callback()
        if predicate(result):
            return result
        time.sleep(0.03)
    raise TimeoutError('Expected ECU state was not observed')


def demo():
    from .process import LabProcess
    with LabProcess(test_enable=True) as lab:
        info, host = lab.info, lab.info['host']
        ports = info['ports']
        show('ECU_READY', **info)
        show('DISCOVERY', **DoIPClient.identify(host, ports['doip']))
        with SignalPublisher(host, ports['signal']), SomeIPClient(host, ports['fan']) as service:
            normal = wait_for(service.status, lambda s: s['owner'] == 'NORMAL_THERMAL')
            show('NORMAL_SIGNAL_70C', **normal)
            with EventSubscription(host, ports['sd']) as subscription:
                show('SOMEIP_EVENT', **decode_status(subscription.receive()))
                result = service.set_duty(1, 25, 1000)
                if result != 0:
                    raise RuntimeError('Normal command rejected')
                show('NORMAL_SERVICE_25_PERCENT', **wait_for(service.status, lambda s: s['owner'] == 'NORMAL_SERVICE'))
                with DoIPClient(host, ports['doip']) as tester:
                    show('READ_TEMPERATURE_DID', uds=tester.request(bytes.fromhex('22 f2 00')).hex())
                    show('EXTENDED_SESSION', uds=tester.request(bytes.fromhex('10 03')).hex())
                    start = tester.request(bytes.fromhex('31 01 f0 01 0f a0 03 e8'))
                    if start != bytes.fromhex('71 01 f0 01 01'):
                        raise RuntimeError(f'Self-test rejected: {start.hex()}')
                    show('SELFTEST_ACCEPTED', uds=start.hex())
                    busy = service.set_duty(2, 60, 1000)
                    if busy != 1:
                        raise RuntimeError('Arbitration failed: expected BUSY_DIAGNOSTIC')
                    show('SOMEIP_REJECTED_DURING_DIAGNOSTIC', result=busy)
                    routine = wait_for(lambda: tester.request(bytes.fromhex('31 03 f0 01')),
                                       lambda data: len(data) == 7 and data[4] != 1)
                    if routine[4] != 2:
                        raise RuntimeError(f'Self-test failed: {routine.hex()}')
                    show('SELFTEST_COMPLETED', uds=routine.hex(), result_rpm=int.from_bytes(routine[5:], 'big'))
                    show('OWNER_RELEASED', **wait_for(service.status, lambda s: s['owner'] == 'NORMAL_THERMAL'))
        show('DEMO_PASS', trace=info['trace'])


def main():
    if len(sys.argv) > 1 and sys.argv[1] == 'ecu':
        from .ecu import main as ecu_main
        sys.argv.pop(1)
        ecu_main()
        return
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    sub.add_parser('demo', help='One command: start ECU, publish signals, run diagnostic and service scenario')
    signal_parser = sub.add_parser('signal', help='Publish normal CAN-PDU data via UDP or Linux SocketCAN')
    signal_parser.add_argument('--host', default='127.0.0.1')
    signal_parser.add_argument('--port', type=int, default=32001)
    signal_parser.add_argument('--temperature', type=float, default=70)
    signal_parser.add_argument('--speed', type=float, default=0)
    signal_parser.add_argument('--seconds', type=float, default=0, help='0 means run until Ctrl+C')
    signal_parser.add_argument('--can-interface')
    diag = sub.add_parser('diag', help='DoIP tester: read DID or execute/query FanSelfTest')
    diag.add_argument('--host', default='127.0.0.1')
    diag.add_argument('--port', type=int, default=13400)
    diag.add_argument('--request', default='22 f2 00', help='UDS request in hex')
    diag.add_argument('--self-test', type=float, help='Duty percent; keep connection open until terminal result')
    diag.add_argument('--duration', type=int, default=1000)
    status = sub.add_parser('status', help='Read FanStatus through SOME/IP TCP')
    setter = sub.add_parser('set-duty', help='Send a normal SOME/IP command')
    events = sub.add_parser('events', help='Find/subscribe/receive SOME/IP FanStatus over UDP')
    snapshot = sub.add_parser('snapshot', help='Read VehicleTelemetryService through SOME/IP TCP')
    for command, port in ((status, 30511), (setter, 30511), (events, 30490), (snapshot, 30501)):
        command.add_argument('--host', default='127.0.0.1')
        command.add_argument('--port', type=int, default=port)
    setter.add_argument('duty', type=float)
    setter.add_argument('--lease', type=int, default=1000)
    setter.add_argument('--id', type=int, default=None)
    events.add_argument('--local-ip', default='127.0.0.2')
    events.add_argument('--count', type=int, default=10)
    args = parser.parse_args()
    try:
        if args.action == 'demo':
            demo()
        elif args.action == 'signal':
            if args.can_interface:
                if not hasattr(socket, 'AF_CAN'):
                    raise RuntimeError('SocketCAN requires Linux')
                with socket.socket(socket.AF_CAN, socket.SOCK_RAW, socket.CAN_RAW) as sock:
                    sock.bind((args.can_interface,))
                    started, counter = time.monotonic(), 0
                    while args.seconds == 0 or time.monotonic() - started < args.seconds:
                        payload = signal_payload(args.temperature, args.speed, counter)
                        sock.send(struct.pack('=IB3x8s', 0x321, 8, payload))
                        counter = (counter + 1) & 255
                        time.sleep(0.1)
            else:
                signal_payload(args.temperature, args.speed)
                with SignalPublisher(args.host, args.port, args.temperature, args.speed):
                    show('PUBLISHING_SIGNAL', temperature=args.temperature, speed=args.speed)
                    if args.seconds:
                        time.sleep(args.seconds)
                    else:
                        while True:
                            time.sleep(1)
        elif args.action == 'diag':
            with DoIPClient(args.host, args.port) as tester:
                if args.self_test is None:
                    show('UDS_RESPONSE', hex=tester.request(bytes.fromhex(args.request)).hex())
                else:
                    show('SESSION', hex=tester.request(bytes.fromhex('10 03')).hex())
                    request = struct.pack('!BBHHH', 0x31, 1, 0xf001, round(args.self_test * 100), args.duration)
                    response = tester.request(request)
                    show('START', hex=response.hex())
                    if response[:4] != bytes.fromhex('71 01 f0 01'):
                        raise ValueError('Self-test was rejected')
                    result = wait_for(lambda: tester.request(bytes.fromhex('31 03 f0 01')),
                                      lambda r: len(r) == 7 and r[4] != 1)
                    show('RESULT', hex=result.hex(), state=result[4], rpm=int.from_bytes(result[5:], 'big'))
        elif args.action in ('status', 'set-duty'):
            with SomeIPClient(args.host, args.port) as client:
                if args.action == 'set-duty':
                    result = client.set_duty(args.id or secrets.randbelow(0xffffffff) + 1, args.duty, args.lease)
                    show('COMMAND_RESULT', result=result)
                    time.sleep(0.03)
                show('FAN_STATUS', **client.status())
        elif args.action == 'events':
            with EventSubscription(args.host, args.port, args.local_ip) as subscription:
                for _ in range(args.count):
                    show('EVENT', **decode_status(subscription.receive()))
        elif args.action == 'snapshot':
            with SomeIPClient(args.host, args.port, service=TELEMETRY_SERVICE) as client:
                boot, seq, uptime, temp, rpm, flags, reserved = struct.unpack('!IIIhHHH', client.request(1))
                show('SNAPSHOT', boot_id=boot, sequence=seq, uptime_ms=uptime,
                     temperature_c=temp / 100, rpm=rpm, flags=flags, reserved=reserved)
    except KeyboardInterrupt:
        pass
    except (ValueError, RuntimeError, OSError, TimeoutError, struct.error) as error:
        parser.exit(1, f'Lab error: {error}\n')


if __name__ == '__main__':
    main()
