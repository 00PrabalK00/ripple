"""Live read-only stdio acceptance probe; run with the ROS environment sourced."""
import asyncio
import json
import os
from pathlib import Path
import sys
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

async def main():
    root=Path(__file__).resolve().parents[2]
    env=dict(os.environ)
    env['PYTHONPATH']=str(root/'product/ripple_edge')+os.pathsep+env.get('PYTHONPATH','')
    params=StdioServerParameters(command=sys.executable,args=['-m','ripple_edge.mcp_server',
        '--profile',str(root/'product/profiles/smr300.yaml')],env=env)
    async with stdio_client(params) as (read,write):
        async with ClientSession(read,write) as client:
            initialized=await client.initialize()
            tools=await client.list_tools()
            await asyncio.sleep(12)
            health=await client.call_tool('get_robot_health',{'request_id':'live-health'})
            pose=await client.call_tool('get_pose',{'request_id':'live-pose'})
            resources=await client.list_resources()
            events=await client.read_resource(resources.resources[0].uri)
            result={'server':initialized.serverInfo.model_dump(),
                'tools':[t.name for t in tools.tools],
                'health':health.structuredContent,'pose':pose.structuredContent,
                'events':json.loads(events.contents[0].text)}
            path=Path('/tmp/ripple-edge-mcp-live.json')
            path.write_text(json.dumps(result,indent=2)+'\n')
            print(json.dumps({'tools':result['tools'],'health_status':result['health']['status'],
                'pose_status':result['pose']['status'],'evidence':str(path)}))

asyncio.run(main())
