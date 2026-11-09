# 02 — Phát triển một project theo từng mốc

Mục tiêu dài hạn: Virtual Embedded Gateway. Mỗi mốc mở rộng hệ thống đã chạy, có capture và ghi chú chứng minh. Thời gian dưới đây là gợi ý cho khoảng 8–10 giờ học/tuần, không phải cam kết đạt trình độ expert.

**Ưu tiên mới sau phần cơ bản:** tập trung DoIP/UDS, signal/message và đường xử lý đến actuator. Xem [lộ trình V3 chuyên sâu](03-doip-signal-actuator.md#12-thứ-tự-học-sau-phần-cơ-bản): signal codec → actuator simulator → UDS → DoIP → SOME/IP và arbitration. Các mốc socket/Linux bên dưới vẫn là nền, có thể xen kẽ theo nhu cầu của V3.

| Mốc | Thời gian gợi ý | Bạn làm thêm | Bằng chứng hoàn thành |
| --- | --- | --- | --- |
| M0: Linux và gói tin | Tuần 1–2 | Làm 4 lab có sẵn; học process, fd, subnet, ARP, route | Capture ARP/ICMP/TCP và giải thích đường đi |
| M1: Telemetry thực tế hơn | Tuần 3–4 | ECU gửi định kỳ; thêm sequence, timestamp; gateway parse và ghi CSV | Gửi 1.000 bản tin; kiểm tra số thứ tự, nội dung, frame lỗi |
| M2: UDP | Tuần 5–6 | Thêm binary UDP riêng bằng `sendto/recvfrom`; thống kê sequence thiếu/trùng/out-of-order | So sánh TCP/UDP khi netem có loss; định nghĩa cách tính loss |
| M3: Nhiều ECU | Tuần 7–8 | Nonblocking + `poll`, sau đó `epoll`; mỗi connection có input/output buffer riêng | 10 client đồng thời; một client chậm không giữ các client còn lại |
| M4: Hồi phục kết nối | Tuần 9–10 | Reconnect backoff, heartbeat, deadline toàn frame, giới hạn queue | Kill/restart gateway; mô tả bản tin nào được retry hoặc mất |
| M5: Linux router | Tuần 11–12 | Namespace thứ ba, hai subnet; bật IP forwarding chỉ trong router namespace | Capture hai phía cho thấy MAC đổi, IP end-to-end giữ nguyên, TTL giảm |
| M6: VLAN và bridge | Sau M5 | Bridge có VLAN filtering, hai nhóm ECU, routing giữa VLAN | Capture/tag và test isolation chứng minh cấu hình đúng |
| M7: Kernel observability | Sau M6 | Đọc đường đi socket→TCP/IP→netdev; tracepoints/ftrace rồi eBPF | Trace có kernel version và phương pháp đo rõ ràng |
| M8: Embedded target | Sau M4 hoặc M7 | Chạy ECU trên Raspberry Pi, gateway trên Linux VM; cross compile sau khi native ổn | Log và capture trên hai máy, test rút/cắm mạng |
| M9: Driver trong VM | Nhánh chuyên sâu | Module virtual netdev, TX/RX giả lập; sau đó nghiên cứu MAC/PHY/NAPI/DMA thật | Load/unload, ping, counters, theo dõi lỗi và tài nguyên |

M0 đã có hướng dẫn và code baseline. M1 trở đi là **bài tập chưa triển khai**; không cần làm đồng thời. Automotive SOME/IP, SocketCAN và TSN để sau khi socket, routing và debug đã vững.

## Việc nên tự code ngay sau bốn lab

1. Cho ECU gửi 10 bản tin trên **cùng một kết nối**, tăng `seq` sau mỗi bản tin và đợi phản hồi.
2. Gateway đếm tổng bản tin của connection rồi in khi disconnect.
3. Gửi frame thiếu payload, quan sát gateway đóng kết nối mà vẫn nhận client mới.
4. Giữ một client kết nối và gửi đều; mở client thứ hai để thấy giới hạn server tuần tự.
5. Trước khi sửa `poll/epoll`, vẽ state machine: đọc header → đọc payload → chờ gửi phản hồi → quay lại đọc header.

Với nonblocking socket, `EAGAIN` có nghĩa “chưa sẵn sàng”, khác timeout trong bản blocking này. Không bê nguyên vòng lặp `recv_exact()` vào event loop: nó chưa giữ trạng thái frame giữa các sự kiện.

## Lịch tuần đầu

| Buổi | Nội dung | Kết quả cần lưu |
| --- | --- | --- |
| 1 | Setup và Lab 1 | Sơ đồ socket calls, log chạy loopback |
| 2 | Linux process, fd, IP, subnet | Giải thích `127.0.0.1`, `/24`, port |
| 3 | Lab 2 | Capture ARP và ICMP, bảng IP/MAC |
| 4 | Lab 3 | TCP handshake, length header trong hex |
| 5 | Lab 4 và review | Bảng RTT trước/sau, một ca debug tự xử lý |

Nếu chưa đọc được C cơ bản, dành thêm thời gian cho pointer, buffer, struct, `sizeof`, kiểu integer và giá trị trả về syscall trước khi sang M1.

## Cách tự đánh giá

Mỗi milestone chỉ hoàn thành khi bạn có đủ: cách tái hiện, kết quả đo/capture, lời giải thích bằng ngôn ngữ của mình và một ca lỗi đã debug. Chạy script thành công mới chỉ là bước đầu.

Nguồn đọc ngay: các manual [network_namespaces](https://man7.org/linux/man-pages/man7/network_namespaces.7.html), [veth](https://man7.org/linux/man-pages/man4/veth.4.html), [TCP](https://man7.org/linux/man-pages/man7/tcp.7.html), [recv](https://man7.org/linux/man-pages/man2/recv.2.html), [socket options](https://man7.org/linux/man-pages/man7/socket.7.html). Đọc đúng phần phục vụ lab đang làm.
