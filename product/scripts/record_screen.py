#!/usr/bin/env python3
"""Record one screen region as timestamped JPEG frames until DIR/STOP exists.

Frames are named by wall-clock time (DIR/NAME/<epoch ms>.jpg), the same scheme as record_dashboard.mjs,
so make_composite.py can cut any source at exact moments. Unchanged frames are skipped; the cut holds
the previous frame until the next one.
  record_screen.py DIR NAME X Y W H [fps]
"""
import sys
import time
from pathlib import Path
from PIL import ImageChops, ImageGrab

out, name = Path(sys.argv[1]), sys.argv[2]
x, y, w, h = (int(v) for v in sys.argv[3:7])
fps = float(sys.argv[7]) if len(sys.argv) > 7 else 3.0
frames = out / name
frames.mkdir(parents=True, exist_ok=True)
(out / f'{name}_region.txt').write_text(f'{x} {y} {w} {h}\n')
print('RECORDING', int(time.time() * 1000), flush=True)
last, kept, last_kept_at = None, 0, 0.0
while not (out / 'STOP').exists():
    started = time.time()
    try:
        img = ImageGrab.grab(bbox=(x, y, x + w, y + h), xdisplay=':0').convert('RGB')
    except Exception:
        time.sleep(1)
        continue
    # Keep a frame when the region changed, and at least every 5 s so a cut never starts before the first frame.
    if last is None or ImageChops.difference(img, last).getbbox() or started - last_kept_at > 5:
        img.save(frames / f'{int(started * 1000)}.jpg', quality=82)
        last, kept, last_kept_at = img, kept + 1, started
    time.sleep(max(0.0, 1 / fps - (time.time() - started)))
print('stopped', kept, 'frames', flush=True)
