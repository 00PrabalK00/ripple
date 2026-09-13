#!/usr/bin/env python3
"""Live check that Ripple learns at a place: the same trouble twice at the same spot.

Each round sends the robot from the Home dock towards Staging Z3. When it reaches the same point
on the way, a block appears right in front of it: the safety controller stops it, Ripple opens an
incident and recovers. The first round should leave a lesson for that place; the second incident
there must be matched to the same lesson and upvote it. Recovery time and steps for both rounds
are reported. Needs the simulator and an agent started with --test-api.
Writes product/evidence/learning-check-<time>.json.
  python3 product/scripts/learning_check.py
"""
import json
import math
import subprocess
import time
import urllib.request
from pathlib import Path

API = 'http://127.0.0.1:8060'
ROOT = Path(__file__).resolve().parents[2]
TRIGGER = (-2.3, -0.2)   # the same point on the Home -> Staging Z3 route in every round
TERMINAL = ('SUCCEEDED', 'ABORTED', 'CANCELED', 'REJECTED', 'UNCERTAIN')


def get(path):
    return json.load(urllib.request.urlopen(API + path, timeout=10))


def post(path, body):
    r = urllib.request.Request(API + path, data=json.dumps(body).encode(), headers={'Content-Type': 'application/json'})
    return json.load(urllib.request.urlopen(r, timeout=120))


def tool(name, **args):
    return post('/api/test/tool', {'name': name, 'args': args, 'text': 'learning check: ' + name})


def obstacle(args):
    out = subprocess.run(['bash', '-c', f'source /opt/ros/humble/setup.bash && python3 {ROOT}/product/scripts/demo_obstacle.py {args}'],
                         capture_output=True, text=True, timeout=90)
    return (out.stdout + out.stderr).strip().splitlines()[-1:] or ['']


def wait(pred, timeout):
    end = time.time() + timeout
    while time.time() < end:
        try:
            s = get('/api/state')
            if pred(s):
                return s
        except Exception:
            pass
        time.sleep(0.5)


def go_home():
    tool('navigate_to', destination='HOME', reason='learning check: start position')
    wait(lambda s: (s['goal'] or {}).get('label') == 'Home dock' and (s['goal'] or {}).get('status') in TERMINAL
         and 'stopped' in (s['goal'] or {}), 240)


def round_(n):
    obstacle('remove block')
    go_home()
    before = {e['id'] for e in get('/api/state')['timeline']}
    tool('navigate_to', destination='Z3', reason=f'learning check round {n}')
    near = wait(lambda s: s['pose'] and math.dist((s['pose']['x'], s['pose']['y']), TRIGGER) < 0.5, 120)
    if not near:
        return {'round': n, 'ok': False, 'detail': 'the robot never reached the trigger point'}
    spawned = obstacle('spawn block --ahead 0.55')
    new = lambda s: [e for e in s['timeline'] if e['id'] not in before]
    opened = wait(lambda s: any(e['kind'] == 'incident' and e['text'].startswith('Incident opened') for e in new(s)), 60)
    if not opened:
        obstacle('remove block')
        return {'round': n, 'ok': False, 'detail': 'no incident opened: ' + ' '.join(spawned)}
    iid = next(e['incident'] for e in new(opened) if e['kind'] == 'incident' and e['text'].startswith('Incident opened'))
    closed = wait(lambda s: any(e['kind'] == 'incident' and e.get('incident') == iid and e.get('state') in ('RESOLVED', 'CLOSED')
                                for e in new(s)), 360)
    if closed is None:
        # Escalated to the engineer: answer as the local operator so the round can finish.
        post('/api/ask', {'text': 'The block stays where it is. Go around it and continue to Staging Z3.'})
        closed = wait(lambda s: any(e['kind'] == 'incident' and e.get('incident') == iid and e.get('state') in ('RESOLVED', 'CLOSED')
                                    for e in new(s)), 300)
    obstacle('remove block')
    entries = new(closed or get('/api/state'))
    steps = [e['text'] for e in entries if e.get('incident') == iid and e['kind'] == 'tool']
    end = next((e for e in entries if e.get('incident') == iid and e.get('state') in ('RESOLVED', 'CLOSED')), None)
    seconds = None
    if end:
        m = end['text'].rsplit('(', 1)[-1].rstrip(')')
        seconds = sum(int(p[:-1]) * (60 if p.endswith('m') else 1) for p in m.split() if p[:-1].isdigit())
    learned = [e['text'] for e in entries if e.get('incident') == iid and 'Site memory updated' in e['text']]
    return {'round': n, 'ok': bool(end and end.get('state') == 'RESOLVED'), 'incident': iid,
            'state': end and end.get('state'), 'recovery_s': seconds, 'steps': steps, 'lesson': learned[-1] if learned else None}


def main():
    results = [round_(1), round_(2)]
    lessons = get('/api/lessons')['lessons']
    incidents = {r.get('incident') for r in results}
    lesson = next((l for l in lessons if incidents <= set(l['evidence'])), None)
    checks = [
        ('round 1 recovered with a verified arrival', results[0]['ok'], results[0]),
        ('round 1 left a lesson for the place', bool(results[0].get('lesson')), results[0].get('lesson')),
        ('round 2 recovered with a verified arrival', results[1]['ok'], results[1]),
        ('both incidents are one lesson for one place, upvoted twice', bool(lesson and lesson['successes'] >= 2),
         lesson and lesson['summary']),
    ]
    for name, ok, detail in checks:
        print(('PASS ' if ok else 'FAIL ') + name)
    for r in results:
        print(f"  round {r['round']}: {r.get('state')} in {r.get('recovery_s')} s with {len(r.get('steps') or [])} steps")
    out = ROOT / 'product' / 'evidence' / time.strftime('learning-check-%Y%m%d-%H%M%S.json')
    out.write_text(json.dumps({'checks': [{'check': n, 'ok': bool(ok), 'detail': d} for n, ok, d in checks], 'rounds': results}, indent=2, default=str))
    print(f"{sum(1 for _, ok, _ in checks if ok)}/{len(checks)} checks passed · {out}")


if __name__ == '__main__':
    main()
