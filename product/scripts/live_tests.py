#!/usr/bin/env python3
"""Live verification against the running simulator and agent (started with --test-api).

Each check prints PASS/FAIL and the whole run is saved to product/evidence/live-tests-<time>.json.
  python3 product/scripts/live_tests.py [--only cancel,refusal,escape,lifecycle,stall]
"""
import argparse
import json
import os
import signal
import subprocess
import time
import urllib.request
from pathlib import Path

API = 'http://127.0.0.1:8060'
ROOT = Path(__file__).resolve().parents[2]
results = []


def state():
    return json.load(urllib.request.urlopen(API + '/api/state', timeout=10))


def post(path, body):
    req = urllib.request.Request(API + path, data=json.dumps(body).encode(), headers={'Content-Type': 'application/json'})
    return json.load(urllib.request.urlopen(req, timeout=120))


def tool(name, **args):
    out = post('/api/test/tool', {'name': name, 'args': args})
    print(f'   {name} {args} -> {out["status"]}: {out["reason"][:140]}', flush=True)
    return out


def incident(cause='unknown'):
    return post('/api/test/incident', {'cause': cause})['incident_id']


def wait(pred, timeout):
    end = time.time() + timeout
    while time.time() < end:
        s = state()
        if pred(s):
            return s
        time.sleep(.5)
    return None


def obstacle(args):
    subprocess.run(['bash', '-c', f'source /opt/ros/humble/setup.bash && python3 {ROOT}/product/scripts/demo_obstacle.py {args}'],
                   capture_output=True)


def check(name, ok, **evidence):
    results.append({'check': name, 'pass': bool(ok), **evidence})
    print(('PASS ' if ok else 'FAIL ') + name, flush=True)


def settle():
    tool('cancel_navigation', reason='test setup')
    wait(lambda s: s['status'] in ('READY', 'HELD'), 30)
    time.sleep(1)


def t_cancel():
    settle()
    go = tool('navigate_to', destination='CHARGE', reason='cancellation test')
    s = wait(lambda s: s['goal'] and s['goal']['status'] == 'EXECUTING' and (s['goal'].get('distance_remaining') or 99) < 30, 20)
    time.sleep(4)
    stop = tool('cancel_navigation', reason='cancellation test')
    s = state()
    check('cancel: owned goal canceled with confirmed stop', go['status'] == 'ok' and stop['status'] == 'ok'
          and s['goal']['status'] == 'CANCELED' and s['goal'].get('stopped'), goal=s['goal'], stop=stop)


def t_refusal():
    settle()
    inc = incident('safety_obstacle')
    obstacle('spawn block --ahead 0.55')
    held = wait(lambda s: 'obstacle' in s['holding'], 15)
    esc = tool('escape', incident_id=inc, primitive='backup', amount=0.2, speed=0.1)
    tel = tool('teleop', direction='forward', distance_m=0.2, reason='refusal test')
    go = tool('navigate_to', destination='Z3', reason='refusal test')
    check('refusal: safety hold observed', held is not None, holding=held and held['holding'])
    check('refusal: escape refused while the safety controller holds', esc['status'] == 'denied' and 'safety_holding' in esc['reason'], escape=esc)
    check('refusal: teleop refused without an explicit override', tel['status'] == 'denied', teleop=tel)
    check('refusal: dispatch refused while held', go['status'] == 'denied' and 'safety_not_holding' in go['reason'], navigate=go)
    over = tool('teleop', direction='backward', distance_m=0.25, override_safety=True, reason='free the robot in simulation')
    obstacle('remove block')
    check('refusal: simulation-only override moves the robot out', over['status'] == 'ok', teleop=over)
    wait(lambda s: not s['holding'], 20)


def t_escape():
    settle()
    inc = incident()
    spin = tool('escape', incident_id=inc, primitive='spin', amount=0.6, speed=0.1)
    back = tool('escape', incident_id=inc, primitive='backup', amount=0.2, speed=0.1)
    third = tool('escape', incident_id=inc, primitive='spin', amount=-0.6, speed=0.1)
    check('escape: Spin moves and is verified by odometry', spin['status'] == 'ok' and spin['verified'], spin=spin)
    check('escape: BackUp moves and is verified by odometry (or is refused for rear clearance)',
          (back['status'] == 'ok' and back['verified']) or 'rear_obstacle' in back['reason'], backup=back)
    check('escape: per-incident budget is enforced', third['status'] == 'denied' and 'budget' in third['reason'], third=third)


def t_lifecycle():
    settle()
    inc = incident('component_down')
    reset = tool('lifecycle_reset', incident_id=inc, node='/planner_server')
    again = tool('lifecycle_reset', incident_id=inc, node='/planner_server')
    check('lifecycle: declared node reset and verified active', reset['status'] == 'ok' and reset['verified'], reset=reset)
    check('lifecycle: budget stops a second reset', again['status'] == 'denied', again=again)
    probe = tool('probe_route', destination='Z3')
    s = wait(lambda s: next(h for h in s['health'] if h['key'] == 'nav2')['state'] == 'ok', 90)
    check('lifecycle: planner answers and Nav2 reports all nodes active after the reset', probe['status'] == 'ok' and s is not None,
          probe=probe, nav2=s and next(h for h in s['health'] if h['key'] == 'nav2'))


def t_stall():
    settle()
    go = tool('navigate_to', destination='CHARGE', reason='stall test')
    wait(lambda s: s['goal'] and s['goal']['status'] == 'EXECUTING', 20)
    time.sleep(3)
    before = {e['id'] for e in state()['timeline']}
    # A higher-priority mux input holds zero velocity: Nav2 keeps commanding, the robot doesn't move.
    holder = subprocess.Popen(['bash', '-c', 'source /opt/ros/humble/setup.bash && exec python3 -c "'
                               'import rclpy,time\nfrom geometry_msgs.msg import Twist\nrclpy.init();n=rclpy.create_node(\'stall_test\')\n'
                               'p=n.create_publisher(Twist,\'/cmd_vel_tracker\',10)\nend=time.time()+30\n'
                               'while time.time()<end:\n p.publish(Twist());time.sleep(.05)"'])
    try:
        s = wait(lambda s: any(e['id'] not in before and e['kind'] in ('incident', 'event') and 'stopped while commanded' in e['text'].lower()
                               for e in s['timeline']), 30)
    finally:
        holder.terminate()
    check('stall: halted-while-commanded detected and an incident opened', s is not None and go['status'] == 'ok',
          timeline=[e for e in (s or state())['timeline'] if e['id'] not in before][:12])
    time.sleep(5)
    settle()


def main():
    tests = {'cancel': t_cancel, 'refusal': t_refusal, 'escape': t_escape, 'lifecycle': t_lifecycle, 'stall': t_stall}
    p = argparse.ArgumentParser()
    p.add_argument('--only', default=','.join(tests))
    a = p.parse_args()
    started = time.strftime('%Y%m%d-%H%M%S')
    for name in a.only.split(','):
        print('==', name, flush=True)
        try:
            tests[name]()
        except Exception as exc:
            check(name + ': ran to completion', False, error=repr(exc))
    out = ROOT / 'product/evidence' / f'live-tests-{started}.json'
    out.write_text(json.dumps({'results': results, 'robot_pose': state()['pose']}, indent=2, default=str))
    print(f"{sum(r['pass'] for r in results)}/{len(results)} passed -> {out}")


if __name__ == '__main__':
    main()
