"""Generate Android launcher icons from static/img/logo.png.

The source logo is wider than tall and sits on a black background. We
center-crop to a square, then resize to each density and write the PNG into
mobile/android/app/src/main/res/mipmap-<density>/ic_launcher.png.

Densities (Android baseline 48dp):
  mdpi    48px
  hdpi    72px
  xhdpi   96px
  xxhdpi  144px
  xxxhdpi 192px
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "static" / "img" / "logo.png"
RES = ROOT / "mobile" / "android" / "app" / "src" / "main" / "res"

DENSITIES = {
    "mipmap-mdpi": 48,
    "mipmap-hdpi": 72,
    "mipmap-xhdpi": 96,
    "mipmap-xxhdpi": 144,
    "mipmap-xxxhdpi": 192,
}


def center_square(img: Image.Image) -> Image.Image:
    w, h = img.size
    side = min(w, h)
    left = (w - side) // 2
    top = (h - side) // 2
    return img.crop((left, top, left + side, top + side))


def main() -> None:
    if not SRC.exists():
        raise SystemExit(f"source not found: {SRC}")
    src = Image.open(SRC).convert("RGBA")
    square = center_square(src)
    for density, px in DENSITIES.items():
        out_dir = RES / density
        out_dir.mkdir(parents=True, exist_ok=True)
        resized = square.resize((px, px), Image.LANCZOS)
        out_path = out_dir / "ic_launcher.png"
        resized.save(out_path, format="PNG", optimize=True)
        print(f"wrote {out_path.relative_to(ROOT)}  ({px}x{px})")


if __name__ == "__main__":
    main()
