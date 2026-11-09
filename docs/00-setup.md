# 00 — Chuẩn bị Linux

## Môi trường

Source đặt tại:

```text
C:\Users\huynh\OneDrive\Máy tính\Project_22_11_2026\linux_networking
```

PowerShell dùng để mở/chỉnh sửa file. Build C, chạy socket demo và lab namespace thực hiện trong Linux. Git Bash không có Linux kernel nên không chạy được lab namespace.

Nếu đã có Ubuntu VM hoặc board Linux, copy thư mục project vào đó và chuyển đến mục cài công cụ. Nếu dùng WSL2, làm theo [hướng dẫn Microsoft](https://learn.microsoft.com/en-us/windows/wsl/install). Lệnh cài trong PowerShell **Administrator**:

```powershell
wsl --install -d Ubuntu
```

Khởi động lại nếu trình cài yêu cầu, mở Ubuntu và tạo user. Kiểm tra trong PowerShell:

```powershell
wsl --list --verbose
```

Ubuntu cần dùng VERSION 2 cho môi trường Linux của bài lab. Nếu cài WSL gặp lỗi hoặc namespace/netem không được kernel hỗ trợ, dùng Ubuntu VM; phần kernel/driver về sau cũng nên dùng VM riêng.

## Cài công cụ trong Ubuntu

```bash
sudo apt update
sudo apt install -y build-essential python3 iproute2 iputils-ping tcpdump ethtool gdb
```

Kiểm tra:

```bash
uname -r
cc --version
ip -V
tcpdump --version
```

## Đưa source vào filesystem Linux

Trong Ubuntu WSL, có thể copy từ đường dẫn Windows đã mount. Dùng một thư mục đích mới để tránh ghi đè bài làm trước đó:

```bash
mkdir -p ~/projects
cp -r '/mnt/c/Users/huynh/OneDrive/Máy tính/Project_22_11_2026/linux_networking' ~/projects/linux-networking-lab
cd ~/projects/linux-networking-lab
make
make test
```

Nếu đích đã tồn tại, mở thư mục đó hoặc chọn tên đích khác. Bản copy trong `~/projects` và bản Windows là hai bản riêng; sửa source trong bản bạn đang build. Bạn cũng có thể `cd` trực tiếp vào đường dẫn `/mnt/c/.../linux_networking` để dùng chung source, nhưng thao tác file/build có thể chậm hơn.

Với Ubuntu VM: dùng shared folder, `scp`, hoặc copy thư mục vào home của VM rồi chạy các lệnh tương tự. Không cần bridge VM ra LAN để chạy mạng ảo nội bộ.

## Nếu gặp lỗi

| Lỗi | Kiểm tra |
| --- | --- |
| `make: command not found` | Cài `build-essential` trong Ubuntu |
| `Operation not permitted` khi tạo namespace | Chạy script bằng `sudo` trên Linux có quyền tạo namespace |
| `Unknown qdisc netem` | Kernel chưa có netem; thử `sudo modprobe sch_netem`, hoặc dùng Ubuntu VM |
| `Address already in use` | Xem `ss -ltnp`, dừng server cũ hoặc đổi port cả hai đầu |
| `Connection refused` | Gateway đã chạy chưa, IP/port đúng chưa? |
| `Resource temporarily unavailable` | Có thể là socket timeout; xem capture, route, tiến trình bên kia |
| `Permission denied` chạy script | Dùng `sudo bash scripts/lab.sh up`, không cần chmod |
| Lỗi có ký tự `\r` | File bị CRLF; lưu `.sh` dạng LF. Repo đã có `.gitattributes` |

Mọi lệnh trong tài liệu còn lại chạy ở **gốc project trong Linux**; mỗi terminal mới cần `cd` đến đó.
