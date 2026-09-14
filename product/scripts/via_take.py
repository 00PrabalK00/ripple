#!/usr/bin/env python3
"""Recorded take for navigate_via: the operator asks for a route through waypoints Ripple picks itself.

The model chooses the points; the edge refuses any point too close to a wall, obstacle or keepout
(with a clear point suggested nearby), plans every leg before the robot moves, and verifies only the
final arrival. Run under record_take.py; writes marks.json into the take.
  python3 product/scripts/via_take.py [--destination 'Packing B']
"""
import argparse
import json
import os
import time
import urllib.request
from pathlib import Path

API = 'http://127.0.0.1:8060'
ROOT = Path(__file__).resolve().parents[2]
ACTIVE = ('SENDING', 'EXECUTING', 'CANCELLING')


def state():
    return json.load(urllib.request.urlopen(API + '/api/state', timeout=10))


def ask(text):
    print('ask:', text, flush=True)
    r = urllib.request.Request(API + '/api/ask', data=json.dumps({'text': text}).encode(),
                               headers={'Content-Type': 'application/json'})
    return json.load(urllib.request.urlopen(r, timeout=60))


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


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--destination', default='Packing B')
    p.add_argument('--label', default=None, help="the station's label as the dashboard shows it")
    a = p.parse_args()
    label = a.label or a.destination
    out = Path(os.environ.get('RIPPLE_TAKE_DIR') or ROOT / 'recordings' / time.strftime('via-%Y%m%d-%H%M%S'))
    out.mkdir(parents=True, exist_ok=True)
    marks = []

    def mark(step, ok):
        marks.append({'at': time.strftime('%H:%M:%S'), 'step': step, 'ok': bool(ok)})
        print(('OK   ' if ok else 'MISS ') + step, flush=True)

    if not wait(lambda s: s['status'] == 'READY' and not s['incident'], 60):
        raise SystemExit('Site is not ready (open incident); fix before recording')
    before = {e['id'] for e in state()['timeline']}
    new = lambda s: [e for e in s['timeline'] if e['id'] not in before]
    time.sleep(3)
    ask(f'Take the robot to {a.destination}, but not along the direct route: choose two or three waypoints yourself '
        'on open floor, well clear of the racks, and drive through them.')
    via = wait(lambda s: any(e['kind'] == 'tool' and 'via' in e['text'].lower() for e in new(s)), 180)
    mark('Ripple chose its own waypoints (navigate_via)', via is not None)
    arrived = wait(lambda s: s['goal'] and s['goal']['label'] == label and s['goal']['status'] == 'SUCCEEDED'
                   and s['goal'].get('verified'), 420)
    mark(f'every leg driven; arrival at {label} verified', arrived is not None)
    time.sleep(6)
    (out / 'marks.json').write_text(json.dumps(marks, indent=2))
    print(f'{sum(m["ok"] for m in marks)}/{len(marks)} steps', flush=True)


if __name__ == '__main__':
    main()
