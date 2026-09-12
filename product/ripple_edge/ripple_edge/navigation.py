"""Owned Nav2 navigation: dispatch checks, route probe, goal tracking and verified arrival."""
import asyncio
import math
import time
from collections import deque
from datetime import datetime, timezone
from action_msgs.msg import GoalStatus
from geometry_msgs.msg import PoseStamped
from nav2_msgs.action import ComputePathToPose, NavigateToPose
from nav_msgs.msg import Path
from rclpy.action import ActionClient
from .executors import response

OUTCOME = {GoalStatus.STATUS_SUCCEEDED: 'SUCCEEDED', GoalStatus.STATUS_ABORTED: 'ABORTED',
           GoalStatus.STATUS_CANCELED: 'CANCELED'}
ACTIVE = ('SENDING', 'EXECUTING', 'CANCELLING')


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def thin(points, limit=160):
    step = max(1, math.ceil(len(points) / limit))
    kept = points[::step]
    if points and kept[-1] != points[-1]:
        kept.append(points[-1])
    return kept


class Navigator:
    def __init__(self, node, profile, detector, ownership, keepouts):
        nav = profile.navigation
        self.node, self.profile, self.detector = node, profile, detector
        self.ownership, self.keepouts = ownership, keepouts
        self.client = ActionClient(node, NavigateToPose, nav.navigate_action)
        self.planner = ActionClient(node, ComputePathToPose, nav.planner_action)
        node.create_subscription(Path, nav.plan_topic, self._plan, 1)
        self.goal = None
        self.handles = {}
        self.history = deque(maxlen=30)
        self.plan = None
        self.listeners = []
        self.owned = lambda goal_id, handle: None
        self.loop = None

    def _plan(self, msg):
        self.plan = (thin([(p.pose.position.x, p.pose.position.y) for p in msg.poses]), time.monotonic())

    def active(self):
        return self.goal is not None and self.goal['status'] in ACTIVE

    def public(self, record=None):
        record = self.goal if record is None else record
        return None if record is None else {k: v for k, v in record.items() if k != 'started'}

    def target(self, dest):
        pose = PoseStamped()
        pose.header.frame_id = dest['frame']  # zero stamp: use the latest transform
        pose.pose.position.x, pose.pose.position.y = float(dest['x']), float(dest['y'])
        pose.pose.orientation.z = math.sin(float(dest['yaw']) / 2)
        pose.pose.orientation.w = math.cos(float(dest['yaw']) / 2)
        return pose

    def _val(self, key):
        o = self.detector.snapshot().get(key)
        return o.value if o is not None and o.fresh else None

    async def probe(self, dest):
        """Read-only planner request from the current pose; no motion."""
        if not self.planner.server_is_ready():
            return {'ok': False, 'reason': 'planner_unavailable'}
        goal = ComputePathToPose.Goal()
        goal.goal = self.target(dest)
        goal.use_start = False
        handle = await response(self.planner.send_goal_async(goal), 5.)
        if not handle.accepted:
            return {'ok': False, 'reason': 'planner_rejected_request'}
        result = await response(handle.get_result_async(), 30.)
        poses = [(p.pose.position.x, p.pose.position.y) for p in result.result.path.poses]
        ok = result.status == GoalStatus.STATUS_SUCCEEDED and len(poses) >= 2
        length = sum(math.dist(a, b) for a, b in zip(poses, poses[1:]))
        return {'ok': ok, 'reason': 'path_found' if ok else 'no_valid_path', 'points': len(poses),
                'length_m': round(length, 2), 'path': thin(poses, 80)}

    def checks(self, dest):
        if dest is None:
            return ['station_registered']
        failures = []
        if not dest.get('available', True):
            failures.append('station_unavailable')
        if self._val('localization') is not True or self._val('pose') is None or self._val('odometry') is None:
            failures.append('facts_fresh')
        if self.profile.safety.required:
            safety = self._val('safety')
            if not safety or not safety.get('known') or any(
                    safety.get(k) for k in ('emergency', 'obstacle', 'localization', 'override')):
                failures.append('safety_not_holding')
        if self._val('mode') not in ('zones', 'autonomous'):
            failures.append('mode_not_manual')
        if not self.ownership.available():
            failures.append('single_owner')
        if not self.keepouts.all_verified():
            failures.append('keepouts_verified')
        return failures

    async def dispatch(self, key, dest, authorization_id, reason, attempt='command'):
        if self.active():
            stopped = await self.cancel('replaced by a new command')
            if stopped['status'] != 'ok':
                return {'status': 'failed', 'reason': 'could not stop the current goal: ' + stopped['reason']}
        failures = self.checks(dest)
        if failures:
            return {'status': 'denied', 'reason': 'dispatch checks failed: ' + ', '.join(failures),
                    'failed_checks': failures}
        probe = await self.probe(dest)
        if not probe['ok']:
            return {'status': 'denied', 'reason': 'route probe failed: ' + probe['reason'],
                    'failed_checks': ['route_probe_ok'], 'probe': {k: v for k, v in probe.items() if k != 'path'}}
        record = dict(id=None, destination=key, label=dest['label'],
                      target={k: dest[k] for k in ('frame', 'x', 'y', 'yaw')},
                      authorization_id=authorization_id, reason=reason, attempt=attempt,
                      started_at=now_iso(), status='SENDING', distance_remaining=None, final_pose=None,
                      verified=False, outcome_reason=None, started=time.monotonic(),
                      route_length_m=probe['length_m'])
        self.goal = record
        goal = NavigateToPose.Goal()
        goal.pose = self.target(dest)
        try:
            handle = await response(self.client.send_goal_async(
                goal, feedback_callback=lambda m, r=record: self._feedback(r, m)), 5.)
        except Exception as exc:
            record['status'], record['outcome_reason'] = 'UNCERTAIN', str(exc)
            return {'status': 'unknown', 'reason': 'goal send uncertain; do not resend blindly: ' + str(exc)}
        if not handle.accepted:
            record['status'], record['outcome_reason'] = 'REJECTED', 'Nav2 rejected the goal'
            record['stopped'] = True
            self._finish(record)
            return {'status': 'failed', 'reason': 'Nav2 rejected the goal'}
        record['id'] = bytes(handle.goal_id.uuid).hex()
        record['status'] = 'EXECUTING'
        self.handles[record['id']] = handle
        self.owned(record['id'], handle)
        handle.get_result_async().add_done_callback(lambda f, r=record: self._result(r, f))
        return {'status': 'ok', 'reason': 'Nav2 accepted the goal', 'goal_id': record['id'],
                'route_length_m': probe['length_m']}

    def _feedback(self, record, msg):
        d = msg.feedback.distance_remaining
        if math.isfinite(d):
            record['distance_remaining'] = round(float(d), 2)

    def _result(self, record, future):
        try:
            record['status'] = OUTCOME.get(future.result().status, 'UNCERTAIN')
        except Exception as exc:
            record['status'], record['outcome_reason'] = 'UNCERTAIN', str(exc)
        if self.loop:
            self.loop.call_soon_threadsafe(lambda: asyncio.ensure_future(self._settle(record)))

    async def settled(self, seconds=6.0):
        deadline, since = time.monotonic() + seconds, None
        while time.monotonic() < deadline:
            odom = self._val('odometry')
            if odom is not None and abs(odom['linear']) < .02 and abs(odom['angular']) < .02:
                since = since or time.monotonic()
                if time.monotonic() - since >= .5:
                    return True
            else:
                since = None
            await asyncio.sleep(.05)
        return False

    async def _settle(self, record):
        stopped = await self.settled()
        pose = self._val('pose')
        record['final_pose'] = dict(pose) if pose else None
        if record['status'] == 'SUCCEEDED':
            t = record['target']
            near = pose is not None and math.dist((pose['x'], pose['y']), (t['x'], t['y'])) <= \
                self.profile.triggers.goal_tolerance_m + .15
            record['verified'] = bool(stopped and near)
            record['outcome_reason'] = ('arrived: settled odometry and pose within tolerance' if record['verified']
                                        else 'Nav2 reported success but arrival could not be verified')
        else:
            record['outcome_reason'] = record['outcome_reason'] or 'Nav2 ' + record['status'].lower()
        record['stopped'] = stopped
        self._finish(record)

    def _finish(self, record):
        self.handles.pop(record.get('id'), None)
        self.history.appendleft(self.public(record))
        for listener in list(self.listeners):
            try:
                listener(self.public(record))
            except Exception:
                pass

    async def cancel(self, reason):
        record = self.goal
        if not self.active():
            return {'status': 'ok', 'reason': 'no active goal'}
        handle = self.handles.get(record['id']) if record['id'] else None
        if handle is None:
            return {'status': 'unknown', 'reason': 'goal handle unavailable; cancellation unverified'}
        record['status'], record['outcome_reason'] = 'CANCELLING', reason
        try:
            await response(handle.cancel_goal_async(), 5.)
        except Exception as exc:
            return {'status': 'unknown', 'reason': 'cancel acknowledgement uncertain: ' + str(exc)}
        deadline = time.monotonic() + 15
        while record['status'] == 'CANCELLING' or 'stopped' not in record:
            if time.monotonic() > deadline:
                return {'status': 'unknown', 'reason': 'no terminal result after cancel'}
            await asyncio.sleep(.1)
        return {'status': 'ok' if record['stopped'] else 'unknown',
                'reason': 'goal ' + record['status'].lower() + '; ' +
                          ('robot stopped' if record['stopped'] else 'stop not confirmed')}
