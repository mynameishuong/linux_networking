"""Black-box integration tests against the compiled C binaries, on Linux."""
import concurrent.futures
import pathlib
import socket
import struct
import subprocess
import time
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]


def frame(payload):
    return struct.pack('!I', len(payload)) + payload


def receive_exact(sock, count):
    result = bytearray()
    while len(result) < count:
        chunk = sock.recv(count - len(result))
        if not chunk:
            raise AssertionError('Unexpected EOF')
        result.extend(chunk)
    return bytes(result)


def receive_frame(sock):
    length, = struct.unpack('!I', receive_exact(sock, 4))
    if not 1 <= length <= 1024:
        raise AssertionError(f'Invalid response length: {length}')
    return receive_exact(sock, length)


class TCPIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            cls.port = probe.getsockname()[1]
        cls.server = subprocess.Popen(
            [str(ROOT / 'build/gateway'), '127.0.0.1', str(cls.port)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        cls.addClassCleanup(cls.stop_server)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if cls.server.poll() is not None:
                raise RuntimeError('Gateway exited during startup')
            try:
                with cls.connect():
                    return
            except OSError:
                time.sleep(0.02)
        raise RuntimeError('Gateway did not start')

    @classmethod
    def stop_server(cls):
        cls.server.terminate()
        try:
            cls.server.wait(timeout=2)
        except subprocess.TimeoutExpired:
            cls.server.kill()
            cls.server.wait(timeout=2)

    @classmethod
    def connect(cls):
        return socket.create_connection(('127.0.0.1', cls.port), timeout=8)

    def assert_healthy(self):
        with self.connect() as sock:
            sock.sendall(frame(b'alive'))
            self.assertEqual(receive_frame(sock), b'alive')

    def test_c_client(self):
        result = subprocess.run(
            [str(ROOT / 'build/ecu'), '127.0.0.1', str(self.port), 'ecu=1,temp=42'],
            capture_output=True, text=True, timeout=8)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Application ACK: ecu=1,temp=42', result.stdout)

    def test_split_header_and_payload(self):
        with self.connect() as sock:
            wire = frame(b'temperature=42')
            for byte in wire:
                sock.sendall(bytes([byte]))
                time.sleep(0.005)
            self.assertEqual(receive_frame(sock), b'temperature=42')

    def test_multiple_frames_in_one_send(self):
        with self.connect() as sock:
            sock.sendall(frame(b'one') + frame(b'two'))
            self.assertEqual(receive_frame(sock), b'one')
            self.assertEqual(receive_frame(sock), b'two')

    def test_maximum_frame(self):
        with self.connect() as sock:
            payload = bytes(range(256)) * 4
            sock.sendall(frame(payload))
            self.assertEqual(receive_frame(sock), payload)

    def test_invalid_lengths_close_connection(self):
        for length in (0, 1025, 0xffffffff):
            with self.subTest(length=length), self.connect() as sock:
                sock.sendall(struct.pack('!I', length))
                self.assertEqual(sock.recv(1), b'')
            self.assert_healthy()

    def test_truncated_frames_close_connection(self):
        for wire in (b'\x00\x00', struct.pack('!I', 5), struct.pack('!I', 5) + b'ab'):
            with self.subTest(wire=wire), self.connect() as sock:
                sock.sendall(wire)
                sock.shutdown(socket.SHUT_WR)
                self.assertEqual(sock.recv(1), b'')
            self.assert_healthy()

    def test_idle_client_times_out(self):
        with self.connect() as sock:
            self.assertEqual(sock.recv(1), b'')
        self.assert_healthy()

    def test_several_clients_eventually_served(self):
        def request(number):
            with self.connect() as sock:
                payload = f'ecu={number}'.encode()
                sock.sendall(frame(payload))
                return receive_frame(sock) == payload
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            self.assertTrue(all(pool.map(request, range(4))))

    def test_invalid_cli(self):
        for binary, arguments in (
            ('ecu', ['127.0.0.1', '0', 'hello']),
            ('ecu', ['bad-ip', '5000', 'hello']),
            ('ecu', ['127.0.0.1', '5000', '']),
            ('ecu', ['127.0.0.1', '5000', 'x' * 1025]),
            ('gateway', ['127.0.0.1', '65536']),
        ):
            with self.subTest(binary=binary, arguments=arguments[:2]):
                result = subprocess.run([str(ROOT / 'build' / binary), *arguments],
                                        capture_output=True, timeout=2)
                self.assertNotEqual(result.returncode, 0)


if __name__ == '__main__':
    unittest.main()
