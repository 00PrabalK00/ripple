"""Local-path bringup using inherited navigation parameters and node layout."""
from pathlib import Path
import yaml
from launch import LaunchDescription
from launch_ros.actions import Node


def generate_launch_description():
    root = Path(__file__).resolve().parents[1]
    baseline = root / 'smr300l_gazebo_ros2control'
    text = (baseline / 'config/nav2_params_working.yaml').read_text()
    text = text.replace('/home/aun/Downloads/smr300l_gazebo_ros2control-main', str(baseline))
    params = yaml.safe_load(text)
    params['amcl']['ros__parameters'].update(set_initial_pose=True,
        initial_pose={'x': 0.0, 'y': 0.0, 'z': 0.0, 'yaw': 0.0})
    runtime = root / 'config/nav2_local.generated.yaml'
    runtime.write_text(yaml.safe_dump(params))
    nodes = [('nav2_map_server', 'map_server'), ('nav2_amcl', 'amcl'),
             ('nav2_controller', 'controller_server'), ('nav2_planner', 'planner_server'),
             ('nav2_behaviors', 'behavior_server'), ('nav2_bt_navigator', 'bt_navigator')]
    actions = [Node(package=package, executable=name, name=name, output='screen',
                    parameters=[str(runtime), {'use_sim_time': True}]) for package, name in nodes]
    actions.append(Node(package='next_ros2ws_core', executable='scan_merger',
                        parameters=[{'use_sim_time': True}], output='screen'))
    actions.append(Node(package='nav2_lifecycle_manager', executable='lifecycle_manager',
        name='ripple_navigation_lifecycle', parameters=[{'use_sim_time': True,
        'autostart': True, 'node_names': [name for _, name in nodes]}], output='screen'))
    return LaunchDescription(actions)
