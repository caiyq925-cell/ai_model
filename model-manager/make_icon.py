import struct, zlib, math

W = H = 1024

def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))

c1 = (38, 58, 112)   # 深蓝
c2 = (91, 141, 239)  # 主题蓝
c3 = (18, 20, 26)    # 背景暗

def rounded_rect_dist(x, y, x0, y0, x1, y1, r):
    """<=0 表示在圆角矩形内部"""
    cx = min(max(x, x0 + r), x1 - r)
    cy = min(max(y, y0 + r), y1 - r)
    dx, dy = x - cx, y - cy
    outside = (x < x0 + r or x > x1 - r) and (y < y0 + r or y > y1 - r)
    if outside:
        return math.hypot(dx, dy) - r
    return max(x0 - x, x - x1, y0 - y, y - y1)

rows = []
for y in range(H):
    row = bytearray([0])  # filter: none
    for x in range(W):
        # 背景透明
        d_bg = rounded_rect_dist(x, y, 32, 32, W - 32, H - 32, 200)
        if d_bg > 0:
            row.append(0); row.append(0); row.append(0); row.append(0)
            continue
        # 渐变 tile
        t = (x / W + y / H) / 2
        r, g, b = lerp(c2, c1, t)
        # 白色 "M" 信号柱图形: 三根圆角竖条 + 顶部圆点连线
        alpha = 255
        bars = [(300, 640, 380, 784), (470, 500, 550, 784), (640, 340, 720, 784)]
        in_shape = False
        for bx0, by0, bx1, by1 in bars:
            if rounded_rect_dist(x, y, bx0, by0, bx1, by1, 36) <= 0:
                in_shape = True
        # 顶部圆点(折线节点)
        for cx, cy, cr in [(340, 480, 52), (510, 340, 52), (680, 220, 52)]:
            if math.hypot(x - cx, y - cy) <= cr:
                in_shape = True
        # 连线(粗线段)
        def seg_dist(px, py, ax, ay, bx, by):
            apx, apy = px - ax, py - ay
            abx, aby = bx - ax, by - ay
            t2 = max(0, min(1, (apx * abx + apy * aby) / (abx * abx + aby * aby)))
            return math.hypot(px - (ax + abx * t2), py - (ay + aby * t2))
        for a, bpt in [((340, 480), (510, 340)), ((510, 340), (680, 220))]:
            if seg_dist(x, y, a[0], a[1], bpt[0], bpt[1]) <= 30:
                in_shape = True
        if in_shape:
            r, g, b = 255, 255, 255
        row.extend([r, g, b, alpha])
    rows.append(bytes(row))

raw = b"".join(rows)

def chunk(tag, data):
    c = struct.pack(">I", len(data)) + tag + data
    return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

png = b"\x89PNG\r\n\x1a\n"
png += chunk(b"IHDR", struct.pack(">IIBBBBB", W, H, 8, 6, 0, 0, 0))
png += chunk(b"IDAT", zlib.compress(raw, 9))
png += chunk(b"IEND", b"")

with open(r"D:\Users\ai_model\model-manager\assets\app-icon.png", "wb") as f:
    f.write(png)
print("icon written")
