"""Black-box image transfers against the C executables; requires POSIX/Linux."""
import base64
import hashlib
import os
import pathlib
import selectors
import socket
import struct
import subprocess
import tempfile
import threading
import time
import unittest
import zlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
BIN = pathlib.Path(os.environ.get('IMAGE_TEST_BIN', ROOT / 'build'))
MAX_SIZE = 10 * 1024 * 1024
PNG = base64.b64decode((ROOT / 'assets/sample.png.b64').read_bytes())


def exact(sock, count):
    data = bytearray()
    while len(data) < count:
        chunk = sock.recv(count - len(data))
        if not chunk:
            raise AssertionError('Unexpected EOF')
        data.extend(chunk)
    return bytes(data)


def header(data, checksum=None):
    crc = zlib.crc32(data) if checksum is None else checksum
    return struct.pack('!4sII', b'IMG1', len(data), crc)


@unittest.skipUnless(os.name == 'posix', 'C sender/receiver require a POSIX runtime')
class ImageTransfer(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='eln-image-')
        self.addCleanup(self.directory.cleanup)
        self.folder = pathlib.Path(self.directory.name)
        self.output = self.folder / 'received image.png'

    def start_receiver(self, timeout=30000):
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            self.port = probe.getsockname()[1]
        self.receiver = subprocess.Popen(
            [str(BIN / 'image_receiver'), '127.0.0.1', str(self.port),
             str(self.output), str(timeout)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.addCleanup(self.stop_receiver)
        with selectors.DefaultSelector() as selector:
            selector.register(self.receiver.stdout, selectors.EVENT_READ)
            self.assertTrue(selector.select(timeout=5), 'No receiver readiness output')
        line = self.receiver.stdout.readline()
        self.assertIn('listening', line)

    def stop_receiver(self):
        if self.receiver.poll() is None:
            self.receiver.terminate()
            try:
                self.receiver.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                self.receiver.kill()
                self.receiver.communicate(timeout=2)
        else:
            self.receiver.communicate()

    def finish(self, success):
        stdout, stderr = self.receiver.communicate(timeout=10)
        if success:
            self.assertEqual(self.receiver.returncode, 0, stderr)
        else:
            self.assertNotEqual(self.receiver.returncode, 0, stdout)
        self.assertEqual(list(self.folder.glob(self.output.name + '.part.*')), [])

    def exchange(self, wire, split=False):
        with socket.create_connection(('127.0.0.1', self.port), timeout=20) as sock:
            if split:
                for byte in wire[:12]:
                    sock.sendall(bytes([byte]))
                    time.sleep(0.003)
                for offset in range(12, len(wire), 137):
                    sock.sendall(wire[offset:offset + 137])
            else:
                sock.sendall(wire)
            sock.shutdown(socket.SHUT_WR)
            magic, status, count, crc = struct.unpack('!4sIII', exact(sock, 16))
            self.assertEqual(magic, b'ACK1')
            return status, count, crc

    def test_c_sender_transfers_real_png(self):
        source = self.folder / 'input image.png'
        source.write_bytes(PNG)
        self.start_receiver()
        result = subprocess.run([str(BIN / 'image_sender'), '127.0.0.1', str(self.port), str(source)],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Image ACK:', result.stdout)
        self.finish(True)
        self.assertEqual(self.output.read_bytes(), PNG)

    def test_binary_larger_than_telemetry_frame_split_across_sends(self):
        data = bytes(range(256)) * 1025 + b'\x00\xff\x00'
        self.start_receiver()
        self.assertEqual(self.exchange(header(data) + data, split=True),
                         (0, len(data), zlib.crc32(data)))
        self.finish(True)
        self.assertEqual(hashlib.sha256(self.output.read_bytes()).digest(), hashlib.sha256(data).digest())

    def test_exact_maximum_size(self):
        data = bytes(range(256)) * (MAX_SIZE // 256)
        self.start_receiver()
        self.assertEqual(self.exchange(header(data) + data), (0, MAX_SIZE, zlib.crc32(data)))
        self.finish(True)
        self.assertEqual(self.output.stat().st_size, MAX_SIZE)
        self.assertEqual(hashlib.sha256(self.output.read_bytes()).digest(), hashlib.sha256(data).digest())

    def test_bad_checksum_does_not_publish(self):
        self.start_receiver()
        status, _, _ = self.exchange(header(PNG, zlib.crc32(PNG) ^ 1) + PNG)
        self.assertEqual(status, 2)
        self.finish(False)
        self.assertFalse(self.output.exists())

    def test_truncated_body_does_not_publish(self):
        self.start_receiver()
        status, _, _ = self.exchange(header(PNG) + PNG[:5])
        self.assertEqual(status, 4)
        self.finish(False)
        self.assertFalse(self.output.exists())

    def test_trailing_bytes_rejected(self):
        self.start_receiver()
        status, _, _ = self.exchange(header(PNG) + PNG + b'X')
        self.assertEqual(status, 4)
        self.finish(False)
        self.assertFalse(self.output.exists())

    def test_invalid_header_and_lengths(self):
        for wire in (b'NOPE' + struct.pack('!II', 1, 0),
                     struct.pack('!4sII', b'IMG1', 0, 0),
                     struct.pack('!4sII', b'IMG1', MAX_SIZE + 1, 0)):
            with self.subTest(wire=wire):
                self.start_receiver()
                self.assertEqual(self.exchange(wire)[0], 1)
                self.finish(False)
                self.assertFalse(self.output.exists())

    def test_existing_output_is_preserved(self):
        self.output.write_bytes(b'keep me')
        result = subprocess.run([str(BIN / 'image_receiver'), '127.0.0.1', '5001', str(self.output)],
                                capture_output=True, timeout=3)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.output.read_bytes(), b'keep me')

    def test_output_created_after_listen_is_not_replaced(self):
        self.start_receiver()
        self.output.write_bytes(b'created concurrently')
        self.assertEqual(self.exchange(header(PNG) + PNG)[0], 3)
        self.finish(False)
        self.assertEqual(self.output.read_bytes(), b'created concurrently')

    def test_missing_output_directory(self):
        self.output = self.folder / 'missing' / 'image.png'
        self.start_receiver()
        # Header alone: disk setup fails before a body is needed.
        self.assertEqual(self.exchange(header(PNG))[0], 3)
        self.finish(False)
        self.assertFalse(self.output.exists())

    def test_deadline_not_reset_by_slow_header(self):
        self.start_receiver(timeout=250)
        start = time.monotonic()
        with socket.create_connection(('127.0.0.1', self.port), timeout=3) as sock:
            for byte in header(PNG)[:3]:
                sock.sendall(bytes([byte]))
                time.sleep(0.10)
        self.finish(False)
        self.assertLess(time.monotonic() - start, 2.5)
        self.assertFalse(self.output.exists())

    def test_sender_rejects_empty_oversized_and_invalid_cli(self):
        empty = self.folder / 'empty.png'
        empty.write_bytes(b'')
        large = self.folder / 'large.png'
        with large.open('wb') as file:
            file.truncate(MAX_SIZE + 1)
        for args in (['127.0.0.1', '5001', str(empty)],
                     ['127.0.0.1', '5001', str(large)],
                     ['127.0.0.1', '5001', str(empty), '0'],
                     []):
            with self.subTest(args=args):
                result = subprocess.run([str(BIN / 'image_sender'), *args],
                                        capture_output=True, timeout=3)
                self.assertNotEqual(result.returncode, 0)

    def test_sender_rejects_invalid_or_negative_ack(self):
        source = self.folder / 'source.png'
        source.write_bytes(PNG)
        variants = (struct.pack('!4sIII', b'BAD!', 0, len(PNG), zlib.crc32(PNG)),
                    struct.pack('!4sIII', b'ACK1', 2, len(PNG), zlib.crc32(PNG)),
                    struct.pack('!4sIII', b'ACK1', 0, len(PNG) - 1, zlib.crc32(PNG)),
                    struct.pack('!4sIII', b'ACK1', 0, len(PNG), zlib.crc32(PNG) ^ 1),
                    b'ACK1')
        for ack in variants:
            with self.subTest(ack=ack), socket.socket() as listener:
                listener.bind(('127.0.0.1', 0))
                listener.listen(1)
                listener.settimeout(5)
                port = listener.getsockname()[1]
                errors = []

                def peer():
                    try:
                        with listener.accept()[0] as sock:
                            sock.settimeout(5)
                            while sock.recv(4096):
                                pass
                            sock.sendall(ack)
                    except Exception as error:
                        errors.append(error)

                thread = threading.Thread(target=peer, daemon=True)
                thread.start()
                result = subprocess.run([str(BIN / 'image_sender'), '127.0.0.1', str(port), str(source)],
                                        capture_output=True, timeout=8)
                thread.join(timeout=6)
                self.assertFalse(thread.is_alive())
                self.assertEqual(errors, [])
                self.assertNotEqual(result.returncode, 0)


if __name__ == '__main__':
    unittest.main()
