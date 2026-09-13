import json
import unittest
from types import SimpleNamespace
import httpx
from ripple_agent import llm
from ripple_agent.llm import GLM, ModelError


def reply(message, finish='stop'):
    return httpx.Response(200, json={'choices': [{'message': message, 'finish_reason': finish}]})


class GLMTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        async def instant(_):
            return None
        self.real = llm.asyncio
        llm.asyncio = SimpleNamespace(sleep=instant)  # retries without waiting; the test loop keeps real asyncio
        self.requests = []

    async def asyncTearDown(self):
        llm.asyncio = self.real

    def glm(self, *responses):
        queue = list(responses)

        def handler(request):
            self.requests.append(request)
            item = queue.pop(0)
            if isinstance(item, Exception):
                raise item
            return item
        g = GLM(api_key='test-key', model='test/glm')
        g.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        return g

    async def test_tool_calls_are_requested_with_the_key_and_tools(self):
        g = self.glm(reply({'content': '', 'tool_calls': [{'id': 'c1'}]}, 'tool_calls'))
        message, finish = await g.chat([{'role': 'user', 'content': 'hi'}], [{'type': 'function', 'function': {'name': 'x'}}])
        self.assertEqual((message['tool_calls'][0]['id'], finish), ('c1', 'tool_calls'))
        body = json.loads(self.requests[0].content)
        self.assertEqual((body['model'], body['tool_choice'], body['provider']), ('test/glm', 'auto', {'require_parameters': True}))
        self.assertEqual(self.requests[0].headers['authorization'], 'Bearer test-key')

    async def test_rate_limits_are_retried(self):
        g = self.glm(httpx.Response(429), httpx.Response(503), reply({'content': 'ok'}))
        message, _ = await g.chat([])
        self.assertEqual((message['content'], g.calls, g.last_error), ('ok', 1, None))

    async def test_gives_up_after_three_attempts(self):
        g = self.glm(httpx.Response(500), httpx.Response(500), httpx.Response(500))
        with self.assertRaisesRegex(ModelError, 'model unavailable: HTTP 500'):
            await g.chat([])
        self.assertEqual(g.last_error, 'HTTP 500')
        g = self.glm(*[httpx.ConnectError('down')] * 3)
        with self.assertRaisesRegex(ModelError, 'ConnectError'):
            await g.chat([])

    async def test_error_payloads_and_a_missing_key(self):
        g = self.glm(httpx.Response(200, json={'error': {'message': 'no such model'}}))
        with self.assertRaisesRegex(ModelError, 'no such model'):
            await g.chat([])
        g.api_key = None
        with self.assertRaisesRegex(ModelError, 'OPENROUTER_API_KEY is not set'):
            await g.chat([])
        self.assertFalse(g.configured)

    async def test_map_regions_are_parsed_and_incomplete_answers_refused(self):
        region = {'bounds': [0.1, 0.2, 0.3, 0.4], 'explanation': 'the aisle', 'clarification': None}
        g = self.glm(reply({'content': json.dumps(region)}), reply({'content': ''}, 'length'))
        got = await g.locate_region('the aisle', b'\x89PNG')
        self.assertEqual(got.bounds, [0.1, 0.2, 0.3, 0.4])
        body = json.loads(self.requests[0].content)
        self.assertEqual(body['response_format']['json_schema']['name'], 'map_region')
        with self.assertRaisesRegex(ModelError, 'did not complete'):
            await g.locate_region('again', b'\x89PNG')


if __name__ == '__main__':
    unittest.main()
