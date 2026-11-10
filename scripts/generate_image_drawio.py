"""Create editable draw.io diagrams. Export PNGs with draw.io Desktop afterwards."""
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'docs/diagrams'
BLUE = ('#e8f0fe', '#3973bd')
TEAL = ('#e3f4ef', '#27856b')
ORANGE = ('#fff1d6', '#c38b2b')
RED = ('#fce8e6', '#ba4942')
GRAY = ('#f3f5f8', '#738196')


class Drawing:
    def __init__(self, name, width, height):
        self.file = ET.Element('mxfile', host='app.diagrams.net', type='device')
        diagram = ET.SubElement(self.file, 'diagram', id=name, name=name)
        model = ET.SubElement(diagram, 'mxGraphModel', dx=str(width), dy=str(height),
                              grid='1', gridSize='10', guides='1', tooltips='1',
                              connect='1', arrows='1', fold='1', page='1', pageScale='1',
                              pageWidth=str(width), pageHeight=str(height), background='#ffffff')
        self.root = ET.SubElement(model, 'root')
        ET.SubElement(self.root, 'mxCell', id='0')
        ET.SubElement(self.root, 'mxCell', id='1', parent='0')
        self.number = 1

    def node(self, text, x, y, width, height, color=GRAY, style='', font=21):
        self.number += 1
        key = f'n{self.number}'
        base = (f'rounded=1;whiteSpace=wrap;html=0;fillColor={color[0]};'
                f'strokeColor={color[1]};strokeWidth=2;fontColor=#172b4d;'
                f'fontFamily=Arial;fontSize={font};spacing=12;')
        cell = ET.SubElement(self.root, 'mxCell', id=key, value=text,
                             style=base + style, vertex='1', parent='1')
        ET.SubElement(cell, 'mxGeometry', x=str(x), y=str(y), width=str(width),
                      height=str(height), **{'as': 'geometry'})
        return key

    def text(self, text, x, y, width, height, size=24):
        return self.node(text, x, y, width, height,
                         style='fillColor=none;strokeColor=none;rounded=0;fontStyle=1;', font=size)

    def edge(self, source, target, label='', style=''):
        self.number += 1
        cell = ET.SubElement(self.root, 'mxCell', id=f'e{self.number}', value=label,
                             source=source, target=target, edge='1', parent='1',
                             style='edgeStyle=orthogonalEdgeStyle;rounded=0;html=0;'
                                   'endArrow=block;endFill=1;strokeColor=#43546a;strokeWidth=2;'
                                   'fontFamily=Arial;fontSize=17;labelBackgroundColor=#ffffff;' + style)
        ET.SubElement(cell, 'mxGeometry', relative='1', **{'as': 'geometry'})

    def line(self, x1, y1, x2, y2, label='', dashed=False, arrow=True):
        self.number += 1
        cell = ET.SubElement(self.root, 'mxCell', id=f'e{self.number}', value=label,
                             edge='1', parent='1',
                             style=f'endArrow={"block" if arrow else "none"};endFill=1;'
                                   f'dashed={int(dashed)};html=0;strokeColor=#43546a;strokeWidth=2;'
                                   'fontFamily=Arial;fontSize=19;labelBackgroundColor=#ffffff;')
        geo = ET.SubElement(cell, 'mxGeometry', relative='1', **{'as': 'geometry'})
        ET.SubElement(geo, 'mxPoint', x=str(x1), y=str(y1), **{'as': 'sourcePoint'})
        ET.SubElement(geo, 'mxPoint', x=str(x2), y=str(y2), **{'as': 'targetPoint'})

    def save(self, filename):
        OUTPUT.mkdir(parents=True, exist_ok=True)
        ET.indent(self.file)
        ET.ElementTree(self.file).write(OUTPUT / filename, encoding='utf-8', xml_declaration=True)


def workflow():
    d = Drawing('Image transfer workflow', 1300, 1120)
    d.text('GỬ ẢNH QUA TCP — WORKFLOW', 60, 30, 1180, 55, 30)
    d.text('Đọc từ trên xuống  |  1 kết nối / 1 ảnh  |  Tối đa 10 MiB', 60, 90, 1180, 45, 21)
    d.node('BÊN GỬ\nimage_sender', 60, 160, 350, 65, BLUE, font=23)
    d.node('DỮ LIỆU TRÊN TCP', 480, 160, 300, 65, ORANGE, font=22)
    d.node('BÊN NHẬN\nimage_receiver', 850, 160, 350, 65, TEAL, font=23)
    left = ['1. Mở file dạng binary\nKiểm tra size 1 byte..10 MiB',
            '2. Đọc file, tính CRC32\nQuay lại đầu file',
            '3. Connect và gửi header\nMagic + size + CRC32',
            '4. Đọc và gửi từng khối\nBuffer tối đa 4096 byte',
            '5. Half-close chiều gửi\nshutdown(SHUT_WR)',
            '6. Chờ ACK từ receiver\nChiều nhận vẫn mở',
            '7. Kiểm tra status/size/CRC\nIn Image ACK khi đúng']
    right = ['1. Listen rồi accept\nChờ một kết nối',
             '2. Socket nonblocking\nTạo deadline truyền',
             '3. Nhận đủ header 12 byte\nKiểm tra magic và size',
             '4. Nhận và ghi file tạm\nCập nhật CRC32 từng khối',
             '5. Kiểm tra EOF + CRC32\nKhông nhận thiếu/thừa byte',
             '6. Flush, fsync, công bố file\nKhông thay file đã tồn tại',
             '7. Gửi ACK 16 byte\nĐóng socket và kết thúc']
    nodes_l, nodes_r = [], []
    for i, (a, b) in enumerate(zip(left, right)):
        y = 260 + i * 110
        nodes_l.append(d.node(a, 60, y, 350, 80, BLUE))
        nodes_r.append(d.node(b, 850, y, 350, 80, TEAL))
        if i:
            d.edge(nodes_l[i - 1], nodes_l[i])
            d.edge(nodes_r[i - 1], nodes_r[i])
    for i, label in [(2, 'HEADER 12 BYTE\nIMG1 | size | CRC32'),
                     (3, 'IMAGE BYTES\nKhông dùng strlen()'),
                     (4, 'EOF CHIỀU GỬI\nKhông phải đóng cả socket')]:
        middle = d.node(label, 480, 260 + i * 110, 300, 80, ORANGE, font=20)
        d.edge(nodes_l[i], middle, style='exitX=1;exitY=0.5;entryX=0;entryY=0.5;')
        d.edge(middle, nodes_r[i], style='exitX=1;exitY=0.5;entryX=0;entryY=0.5;')
    ack = d.node('ACK1 | status | bytes | CRC\nOK chỉ sau bước lưu file', 480, 920, 300, 80, ORANGE, font=20)
    d.edge(nodes_r[-1], ack, style='exitX=0;exitY=0.5;entryX=1;entryY=0.5;')
    d.edge(ack, nodes_l[-1], style='exitX=0;exitY=0.5;entryX=1;entryY=0.5;')
    d.text('TCP ACK ≠ Image ACK. Lỗi/timeout: không công bố ảnh chưa hoàn tất.', 60, 1030, 1140, 50, 20)
    d.save('image-workflow.drawio')


def sequence():
    d = Drawing('UML sequence - image transfer', 1580, 1220)
    d.text('UML SEQUENCE — MỘT LẦN GỬI ẢNH', 40, 25, 1500, 60, 30)
    d.text('Thời gian đi từ trên xuống. Mỗi mũi tên là lời gọi hoặc message logic.', 40, 90, 1500, 45, 20)
    columns = [(170, 'Sender'), (450, 'File nguồn'), (850, 'Receiver'),
               (1130, 'File tạm'), (1410, 'File kết quả')]
    for x, name in columns:
        d.node(name, x - 110, 165, 220, 65, BLUE if x < 700 else TEAL, font=23)
        d.line(x, 230, x, 1140, dashed=True, arrow=False)
    for y, x1, x2, label, dashed in [
        (280, 170, 450, 'fopen + stat', False),
        (335, 450, 170, 'bytes để tính CRC32', True),
        (410, 170, 850, 'TCP connect / accept', False),
        (480, 170, 850, 'Header IMG1: size + CRC32', False),
        (540, 850, 1130, 'mkstemp + fdopen', False),
        (625, 170, 850, 'Image bytes: các khối ≤ 4096', False),
        (685, 850, 1130, 'fwrite + cập nhật CRC32', False),
        (785, 170, 850, 'shutdown(SHUT_WR) → EOF', False),
        (900, 850, 1130, 'fflush + fsync + fclose', False),
        (965, 850, 1410, 'link(temp, output): công bố, không overwrite', False),
        (1050, 850, 170, 'ACK1: OK + size + CRC32', True),
    ]:
        d.line(x1, y, x2, y, label, dashed)
    d.node('LOOP: dữ liệu ảnh', 70, 575, 1300, 160, GRAY,
           style='fillColor=none;dashed=1;verticalAlign=top;align=left;', font=18)
    d.node('Kiểm tra đủ size, EOF\nvà CRC32 khớp', 745, 815, 290, 60, ORANGE, font=18)
    d.node('Kiểm tra ACK\nĐóng socket / file', 60, 1090, 240, 65, BLUE, font=18)
    d.save('image-sequence.drawio')


def receiver_states():
    d = Drawing('UML states - receiver', 1370, 1170)
    d.text('UML STATE — RECEIVER: THÀNH CÔNG VÀ LỖI', 40, 25, 1290, 60, 28)
    d.text('Luồng chính đi xuống. Mỗi nhánh lỗi kết thúc ngay bên phải, không nối chéo.',
           40, 90, 1290, 50, 20)
    start = d.node('', 295, 160, 26, 26, GRAY,
                   style='shape=ellipse;fillColor=#172b4d;strokeColor=#172b4d;')
    states = [
        ('READ_HEADER\nĐọc đủ 12 byte, validate magic/size', 'BAD_HEADER = 1\nHeader thiếu/sai hoặc size không hợp lệ'),
        ('RECEIVE_BODY\nNhận đủ size và ghi file tạm', 'BAD_STREAM = 4 / FILE_ERROR = 3\nLỗi nhận, tạo hoặc ghi file tạm'),
        ('VERIFY\nPhải có EOF; CRC32 phải khớp', 'BAD_STREAM = 4 / BAD_CRC = 2\nCó byte thừa hoặc CRC32 sai'),
        ('COMMIT\nFlush, fsync, close, link file mới', 'FILE_ERROR = 3\nLỗi tạo/ghi/sync/link; file cũ giữ nguyên'),
    ]
    previous = start
    for i, (normal, error) in enumerate(states):
        y = 240 + i * 170
        good = d.node(normal, 80, y, 460, 95, TEAL, font=22)
        bad = d.node(error + '\nDọn temp; thử gửi NACK; exit 1', 760, y, 470, 110, RED, font=20)
        end = d.node('', 1280, y + 40, 25, 25, RED,
                     style='shape=ellipse;fillColor=#ba4942;')
        d.edge(previous, good, 'OK' if i else '')
        d.edge(good, bad, 'Lỗi', 'exitX=1;exitY=0.5;entryX=0;entryY=0.5;')
        d.edge(bad, end, style='exitX=1;exitY=0.5;entryX=0;entryY=0.5;')
        previous = good
    ack = d.node('SEND_ACK\nFile đã công bố → gửi status OK', 80, 920, 460, 95, BLUE, font=22)
    d.edge(previous, ack, 'OK')
    end = d.node('', 295, 1080, 30, 30, GRAY,
                 style='shape=ellipse;fillColor=#172b4d;')
    d.edge(ack, end, 'ACK gửi thành công → exit 0')
    d.node('Nếu gửi ACK thất bại sau COMMIT:\nreceiver exit 1 nhưng file có thể đã tồn tại.\nKhông tự retry bằng cách ghi đè file đó.',
           760, 920, 470, 135, ORANGE, font=21)
    d.save('image-receiver-states.drawio')


if __name__ == '__main__':
    workflow()
    sequence()
    receiver_states()
    print(f'Wrote 3 editable draw.io files to {OUTPUT}')
