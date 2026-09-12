import unittest
from ripple.contracts import Target
from ripple.guards import parse_safety_status
from ripple.store import Store
from ripple.supervisor import Supervisor


class Robot:
    def __init__(self):
        self.sent, self.cancelled = [], []

    def send(self, goal_id, target):
        self.sent.append((goal_id, target))

    def cancel(self, goal_id):
        self.cancelled.append(goal_id)


class ApprovalTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.0
        self.robot, self.store = Robot(), Store()
        self.addCleanup(self.store.close)
        self.s = Supervisor(self.robot, self.store, lambda: self.now)
        self.target = Target('B', 'map', 1.0, 2.0, 0.0)
        for key, value in {'robot.mode': 'zones', 'robot.safety_clear': True,
                           'robot.localized': True, 'robot.odom_fresh': True,
                           'station.B.available': True, 'camera.B': 'CLEAR'}.items():
            self.s.observe(key, value, 'test fixture', 1.0)

    def proposal(self):
        p = self.s.propose(self.target, 'B is available')
        self.s.approve(p.id, 'test operator')
        return p

    def test_duplicate_approval_and_dispatch_send_once(self):
        p = self.proposal()
        self.assertFalse(self.s.approve(p.id, 'test operator'))
        self.assertTrue(self.s.dispatch(p.id, self.target))
        self.assertFalse(self.s.dispatch(p.id, self.target))
        self.assertEqual(len(self.robot.sent), 1)

    def test_block_then_clear_never_restores_approval(self):
        p = self.proposal()
        self.s.observe('camera.B', 'BLOCKED', 'camera', 1)
        self.s.observe('camera.B', 'CLEAR', 'camera', 1)
        self.assertEqual(p.state, 'EXPIRED')
        self.assertFalse(self.s.dispatch(p.id, self.target))
        self.assertEqual(self.robot.sent, [])
        fresh = self.proposal()
        self.assertTrue(self.s.dispatch(fresh.id, self.target))

    def test_same_meaning_refresh_preserves_version(self):
        p = self.proposal()
        self.now += 0.5
        self.s.observe('camera.B', 'CLEAR', 'camera', 1)
        self.assertEqual(self.s.facts['camera.B'].version, 1)
        self.assertTrue(self.s.dispatch(p.id, self.target))

    def test_camera_loss_expires_approval(self):
        p = self.proposal()
        self.now += 1.1
        self.assertFalse(self.s.dispatch(p.id, self.target))
        self.assertEqual(p.state, 'EXPIRED')

    def test_changed_target_and_manual_mode_block(self):
        p = self.proposal()
        self.assertFalse(self.s.dispatch(p.id, Target('B', 'map', 9, 2, 0)))
        self.s.observe('robot.mode', 'manual', 'robot', 1)
        self.assertFalse(self.s.dispatch(p.id, self.target))

    def test_cancel_ack_is_not_completion(self):
        p = self.proposal()
        self.s.dispatch(p.id, self.target)
        goal = self.s.goal_id
        self.s.accepted(goal)
        self.s.observe('camera.B', 'BLOCKED', 'camera', 1)
        self.assertEqual(self.robot.cancelled, [goal])
        self.s.observe('camera.B', 'CLEAR', 'camera', 1)
        repair = self.proposal()
        self.assertFalse(self.s.dispatch(repair.id, self.target))
        self.s.terminal(goal, 'CANCELED')
        self.assertFalse(self.s.dispatch(repair.id, self.target))
        self.now += 0.1
        self.s.odometry(0, 0, self.now)
        self.now += 0.31
        self.s.odometry(0, 0, self.now)
        self.assertTrue(self.s.dispatch(repair.id, self.target))

    def test_restart_holds(self):
        restarted = Supervisor(self.robot, self.store, lambda: self.now)
        self.assertTrue(restarted.reconciliation_required)
        self.assertEqual(restarted.state, 'HELD')

    def test_approval_timeout(self):
        p = self.proposal()
        self.now += 60
        self.s.tick()
        self.assertEqual(p.state, 'EXPIRED')

    def test_uncertain_send_is_never_replayed(self):
        def fail(*args):
            raise TimeoutError('unknown send result')
        self.robot.send = fail
        p = self.proposal()
        self.s.dispatch(p.id, self.target)
        self.assertEqual(self.s.state, 'HELD')
        self.assertEqual(p.state, 'CONSUMED')
        self.assertFalse(self.s.dispatch(p.id, self.target))

    def test_safety_status_missing_and_duplicate_fields_fail_closed(self):
        status = ('E-Stop: inactive\nLow Confidence Stop: inactive\n'
                  'Obstacle Stop: inactive\nManual Override: disabled\nControl Mode: zones')
        self.assertTrue(parse_safety_status(True, status))
        self.assertFalse(parse_safety_status(False, status))
        self.assertFalse(parse_safety_status(True, status.replace('E-Stop: inactive', '')))
        self.assertFalse(parse_safety_status(True, status + '\nE-Stop: ACTIVE'))
        self.assertTrue(parse_safety_status(True, status + '\nNearest: 1.0m\nNearest: 2.0m'))


if __name__ == '__main__':
    unittest.main()
