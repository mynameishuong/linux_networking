# Virtual ECU Telemetry Lab

Project học **Embedded Linux Networking bằng C**, bắt đầu từ số 0 rồi mở rộng trên cùng một hệ thống. Một ECU giả lập gửi dữ liệu cảm biến đến gateway; bạn dùng công cụ Linux để giải thích dữ liệu đi qua mạng như thế nào.

```text
ECU process (C)                                Gateway process (C)
socket → connect → send/recv                    socket → bind → listen → accept
          │                                              │
     TCP/IP stack                                   TCP/IP stack
          │                                              │
  eln-e0: 10.22.0.1/24  ═══════ veth ═══════  eln-g0: 10.22.0.2/24
  namespace eln-ecu                             namespace eln-gw
```

Hai namespace có network stack, interface và route riêng; veth nối chúng. Xem [network_namespaces(7)](https://man7.org/linux/man-pages/man7/network_namespaces.7.html) và [veth(4)](https://man7.org/linux/man-pages/man4/veth.4.html).

Tên “gateway” ở phiên bản đầu là **ứng dụng nhận dữ liệu**, chưa phải IP router. Chưa cần Raspberry Pi hoặc phần cứng automotive.

## Bắt đầu ở đâu?

Đọc [thiết kế tổng thể, workflow, spec V1 và hướng SOME/IP V2](PROJECT_DESIGN_SPEC.md) để hiểu code đã viết và kiến trúc phát triển.

1. Đọc [Chuẩn bị Linux](docs/00-setup.md).
2. Làm [các lab đầu tiên](docs/01-labs.md) theo thứ tự: loopback → ARP/ping → TCP → delay/loss.
3. Dùng [các mốc phát triển](docs/02-roadmap.md) để tự viết thêm tính năng.
4. Ghi kết quả vào [nhật ký học](notes/journal.md). Chỉ đánh dấu hoàn thành sau khi giải thích và chạy lại được.

## Những gì đã có

| Thành phần | Vai trò |
| --- | --- |
| `src/gateway.c` | TCP server, xử lý lần lượt từng client, echo dữ liệu để xác nhận |
| `src/ecu.c` | TCP client, gửi một bản tin cảm biến, kiểm tra phản hồi |
| `src/net.c` | Framing, byte order, partial send/recv, timeout socket |
| `scripts/lab.sh` | Tạo/dọn mạng ảo, kiểm tra ping, thêm delay/loss |
| `tests/test_tcp.py` | Integration test với các binary C thật |

Ví dụ nhanh **trong Linux**, chạy ở thư mục project:

```bash
make
# Terminal 1
./build/gateway 127.0.0.1 5000
```

```bash
# Terminal 2, cũng ở thư mục project
./build/ecu 127.0.0.1 5000 'ecu=1,seq=1,temp_c=42,rpm=1200'
make test
```

Kết quả client mong đợi:

```text
Application ACK: ecu=1,seq=1,temp_c=42,rpm=1200
```

`Application ACK` là phản hồi do gateway gửi trong payload, khác với cờ ACK của TCP.

## Giao thức phiên bản 1

```text
| payload_length: uint32, network byte order | payload: 1..1024 byte |
|                  4 byte                   |  UTF-8 text cho demo |
```

Ví dụ `hello`: header `00 00 00 05`, sau đó 5 byte `68 65 6c 6c 6f`. Không gửi byte NUL kết thúc chuỗi. Giới hạn tính theo **byte**, không theo số ký tự tiếng Việt. Gateway echo nguyên payload và chưa phân tích trường telemetry.

TCP là byte stream và không giữ ranh giới bản tin. Vì vậy code phải đọc đủ header, kiểm tra length, rồi đọc đủ payload; một `send()` không tương ứng một `recv()` hay một packet. Tham khảo [tcp(7)](https://man7.org/linux/man-pages/man7/tcp.7.html) và [recv(2)](https://man7.org/linux/man-pages/man2/recv.2.html).

## Giới hạn để bạn học tiếp

- IPv4 dạng số; chưa DNS/IPv6, UDP, reconnect hoặc `epoll`.
- Server có thể nhận nhiều client theo thời gian, nhưng chỉ xử lý **một kết nối đang hoạt động**. Backlog không phải cơ chế xử lý đồng thời.
- Socket đặt timeout 5 giây cho thao tác I/O blocking; đây không phải deadline 5 giây cho toàn bản tin. Client gửi chậm nhưng liên tục vẫn có thể giữ server lâu hơn. Hành vi timeout xem [socket(7)](https://man7.org/linux/man-pages/man7/socket.7.html).
- Payload test có thể là binary, nhưng log chỉ dành cho text. Chưa lưu trữ, xác thực hoặc mã hóa; dùng cho lab.
- veth giúp học network stack; nó không mô phỏng MAC/PHY, DMA, IRQ hay Ethernet driver thật.

## Trạng thái kiểm chứng khi khởi tạo

Đã kiểm tra cú pháp Bash và Python trên máy Windows. Chưa build/chạy binary C hoặc tạo namespace vì phiên làm việc này chưa có Linux runtime dùng được. Chạy `make test` trong Linux để kiểm tra framing, nhiều bản tin cùng stream, frame lỗi, disconnect, timeout và client C.
# linux_networking
