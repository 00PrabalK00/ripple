#!/usr/bin/env python3
"""Live checks for navigate_via and pending keepouts, through a running agent started with --test-api.

  1. A via point against a wall is refused, and a nearby clear point is suggested.
  2. A route to Staging Z3 through two open-floor points: every leg plans first, every leg is driven,
     and only the final arrival is verified.
  3. A keepout drawn over the robot is saved as pending, then applied once the robot has driven away.
Writes product/evidence/via-check-<time>.json.
  python3 product/scripts/via_check.py
"""
import json
import time
import urllib.request
from pathlib import Path

API = 'http://127.0.0.1:8060'
ROOT = Path(__file__).resolve().parents[2]
TERMINAL = ('SUCCEEDED', 'ABORTED', 'CANCELED', 'REJECTED', 'UNCERTAIN')


def state():
    return json.load(urllib.request.urlopen(API + '/api/state', timeout=10))


def tool(name, args):
    body = json.dumps({'name': name, 'args': args, 'text': 'via check: ' + name}).encode()
    request = urllib.request.Request(API + '/api/test/tool', data=body, headers={'Content-Type': 'application/json'})
    return json.load(urllib.request.urlopen(request, timeout=120))


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
    results = []

    def check(name, ok, detail):
        results.append({'check': name, 'ok': bool(ok), 'detail': detail, 'at': time.strftime('%H:%M:%S')})
        print(('PASS ' if ok else 'FAIL ') + name + ' — ' + detail, flush=True)

    # 1. Against the west wall of the Staging Z3 alcove.
    r = tool('navigate_via', {'destination': 'Z3', 'via': [{'x': -6.85, 'y': 1.0}]})
    problems = (r.get('data') or {}).get('problems') or []
    better = problems[0].get('nearest_clear_point') if problems else None
    check('a via point against a wall is refused with a nearest clear point',
          r['status'] == 'denied' and better, f"{r['reason'][:180]} · suggested {better}")

    # 2. Two open-floor points south of the shelf block, then into the alcove.
    r = tool('navigate_via', {'destination': 'Z3', 'via': [{'x': -2.0, 'y': -1.6}, {'x': -3.6, 'y': -0.6}],
                              'reason': 'via check: route through open floor'})
    data = r.get('data') or {}
    check('the via route is accepted after every leg plans', r['status'] == 'ok',
          f"{r['reason']} · route {data.get('route_length_m')} m")
    seen = {'legs': set(), 'markers': False}

    def finished(s):
        g = s['goal'] or {}
        if g.get('label') == 'Staging Z3' and g.get('status') == 'EXECUTING':
            seen['legs'].add(g.get('leg', 0))
            seen['markers'] = seen['markers'] or len(g.get('via_uv') or []) == 2
        return g.get('label') == 'Staging Z3' and g.get('status') in TERMINAL and 'stopped' in g
    s = wait(finished, 360) if r['status'] == 'ok' else None
    g = (s or {}).get('goal') or {}
    check('every leg is driven and only the final arrival is verified',
          g.get('verified') and len(g.get('leg_ids') or []) == 3 and seen['legs'] == {0, 1, 2},
          f"status {g.get('status')} · verified {g.get('verified')} · Nav2 goals {len(g.get('leg_ids') or [])} · "
          f"legs seen {sorted(seen['legs'])} · {g.get('outcome_reason')}")
    check('the dashboard state carries the via markers while driving', seen['markers'], f"via_uv seen: {seen['markers']}")

    # 3. A small keepout under the robot, which then leaves for the Charger.
    pose = state()['pose']
    box = [round(pose['x'] - .3, 2), round(pose['y'] - .3, 2), round(pose['x'] + .3, 2), round(pose['y'] + .3, 2)]
    r = tool('add_keepout', {'region': {'box_m': box}, 'reason': 'via check: keepout drawn under the robot'})
    kid = (r.get('data') or {}).get('keepout_id')
    check('a keepout over the robot is saved as pending instead of refused',
          r['status'] == 'ok' and (r.get('data') or {}).get('state') == 'PENDING', r['reason'][:180])
    r = tool('navigate_to', {'destination': 'CHARGE', 'reason': 'via check: leave the pending keepout'})
    s = wait(lambda s: any(k['id'] == kid and k['state'] == 'APPLIED' for k in s['keepouts']), 240) if kid else None
    k = next((k for k in (s or {}).get('keepouts', []) if k['id'] == kid), {})
    check('the pending keepout is applied and verified once the robot has left', s is not None,
          f"navigate {r['status']} · keepout {k.get('state')} · {k.get('verification')}")
    s = wait(lambda s: (s['goal'] or {}).get('label') == 'Charger' and (s['goal'] or {}).get('status') in TERMINAL
             and 'stopped' in s['goal'], 300)
    check('the robot then reaches the Charger, verified', s and s['goal'].get('verified'),
          f"{((s or {}).get('goal') or {}).get('outcome_reason')}")
    if kid:
        tool('remove_keepout', {'keepout_id': kid})

    out = ROOT / 'product' / 'evidence' / time.strftime('via-check-%Y%m%d-%H%M%S.json')
    out.write_text(json.dumps(results, indent=2))
    print(f"{sum(r['ok'] for r in results)}/{len(results)} checks passed · {out}", flush=True)


if __name__ == '__main__':
    main()
