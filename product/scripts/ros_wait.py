#!/usr/bin/env python3
"""Wait for ROS state without the ros2 CLI daemon. Exit 0 when satisfied, 1 on timeout.

  ros_wait.py --topic /diff_cont/odom --type nav_msgs/msg/Odometry
  ros_wait.py --lifecycle /bt_navigator
  ros_wait.py --service-contains /safety/status "Control Mode: zones"
"""
import argparse
import importlib
import sys
import time
import rclpy
from rclpy.qos import qos_profile_sensor_data


def message(name):
    package, kind, cls = name.split('/')
    return getattr(importlib.import_module(f'{package}.{kind}'), cls)


def call(node, client, request, timeout):
    if not client.wait_for_service(timeout_sec=min(timeout, 5)):
        return None
    future = client.call_async(request)
    rclpy.spin_until_future_complete(node, future, timeout_sec=5)
    return future.result() if future.done() else None


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--topic')
    p.add_argument('--type')
    p.add_argument('--lifecycle')
    p.add_argument('--service-contains', nargs=2, metavar=('SERVICE', 'TEXT'))
    p.add_argument('--timeout', type=float, default=60)
    a = p.parse_args()
    rclpy.init()
    node = rclpy.create_node('ripple_wait')
    end = time.monotonic() + a.timeout
    ok = False
    try:
        if a.topic:
            got = []
            node.create_subscription(message(a.type), a.topic, got.append, qos_profile_sensor_data)
            while not got and time.monotonic() < end:
                rclpy.spin_once(node, timeout_sec=.2)
            ok = bool(got)
        elif a.lifecycle:
            from lifecycle_msgs.srv import GetState
            client = node.create_client(GetState, a.lifecycle + '/get_state')
            while not ok and time.monotonic() < end:
                r = call(node, client, GetState.Request(), end - time.monotonic())
                ok = r is not None and r.current_state.label == 'active'
                if not ok:
                    time.sleep(1)
        elif a.service_contains:
            from std_srvs.srv import Trigger
            service, text = a.service_contains
            client = node.create_client(Trigger, service)
            while not ok and time.monotonic() < end:
                r = call(node, client, Trigger.Request(), end - time.monotonic())
                ok = r is not None and text in r.message
                if not ok:
                    time.sleep(1)
    finally:
        node.destroy_node()
        rclpy.shutdown()
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
