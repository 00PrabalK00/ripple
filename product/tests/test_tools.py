"""The edge's tool layer with a fake runtime: approvals, keepout rules, via recovery budgets."""
import unittest
from collections import deque
from pathlib import Path
from types import SimpleNamespace
from ripple_edge.authorization import Authorizations
from ripple_edge.contracts import Observation
from ripple_edge.geometry import MapGeometry
from ripple_edge.profile import load_profile
from ripple_edge.site import Site
from ripple_edge.tools import EdgeTools

ROOT = Path(__file__).resolve().parents[1]
PRABAL = '00000000-0000-4000-8000-00000000cafe'
GEOM = MapGeometry(280, 419, .05, -6.99, -10.4)


class FakeNavigator:
    def __init__(self):
        self.dispatched, self.problems = [], []

    def check_via(self, points):
        return self.problems

    async def dispatch(self, key, dest, auth_id, reason, attempt='command', via=None):
        self.dispatched.append({'key': key, 'auth': auth_id, 'attempt': attempt, 'via': via})
        return {'status': 'ok', 'reason': 'Nav2 accepted the goal', 'goal_id': 'g1', 'route_length_m': 3.0}

    def public(self):
        return None

    def active(self):
        return False


class FakeKeepouts:
    enabled = True

    async def apply(self, record):
        return True, 'observed in the keepout mask and global costmap'

    async def remove(self, record):
        return True, 'cleared from the keepout mask'


class Journal:
    def __init__(self):
        self.claims = {}

    def request(self, op, **kw):
        if op != 'claim':
            return {}
        key = (kw['incident'], kw['action'])
        self.claims[key] = self.claims.get(key, 0) + 1
        ok = self.claims[key] <= kw['limit']
        return {'claimed': ok, 'reason': '' if ok else 'incident_budget_exhausted'}


def runtime(pose=(0.0, 0.0)):
    profile = load_profile(ROOT / 'profiles/smr300.yaml')
    facts = {'pose': Observation(value={'frame': 'map', 'x': pose[0], 'y': pose[1], 'yaw': 0.0}, source='t', fresh=True)}
    rt = SimpleNamespace(profile=profile, site=Site(profile), navigator=FakeNavigator(), keepouts=FakeKeepouts(),
                         auths=Authorizations({PRABAL: 'Prabal Khare'}), journal=Journal(), geometry=lambda: GEOM,
                         node=SimpleNamespace(detector=SimpleNamespace(snapshot=lambda: facts), events=deque(), findings=deque()),
                         policy=SimpleNamespace(register_incident=lambda e: None), reader=None, recorded=[])
    rt.record_tool = lambda result, args: rt.recorded.append(result)
    return rt


class ToolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.rt = runtime()
        self.tools = EdgeTools(self.rt)
        self.auth = self.rt.auths.record('ambiguous', PRABAL, 'send the robot to B').id

    async def test_commands_need_the_operator_message_that_asked(self):
        denied = await self.tools.call('navigate_to', {'destination': 'B', 'authorization_id': 'auth-made-up'})
        self.assertEqual(denied['status'], 'denied')
        self.assertIn('command actions need the ID of the operator message', denied['reason'])
        ok = await self.tools.call('navigate_to', {'destination': 'packing station b', 'authorization_id': self.auth})
        self.assertEqual((ok['status'], ok['data']['destination']), ('ok', 'B'))
        self.assertEqual(self.rt.navigator.dispatched[0]['auth'], self.auth)
        unknown = await self.tools.call('navigate_to', {'destination': 'Mars', 'authorization_id': self.auth})
        self.assertIn('unknown destination', unknown['reason'])
        self.assertEqual(len(self.rt.recorded), 3)  # every call is journaled for the timeline

    async def test_invalid_arguments_are_refused_before_anything_runs(self):
        out = await self.tools.call('navigate_to', {'destination': 'B', 'authorization_id': self.auth, 'speed': 9})
        self.assertIn('invalid_arguments', out['reason'])
        self.assertEqual(self.rt.navigator.dispatched, [])

    async def test_a_keepout_over_the_robot_waits_and_can_be_cancelled(self):
        out = await self.tools.call('add_keepout', {'region': {'box_m': [-0.5, -0.5, 0.5, 0.5]}, 'reason': 'pallet',
                                                    'authorization_id': self.auth})
        self.assertEqual(out['data']['state'], 'PENDING')
        site = await self.tools.call('get_site', {})
        self.assertEqual(site['data']['keepouts'][0]['state'], 'PENDING')
        auth2 = self.rt.auths.record('ambiguous', PRABAL, 'reopen it').id
        removed = await self.tools.call('remove_keepout', {'keepout_id': out['data']['keepout_id'], 'authorization_id': auth2})
        self.assertEqual((removed['status'], removed['data']['state']), ('ok', 'REMOVED'))

    async def test_a_keepout_away_from_the_robot_is_applied_and_verified(self):
        out = await self.tools.call('add_keepout', {'region': {'box_m': [2, 2, 3, 3]}, 'reason': 'spill', 'authorization_id': self.auth})
        self.assertEqual((out['status'], out['verified'], out['data']['state']), ('ok', True, 'APPLIED'))

    async def test_navigate_via_as_incident_recovery_uses_the_retry_budget(self):
        iid = self.tools.open_incident('navigation_failed', 'unknown', goal={'destination': 'B', 'authorization_id': self.auth})
        via = [{'x': 1.0, 'y': 1.0}]
        for expected in ('ok', 'ok', 'denied'):  # the profile allows two retries per incident
            out = await self.tools.call('navigate_via', {'via': via, 'incident_id': iid})
            self.assertEqual(out['status'], expected)
        self.assertEqual(out['reason'], 'incident_budget_exhausted')
        self.assertEqual(self.rt.navigator.dispatched[0]['attempt'], 'reroute')

    async def test_navigate_via_reports_rejected_points(self):
        self.rt.navigator.problems = [{'point': 1, 'problem': 'only 0.1 m from an obstacle', 'nearest_clear_point': {'x': 1, 'y': 2}}]
        out = await self.tools.call('navigate_via', {'destination': 'B', 'via': [{'x': 0, 'y': 0}], 'authorization_id': self.auth})
        self.assertEqual(out['status'], 'denied')
        self.assertEqual(out['data']['problems'][0]['nearest_clear_point'], {'x': 1, 'y': 2})

    async def test_recovery_tools_need_a_known_incident(self):
        out = await self.tools.call('retry_navigation', {'incident_id': 'inc-unknown'})
        self.assertEqual(out['status'], 'denied')


if __name__ == '__main__':
    unittest.main()
