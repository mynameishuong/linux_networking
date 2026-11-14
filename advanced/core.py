"""ctypes boundary: bytes and clock values enter C; C owns all control decisions."""
import ctypes as C
import struct
import time
from .build import library_path

OWNERS = ('FALLBACK', 'NORMAL_THERMAL', 'NORMAL_SERVICE', 'DIAGNOSTIC')
ROUTINES = ('IDLE', 'RUNNING', 'COMPLETED', 'ABORTED', 'FAILED')
STATE_FIELDS = ('temperature_centi_c', 'speed_raw', 'quality', 'age_ms', 'target_raw',
                'applied_raw', 'actual_rpm', 'owner', 'routine', 'result_rpm', 'reason',
                'counter', 'samples', 'fault', 'sequence', 'last_command', 'test_enable', 'routine_connection')


def now_ms():
    return time.monotonic_ns() // 1_000_000


class Core:
    def __init__(self, test_enable=False, s3_ms=5000, stale_ms=300, now=None):
        path = library_path()
        if not path.exists():
            raise RuntimeError('Build the C core first: python -m advanced.build')
        self.lib = C.CDLL(str(path))
        pointer = C.c_void_p
        byteptr = C.POINTER(C.c_uint8)
        signatures = {
            'create': ([C.c_int, C.c_uint32, C.c_uint32, C.c_uint64], pointer),
            'destroy': ([pointer], None),
            'signal': ([pointer, byteptr, C.c_size_t, C.c_uint64], C.c_int),
            'tick': ([pointer, C.c_uint64], None),
            'fault': ([pointer, C.c_uint], None),
            'diag_open': ([pointer, C.c_uint32, C.c_uint64], C.c_int),
            'diag_close': ([pointer, C.c_uint32], None),
            'session': ([pointer, C.c_uint32], C.c_int),
            'uds': ([pointer, C.c_uint32, byteptr, C.c_size_t, byteptr, C.c_size_t, C.c_uint64], C.c_int),
            'command': ([pointer, C.c_uint64, C.c_uint32, C.c_uint16, C.c_uint16, C.c_uint64], C.c_int),
            'fan_status': ([pointer, byteptr], None),
            'telemetry': ([pointer, C.c_uint32, byteptr, C.c_uint64], None),
            'debug': ([pointer, C.POINTER(C.c_int64), C.c_uint64], None),
        }
        for name, (args, result) in signatures.items():
            function = getattr(self.lib, 'v3_' + name)
            function.argtypes, function.restype = args, result
        self.handle = self.lib.v3_create(test_enable, s3_ms, stale_ms, now_ms() if now is None else now)
        if not self.handle:
            raise ValueError('Cannot create core: invalid configuration or allocation failed')

    def close(self):
        if self.handle:
            self.lib.v3_destroy(self.handle)
            self.handle = None

    @staticmethod
    def _bytes(data):
        return (C.c_uint8 * len(data)).from_buffer_copy(data)

    def signal(self, data, now):
        return self.lib.v3_signal(self.handle, self._bytes(data), len(data), now)

    def tick(self, now):
        self.lib.v3_tick(self.handle, now)

    def fault(self, flags):
        self.lib.v3_fault(self.handle, flags)

    def open(self, connection, now):
        return self.lib.v3_diag_open(self.handle, connection, now)

    def disconnect(self, connection):
        self.lib.v3_diag_close(self.handle, connection)

    def session(self, connection):
        return self.lib.v3_session(self.handle, connection)

    def uds(self, connection, request, now):
        output = (C.c_uint8 * 64)()
        length = self.lib.v3_uds(self.handle, connection, self._bytes(request), len(request), output, len(output), now)
        if length < 0:
            raise ValueError('Invalid core request/API arguments')
        return bytes(output[:length])

    def command(self, client, command_id, duty, lease, now):
        if not (0 <= client <= 0xffffffffffffffff and 0 <= command_id <= 0xffffffff and
                0 <= duty <= 65535 and 0 <= lease <= 65535):
            return 2
        return self.lib.v3_command(self.handle, client, command_id, duty, lease, now)

    def fan_status(self):
        output = (C.c_uint8 * 16)()
        self.lib.v3_fan_status(self.handle, output)
        return bytes(output)

    def telemetry(self, boot_id, now):
        output = (C.c_uint8 * 20)()
        self.lib.v3_telemetry(self.handle, boot_id, output, now)
        return bytes(output)

    def state(self, now):
        output = (C.c_int64 * 18)()
        self.lib.v3_debug(self.handle, output, now)
        return dict(zip(STATE_FIELDS, output))


def decode_status(data):
    if len(data) != 16:
        raise ValueError('FanStatus must contain 16 bytes')
    sequence, command_id, target, applied, rpm, owner, routine = struct.unpack('!IIHHHBB', data)
    return dict(sequence=sequence, command_id=command_id, target_percent=target / 100,
                applied_percent=applied / 100, actual_rpm=rpm,
                owner=OWNERS[owner], routine=ROUTINES[routine])
