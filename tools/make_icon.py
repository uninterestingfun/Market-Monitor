"""生成应用图标 assets/icon.ico（深色圆角底 + 青绿色走势线）。"""
import os

from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "assets", "icon.ico")
SIZE = 256


def main() -> None:
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # 圆角深色底
    d.rounded_rectangle([4, 4, SIZE - 5, SIZE - 5], radius=58, fill=(10, 16, 28, 255))
    # 内描边
    d.rounded_rectangle([10, 10, SIZE - 11, SIZE - 11], radius=52, outline=(94, 234, 212, 70), width=3)
    # 背景网格
    for x in range(48, SIZE - 30, 44):
        d.line([(x, 40), (x, SIZE - 48)], fill=(255, 255, 255, 10), width=2)
    for y in range(56, SIZE - 40, 44):
        d.line([(40, y), (SIZE - 40, y)], fill=(255, 255, 255, 10), width=2)

    # 走势线
    pts = [(44, 186), (86, 138), (122, 158), (166, 96), (212, 118)]
    d.line(pts, fill=(94, 234, 212, 255), width=15, joint="curve")
    for p in (pts[0], pts[-1]):
        d.ellipse([p[0] - 10, p[1] - 10, p[0] + 10, p[1] + 10], fill=(94, 234, 212, 255))

    img.save(OUT, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("saved", OUT, os.path.getsize(OUT), "bytes")


if __name__ == "__main__":
    main()
