#!/usr/bin/env python3
"""Run the real `ripple setup` for a screen recording, answered with real keypresses.

The wizard runs in a pseudo-terminal the size of this terminal and everything it draws is relayed
here, so a recording of this window shows exactly what a person sees. A terminal emulator (pyte)
keeps a copy of the screen; each dialog is recognised from it and answered after a pause long enough
to read it. The answers are the ones a careful person gives: keep the keys already in .env (shown
masked), read the live ROS graph, keep the teleop safety override off.

.env is compared before and after; if the run changed it, the original is written back.
  setup_demo.py --workspace ROBOT_WS --out SITE.json [--dry-run LOG]
Needs pyte (pip install pyte).
"""
import argparse
import fcntl
import json
import os
import pty
import re
import select
import signal
import struct
import sys
import termios
import time
from pathlib import Path
import pyte

ROOT = Path(__file__).resolve().parents[2]
PGDN, TAB, ENTER = b'\x1b[6~', b'\t', b'\r'
# (text on the screen, keys, seconds to read before answering). The first match wins.
ANSWERS = [
    ('Setting up Ripple for the robot', [ENTER], 3.5),
    ('openrouter.ai/keys', [ENTER, ENTER], 3.0),          # the key already in .env, masked: keep it
    ("agent's API token", [ENTER, ENTER], 3.0),            # the Ambiguous token already in .env, masked: keep it
    ('may command the robot', [ENTER], 3.5),               # Yes: these engineers are the operators
    ('live ROS graph gives', [ENTER], 3.0),                # Yes: read the running robot
    ('What Ripple found', [PGDN, PGDN, PGDN, ENTER], 3.0),  # scroll through what the crawler found
    ('Use the robot', [ENTER], 3.0),                       # drift: take the robot's current value
    ('which is right?', [TAB, ENTER], 3.0),                # an uncertain field: keep the best guess
    # The crawler's robot name came from a generic URDF name: replace it, as a person would.
    ('the name is generic', [b'\x7f'] * 8 + [c.encode() for c in 'smr300_01'] + [ENTER, ENTER], 3.0),
    ('Confirm or correct', [ENTER, ENTER], 3.0),
    ('A short name for this robot', [ENTER, ENTER], 2.5),
    ('Is this a simulation', [ENTER], 2.5),                # Yes
    ('On a real robot this is always refused', [TAB, ENTER], 4.0),  # No: the override stays off
    ('named places', [ENTER], 3.5),                        # Yes: use the stations
]
BUTTONS = re.compile(r'<\s*(Ok|OK|Yes|No|Cancel)\s*>')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--workspace', type=Path, required=True)
    ap.add_argument('--out', type=Path, required=True)
    ap.add_argument('--dry-run', type=Path, help='no relay; log each dialog and the answer to this file')
    ap.add_argument('--shown-command', default=None, help='the command line typed on screen')
    a = ap.parse_args()
    dry = a.dry_run
    env_path = ROOT / '.env'
    env_before = env_path.read_bytes() if env_path.exists() else None
    agent = json.loads((ROOT / 'product/config/agent.json').read_text())
    child_env = {**os.environ, 'TERM': 'xterm-256color', 'RIPPLE_WORKSPACE': str(a.workspace),
                 'RIPPLE_ESCALATION_CHANNEL': agent['ambiguous']['escalation_channel_id']}
    cmd = [str(ROOT / 'product/scripts/ripple'), 'setup', '--workspace', str(a.workspace), '--out', str(a.out)]
    log = open(dry, 'w') if dry else None
    if not dry:
        shown = a.shown_command or ' '.join(['ripple', 'setup', '--workspace', str(a.workspace)])
        time.sleep(2.5)  # the window is placed and sized while this waits
        sys.stdout.write('\x1b[2J\x1b[H\x1b[1;32m$\x1b[0m ')
        sys.stdout.flush()
        time.sleep(1.5)
        for ch in shown:
            sys.stdout.write(ch)
            sys.stdout.flush()
            time.sleep(0.045)
        time.sleep(0.8)
        sys.stdout.write('\r\n')
        sys.stdout.flush()
    cols, rows = (120, 34) if dry else os.get_terminal_size()  # read after the window has its final size
    pid, fd = pty.fork()
    if pid == 0:
        os.chdir(a.workspace)
        os.execve(cmd[0], cmd, child_env)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack('HHHH', rows, cols, 0, 0))
    screen = pyte.Screen(cols, rows)
    stream = pyte.ByteStream(screen)
    # Each dialog is its own full-screen application: it enters the alternate screen when it starts and leaves
    # it when it is answered. pyte has no alternate screen, so the screen copy is reset when a dialog starts,
    # and each dialog (one `dialog` number) is answered exactly once. Keys are never sent between dialogs,
    # where they would be read by whatever comes next.
    state = {'last_out': time.time(), 'alive': True, 'dialog': 0, 'open': False}
    ENTER_ALT, LEAVE_ALT = b'\x1b[?1049h', b'\x1b[?1049l'

    def pump(seconds):
        end = time.time() + seconds
        while time.time() < end and state['alive']:
            r, _, _ = select.select([fd], [], [], 0.05)
            if not r:
                continue
            try:
                data = os.read(fd, 65536)
            except OSError:
                data = b''
            if not data:
                state['alive'] = False
                return
            if not dry:
                os.write(sys.stdout.fileno(), data)
            for part in re.split(b'(' + re.escape(ENTER_ALT) + b'|' + re.escape(LEAVE_ALT) + b')', data):
                if part == ENTER_ALT:
                    screen.reset()
                    state['dialog'] += 1
                    state['open'] = True
                elif part == LEAVE_ALT:
                    state['open'] = False
                else:
                    stream.feed(part)
            state['last_out'] = time.time()

    def text():
        return '\n'.join(line.rstrip() for line in screen.display)

    def restore_env(*_):
        env_after = env_path.read_bytes() if env_path.exists() else None
        if env_before is not None and env_after != env_before:
            env_path.write_bytes(env_before)
            env_path.chmod(0o600)
            print('\n(.env was changed by this run and has been restored)', file=sys.stderr)

    signal.signal(signal.SIGTERM, lambda *_: sys.exit(1))
    status = None
    try:
        started, answered = time.time(), 0
        while state['alive'] and time.time() - started < 900:
            pump(0.3)
            if not state['open'] or state['dialog'] == answered or time.time() - state['last_out'] < 0.7:
                continue  # between dialogs, already answered, or still drawing
            snap = text()
            if not BUTTONS.search(snap):
                continue
            rule = next(((needle, keys, wait) for needle, keys, wait in ANSWERS if needle in snap), None)
            if rule is None:  # a dialog not listed above: accept what it proposes
                rule = ('(unlisted)', [ENTER], 2.5)
            needle, keys, wait = rule
            answered = state['dialog']
            if log:
                log.write(f'--- dialog {answered}: {needle}\n' + '\n'.join(l for l in snap.splitlines() if l.strip())[:1500] + '\n')
                log.flush()
            pump(0.2 if dry else wait)
            for key in keys:
                if not state['open'] or state['dialog'] != answered:
                    break  # the dialog closed early (e.g. Enter on a one-button dialog): never type into the next one
                os.write(fd, key)
                pump(0.9 if key == PGDN else 0.12 if len(key) == 1 and key not in (ENTER, TAB) else 0.5)
        pump(3)
        _, status = os.waitpid(pid, 0)
    finally:
        if status is None:
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        restore_env()
        if log:
            log.write('--- final screen\n' + text() + f'\nexit {status if status is None else os.waitstatus_to_exitcode(status)}\n')
            log.close()
    if not dry:
        time.sleep(4)


if __name__ == '__main__':
    main()
