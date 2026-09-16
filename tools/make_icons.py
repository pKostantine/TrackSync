"""Generate every icon TrackSync needs from assets/logo.png.

One script for both platforms so the Windows .ico and the macOS .icns can
never drift apart. Pillow writes both formats on any operating system, which
means the build does not depend on Windows' icon tooling or on macOS's
iconutil being present.

    python tools/make_icons.py
"""

import os
import sys

from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(ROOT, "assets")

ICO_SIZES = [256, 128, 64, 48, 32, 24, 16]
#: macOS wants power-of-two sizes up to 1024 for a crisp Dock and Finder icon
ICNS_SIZES = [16, 32, 64, 128, 256, 512, 1024]


def main():
    src = os.path.join(ASSETS, "logo.png")
    if not os.path.exists(src):
        print(f"make_icons: {src} is missing", file=sys.stderr)
        return 1

    im = Image.open(src).convert("RGBA")
    made = []

    ico = os.path.join(ASSETS, "TrackSync.ico")
    im.save(ico, format="ICO", sizes=[(s, s) for s in ICO_SIZES])
    made.append(ico)

    icns = os.path.join(ASSETS, "TrackSync.icns")
    layers = [im.resize((s, s), Image.LANCZOS) for s in ICNS_SIZES]
    layers[-1].save(icns, format="ICNS", append_images=layers[:-1])
    made.append(icns)

    png256 = os.path.join(ASSETS, "logo_256.png")
    im.resize((256, 256), Image.LANCZOS).save(png256)
    made.append(png256)

    # the checkbox tick, drawn rather than shipped as a binary blob
    from PIL import ImageDraw
    size = 64
    tick = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(tick)
    w = int(size * 0.13)
    pts = [(size * 0.20, size * 0.53), (size * 0.42, size * 0.74),
           (size * 0.80, size * 0.28)]
    d.line(pts, fill=(255, 255, 255, 255), width=w, joint="curve")
    for x, y in pts:
        r = w / 2.0
        d.ellipse([x - r, y - r, x + r, y + r], fill=(255, 255, 255, 255))
    check = os.path.join(ASSETS, "check.png")
    tick.save(check)
    made.append(check)

    for path in made:
        print(f"  {os.path.basename(path):<20} {os.path.getsize(path):>9,} bytes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
