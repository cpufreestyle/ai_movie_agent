#!/usr/bin/env python
"""生成写实感起始图，用于 Wan2.2-TI2V 真·图生视频(I2V) 验证。
画"红船+日出湖面"：渐变天空+太阳光晕+云、远山、带倒影与反光的湖面、红船+桅杆+帆。
起始图越写实，I2V 动起来的画面越清晰。
用法: python make_start_image.py [out_path] [W] [H]
"""
import sys
from PIL import Image, ImageDraw


def lerp(a, b, t):
    return int(a + (b - a) * t)


def make(path, W=1280, H=704):
    img = Image.new("RGB", (W, H))
    px = img.load()
    horizon = int(H * 0.60)

    # 天空渐变：顶深蓝 -> 中段浅蓝 -> 地平线暖橙
    top = (28, 52, 110)
    mid = (120, 150, 200)
    hz = (255, 180, 110)
    for y in range(horizon):
        if y < horizon * 0.55:
            t = y / (horizon * 0.55)
            r, g, b = lerp(top[0], mid[0], t), lerp(top[1], mid[1], t), lerp(top[2], mid[2], t)
        else:
            t = (y - horizon * 0.55) / (horizon * 0.45)
            r, g, b = lerp(mid[0], hz[0], t), lerp(mid[1], hz[1], t), lerp(mid[2], hz[2], t)
        for x in range(W):
            px[x, y] = (r, g, b)

    # 湖面渐变：地平线亮 -> 底部深蓝绿；加太阳倒影光柱 + 横向波纹
    wtop = (150, 170, 190)
    wbot = (20, 60, 90)
    sunx = int(W * 0.68)
    for y in range(horizon, H):
        t = (y - horizon) / (H - horizon)
        r0 = lerp(wtop[0], wbot[0], t)
        g0 = lerp(wtop[1], wbot[1], t)
        b0 = lerp(wtop[2], wbot[2], t)
        for x in range(W):
            refl = max(0, 1 - abs(x - sunx) / (W * 0.06)) * (1 - t) * 120  # 倒影光柱
            ripple = 10 if (y // 5) % 2 == 0 else -6                          # 波纹
            rr = min(255, max(0, int(r0 + refl + ripple)))
            gg = min(255, max(0, int(g0 + refl + ripple)))
            bb = min(255, max(0, int(b0 + refl * 0.8 + ripple)))
            px[x, y] = (rr, gg, bb)

    d = ImageDraw.Draw(img)
    # 太阳：光晕(3 层) + 圆盘
    sx, sy = sunx, int(horizon * 0.5)
    for rad, col in [(120, (255, 210, 150)), (70, (255, 230, 175)), (40, (255, 245, 205))]:
        d.ellipse([sx - rad, sy - rad, sx + rad, sy + rad], fill=col)
    d.ellipse([sx - 34, sy - 34, sx + 34, sy + 34], fill=(255, 240, 180))
    # 云（浅色椭圆）
    for cx, cy, cw, ch in [(W * 0.2, horizon * 0.3, 160, 26),
                           (W * 0.45, horizon * 0.18, 220, 30),
                           (W * 0.82, horizon * 0.34, 140, 22)]:
        d.ellipse([cx - cw / 2, cy - ch / 2, cx + cw / 2, cy + ch / 2], fill=(235, 238, 245))
    # 远山
    d.polygon([(0, horizon), (W * 0.22, horizon - 70), (W * 0.4, horizon - 20),
               (W * 0.6, horizon - 85), (W * 0.82, horizon - 30), (W, horizon)],
              fill=(60, 72, 105))
    # 红船 + 桅杆 + 帆
    bx, by = int(W * 0.30), int(horizon + (H - horizon) * 0.42)
    d.polygon([(bx - 78, by), (bx + 78, by), (bx + 52, by + 38), (bx - 52, by + 38)],
              fill=(196, 32, 32))
    d.rectangle([bx - 3, by - 82, bx + 3, by], fill=(120, 70, 30))
    d.polygon([(bx + 3, by - 78), (bx + 3, by - 16), (bx + 52, by - 34)], fill=(245, 245, 245))
    # 倒影（暗化、下移）
    d.polygon([(bx - 78, by + 38), (bx + 78, by + 38), (bx + 52, by + 70), (bx - 52, by + 70)],
              fill=(110, 22, 22))
    d.rectangle([bx - 3, by + 38, bx + 3, by + 70], fill=(70, 42, 18))

    img.save(path)
    print(f"[ok] 起始图 -> {path}  ({W}x{H})")


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "outputs/start_boat2.png"
    W = int(sys.argv[2]) if len(sys.argv) > 2 else 1280
    H = int(sys.argv[3]) if len(sys.argv) > 3 else 704
    make(out, W, H)
