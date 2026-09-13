"""Bounded escape motion via Nav2's collision-checked BackUp and Spin behaviors.

Never raw velocity: the behaviors publish on Nav2's lowest-priority velocity input,
so the robot's safety controller and any human input still win.
"""
import asyncio
import hashlib
import json
import math
from action_msgs.msg import GoalStatus
from builtin_interfaces.msg import Duration
from nav2_msgs.action import BackUp, Spin
from rclpy.action import ActionClient
from .executors import response

HOLDS = ('emergency', 'obstacle', 'localization', 'override')


class Escape:
    def __init__(self, node, profile, detector, navigator, auths, journal):
        e = profile.recovery.escape
        self.profile, self.detector, self.navigator = profile, detector, navigator
        self.auths, self.journal = auths, journal
        self.clients = {'backup': ActionClient(node, BackUp, e.backup_action),
                        'spin': ActionClient(node, Spin, e.spin_action)}

    def _val(self, facts, key):
        o = facts.get(key)
        return o.value if o is not None and o.fresh else None

    def denial(self, primitive, amount, speed, authorization_id):
        e = self.profile.recovery.escape
        if e.autonomy == 'off':
            return 'escape_disabled' if self.profile.safety.required else 'escape_disabled_no_safety_layer'
        if e.autonomy == 'suggest':
            return 'autonomy_suggest_only'
        if primitive == 'backup':
            if not 0 < amount <= e.max_backup_m or not 0 < speed <= e.max_backup_mps:
                return 'exceeds_profile_limits'
        elif not 0 < abs(amount) <= e.max_spin_rad:
            return 'exceeds_profile_limits'
        facts = self.detector.snapshot()
        safety = self._val(facts, 'safety')
        if 'safety_present' in e.requires and not (safety and safety.get('known')):
            return 'safety_unknown'
        held = [k for k in HOLDS if safety and safety.get(k)]
        if 'safety_not_holding' in e.requires and held:
            return 'safety_holding:' + ','.join(held)
        if 'mode_not_manual' in e.requires and self._val(facts, 'mode') not in ('zones', 'autonomous'):
            return 'mode_manual_or_unknown'
        if 'localization_fresh' in e.requires and self._val(facts, 'localization') is not True:
            return 'localization_unknown_or_degraded'
        goal = self._val(facts, 'goal')
        if 'no_active_goal' in e.requires and (self.navigator.active() or (goal and goal.get('active'))):
            return 'active_goal'
        if primitive == 'backup':
            rear = self._val(facts, 'scan.rear')
            if 'rear_scan_fresh' in e.requires and rear is None:
                return 'rear_scan_stale'
            if rear and rear.get('minimum_m') is not None and rear['minimum_m'] < amount + 0.40:
                return 'rear_obstacle_too_close'
        if e.autonomy == 'ask':
            reason = self.auths.check(authorization_id, 'escape')
            if reason:
                return 'human_authorization_required (' + reason + ')'
        return None

    def _pose(self):
        return self._val(self.detector.snapshot(), 'odometry_pose')

    async def execute(self, incident_id, primitive, amount, speed, authorization_id, request_id):
        e = self.profile.recovery.escape
        why = self.denial(primitive, amount, speed, authorization_id)
        if why:
            return {'status': 'denied', 'reason': why, 'verified': False}
        client = self.clients[primitive]
        if not client.server_is_ready():
            return {'status': 'failed', 'reason': primitive + ' behavior server unavailable', 'verified': False}
        fingerprint = hashlib.sha256(json.dumps([primitive, amount, speed, incident_id]).encode()).hexdigest()
        claim = await asyncio.to_thread(self.journal.request, op='claim', robot=self.profile.robot,
                                        request=request_id, incident=incident_id, action='escape',
                                        fingerprint=fingerprint, limit=e.per_incident)
        if not claim['claimed']:
            return {'status': 'denied', 'reason': claim['reason'], 'verified': False}
        if e.autonomy == 'ask':
            self.auths.consume(authorization_id, 'escape')
        before = self._pose()
        if primitive == 'backup':
            allowance = int(math.ceil(amount / speed)) + 8
            goal = BackUp.Goal()
            goal.target.x, goal.speed = float(amount), float(speed)
        else:
            allowance = 15
            goal = Spin.Goal()
            goal.target_yaw = float(amount)
        goal.time_allowance = Duration(sec=allowance)
        status = None
        try:
            handle = await response(client.send_goal_async(goal), 5.)
            if not handle.accepted:
                outcome = {'status': 'failed', 'reason': 'behavior server rejected the request'}
            else:
                status = (await response(handle.get_result_async(), allowance + 10.)).status
                outcome = {'status': 'ok' if status == GoalStatus.STATUS_SUCCEEDED else 'failed',
                           'reason': 'behavior ' + {GoalStatus.STATUS_SUCCEEDED: 'succeeded',
                                                    GoalStatus.STATUS_ABORTED: 'aborted (collision check or safety stop)',
                                                    GoalStatus.STATUS_CANCELED: 'canceled'}.get(status, 'ended uncertainly')}
        except Exception as exc:
            outcome = {'status': 'unknown', 'reason': 'behavior outcome uncertain: ' + str(exc)}
        await asyncio.sleep(.5)
        after = self._pose()
        moved = None
        if before and after:
            moved = (math.dist((before['x'], before['y']), (after['x'], after['y'])) if primitive == 'backup'
                     else abs(math.remainder(after['yaw'] - before['yaw'], 2 * math.pi)))
        verified = status == GoalStatus.STATUS_SUCCEEDED and moved is not None and moved >= .6 * abs(amount)
        outcome.update(verified=verified, data={'primitive': primitive, 'requested': amount,
                                                'moved': None if moved is None else round(moved, 3)})
        if outcome['status'] == 'ok' and not verified:
            outcome.update(status='unknown', reason=outcome['reason'] + '; odometry did not confirm the motion')
        await asyncio.to_thread(self.journal.request, op='finish', robot=self.profile.robot,
                                request=request_id, status=outcome['status'], result=outcome)
        return outcome
