import concurrent.futures
import unittest
from pathlib import Path
from types import SimpleNamespace
from action_msgs.msg import GoalStatus
from ripple_edge.authorization import Authorizations
from ripple_edge.contracts import Observation
from ripple_edge.escape import Escape
from ripple_edge.profile import load_profile

ROOT = Path(__file__).resolve().parents[1]
PRABAL = 'a37c631f-b487-47a5-ada7-e0e5140681be'
CLEAR = {'known': True, 'emergency': False, 'obstacle': False, 'localization': False, 'override': False}


def done(value):
    f = concurrent.futures.Future()
    f.set_result(value)
    return f


class Robot:
    def __init__(self):
        self.values = {'safety': dict(CLEAR), 'mode': 'zones', 'localization': True, 'goal': {'active': False},
                       'scan.rear': {'minimum_m': 2.0}, 'odometry_pose': {'x': 0.0, 'y': 0.0, 'yaw': 0.0}}

    def snapshot(self):
        return {k: Observation(value=v, source='t', fresh=True) for k, v in self.values.items()}


class Behavior:
    def __init__(self, robot, status=GoalStatus.STATUS_SUCCEEDED):
        self.robot, self.status, self.goals = robot, status, []

    def server_is_ready(self):
        return True

    def send_goal_async(self, goal):
        self.goals.append(goal)
        self.robot.values['odometry_pose'] = {'x': -0.2, 'y': 0.0, 'yaw': 0.0}  # the robot backs up
        return done(SimpleNamespace(accepted=True, get_result_async=lambda: done(SimpleNamespace(status=self.status))))


class EscapeTests(unittest.IsolatedAsyncioTestCase):
    def escape(self, **escape_changes):
        profile = load_profile(ROOT / 'profiles/smr300.yaml')
        if escape_changes:
            e = profile.recovery.escape.model_copy(update=escape_changes)
            profile = profile.model_copy(update={'recovery': profile.recovery.model_copy(update={'escape': e})})
        self.robot, self.auths = Robot(), Authorizations({PRABAL: 'Prabal Khare'})
        esc = Escape.__new__(Escape)
        esc.profile, esc.detector, esc.auths = profile, self.robot, self.auths
        esc.navigator = SimpleNamespace(active=lambda: False)
        esc.journal = SimpleNamespace(request=lambda **kw: {'claimed': True})
        esc.clients = {'backup': Behavior(self.robot), 'spin': Behavior(self.robot)}
        return esc

    def test_refusals(self):
        self.assertEqual(self.escape(autonomy='off').denial('backup', .2, .1, None), 'escape_disabled')
        esc = self.escape()
        auth = self.auths.record('ambiguous', PRABAL, 'back it out').id
        self.assertEqual(esc.denial('backup', .9, .1, auth), 'exceeds_profile_limits')
        self.robot.values['safety']['obstacle'] = True
        self.assertEqual(esc.denial('backup', .2, .1, auth), 'safety_holding:obstacle')
        self.robot.values['safety']['obstacle'] = False
        self.robot.values['mode'] = 'manual'
        self.assertEqual(esc.denial('backup', .2, .1, auth), 'mode_manual_or_unknown')
        self.robot.values['mode'] = 'zones'
        self.robot.values['goal'] = {'active': True}
        self.assertEqual(esc.denial('spin', 1.0, .1, auth), 'active_goal')
        self.robot.values['goal'] = {'active': False}
        self.robot.values['scan.rear'] = {'minimum_m': 0.5}
        self.assertEqual(esc.denial('backup', .2, .1, auth), 'rear_obstacle_too_close')
        self.robot.values['scan.rear'] = {'minimum_m': 2.0}
        self.assertIn('human_authorization_required', esc.denial('backup', .2, .1, None))
        self.assertIsNone(esc.denial('backup', .2, .1, auth))

    async def test_a_backup_is_verified_by_odometry(self):
        esc = self.escape()
        auth = self.auths.record('ambiguous', PRABAL, 'back it out').id
        out = await esc.execute('inc-1', 'backup', .2, .1, auth, 'r1')
        self.assertEqual((out['status'], out['verified'], out['data']['moved']), ('ok', True, 0.2))
        self.assertEqual(esc.clients['backup'].goals[0].target.x, .2)

    async def test_an_aborted_behavior_is_reported(self):
        esc = self.escape()
        esc.clients['backup'] = Behavior(self.robot, GoalStatus.STATUS_ABORTED)
        auth = self.auths.record('ambiguous', PRABAL, 'back it out').id
        out = await esc.execute('inc-1', 'backup', .2, .1, auth, 'r2')
        self.assertEqual(out['status'], 'failed')
        self.assertIn('collision check or safety stop', out['reason'])


if __name__ == '__main__':
    unittest.main()
