#!/usr/bin/env python3
"""Recorded end-to-end demo take on the open west floor. Writes recordings/take-<time>/.

Scenes: keepout from the dashboard -> robot routes around it -> pallet blocks the Home dock ->
Ripple detects, investigates, attempts recovery, asks the engineer on Ambiguous -> the reply
(real, or a dashboard fallback) becomes a keepout and a new destination -> verified arrival ->
incident resolved -> report filed to the workspace.
  python3 product/scripts/fast_demo.py [--reply-timeout 120]
"""
import argparse
import json
import re
import signal
import subprocess
import time
import urllib.request
from pathlib import Path

API = 'http://127.0.0.1:8060'
ROOT = Path(__file__).resolve().parents[2]
WALKWAY = [0.214, 0.4455, 0.286, 0.509]
ACTIVE = ('SENDING', 'EXECUTING', 'CANCELLING')
REPLY = 'Yes, a pallet is parked on the Home dock. Keep robots out of it and send the robot to the Charger instead.'


def state():
    return json.load(urllib.request.urlopen(API + '/api/state', timeout=10))


def post(p, b):
    r = urllib.request.Request(API + p, data=json.dumps(b).encode(), headers={'Content-Type': 'application/json'})
    return json.load(urllib.request.urlopen(r, timeout=60))


def ask(t):
    print('ask:', t, flush=True)
    post('/api/ask', {'text': t})


def wait(pred, timeout):
    end = time.time() + timeout
    while time.time() < end:
        try:
            s = state()
            if pred(s):
                return s
        except Exception:
            pass
        time.sleep(1)


def done(label):
    return lambda s: s['goal'] and s['goal']['label'] == label and s['goal']['status'] not in ACTIVE and 'stopped' in s['goal']


def obstacle(a):
    subprocess.run(['bash', '-c', f'source /opt/ros/humble/setup.bash && python3 {ROOT}/product/scripts/demo_obstacle.py {a}'],
                   capture_output=True)


def log(msg):
    print(f'[{time.strftime("%H:%M:%S")}] {msg}', flush=True)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--reply-timeout', type=float, default=120)
    a = p.parse_args()
    out = ROOT / 'recordings' / time.strftime('take-%Y%m%d-%H%M%S')
    out.mkdir(parents=True, exist_ok=True)
    # Clean start: no pallet, no keepouts, robot idle near spawn.
    obstacle('remove pallet')
    for k in state()['keepouts']:
        post('/api/keepouts/reopen', {'id': k['id']})
    if not wait(lambda s: s['status'] == 'READY' and not s['keepouts'], 60):
        raise SystemExit('Site is not clean (open incident or keepout); fix before recording')
    subprocess.run(['bash', '-c', 'source /opt/ros/humble/setup.bash && timeout 5 gz camera -c gzclient_camera -f my_bot'],
                   capture_output=True)
    tree = subprocess.run(['xwininfo', '-root', '-tree'], capture_output=True, text=True).stdout
    xid = next((m.group(1) for m in re.finditer(r'(0x[0-9a-f]+) "Gazebo": \("gazebo" "gazebo"\)\s+(\d+)x', tree)
                if int(m.group(2)) > 800), None)
    procs = [subprocess.Popen(['node', str(ROOT / 'product/scripts/record_dashboard.mjs'), str(out)], cwd=ROOT)]
    if xid:
        procs.append(subprocess.Popen(
            ['gst-launch-1.0', '-e', 'ximagesrc', f'xid={xid}', 'use-damage=0', '!', 'video/x-raw,framerate=10/1', '!',
             'videoscale', '!', 'video/x-raw,width=924,height=568', '!', 'videoconvert', '!', 'vp8enc', 'deadline=1',
             'cpu-used=16', 'threads=4', 'target-bitrate=2500000', '!', 'webmmux', '!', 'filesink',
             f'location={out / "gazebo.webm"}'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    time.sleep(5)
    marks = []

    def mark(name, ok=True):
        marks.append({'at': time.strftime('%H:%M:%S'), 'step': name, 'ok': bool(ok)})
        log(('OK   ' if ok else 'MISS ') + name)

    try:
        r = post('/api/draw', {'action': 'keepout', 'bounds': WALKWAY, 'name': 'West walkway', 'reason': 'Closed for construction'})
        mark('keepout applied and verified', r.get('status') == 'ok' and r.get('verified'))
        time.sleep(3)
        ask('Send the robot to Staging Z3.')
        s = wait(done('Staging Z3'), 180)
        mark('robot routed around the keepout and arrived verified', s and s['goal']['status'] == 'SUCCEEDED' and s['goal']['verified'])
        obstacle('spawn pallet --station HOME')
        time.sleep(2)
        ask('Take the robot back to the Home dock.')
        s = wait(lambda s: s['incident'] and s['incident']['state'] == 'ESCALATED', 240)
        mark('failure detected, investigated and escalated to the engineer on Ambiguous', s is not None)
        replied = wait(lambda s: s['incident'] and s['incident']['state'] != 'ESCALATED', a.reply_timeout)
        mark('engineer replied on Ambiguous', replied is not None)
        if not replied:
            ask(REPLY)
        s = wait(lambda s: s['incident'] and s['incident']['state'] in ('RESOLVED', 'CLOSED'), 300)
        mark('human context became keepout + new destination; verified arrival resolved the incident',
             s is not None and s['incident']['state'] == 'RESOLVED')
        ask('File a short incident report in Ambiguous for what just happened.')
        s = wait(lambda s: any('Ambiguous report create' in e['text'] for e in s['timeline'][-25:]), 150)
        mark('incident report filed to the Ambiguous workspace', s is not None and any(
            'Ambiguous report create — ok' in e['text'] for e in s['timeline'][-25:]))
        time.sleep(6)
    finally:
        (out / 'marks.json').write_text(json.dumps(marks, indent=2))
        try:
            (out / 'timeline.json').write_text(json.dumps(state()['timeline'], indent=2))
        except Exception:
            pass
        (out / 'STOP').touch()
        for proc in procs[1:]:
            proc.send_signal(signal.SIGINT)
        for proc in procs:
            try:
                proc.wait(timeout=90)
            except subprocess.TimeoutExpired:
                proc.kill()
        log(f'saved {out}: {sum(m["ok"] for m in marks)}/{len(marks)} steps')


if __name__ == '__main__':
    main()
