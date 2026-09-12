#!/usr/bin/env python3
"""Drive and record the Ripple demo through the real agent API.

Scenes follow the win condition: constrain the site in words, watch the robot obey,
cause an unexpected failure, watch Ripple notice/investigate/recover, get asked on
Ambiguous, reply in plain language, watch the robot continue, and file a report.

Output: recordings/demo-<time>/{dashboard.webm, gazebo.webm, chapters.json, timeline.json}
  python3 product/scripts/demo_run.py [--no-record] [--reply-timeout 90]
"""
import argparse
import json
import os
import re
import signal
import subprocess
import time
import urllib.request
from pathlib import Path

API = 'http://127.0.0.1:8060'
ROOT = Path(__file__).resolve().parents[2]
OBSTACLE = ROOT / 'product/scripts/demo_obstacle.py'
REPLY = 'Yes — a pallet is parked at Rack B5. Keep robots out of there and use Rack B3 instead.'
# Aisle segment between Rack A3 and Rack A5 (map x 3.39..3.99, y -4.8..-3.8), as normalized map-image bounds.
AISLE = [0.7414, 0.6850, 0.7843, 0.7327]
ACTIVE = ('SENDING', 'EXECUTING', 'CANCELLING')
HOME = (-0.79, -0.67)  # Home dock: wide open, a clean start for every take


def state():
    return json.load(urllib.request.urlopen(API + '/api/state', timeout=10))


def post(path, body):
    req = urllib.request.Request(API + path, data=json.dumps(body).encode(), headers={'Content-Type': 'application/json'})
    return json.load(urllib.request.urlopen(req, timeout=60))


def ask(text):
    print('   ask:', text, flush=True)
    post('/api/ask', {'text': text})


def wait(pred, timeout, every=1.0):
    end = time.time() + timeout
    while time.time() < end:
        try:
            s = state()
            if pred(s):
                return s
        except Exception:
            pass
        time.sleep(every)
    return None


def obstacle(args):
    subprocess.run(['bash', '-c', f'source /opt/ros/humble/setup.bash && python3 {OBSTACLE} {args}'], check=False)


def arrived(label, success=False):
    def check(s):
        g = s['goal']
        done = g and g['label'] == label and g['status'] not in ACTIVE and 'stopped' in g
        return done and (not success or (g['status'] == 'SUCCEEDED' and g.get('verified')))
    return check


def incident_state(*states):
    return lambda s: s['incident'] and s['incident']['state'] in states


def gazebo_window():
    tree = subprocess.run(['xwininfo', '-root', '-tree'], capture_output=True, text=True).stdout
    for line in tree.splitlines():
        m = re.match(r'\s*(0x[0-9a-f]+) "Gazebo": \("gazebo" "gazebo"\)\s+(\d+)x(\d+)', line)
        if m and int(m.group(2)) > 800:
            return m.group(1)
    return None


def prepare():
    print('== preparing the site (not recorded)', flush=True)
    obstacle('remove pallet')
    s = state()
    for k in s['keepouts']:
        post('/api/keepouts/reopen', {'id': k['id']})
    if any(not st['available'] for st in s['stations']):
        ask('All stations are available again.')
        wait(lambda s: all(st['available'] for st in s['stations']), 90)
    if not s['pose'] or abs(s['pose']['x'] - HOME[0]) > 0.4 or abs(s['pose']['y'] - HOME[1]) > 0.4:
        ask('Send the robot to the Home dock.')
        if not wait(arrived('Home dock', success=True), 240):
            raise SystemExit('Could not reach the starting point (Home dock)')
    if not wait(lambda s: s['status'] == 'READY', 120):
        raise SystemExit('An incident is still open; resolve it before recording')
    subprocess.run(['bash', '-c', 'source /opt/ros/humble/setup.bash && timeout 5 gz camera -c gzclient_camera -f my_bot'],
                   capture_output=True)
    time.sleep(3)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--no-record', action='store_true')
    p.add_argument('--reply-timeout', type=float, default=90)
    p.add_argument('--skip-prepare', action='store_true')
    a = p.parse_args()
    if not a.skip_prepare:
        prepare()
    out = ROOT / 'recordings' / time.strftime('demo-%Y%m%d-%H%M%S')
    out.mkdir(parents=True, exist_ok=True)
    procs = []
    if not a.no_record:
        procs.append(subprocess.Popen(['node', str(ROOT / 'product/scripts/record_dashboard.mjs'), str(out)], cwd=ROOT))
        xid = gazebo_window()
        if xid:
            procs.append(subprocess.Popen(
                ['gst-launch-1.0', '-e', 'ximagesrc', f'xid={xid}', 'use-damage=0', '!', 'video/x-raw,framerate=10/1', '!',
                 'videoscale', '!', 'video/x-raw,width=924,height=568', '!', 'videoconvert', '!',
                 'vp8enc', 'deadline=1', 'cpu-used=16', 'threads=4', 'target-bitrate=2500000', '!', 'webmmux', '!',
                 'filesink', f'location={out / "gazebo.webm"}'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        time.sleep(4)
    t0 = time.time()
    chapters = []

    def chapter(title):
        chapters.append({'t': round(time.time() - t0, 1), 'title': title})
        print(f'[{chapters[-1]["t"]:6.1f}s] {title}', flush=True)

    try:
        chapter('Ripple · an always-on site engineer for robots')
        time.sleep(3)
        chapter('1 · Tell Ripple where the robot may operate')
        post('/api/draw', {'action': 'area', 'bounds': AISLE, 'name': 'Aisle 3'})
        time.sleep(2)
        ask('Aisle 3 is closed for construction. Keep robots out of it.')
        if not wait(lambda s: any(k['state'] == 'APPLIED' for k in s['keepouts']), 90):
            print('   WARNING: keepout not verified in time', flush=True)
        time.sleep(3)
        chapter('2 · Watch the robot obey the new restriction')
        ask('Send the robot to Rack A5.')
        s = wait(arrived('Rack A5'), 240)
        print('   outcome:', s and s['goal']['status'], s and s['goal'].get('verified'), flush=True)
        chapter('3 · Something unexpected: a pallet blocks Rack B5')
        obstacle('spawn pallet --station B5')
        time.sleep(2)
        ask('Take the robot to Rack B5.')
        chapter('4 · Ripple notices, investigates and tries to recover on its own')
        if not wait(incident_state('ESCALATED'), 300):
            print('   WARNING: incident did not escalate in time', flush=True)
        chapter('5 · Ripple asks the engineer on Ambiguous')
        replied = wait(lambda s: s['incident'] and s['incident']['state'] not in ('ESCALATED',), a.reply_timeout)
        if not replied:
            print('   no Ambiguous reply yet; replying from the dashboard', flush=True)
            ask(REPLY)
        chapter('6 · Human context becomes robot action')
        s = wait(incident_state('RESOLVED', 'CLOSED'), 300)
        print('   incident:', s and s['incident']['state'], s and s['incident'].get('resolution'), flush=True)
        time.sleep(4)
        chapter('7 · Ripple reports to the team')
        ask('File a short incident report in Ambiguous for what just happened.')
        wait(lambda s: any('Ambiguous report create' in e['text'] for e in s['timeline'][-20:]), 120)
        time.sleep(6)
        chapter('End')
    finally:
        (out / 'chapters.json').write_text(json.dumps(chapters, indent=2))
        try:
            (out / 'timeline.json').write_text(json.dumps(state()['timeline'], indent=2))
        except Exception:
            pass
        (out / 'STOP').touch()
        for proc in procs[1:]:
            proc.send_signal(signal.SIGINT)
        for proc in procs:
            try:
                proc.wait(timeout=60)
            except subprocess.TimeoutExpired:
                proc.kill()
        print('== saved', out, flush=True)


if __name__ == '__main__':
    main()
