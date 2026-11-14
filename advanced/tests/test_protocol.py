import asyncio
import struct
import unittest
from advanced.protocol import (Endpoint, ProtocolError, SDEntry, SomeIP, doip, parse_doip,
                               parse_sd, parse_signal, read_frame, sd_packet, signal_datagram,
                               signal_payload)


class ProtocolTests(unittest.TestCase):
    def test_doip_golden_vector(self):
        uds = bytes.fromhex('31 01 f0 01 0f a0 03 e8')
        wire = doip(0x8001, bytes.fromhex('0e 00 00 01') + uds)
        self.assertEqual(wire.hex(), '03fc80010000000c0e0000013101f0010fa003e8')
        self.assertEqual(parse_doip(wire), (3, 0x8001, bytes.fromhex('0e000001') + uds))

    def test_doip_invalid_version_size_and_truncation(self):
        for wire in (b'', bytes.fromhex('03 fd 00 01 00 00 00 00'),
                     doip(1) + b'x', struct.pack('!BBHI', 3, 252, 1, 4097)):
            with self.subTest(wire=wire), self.assertRaises(ProtocolError): parse_doip(wire)

    def test_someip_golden_header(self):
        wire = SomeIP(0x1235, 1, 0x1002, 1, payload=bytes.fromhex('000000010fa003e8')).encode()
        self.assertEqual(wire.hex(), '12350001000000101002000101010000000000010fa003e8')
        self.assertEqual(SomeIP.decode(wire).payload, bytes.fromhex('000000010fa003e8'))

    def test_signal_envelope_vector(self):
        payload = signal_payload(counter=1)
        self.assertEqual(payload.hex(), '4c04000001010000')
        self.assertEqual(parse_signal(signal_datagram(payload)), payload)
        with self.assertRaises(ProtocolError): parse_signal(signal_datagram(payload, can_id=0x322))

    def test_sd_offer_options_and_subscribe_counter(self):
        options = [Endpoint('127.0.0.1', 30511, 6), Endpoint('127.0.0.1', 30512)]
        _, entries, parsed = parse_sd(sd_packet([SDEntry(1, 0x1235, count1=2)], options))
        self.assertEqual(entries[0].selected_options(parsed), options)
        entry = SDEntry(6, 0x1235, value=0x00030001, count1=1)
        _, entries, _ = parse_sd(sd_packet([entry], [Endpoint('127.0.0.2', 45678)]))
        self.assertEqual(entries[0].value, 0x00030001)

    def test_sd_bad_index_and_bad_length(self):
        with self.assertRaises(ProtocolError): parse_sd(sd_packet([SDEntry(6, 0x1235, index1=9, count1=1)]))
        wire = bytearray(sd_packet([SDEntry(0, 0x1235)]))
        wire[20:24] = struct.pack('!I', 17)
        with self.assertRaises(ProtocolError): parse_sd(bytes(wire))


class StreamTests(unittest.IsolatedAsyncioTestCase):
    async def test_coalesced_frames(self):
        reader = asyncio.StreamReader()
        first, second = doip(1), doip(0x4001)
        reader.feed_data(first + second)
        self.assertEqual(await read_frame(reader, 'doip'), first)
        self.assertEqual(await read_frame(reader, 'doip'), second)

    async def test_split_frame(self):
        reader = asyncio.StreamReader()
        wire = doip(0x8001, b'abcdef')
        async def feed():
            for byte in wire:
                reader.feed_data(bytes([byte]))
                await asyncio.sleep(0.001)
        task = asyncio.create_task(feed())
        self.assertEqual(await read_frame(reader, 'doip'), wire)
        await task

    async def test_slow_frame_has_absolute_deadline(self):
        reader = asyncio.StreamReader()
        reader.feed_data(b'\x03')
        async def feed():
            for byte in doip(1)[1:]:
                await asyncio.sleep(0.02)
                reader.feed_data(bytes([byte]))
        task = asyncio.create_task(feed())
        try:
            with self.assertRaises(ProtocolError):
                await read_frame(reader, 'doip', frame_seconds=0.05)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


if __name__ == '__main__':
    unittest.main()
