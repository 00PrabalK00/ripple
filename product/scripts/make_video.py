#!/usr/bin/env python3
"""Cut a take into the site video: speed up to <=115 s, Gazebo inset, H.264 + poster.
  make_video.py RECORDING_DIR OUT_DIR"""
import re
import subprocess
import sys
from pathlib import Path
import imageio_ffmpeg

FF = imageio_ffmpeg.get_ffmpeg_exe()
src, out = Path(sys.argv[1]), Path(sys.argv[2])
out.mkdir(parents=True, exist_ok=True)


def duration(path):
    log = subprocess.run([FF, '-i', str(path), '-f', 'null', '-'], capture_output=True, text=True).stderr
    times = re.findall(r'time=(\d+):(\d+):([\d.]+)', log)
    h, m, s = times[-1]
    return int(h) * 3600 + int(m) * 60 + float(s)


dash = src / 'dashboard.webm'
if not dash.exists():  # recorder stopped before renaming its output
    dash = max(src.glob('page@*.webm'), key=lambda p: p.stat().st_size)
gazebo = src / 'gazebo.webm'
length = duration(dash)
speed = max(1.0, length / 115.0)
print(f'take {length:.0f}s -> speed x{speed:.2f}')
inputs = ['-i', str(dash)]
graph = f'[0:v]setpts=PTS/{speed},fps=30,scale=1920:1080[v]'
if gazebo.exists() and gazebo.stat().st_size > 10000:
    inputs += ['-i', str(gazebo)]
    graph = (f'[0:v]setpts=PTS/{speed},fps=30,scale=1920:1080[a];[1:v]setpts=PTS/{speed},fps=30,scale=520:-2[b];'
             f'[a][b]overlay=24:H-h-24:eof_action=pass[v]')
subprocess.run([FF, '-y', *inputs, '-filter_complex', graph, '-map', '[v]', '-an', '-t', '120',
                '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '27', '-pix_fmt', 'yuv420p', '-movflags', '+faststart',
                str(out / 'demo.mp4')], check=True)
subprocess.run([FF, '-y', '-ss', '40', '-i', str(out / 'demo.mp4'), '-frames:v', '1', str(out / 'poster.jpg')], check=True)
print('done', out / 'demo.mp4', (out / 'demo.mp4').stat().st_size // 1024, 'KB')
