"""Generate the PWA icons into web-remote/public/icons (run once; outputs are committed).

python scripts/make_icons.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent.parent / "web-remote" / "public" / "icons"
BG = (11, 13, 18)
ACCENT = (14, 165, 233)
LIGHT = (232, 234, 240)


def draw(size: int, *, maskable: bool) -> Image.Image:
    """A sofa-remote glyph: rounded body, a lit screen dot and a row of buttons."""
    img = Image.new("RGB", (size, size), BG)
    d = ImageDraw.Draw(img)
    # maskable icons keep important content inside the central 80% safe zone
    scale = 0.62 if maskable else 0.74
    w, h = size * scale * 0.56, size * scale
    x0, y0 = (size - w) / 2, (size - h) / 2
    d.rounded_rectangle(
        [x0, y0, x0 + w, y0 + h],
        radius=w * 0.28,
        fill=(30, 36, 48),
        outline=ACCENT,
        width=max(2, size // 64),
    )
    cx = size / 2
    d.ellipse([cx - w * 0.2, y0 + h * 0.12, cx + w * 0.2, y0 + h * 0.12 + w * 0.4], fill=ACCENT)
    for row in range(3):
        for col in range(2):
            bx = cx + (col - 0.5) * w * 0.42
            by = y0 + h * (0.5 + row * 0.15)
            r = w * 0.08
            d.ellipse([bx - r, by - r, bx + r, by + r], fill=LIGHT)
    return img


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    draw(192, maskable=False).save(OUT / "icon-192.png")
    draw(512, maskable=False).save(OUT / "icon-512.png")
    draw(512, maskable=True).save(OUT / "maskable-512.png")
    draw(180, maskable=False).save(OUT / "apple-touch-icon.png")
    print(f"wrote icons to {OUT}")


if __name__ == "__main__":
    main()
