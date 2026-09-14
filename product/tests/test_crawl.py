import tempfile
import unittest
from pathlib import Path
from ripple_edge.contracts import Profile
from ripple_edge.crawl import propose, static_scan


def topic(types, publishers=(), subscribers=()):
    return {'types': [types], 'publishers': list(publishers), 'subscribers': list(subscribers)}


# A robot whose names differ from the SMR300's: namespaced odometry, a lidar called /lidar/front, no twist_mux.
LIVE = {
    'nodes': ['/amcl', '/planner_server', '/controller_server', '/bt_navigator', '/behavior_server', '/map_server',
              '/base/driver', '/safety_node'],
    'topics': {
        '/wheel/odometry': topic('nav_msgs/msg/Odometry', ['/base/driver'], ['/controller_server']),
        '/odometry/filtered': topic('nav_msgs/msg/Odometry', ['/ekf'], []),
        '/amcl_pose': topic('geometry_msgs/msg/PoseWithCovarianceStamped', ['/amcl'], []),
        '/initialpose': topic('geometry_msgs/msg/PoseWithCovarianceStamped', [], ['/amcl']),
        '/lidar/front': topic('sensor_msgs/msg/LaserScan', ['/lidar_front'], ['/amcl']),
        '/lidar/rear': topic('sensor_msgs/msg/LaserScan', ['/lidar_rear'], []),
        '/map': topic('nav_msgs/msg/OccupancyGrid', ['/map_server'], []),
        '/global_costmap/costmap': topic('nav_msgs/msg/OccupancyGrid', ['/global_costmap/global_costmap'], []),
        '/plan': topic('nav_msgs/msg/Path', ['/planner_server'], []),
        '/base/cmd_vel': topic('geometry_msgs/msg/Twist', ['/controller_server'], ['/base/driver']),
        '/goal_pose': topic('geometry_msgs/msg/PoseStamped', [], ['/bt_navigator']),
    },
    'services': {
        **{f'/{n}/get_state': ['lifecycle_msgs/srv/GetState'] for n in ('amcl', 'planner_server', 'controller_server',
                                                                        'bt_navigator', 'behavior_server', 'map_server')},
        '/global_costmap/clear_entirely_global_costmap': ['nav2_msgs/srv/ClearEntireCostmap'],
        '/local_costmap/clear_entirely_local_costmap': ['nav2_msgs/srv/ClearEntireCostmap'],
    },
    'actions': {
        '/navigate_to_pose': {'types': ['nav2_msgs/action/NavigateToPose'], 'servers': ['/bt_navigator'], 'clients': []},
        '/compute_path_to_pose': {'types': ['nav2_msgs/action/ComputePathToPose'], 'servers': ['/planner_server'], 'clients': []},
        '/backup': {'types': ['nav2_msgs/action/BackUp'], 'servers': ['/behavior_server'], 'clients': []},
        '/spin': {'types': ['nav2_msgs/action/Spin'], 'servers': ['/behavior_server'], 'clients': []},
    },
    'params': {'/controller_server': {'odom_topic': 'wheel/odometry'},
               '/global_costmap/global_costmap': {'global_frame': 'map', 'robot_base_frame': 'base_footprint',
                                                  'footprint': '[[0.3, 0.2], [0.3, -0.2], [-0.3, -0.2], [-0.3, 0.2]]'}},
    'safety_text': {},
}


class CrawlTests(unittest.TestCase):
    def test_roles_are_matched_by_type_and_publisher_not_by_name(self):
        p = propose({}, LIVE)
        self.assertEqual(p.value('odometry'), '/wheel/odometry')  # what the controller uses, not the EKF output
        self.assertEqual(p.fields['odometry'].confidence, 'high')
        self.assertEqual(p.value('localization'), '/amcl_pose')  # /initialpose is an input, not an estimate
        self.assertEqual(p.fields['localization'].confidence, 'high')
        self.assertEqual(p.value('scans'), {'front': '/lidar/front', 'rear': '/lidar/rear'})
        self.assertEqual(p.value('navigation.base_frame'), 'base_footprint')
        self.assertEqual(p.value('navigation.footprint_radius_m'), 0.36)
        self.assertEqual(p.value('motion_output'), '/base/cmd_vel')  # no multiplexer: the controller's command topic
        self.assertNotIn('/map_server', p.value('navigation.lifecycle_nodes'))

    def test_without_a_safety_layer_the_profile_is_conservative_and_valid(self):
        profile = propose({}, LIVE).profile()
        Profile.model_validate(profile)
        self.assertFalse(profile['safety']['required'])
        self.assertEqual(profile['recovery']['escape']['autonomy'], 'off')
        self.assertNotIn('teleop', profile)

    def test_static_scan_reads_mux_params_and_named_poses(self):
        with tempfile.TemporaryDirectory() as root:
            Path(root, 'pkg').mkdir()
            Path(root, 'pkg/package.xml').write_text('<package><name>my_robot_bringup</name></package>')
            Path(root, 'pkg/twist_mux.yaml').write_text(
                'twist_mux:\n  ros__parameters:\n    topics:\n      navigation: {topic: cmd_vel, timeout: 0.5, priority: 10}\n'
                '      joystick: {topic: joy_vel, timeout: 0.5, priority: 90}\n')
            Path(root, 'pkg/waypoints.yaml').write_text(
                'dock: {x: 1.0, y: 2.0, yaw: 0.5}\npick: {x: -3.0, y: 0.5, yaw: 0.0}\n')
            Path(root, 'build').mkdir()
            Path(root, 'build/package.xml').write_text('<package><name>ignored_build_copy</name></package>')
            s = static_scan(root)
        self.assertEqual(s['packages'], ['my_robot_bringup'])
        p = propose(s, None)
        self.assertEqual(p.value('motion_inputs')['joystick'], {'topic': '/joy_vel', 'priority': 90, 'max_age_s': 0.5})
        self.assertEqual(p.value('teleop.topic'), '/joy_vel')
        self.assertEqual(set(p.value('stations')), {'dock', 'pick'})
        self.assertEqual(p.fields['navigation.navigate_action'].confidence, 'medium')  # not confirmed live

    def test_existing_config_is_kept_but_drift_is_reported(self):
        existing = {'odometry': {'topic': '/old/odom'}, 'localization': {'topic': '/amcl_pose'}}
        p = propose({}, LIVE, existing)
        self.assertEqual(p.drift, [{'field': 'odometry', 'configured': '/old/odom', 'observed': '/wheel/odometry'}])
        self.assertEqual(p.value('localization'), '/amcl_pose')

    def test_rerunning_setup_over_its_own_profile_keeps_it_valid(self):
        # Setup run a second time reads the ripple.json it wrote; scans are stored there as {name: {topic, max_age_s}}.
        first = propose({}, LIVE).profile()
        p = propose({}, LIVE, first)
        self.assertEqual(p.drift, [])
        self.assertEqual(p.value('scans'), {'front': '/lidar/front', 'rear': '/lidar/rear'})
        self.assertEqual(p.value('recovery.backup_action'), '/backup')
        Profile.model_validate(p.profile())


if __name__ == '__main__':
    unittest.main()
