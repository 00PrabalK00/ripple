"""`ripple setup`: guided setup for one robot, in the terminal.

  1. Keys: the OpenRouter key (checked live), and the database URL (created when missing).
  2. Ambiguous: an API token (checked with whoami), the escalation channel and the operators.
  3. The robot: the crawler proposes a profile from the workspace and the live ROS graph; only
     uncertain fields are put to the person; the teleop safety override stays off unless they
     enable it on a declared simulation.
  4. RosScope: found, or cloned and built (optional).
  5. ripple.json is written into the robot workspace (validated first; the old one is kept as .bak),
     secrets go to .env in the Ripple checkout (mode 600), and `ripple doctor` checks the result.

Interactive by default (full-screen dialogs). `--non-interactive` takes every answer from flags
and accepts the crawler's proposal, for scripted installs and tests.
"""
import argparse
import json
import os
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

ROSSCOPE_REPO = 'https://github.com/00PrabalK00/RosScope.git'
TITLE = 'Ripple setup'


class Dialogs:
    """Full-screen prompts (prompt_toolkit)."""
    def __init__(self):
        from prompt_toolkit import shortcuts
        self.s = shortcuts

    def info(self, title, text):
        if text.count('\n') < 18:
            self.s.message_dialog(title=title, text=text).run()
            return
        # Long text (the crawler's report) scrolls instead of running off the screen:
        # arrows and PgUp/PgDn scroll, Enter or Esc continues, the mouse wheel works too.
        from prompt_toolkit.application import Application
        from prompt_toolkit.application.current import get_app
        from prompt_toolkit.key_binding import KeyBindings, merge_key_bindings
        from prompt_toolkit.key_binding.defaults import load_key_bindings
        from prompt_toolkit.layout import Layout
        from prompt_toolkit.layout.dimension import Dimension
        from prompt_toolkit.widgets import Button, Dialog, TextArea
        area = TextArea(text=text, read_only=True, scrollbar=True, height=Dimension(preferred=24))
        dialog = Dialog(title=f'{title}  (↑↓ PgUp/PgDn scroll · Enter continues)', body=area,
                        buttons=[Button(text='Ok', handler=lambda: get_app().exit())], with_background=True)
        keys = KeyBindings()
        for key in ('enter', 'escape'):
            keys.add(key)(lambda event: event.app.exit())
        Application(layout=Layout(dialog, focused_element=area), key_bindings=merge_key_bindings([load_key_bindings(), keys]),
                    mouse_support=True, full_screen=True).run()

    def ask(self, title, text, default='', password=False):
        try:
            app = self.s.input_dialog(title=title, text=text, default=default, password=password)
        except TypeError:  # prompt_toolkit before 3.0.37 has no default: pre-fill the focused text field
            app = self.s.input_dialog(title=title, text=text, password=password)
            if default and app.layout.current_buffer is not None:
                app.layout.current_buffer.text = default
                app.layout.current_buffer.cursor_position = len(default)
        return app.run()

    def confirm(self, title, text, default=True):
        return self.s.yes_no_dialog(title=title, text=text).run()

    def choose(self, title, text, options, default=None):
        return self.s.radiolist_dialog(title=title, text=text, values=options, default=default).run()


class Scripted:
    """Answers from flags; anything not given takes the default."""
    def __init__(self, answers):
        self.answers = answers

    def info(self, title, text):
        print(f'== {title}\n{text}\n')

    def ask(self, title, text, default='', password=False):
        return self.answers.get(title, default)

    def confirm(self, title, text, default=True):
        return self.answers.get(title, default)

    def choose(self, title, text, options, default=None):
        return self.answers.get(title, default if default is not None else (options[0][0] if options else None))


def read_env(path):
    values = {}
    if path.exists():
        for line in path.read_text().splitlines():
            if '=' in line and not line.lstrip().startswith('#'):
                key, value = line.split('=', 1)
                values[key.strip()] = value.strip()
    return values


def write_env(path, updates):
    """Update keys in place, keep everything else, and keep the file private."""
    lines = path.read_text().splitlines() if path.exists() else []
    done = set()
    for i, line in enumerate(lines):
        key = line.split('=', 1)[0].strip()
        if '=' in line and not line.lstrip().startswith('#') and key in updates:
            lines[i] = f'{key}={updates[key]}'
            done.add(key)
    lines += [f'{k}={v}' for k, v in updates.items() if k not in done]
    path.write_text('\n'.join(lines) + '\n')
    path.chmod(0o600)


def step_keys(ui, root, env, args):
    from .doctor import openrouter_ok
    key = args.openrouter_key or env.get('OPENROUTER_API_KEY', '')
    while True:
        if not key or not isinstance(ui, Scripted):
            key = ui.ask('OpenRouter key', 'Ripple reasons with GLM through OpenRouter. Paste your OpenRouter API key\n'
                         '(https://openrouter.ai/keys). It is stored only in .env on this machine.', key, password=True) or ''
        if not key:
            ui.info('OpenRouter key', 'No key: Ripple can observe the robot but cannot reason about incidents. '
                    'Run `ripple setup` again to add one.')
            return {}
        ok, detail = (True, 'not checked (--no-verify)') if args.no_verify else openrouter_ok(key)
        if ok:
            return {'OPENROUTER_API_KEY': key}
        if isinstance(ui, Scripted) or not ui.confirm('OpenRouter key', f'{detail}. Try another key?'):
            raise SystemExit(f'OpenRouter key rejected: {detail}')
        key = ''


def step_database(ui, root, env, args):
    if env.get('DATABASE_URL'):
        return {}
    password = env.get('RIPPLE_DB_PASSWORD') or secrets.token_urlsafe(18)
    updates = {'RIPPLE_DB_PASSWORD': password, 'DATABASE_URL': f'postgresql://ripple:{password}@127.0.0.1:5432/ripple'}
    if shutil.which('docker') and (root / 'compose.yaml').exists() and ui.confirm(
            'Database', 'Ripple keeps its site memory and action journal in PostgreSQL.\nStart it now with Docker?', True):
        run_env = {**os.environ, **updates}
        subprocess.run(['docker', 'compose', 'up', '-d', '--wait', 'postgres'], cwd=root, env=run_env, check=False)
        if (root / 'package.json').exists() and shutil.which('npm'):
            subprocess.run(['npm', 'run', 'db:migrate'], cwd=root, env=run_env, check=False)
    return updates


def ambiguous(root, token, *command):
    env = {k: v for k, v in os.environ.items() if k in ('PATH', 'HOME', 'NODE_EXTRA_CA_CERTS', 'AMBI_API_URL')}
    if token:
        env['AMBI_API_TOKEN'] = token
    out = subprocess.run(['npx', '--yes', 'ambiguous@0.9.0', *command], cwd=root, env=env,
                         capture_output=True, text=True, timeout=90)
    return json.loads(out.stdout)


def step_ambiguous(ui, root, env, args):
    """Returns (env updates, agent config)."""
    local = [{'id': 'local-dashboard', 'name': 'Local operator', 'via': 'dashboard'}]
    if args.skip_ambiguous or not shutil.which('npx'):
        if not args.skip_ambiguous:
            ui.info('Ambiguous', 'Node.js (npx) is not installed, so Ambiguous is skipped: operators use the local dashboard.')
        return {}, {'operators': local}
    token = args.ambiguous_token or env.get('AMBI_API_TOKEN', '')
    if not isinstance(ui, Scripted):
        token = ui.ask('Ambiguous', 'Ripple talks to engineers through Ambiguous (https://app.ambiguous.ai).\n'
                       "Paste the Ripple agent's API token, or leave it empty to use the identity already\n"
                       'signed in on this machine (`npx ambiguous auth login`).', token, password=True) or ''
    try:
        who = ambiguous(root, token, 'whoami')
    except Exception as exc:
        ui.info('Ambiguous', f'The Ambiguous CLI failed ({exc}); operators use the local dashboard for now.')
        return {}, {'operators': local}
    if not who.get('authenticated'):
        ui.info('Ambiguous', 'Not signed in to Ambiguous; operators use the local dashboard for now.')
        return {}, {'operators': local}
    me = who['userId']
    channels = ambiguous(root, token, 'chat', 'channels', 'list')
    channels = channels if isinstance(channels, list) else channels.get('channels') or channels.get('data') or []
    options = [(c['id'], f"{c.get('name') or 'direct message'} ({c.get('type')}, {c.get('member_count', '?')} members)")
               for c in channels if c.get('id')]
    if not options:
        ui.info('Ambiguous', 'This identity is in no chat channels yet. Start a direct message with the robot, then run setup again.')
        return ({'AMBI_API_TOKEN': token} if token else {}), {'operators': local}
    dms = [o for o in options if '(dm,' in o[1]]
    channel = args.escalation_channel or ui.choose('Escalation channel', 'Where should Ripple ask for help?',
                                                   options, (dms or options)[0][0])
    members = ambiguous(root, token, 'chat', 'channels', 'get', channel).get('members') or []
    people = [m for m in members if (m.get('user_id') or m.get('id')) != me and m.get('type', 'human') != 'agent']
    operators = [{'id': m.get('user_id') or m.get('id'), 'name': m.get('name') or m.get('display_name') or 'Operator', 'via': 'ambiguous'}
                 for m in people]
    names = ', '.join(o['name'] for o in operators) or 'nobody'
    if not ui.confirm('Operators', f'These people may command the robot through Ambiguous: {names}.\n'
                      'Their messages are the approval for what they ask. Allow them?', True):
        operators = []
    agent = {'ambiguous': {'user_id': me, 'workspace_id': who['workspaceId'], 'escalation_channel_id': channel},
             'operators': operators + local,
             'authorization_source': 'Operators are site engineers named by the robot owner in `ripple setup`. '
                                     'Their messages are the approval for the actions they ask for.'}
    return ({'AMBI_API_TOKEN': token} if token else {}), agent


def step_robot(ui, workspace, site_path, args):
    from ripple_edge.crawl import live_scan, propose, report, static_scan
    existing = None
    if site_path.exists():
        existing = json.loads(site_path.read_text()).get('profile')
    live = None
    if args.live is not False and (args.live or ui.confirm('Robot discovery', 'Is the robot (or its simulation) running now?\n'
                                                           'Reading the live ROS graph gives the most accurate configuration.', True)):
        try:
            live = live_scan()
        except Exception as exc:
            ui.info('Robot discovery', f'Could not read the live ROS graph ({exc}); using the workspace files only.')
    p = propose(static_scan(workspace), live, existing)
    ui.info('What Ripple found', report(p) + '\n\n✓ confirmed   ? best guess   ! default')
    for d in p.drift:
        if ui.confirm('Configuration drift', f"{d['field']} is {d['configured']} in ripple.json,\nbut the robot now has "
                      f"{d['observed']}. Use the robot's value?", True):
            p.set(d['field'], d['observed'], 'high', 'confirmed in setup (was drift)')
    for path, finding in sorted(p.uncertain().items()):
        if path in ('stations', 'simulation', 'safety.never_touch', 'motion_inputs') or not isinstance(finding.value, (str, type(None))):
            continue
        choices = [v for v in [finding.value, *finding.alternatives] if isinstance(v, str)]
        if len(choices) > 1:
            value = ui.choose(path, f'{path}: which is right?  ({finding.source})', [(c, c) for c in choices], finding.value)
        else:
            value = ui.ask(path, f'{path}\n{finding.source}\nConfirm or correct (empty for none):', finding.value or '')
        p.set(path, value or None, 'high', 'confirmed in setup')
    name = args.robot_name or ui.ask('Robot name', 'A short name for this robot (used in messages and memory):', p.value('robot'))
    p.set('robot', name, 'high', 'named in setup')
    simulation = ui.confirm('Simulation', 'Is this a simulation (Gazebo, Isaac, Webots)?', bool(p.value('simulation')))
    p.set('simulation', bool(simulation), 'high', 'confirmed in setup')
    profile = p.profile()
    if simulation and profile.get('teleop') and profile['safety']['required'] and (args.allow_override or ui.confirm(
            'Teleop override', 'In simulation only, may Ripple move the robot while the safety controller holds it\n'
            '(still stopping inside the minimum clearance)? On a real robot this is always refused.', False)):
        profile['teleop']['override_safety'] = True
    stations = profile['stations']
    if stations and not ui.confirm('Stations', f"Found {len(stations)} named places: {', '.join(stations)}.\nUse them as destinations?", True):
        profile['stations'] = {}
    return profile, p


def find_rosscope(root):
    for path in (os.environ.get('RIPPLE_ROSSCOPE_BIN'), root / 'build/rosscope-observe', Path.home() / '.ripple/bin/rosscope-observe',
                 Path.home() / 'Ripple/build/rosscope-observe'):
        if path and Path(path).is_file() and os.access(path, os.X_OK):
            return Path(path)
    return None


def step_rosscope(ui, root, args):
    found = find_rosscope(root)
    if found or args.skip_rosscope:
        return found
    if not ui.confirm('RosScope', 'RosScope adds deeper evidence (processes, CPU, logs) to incident reports.\n'
                      'Download and build it now? It needs git, g++ and Qt6Core (qt6-base-dev).', True):
        return None
    source = Path(os.environ.get('ROSSCOPE_SOURCE', Path.home() / '.ripple/RosScope'))
    if not source.exists():
        subprocess.run(['git', 'clone', '--depth', '1', ROSSCOPE_REPO, str(source)], check=False)
    qt = subprocess.run(['pkg-config', '--exists', 'Qt6Core']).returncode == 0 or (source / '.deps/usr').exists()
    if not qt:
        ui.info('RosScope', 'Qt6Core is missing: install it with `sudo apt install qt6-base-dev`, then run setup again.\n'
                'Ripple works without RosScope.')
        return None
    build = subprocess.run(['bash', str(root / 'scripts/build_rosscope_bridge.sh')], cwd=root,
                           env={**os.environ, 'ROSSCOPE_SOURCE': str(source)}, capture_output=True, text=True)
    if build.returncode == 0 and (root / 'build/rosscope-observe').exists():
        return root / 'build/rosscope-observe'
    ui.info('RosScope', 'The build failed; Ripple works without RosScope.\n' + build.stderr[-600:])
    return None


def main():
    ap = argparse.ArgumentParser(description='Guided setup for one robot.')
    ap.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[3], help='Ripple checkout')
    ap.add_argument('--workspace', type=Path, default=Path.cwd(), help="the robot's workspace (ripple.json goes here)")
    ap.add_argument('--out', type=Path, help='where to write ripple.json (default: WORKSPACE/ripple.json)')
    ap.add_argument('--non-interactive', action='store_true')
    ap.add_argument('--openrouter-key')
    ap.add_argument('--ambiguous-token')
    ap.add_argument('--escalation-channel')
    ap.add_argument('--skip-ambiguous', action='store_true')
    ap.add_argument('--robot-name')
    live = ap.add_mutually_exclusive_group()
    live.add_argument('--live', dest='live', action='store_true', default=None)
    live.add_argument('--no-live', dest='live', action='store_false')
    ap.add_argument('--allow-override', action='store_true', help='simulation only: let teleop override the safety hold')
    ap.add_argument('--skip-rosscope', action='store_true')
    ap.add_argument('--no-verify', action='store_true', help='skip network checks of keys (tests)')
    ap.add_argument('--no-doctor', action='store_true')
    args = ap.parse_args()
    from ripple_edge.contracts import Profile
    from .doctor import print_results, run as doctor
    root, workspace = args.root.resolve(), args.workspace.expanduser().resolve()
    site = (args.out or workspace / 'ripple.json').resolve()
    interactive = not args.non_interactive and sys.stdin.isatty()
    ui = Dialogs() if interactive else Scripted({})
    ui.info(TITLE, f'Setting up Ripple for the robot in\n  {workspace}\n\nSecrets go to {root / ".env"} (private to you);\n'
            f'the robot configuration goes to {site}.')
    env_path = root / '.env'
    env = read_env(env_path)
    updates = {**step_keys(ui, root, env, args), **step_database(ui, root, env, args)}
    amb_env, agent = step_ambiguous(ui, root, {**env, **updates}, args)
    updates.update(amb_env)
    profile, proposal = step_robot(ui, workspace, site, args)
    Profile.model_validate(profile)  # never write a configuration the edge would refuse
    rosscope = step_rosscope(ui, root, args)
    if updates:
        write_env(env_path, updates)
    site.parent.mkdir(parents=True, exist_ok=True)
    if site.exists():
        shutil.copy2(site, site.with_suffix('.json.bak'))
    evidence = {path: {'confidence': f.confidence, 'source': f.source} for path, f in proposal.fields.items()}
    site.write_text(json.dumps({'schema': 1, 'profile': profile, 'agent': agent,
                                'setup': {'workspace': str(workspace), 'rosscope': str(rosscope) if rosscope else None,
                                          'evidence': evidence}}, indent=2) + '\n')
    print(f'Wrote {site}')
    if not args.no_doctor:
        failed = print_results(doctor(site, root, live=args.live is not False, keys=not args.no_verify))
        if failed:
            print('Fix the failed checks (edit ripple.json or rerun `ripple setup`), then run `ripple doctor --site`', site)
    print(f'\nStart Ripple for this robot:\n  ripple run --site {site}')


if __name__ == '__main__':
    main()
