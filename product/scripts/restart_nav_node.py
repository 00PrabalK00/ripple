#!/usr/bin/env python3
"""Operator/dev tool: restart one hung Nav2 lifecycle node in place and bring it back to active.

Not an agent tool — Ripple deliberately cannot restart processes. Reuses the node's original
command line, then configures and activates it through its own lifecycle services.
  python3 restart_nav_node.py controller_server
"""
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import rclpy
from lifecycle_msgs.msg import Transition
from lifecycle_msgs.srv import ChangeState, GetState

name = sys.argv[1] if len(sys.argv) > 1 else 'controller_server'
pids = subprocess.run(['pgrep', '-f', f'__node:={name}( |$)'], capture_output=True, text=True).stdout.split()
if len(pids) != 1:
    raise SystemExit(f'expected one {name} process, found {pids}')
pid = int(pids[0])
argv = Path(f'/proc/{pid}/cmdline').read_bytes().rstrip(b'\0').split(b'\0')
env = dict(os.environ)
env.update({k.decode(): v.decode() for k, v in (line.split(b'=', 1) for line in
            Path(f'/proc/{pid}/environ').read_bytes().split(b'\0') if b'=' in line)})
os.kill(pid, signal.SIGINT)
end = time.monotonic() + 5
while Path(f'/proc/{pid}').exists() and time.monotonic() < end:
    time.sleep(.1)
if Path(f'/proc/{pid}').exists():
    os.kill(pid, signal.SIGKILL)
    time.sleep(.5)
log = open('/tmp/ripple-navigation.log', 'ab')
subprocess.Popen([a.decode() for a in argv], env=env, stdout=log, stderr=log, stdin=subprocess.DEVNULL, start_new_session=True)
print(f'restarted {name} (old pid {pid})', flush=True)

rclpy.init()
node = rclpy.create_node('ripple_node_restart')
get = node.create_client(GetState, f'/{name}/get_state')
change = node.create_client(ChangeState, f'/{name}/change_state')
if not (get.wait_for_service(timeout_sec=30) and change.wait_for_service(timeout_sec=30)):
    raise SystemExit('lifecycle services did not come up')


def call(client, request):
    future = client.call_async(request)
    rclpy.spin_until_future_complete(node, future, timeout_sec=30)
    if not future.done():
        raise SystemExit('lifecycle call timed out')
    return future.result()


for transition in (Transition.TRANSITION_CONFIGURE, Transition.TRANSITION_ACTIVATE):
    request = ChangeState.Request()
    request.transition.id = transition
    if not call(change, request).success:
        raise SystemExit(f'transition {transition} refused')
state = call(get, GetState.Request()).current_state.label
print(f'{name} is {state}')
node.destroy_node()
rclpy.shutdown()
sys.exit(0 if state == 'active' else 1)
