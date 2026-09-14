#!/usr/bin/env python3
"""Cut the long demo film from recorded takes: one section per voiceover clip.

A cut file (JSON) lists the sections in order. Each section has a layout, the take windows it shows
(wall-clock UTC times of day), and a target length. When a voiceover clip for the section exists, its
length sets the section's length, and the footage is sped up evenly to fill it. Every frame is real
footage from a take; captions are that run's own timeline entries, shown when they happened.

Layouts:
  card      a title card (optionally over a darkened dashboard frame)
  terminal  a recorded terminal (record_screen.py frames), fitted to the frame
  dash      the dashboard, a Gazebo inset and a caption bar
  dash_amb  the dashboard, the Ambiguous conversation beside it, Gazebo and a caption panel below
Outputs in OUT_DIR: film.mp4 (voiced when every section has a clip in --voice, else silent),
clips/<id>.mp4 (silent, for the website), poster.jpg, and film.json (the timings used).
  make_film.py CUT.json OUT_DIR [--voice DIR]
"""
import argparse
import json
import re
import subprocess
import tempfile
from pathlib import Path
from PIL import Image, ImageDraw, ImageEnhance, ImageFont
import make_composite as mc

REC = mc.REC
W, H = 1920, 1080
BG = (11, 16, 21)
LEAD, TAIL = 0.3, 0.7  # silence before and after each voiceover clip
FONT, REGULAR = mc.FONT, mc.FONT_REGULAR


def run(args):
    subprocess.run([mc.FF, '-y', '-loglevel', 'error', *args], check=True)


def media_seconds(path):
    log = subprocess.run([mc.FF, '-i', str(path)], capture_output=True, text=True).stderr
    h, m, s = re.search(r'Duration: (\d+):(\d+):([\d.]+)', log).groups()
    return int(h) * 3600 + int(m) * 60 + float(s)


def text_png(path, size, header, text, font_size=30, max_lines=7, fill=(12, 19, 25, 235)):
    img = Image.new('RGBA', size, fill)
    d = ImageDraw.Draw(img)
    y = 18
    if header:
        d.text((24, y), header.upper(), font=ImageFont.truetype(FONT, 20), fill=(133, 221, 195))
        y += 38
    font = ImageFont.truetype(REGULAR, font_size)
    lines = mc.wrap(d, text, font, size[0] - 48)
    if len(lines) > max_lines:
        lines = lines[:max_lines - 1] + [lines[max_lines - 1][:-1] + '…']
    for line in lines:
        d.text((24, y), line, font=font, fill='white')
        y += int(font_size * 1.3)
    img.save(path)


def card_png(path, title, subtitle, background=None):
    img = Image.new('RGB', (W, H), BG)
    if background:
        bg = Image.open(background).convert('RGB').resize((W, H))
        img = ImageEnhance.Brightness(bg).enhance(0.25)
    d = ImageDraw.Draw(img)
    d.text((140, 400), title, font=ImageFont.truetype(FONT, 72), fill='white')
    y = 520
    for line in mc.wrap(d, subtitle or '', ImageFont.truetype(REGULAR, 36), 1640):
        d.text((140, y), line, font=ImageFont.truetype(REGULAR, 36), fill=(205, 214, 220))
        y += 52
    img.save(path)


def frames_in(folder, a, b):
    frames = sorted((int(p.stem), p) for p in folder.glob('*.jpg'))
    inside = [(t / 1000, p) for t, p in frames if a * 1000 <= t <= b * 1000]
    before = [(t / 1000, p) for t, p in frames if t < a * 1000]
    # A region recorder keeps only changed frames: start from the last frame before the window.
    return ([(a, before[-1][1])] if before else []) + inside


def frames_input(folder, a, b, speed, listing):
    chosen = frames_in(folder, a, b)
    if not chosen:
        raise SystemExit(f'no frames in {folder} between {a} and {b}')
    lines = []
    for (t, p), (nxt, _) in zip(chosen, chosen[1:] + [(b, None)]):
        lines += [f"file '{p}'", f'duration {max(nxt - t, .01) / speed:.4f}']
    listing.write_text('\n'.join(lines + [f"file '{chosen[-1][1]}'"]) + '\n')
    return ['-f', 'concat', '-safe', '0', '-i', str(listing)]


def events(folder, a, b, include=('Hypothesis', 'Site memory updated')):
    out = []
    for e in json.loads((folder / 'timeline.json').read_text()):
        at, kind, text = mc.epoch(e['t']), e['kind'], ' '.join(e['text'].split())
        if not a <= at <= b or kind in ('event', 'system'):
            continue
        if kind == 'agent' and not text.startswith(include):
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


def caption_times(evts, speed, length, gap=2.0):
    """Captions at the moments they happened, at least `gap` s apart on screen, never overlapping."""
    caps, prev = [], -9.0
    for rel, text in evts:
        at = rel / speed
        if at >= length - 0.5:
            break
        if caps and at - caps[-1][0] < gap and len(caps[-1][1]) < 2:
            caps[-1][1].append(text)
            continue
        prev = max(at, prev + gap)
        caps.append((prev, [text]))
    return [(at, texts[0] if len(texts) == 1 else '  ·  '.join(t if len(t) < 160 else t[:157] + '…' for t in texts))
            for at, texts in caps if at < length - 0.5]


def window_clip(sec, win, speed, work, tag):
    """One continuous window of one take, laid out for the section, as a silent clip."""
    folder = REC / win['take']
    a, b = mc.window(folder, win['from'], win['to'])
    length = (b - a) / speed
    layout = sec['layout']
    inputs, graph = ['-loop', '1', '-t', f'{length:.2f}', '-i', str(work / 'bg.png')], []
    last, n = '0:v', 1

    def add(args):
        nonlocal n
        inputs.extend(args)
        n += 1
        return n - 1

    if layout == 'terminal':
        k = add(frames_input(folder / 'terminal', a, b, speed, work / f'{tag}_term.txt'))
        x, y, w, h = sec.get('crop', [0, 40, 1920, 1160])
        graph.append(f'[{k}:v]crop={w}:{h}:{x}:{y},scale={W}:{H - 40}:force_original_aspect_ratio=decrease[t]')
        graph.append(f'[{last}][t]overlay=(W-w)/2:20[v{n}]')
        last = f'v{n}'
    else:
        big = layout == 'dash'
        dw, dh = (W, H) if big else (1350, 760)
        k = add(frames_input(folder / 'frames', a, b, speed, work / f'{tag}_dash.txt'))
        graph.append(f'[{k}:v]scale={dw}:{dh}[d]')
        graph.append(f'[{last}][d]overlay=0:0[v{n}]')
        last = f'v{n}'
        if layout == 'dash_amb':
            x, y, w, h = sec['amb_crop']
            k = add(frames_input(folder / 'ambiguous', a, b, speed, work / f'{tag}_amb.txt'))
            graph.append(f'[{k}:v]crop={w}:{h}:{x}:{y},scale={W - dw - 16}:{H - 16}:force_original_aspect_ratio=decrease[am]')
            graph.append(f'[{last}][am]overlay={dw + 8}+({W - dw - 16}-w)/2:8[v{n}]')
            last = f'v{n}'
        gazebo = folder / 'gazebo.webm'
        if gazebo.exists() and win.get('gazebo', True):
            start = float((folder / 'gazebo_start.txt').read_text())
            k = add(['-ss', f'{max(0.0, a - start):.2f}', '-t', f'{b - a:.2f}', '-i', str(gazebo)])
            gw, gy = (520, None) if big else (520, dh + 4)
            # The capture is the whole Gazebo window (640x394): crop to its 3D view when the section says where it is.
            crop = 'crop={2}:{3}:{0}:{1},'.format(*sec['gazebo_crop']) if sec.get('gazebo_crop') else ''
            graph.append(f'[{k}:v]setpts=PTS/{speed},{crop}scale={gw}:-2[g]')
            pos = '24:H-h-24' if big else f'0:{gy}'
            graph.append(f'[{last}][g]overlay={pos}:eof_action=pass[v{n}]')
            last = f'v{n}'
        caps = caption_times(events(folder, a, b), speed, length)
        # A section can move the caption bar (dash layout) so it doesn't cover what the section is about.
        cap_x, cap_w = sec.get('caption_box', (560, 1340))
        box = (cap_w, 170) if big else (dw - 536, H - dh - 12)
        for i, (at, text) in enumerate(caps):
            png = work / f'{tag}_cap{i}.png'
            text_png(png, box, sec.get('label'), text, 28 if big else 27, 3 if big else 6)
            until = caps[i + 1][0] - 0.05 if i + 1 < len(caps) else length
            k = add(['-loop', '1', '-t', f'{length:.2f}', '-i', str(png)])
            pos = f'{cap_x}:H-{box[1] + 22}' if big else f'532:{dh + 6}'
            graph.append(f"[{last}][{k}:v]overlay={pos}:enable='between(t,{at:.2f},{until:.2f})'[v{n}]")
            last = f'v{n}'
    clip = work / f'{tag}.mp4'
    mc.encode([*inputs, '-filter_complex', ';'.join(graph) if graph else 'null', '-map', f'[{last}]'], clip,
              ('-t', f'{length:.2f}', '-s', f'{W}x{H}'))
    return clip, length


def section_clip(sec, seconds, work):
    tag = sec['id']
    if sec['layout'] == 'card':
        png = work / f'{tag}_card.png'
        background = None
        if sec.get('background'):
            folder = REC / sec['background']['take']
            at, _ = mc.window(folder, sec['background']['at'], sec['background']['at'])
            chosen = frames_in(folder / 'frames', at, at + 5)
            background = chosen[0][1] if chosen else None
        card_png(png, sec['title'], sec.get('subtitle'), background)
        clip = work / f'{tag}.mp4'
        mc.encode(['-loop', '1', '-t', f'{seconds:.2f}', '-i', str(png), '-vf', f'scale={W}:{H}'], clip)
        return clip
    source = sum(mc.window(REC / w['take'], w['from'], w['to'])[1] - mc.window(REC / w['take'], w['from'], w['to'])[0]
                 for w in sec['windows'])
    speed = max(1.0, source / seconds)
    parts = [window_clip(sec, w, speed, work, f'{tag}_{i}')[0] for i, w in enumerate(sec['windows'])]
    joined = work / f'{tag}_joined.mp4'
    (work / f'{tag}_parts.txt').write_text(''.join(f"file '{p}'\n" for p in parts))
    run(['-f', 'concat', '-safe', '0', '-i', str(work / f'{tag}_parts.txt'), '-c', 'copy', str(joined)])
    clip = work / f'{tag}.mp4'
    extra = []
    if sec.get('outro'):  # a closing panel over the last seconds, e.g. measured results from the take
        png = work / f'{tag}_outro.png'
        text_png(png, (1100, 300), sec.get('label'), sec['outro'], 34, 5)
        extra = ['-loop', '1', '-i', str(png), '-filter_complex',
                 f"[0:v]tpad=stop_mode=clone:stop_duration=60[b];[b][1:v]overlay=(W-w)/2:(H-h)/2:enable='gte(t,{seconds - 5:.2f})'"]
    else:
        extra = ['-vf', 'tpad=stop_mode=clone:stop_duration=60']
    mc.encode(['-i', str(joined), *extra], clip, ('-t', f'{seconds:.2f}'))
    print(f"{tag}: {source:.0f} s of footage at x{speed:.2f} in {seconds:.1f} s", flush=True)
    return clip


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('cut', type=Path)
    ap.add_argument('out', type=Path)
    ap.add_argument('--voice', type=Path)
    a = ap.parse_args()
    cut = json.loads(a.cut.read_text())
    a.out.mkdir(parents=True, exist_ok=True)
    (a.out / 'clips').mkdir(exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix='ripple-film-'))
    Image.new('RGB', (W, H), BG).save(work / 'bg.png')
    voices, timings, parts = {}, [], []
    for sec in cut['sections']:
        clip = next(iter(sorted(a.voice.glob(sec['id'] + '*.mp3')) + sorted(a.voice.glob(sec['id'] + '*.wav'))), None) \
            if a.voice else None
        if clip:
            voices[sec['id']] = clip
        seconds = media_seconds(clip) + LEAD + TAIL if clip else float(sec['seconds'])
        path = section_clip(sec, seconds, work)
        parts.append(path)
        run(['-i', str(path), '-c', 'copy', '-movflags', '+faststart', str(a.out / 'clips' / f"{sec['id']}.mp4")])
        timings.append({'id': sec['id'], 'seconds': round(seconds, 2), 'voice': clip.name if clip else None})
    (work / 'film.txt').write_text(''.join(f"file '{p}'\n" for p in parts))
    silent = work / 'film_silent.mp4'
    run(['-f', 'concat', '-safe', '0', '-i', str(work / 'film.txt'), '-c', 'copy', str(silent)])
    film = a.out / 'film.mp4'
    if voices and len(voices) == len(cut['sections']):
        inputs, chains = ['-i', str(silent)], []
        for i, t in enumerate(timings, 1):
            inputs += ['-i', str(voices[t['id']])]
            chains.append(f"[{i}:a]aformat=sample_rates=48000:channel_layouts=mono,adelay={int(LEAD * 1000)},apad,"
                          f"atrim=0:{t['seconds']:.3f},asetpts=N/SR/TB[a{i}]")
        chains.append(''.join(f'[a{i}]' for i in range(1, len(timings) + 1)) + f'concat=n={len(timings)}:v=0:a=1[a]')
        run([*inputs, '-filter_complex', ';'.join(chains), '-map', '0:v', '-map', '[a]', '-c:v', 'copy', '-c:a', 'aac',
             '-b:a', '160k', '-movflags', '+faststart', str(film)])
    else:
        run(['-i', str(silent), '-c', 'copy', '-movflags', '+faststart', str(film)])
    run(['-ss', str(cut.get('poster_at', 20)), '-i', str(film), '-frames:v', '1', '-update', '1', str(a.out / 'poster.jpg')])
    (a.out / 'film.json').write_text(json.dumps({'sections': timings, 'voiced': len(voices) == len(cut['sections'])}, indent=2))
    total = sum(t['seconds'] for t in timings)
    print(f'done {film} {int(total // 60)}:{total % 60:04.1f} voiced={len(voices)}/{len(cut["sections"])}', flush=True)


if __name__ == '__main__':
    main()
