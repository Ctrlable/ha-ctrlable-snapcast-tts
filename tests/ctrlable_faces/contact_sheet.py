"""Lay the frames render_test wrote into one sheet per screen shape.
    python3 tests/ctrlable_faces/contact_sheet.py OUTDIR"""
import sys
from pathlib import Path
from PIL import Image, ImageDraw

d = Path(sys.argv[1])
steps = ["starting", "idle", "awake", "listening", "thinking", "replying", "muted", "error", "nowifi", "noserver"]
for shape, (w, h) in (("round", (360, 360)), ("rect", (320, 240))):
    sc = 0.5
    cw, ch = int(w * sc), int(h * sc)
    sheet = Image.new("RGB", (cw * 10 + 11 * 6, (ch + 18) * 3 + 6), (20, 26, 30))
    dr = ImageDraw.Draw(sheet)
    for r, style in enumerate(("orb", "halo", "dot")):
        for c, st in enumerate(steps):
            im = Image.open(d / f"{style}_{st}_{shape}.ppm").resize((cw, ch), Image.LANCZOS)
            x, y = 6 + c * (cw + 6), 6 + r * (ch + 18)
            sheet.paste(im, (x, y + 14))
            dr.text((x, y), f"{style} {st}", fill=(150, 170, 175))
    sheet.save(d / f"sheet_{shape}.png")
    print(d / f"sheet_{shape}.png")
