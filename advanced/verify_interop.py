"""Check actual wire interoperability with optional independently authored libraries."""
import argparse
import ipaddress
from pathlib import Path
import socket
import struct
import sys
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--deps-path', type=Path, help='Optional isolated pip --target directory')
    args = parser.parse_args()
    if args.deps_path:
        sys.path.insert(0, str(args.deps_path.resolve()))
    from doipclient import DoIPClient
    from doipclient.connectors import DoIPClientUDSConnector
    from udsoncan.client import Client
    from someip import header as external
    from .client import SignalPublisher, exact
    from .process import LabProcess

    with LabProcess(test_enable=True) as lab:
        host, ports = lab.info['host'], lab.info['ports']
        with SignalPublisher(host, ports['signal']):
            time.sleep(0.12)
            doip = DoIPClient(host, 1, tcp_port=ports['doip'], udp_port=ports['doip'],
                              protocol_version=3, client_logical_address=0x0e10)
            try:
                connection = DoIPClientUDSConnector(doip)
                config = {'data_identifiers': {0xf200: '>hBH'}}
                with Client(connection, config=config, request_timeout=2) as uds:
                    result = uds.read_data_by_identifier(0xf200)
                    assert result.service_data.values[0xf200][0] == 7000
                    uds.change_session(3)
                    result = uds.start_routine(0xf001, data=struct.pack('!HH', 4000, 300))
                    assert result.service_data.routine_status_record == b'\x01'
                    time.sleep(0.35)
                    result = uds.get_routine_result(0xf001)
                    assert result.service_data.routine_status_record == bytes.fromhex('02 07 d0')
                print('PASS: doipclient + udsoncan: routing, DID codec, session, routine start/result')
            finally:
                doip.close()

            with socket.create_connection((host, ports['fan']), timeout=3) as sock:
                request = external.SOMEIPHeader(
                    service_id=0x1235, method_id=2, client_id=0x1003, session_id=1,
                    interface_version=1, message_type=external.SOMEIPMessageType.REQUEST)
                sock.sendall(request.build())
                header = exact(sock, 16)
                length = struct.unpack_from('!I', header, 4)[0]
                response, rest = external.SOMEIPHeader.parse(header + exact(sock, length - 8))
                assert not rest and response.return_code == external.SOMEIPReturnCode.E_OK
                assert len(response.payload) == 16 and response.payload[14] == 1
                print('PASS: independently built SOME/IP TCP request and parsed response')

            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sd, socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as events:
                sd.bind(('127.0.0.2', ports['sd'])); sd.settimeout(3)
                events.bind(('127.0.0.2', 0)); events.settimeout(3)
                counter = 0

                def send(entries):
                    nonlocal counter
                    counter += 1
                    payload = external.SOMEIPSDHeader(tuple(entries), flag_reboot=True).assign_option_indexes().build()
                    wire = external.SOMEIPHeader(0xffff, 0x8100, 0, counter, 1,
                                                external.SOMEIPMessageType.NOTIFICATION, payload=payload).build()
                    sd.sendto(wire, (host, ports['sd']))

                find = external.SOMEIPSDEntry(external.SOMEIPSDEntryType.FindService, 0x1235, 1, 1, 0, 0)
                send([find])
                offer_message, rest = external.SOMEIPHeader.parse(sd.recvfrom(4096)[0])
                offer, rest = external.SOMEIPSDHeader.parse(offer_message.payload)
                offer = offer.resolve_options()
                assert offer.entries[0].service_id == 0x1235 and len(offer.entries[0].options_1) == 2
                endpoint = external.IPv4EndpointOption(ipaddress.IPv4Address('127.0.0.2'), 17, events.getsockname()[1])
                subscribe = external.SOMEIPSDEntry(external.SOMEIPSDEntryType.Subscribe, 0x1235, 1, 1, 3, 1,
                                                  options_1=(endpoint,))
                send([subscribe])
                ack_message, _ = external.SOMEIPHeader.parse(sd.recvfrom(4096)[0])
                ack, _ = external.SOMEIPSDHeader.parse(ack_message.payload)
                assert ack.entries[0].ttl == 3
                notification, rest = external.SOMEIPHeader.parse(events.recvfrom(4096)[0])
                assert not rest and notification.method_id == 0x8001 and len(notification.payload) == 16
                print('PASS: independently built SD Find/Subscribe, parsed Offer/Ack and real UDP event')
        print('Interop checks passed; scope is this lab subset, not AUTOSAR/ISO certification.')


if __name__ == '__main__':
    main()
