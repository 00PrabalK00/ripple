"""Explicit Level 2 integration test: synthetic incident, real costmap clear, no motion."""
import asyncio
import json
import os
from pathlib import Path
from uuid import uuid4
from dotenv import dotenv_values
import rclpy
from std_srvs.srv import Empty
from ripple_edge.profile import load_profile
from ripple_edge.observer import Observer
from ripple_edge.ownership import Ownership
from ripple_edge.journal import ActionJournal
from ripple_edge.policy import RecoveryPolicy
from ripple_edge.executors import Level2Executor,response
from ripple_edge.contracts import Event

async def main():
    root=Path(__file__).resolve().parents[2]
    profile=load_profile(root/'product/profiles/smr300.yaml')
    os.environ['ROS_DOMAIN_ID']=str(profile.domain_id)
    rclpy.init();node=Observer(profile);lease=Ownership(node)
    journal=ActionJournal(dotenv_values('/home/zuci/Ripple/.env')['DATABASE_URL'],root)
    policy=RecoveryPolicy(profile,journal,lease.available)
    async def spin():
        while True:
            rclpy.spin_once(node,timeout_sec=0)
            await asyncio.sleep(.002)
    worker=asyncio.create_task(spin())
    try:
        await asyncio.sleep(5)
        if not lease.available():raise RuntimeError('Other navigation client exists; test refused')
        journal.request(op='interrupt',robot=profile.robot)
        executor=Level2Executor(node,policy)
        # Declared no-motion AMCL refresh obtains a fresh stationary laser update.
        refresh=node.create_client(Empty,profile.localization_refresh_service)
        await asyncio.sleep(2)
        if not refresh.service_is_ready():raise RuntimeError('AMCL refresh unavailable')
        await response(refresh.call_async(Empty.Request()))
        await asyncio.sleep(.5)
        incident='integration-test-'+str(uuid4())
        policy.register_incident(Event(id=incident,robot=profile.robot,kind='no_progress',
            cause='nav2_stall',evidence={},observed_at='synthetic integration fixture'))
        request={'request_id':str(uuid4()),'tool':'clear_costmap','incident_id':incident,'target':'local'}
        result=await executor.execute(request)
        retry=await executor.execute(dict(request,request_id=str(uuid4())))
        evidence={'scope':'Synthetic incident fixture; real ROS clear and post-clear publication. No stall or motion claim.',
            'exclusive_owner':lease.available(),'clear':result.model_dump(),'second_attempt':retry.model_dump()}
        (root/'product/evidence/level2-costmap-live.json').write_text(json.dumps(evidence,indent=2)+'\n')
        print(json.dumps({'clear_status':result.status,'verified':result.verified,'second_attempt':retry.reason}))
        assert result.status=='ok' and result.verified,result.reason
        assert retry.status=='denied' and retry.reason=='incident_budget_exhausted',retry.reason
    finally:
        worker.cancel()
        try:await worker
        except asyncio.CancelledError:pass
        journal.close();lease.close();node.destroy_node();rclpy.shutdown()

asyncio.run(main())
