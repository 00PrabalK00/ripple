"""Monotonic-time event detection and explicit evidence-based attribution."""
from collections import deque
from datetime import datetime, timezone
import math
from uuid import uuid4
from .contracts import Event, Observation


def parse_smr300(success, text):
    fields={}
    required=('E-Stop','Obstacle Stop','Low Confidence Stop','Manual Override','Control Mode')
    for line in text.splitlines():
        if ':' not in line: continue
        k,v=line.strip().split(':',1)
        if k in required:
            if k in fields: return {'known':False,'reason':'duplicate safety fields'}
            fields[k]=v.strip()
    if not success or any(k not in fields for k in required):return {'known':False,'reason':'missing safety fields'}
    if any(fields[k] not in ('ACTIVE','inactive') for k in required[:3]):
        return {'known':False,'reason':'unrecognized safety stop value'}
    if fields['Manual Override'] not in ('enabled','disabled'):return {'known':False,'reason':'unknown override'}
    return dict(known=True,emergency=fields['E-Stop']=='ACTIVE',obstacle=fields['Obstacle Stop']=='ACTIVE',
                localization=fields['Low Confidence Stop']=='ACTIVE',override=fields['Manual Override']=='enabled',
                mode=fields['Control Mode'])

class Detector:
    def __init__(self, profile, clock):
        self.profile,self.clock=profile,clock
        self.facts={};self.samples=deque();self.failures=deque();self.failed_ids=set()
        self.latches=set();self.goal_id=None;self.degraded_since=None;self.started=clock()

    def observe(self,key,value,source,max_age):
        self.facts[key]=(value,source,self.clock(),max_age)

    def snapshot(self):
        now=self.clock()
        return {k:Observation(value=v,source=src,age_s=max(0,now-at),fresh=0<=now-at<=age)
                for k,(v,src,at,age) in self.facts.items()}

    def failure(self,goal_id):
        if goal_id not in self.failed_ids:
            self.failed_ids.add(goal_id);self.failures.append((self.clock(),goal_id))

    def cause(self, facts):
        def val(key):return facts[key].value if key in facts and facts[key].fresh else None
        safety=val('safety');mode=val('mode')
        if mode=='manual' or (safety and safety.get('mode')=='manual'):return 'manual_control'
        if safety and safety.get('known'):
            if safety['emergency']:return 'emergency_stop'
            if safety['obstacle']:return 'safety_obstacle'
            if safety['localization']:return 'localization_stop'
            if safety['override']:return 'unknown'
        elif self.profile.safety.required:return 'unknown'
        if mode not in ('zones','autonomous'):return 'unknown'
        winner=self.winner(facts)
        if winner in ('joystick','safety','tracker'):return 'unknown'
        if val('localization') is not True:return 'unknown'
        lifecycle=val('lifecycle')
        if not lifecycle:return 'unknown'
        states=[lifecycle.get(n) for n in self.profile.navigation.lifecycle_nodes]
        if any(s is None or s=='unknown' for s in states):return 'unknown'
        if any(s!='active' for s in states):return 'component_down'
        return 'nav2_stall'

    def winner(self,facts):
        inputs=[(cfg.priority,name) for name,cfg in self.profile.motion_inputs.items()
                if (o:=facts.get('velocity.'+name)) and o.fresh]
        return max(inputs)[1] if inputs else None

    def tick(self):
        now=self.clock();facts=self.snapshot();t=self.profile.triggers;events=[];active=set()
        def value(key):return facts[key].value if key in facts and facts[key].fresh else None
        cause=self.cause(facts)
        def emit(key,kind,why=cause):
            active.add(key)
            if key not in self.latches:
                events.append(Event(id=str(uuid4()),robot=self.profile.robot,kind=kind,cause=why,
                    evidence=facts,observed_at=datetime.now(timezone.utc).isoformat()))
        if cause in ('safety_obstacle','emergency_stop','localization_stop','manual_control'):
            emit('cause:'+cause,'cause_changed')
        goal=value('goal');odom=value('odometry');distance=value('distance_remaining')
        goal_id=goal.get('id') if goal and goal.get('active') else None
        if goal_id!=self.goal_id:self.samples.clear();self.goal_id=goal_id
        if cause=='manual_control':self.samples.clear()
        elif goal_id and odom and isinstance(distance,(int,float)) and math.isfinite(distance):
            if distance<=t.goal_tolerance_m or abs(odom['angular'])>=t.turn_below:
                self.samples.clear()
            else:
                self.samples.append((now,distance,abs(odom['linear'])))
                while self.samples and now-self.samples[0][0]>max(t.flat_for_s,t.halted_for_s)+1:
                    self.samples.popleft()
                for duration,kind in [(t.halted_for_s,'halted_while_commanded'),(t.flat_for_s,'no_progress')]:
                    window=[s for s in self.samples if now-s[0]<=duration+.2]
                    if not window or now-window[0][0]<duration:continue
                    progress=window[0][1]-min(s[1] for s in window)
                    stopped=all(s[2]<t.speed_below for s in window)
                    if progress<t.min_progress_m and (stopped or kind=='no_progress'):
                        emit(kind,kind)
        else:self.samples.clear()
        while self.failures and now-self.failures[0][0]>t.failure_window_s:
            _,gid=self.failures.popleft();self.failed_ids.discard(gid)
        if len(self.failures)>=t.failure_count:emit('failures','nav_failures')
        if value('localization') is not True:
            if self.degraded_since is None:self.degraded_since=now
            if now-self.degraded_since>=t.localization_degraded_s:emit('localization','localization_degraded','unknown')
        else:self.degraded_since=None
        lifecycle=value('lifecycle')
        if now-self.started>5 and (not lifecycle or any(lifecycle.get(n)!='active' for n in self.profile.navigation.lifecycle_nodes)):
            emit('lifecycle','node_not_active','component_down' if lifecycle and any(
                lifecycle.get(n) not in (None,'unknown','active') for n in self.profile.navigation.lifecycle_nodes) else 'unknown')
        self.latches=active
        return events
