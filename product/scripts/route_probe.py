#!/usr/bin/env python3
"""Read-only Nav2 planner probe between two map points (no motion).
  python3 route_probe.py --start X Y --goal X Y
Prints path length and every Nth waypoint as JSON."""
import argparse
import json
import math
import time
import rclpy
from action_msgs.msg import GoalStatus
from nav2_msgs.action import ComputePathToPose
from rclpy.action import ActionClient

p = argparse.ArgumentParser()
p.add_argument('--start', nargs=2, type=float, required=True)
p.add_argument('--goal', nargs=2, type=float, required=True)
p.add_argument('--every', type=int, default=10)
a = p.parse_args()
rclpy.init()
node = rclpy.create_node('ripple_route_probe')
client = ActionClient(node, ComputePathToPose, '/compute_path_to_pose')


def wait(future, seconds):
    end = time.monotonic() + seconds
    while not future.done() and time.monotonic() < end:
        rclpy.spin_once(node, timeout_sec=.1)
    if not future.done():
        raise SystemExit('planner timed out')
    return future.result()


if not client.wait_for_server(timeout_sec=10):
    raise SystemExit('planner unavailable')
goal = ComputePathToPose.Goal()
goal.use_start = True
for pose, (x, y) in ((goal.start, a.start), (goal.goal, a.goal)):
    pose.header.frame_id = 'map'
    pose.pose.position.x, pose.pose.position.y = x, y
    pose.pose.orientation.w = 1.0
handle = wait(client.send_goal_async(goal), 10)
result = wait(handle.get_result_async(), 30)
pts = [(round(q.pose.position.x, 2), round(q.pose.position.y, 2)) for q in result.result.path.poses]
length = sum(math.dist(u, v) for u, v in zip(pts, pts[1:]))
print(json.dumps({'ok': result.status == GoalStatus.STATUS_SUCCEEDED and len(pts) > 1, 'length_m': round(length, 2),
                  'points': pts[::a.every] + pts[-1:]}))
node.destroy_node()
rclpy.shutdown()
