"""Bounded DoIP/SOME-IP framing and the IPv4 unicast SOME/IP-SD lab subset."""
import asyncio
from dataclasses import dataclass
import socket
import struct

MAX_PAYLOAD = 4096
FAN_SERVICE, TELEMETRY_SERVICE = 0x1235, 0x1234
SD_SERVICE, SD_METHOD = 0xffff, 0x8100
CAN_ID = 0x321
DOIP_HEADER = struct.Struct('!BBHI')
SOMEIP_HEADER = struct.Struct('!HHIHHBBBB')


class ProtocolError(ValueError):
    def __init__(self, text, nack=4):
        super().__init__(text)
        self.nack = nack


class IdleTimeout(TimeoutError):
    pass


def doip(kind, payload=b'', version=3):
    return DOIP_HEADER.pack(version, version ^ 0xff, kind, len(payload)) + payload


def parse_doip(data, discovery=False):
    if len(data) < 8:
        raise ProtocolError('Truncated DoIP header')
    version, inverse, kind, length = DOIP_HEADER.unpack_from(data)
    if inverse != version ^ 0xff or version not in ((2, 3, 255) if discovery else (2, 3)):
        raise ProtocolError('Bad DoIP version/inverse', 0)
    if length > MAX_PAYLOAD:
        raise ProtocolError('DoIP body too large', 2)
    if len(data) != length + 8:
        raise ProtocolError('DoIP length mismatch')
    return version, kind, data[8:]


@dataclass(frozen=True)
class SomeIP:
    service: int
    method: int
    client: int = 0
    session: int = 0
    interface: int = 1
    message_type: int = 0
    return_code: int = 0
    payload: bytes = b''
    protocol: int = 1

    def encode(self):
        return SOMEIP_HEADER.pack(self.service, self.method, len(self.payload) + 8,
                                  self.client, self.session, self.protocol, self.interface,
                                  self.message_type, self.return_code) + self.payload

    @classmethod
    def decode(cls, data):
        if len(data) < 16:
            raise ProtocolError('Truncated SOME/IP header')
        service, method, length, client, session, protocol, interface, kind, code = SOMEIP_HEADER.unpack_from(data)
        if length < 8 or length > MAX_PAYLOAD + 8 or len(data) != length + 8:
            raise ProtocolError('SOME/IP length mismatch')
        return cls(service, method, client, session, interface, kind, code, data[16:], protocol)


async def read_frame(reader, family, idle_seconds=30, frame_seconds=1):
    """Idle time is separate; one absolute budget covers the rest of this frame."""
    try:
        first = await asyncio.wait_for(reader.readexactly(1), idle_seconds)
    except asyncio.TimeoutError as error:
        raise IdleTimeout from error
    header_size = 8 if family == 'doip' else 16

    async def rest():
        header = first + await reader.readexactly(header_size - 1)
        if family == 'doip':
            version, inverse, _, length = DOIP_HEADER.unpack(header)
            if version not in (2, 3) or inverse != version ^ 0xff:
                raise ProtocolError('Bad DoIP version/inverse', 0)
        else:
            length = SOMEIP_HEADER.unpack(header)[2]
            if length < 8:
                raise ProtocolError('SOME/IP length below 8')
            length -= 8
        if length > MAX_PAYLOAD:
            raise ProtocolError('Payload exceeds lab cap', 2)
        return header + await reader.readexactly(length)

    try:
        return await asyncio.wait_for(rest(), frame_seconds)
    except asyncio.TimeoutError as error:
        raise ProtocolError('Frame assembly deadline exceeded') from error


def signal_payload(temperature=70.0, speed=0.0, counter=0, valid=True):
    raw = round((temperature + 40) * 10)
    raw_speed = round(speed * 100)
    if not (0 <= raw <= 1900 and 0 <= raw_speed <= 30000):
        raise ValueError('Lab signal range: -40..150 C, 0..300 km/h')
    return struct.pack('<HHBBH', raw, raw_speed, counter & 255, int(valid), 0)


def signal_datagram(payload, can_id=CAN_ID):
    return b'CAN1' + struct.pack('!IB', can_id, len(payload)) + payload


def parse_signal(data):
    if len(data) != 17 or data[:4] != b'CAN1':
        raise ProtocolError('Lab UDP CAN envelope must contain 17 bytes')
    can_id, dlc = struct.unpack_from('!IB', data, 4)
    if can_id != CAN_ID or dlc != 8:
        raise ProtocolError('Unsupported CAN ID/DLC')
    return data[9:]


@dataclass(frozen=True)
class Endpoint:
    address: str
    port: int
    protocol: int = 17

    def encode(self):
        return struct.pack('!HBB4sBBH', 9, 4, 0, socket.inet_aton(self.address), 0, self.protocol, self.port)


@dataclass(frozen=True)
class SDEntry:
    kind: int
    service: int
    instance: int = 1
    major: int = 1
    ttl: int = 3
    value: int = 0  # Minor version or reserved/counter/eventgroup.
    index1: int = 0
    index2: int = 0
    count1: int = 0
    count2: int = 0

    def encode(self):
        if not 0 <= self.ttl <= 0xffffff:
            raise ValueError('TTL does not fit 24 bits')
        return (struct.pack('!BBBBHHB', self.kind, self.index1, self.index2,
                            self.count1 << 4 | self.count2, self.service, self.instance, self.major)
                + self.ttl.to_bytes(3, 'big') + struct.pack('!I', self.value))

    def selected_options(self, options):
        if self.index1 + self.count1 > len(options) or self.index2 + self.count2 > len(options):
            raise ProtocolError('SD option index out of bounds')
        return options[self.index1:self.index1 + self.count1] + options[self.index2:self.index2 + self.count2]


def sd_packet(entries, options=(), session=1, reboot=True):
    body_entries = b''.join(entry.encode() for entry in entries)
    body_options = b''.join(option.encode() for option in options)
    payload = (bytes([0x40 | (0x80 if reboot else 0), 0, 0, 0])
               + struct.pack('!I', len(body_entries)) + body_entries
               + struct.pack('!I', len(body_options)) + body_options)
    return SomeIP(SD_SERVICE, SD_METHOD, session=session, message_type=2, payload=payload).encode()


def parse_sd(data):
    message = SomeIP.decode(data)
    if (message.service, message.method, message.protocol, message.interface,
        message.message_type, message.return_code, message.client) != (SD_SERVICE, SD_METHOD, 1, 1, 2, 0, 0):
        raise ProtocolError('Not a supported SD notification')
    payload = message.payload
    if len(payload) < 12 or payload[1:4] != b'\x00\x00\x00':
        raise ProtocolError('Truncated SD header/reserved bytes')
    entry_size = struct.unpack_from('!I', payload, 4)[0]
    end = 8 + entry_size
    if entry_size % 16 or end + 4 > len(payload):
        raise ProtocolError('Bad SD entry length')
    option_size = struct.unpack_from('!I', payload, end)[0]
    if end + 4 + option_size != len(payload):
        raise ProtocolError('Bad SD option length')
    options, offset = [], end + 4
    while offset < len(payload):
        if offset + 3 > len(payload):
            raise ProtocolError('Truncated option')
        length, kind = struct.unpack_from('!HB', payload, offset)
        next_offset = offset + 3 + length
        if next_offset > len(payload) or not length:
            raise ProtocolError('Invalid option size')
        body = payload[offset + 3:next_offset]
        if kind == 4 and length == 9:
            if body[0] or body[5]:
                raise ProtocolError('Endpoint reserved bytes')
            options.append(Endpoint(socket.inet_ntoa(body[1:5]), struct.unpack_from('!H', body, 7)[0], body[6]))
        else:
            options.append(None)  # Unknown options retain their index, but are never used as endpoints.
        offset = next_offset
    entries = []
    for offset in range(8, end, 16):
        kind, i1, i2, count, service, instance, major = struct.unpack_from('!BBBBHHB', payload, offset)
        ttl = int.from_bytes(payload[offset + 9:offset + 12], 'big')
        value = struct.unpack_from('!I', payload, offset + 12)[0]
        entry = SDEntry(kind, service, instance, major, ttl, value, i1, i2, count >> 4, count & 15)
        entry.selected_options(options)
        entries.append(entry)
    return message, entries, options
