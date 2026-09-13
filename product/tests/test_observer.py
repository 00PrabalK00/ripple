"""The observer's callbacks with real ROS messages on a real (offline) node."""
import unittest
from pathlib import Path
import rclpy
from action_msgs.msg import GoalStatus, GoalStatusArray
from geometry_msgs.msg import PoseWithCovarianceStamped
from nav_msgs.msg import Odometry
from rcl_interfaces.msg import Log
from sensor_msgs.msg import LaserScan
from ripple_edge.observer import Observer
from ripple_edge.profile import load_profile

ROOT = Path(__file__).resolve().parents[1]


class ObserverTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        rclpy.init()
        cls.obs = Observer(load_profile(ROOT / 'profiles/smr300.yaml'))

    @classmethod
    def tearDownClass(cls):
        cls.obs.destroy_node()
        rclpy.shutdown()

    def value(self, key):
        o = self.obs.detector.snapshot().get(key)
        return o.value if o and o.fresh else None

    def test_localization_is_ok_only_within_the_covariance_limits(self):
        msg = PoseWithCovarianceStamped()
        msg.header.frame_id = 'map'
        msg.pose.pose.position.x, msg.pose.pose.orientation.w = 1.5, 1.0
        msg.pose.covariance[0] = msg.pose.covariance[7] = 0.1
        self.obs.pose(msg)
        self.assertTrue(self.value('localization'))
        self.assertEqual(self.value('pose')['x'], 1.5)
        msg.pose.covariance[0] = 3.0
        self.obs.pose(msg)
        self.assertFalse(self.value('localization'))

    def test_odometry_and_scans(self):
        odom = Odometry()
        odom.twist.twist.linear.x, odom.twist.twist.angular.z = 0.3, 0.1
        odom.pose.pose.orientation.w = 1.0
        self.obs.odom(odom)
        self.assertAlmostEqual(self.value('odometry')['linear'], 0.3)
        scan = LaserScan()
        scan.range_min, scan.range_max = 0.1, 10.0
        scan.ranges = [float('inf'), 0.05, 0.8, 2.0]  # out-of-range readings are ignored
        cfg = self.obs.profile.scans['front']
        self.obs.scan('front', cfg, scan)
        seen = self.value('scan.front')
        self.assertAlmostEqual(seen['minimum_m'], 0.8, places=5)  # ranges are 32-bit floats
        self.assertEqual(seen['valid_points'], 2)

    def test_goal_status_and_feedback(self):
        status = GoalStatusArray()
        s = GoalStatus()
        s.goal_info.goal_id.uuid = [7] * 16
        s.status = GoalStatus.STATUS_ABORTED
        status.status_list = [s]
        self.obs.status(status)
        self.assertEqual(self.value('goal'), {'active': False, 'id': None})
        self.assertEqual(len(self.obs.detector.failures), 1)

    def test_only_declared_nodes_at_warning_level_become_findings(self):
        before = len(self.obs.findings)
        info, error = 20, 40  # rcl_interfaces/Log levels (the message constants are bytes)
        self.obs.log(Log(level=info, name='controller_server', msg='Failed to make progress'))
        self.obs.log(Log(level=error, name='some_other_node', msg='Failed to make progress'))
        self.assertEqual(len(self.obs.findings), before)
        self.obs.log(Log(level=error, name='controller_server', msg='Failed to make progress'))
        self.assertGreater(len(self.obs.findings), before)


if __name__ == '__main__':
    unittest.main()
