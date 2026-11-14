import json
from pathlib import Path
import socket
import struct
import tempfile
import time
import unittest

from advanced.__main__ import wait_for
from advanced.client import DoIPClient, EventSubscription, SignalPublisher, SomeIPClient, exact
from advanced.core import decode_status
from advanced.process import LabProcess
from advanced.protocol import (Endpoint, FAN_SERVICE, SDEntry, SomeIP, TELEMETRY_SERVICE,
                               doip, parse_doip, parse_sd, sd_packet)


def receive_doip(sock):
    head = exact(sock, 8)
    length = struct.unpack_from('!I', head, 4)[0]
    return parse_doip(head + exact(sock, length))


class NetworkTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix='eln-v3-')
        self.addCleanup(folder.cleanup)
        self.trace = Path(folder.name) / 'trace.jsonl'

    def launch(self, **config):
        config.setdefault('test_enable', True)
        self.lab = LabProcess(trace=self.trace, **config)
        self.addCleanup(self.lab.close)
        self.host, self.ports = self.lab.info['host'], self.lab.info['ports']

    def diagnostic_client(self, **options):
        client = DoIPClient(self.host, self.ports['doip'], **options)
        self.addCleanup(client.close)
        return client

    def service(self):
        client = SomeIPClient(self.host, self.ports['fan'])
        self.addCleanup(client.close)
        return client

    def publisher(self, **options):
        return SignalPublisher(self.host, self.ports['signal'], **options)

    def start_routine(self, tester, duration=500):
        self.assertEqual(tester.request(b'\x10\x03')[:2], b'\x50\x03')
        result = tester.request(struct.pack('!BBHHH', 0x31, 1, 0xf001, 4000, duration))
        self.assertEqual(result, bytes.fromhex('71 01 f0 01 01'))

    def test_discovery_and_doip_version2_compatibility(self):
        self.launch()
        self.assertEqual(DoIPClient.identify(self.host, self.ports['doip'])['logical_address'], 1)
        self.assertEqual(self.diagnostic_client(version=2).request(bytes.fromhex('22 f2 00'))[:3], bytes.fromhex('62 f2 00'))

    def test_coalesced_activation_and_read(self):
        self.launch()
        with socket.create_connection((self.host, self.ports['doip']), timeout=3) as sock:
            sock.sendall(doip(5, bytes.fromhex('0e000000000000')) +
                         doip(0x8001, bytes.fromhex('0e00000122f200')))
            self.assertEqual(receive_doip(sock)[1], 6)
            self.assertEqual(receive_doip(sock)[1], 0x8002)
            self.assertEqual(receive_doip(sock)[2][4:7], bytes.fromhex('62 f2 00'))

    def test_diagnostic_before_activation_is_nacked(self):
        self.launch()
        with socket.create_connection((self.host, self.ports['doip']), timeout=3) as sock:
            sock.sendall(doip(0x8001, bytes.fromhex('0e00000122f200')))
            _, kind, payload = receive_doip(sock)
            self.assertEqual((kind, payload[-1]), (0x8003, 2))
        self.assertEqual(self.service().status()['routine'], 'IDLE')

    def test_wrong_source_and_wrong_target_do_not_dispatch(self):
        self.launch()
        tester = self.diagnostic_client()
        tester.socket.sendall(doip(0x8001, bytes.fromhex('0e00000222f200')))
        self.assertEqual(tester.receive()[0], 0x8003)
        self.assertEqual(tester.request(bytes.fromhex('22f200'))[:3], bytes.fromhex('62f200'))
        tester.socket.sendall(doip(0x8001, bytes.fromhex('0e01000122f200')))
        kind, payload = tester.receive()
        self.assertEqual((kind, payload[-1]), (0x8003, 2))

    def test_duplicate_source_address_is_rejected(self):
        self.launch()
        first = self.diagnostic_client()
        with self.assertRaises(ValueError): DoIPClient(self.host, self.ports['doip'])
        self.assertEqual(first.request(bytes.fromhex('22f201'))[:3], bytes.fromhex('62f201'))

    def test_bad_version_and_large_length(self):
        self.launch()
        for wire, code in ((bytes.fromhex('03fd000100000000'), 0),
                           (struct.pack('!BBHI', 3, 252, 0x8001, 4097), 2)):
            with self.subTest(code=code), socket.create_connection((self.host, self.ports['doip']), timeout=3) as sock:
                sock.sendall(wire)
                self.assertEqual(receive_doip(sock)[1:], (0, bytes([code])))

    def test_whole_frame_deadline_with_slow_partial_header(self):
        self.launch(frame_ms=100)
        with socket.create_connection((self.host, self.ports['doip']), timeout=3) as sock:
            sock.sendall(b'\x03')
            time.sleep(0.07)
            sock.sendall(b'\xfc')
            self.assertEqual(receive_doip(sock)[1:], (0, b'\x04'))

    def test_normal_service_diagnostic_arbitration_and_release(self):
        self.launch()
        service, tester = self.service(), self.diagnostic_client()
        with self.publisher():
            wait_for(service.status, lambda s: s['owner'] == 'NORMAL_THERMAL')
            self.assertEqual(service.set_duty(1, 25), 0)
            self.assertEqual(wait_for(service.status, lambda s: s['owner'] == 'NORMAL_SERVICE')['actual_rpm'], 1250)
            self.start_routine(tester)
            self.assertEqual(service.set_duty(2, 60), 1)
            result = wait_for(lambda: tester.request(bytes.fromhex('31 03 f0 01')),
                              lambda r: len(r) == 7 and r[4] != 1)
            self.assertEqual(result, bytes.fromhex('71 03 f0 01 02 07 d0'))
            self.assertEqual(wait_for(service.status, lambda s: s['owner'] == 'NORMAL_THERMAL')['target_percent'], 40)

    def test_disconnect_releases_diagnostic_with_signal_still_fresh(self):
        self.launch()
        service, tester = self.service(), self.diagnostic_client()
        with self.publisher():
            wait_for(service.status, lambda s: s['owner'] == 'NORMAL_THERMAL')
            self.start_routine(tester, 1000)
            wait_for(service.status, lambda s: s['owner'] == 'DIAGNOSTIC')
            tester.close()
            status = wait_for(service.status, lambda s: s['routine'] == 'ABORTED' and s['owner'] == 'NORMAL_THERMAL')
            self.assertEqual(status['owner'], 'NORMAL_THERMAL')

    def test_signal_loss_aborts_and_selects_fallback(self):
        self.launch()
        service, tester = self.service(), self.diagnostic_client()
        with self.publisher():
            wait_for(service.status, lambda s: s['owner'] == 'NORMAL_THERMAL')
            self.start_routine(tester, 1000)
        status = wait_for(service.status, lambda s: s['routine'] == 'ABORTED')
        self.assertEqual((status['owner'], status['applied_percent']), ('FALLBACK', 0))

    def test_session_timeout_while_tcp_remains_connected(self):
        self.launch(s3_ms=150)
        service, tester = self.service(), self.diagnostic_client()
        with self.publisher():
            wait_for(service.status, lambda s: s['owner'] == 'NORMAL_THERMAL')
            self.start_routine(tester, 1000)
            wait_for(service.status, lambda s: s['routine'] == 'ABORTED')
            result = tester.request(bytes.fromhex('31 01 f0 01 0f a0 03 e8'))
            self.assertEqual(result, bytes.fromhex('7f 31 7e'))

    def test_stall_reports_failure_instead_of_completed(self):
        self.launch(fault=2)
        service, tester = self.service(), self.diagnostic_client()
        with self.publisher():
            wait_for(service.status, lambda s: s['owner'] == 'NORMAL_THERMAL')
            self.start_routine(tester, 300)
            result = wait_for(lambda: tester.request(bytes.fromhex('31 03 f0 01')),
                              lambda r: len(r) == 7 and r[4] != 1)
            self.assertEqual(result, bytes.fromhex('71 03 f0 01 04 00 00'))

    def test_sd_subscription_real_udp_event_and_renewal(self):
        self.launch()
        with self.publisher(), EventSubscription(self.host, self.ports['sd']) as events:
            end, count = time.monotonic() + 3.2, 0
            while time.monotonic() < end:
                status = decode_status(events.receive())
                count += 1
            self.assertGreater(count, 20)
            self.assertEqual(status['owner'], 'NORMAL_THERMAL')
        records = [json.loads(line) for line in self.trace.read_text(encoding='utf-8').splitlines()]
        self.assertFalse(any(r['stage'] == 'SUBSCRIPTION_EXPIRED' for r in records))

    def test_sd_ttl_expiry_and_resubscribe(self):
        self.launch()
        with EventSubscription(self.host, self.ports['sd']) as events:
            time.sleep(3.2)
            events.events.settimeout(0.08)
            while True:
                try: events.events.recvfrom(4096)
                except socket.timeout: break
            with self.assertRaises(socket.timeout): events.events.recvfrom(4096)
            events.renew()
            self.assertEqual(len(events.receive()), 16)
        records = [json.loads(line) for line in self.trace.read_text(encoding='utf-8').splitlines()]
        self.assertTrue(any(r['stage'] == 'SUBSCRIPTION_EXPIRED' for r in records))

    def test_sd_refuses_foreign_event_endpoint(self):
        self.launch()
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.bind(('127.0.0.2', self.ports['sd']))
            sock.settimeout(3)
            wire = sd_packet([SDEntry(6, FAN_SERVICE, value=1, count1=1)], [Endpoint('127.0.0.3', 40000)])
            sock.sendto(wire, (self.host, self.ports['sd']))
            _, entries, _ = parse_sd(sock.recvfrom(4096)[0])
            self.assertEqual(entries[0].ttl, 0)

    def test_telemetry_service_returns_same_model(self):
        self.launch()
        with self.publisher(), SomeIPClient(self.host, self.ports['telemetry'], TELEMETRY_SERVICE) as client:
            result = wait_for(lambda: struct.unpack('!IIIhHHH', client.request(1)), lambda r: r[5] & 1)
            self.assertEqual((result[0], result[3], result[6]), (self.lab.info['boot_id'], 7000, 0))

    def test_someip_bad_interface_and_message_type_do_not_apply(self):
        self.launch()
        for interface, kind, expected in ((2, 0, 8), (1, 2, 10)):
            with socket.create_connection((self.host, self.ports['fan']), timeout=3) as sock:
                request = SomeIP(FAN_SERVICE, 1, 0x1002, 1, interface=interface, message_type=kind,
                                 payload=struct.pack('!IHH', 99, 10000, 2000))
                sock.sendall(request.encode())
                header = exact(sock, 16)
                length = struct.unpack_from('!I', header, 4)[0] - 8
                self.assertEqual(SomeIP.decode(header + exact(sock, length)).return_code, expected)
        self.assertEqual(self.service().status()['applied_percent'], 0)

    def test_suppressed_positive_but_negative_response_is_still_read(self):
        self.launch()
        tester = self.diagnostic_client()
        self.assertEqual(tester.request(bytes.fromhex('3e 80')), b'')
        self.assertEqual(tester.request(bytes.fromhex('3e ff')), bytes.fromhex('7f 3e 12'))
        self.assertEqual(tester.request(bytes.fromhex('22 f2 01'))[:3], bytes.fromhex('62f201'))

    def test_alive_check_is_answered_by_client(self):
        self.launch(alive_ms=100)
        tester = self.diagnostic_client()
        time.sleep(0.15)
        self.assertEqual(tester.request(bytes.fromhex('22f201'))[:3], bytes.fromhex('62f201'))

    def test_trace_correlates_receive_decode_apply_and_terminal_result(self):
        self.launch()
        service, tester = self.service(), self.diagnostic_client()
        with self.publisher():
            wait_for(service.status, lambda s: s['owner'] == 'NORMAL_THERMAL')
            self.start_routine(tester, 200)
            wait_for(service.status, lambda s: s['routine'] == 'COMPLETED')
        records = [json.loads(line) for line in self.trace.read_text(encoding='utf-8').splitlines()]
        admission = next(r for r in records if r['stage'] == 'ADMISSION' and r.get('result') == 'ALLOW')
        related = [r for r in records if r['trace'] == admission['trace']]
        stages = {r['stage'] for r in related}
        self.assertTrue({'RX_DOIP', 'UDS', 'ADMISSION', 'ARBITER', 'APPLY', 'FEEDBACK', 'ROUTINE'} <= stages)
        self.assertTrue(any(r['stage'] == 'ROUTINE' and r['state'] == 'COMPLETED' for r in related))


if __name__ == '__main__':
    unittest.main()
