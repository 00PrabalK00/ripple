"""Per-robot read-only MCP tools and event-resource subscriptions over stdio."""
import argparse
import asyncio
import json
import os
from pathlib import Path
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.server.stdio import stdio_server
from pydantic import AnyUrl
from .queries import FIELDS, query

class EdgeMCP:
    def __init__(self, robot, snapshot):
        self.snapshot = snapshot
        self.uri = AnyUrl('ripple://'+robot+'/events')
        self.server = Server('ripple-edge-'+robot)
        self.subscribers = set()
        self.last_ids = None

        @self.server.list_tools()
        async def tools():
            return [types.Tool(name=name,description='Read '+name.removeprefix('get_').replace('_',' ')+
                ' with observation freshness; read-only.',
                inputSchema={'type':'object','properties':{'request_id':{'type':'string','minLength':1}},
                    'required':['request_id'],'additionalProperties':False},
                annotations=types.ToolAnnotations(readOnlyHint=True,destructiveHint=False)) for name in FIELDS]

        @self.server.call_tool()
        async def call(name, arguments):
            if name not in FIELDS:
                raise ValueError('Unknown read-only tool')
            result = query(self.snapshot(),dict(arguments,tool=name))
            return types.CallToolResult(content=[types.TextContent(type='text',text=result.model_dump_json())],
                structuredContent=result.model_dump(),isError=False)

        @self.server.list_resources()
        async def resources():
            return [types.Resource(uri=self.uri,name='Robot events',mimeType='application/json',
                description='Bounded event history; subscribe for updates, then read again.')]

        @self.server.read_resource()
        async def read(uri):
            self.check_uri(uri)
            return [ReadResourceContents(content=json.dumps(self.snapshot()['events']),mime_type='application/json')]

        @self.server.subscribe_resource()
        async def subscribe(uri):
            self.check_uri(uri)
            self.subscribers.add(self.server.request_context.session)

        @self.server.unsubscribe_resource()
        async def unsubscribe(uri):
            self.check_uri(uri)
            self.subscribers.discard(self.server.request_context.session)

    def check_uri(self, uri):
        if uri != self.uri:raise ValueError('Unknown robot resource')

    async def notify_events(self):
        ids = tuple(e['id'] for e in self.snapshot()['events'])
        if ids == self.last_ids:return
        self.last_ids = ids
        for session in list(self.subscribers):
            try:await asyncio.wait_for(session.send_resource_updated(self.uri),timeout=1.)
            except Exception:self.subscribers.discard(session)

async def serve(profile, rosscope_binary=None):
    import rclpy
    from .observer import Observer
    rclpy.init()
    node = Observer(profile)
    reader = None
    if rosscope_binary:
        from .rosscope import RosScopeReader
        reader=RosScopeReader(rosscope_binary.resolve(),profile.domain_id)
        reader.start()
    def snapshot():
        data=node.snapshot()
        if reader:data['observations']['rosscope']=reader.snapshot().model_dump()
        return data
    edge=EdgeMCP(profile.robot,snapshot)
    async def observe():
        while True:
            rclpy.spin_once(node,timeout_sec=0)
            await edge.notify_events()
            await asyncio.sleep(.01)
    worker=asyncio.create_task(observe())
    try:
        async with stdio_server() as (incoming,outgoing):
            await edge.server.run(incoming,outgoing,edge.server.create_initialization_options())
    finally:
        worker.cancel()
        try:await worker
        except asyncio.CancelledError:pass
        if reader:reader.close()
        node.destroy_node()
        rclpy.shutdown()

def main():
    from .profile import load_profile
    parser=argparse.ArgumentParser()
    parser.add_argument('--profile',required=True)
    parser.add_argument('--rosscope-binary',type=Path)
    args=parser.parse_args()
    profile=load_profile(args.profile)
    os.environ['ROS_DOMAIN_ID']=str(profile.domain_id)
    asyncio.run(serve(profile,args.rosscope_binary))

if __name__=='__main__':main()
