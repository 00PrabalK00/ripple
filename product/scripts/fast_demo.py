#!/usr/bin/env python3
"""Short recorded demo take on the open west floor. Writes recordings/fast-<time>/."""
import json, signal, subprocess, time, urllib.request, re
from pathlib import Path
API = 'http://127.0.0.1:8060'
ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'recordings' / time.strftime('fast-%H%M%S'); OUT.mkdir(parents=True, exist_ok=True)
WALKWAY = [0.214, 0.4455, 0.286, 0.509]
ACTIVE = ('SENDING', 'EXECUTING', 'CANCELLING')

def state(): return json.load(urllib.request.urlopen(API + '/api/state', timeout=10))
def post(p, b):
    r = urllib.request.Request(API + p, data=json.dumps(b).encode(), headers={'Content-Type': 'application/json'})
    return json.load(urllib.request.urlopen(r, timeout=60))
def ask(t): print('ask:', t, flush=True); post('/api/ask', {'text': t})
def wait(pred, timeout):
    end = time.time() + timeout
    while time.time() < end:
        try:
            s = state()
            if pred(s): return s
        except Exception: pass
        time.sleep(1)
def done(label): return lambda s: s['goal'] and s['goal']['label'] == label and s['goal']['status'] not in ACTIVE and 'stopped' in s['goal']
def obstacle(a): subprocess.run(['bash', '-c', f'source /opt/ros/humble/setup.bash && python3 {ROOT}/product/scripts/demo_obstacle.py {a}'])

tree = subprocess.run(['xwininfo', '-root', '-tree'], capture_output=True, text=True).stdout
xid = next((m.group(1) for m in re.finditer(r'(0x[0-9a-f]+) "Gazebo": \("gazebo" "gazebo"\)\s+(\d+)x', tree) if int(m.group(2)) > 800), None)
procs = [subprocess.Popen(['node', str(ROOT / 'product/scripts/record_dashboard.mjs'), str(OUT)], cwd=ROOT)]
if xid:
    procs.append(subprocess.Popen(['gst-launch-1.0', '-e', 'ximagesrc', f'xid={xid}', 'use-damage=0', '!', 'video/x-raw,framerate=10/1', '!',
        'videoscale', '!', 'video/x-raw,width=924,height=568', '!', 'videoconvert', '!', 'vp8enc', 'deadline=1', 'cpu-used=16',
        'threads=4', 'target-bitrate=2500000', '!', 'webmmux', '!', 'filesink', f'location={OUT / "gazebo.webm"}'],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
time.sleep(5)
try:
    post('/api/draw', {'action': 'keepout', 'bounds': WALKWAY, 'name': 'West walkway', 'reason': 'Closed for construction'})
    time.sleep(3)
    ask('Send the robot to Staging Z3.')
    wait(done('Staging Z3'), 150)
    obstacle('spawn pallet --station HOME')
    time.sleep(2)
    ask('Take the robot back to the Home dock.')
    wait(lambda s: s['incident'] and s['incident']['state'] == 'ESCALATED', 200)
    time.sleep(4)
    ask('Yes, a pallet is parked on the Home dock. Keep robots out of it and send the robot to the Charger instead.')
    wait(lambda s: s['goal'] and s['goal']['label'] == 'Charger' and s['goal']['status'] in ACTIVE, 150)
    time.sleep(35)
finally:
    (OUT / 'timeline.json').write_text(json.dumps(state()['timeline'], indent=2))
    (OUT / 'STOP').touch()
    for p in procs[1:]: p.send_signal(signal.SIGINT)
    for p in procs:
        try: p.wait(timeout=60)
        except subprocess.TimeoutExpired: p.kill()
    print('SAVED', OUT, flush=True)
