#!/usr/bin/env python3
"""Place or remove demo obstacles in the running Gazebo world.

Source ROS first:   source /opt/ros/humble/setup.bash

  python3 scripts/demo_obstacle.py spawn pallet --station A     # parked on Packing A
  python3 scripts/demo_obstacle.py spawn crate --at 1.5 0.7     # map coordinates, metres
  python3 scripts/demo_obstacle.py spawn block --ahead 0.60     # 0.60 m in front of the robot's centre
  python3 scripts/demo_obstacle.py remove pallet

Coordinates are map-frame metres. In this simulator the map frame matches the
Gazebo world frame: the robot spawns at world (0, 0) and AMCL starts at map (0, 0).
Entities are named ripple_demo_<name>, so this script can't delete warehouse models.
"""
import argparse
import json
import math
from pathlib import Path
import time

import rclpy
import yaml
from gazebo_msgs.srv import DeleteEntity, SpawnEntity
from geometry_msgs.msg import Pose, PoseWithCovarianceStamped
from rclpy.qos import DurabilityPolicy, HistoryPolicy, QoSProfile, ReliabilityPolicy

ROOT = Path(__file__).resolve().parents[1]
PREFIX = 'ripple_demo_'
ROBOT_RADIUS = math.hypot(0.40, 0.25)  # circumscribed half-footprint used by the runtime

SDF = """<?xml version="1.0"?>
<sdf version="1.6">
  <model name="{name}">
    <static>true</static>
    <link name="body">
      <collision name="collision"><geometry><box><size>{l} {w} {h}</size></box></geometry></collision>
      <visual name="visual">
        <geometry><box><size>{l} {w} {h}</size></box></geometry>
        <material><ambient>0.85 0.45 0.1 1</ambient><diffuse>0.85 0.45 0.1 1</diffuse></material>
      </visual>
    </link>
  </model>
</sdf>"""


def entity(name):
    return name if name.startswith(PREFIX) else PREFIX + name


def station_xy(station):
    registry = json.loads((ROOT / 'config/stations.json').read_text())
    zones = yaml.safe_load((ROOT / 'smr300l_gazebo_ros2control/zones.yaml').read_text())['zones']
    key = station.upper()
    if key not in registry:
        raise SystemExit(f'Unknown station "{station}". Registered: {", ".join(registry)}')
    p = zones[registry[key]['zone']]['position']
    return p['x'], p['y']


def robot_pose(node, timeout=3.0):
    qos = QoSProfile(reliability=ReliabilityPolicy.RELIABLE, durability=DurabilityPolicy.TRANSIENT_LOCAL,
                     history=HistoryPolicy.KEEP_LAST, depth=1)
    got = {}
    sub = node.create_subscription(PoseWithCovarianceStamped, '/amcl_pose',
                                   lambda msg: got.setdefault('pose', msg.pose.pose), qos)
    end = time.monotonic() + timeout
    while 'pose' not in got and time.monotonic() < end:
        rclpy.spin_once(node, timeout_sec=0.1)
    node.destroy_subscription(sub)
    if 'pose' not in got:
        return None
    p = got['pose']
    return p.position.x, p.position.y, 2 * math.atan2(p.orientation.z, p.orientation.w)


def gap_to_robot(robot, x, y, yaw, length, width):
    """Distance from the robot centre to the nearest point of the box."""
    dx, dy = robot[0] - x, robot[1] - y
    lx = dx * math.cos(-yaw) - dy * math.sin(-yaw)
    ly = dx * math.sin(-yaw) + dy * math.cos(-yaw)
    return math.hypot(max(abs(lx) - length / 2, 0), max(abs(ly) - width / 2, 0))


def call(node, kind, service, request, timeout=10.0):
    client = node.create_client(kind, service)
    if not client.wait_for_service(timeout_sec=timeout):
        raise SystemExit(f'{service} is unavailable. Is Gazebo running with libgazebo_ros_factory.so?')
    future = client.call_async(request)
    rclpy.spin_until_future_complete(node, future, timeout_sec=timeout)
    if not future.done():
        raise SystemExit(f'{service} timed out')
    return future.result()


def spawn(node, args):
    robot = robot_pose(node)
    if args.ahead is not None:
        if robot is None:
            raise SystemExit('No /amcl_pose received; --ahead needs a localized robot.')
        length, width, height = args.size or (0.4, 1.2, 1.0)
        yaw = robot[2]
        reach = args.ahead + length / 2
        x, y = robot[0] + reach * math.cos(yaw), robot[1] + reach * math.sin(yaw)
    else:
        x, y = station_xy(args.station) if args.station else args.at
        length, width, height = args.size or (1.2, 0.8, 1.0)
        yaw = math.radians(args.yaw)
    if robot is not None:
        gap = gap_to_robot(robot, x, y, yaw, length, width)
        if gap < ROBOT_RADIUS + 0.05 and not args.force:
            raise SystemExit(f'Refusing: the box would sit {gap:.2f} m from the robot centre and overlap it. '
                             'Move it, or pass --force.')
    pose = Pose()
    pose.position.x, pose.position.y, pose.position.z = x, y, height / 2
    pose.orientation.z, pose.orientation.w = math.sin(yaw / 2), math.cos(yaw / 2)
    name = entity(args.name)
    request = SpawnEntity.Request(name=name, xml=SDF.format(name=name, l=length, w=width, h=height),
                                  initial_pose=pose, reference_frame='world')
    result = call(node, SpawnEntity, '/spawn_entity', request)
    if not result.success:
        raise SystemExit(f'Spawn failed: {result.status_message}')
    where = f'robot at ({robot[0]:.2f}, {robot[1]:.2f}), {gap_to_robot(robot, x, y, yaw, length, width):.2f} m away' \
        if robot else 'robot pose unknown'
    print(f'Placed {name} ({length:.2f} × {width:.2f} × {height:.2f} m) at map ({x:.2f}, {y:.2f}); {where}.')


def remove(node, args):
    name = entity(args.name)
    result = call(node, DeleteEntity, '/delete_entity', DeleteEntity.Request(name=name))
    if not result.success:
        raise SystemExit(f'Nothing removed: {result.status_message}')
    print(f'Removed {name}.')


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest='command', required=True)
    s = sub.add_parser('spawn', help='place a static box')
    s.add_argument('name')
    where = s.add_mutually_exclusive_group(required=True)
    where.add_argument('--station', help='registered station, e.g. A')
    where.add_argument('--at', nargs=2, type=float, metavar=('X', 'Y'), help='map coordinates in metres')
    where.add_argument('--ahead', type=float, metavar='M', help="metres from the robot's centre to the box face")
    s.add_argument('--size', nargs=3, type=float, metavar=('L', 'W', 'H'), help='metres')
    s.add_argument('--yaw', type=float, default=0.0, help='degrees (ignored with --ahead)')
    s.add_argument('--force', action='store_true', help='allow a box that overlaps the robot')
    r = sub.add_parser('remove', help='delete a box placed by this script')
    r.add_argument('name')
    args = p.parse_args()
    rclpy.init()
    node = rclpy.create_node('ripple_demo_obstacle')
    try:
        (spawn if args.command == 'spawn' else remove)(node, args)
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
