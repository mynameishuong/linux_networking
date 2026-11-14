import struct
import unittest
from advanced.core import Core
from advanced.protocol import signal_payload


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.now, self.counter = 1000000, 0
        self.core = Core(test_enable=True, now=self.now)
        self.addCleanup(lambda: self.core.close())

    def sample(self, temperature=70, speed=0, valid=True):
        self.counter = (self.counter + 1) & 255
        return self.core.signal(signal_payload(temperature, speed, self.counter, valid), self.now)

    def state(self):
        return self.core.state(self.now)

    def advance(self, milliseconds, refresh=False):
        end = self.now + milliseconds
        while self.now < end:
            self.now = min(self.now + 10, end)
            if refresh:
                self.sample()
            self.core.tick(self.now)

    def start(self, duty=4000, duration=1000, connection=1):
        self.sample()
        self.core.open(connection, self.now)
        self.assertEqual(self.core.uds(connection, b'\x10\x03', self.now), bytes.fromhex('50 03 00 32 01 f4'))
        request = struct.pack('!BBHHH', 0x31, 1, 0xf001, duty, duration)
        self.assertEqual(self.core.uds(connection, request, self.now), bytes.fromhex('71 01 f0 01 01'))

    def test_signal_golden_vector(self):
        self.assertEqual(self.core.signal(bytes.fromhex('4c04000001010000'), self.now), 0)
        self.core.tick(self.now)
        self.assertEqual((self.state()['temperature_centi_c'], self.state()['speed_raw'],
                          self.state()['target_raw'], self.state()['actual_rpm']), (7000, 0, 4000, 2000))

    def test_hysteresis(self):
        self.sample(70); self.core.tick(self.now)
        self.sample(57); self.core.tick(self.now)
        self.assertEqual(self.state()['target_raw'], 4000)
        self.sample(55); self.core.tick(self.now)
        self.assertEqual(self.state()['target_raw'], 0)
        self.sample(57); self.core.tick(self.now)
        self.assertEqual(self.state()['target_raw'], 0)

    def test_negative_temperature_and_telemetry_codec(self):
        self.sample(-10); self.core.tick(self.now)
        fields = struct.unpack('!IIIhHHH', self.core.telemetry(123, self.now))
        self.assertEqual(fields, (123, 1, 0, -1000, 0, 3, 0))

    def test_invalid_reserved_and_range_do_not_refresh_model(self):
        self.sample()
        for data in (b'', b'1234567', bytes.fromhex('4c f4 00 00 02 01 00 00'),
                     bytes.fromhex('ff 07 00 00 02 01 00 00')):
            self.assertEqual(self.core.signal(data, self.now), 3)
        self.assertEqual(self.state()['samples'], 1)

    def test_invalid_flag_updates_quality_and_fallback(self):
        self.sample(valid=False); self.core.tick(self.now)
        self.assertEqual((self.state()['quality'], self.state()['owner']), (1, 0))

    def test_duplicate_does_not_refresh_freshness(self):
        data = signal_payload(counter=1)
        self.assertEqual(self.core.signal(data, self.now), 0)
        self.now += 200
        self.assertEqual(self.core.signal(data, self.now), 1)
        self.advance(100)
        self.assertEqual((self.state()['quality'], self.state()['owner']), (2, 0))

    def test_counter_wrap_and_reorder(self):
        self.assertEqual(self.core.signal(signal_payload(counter=255), self.now), 0)
        self.assertEqual(self.core.signal(signal_payload(counter=0), self.now), 0)
        self.assertEqual(self.core.signal(signal_payload(counter=254), self.now), 2)
        self.advance(300)
        self.assertEqual(self.core.signal(signal_payload(counter=3), self.now), 0)

    def test_default_session_and_bad_records_do_not_start(self):
        self.sample(); self.core.open(1, self.now)
        self.assertEqual(self.core.uds(1, bytes.fromhex('31 01 f0 01 0f a0 03 e8'), self.now), b'\x7f\x31\x7e')
        self.assertEqual(self.core.uds(1, b'\x31\x01', self.now), b'\x7f\x31\x13')
        self.assertEqual(self.state()['routine'], 0)

    def test_read_did_is_readonly_and_validates_unknown_did(self):
        self.sample(); self.core.open(1, self.now)
        before = self.core.fan_status()
        self.assertEqual(self.core.uds(1, bytes.fromhex('22 f2 00'), self.now), bytes.fromhex('62 f2 00 1b 58 00 00 00'))
        self.assertEqual(self.core.uds(1, bytes.fromhex('22 f2 01'), self.now), b'\x62\xf2\x01' + before)
        self.assertEqual(self.core.uds(1, bytes.fromhex('22 f2 ff'), self.now), b'\x7f\x22\x31')
        self.assertEqual(self.core.fan_status(), before)

    def test_session_capacity_is_bounded(self):
        for i in range(1, 9): self.assertEqual(self.core.open(i, self.now), 0)
        self.assertEqual(self.core.open(9, self.now), -1)
        self.core.disconnect(4)
        self.assertEqual(self.core.open(9, self.now), 0)

    def test_selftest_completes_and_preserves_terminal_rpm(self):
        self.start(duty=2500, duration=500)
        self.advance(520, refresh=True)
        self.assertEqual((self.state()['routine'], self.state()['result_rpm'],
                          self.state()['actual_rpm'], self.state()['owner']), (2, 1250, 2000, 1))
        self.assertEqual(self.core.uds(1, bytes.fromhex('31 03 f0 01'), self.now), bytes.fromhex('71 03 f0 01 02 04 e2'))

    def test_start_is_accepted_before_control_tick_applies_output(self):
        self.start()
        self.assertEqual(self.state()['applied_raw'], 0)
        self.advance(10)
        self.assertEqual((self.state()['owner'], self.state()['applied_raw']), (3, 4000))

    def test_minimum_duration_completes(self):
        self.start(duration=100)
        self.advance(120, refresh=True)
        self.assertEqual(self.state()['routine'], 2)

    def test_stale_signal_aborts_routine(self):
        self.start()
        self.advance(300)
        self.assertEqual((self.state()['routine'], self.state()['reason'], self.state()['owner']), (3, 3, 0))

    def test_vehicle_movement_aborts_routine(self):
        self.start(); self.advance(10)
        self.sample(speed=1); self.advance(10)
        self.assertEqual((self.state()['routine'], self.state()['reason']), (3, 2))

    def test_stop_and_nonowner_stop(self):
        self.start(); self.advance(10)
        self.core.open(2, self.now)
        self.assertEqual(self.core.uds(2, bytes.fromhex('31 02 f0 01'), self.now), b'\x7f\x31\x24')
        self.assertEqual(self.core.uds(1, bytes.fromhex('31 02 f0 01'), self.now), bytes.fromhex('71 02 f0 01 03'))
        self.advance(10)
        self.assertEqual(self.state()['owner'], 1)

    def test_disconnect_aborts_and_releases(self):
        self.start(); self.advance(10)
        self.core.disconnect(1); self.advance(10)
        self.assertEqual((self.state()['routine'], self.state()['reason'], self.state()['owner']), (3, 5, 1))

    def test_session_expiry_aborts_with_tcp_context_still_open(self):
        self.core.close()
        self.core = Core(test_enable=True, s3_ms=200, now=self.now)
        self.start(); self.advance(210, refresh=True)
        self.assertEqual((self.core.session(1), self.state()['routine'], self.state()['reason']), (1, 3, 4))

    def test_tester_present_and_suppression(self):
        self.core.close()
        self.core = Core(test_enable=True, s3_ms=200, now=self.now)
        self.start(duration=500)
        for _ in range(6):
            self.advance(100, refresh=True)
            self.assertEqual(self.core.uds(1, b'\x3e\x80', self.now), b'')
        self.assertEqual(self.state()['routine'], 2)
        self.assertEqual(self.core.uds(1, b'\x3e\xff', self.now), b'\x7f\x3e\x12')

    def test_default_session_releases_diagnostic_owner(self):
        self.start(); self.advance(10)
        self.core.uds(1, b'\x10\x01', self.now); self.advance(10)
        self.assertEqual((self.state()['routine'], self.state()['reason']), (3, 4))

    def test_routine_range_validation(self):
        self.sample(); self.core.open(1, self.now); self.core.uds(1, b'\x10\x03', self.now)
        for duty, duration in ((10001, 1000), (4000, 99), (4000, 2001)):
            self.assertEqual(self.core.uds(1, struct.pack('!BBHHH', 0x31, 1, 0xf001, duty, duration), self.now), b'\x7f\x31\x31')

    def test_test_enable_is_required(self):
        self.core.close(); self.core = Core(test_enable=False, now=self.now)
        self.sample(); self.core.open(1, self.now); self.core.uds(1, b'\x10\x03', self.now)
        self.assertEqual(self.core.uds(1, bytes.fromhex('31 01 f0 01 0f a0 03 e8'), self.now), b'\x7f\x31\x22')

    def test_driver_fault_fails_running_routine(self):
        self.start(); self.advance(10); self.core.fault(1); self.advance(10)
        self.assertEqual((self.state()['routine'], self.state()['reason'], self.state()['applied_raw']), (4, 6, 0))

    def test_stall_does_not_claim_success_from_applied_duty(self):
        self.start(duration=500); self.core.fault(2)
        self.advance(520, refresh=True)
        self.assertEqual((self.state()['routine'], self.state()['reason'], self.state()['actual_rpm']), (4, 7, 0))

    def test_service_command_applied_only_by_tick(self):
        self.assertEqual(self.core.command(1, 1, 2500, 1000, self.now), 0)
        self.assertEqual(self.state()['applied_raw'], 0)
        self.advance(10)
        self.assertEqual((self.state()['owner'], self.state()['applied_raw']), (2, 2500))

    def test_replay_does_not_extend_lease_and_payload_conflict_rejected(self):
        self.sample()
        self.assertEqual(self.core.command(1, 1, 2500, 100, self.now), 0)
        self.advance(90)
        self.assertEqual(self.core.command(1, 1, 2500, 100, self.now), 0)
        self.assertEqual(self.core.command(1, 1, 3500, 100, self.now), 2)
        self.advance(20)
        self.assertEqual(self.state()['owner'], 1)

    def test_diag_cancels_previous_service_lease_and_rejects_new_commands(self):
        self.core.command(1, 1, 2500, 2000, self.now)
        self.start(duration=500)
        self.assertEqual(self.core.command(1, 2, 6000, 1000, self.now), 1)
        self.advance(520, refresh=True)
        self.assertEqual(self.state()['owner'], 1)
        self.assertEqual(self.core.command(1, 2, 6000, 1000, self.now), 1)

    def test_invalid_and_faulted_service_commands(self):
        for command, duty, lease in ((0, 4000, 1000), (1, 10001, 1000), (2, 4000, 99)):
            self.assertEqual(self.core.command(1, command, duty, lease, self.now), 2)
        self.core.fault(1)
        self.assertEqual(self.core.command(1, 4, 4000, 1000, self.now), 3)

    def test_replay_cache_is_bounded_and_eviction_has_documented_semantics(self):
        for command in range(1, 17):
            self.assertEqual(self.core.command(1, command, 2500, 1000, self.now), 0)
        self.assertEqual(self.core.command(1, 1, 2500, 1000, self.now), 0)
        self.assertEqual(self.state()['last_command'], 16)  # Still cached: not executed again.
        self.core.command(1, 17, 2500, 1000, self.now)
        self.core.command(1, 1, 2500, 1000, self.now)
        self.assertEqual(self.state()['last_command'], 1)  # Evicted: no exactly-once guarantee.

    def test_command_identity_is_scoped_to_client(self):
        self.assertEqual(self.core.command(1, 7, 2500, 1000, self.now), 0)
        self.assertEqual(self.core.command(2, 7, 6000, 1000, self.now), 0)
        self.advance(10)
        self.assertEqual(self.state()['applied_raw'], 6000)


if __name__ == '__main__':
    unittest.main()
