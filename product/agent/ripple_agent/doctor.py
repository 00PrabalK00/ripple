"""Check a site configuration (ripple.json) against the live robot, so the config stays correct.

Schema first, then every configured topic, action and service against the running ROS graph,
then drift (the crawler's view of the robot versus the config), then the keys.
  python3 -m ripple_agent.doctor --site ripple.json [--root RIPPLE_CHECKOUT] [--no-live] [--no-keys]
Exit status 1 if any check fails.
"""
import argparse
import json
import os
import shutil
import subprocess
from pathlib import Path


def load_env(path):
    values = {}
    if path.exists():
        for line in path.read_text().splitlines():
            if '=' in line and not line.lstrip().startswith('#'):
                key, value = line.split('=', 1)
                values[key.strip()] = value.strip()
    return values


def openrouter_ok(key):
    import httpx
    if not key:
        return False, 'OPENROUTER_API_KEY is not set'
    for path in ('/api/v1/key', '/api/v1/auth/key'):
        try:
            r = httpx.get('https://openrouter.ai' + path, headers={'Authorization': f'Bearer {key}'}, timeout=15)
        except Exception as exc:
            return False, f'OpenRouter unreachable: {exc}'
        if r.status_code == 200:
            # OpenRouter's label for a key is a masked preview of the key itself: never print it.
            return True, 'OpenRouter accepted the key'
        if r.status_code in (401, 403):
            return False, 'OpenRouter rejected the key'
    return False, f'OpenRouter answered {r.status_code}'


def ambiguous_whoami(root, token=None):
    env = {k: v for k, v in os.environ.items() if k in ('PATH', 'HOME', 'NODE_EXTRA_CA_CERTS', 'AMBI_API_URL')}
    if token:
        env['AMBI_API_TOKEN'] = token
    if not shutil.which('npx', path=env.get('PATH')):
        return None, 'npx is not installed (Node.js is needed for the Ambiguous CLI)'
    try:
        out = subprocess.run(['npx', '--yes', 'ambiguous@0.9.0', 'whoami'], cwd=root, env=env,
                             capture_output=True, text=True, timeout=60)
        data = json.loads(out.stdout)
    except Exception as exc:
        return None, f'Ambiguous CLI failed: {exc}'
    if not data.get('authenticated'):
        return None, 'not authenticated'
    # `user` is a display name (older CLIs sent an object). Never print an email address: this line ends up in
    # terminals, logs and screen recordings.
    user = data.get('user')
    name = str((user.get('name') if isinstance(user, dict) else user) or '')
    return data, 'authenticated as ' + (name if name and '@' not in name else 'the configured Ambiguous identity')


def role_checks(profile):
    """(label, name, kind, needs_publisher) for everything the edge will use."""
    nav, rec = profile['navigation'], profile['recovery']
    out = [('odometry', profile['odometry']['topic'], 'topic', True),
           ('localization', profile['localization']['topic'], 'topic', True),
           ('map', nav['map_topic'], 'topic', True),
           ('motion output', profile['motion_output']['topic'], 'topic', False),
           ('navigate action', nav['navigate_action'], 'action', True),
           ('planner action', nav['planner_action'], 'action', True)]
    out += [(f'scan {k}', v['topic'], 'topic', True) for k, v in profile['scans'].items()]
    out += [(f'motion input {k}', v['topic'], 'topic', False) for k, v in profile['motion_inputs'].items()]
    out += [(f'clear {k} costmap', v, 'service', True) for k, v in nav['clear_services'].items()]
    out += [(f'lifecycle {n}', n + '/get_state', 'service', True) for n in nav['lifecycle_nodes']]
    if nav.get('global_costmap_topic'):
        out.append(('global costmap', nav['global_costmap_topic'], 'topic', True))
    if nav.get('verify_mask'):
        out.append(('keepout mask', nav['verify_mask'], 'topic', True))
    if profile.get('mode'):
        out.append(('control mode', profile['mode']['topic'], 'topic', False))
    if profile['safety'].get('service'):
        out.append(('safety status', profile['safety']['service'], 'service', True))
    if profile.get('localization_refresh_service'):
        out.append(('localization refresh', profile['localization_refresh_service'], 'service', True))
    if rec['escape']['autonomy'] != 'off':
        out += [('escape backup', rec['escape']['backup_action'], 'action', True), ('escape spin', rec['escape']['spin_action'], 'action', True)]
    return out


def run(site, root, live=True, keys=True):
    from ripple_edge.contracts import Profile
    results = []

    def add(ok, name, detail):
        results.append((ok, name, detail))

    data = json.loads(Path(site).read_text())
    profile = data.get('profile', data)
    try:
        Profile.model_validate(profile)
        add(True, 'schema', f"profile for {profile['robot']} is valid")
    except Exception as exc:
        add(False, 'schema', str(exc).splitlines()[0])
        return results
    if live:
        from ripple_edge.crawl import live_scan, propose
        try:
            scan = live_scan()
        except Exception as exc:
            add(False, 'ROS graph', f'could not read the live graph: {exc}')
            scan = None
        if scan:
            add(True, 'ROS graph', f"{len(scan['nodes'])} nodes, {len(scan['topics'])} topics")
            for label, name, kind, needs_pub in role_checks(profile):
                if kind == 'topic':
                    t = scan['topics'].get(name)
                    ok = t is not None and (bool(t['publishers']) or not needs_pub)
                    detail = 'missing from the graph' if t is None else (f"{len(t['publishers'])} publisher(s)" if t['publishers'] else 'no publisher')
                elif kind == 'action':
                    a = scan['actions'].get(name)
                    ok, detail = bool(a and a['servers']), ('served by ' + ', '.join(a['servers'])) if a and a['servers'] else 'no action server'
                else:
                    ok, detail = name in scan['services'], 'available' if name in scan['services'] else 'service missing'
                add(ok, f'{label} {name}', detail)
            for d in propose({}, scan, profile).drift:
                add(False, f"drift {d['field']}", f"configured {d['configured']} but the robot has {d['observed']}")
    if keys:
        env = {**load_env(Path(root) / '.env'), **{k: v for k, v in os.environ.items() if k.startswith(('OPENROUTER', 'AMBI'))}}
        ok, detail = openrouter_ok(env.get('OPENROUTER_API_KEY'))
        add(ok, 'OpenRouter key', detail)
        amb = (data.get('agent') or {}).get('ambiguous')
        if amb:
            who, detail = ambiguous_whoami(root, env.get('AMBI_API_TOKEN'))
            ok = bool(who) and who.get('userId') == amb.get('user_id') and who.get('workspaceId') == amb.get('workspace_id')
            add(ok, 'Ambiguous identity', detail if ok or not who else 'signed in as a different user or workspace than ripple.json names')
        else:
            add(None, 'Ambiguous', 'not configured (dashboard only)')
    binary = os.environ.get('RIPPLE_ROSSCOPE_BIN') or next((str(p) for p in (Path(root) / 'build/rosscope-observe',
                                                                              Path.home() / '.ripple/bin/rosscope-observe',
                                                                              Path.home() / 'Ripple/build/rosscope-observe') if p.exists()), None)
    add(True if binary else None, 'RosScope', binary or 'not installed (optional: deeper process and log evidence)')
    return results


def print_results(results):
    for ok, name, detail in results:
        mark = '✓' if ok else ('·' if ok is None else '✗')
        print(f'{mark} {name:48s} {detail}')
    failed = sum(1 for ok, _, _ in results if ok is False)
    print(f"\n{'All checks passed.' if not failed else f'{failed} check(s) failed.'}")
    return failed


def main():
    ap = argparse.ArgumentParser(description='Check ripple.json against the live robot and the keys.')
    ap.add_argument('--site', type=Path, required=True)
    ap.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[3])
    ap.add_argument('--no-live', action='store_true')
    ap.add_argument('--no-keys', action='store_true')
    a = ap.parse_args()
    raise SystemExit(1 if print_results(run(a.site, a.root, not a.no_live, not a.no_keys)) else 0)


if __name__ == '__main__':
    main()
