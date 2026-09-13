"""The navigator's via legs, cancellation and via-point checks, with fake Nav2 action clients."""
import asyncio
import concurrent.futures
import time
import unittest
from collections import deque
from pathlib import Path
from types import SimpleNamespace
from action_msgs.msg import GoalStatus
from ripple_edge.contracts import Observation
from ripple_edge.navigation import Navigator
from ripple_edge.profile import load_profile
from ripple_edge.site import Site
from test_keepouts import grid

ROOT = Path(__file__).resolve().parents[1]


def done(value):
    f = concurrent.futures.Future()
    f.set_result(value)
    return f


class Facts:
    def __init__(self):
        self.values = {'localization': True, 'pose': {'frame': 'map', 'x': 0.5, 'y': 0.5, 'yaw': 0.0},
                       'odometry': {'linear': 0.0, 'angular': 0.0}, 'mode': 'zones',
                       'safety': {'known': True, 'emergency': False, 'obstacle': False, 'localization': False, 'override': False}}

    def snapshot(self):
        return {k: Observation(value=v, source='test', fresh=True) for k, v in self.values.items()}


class FakePlanner:
    def server_is_ready(self):
        return True

    def send_goal_async(self, goal):
        start = goal.start.pose.position if goal.use_start else SimpleNamespace(x=0.5, y=0.5)
        end = goal.goal.pose.position
        poses = [SimpleNamespace(pose=SimpleNamespace(position=SimpleNamespace(x=start.x + (end.x - start.x) * i / 20,
                                                                              y=start.y + (end.y - start.y) * i / 20)))
                 for i in range(21)]
        result = SimpleNamespace(status=GoalStatus.STATUS_SUCCEEDED, result=SimpleNamespace(path=SimpleNamespace(poses=poses)))
        return done(SimpleNamespace(accepted=True, get_result_async=lambda: done(result)))


class FakeClient:
    def __init__(self):
        self.sent, self.pending, self.canceled = [], [], []

    def send_goal_async(self, goal, feedback_callback=None):
        p = goal.pose.pose.position
        self.sent.append((round(p.x, 2), round(p.y, 2)))
        result = concurrent.futures.Future()
        self.pending.append(result)
        n = len(self.sent)
        handle = SimpleNamespace(accepted=True, goal_id=SimpleNamespace(uuid=bytes([n] * 16)),
                                 get_result_async=lambda: result,
                                 cancel_goal_async=lambda: (self.canceled.append(n), done(True))[1])
        return done(handle)


class NavigatorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        profile = load_profile(ROOT / 'profiles/smr300.yaml')
        site = Site(profile)
        nav = Navigator.__new__(Navigator)
        nav.node, nav.profile, nav.detector = None, profile, Facts()
        nav.ownership = SimpleNamespace(available=lambda: True)
        wall = lambda x, y: 100 if 2.9 <= x <= 3.1 else 0  # a wall along x = 3 m
        nav.keepouts = SimpleNamespace(costmap=(grid(wall), time.monotonic()), site=site, all_verified=lambda: True)
        nav.client, nav.planner = FakeClient(), FakePlanner()
        nav.goal, nav.handles, nav.history, nav.plan = None, {}, deque(maxlen=30), None
        nav.listeners, nav.owned, nav._grid = [], (lambda gid, handle: None), None
        nav.loop = asyncio.get_running_loop()
        self.nav, self.finished = nav, []
        nav.listeners.append(self.finished.append)
        self.dest = {'label': 'Bay', 'frame': 'map', 'x': 2.5, 'y': 2.5, 'yaw': 0.0, 'available': True}

    async def finish_leg(self, i, status=GoalStatus.STATUS_SUCCEEDED):
        self.nav.client.pending[i].set_result(SimpleNamespace(status=status))
        await asyncio.sleep(0.1)

    async def test_a_via_route_is_one_goal_driven_leg_by_leg_and_verified_at_the_end(self):
        out = await self.nav.dispatch('BAY', self.dest, 'auth-1', 'test', via=[(1.0, 0.5), (1.5, 2.0)])
        self.assertEqual((out['status'], out['reason']), ('ok', 'Nav2 accepted leg 1 of 3'))
        self.assertEqual(self.nav.client.sent, [(1.0, 0.5)])
        await self.finish_leg(0)
        self.assertEqual(self.nav.client.sent, [(1.0, 0.5), (1.5, 2.0)])
        self.assertEqual((self.nav.goal['status'], self.nav.goal['leg']), ('EXECUTING', 1))
        await self.finish_leg(1)
        self.nav.detector.values['pose'] = {'frame': 'map', 'x': 2.5, 'y': 2.5, 'yaw': 0.0}
        await self.finish_leg(2)
        await asyncio.sleep(0.8)  # settle: still for half a second, then verify the final arrival only
        self.assertEqual(len(self.finished), 1)
        record = self.finished[0]
        self.assertTrue(record['verified'])
        self.assertEqual(len(record['leg_ids']), 3)
        self.assertEqual(record['id'], record['leg_ids'][0])
        self.assertNotIn('legs', record)

    async def test_cancel_stops_the_current_leg(self):
        await self.nav.dispatch('BAY', self.dest, 'auth-1', 'test', via=[(1.0, 0.5)])
        self.nav.loop.call_later(0.1, lambda: self.nav.client.pending[0].set_result(SimpleNamespace(status=GoalStatus.STATUS_CANCELED)))
        out = await self.nav.cancel('operator stop')
        self.assertEqual(out['status'], 'ok')
        self.assertEqual(self.nav.client.canceled, [1])
        self.assertEqual(len(self.nav.client.sent), 1)  # no further legs after a cancel

    async def test_via_points_against_a_wall_are_refused_with_a_clear_alternative(self):
        problems = self.nav.check_via([(3.0, 1.0), (1.0, 1.0)])
        self.assertEqual(len(problems), 1)
        self.assertEqual(problems[0]['point'], 1)
        better = problems[0]['nearest_clear_point']
        self.assertGreaterEqual(abs(better['x'] - 3.0), 0.4)
        out = await self.nav.dispatch('BAY', self.dest, 'auth-1', 'test')
        self.assertEqual(out['status'], 'ok')  # no via points: a plain goal

    async def test_dispatch_is_refused_when_a_check_fails(self):
        self.nav.detector.values['mode'] = 'manual'
        out = await self.nav.dispatch('BAY', self.dest, 'auth-1', 'test')
        self.assertEqual(out['failed_checks'], ['mode_not_manual'])
        self.assertEqual(self.nav.client.sent, [])


if __name__ == '__main__':
    unittest.main()
