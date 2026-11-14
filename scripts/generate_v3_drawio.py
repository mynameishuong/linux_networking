"""Generate editable V3 diagrams; export the .drawio files with draw.io Desktop."""
from generate_image_drawio import Drawing, BLUE, TEAL, ORANGE, RED, GRAY


def workflow():
    d = Drawing('V3 implementation workflow', 1500, 1320)
    d.text('V3 — TỪ MESSAGE ĐẾN QUẠT GIẢ LẬP', 40, 25, 1420, 65, 30)
    d.text('Ba đường vào thật qua socket. Một C control tick sở hữu đầu ra actuator.',
           40, 100, 1420, 45, 21)
    lanes = [
        (60, TEAL, ['SIGNAL PRODUCER', 'UDP CAN1 / Linux SocketCAN\nCAN ID 0x321, payload 8 byte',
                    'Unpack bit + decode signal\n70°C, speed=0, valid/fresh',
                    'Vehicle model\nThermal target + hysteresis']),
        (550, BLUE, ['SOME/IP CLIENT', 'TCP method / UDP SD-event\nFanControl + VehicleTelemetry',
                    'Validate header + decode method\nSetNormalDuty / GetSnapshot',
                    'Typed normal command\nDuty + lease + duplicate cache']),
        (1040, ORANGE, ['DOIP / UDS TESTER', 'TCP 13400 + UDP discovery\nRouting activation + SA / TA',
                       'UDS dispatcher bằng C\nSession / DID / RID / conditions',
                       'Diagnostic routine manager\nStart / stop / result / deadline']),
    ]
    bottoms = []
    for x, color, text in lanes:
        previous = None
        for i, label in enumerate(text):
            box = d.node(label, x, 190 + i * 125, 390, 90, color, font=21)
            if previous:
                d.edge(previous, box)
            previous = box
        bottoms.append(previous)
    arbiter = d.node('C CORE: v3_tick() — chu kỳ mục tiêu 10 ms\nDRIVER FAULT → FALLBACK; sau đó DIAGNOSTIC → NORMAL_SERVICE → NORMAL_THERMAL',
                     90, 730, 1320, 105, GRAY, font=23)
    for i, box in enumerate(bottoms):
        d.edge(box, arbiter, style=f'exitX=0.5;exitY=1;entryX={(165 + i * 490) / 1320};entryY=0;')
    hal = d.node('ACTUATOR HAL — fan_hal.c\nApply target duty → applied duty → simulated actual RPM',
                 390, 900, 720, 95, TEAL, font=23)
    feedback = d.node('FEEDBACK VÀ KẾT QUẢ\nFanStatus event / UDS DID / RoutineControl result / JSONL trace',
                      330, 1070, 840, 105, BLUE, font=23)
    d.edge(arbiter, hal)
    d.edge(hal, feedback)
    d.text('ACCEPTED ≠ APPLIED ≠ actual RPM đạt yêu cầu. Không có handler mạng nào ghi PWM trực tiếp.',
           40, 1220, 1420, 55, 21)
    d.save('v3-workflow.drawio')


def sequence():
    d = Drawing('V3 UML diagnostic sequence', 1570, 1510)
    d.text('UML SEQUENCE — DOIP SELF-TEST VÀ SOME/IP ARBITRATION', 30, 25, 1510, 65, 27)
    d.text('Signal producer vẫn refresh dữ liệu 100 ms. Mỗi dòng là một bước logic theo thời gian.',
           40, 100, 1490, 50, 20)
    columns = [(150, 'UDS tester'), (440, 'DoIP adapter'), (760, 'C core'),
               (1080, 'Fan HAL'), (1400, 'SOME/IP client')]
    for x, label in columns:
        d.node(label, x - 115, 185, 230, 65, BLUE if x in (150, 1400) else TEAL, font=22)
        d.line(x, 250, x, 1430, dashed=True, arrow=False)
    messages = [
        (300, 150, 440, 'TCP + routing activation', False),
        (365, 440, 150, 'Routing active: 0x10', True),
        (430, 150, 440, 'UDS 10 03: extended session', False),
        (490, 440, 760, 'v3_uds(): session=EXTENDED', False),
        (550, 150, 440, '31 01 F001: 40%, 1000 ms', False),
        (610, 440, 150, 'DoIP ACK (chỉ tầng DoIP)', True),
        (670, 440, 760, 'Decode + admission → RUNNING', False),
        (730, 440, 150, 'UDS 71 01 F001 01: RUNNING', True),
        (805, 760, 1080, 'Control tick: apply duty 40%', False),
        (870, 1080, 760, 'Feedback: actual RPM = 2000', True),
        (945, 1400, 760, 'SetNormalDuty: 60%', False),
        (1005, 760, 1400, 'BUSY_DIAGNOSTIC (payload result=1)', True),
        (1140, 760, 1080, 'Release → normal thermal', False),
        (1220, 150, 440, '31 03 F001: query result', False),
        (1280, 440, 760, 'Read terminal result snapshot', False),
        (1350, 440, 150, 'UDS: COMPLETED, 2000 RPM', True),
    ]
    for y, x1, x2, label, dashed in messages:
        d.line(x1, y, x2, y, label, dashed)
    d.node('Sau duration: kiểm tra feedback\nĐủ tiêu chí mới COMPLETED', 605, 1050, 310, 70, ORANGE, font=19)
    d.save('v3-diagnostic-sequence.drawio')


def states():
    d = Drawing('V3 UML routine states', 1480, 1170)
    d.text('UML STATE — FAN SELF-TEST', 40, 25, 1400, 60, 30)
    d.text('Routine state và actuator owner là hai domain riêng. Apply/release diễn ra trong control tick.',
           40, 100, 1400, 50, 20)
    idle = d.node('IDLE\nChưa có routine', 535, 205, 400, 90, GRAY, font=24)
    running = d.node('RUNNING\nStart hợp lệ; control tick cấp DIAG owner\nDuration bắt đầu ở tick apply đầu tiên',
                     465, 390, 540, 115, ORANGE, font=22)
    d.edge(idle, running, 'Extended session + fresh speed=0 + test enable')
    terminal = [
        (45, TEAL, 'COMPLETED\nHết duration, feedback đạt\nliên tục ít nhất 100 ms', 'Đạt tiêu chí'),
        (545, RED, 'FAILED\nDriver fault hoặc feedback\nkhông đạt khi hết duration', 'Lỗi / không đạt'),
        (1045, ORANGE, 'ABORTED\nStop / speed / stale / session\nhoặc tester disconnect', 'Mất điều kiện'),
    ]
    for i, (x, color, label, trigger) in enumerate(terminal):
        box = d.node(label, x, 650, 390, 125, color, font=22)
        d.edge(running, box, trigger,
               f'exitX={0.12 + i * 0.38};exitY=1;entryX=0.5;entryY=0;')
        d.node('Lưu result RPM + reason\nTrả owner về normal/fallback', x, 860, 390, 100, GRAY, font=21)
        d.line(x + 195, 775, x + 195, 860)
    d.text('Query result không start lại routine. Start mới hợp lệ có thể chuyển từ terminal state về RUNNING.',
           40, 1020, 1400, 70, 21)
    d.save('v3-routine-states.drawio')


if __name__ == '__main__':
    workflow()
    sequence()
    states()
    print('Wrote 3 V3 draw.io diagrams.')
