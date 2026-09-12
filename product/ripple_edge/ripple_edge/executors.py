"""Narrow ROS-native Level 2 executors. Construct only under exclusive ownership."""
import asyncio
import time
from nav2_msgs.srv import ClearEntireCostmap
from lifecycle_msgs.srv import ChangeState,GetState
from lifecycle_msgs.msg import Transition
from nav_msgs.msg import OccupancyGrid
from map_msgs.msg import OccupancyGridUpdate
from .contracts import RecoveryRequest,ToolResult

async def response(future, timeout=5.):
    deadline=time.monotonic()+timeout
    while not future.done():
        if time.monotonic()>=deadline:
            # A timeout is uncertain, not proof the remote service did nothing.
            future.cancel()
            raise TimeoutError('ROS acknowledgement timed out; do not replay')
        await asyncio.sleep(.02)
    return future.result()

class Level2Executor:
    def __init__(self, observer, policy):
        if not policy.exclusive_owner():raise RuntimeError('Exclusive owner required')
        self.node,self.policy=observer,policy
        self.lock=asyncio.Lock()
        self.costmaps={}
        self.clear={}
        self.handles={}
        for scope,service in observer.profile.navigation.clear_services.items():
            self.clear[scope]=observer.create_client(ClearEntireCostmap,service)
            topic=service.rsplit('/',1)[0]+'/costmap'
            observer.create_subscription(OccupancyGrid,topic,
                lambda msg,s=scope:self.costmaps.__setitem__(s,time.monotonic()),1)
            observer.create_subscription(OccupancyGridUpdate,topic+'_updates',
                lambda msg,s=scope:self.costmaps.__setitem__(s,time.monotonic()),1)
        self.lifecycle={n:(observer.create_client(ChangeState,n+'/change_state'),
                           observer.create_client(GetState,n+'/get_state'))
                        for n in observer.profile.recovery.reset_nodes}

    def permitted(self,r):
        decision=self.policy.claim(r,self.node.detector.snapshot(),reserve=False)
        if not decision['claimed']:raise RuntimeError(decision['reason'])

    async def execute(self,request):
        r=RecoveryRequest.model_validate(request)
        async with self.lock:
            facts=self.node.detector.snapshot()
            decision=await asyncio.to_thread(self.policy.claim,r,facts)
            if not decision['claimed']:
                return self.result(r,'denied',decision['reason'],False)
            try:
                self.permitted(r) # Re-read after the durable claim; facts may have expired.
                if r.tool=='clear_costmap':await self.clear_costmap(r)
                elif r.tool=='lifecycle_reset':await self.reset(r)
                else:await self.cancel(r)
                result=self.result(r,'ok','ROS operation and postcondition observed; recovery progress must be assessed separately',True)
            except Exception as exc:
                result=self.result(r,'unknown',str(exc),False)
            # Claim remains spent if journaling fails; never retry the ROS mutation.
            await asyncio.to_thread(self.policy.journal.request,op='finish',robot=self.policy.profile.robot,
                request=r.request_id,status=result.status,result=result.model_dump())
            return result

    def result(self,r,status,reason,verified):
        return ToolResult(robot=self.policy.profile.robot,request_id=r.request_id,status=status,
            reason=reason,observations=self.node.detector.snapshot(),event_ids=[],verified=verified)

    async def clear_costmap(self,r):
        client=self.clear.get(r.target)
        if client is None or not client.service_is_ready():raise RuntimeError('Declared costmap service unavailable')
        await response(client.call_async(ClearEntireCostmap.Request()))
        acknowledged=time.monotonic()
        deadline=acknowledged+5
        while self.costmaps.get(r.target,0)<=acknowledged:
            if time.monotonic()>=deadline:raise TimeoutError('No post-clear costmap received')
            await asyncio.sleep(.05)
        self.permitted(r)

    async def reset(self,r):
        change,get=self.lifecycle[r.target]
        if not change.service_is_ready() or not get.service_is_ready():raise RuntimeError('Declared lifecycle service unavailable')
        initial=(await response(get.call_async(GetState.Request()))).current_state.label
        steps={'active':[(Transition.TRANSITION_DEACTIVATE,'inactive'),(Transition.TRANSITION_CLEANUP,'unconfigured')],
               'inactive':[(Transition.TRANSITION_CLEANUP,'unconfigured')], 'unconfigured':[]}
        if initial not in steps:raise RuntimeError('Unsupported lifecycle state')
        sequence=steps[initial]+[(Transition.TRANSITION_CONFIGURE,'inactive'),(Transition.TRANSITION_ACTIVATE,'active')]
        for transition,expected in sequence:
            self.permitted(r)
            req=ChangeState.Request();req.transition.id=transition
            if not (await response(change.call_async(req))).success:raise RuntimeError('Lifecycle transition refused')
            state=(await response(get.call_async(GetState.Request()))).current_state.label
            if state!=expected:raise RuntimeError('Lifecycle transition verification failed')
        # Planner probe belongs to the recovery ladder; active alone is not recovery.

    async def cancel(self,r):
        handle=self.handles.get(r.target)
        if handle is None:raise RuntimeError('Owned goal handle unavailable; cancellation unverified')
        await response(handle.cancel_goal_async())
        terminal=await response(handle.get_result_async(),10.)
        if terminal.status not in (4,5,6):raise RuntimeError('Goal did not reach terminal state')
        settled=None;deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            odom=self.node.detector.snapshot().get('odometry')
            stopped=odom and odom.fresh and abs(odom.value['linear'])<.02 and abs(odom.value['angular'])<.02
            if stopped:
                if settled is None:settled=time.monotonic()
                if time.monotonic()-settled>=.3:return
            else:settled=None
            await asyncio.sleep(.05)
        raise TimeoutError('Post-cancel settled odometry not observed')
