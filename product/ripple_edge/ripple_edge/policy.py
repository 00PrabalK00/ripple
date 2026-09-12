"""Deterministic edge policy. Model inputs cannot set budgets, ownership or approvals."""
import hashlib
import json
from .contracts import RecoveryRequest

class RecoveryPolicy:
    def __init__(self, profile, journal, exclusive_owner, authorized=lambda request:False):
        self.profile=profile
        self.journal=journal
        self.exclusive_owner=exclusive_owner
        self.authorized=authorized
        self.incidents=set()
        self.owned_goals=set()

    def register_incident(self,event):
        # Called only by the edge event path, never by an MCP tool argument.
        if event.robot!=self.profile.robot:raise ValueError('Wrong robot event')
        self.incidents.add(event.id)

    def claim(self, request, facts, *, reserve=True):
        r=RecoveryRequest.model_validate(request)
        def deny(reason):return {'claimed':False,'reason':reason}
        if not self.exclusive_owner():return deny('exclusive_owner_required')
        if r.tool!='cancel_goal' and r.incident_id not in self.incidents:return deny('unknown_incident')
        if r.tool=='cancel_goal':
            if r.target not in self.owned_goals:return deny('goal_not_owned')
            # Stop does not depend on fresh safety/localization and is never prevented
            # by an incident budget. Duplicate cancellation remains idempotent.
            key='cancel_goal:'+r.target;limit=1000000
        else:
            def value(key):
                obs=facts.get(key)
                return obs.value if obs and obs.fresh else None
            mode=value('mode')
            if mode not in ('zones','autonomous'):return deny('mode_manual_or_unknown')
            safety=value('safety')
            if self.profile.safety.required:
                if not safety or not safety.get('known'):return deny('safety_unknown')
                if any(safety.get(k) for k in ('emergency','obstacle','localization','override')):
                    return deny('safety_holding_or_override')
            if value('localization') is not True:return deny('localization_unknown_or_degraded')
            if r.tool=='clear_costmap':
                if r.target not in ('local','global'):return deny('undeclared_costmap')
                policy=self.profile.recovery.clear_costmaps
                # One clear per map; the ladder permits local then global, never
                # repeated clearing of the same map under a new request ID.
                key='clear_costmap:'+r.target
            else:
                if r.target not in self.profile.recovery.reset_nodes:return deny('undeclared_lifecycle_node')
                goal=value('goal');odom=value('odometry')
                if goal is None or goal.get('active'):return deny('goal_must_be_confirmed_terminal')
                if odom is None or abs(odom['linear'])>=.02 or abs(odom['angular'])>=.02:return deny('robot_not_settled')
                policy=self.profile.recovery.lifecycle_reset;key='lifecycle_reset'
            if policy.autonomy in ('off','suggest'):return deny('autonomy_'+policy.autonomy)
            if policy.autonomy=='ask' and not self.authorized(r):return deny('human_authorization_required')
            limit=policy.per_incident
        if not reserve:return {'claimed':True}
        fingerprint=hashlib.sha256(json.dumps(r.model_dump(),sort_keys=True).encode()).hexdigest()
        return self.journal.request(op='claim',robot=self.profile.robot,request=r.request_id,
            incident=r.incident_id,action=key,fingerprint=fingerprint,limit=limit)
