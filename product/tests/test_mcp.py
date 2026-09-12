import asyncio
import json
import unittest
from mcp.shared.memory import create_connected_server_and_client_session
from ripple_edge.mcp_server import EdgeMCP

class MCPTests(unittest.IsolatedAsyncioTestCase):
    async def test_wire_tools_resources_notifications_and_denials(self):
        state={'robot':'test','observations':{},'events':[],'findings':[]}
        edge=EdgeMCP('test',lambda:state)
        received=asyncio.Event()
        async def message(message):
            if getattr(getattr(message,'root',None),'method',None)=='notifications/resources/updated':
                received.set()
        async with create_connected_server_and_client_session(edge.server,message_handler=message) as client:
            tools=await client.list_tools()
            self.assertEqual(len(tools.tools),6)
            self.assertTrue(all(t.annotations.readOnlyHint for t in tools.tools))
            result=await client.call_tool('get_pose',{'request_id':'test'})
            self.assertEqual(result.structuredContent['status'],'unknown')
            invalid=await client.call_tool('get_pose',{'request_id':'test','shell':'move'})
            self.assertTrue(invalid.isError)
            motion=await client.call_tool('set_goal',{'station':'B'})
            self.assertTrue(motion.isError)
            await client.subscribe_resource(edge.uri)
            state['events']=[{'id':'event1','kind':'cause_changed'}]
            await edge.notify_events()
            await asyncio.wait_for(received.wait(),2)
            data=await client.read_resource(edge.uri)
            self.assertEqual(json.loads(data.contents[0].text)[0]['id'],'event1')
            await client.unsubscribe_resource(edge.uri)
            self.assertFalse(edge.subscribers)
