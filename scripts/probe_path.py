"""Read-only Nav2 planning probe. Does not send a navigation/motion goal."""
import argparse
import json
import time
import rclpy
from rclpy.action import ActionClient
from rclpy.node import Node
from rclpy.parameter import Parameter
from nav2_msgs.action import ComputePathToPose

p=argparse.ArgumentParser();p.add_argument('--x',type=float,required=True);p.add_argument('--y',type=float,required=True)
a=p.parse_args();rclpy.init();node=Node('ripple_planning_probe')
node.set_parameters([Parameter('use_sim_time',value=True)])
client=ActionClient(node,ComputePathToPose,'/compute_path_to_pose')
def wait(f):
    end=time.monotonic()+45
    while not f.done() and time.monotonic()<end:rclpy.spin_once(node,timeout_sec=.1)
    if not f.done():raise TimeoutError('Planner response timed out')
    return f.result()
try:
    if not client.wait_for_server(timeout_sec=10):raise RuntimeError('Planner unavailable')
    goal=ComputePathToPose.Goal();goal.goal.header.frame_id='map'
    goal.goal.pose.position.x=a.x;goal.goal.pose.position.y=a.y;goal.goal.pose.orientation.w=1.
    goal.use_start=False
    handle=wait(client.send_goal_async(goal))
    if not handle.accepted:raise RuntimeError('Planner rejected request')
    result=wait(handle.get_result_async())
    print(json.dumps(dict(target=[a.x,a.y],status=result.status,
        points=[[p.pose.position.x,p.pose.position.y] for p in result.result.path.poses])))
finally:
    node.destroy_node();rclpy.shutdown()
