#!/usr/bin/env python3
"""Record a take around any command: the dashboard (headless, timestamped frames), the Gazebo window,
and optional screen regions (an Ambiguous window, a terminal), all on one wall clock.

Writes recordings/take-<time>-<name>/: frames/ (dashboard), gazebo.webm + gazebo_start.txt,
<region>/ frames for each --region, timeline.json, and the command's exit status. The command gets
RIPPLE_TAKE_DIR so it can leave its own notes (marks.json) in the take.
  record_take.py NAME [--region ambiguous=X,Y,W,H] [--no-dashboard] [--no-gazebo] -- COMMAND...
"""
import argparse
import json
import os
import re
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PY = str(ROOT / 'product/.venv/bin/python')
LOW_MEMORY_MB = 1200
LOW = ['nice', '-n', '15', 'ionice', '-c3']  # at normal priority, recorders starved Nav2's controller


def available_mb():
    return next(int(l.split()[1]) // 1024 for l in open('/proc/meminfo') if l.startswith('MemAvailable'))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('name')
    ap.add_argument('--region', action='append', default=[], help='NAME=X,Y,W,H of the screen to record')
    ap.add_argument('--fps', type=float, default=3.0)
    ap.add_argument('--no-dashboard', action='store_true')
    ap.add_argument('--no-gazebo', action='store_true')
    # Everything after `--` is the command; argparse's REMAINDER would also swallow the options before it.
    argv = sys.argv[1:]
    split = argv.index('--') if '--' in argv else len(argv)
    a, command = ap.parse_args(argv[:split]), argv[split + 1:]
    if command and not Path(command[0]).exists() and not any(
            os.access(os.path.join(p, command[0]), os.X_OK) for p in os.environ.get('PATH', '').split(os.pathsep)):
        raise SystemExit(f'command not found: {command[0]}')
    out = ROOT / 'recordings' / f"take-{time.strftime('%Y%m%d-%H%M%S')}-{a.name}"
    out.mkdir(parents=True)
    procs, gst = [], None
    if not a.no_dashboard:
        procs.append(subprocess.Popen([*LOW, 'node', str(ROOT / 'product/scripts/record_dashboard.mjs'), str(out)], cwd=ROOT))
    for spec in a.region:
        name, box = spec.split('=')
        procs.append(subprocess.Popen([*LOW, PY, str(ROOT / 'product/scripts/record_screen.py'), str(out), name,
                                       *box.split(','), str(a.fps)]))
    if not a.no_gazebo:
        subprocess.run(['bash', '-c', 'source /opt/ros/humble/setup.bash && timeout 5 gz camera -c gzclient_camera -f my_bot'],
                       capture_output=True)
        tree = subprocess.run(['xwininfo', '-root', '-tree'], capture_output=True, text=True).stdout
        xid = next((m.group(1) for m in re.finditer(r'(0x[0-9a-f]+) "Gazebo": \("gazebo" "gazebo"\)\s+(\d+)x', tree)
                    if int(m.group(2)) > 500), None)
        if xid:
            gst = subprocess.Popen(
                ['nice', '-n', '19', 'ionice', '-c3', 'gst-launch-1.0', '-e', 'ximagesrc', f'xid={xid}', 'use-damage=0', '!',
                 'video/x-raw,framerate=6/1', '!', 'videoscale', '!', 'video/x-raw,width=640,height=394', '!', 'videoconvert', '!',
                 'vp8enc', 'deadline=1', 'cpu-used=16', 'threads=2', 'target-bitrate=1200000', '!', 'webmmux', '!', 'filesink',
                 f'location={out / "gazebo.webm"}'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            (out / 'gazebo_start.txt').write_text(str(time.time()))
    time.sleep(4)
    print(f'[take] recording {out}', flush=True)
    status, low = None, available_mb()
    job = subprocess.Popen(command, env={**os.environ, 'RIPPLE_TAKE_DIR': str(out)}) if command else None
    try:
        while job and job.poll() is None:
            low = min(low, available_mb())
            if low < LOW_MEMORY_MB:
                print(f'[take] only {low} MB of memory available: stopping the take', flush=True)
                job.send_signal(signal.SIGINT)
                job.wait(timeout=60)
                break
            time.sleep(1)
        status = job.returncode if job else 0
    except KeyboardInterrupt:
        if job:
            job.send_signal(signal.SIGINT)
            job.wait(timeout=60)
        status = 'interrupted'
    finally:
        try:
            timeline = json.load(urllib.request.urlopen('http://127.0.0.1:8060/api/state', timeout=10))['timeline']
            (out / 'timeline.json').write_text(json.dumps(timeline, indent=2))
        except Exception as exc:
            print('[take] could not save the timeline:', exc, flush=True)
        time.sleep(3)
        (out / 'STOP').touch()
        if gst:
            gst.send_signal(signal.SIGINT)
        for p in procs + ([gst] if gst else []):
            try:
                p.wait(timeout=90)
            except subprocess.TimeoutExpired:
                p.kill()
        (out / 'status.json').write_text(json.dumps({'command': command, 'status': status, 'lowest_memory_mb': low}))
        print(f'[take] saved {out} (command status {status}, lowest available memory {low} MB)', flush=True)
    sys.exit(0 if status == 0 else 1)


if __name__ == '__main__':
    main()
