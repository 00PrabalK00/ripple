#!/usr/bin/env python3
"""Cut the site video from segments of real recorded runs, each introduced by a title card.

Every segment is unedited footage of a live run (sped up). A caption bar shows each real
timeline event from that run at the moment it happened, so a viewer can follow what Ripple did.
Takes recorded as timestamped dashboard frames (frames/<epoch ms>.jpg) are cut by exact wall time;
older takes recorded as a Playwright video are cut by an offset from the recording start.
  make_composite.py OUT_DIR
"""
import datetime as dt
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
import imageio_ffmpeg
from PIL import Image, ImageDraw, ImageFont

FF = imageio_ffmpeg.get_ffmpeg_exe()
REC = Path(__file__).resolve().parents[2] / 'recordings'
FONT = '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'
FONT_REGULAR = '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'

# key -> (take dir, recording start as epoch ms for video takes (None for frame takes), usable Gazebo capture)
TAKES = {'B': ('take-20260912-184334', 1789253031022, True), 'C': ('take-20260913-015826', None, True)}
# (title, subtitle, (take key, UTC start 'HH:MM:SS', UTC end, speed) or None)
SCENES = [
    ('Ripple', 'An always-on site engineer for robots. One continuous live run: SMR300 in Gazebo, Nav2, GLM 5.3 as the control plane.', None),
    ('1 · Tell Ripple where the robot may operate', 'A keepout drawn on the map is verified in the mask and costmap. Nav2 plans around it and Ripple verifies the arrival.',
     ('C', '05:59:08', '06:00:09', 3.0)),
    ('2 · Words become site state, then something unexpected', 'The walkway is reopened from words. A pallet blocks the Home dock: the trip home fails, Ripple investigates on its own, then asks the engineer on Ambiguous.',
     ('C', '06:00:08', '06:01:50', 4.0)),
    ("3 · The reply becomes robot action", 'No one answered on Ambiguous in this run, so the reply was typed on the dashboard. Ripple locates the dock from words, verifies a keepout and reroutes; the arrival is verified.',
     ('C', '06:06:47', '06:08:02', 3.0, False)),  # the Gazebo window capture went grey late in this take
    ('4 · The record', "Ripple files the incident report to the team's Ambiguous workspace.",
     ('C', '06:08:05', '06:08:35', 3.0, False)),
    ('5 · An earlier live run: a real reply on Ambiguous', 'The engineer answers on Ambiguous. Ripple reads the scans, teleops 0.32 m clear, probes and retries the route home.',
     ('B', '22:47:40', '22:49:05', 4.0)),
]
CARD_S = 2.5
CAPTION_X, CAPTION_W, CAPTION_H = 560, 1340, 150


def card(title, subtitle, path):
    img = Image.new('RGB', (1920, 1080), 'black')
    d = ImageDraw.Draw(img)
    big, small = ImageFont.truetype(FONT, 64), ImageFont.truetype(FONT_REGULAR, 34)
    d.text((140, 430), title, font=big, fill='white')
    y = 540
    for line in wrap(d, subtitle, small, 1600):
        d.text((140, y), line, font=small, fill=(200, 200, 200))
        y += 48
    img.save(path)


def wrap(draw, text, font, width):
    lines, line = [], ''
    for word in text.split():
        trial = (line + ' ' + word).strip()
        if draw.textlength(trial, font=font) > width and line:
            lines.append(line)
            line = word
        else:
            line = trial
    return lines + ([line] if line else [])


def caption(text, path):
    img = Image.new('RGBA', (CAPTION_W, CAPTION_H), (0, 0, 0, 225))
    d = ImageDraw.Draw(img)
    font = ImageFont.truetype(FONT_REGULAR, 28)
    lines = wrap(d, text, font, CAPTION_W - 48)
    if len(lines) > 4:
        lines = lines[:3] + [lines[3][:-1] + '…']
    y = (CAPTION_H - 36 * len(lines)) // 2
    for line in lines:
        d.text((24, y), line, font=font, fill='white')
        y += 36
    img.save(path)


def epoch(iso):
    return dt.datetime.fromisoformat(iso).timestamp()


def window(folder, t0, t1):
    """Scene bounds as epoch seconds, from UTC times of day on the take's date (taken from its timeline)."""
    day = json.loads((folder / 'timeline.json').read_text())[-1]['t'][:10]
    return epoch(f'{day}T{t0}+00:00'), epoch(f'{day}T{t1}+00:00')


def events(folder, a, b):
    """Real timeline entries from the run within [a, b] epoch seconds, as (seconds after a, caption text)."""
    out = []
    for e in json.loads((folder / 'timeline.json').read_text()):
        at, kind, text = epoch(e['t']), e['kind'], ' '.join(e['text'].split())
        if not a <= at <= b or kind in ('event', 'system'):
            continue
        if kind == 'agent' and not text.startswith('Hypothesis'):
            continue
        where = 'Ambiguous' if e.get('channel') == 'ambiguous' else 'dashboard'
        if kind == 'operator':
            text = f"{e.get('operator') or 'Operator'} ({where}): {text}"
        elif kind == 'reply':
            text = f"Ripple → {e.get('to') or 'engineer'} ({where}): {text}"
        elif kind == 'incident':
            text = re.sub(r'^Incident (?!opened|closed)', 'Incident — ', text)
        out.append((at - a, text))
    return out


def encode(args, out, extra=()):
    # Web defaults keep the site video small; RIPPLE_CRF=18 RIPPLE_PRESET=slow makes an upload-quality master.
    subprocess.run([FF, '-y', '-loglevel', 'error', *args, '-an', '-r', '30', '-c:v', 'libx264',
                    '-preset', os.environ.get('RIPPLE_PRESET', 'veryfast'), '-crf', os.environ.get('RIPPLE_CRF', '26'),
                    '-pix_fmt', 'yuv420p', *extra, str(out)], check=True)


def dashboard_input(folder, epoch_ms, a, b, speed, work, i):
    """ffmpeg input args for the dashboard between epochs a and b, played `speed` times faster."""
    frames = sorted((int(p.stem), p) for p in (folder / 'frames').glob('*.jpg')) if (folder / 'frames').is_dir() else []
    if frames:
        chosen = [(t / 1000, p) for t, p in frames if a * 1000 <= t <= b * 1000]
        listing = work / f'frames{i}.txt'
        lines = []
        for (t, p), (nxt, _) in zip(chosen, chosen[1:] + [(b, None)]):
            lines += [f"file '{p}'", f'duration {max(nxt - t, .01) / speed:.4f}']
        listing.write_text('\n'.join(lines + [f"file '{chosen[-1][1]}'"]) + '\n')
        return ['-f', 'concat', '-safe', '0', '-i', str(listing)], 'scale=1920:1080'
    video = folder / 'dashboard.webm'
    if not video.exists():
        video = max(folder.glob('page@*.webm'), key=lambda p: p.stat().st_size)
    start = max(0.0, a - epoch_ms / 1000 + 1.0)  # page video starts about a second before the RECORDING mark
    return ['-ss', f'{start:.2f}', '-t', f'{b - a:.2f}', '-i', str(video)], f'setpts=PTS/{speed},scale=1920:1080'


def scene_clip(i, seg, work):
    key, t0, t1, speed, *inset = seg  # optional fifth field turns the Gazebo inset off for this scene
    take, epoch_ms, use_gazebo = TAKES[key]
    use_gazebo = use_gazebo and (inset[0] if inset else True)
    folder = REC / take
    a, b = window(folder, t0, t1)
    out_len = (b - a) / speed
    inputs, first = dashboard_input(folder, epoch_ms, a, b, speed, work, i)
    graph = [f'[0:v]{first}[v0]']
    last, n = 'v0', 1
    # Events closer together than a reader can follow share one caption; captions never overlap.
    groups = []
    for rel, text in events(folder, a, b):
        at = rel / speed
        if groups and at - groups[-1][0] < 1.5 and len(groups[-1][1]) < 2:
            groups[-1][1].append(text)
        else:
            groups.append((at, [text]))
    caps, prev = [], -9.0
    for at, texts in groups:
        prev = max(at, prev + 1.5)
        caps.append((prev, texts[0] if len(texts) == 1 else '  ·  '.join(t if len(t) < 150 else t[:147] + '…' for t in texts)))
    for k, (at, text) in enumerate(caps):
        png = work / f'cap{i}_{k}.png'
        caption(text, png)
        until = (caps[k + 1][0] - 0.05) if k + 1 < len(caps) else out_len
        inputs += ['-loop', '1', '-t', f'{out_len:.2f}', '-i', str(png)]
        graph.append(f"[{last}][{n}:v]overlay={CAPTION_X}:H-{CAPTION_H + 22}:enable='between(t,{at:.2f},{until:.2f})'[c{k}]")
        last, n = f'c{k}', n + 1
    gazebo = folder / 'gazebo.webm'
    if use_gazebo and gazebo.exists():
        stamp = folder / 'gazebo_start.txt'
        gz_start = float(stamp.read_text()) if stamp.exists() else epoch_ms / 1000 - 1.5
        inputs += ['-ss', f'{max(0.0, a - gz_start):.2f}', '-t', f'{b - a:.2f}', '-i', str(gazebo)]
        graph.append(f'[{n}:v]setpts=PTS/{speed},scale=520:-2[g];[{last}][g]overlay=24:H-h-24:eof_action=pass[out]')
        last = 'out'
    clip = work / f'scene{i}.mp4'
    encode([*inputs, '-filter_complex', ';'.join(graph), '-map', f'[{last}]'], clip, ('-t', f'{out_len:.2f}'))
    print(f'scene {i}: {take} {t0}-{t1} ({b - a:.0f}s) x{speed}, {len(caps)} captions', flush=True)
    return clip


def main():
    out_dir = Path(sys.argv[1])
    out_dir.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix='ripple-cut-'))
    parts = []
    for i, (title, subtitle, seg) in enumerate(SCENES):
        png, clip = work / f'card{i}.png', work / f'card{i}.mp4'
        card(title, subtitle, png)
        encode(['-loop', '1', '-t', str(CARD_S), '-i', str(png), '-vf', 'scale=1920:1080'], clip)
        parts.append(clip)
        if seg:
            parts.append(scene_clip(i, seg, work))
    listing = work / 'parts.txt'
    listing.write_text(''.join(f"file '{p}'\n" for p in parts))
    subprocess.run([FF, '-y', '-loglevel', 'error', '-f', 'concat', '-safe', '0', '-i', str(listing), '-c', 'copy',
                    '-movflags', '+faststart', str(out_dir / 'demo.mp4')], check=True)
    subprocess.run([FF, '-y', '-loglevel', 'error', '-ss', '14', '-i', str(out_dir / 'demo.mp4'), '-frames:v', '1',
                    '-update', '1', str(out_dir / 'poster.jpg')], check=True)
    log = subprocess.run([FF, '-i', str(out_dir / 'demo.mp4')], capture_output=True, text=True).stderr
    print('done', out_dir / 'demo.mp4', re.search(r'Duration: ([\d:.]+)', log).group(1),
          (out_dir / 'demo.mp4').stat().st_size // 1024, 'KB')


if __name__ == '__main__':
    main()
