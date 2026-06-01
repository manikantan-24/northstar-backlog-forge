"""Generate a hand-drawn-style whiteboard photo for the vision demo.

Produces samples/whiteboard_sprint_planning.png — a NorthStar Retail sprint
planning sketch (marker boxes, sticky notes, arrows). Feeding it through the
Parser (vision-capable model) should yield topics for POS offline mode, the
loyalty tier view, pharmacy refill unification, and a PCI-blocked offline-card
ask — i.e. it exercises stories + a constraint conflict from an *image*.

Run:  python scripts/make_whiteboard_sample.py
"""
from __future__ import annotations

import math
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "samples" / "whiteboard_sprint_planning.png"

FONT = "/System/Library/Fonts/Supplemental/Bradley Hand Bold.ttf"
W, H = 1500, 1050
random.seed(7)

INK = {
    "black": (38, 38, 44),
    "blue": (31, 78, 140),
    "red": (179, 38, 30),
    "green": (27, 122, 61),
    "violet": (124, 58, 173),
}


def font(sz):
    try:
        return ImageFont.truetype(FONT, sz)
    except Exception:
        return ImageFont.load_default()


def jitter(p, a=2):
    return (p[0] + random.uniform(-a, a), p[1] + random.uniform(-a, a))


def sketch_line(d, p1, p2, color, width=3):
    """A slightly wobbly line so it reads as hand-drawn."""
    steps = max(2, int(math.dist(p1, p2) / 40))
    pts = []
    for i in range(steps + 1):
        t = i / steps
        x = p1[0] + (p2[0] - p1[0]) * t
        y = p1[1] + (p2[1] - p1[1]) * t
        pts.append(jitter((x, y), 1.6))
    d.line(pts, fill=color, width=width, joint="curve")


def sketch_box(d, xy, color, width=3):
    x0, y0, x1, y1 = xy
    sketch_line(d, (x0, y0), (x1, y0), color, width)
    sketch_line(d, (x1, y0), (x1, y1), color, width)
    sketch_line(d, (x1, y1), (x0, y1), color, width)
    sketch_line(d, (x0, y1), (x0, y0), color, width)


def arrow(d, p1, p2, color, width=4):
    sketch_line(d, p1, p2, color, width)
    ang = math.atan2(p2[1] - p1[1], p2[0] - p1[0])
    for da in (math.radians(150), math.radians(-150)):
        hx = p2[0] + 16 * math.cos(ang + da)
        hy = p2[1] + 16 * math.sin(ang + da)
        sketch_line(d, p2, (hx, hy), color, width)


def text(d, pos, s, color, sz, spacing=6):
    d.multiline_text(pos, s, fill=color, font=font(sz), spacing=spacing)


def sticky(base, xy, text_s, fill, sz=30, rot=0):
    """A rotated sticky note pasted onto the board."""
    w, h = 300, 200
    tile = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    td = ImageDraw.Draw(tile)
    td.rectangle((6, 8, w - 6, h - 6), fill=fill, outline=(0, 0, 0, 40))
    td.multiline_text((22, 26), text_s, fill=INK["black"], font=font(sz), spacing=6)
    tile = tile.rotate(rot, expand=True, resample=Image.BICUBIC)
    base.alpha_composite(tile, xy)


def main() -> int:
    img = Image.new("RGBA", (W, H), (247, 246, 242, 255))
    # faint whiteboard sheen + border frame
    d = ImageDraw.Draw(img)
    for i in range(40):
        d.line([(0, i * 28), (W, i * 28)], fill=(242, 241, 236), width=1)
    d.rectangle((14, 14, W - 14, H - 14), outline=(180, 178, 170), width=10)

    # Title
    text(d, (60, 40), "NorthStar Retail  —  Sprint Planning", INK["black"], 58)
    sketch_line(d, (62, 118), (760, 118), INK["blue"], 4)
    text(d, (1080, 52), "Q3  ·  Mobile + POS", INK["violet"], 30)

    # --- Left column: marker boxes ---
    sketch_box(d, (60, 165, 690, 320), INK["blue"], 4)
    text(d, (80, 180), "POS OFFLINE MODE", INK["blue"], 38)
    text(d, (80, 232),
         "keep CASH sales working when\nthe WAN drops (rural lanes!)  ~weekly",
         INK["black"], 30)
    text(d, (600, 175), "P1", INK["red"], 40)

    sketch_box(d, (60, 360, 690, 520), INK["green"], 4)
    text(d, (80, 374), "PHARMACY REFILLS", INK["green"], 38)
    text(d, (80, 426),
         "unify app + IVR  ->  ONE write\nto Rx Hub  (stop double refills)",
         INK["black"], 30)
    text(d, (600, 370), "P1", INK["red"], 40)

    sketch_box(d, (60, 560, 690, 720), INK["violet"], 4)
    text(d, (80, 574), "SEARCH FIX", INK["violet"], 38)
    text(d, (80, 626),
         "hide OUT-OF-STOCK items\nper store  (stop dead ends)",
         INK["black"], 30)
    text(d, (600, 570), "P2", INK["green"], 36)

    # --- Right column: the offline-card ask + the BLOCKED note ---
    sketch_box(d, (820, 165, 1430, 300), INK["black"], 4)
    text(d, (840, 182), "Can we do CARD sales", INK["black"], 34)
    text(d, (840, 226), "offline too??", INK["black"], 34)

    # BLOCKED sticky + arrow
    arrow(d, (1120, 305), (1120, 360), INK["red"], 5)
    sticky(img, (840, 360), "X  BLOCKED — PCI\nno card data stored\nlocally (wiki s.4)",
           (255, 138, 138, 255), sz=30, rot=-4)
    d = ImageDraw.Draw(img)  # refresh draw handle after composite

    # Loyalty sticky
    sticky(img, (1140, 360), "Loyalty:\ntier progress bar\npts -> next tier",
           (255, 226, 120, 255), sz=30, rot=3)
    d = ImageDraw.Draw(img)

    # Parking lot
    sketch_box(d, (820, 620, 1430, 760), INK["black"], 3)
    text(d, (840, 632), "PARKING LOT (later)", INK["black"], 30)
    text(d, (840, 678),
         "- vendor portal redesign\n- self-checkout pilot",
         INK["black"], 27)

    # footer scribble
    text(d, (60, 940), "owners: Anika (PM) · Devon (eng) · Priya (mobile)",
         INK["blue"], 28)
    text(d, (1020, 940), "* draft — confirm w/ arch", INK["red"], 26)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(OUT, "PNG")
    print(f"wrote {OUT}  ({img.size[0]}x{img.size[1]})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
