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
# Map box x -2.6..-2.0, y -0.2..0.4 on the Home dock -> Staging Z3 line in open floor. With robot clearance it
# closes the gap to the shelf block, so the planner detours south through open floor. (The earlier box at
# x -4.3..-3.7 nearly sealed the Z3 alcove mouth: every detour squeezed past a wall end or shelf corner and the
# safety controller's front zone stopped the robot there.)
WALKWAY = [0.31357, 0.48449, 0.35643, 0.51313]
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


LOW_MEMORY_MB = 1200
low_water = [None]


def memory_ok():
    """Stop the take cleanly, recorders included, before the machine runs short of memory."""
    avail = next(int(l.split()[1]) // 1024 for l in open('/proc/meminfo') if l.startswith('MemAvailable'))
    low_water[0] = avail if low_water[0] is None else min(low_water[0], avail)
    if avail < LOW_MEMORY_MB:
        raise SystemExit(f'Stopping the take: only {avail} MB of memory available')


def wait(pred, timeout):
    end = time.time() + timeout
    while time.time() < end:
        memory_ok()
        try:
            s = state()
            if pred(s):
                return s
        except Exception:
            pass
        time.sleep(1)


def done(label):
    """Verified arrival at label; a canceled or aborted goal (e.g. replaced during recovery) doesn't count."""
    return lambda s: (s['goal'] and s['goal']['label'] == label and s['goal']['status'] == 'SUCCEEDED'
                      and s['goal'].get('verified'))


def obstacle(a):
    out = subprocess.run(['bash', '-c', f'source /opt/ros/humble/setup.bash && python3 {ROOT}/product/scripts/demo_obstacle.py {a}'],
                         capture_output=True, text=True)
    text = (out.stdout + out.stderr).strip().splitlines()
    print('obstacle:', text[-1] if text else '(no output)', flush=True)
    return out.returncode == 0


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
    # The take starts with the robot docked at an available Home dock (a previous take may have closed it).
    if not next(st['available'] for st in state()['stations'] if st['name'] == 'HOME'):
        post('/api/test/tool', {'name': 'set_station_availability', 'text': 'reset before recording',
                                'args': {'destination': 'HOME', 'available': True, 'reason': 'pallet removed'}})
    s = state()
    at_home = s['goal'] and s['goal']['label'] == 'Home dock' and s['goal']['status'] == 'SUCCEEDED'
    if not at_home:
        # One retry: a transient localization or planner hiccup on the way home should not cost the take.
        for _ in range(2):
            ask('Send the robot to the Home dock.')
            if wait(done('Home dock'), 240):
                break
        else:
            raise SystemExit('Robot did not reach the Home dock before recording')
    subprocess.run(['bash', '-c', 'source /opt/ros/humble/setup.bash && timeout 5 gz camera -c gzclient_camera -f my_bot'],
                   capture_output=True)
    tree = subprocess.run(['xwininfo', '-root', '-tree'], capture_output=True, text=True).stdout
    xid = next((m.group(1) for m in re.finditer(r'(0x[0-9a-f]+) "Gazebo": \("gazebo" "gazebo"\)\s+(\d+)x', tree)
                if int(m.group(2)) > 800), None)
    # Recorders run at low CPU and I/O priority: at normal priority their encoding starved Nav2's controller.
    low = ['nice', '-n', '15', 'ionice', '-c3']
    procs = [subprocess.Popen([*low, 'node', str(ROOT / 'product/scripts/record_dashboard.mjs'), str(out)], cwd=ROOT)]
    if xid:
        procs.append(subprocess.Popen(
            ['nice', '-n', '19', 'ionice', '-c3', 'gst-launch-1.0', '-e', 'ximagesrc', f'xid={xid}', 'use-damage=0', '!',
             'video/x-raw,framerate=6/1', '!', 'videoscale', '!', 'video/x-raw,width=640,height=394', '!', 'videoconvert', '!',
             'vp8enc', 'deadline=1', 'cpu-used=16', 'threads=2', 'target-bitrate=1200000', '!', 'webmmux', '!', 'filesink',
             f'location={out / "gazebo.webm"}'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        (out / 'gazebo_start.txt').write_text(str(time.time()))  # lines the simulator view up with wall time
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
        # Allow for a safety stop on the way and Ripple's own recovery from it (simulator runs below real time).
        s = wait(done('Staging Z3'), 360)
        mark('robot routed around the keepout and arrived verified', s and s['goal']['status'] == 'SUCCEEDED' and s['goal']['verified'])
        # Site memory: reopen the walkway by referring to it in words, so the way home is clear.
        ask('Construction on the West walkway is finished. Open that area again.')
        s = wait(lambda s: not s['keepouts'], 120)
        mark('keepout reopened from words (site memory)', s is not None)
        if not obstacle('spawn pallet --station HOME'):
            mark('pallet placed on the Home dock', False)
            raise SystemExit('Pallet did not spawn; stopping so the take never tells a false story')
        time.sleep(2)
        # Every check below is tied to the incident this command opens, never to whichever incident is current.
        before = {e['id'] for e in state()['timeline']}
        new = lambda s: [e for e in s['timeline'] if e['id'] not in before]
        ask('Take the robot back to the Home dock.')

        def opened(s):
            return next((e['incident'] for e in new(s) if e['kind'] == 'incident' and e['text'].startswith('Incident opened')), None)

        def asked(s):
            iid = opened(s)
            return iid and any(e['kind'] == 'reply' and e.get('incident') == iid and e.get('channel') == 'ambiguous' for e in new(s))
        s = wait(asked, 300)
        iid = opened(s) if s else None
        mark(f'failure detected, investigated and escalated to the engineer on Ambiguous ({iid})', s is not None)
        replied = wait(lambda s: any(e['kind'] == 'operator' and e.get('channel') == 'ambiguous' for e in new(s)), a.reply_timeout)
        mark('engineer replied on Ambiguous', replied is not None)
        if not replied:
            ask(REPLY)  # dashboard fallback so the take can finish; the step above stays a miss

        def recovered(s):
            home = next(st for st in s['stations'] if st['name'] == 'HOME')
            return (done('Charger')(s) and s['keepouts'] and not home['available'] and any(
                e['kind'] == 'incident' and e.get('incident') == iid and e.get('state') == 'RESOLVED' for e in new(s)))
        s = wait(recovered, 420)
        mark('human context became a keepout, an unavailable dock and a new destination; verified arrival resolved the incident',
             s is not None)
        before = {e['id'] for e in state()['timeline']}
        ask('File a short incident report in Ambiguous for what just happened.')
        s = wait(lambda s: any('Ambiguous report create — ok' in e['text'] for e in new(s)), 150)
        mark('incident report filed to the Ambiguous workspace', s is not None)
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
        log(f'saved {out}: {sum(m["ok"] for m in marks)}/{len(marks)} steps; lowest available memory {low_water[0]} MB')


if __name__ == '__main__':
    main()
