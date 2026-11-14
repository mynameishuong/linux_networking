# V3 — Báo cáo kiểm chứng

**Ngày chốt:** 14/11/2026. Phạm vi là runtime `advanced/` và C core, không gộp kết quả telemetry/image/Linux namespace của các bản trước.

Môi trường chạy: Windows 10 build 19045, Python 3.12.0 AMD64, Zig 0.17.1 C compiler. Native test dùng DLL Windows; cross-build Linux dùng target `x86_64-linux-musl`.

## Kết quả đã chạy

| Kiểm tra | Kết quả thực tế |
| --- | --- |
| Build C core cho Windows | PASS, Zig C compiler với C11, `-Wall -Wextra -Wpedantic -Werror` |
| Cross-build C core cho Linux x86_64/musl | PASS; tạo shared library Linux, không thực thi Linux binary tại phiên này |
| Unit/protocol/network tests | **59 tests PASS**: 30 core, 9 protocol/stream, 20 network integration |
| Demo một lệnh | PASS qua socket TCP/UDP thật, có `DEMO_PASS` và JSONL trace |
| DoIP + UDS peer độc lập | PASS với `doipclient 1.3.0`, `udsoncan 1.26.1` |
| SOME/IP + SD peer độc lập | PASS với `someip 0.3.0`, build packet và parse response/event độc lập |
| Hình workflow/UML | Source `.drawio`, xuất PNG bằng draw.io Desktop và mở kiểm tra bố cục |

Unit tests gọi **C shared library thật qua ctypes**, dùng fake clock để kiểm tra timer/state xác định. Network tests chạy ECU như process riêng với C core đó, gửi dữ liệu qua socket và clock thật. Không dùng mock actuator response thay cho toàn bộ network scenario; actuator cuối vẫn là simulator đã định nghĩa.

Lệnh xác nhận:

```bash
python -m advanced.build
python -m unittest discover -s advanced/tests -v
python -m advanced demo
python -m advanced.verify_interop
```

Lệnh interop cuối cần dependencies tùy chọn trong `advanced/requirements-interop.txt`. Phiên này cài chúng vào thư mục `.tmp/v3-peers` riêng, rồi chạy:

```bash
python -m advanced.verify_interop --deps-path ../.tmp/v3-peers
```

Tổng kết bộ test tại lần chạy cuối có đầy đủ:

```text
Ran 59 tests in 13.682s
OK
```

Thời gian chạy test không phải benchmark throughput/latency của ECU.

## Bằng chứng chức năng

Demo quan sát được các trạng thái qua SOME/IP và UDS:

| Bước | Kết quả |
| --- | --- |
| Signal 70°C, speed 0 | NORMAL_THERMAL, duty 40%, actual 2000 RPM |
| Normal service command 25% | NORMAL_SERVICE, actual 1250 RPM |
| Extended session | UDS `50 03 00 32 01 f4` |
| Start self-test 40%, 1000 ms | UDS `71 01 f0 01 01` |
| SOME/IP command khi DIAG giữ quyền | Business result 1, BUSY_DIAGNOSTIC |
| Routine result | UDS `71 03 f0 01 02 07 d0`, COMPLETED, terminal 2000 RPM |
| Release | NORMAL_THERMAL; service lease trước đó không tự chạy lại |

Tests còn xác nhận: sai routing/SA/TA/session không đến actuator; invalid length/version/range; split/coalesced frame; absolute assembly deadline; signal stale/duplicate/reorder/wrap; disconnect và session expiry; known driver fault và feedback stall; replay conflict/lease/eviction; subscription TTL/renew; negative response khi positive suppression được bật; trace correlation tới terminal result.

Interop dùng packet/request do thư viện độc lập tạo, không chỉ encode rồi decode bằng cùng codec của project:

1. `doipclient` thực hiện activation, `udsoncan` đọc DID theo codec, đổi session và start/query routine.
2. `someip` tạo request SOME/IP TCP và parse response từ server.
3. `someip` tạo SD Find/Subscribe và parse Offer/Ack, sau đó nhận notification UDP thực.

## Phần chưa kiểm chứng hoặc ngoài phạm vi

- Chưa chạy runtime trong Linux/WSL, SocketCAN/vcan hoặc topology namespace V3; mới cross-build core Linux.
- Chưa thử trên Ethernet/CAN hardware thật, chưa điều khiển GPIO/PWM vật lý.
- Chưa packet capture thực trong phiên này; JSONL trace là log application, không phải pcap.
- Chưa soak 30 phút, đo RSS/CPU, p95/p99 RTT hoặc bảo đảm tick/abort hard real-time.
- SD chỉ có unicast subset công bố trong [hướng dẫn chạy](05-v3-running-lab.md); chưa full multicast lifecycle/reboot handling.
- Chưa có AUTOSAR RTE/BSW, ARXML generation, `vsomeip` integration, TLS/diagnostic security hoặc certification ISO/AUTOSAR.

Những giới hạn trên không được suy diễn thành đã đạt chỉ vì demo/test subset pass. Xem [hướng dẫn V3](05-v3-running-lab.md) để chạy lại đúng phạm vi.
