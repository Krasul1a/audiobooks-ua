"""Малює іконку програми: розгорнута книга з вишиванкою та звуковими хвилями.
Запуск: python make_icon.py  -> assets/icon.png, assets/icon.ico"""

import os

from PIL import Image, ImageDraw, ImageFilter

S = 1024                                   # малюємо великим, потім зменшуємо
BLUE_TOP, BLUE_BOT = (0, 98, 200), (0, 52, 128)
YELLOW = (255, 213, 0)
PAPER, PAPER_SHADE = (255, 250, 238), (236, 226, 204)
RED, BLACK = (200, 30, 40), (35, 30, 30)

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "assets")


def gradient(size, top, bot):
    g = Image.new("RGB", (1, size))
    for y in range(size):
        t = y / (size - 1)
        g.putpixel((0, y), tuple(int(a + (b - a) * t) for a, b in zip(top, bot)))
    return g.resize((size, size))


# Ромбовий «вишитий» мотив 7x7 (1 - червоний, 2 - чорний)
MOTIF = [
    "...1...",
    "..121..",
    ".12.21.",
    "12.2.21",
    ".12.21.",
    "..121..",
    "...1...",
]


def stitch(d, x, y, c, cell):
    """Один хрестик вишивки."""
    p = cell * 0.18
    w = max(2, int(cell * 0.28))
    d.line([(x + p, y + p), (x + cell - p, y + cell - p)], fill=c, width=w)
    d.line([(x + cell - p, y + p), (x + p, y + cell - p)], fill=c, width=w)


def embroidery_band(d, x0, y0, width, cell):
    """Горизонтальна смуга з ромбами-вишиванкою."""
    n = int(width // (cell * 8))
    total = n * cell * 8 - cell
    x = x0 + (width - total) / 2
    for k in range(n):
        for r, row in enumerate(MOTIF):
            for c, ch in enumerate(row):
                if ch != ".":
                    stitch(d, x + c * cell, y0 + r * cell, RED if ch == "1" else BLACK, cell)
        x += cell * 8


def main():
    os.makedirs(OUT, exist_ok=True)
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))

    # фон - заокруглений квадрат з синім градієнтом
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, S - 1, S - 1], radius=220, fill=255)
    img.paste(gradient(S, BLUE_TOP, BLUE_BOT), (0, 0), mask)
    d = ImageDraw.Draw(img)

    # звукові хвилі над книгою (жовті дуги)
    cx, cy = S // 2, 470
    for i, r in enumerate((150, 230, 310)):
        d.arc([cx - r, cy - r, cx + r, cy + r], start=215, end=325, fill=YELLOW,
              width=46 - i * 6)

    # тінь книги
    shadow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    sd.polygon([(150, 560), (512, 620), (874, 560), (874, 860), (512, 920), (150, 860)],
               fill=(0, 0, 30, 120))
    img.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(24)), (0, 18))
    d = ImageDraw.Draw(img)

    # обкладинка (жовта смужка під сторінками)
    d.polygon([(130, 545), (512, 600), (894, 545), (894, 870), (512, 925), (130, 870)],
              fill=YELLOW)
    # сторінки
    left = [(160, 520), (500, 575), (500, 885), (160, 835)]
    right = [(524, 575), (864, 520), (864, 835), (524, 885)]
    d.polygon(left, fill=PAPER)
    d.polygon(right, fill=PAPER)
    d.polygon([(470, 570), (500, 575), (500, 885), (470, 880)], fill=PAPER_SHADE)
    d.polygon([(524, 575), (554, 570), (554, 880), (524, 885)], fill=PAPER_SHADE)
    d.line([(512, 578), (512, 890)], fill=(200, 188, 160), width=6)

    # вишиванка на сторінках: малюємо на окремому шарі й нахиляємо під сторінку
    for side in ("l", "r"):
        layer = Image.new("RGBA", (340, 120), (0, 0, 0, 0))
        embroidery_band(ImageDraw.Draw(layer), 0, 8, 340, 13)
        w, h = layer.size
        if side == "l":
            # зсув вниз праворуч (сторінка нахилена до корінця)
            coeffs = (1, 0, 0, -55 / 340, 1, 0)
            layer = layer.transform((w, h + 60), Image.AFFINE, coeffs, Image.BICUBIC)
            img.alpha_composite(layer, (160, 560))
        else:
            coeffs = (1, 0, 0, 55 / 340, 1, -55)
            layer = layer.transform((w, h + 60), Image.AFFINE, coeffs, Image.BICUBIC)
            img.alpha_composite(layer, (524, 560))

    # рядки тексту
    d = ImageDraw.Draw(img)
    for k in range(4):
        y = 720 + k * 34
        d.line([(200, y), (460, y + 42)],
               fill=(190, 180, 160), width=12)
        d.line([(564, y + 42), (824, y)], fill=(190, 180, 160), width=12)

    img.save(os.path.join(OUT, "icon.png"))
    img.resize((256, 256), Image.LANCZOS).save(
        os.path.join(OUT, "icon.ico"),
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("assets/icon.png, assets/icon.ico")


if __name__ == "__main__":
    main()
