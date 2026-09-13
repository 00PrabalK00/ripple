#!/usr/bin/env python3
"""Live checks for (a) the stock-Nav2 profile with no code changes and (b) memory across restarts.

Stops the main agent, runs the same code with product/profiles/smr300_stock.yaml, then brings the
main agent back and confirms its site/incident/timeline memory was restored from PostgreSQL.
Evidence: product/evidence/second-profile-<time>.json
"""
import json
import subprocess
import time
import urllib.request
from pathlib import Path

API = 'http://127.0.0.1:8060'
ROOT = Path(__file__).resolve().parents[2]
results = []


def state():
    return json.load(urllib.request.urlopen(API + '/api/state', timeout=10))


def tool(name, **args):
    req = urllib.request.Request(API + '/api/test/tool', data=json.dumps({'name': name, 'args': args}).encode(),
                                 headers={'Content-Type': 'application/json'})
    out = json.load(urllib.request.urlopen(req, timeout=180))
    print(f'   {name} {args} -> {out["status"]}: {out["reason"][:140]}', flush=True)
    return out


def check(name, ok, **evidence):
    results.append({'check': name, 'pass': bool(ok), **evidence})
    print(('PASS ' if ok else 'FAIL ') + name, flush=True)


def stop_agent():
    subprocess.run(['bash', '-c', 'pid=$(pgrep -f "[r]ipple_agent.app" | head -1); [ -n "$pid" ] && kill -TERM $pid; '
                    'for i in $(seq 1 30); do pgrep -f "[r]ipple_agent.app" >/dev/null || exit 0; sleep 1; done'])


def start_agent(*extra):
    subprocess.run(['bash', '-c', f'cd {ROOT} && (setsid nohup bash product/scripts/ripple_agent.sh --test-api {" ".join(extra)} '
                    '> /tmp/ripple-agent.log 2>&1 < /dev/null &)'])
    for _ in range(120):
        try:
            s = state()
            if s['pose']:
                return s
        except Exception:
            pass
        time.sleep(1)
    raise SystemExit('agent did not come up')


def wait_goal(label, timeout=240):
    end = time.time() + timeout
    while time.time() < end:
        g = state()['goal']
        if g and g['label'] == label and g['status'] not in ('SENDING', 'EXECUTING', 'CANCELLING') and 'stopped' in g:
            return g
        time.sleep(1)


before = state()
memory = {'timeline': len(before['timeline']), 'areas': sorted(a['name'] for a in before['areas']),
          'keepouts': sorted(k['id'] for k in before['keepouts'])}
print('main agent memory before restart:', memory, flush=True)

stop_agent()
s = start_agent('--profile', str(ROOT / 'product/profiles/smr300_stock.yaml'), '--no-ambiguous')
check('stock profile: same code starts and observes the robot', s['robot'] == 'smr300_stock' and s['pose'] is not None,
      robot=s['robot'], health=s['health'])
check('stock profile: safety reported as not declared', next(h for h in s['health'] if h['key'] == 'safety')['state'] == 'na')
esc_inc = json.load(urllib.request.urlopen(urllib.request.Request(API + '/api/test/incident', data=b'{}',
                                                                    headers={'Content-Type': 'application/json'}), timeout=30))['incident_id']
esc = tool('escape', incident_id=esc_inc, primitive='spin', amount=0.5, speed=0.1)
check('stock profile: escape disabled without a safety layer', esc['status'] == 'denied' and 'no_safety_layer' in esc['reason'], escape=esc)
tel = tool('teleop', direction='forward', distance_m=0.2, override_safety=True, reason='check override refusal')
check('stock profile: teleop safety override refused', tel['status'] == 'denied', teleop=tel)
ko = tool('add_keepout', region={'box_m': [-3.0, 3.0, -2.5, 3.5]}, reason='check adapter')
check('stock profile: keepouts refused when the profile declares no adapter', ko['status'] == 'denied', keepout=ko)
go = tool('navigate_to', destination='home', reason='stock profile navigation check')
g = wait_goal('Home dock') if go['status'] == 'ok' else None
check('stock profile: command-as-approval navigation arrives verified', g is not None and g['status'] == 'SUCCEEDED' and g['verified'],
      navigate=go, goal=g)

stop_agent()
s = start_agent()
after = {'timeline': len(s['timeline']), 'areas': sorted(a['name'] for a in s['areas']),
         'keepouts': sorted(k['id'] for k in s['keepouts'])}
print('main agent memory after restart:', after, flush=True)
check('memory: site areas restored from PostgreSQL', after['areas'] == memory['areas'], before=memory, after=after)
check('memory: active keepouts restored and re-verified', after['keepouts'] == memory['keepouts'],
      states=[(k['id'], k['state'], k.get('verification')) for k in s['keepouts']])
check('memory: timeline history restored', after['timeline'] >= min(memory['timeline'], 100))

out = ROOT / 'product/evidence' / time.strftime('second-profile-%Y%m%d-%H%M%S.json')
out.write_text(json.dumps({'results': results}, indent=2, default=str))
print(f"{sum(r['pass'] for r in results)}/{len(results)} passed -> {out}")
