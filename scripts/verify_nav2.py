"""Live simulated Nav2 acceptance check. Sends one goal through approval guards."""
import argparse
import json
import queue
import time
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import rclpy
from rclpy.parameter import Parameter
from ripple.robot_adapter import Nav2Adapter
from ripple.contracts import Target
from ripple.store import Store
from ripple.supervisor import Supervisor


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--complete', action='store_true')
    parser.add_argument('--x', type=float, default=3.61)
    parser.add_argument('--y', type=float, default=0.47)
    args = parser.parse_args()
    rclpy.init()
    events = queue.Queue()
    adapter = Nav2Adapter(events)
    adapter.set_parameters([Parameter('use_sim_time', value=True)])
    store = Store()
    supervisor = Supervisor(adapter, store)
    supervisor.observe('station.A.available', True, 'explicit simulator integration fixture')
    target = Target('A', 'map', args.x, args.y, 0.0)
    started = time.monotonic()
    sent_at = None
    moved = False
    try:
        while time.monotonic() - started < (120 if args.complete else 45):
            rclpy.spin_once(adapter, timeout_sec=0.05)
            while not events.empty():
                event = events.get_nowait()
                data = event.data
                if event.kind == 'odometry':
                    supervisor.observe('robot.odom_fresh', True, 'odometry', 1)
                    supervisor.odometry(data['linear_speed'], data['angular_speed'], data['received_at'])
                    moved |= abs(data['linear_speed']) > 0.03
                elif event.kind == 'localization':
                    supervisor.observe('robot.localized', True, 'AMCL', 5)
                elif event.kind == 'safety':
                    supervisor.observe('robot.safety_clear', data['clear'], 'safety service', 1)
                    if data['clear']:
                        supervisor.observe('robot.mode', 'zones', 'verified safety service', 1)
                elif event.kind == 'accepted':
                    supervisor.accepted(event.goal_id)
                elif event.kind == 'terminal':
                    supervisor.terminal(event.goal_id, data['outcome'], data['final_pose'])
                elif event.kind == 'rejected':
                    supervisor.terminal(event.goal_id, 'REJECTED')
                elif event.kind == 'uncertain':
                    raise RuntimeError(data['reason'])
            if sent_at is None and adapter.client.server_is_ready() and all(
                    key in supervisor.facts for key in ('robot.mode', 'robot.safety_clear',
                                                        'robot.localized', 'robot.odom_fresh')):
                p = supervisor.propose(target, 'Explicit live simulator acceptance check')
                supervisor.approve(p.id, 'integration check requested by operator')
                if supervisor.dispatch(p.id, target):
                    sent_at = time.monotonic()
            if sent_at and not args.complete and time.monotonic() - sent_at > 3 and supervisor.state == 'EXECUTING':
                supervisor.cancel('integration cancellation check')
            if sent_at and supervisor.goal_id is None:
                receipts = store.receipts()
                result = next(r for r in receipts if r['kind'] == 'goal_terminal')
                expected = 'SUCCEEDED' if args.complete else 'CANCELED'
                print(json.dumps({'expected': expected, 'moved': moved, 'receipts': receipts}, indent=2))
                return 0 if result['outcome'] == expected and moved else 1
        print(json.dumps({'error': 'verification timeout', 'state': supervisor.state,
                          'receipts': store.receipts()}, indent=2))
        return 1
    finally:
        if supervisor.goal_id in adapter.handles:
            adapter.cancel(supervisor.goal_id)
            end = time.monotonic() + 5
            while time.monotonic() < end and adapter.handles:
                rclpy.spin_once(adapter, timeout_sec=0.1)
        adapter.destroy_node()
        rclpy.shutdown()
        store.close()


if __name__ == '__main__':
    raise SystemExit(main())
