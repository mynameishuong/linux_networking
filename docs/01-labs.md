# 01 — Bốn lab đầu tiên

Mỗi lab: dự đoán trước → chạy → quan sát → giải thích → ghi bằng chứng. Code mẫu là mốc đối chiếu; sau khi hiểu, tự viết lại một phiên bản nhỏ trong file riêng.

## Lab 1 — TCP trên cùng máy

Mục tiêu: biết process nào listen, process nào connect và phân biệt listening socket với connected socket.

```bash
make
# Terminal A
./build/gateway 127.0.0.1 5000
```

```bash
# Terminal B
ss -ltnp 'sport = :5000'
./build/ecu 127.0.0.1 5000 'ecu=1,seq=1,temp_c=42'
```

Kỳ vọng: `ss` thấy LISTEN, client in `Application ACK`, gateway in RX. Client kết thúc ngay sau phản hồi nên trạng thái ESTABLISHED có thể biến mất trước khi bạn chạy `ss` lần nữa.

Đọc `gateway.c` và `ecu.c`, vẽ thứ tự `socket/bind/listen/accept/connect/send/recv/close`. Trả lời: tại sao socket do `accept()` trả về khác listening socket? Tắt gateway bằng Ctrl+C rồi chạy client lại: lỗi gì và phát sinh ở syscall nào?

**Đạt khi:** tự build/chạy lại được và giải thích được vai trò địa chỉ IP, port, file descriptor.

## Lab 2 — Hai network stack, ARP và ICMP

Tắt gateway ở Lab 1. Tạo mạng:

```bash
sudo bash scripts/lab.sh up
sudo bash scripts/lab.sh status
```

Terminal A — bắt gói trước khi ping:

```bash
sudo ip netns exec eln-ecu tcpdump -i eln-e0 -nn -e 'arp or icmp'
```

Terminal B:

```bash
sudo ip -n eln-ecu neigh flush dev eln-e0
sudo bash scripts/lab.sh ping
sudo ip -n eln-ecu route get 10.22.0.2
sudo ip -n eln-ecu neigh show
```

Kỳ vọng: ARP hỏi MAC của `10.22.0.2`, ARP reply, ICMP echo request/reply. `route get` chọn interface `eln-e0`; không cần default gateway vì đích cùng subnet. Namespace có thể tạo thêm lưu lượng khác, nên capture dùng filter.

Tự làm lại thủ công trên giấy: hai namespace, một veth pair, gán IP, bật lo/interface, ping. Đọc từng lệnh trong nhánh `up` của script rồi giải thích mục đích.

**Đạt khi:** chỉ ra IP/MAC hai đầu trong capture và giải thích được ARP cache làm lần ping sau khác lần đầu.

## Lab 3 — TCP giữa ECU và gateway

Giữ mạng Lab 2. Mở ba terminal ở gốc project.

Terminal A — gateway:

```bash
sudo ip netns exec eln-gw ./build/gateway 10.22.0.2 5000
```

Terminal B — capture, để chạy cho đến sau khi client kết thúc:

```bash
mkdir -p captures
sudo ip netns exec eln-ecu tcpdump -i eln-e0 -nn -s 0 -U -w captures/telemetry.pcap 'arp or tcp port 5000'
```

Terminal C — gửi dữ liệu:

```bash
sudo ip -n eln-ecu neigh flush dev eln-e0
sudo ip netns exec eln-ecu ./build/ecu 10.22.0.2 5000 'ecu=1,seq=1,temp_c=42,rpm=1200'
```

Ctrl+C ở terminal B để đóng capture, rồi đọc:

```bash
tcpdump -nn -r captures/telemetry.pcap
tcpdump -nn -X -r captures/telemetry.pcap 'tcp port 5000'
```

Trong Wireshark mở file, dùng filter `tcp.port == 5000`, tìm SYN → SYN/ACK → ACK; chọn **Follow → TCP Stream** để xem dữ liệu ứng dụng. Tìm header length 4 byte ở chế độ hex. ACK có thể được gộp với dữ liệu; không mặc định mỗi thao tác ứng dụng tạo đúng một packet.

Đọc `net.c`: vì sao dùng `htonl/ntohl`, vì sao vòng lặp đọc/ghi tăng biến `done`, và `recv()` trả 0 khác trả -1 thế nào?

Chạy kiểm tra framing trên loopback, không cần sudo hay namespace:

```bash
make test
```

Test gửi header/payload thành nhiều lần, gửi hai frame cùng một lần, thử length 0/quá lớn, frame thiếu, idle timeout và client C. Việc chia thành nhiều lần `send` không bảo đảm kernel chia packet hoặc `recv` y hệt; test kiểm tra ứng dụng chịu được cách gửi đó. Một test timeout mất khoảng 5 giây.

**Đạt khi:** giải thích được vì sao framing cần thiết và chỉ ra đâu là TCP ACK, đâu là phản hồi ứng dụng.

## Lab 4 — Delay, loss và debug

Giữ gateway ở namespace đang chạy. Đo trước khi gây lỗi:

```bash
sudo bash scripts/lab.sh ping
sudo bash scripts/lab.sh delay
sudo ip netns exec eln-ecu tc -s qdisc show dev eln-e0
sudo ip netns exec eln-ecu ping -c 50 -i 0.1 -W 2 10.22.0.2
time sudo ip netns exec eln-ecu ./build/ecu 10.22.0.2 5000 'ecu=1,seq=2,temp_c=43'
sudo bash scripts/lab.sh clear
sudo bash scripts/lab.sh ping
```

Script thêm 50 ms delay và 1% random loss vào **egress của ECU**. RTT ping thường tăng khoảng 50 ms, không phải 100 ms; số thực tế còn phụ thuộc scheduling. Với ít packet có thể không thấy gói mất. TCP có retransmission nên mất packet không đồng nghĩa mất bản tin ứng dụng. Ghi lại cấu hình, số mẫu và capture trước khi kết luận.

Bài debug: trong terminal khác, tắt riêng interface lab rồi thử client:

```bash
sudo ip -n eln-ecu link set eln-e0 down
sudo ip netns exec eln-ecu ./build/ecu 10.22.0.2 5000 'ecu=1,seq=3,temp_c=44'
sudo ip -n eln-ecu link set eln-e0 up
```

Dựa vào thông báo lỗi, `ip -br addr`, route, neighbor và capture để xác định lỗi nằm ở đâu. Khôi phục interface trước khi chuyển bài.

**Đạt khi:** đo được ảnh hưởng delay, phân biệt packet loss với lỗi ứng dụng và tự khôi phục lab.

## Dọn lab

Ctrl+C mọi gateway/tcpdump/ping đang chạy trong hai namespace trước, rồi:

```bash
sudo bash scripts/lab.sh down
sudo bash scripts/lab.sh status
```

Script chỉ quản lý namespace `eln-ecu`, `eln-gw` và veth của lab; dành riêng các tên đó cho project. `up` từ chối ghi đè namespace đã có, `down` từ chối dọn khi còn tiến trình. Script không thay route/firewall của host.
