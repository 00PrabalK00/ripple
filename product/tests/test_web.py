import asyncio
import unittest
from starlette.testclient import TestClient
from test_orchestrator import make
from ripple_agent.orchestrator import Orchestrator
from ripple_agent.web import build_app


class WebTests(unittest.TestCase):
    def setUp(self):
        self.edge = make()
        self.edge.keepouts.map = None
        self.orch = Orchestrator(self.edge, None, escalation=None)
        self.orch.loop = asyncio.new_event_loop()
        self.client = TestClient(build_app(self.orch, self.edge, 8060, test_api=False), base_url='http://127.0.0.1:8060')

    def tearDown(self):
        self.orch.loop.close()

    def test_only_local_hosts_are_served(self):
        self.assertEqual(self.client.get('/api/lessons').status_code, 200)
        self.assertEqual(self.client.get('/api/lessons', headers={'host': 'evil.example'}).status_code, 400)

    def test_posts_need_json_and_a_local_origin(self):
        self.assertEqual(self.client.post('/api/ask', content='text=go', headers={'content-type': 'text/plain'}).status_code, 415)
        self.assertEqual(self.client.post('/api/ask', json={'text': 'go'}, headers={'origin': 'http://evil.example'}).status_code, 403)
        self.assertEqual(self.client.post('/api/ask', json={'text': '   '}).status_code, 400)
        r = self.client.post('/api/ask', json={'text': 'Send the robot to B', 'selection': [0.1, 0.1, 0.2, 0.2]},
                             headers={'origin': 'http://127.0.0.1:8060'})
        self.assertEqual(r.json(), {'queued': True})
        kind, msg = self.orch.queue.get_nowait()
        self.assertEqual((kind, msg['text'], msg['selection']), ('operator', 'Send the robot to B', [0.1, 0.1, 0.2, 0.2]))

    def test_the_test_api_is_off_unless_enabled(self):
        self.assertEqual(self.client.post('/api/test/tool', json={'name': 'robot_status'}).status_code, 404)
        self.assertEqual(self.client.post('/api/test/incident', json={}).status_code, 404)

    def test_map_is_unavailable_until_it_arrives(self):
        self.assertEqual(self.client.get('/api/map.png').status_code, 503)

    def test_lessons_can_be_listed_and_forgotten(self):
        lesson = self.orch.memory.record({'id': 'inc-1', 'state': 'RESOLVED', 'verified_arrival': True, 'location': [1.0, 2.0],
                                          'trigger': {'kind': 'halted_while_commanded', 'cause': 'safety_obstacle'},
                                          'goal': {'label': 'B'}, 'human_messages': [],
                                          'actions': [{'tool': 'teleop', 'status': 'ok', 'args': {'direction': 'backward'}}]})
        listed = self.client.get('/api/lessons').json()
        self.assertEqual(listed['lessons'][0]['id'], lesson['id'])
        self.assertIn('Worked: teleop backward', listed['lessons'][0]['summary'])
        self.assertEqual(self.client.post('/api/lessons/forget', json={'id': lesson['id']}).json(), {'forgotten': True})
        self.assertEqual(self.client.get('/api/lessons').json()['lessons'], [])


if __name__ == '__main__':
    unittest.main()
