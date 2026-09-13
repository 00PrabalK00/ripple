#!/usr/bin/env python3
"""Drive the setup wizard's dialogs in a pseudo-terminal with real keypresses (the dialogs need a TTY).

Checks that each kind of prompt draws and returns what a person chose: a pre-filled answer, the
scrolling report, a preselected choice and a yes/no. Exits non-zero on any mismatch.
  PYTHONPATH=product/agent:product/ripple_edge product/.venv/bin/python product/scripts/tui_smoke.py
"""
import json
import os
import pty
import select
import sys
import tempfile
import time

out = os.path.join(tempfile.mkdtemp(prefix='ripple-tui-'), 'result.json')
child = f"""
import json
from ripple_agent.setup import Dialogs
d = Dialogs()
r1 = d.ask('OpenRouter key', 'Paste your key', 'sk-prefilled', password=True)
d.info('What Ripple found', '\\n'.join(f'field {{i}}' for i in range(40)))
r2 = d.choose('odometry', 'which is right?', [('a', 'A'), ('b', 'B')], 'b')
r3 = d.confirm('Simulation', 'Is this a simulation?')
open({out!r}, 'w').write(json.dumps([r1, r2, r3]))
"""
# The keys a person presses, dialog by dialog.
keys = [b'\r', b'\r',   # ask: accept the pre-filled text, then OK
        b'\r',          # info: Enter continues
        b'\t', b'\r',   # choose: move to Ok and press it; the default stays selected
        b'\r']          # confirm: Yes has focus

pid, fd = pty.fork()
if pid == 0:
    os.environ['TERM'] = 'xterm'
    os.execv(sys.executable, [sys.executable, '-c', child])
screen = b''


def pump(seconds):
    global screen
    end = time.time() + seconds
    while time.time() < end:
        ready, _, _ = select.select([fd], [], [], 0.1)
        if ready:
            try:
                screen += os.read(fd, 65536)
            except OSError:
                return


pump(2.0)
for key in keys:
    os.write(fd, key)
    pump(1.2)
pump(2.0)
os.waitpid(pid, 0)
result = json.loads(open(out).read()) if os.path.exists(out) else None
text = screen.decode(errors='replace')
drawn = all(t in text for t in ('OpenRouter key', 'What Ripple found', 'Enter continues', 'odometry', 'Simulation'))
ok = result == ['sk-prefilled', 'b', True] and drawn and 'Traceback' not in text
print('PASS' if ok else 'FAIL', 'dialogs returned', result, '| all dialogs drawn' if drawn else '| a dialog did not draw')
raise SystemExit(0 if ok else 1)
