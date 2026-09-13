import unittest
from pathlib import Path
from types import SimpleNamespace
from ripple_edge.contracts import Observation
from ripple_edge.profile import load_profile
from ripple_edge.teleop import Teleop

ROOT = Path(__file__).resolve().parents[1]
CLEAR = {'known': True, 'emergency': False, 'obstacle': False, 'localization': False, 'override': False}


class Robot:
    """Facts plus a body that moves when teleop publishes."""
    def __init__(self, safety=CLEAR, front=2.0, rear=2.0):
        self.safety, self.front, self.rear = dict(safety), front, rear
        self.x, self.published, self.modes = 0.0, [], []

    def snapshot(self):
        values = {'safety': self.safety, 'odometry_pose': {'x': self.x, 'y': 0.0, 'yaw': 0.0},
                  'scan.front': {'minimum_m': self.front}, 'scan.rear': {'minimum_m': self.rear}}
        return {k: Observation(value=v, source='test', fresh=True) for k, v in values.items()}

    def create_publisher(self, kind, topic, depth):
        robot = self

        class Pub:
            def publish(self, msg):
                if hasattr(msg, 'linear'):
                    robot.published.append(msg.linear.x)
                    robot.x += msg.linear.x * 0.5
                else:
                    robot.modes.append(msg.data)
        return Pub()


class TeleopTests(unittest.IsolatedAsyncioTestCase):
    def teleop(self, robot, **profile_changes):
        profile = load_profile(ROOT / 'profiles/smr300.yaml').model_copy(update=profile_changes)
        return Teleop(robot, profile, robot, SimpleNamespace(active=lambda: False), SimpleNamespace(request=lambda **k: {}))

    async def test_refusals(self):
        robot = Robot(safety={**CLEAR, 'obstacle': True})
        out = await self.teleop(robot, teleop=None).execute('forward', .2, .1, False, 'x', 'r1')
        self.assertEqual(out['reason'], 'teleop_not_declared_in_profile')
        out = await self.teleop(robot).execute('forward', .2, .1, False, 'x', 'r2')
        self.assertIn('pass override_safety', out['reason'])
        out = await self.teleop(robot, simulation=False).execute('backward', .2, .1, True, 'x', 'r3')
        self.assertIn('only allowed on a declared simulation', out['reason'])
        robot.safety = {**CLEAR, 'emergency': True}
        out = await self.teleop(robot).execute('backward', .2, .1, True, 'x', 'r4')
        self.assertIn('never overrides an e-stop', out['reason'])
        self.assertEqual(robot.published, [])

    def test_auto_picks_the_side_with_room(self):
        t = self.teleop(Robot())
        self.assertEqual(t.choose('auto', Robot(front=0.3, rear=2.0).snapshot()), 'backward')
        self.assertEqual(t.choose('auto', Robot(front=0.25, rear=0.25).snapshot()), 'rotate_left')
        self.assertEqual(t.choose('forward', Robot().snapshot()), 'forward')

    async def test_a_bounded_move_is_verified_and_the_mode_restored_after_an_override(self):
        robot = Robot(safety={**CLEAR, 'obstacle': True})
        out = await self.teleop(robot).execute('backward', .1, 5.0, True, 'free it', 'r5', 'inc-1')
        self.assertEqual(out['status'], 'ok')
        self.assertTrue(out['data']['overrode_safety'])
        self.assertEqual(min(robot.published), -0.15)  # capped at the profile's max_speed_mps
        self.assertEqual(robot.published[-1], 0.0)     # always ends with a stop
        self.assertEqual((robot.modes[0], robot.modes[-1]), ('manual', 'zones'))

    async def test_it_stops_inside_the_minimum_clearance(self):
        robot = Robot(rear=0.1)
        out = await self.teleop(robot).execute('backward', .3, .1, False, 'x', 'r6')
        self.assertEqual(out['status'], 'failed')
        self.assertIn('scan shows 0.10 m', out['reason'])


if __name__ == '__main__':
    unittest.main()
