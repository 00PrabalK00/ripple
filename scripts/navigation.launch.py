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
    # Inherited 20 ms acknowledgement deadline is too short under measured local load.
    params['bt_navigator']['ros__parameters']['default_server_timeout'] = 1000
    # Under simulator load AMCL's map->odom arrives late; the inherited 0.5 s tolerance aborted live goals
    # with "Transform data too old" (measured ~1.4 s lag at real-time factor 0.84).
    params['amcl']['ros__parameters']['transform_tolerance'] = 1.5
    params['controller_server']['ros__parameters']['FollowPath']['transform_tolerance'] = 1.0
    # 20 Hz missed its deadline continuously under simulator load and DWB oscillated; 10 Hz is ample at 0.3 m/s.
    params['controller_server']['ros__parameters']['controller_frequency'] = 10.0
    # Inherited inflation (0.5 m, scaling 5.0) barely exceeds the footprint's 0.47 m circumscribed radius, so plans
    # hugged shelf corners and the safety controller's 0.35 m front zone stopped the robot there. Keep paths wider.
    inflation = {'global_costmap': (0.9, 3.0), 'local_costmap': (0.7, 3.0)}
    for costmap in ('local_costmap', 'global_costmap'):
        params[costmap][costmap]['ros__parameters']['transform_tolerance'] = 1.0
        radius, scaling = inflation[costmap]
        params[costmap][costmap]['ros__parameters']['inflation_layer'].update(
            inflation_radius=radius, cost_scaling_factor=scaling)
        # Inflate around keepouts: with the filter after the inflation layer, paths hugged the keepout edge,
        # the footprint drifted into it, and Nav2 could no longer plan from the robot's own pose.
        costmap_params = params[costmap][costmap]['ros__parameters']
        plugins = [p for p in costmap_params['plugins'] if p != 'keepout_filter']
        plugins.insert(plugins.index('inflation_layer'), 'keepout_filter')
        costmap_params['plugins'] = plugins
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
        'autostart': True, 'node_names': [name for _, name in nodes],
        # Bonds make the manager shut the whole stack down when one node is reset for recovery.
        'bond_timeout': 0.0}], output='screen'))
    return LaunchDescription(actions)
