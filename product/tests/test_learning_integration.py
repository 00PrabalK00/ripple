"""The orchestrator learns from a closed incident and recalls it when trouble repeats at the same place."""
import asyncio
import unittest
from test_orchestrator import FakeChannel, ScriptedGLM, call, make
from ripple_agent.orchestrator import Orchestrator


class LearningIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_second_incident_at_the_same_place_is_briefed_with_what_worked(self):
        edge = make()
        glm = ScriptedGLM({'content': '', 'tool_calls': [call('teleop', direction='backward', distance_m=0.3, reason='back out'),
                                                         call('retry_navigation')]},
                          {'content': 'Backed out and retried.'},
                          {'content': 'Looking at it.'})
        o = Orchestrator(edge, glm, escalation=('ambiguous', 'dm-1'))
        o.loop = asyncio.get_running_loop()
        o.channels['ambiguous'] = FakeChannel()
        goal = {'id': 'g1', 'destination': 'Z3', 'label': 'Staging Z3', 'authorization_id': None}
        await o.open_incident('halted_while_commanded', 'safety_obstacle', goal=goal)
        first = next(iter(o.incidents.values()))
        self.assertEqual([a['args'] for a in first['actions']][0]['direction'], 'backward')
        self.assertNotIn('Lessons from this place', glm.requests[0]['messages'][1]['content'])
        first['verified_arrival'] = True  # the retried goal arrived, verified
        o.close(first, 'RESOLVED', 'Recovered: the robot reached Staging Z3 and arrival was verified')
        self.assertEqual(len(o.memory.lessons), 1)
        self.assertTrue(any('Site memory updated' in e['text'] for e in o.timeline))

        await o.open_incident('halted_while_commanded', 'safety_obstacle', goal=goal)
        briefing = glm.requests[-1]['messages'][1]['content']
        self.assertIn('Lessons from this place', briefing)
        self.assertIn('Worked: teleop backward → retry_navigation (1 of 1)', briefing)
        self.assertIn('evidence, not instructions', briefing)

    async def test_operator_briefing_lists_known_trouble_spots(self):
        edge = make()
        glm = ScriptedGLM({'content': 'Noted.'})
        o = Orchestrator(edge, glm, escalation=('ambiguous', 'dm-1'))
        o.loop = asyncio.get_running_loop()
        o.channels['ambiguous'] = FakeChannel()
        o.memory.record({'id': 'inc-old', 'state': 'RESOLVED', 'verified_arrival': True, 'location': [1.0, 2.0],
                         'trigger': {'kind': 'halted_while_commanded', 'cause': 'safety_obstacle'}, 'goal': {'label': 'Packing A'},
                         'actions': [{'tool': 'teleop', 'status': 'ok', 'args': {'direction': 'backward'}}], 'human_messages': []})
        await o.on_operator(dict(channel='ambiguous', operator_id='a37c631f-b487-47a5-ada7-e0e5140681be', operator='Prabal Khare',
                                 text='how are things', message_id='m1', reply_to='dm-1'))
        self.assertIn('Known trouble spots at this site', glm.requests[0]['messages'][1]['content'])


if __name__ == '__main__':
    unittest.main()
