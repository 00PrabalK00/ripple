"""Orchestrator behaviour with a scripted GLM and a fake edge: no ROS, no network."""
import asyncio
import json
import unittest
from collections import deque
from pathlib import Path
from types import SimpleNamespace
from ripple_agent.llm import ModelError
from ripple_agent.orchestrator import Orchestrator
from ripple_edge.authorization import Authorizations
from ripple_edge.profile import load_profile
from ripple_edge.site import Site

ROOT = Path(__file__).resolve().parents[1]
PRABAL = 'a37c631f-b487-47a5-ada7-e0e5140681be'


def call(name, **args):
    return {'id': 'c-' + name, 'type': 'function', 'function': {'name': name, 'arguments': json.dumps(args)}}


class ScriptedGLM:
    model = 'test/glm'
    configured = True
    last_error = None

    def __init__(self, *turns):
        self.turns = deque(turns)
        self.requests = []

    async def chat(self, messages, tools=None, max_tokens=0):
        self.requests.append({'messages': messages, 'tools': [t['function']['name'] for t in tools or []]})
        turn = self.turns.popleft()
        if isinstance(turn, Exception):
            raise turn
        return turn, 'tool_calls' if turn.get('tool_calls') else 'stop'


class FakeDetector:
    def __init__(self):
        self.cause_value = 'unknown'
        self.failures = []

    def snapshot(self):
        return {}

    def cause(self, facts):
        return self.cause_value


class FakeTools:
    def __init__(self, edge):
        self.edge = edge
        self.calls = []
        self.results = {}
        self.specs = {n: None for n in ('robot_status', 'get_diagnostics', 'get_recent_logs', 'get_site', 'probe_route',
                                        'navigate_to', 'cancel_navigation', 'add_keepout', 'remove_keepout', 'define_area',
                                        'set_station_availability', 'clear_costmap', 'lifecycle_reset', 'escape', 'retry_navigation',
                                        'teleop')}
        self.opened = []

    def definitions(self, names=None):
        return [{'type': 'function', 'function': {'name': n, 'parameters': {}}} for n in self.specs if names is None or n in names]

    def open_incident(self, kind, cause, goal=None):
        self.opened.append((kind, cause))
        return f'inc-{len(self.opened)}'

    def _val(self, key):
        return {'x': 1.0, 'y': 2.0, 'yaw': 0.0} if key == 'pose' else None

    async def call(self, name, args, request_id=None):
        self.calls.append((name, dict(args)))
        result = {'tool': name, 'request_id': 'r', 'status': 'ok', 'reason': 'done', 'verified': True, 'data': {}}
        result.update(self.results.get(name, {}))
        for listener in self.edge.tool_listeners:
            listener(result, args)
        return result


class FakeChannel:
    def __init__(self):
        self.sent = []

    async def send(self, channel, text, thread_id=None):
        self.sent.append((channel, text))


def make():
    profile = load_profile(ROOT / 'profiles/smr300.yaml')
    edge = SimpleNamespace(profile=profile, tool_listeners=[], remember=lambda *a: None, recall=lambda *a: [],
                           geometry=lambda: None, rosscope_summary=lambda: {'status': 'not_configured'})
    edge.node = SimpleNamespace(detector=FakeDetector(), events=deque())
    edge.navigator = SimpleNamespace(listeners=[], plan=None, goal=None, public=lambda: None, active=lambda: False)
    edge.auths = Authorizations({PRABAL: 'Prabal Khare', 'local-dashboard': 'Local operator'})
    edge.site = Site(profile)
    edge.keepouts = SimpleNamespace(enabled=True)
    edge.tools = FakeTools(edge)
    return edge


def dm(text, who=PRABAL):
    return dict(channel='ambiguous', operator_id=who, operator='Prabal Khare', text=text, message_id='m1', reply_to='dm-1')


class OrchestratorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.edge = make()
        self.channel = FakeChannel()

    def orch(self, *turns):
        o = Orchestrator(self.edge, ScriptedGLM(*turns), escalation=('ambiguous', 'dm-1'))
        o.loop = asyncio.get_running_loop()
        o.channels['ambiguous'] = self.channel
        return o

    async def test_command_is_the_approval(self):
        o = self.orch({'content': '', 'tool_calls': [call('navigate_to', destination='B')]},
                      {'content': 'Heading to Packing B now.'})
        await o.on_operator(dm('send the robot to B'))
        name, args = self.edge.tools.calls[0]
        self.assertEqual(name, 'navigate_to')
        auth = self.edge.auths.get(args['authorization_id'])
        self.assertEqual((auth.operator, auth.text), ('Prabal Khare', 'send the robot to B'))
        self.assertEqual(self.channel.sent, [('dm-1', 'Heading to Packing B now.')])
        self.assertNotIn('ask_engineer', o.llm.requests[0]['tools'])

    async def test_unlisted_sender_gets_no_model_turn(self):
        o = self.orch()
        await o.on_operator(dm('send the robot to B', who='someone-else'))
        self.assertEqual(o.llm.requests, [])
        self.assertIn('only act on instructions', self.channel.sent[0][1])

    async def test_stop_never_waits_for_the_model(self):
        o = self.orch()
        o.submit(dm('stop the robot'))
        await asyncio.sleep(0.05)
        self.assertEqual(self.edge.tools.calls[0][0], 'cancel_navigation')
        self.assertTrue(o.queue.empty())
        self.assertEqual(o.llm.requests, [])

    async def test_sim_teleop_is_offered_during_a_safety_hold(self):
        self.edge.node.detector.cause_value = 'safety_obstacle'
        o = self.orch({'content': 'Holding.'})
        await o.on_nav({'id': 'g1', 'status': 'ABORTED', 'label': 'Packing B', 'destination': 'B', 'verified': False})
        self.assertIn('teleop', o.llm.requests[0]['tools'])

    async def test_safety_hold_removes_recovery_motion_and_escalates(self):
        self.edge.profile = self.edge.profile.model_copy(update={'simulation': False})
        self.edge.node.detector.cause_value = 'safety_obstacle'
        o = self.orch({'content': 'The safety controller is holding the robot.'})
        await o.on_nav({'id': 'g1', 'status': 'ABORTED', 'label': 'Packing B', 'destination': 'B', 'verified': False})
        offered = o.llm.requests[0]['tools']
        for tool in ('clear_costmap', 'escape', 'retry_navigation', 'lifecycle_reset', 'navigate_to'):
            self.assertNotIn(tool, offered)
        inc = next(iter(o.incidents.values()))
        self.assertEqual(inc['state'], 'ESCALATED')
        self.assertIn('physically blocking', self.channel.sent[-1][1])

    async def test_human_assisted_recovery_end_to_end(self):
        o = self.orch(
            {'content': '', 'tool_calls': [call('robot_status')]},
            {'content': '', 'tool_calls': [call('clear_costmap', costmap='local', incident_id='made-up')]},
            {'content': '', 'tool_calls': [call('record_hypothesis', text='Route to B is obstructed')]},
            {'content': '', 'tool_calls': [call('ask_engineer', question='Is the aisle to Packing B blocked?')]},
            # engineer replies
            {'content': '', 'tool_calls': [call('add_keepout', region={'box_m': [3.8, 1.5, 4.4, 2.0]}, reason='pallet')]},
            {'content': '', 'tool_calls': [call('navigate_to', destination='A')]},
            {'content': 'Keepout added around the pallet; heading to Packing A instead.'})
        await o.on_nav({'id': 'g1', 'status': 'ABORTED', 'label': 'Packing B', 'destination': 'B', 'verified': False})
        inc = next(iter(o.incidents.values()))
        self.assertEqual(inc['state'], 'ESCALATED')
        self.assertEqual(self.edge.tools.calls[1][1]['incident_id'], inc['id'])  # edge-issued ID, not the model's
        self.assertEqual(self.channel.sent[-1], ('dm-1', 'Is the aisle to Packing B blocked?'))
        self.edge.tools.results['navigate_to'] = {'data': {'goal_id': 'g2', 'label': 'Packing A'}}
        self.edge.navigator.active = lambda: True
        await o.on_operator(dm('Yes, a pallet is blocking it. Use A instead.'))
        self.assertEqual(inc['state'], 'RECOVERING')
        keepout = [a for n, a in self.edge.tools.calls if n == 'add_keepout'][0]
        self.assertEqual(self.edge.auths.get(keepout['authorization_id']).text, 'Yes, a pallet is blocking it. Use A instead.')
        self.edge.navigator.active = lambda: False
        await o.on_nav({'id': 'g2', 'status': 'SUCCEEDED', 'label': 'Packing A', 'destination': 'A', 'verified': True})
        self.assertEqual(inc['state'], 'RESOLVED')
        self.assertIn('Resolved', self.channel.sent[-1][1])
        self.assertIn('INVESTIGATING', [s for s, _ in inc['states']])

    async def test_failed_retry_escalates_instead_of_looping(self):
        o = self.orch({'content': '', 'tool_calls': [call('retry_navigation')]}, {'content': 'Retrying once.'})
        self.edge.tools.results['retry_navigation'] = {'data': {'goal_id': 'g3'}}
        self.edge.navigator.active = lambda: True
        await o.on_nav({'id': 'g1', 'status': 'ABORTED', 'label': 'Packing B', 'destination': 'B', 'verified': False})
        inc = next(iter(o.incidents.values()))
        self.assertEqual(inc['state'], 'RECOVERING')
        self.edge.navigator.active = lambda: False
        await o.on_nav({'id': 'g3', 'status': 'ABORTED', 'label': 'Packing B', 'destination': 'B', 'verified': False})
        self.assertEqual(inc['state'], 'ESCALATED')
        self.assertIn('retry failed', self.channel.sent[-1][1])
        self.assertEqual(len(o.llm.requests), 2)  # no second investigation turn

    async def test_model_outage_falls_back_to_bounded_behaviour(self):
        o = self.orch(ModelError('down'))
        await o.on_operator(dm('Please send the robot to Packing B'))
        self.assertEqual(self.edge.tools.calls[0][0], 'navigate_to')
        self.assertEqual(self.edge.tools.calls[0][1]['destination'].lower(), 'packing b')
        self.assertTrue(self.channel.sent[-1][1].startswith('Heading to'))


if __name__ == '__main__':
    unittest.main()
