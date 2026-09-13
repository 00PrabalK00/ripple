"""Owned Nav2 navigation: dispatch checks, route probe, goal tracking and verified arrival.

A goal can pass through via points: the legs are driven in order as one goal record, and
only the final leg is verified as an arrival.
"""
import asyncio
import math
import time
from collections import deque
from datetime import datetime, timezone
import numpy as np
from action_msgs.msg import GoalStatus
from geometry_msgs.msg import PoseStamped
from nav2_msgs.action import ComputePathToPose, NavigateToPose
from nav_msgs.msg import Path
from rclpy.action import ActionClient
from .executors import response
from .geometry import MapGeometry, inside

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


def clearance(geom, grid, x, y, reach=1.5):
    """Metres from map point (x, y) to the nearest inscribed or lethal costmap cell (keepouts included),
    capped at reach; None off the costmap."""
    gx, gy = geom.map_to_grid(x, y)
    gx, gy = int(math.floor(gx)), int(math.floor(gy))
    if not (0 <= gx < geom.width and 0 <= gy < geom.height):
        return None
    r = int(math.ceil(reach / geom.resolution))
    x0, y0 = max(0, gx - r), max(0, gy - r)
    rows, cols = np.nonzero(grid[y0:gy + r + 1, x0:gx + r + 1] >= 99)
    if not len(cols):
        return reach
    return round(min(reach, float(np.hypot(cols + x0 - gx, rows + y0 - gy).min()) * geom.resolution), 2)


def nearest_clear(geom, grid, x, y, need, search=1.5):
    """The closest map point within `search` metres of (x, y) with at least `need` clearance, or None."""
    gx, gy = geom.map_to_grid(x, y)
    r = int(math.ceil(search / geom.resolution))
    offsets = sorted(((dx, dy) for dx in range(-r, r + 1, 2) for dy in range(-r, r + 1, 2) if dx * dx + dy * dy <= r * r),
                     key=lambda d: d[0] * d[0] + d[1] * d[1])
    for dx, dy in offsets:
        px, py = geom.grid_to_map(gx + dx, gy + dy)
        c = clearance(geom, grid, px, py, need + .05)
        if c is not None and c >= need:
            return round(px, 2), round(py, 2)
    return None


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
        self._grid = None

    def _plan(self, msg):
        self.plan = (thin([(p.pose.position.x, p.pose.position.y) for p in msg.poses]), time.monotonic())

    def active(self):
        return self.goal is not None and self.goal['status'] in ACTIVE

    def public(self, record=None):
        record = self.goal if record is None else record
        return None if record is None else {k: v for k, v in record.items() if k not in ('started', 'legs')}

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

    def costmap(self):
        """(geometry, cost grid) of the latest global costmap, rows by gy; None before one arrives."""
        sample = self.keepouts.costmap
        if sample is None:
            return None
        if self._grid is None or self._grid[0] is not sample[0]:
            msg = sample[0]
            self._grid = (msg, MapGeometry.from_info(msg.info),
                          np.asarray(msg.data, dtype=np.int16).reshape(msg.info.height, msg.info.width))
        return self._grid[1:]

    def check_via(self, points):
        """Every via point must be on the costmap, outside keepouts, with room for the robot around it."""
        need = self.profile.navigation.via_min_clearance_m
        got = self.costmap()
        if got is None:
            return [{'point': 1, 'problem': 'global costmap not received yet'}]
        geom, grid = got
        problems = []
        for i, (x, y) in enumerate(points, 1):
            zone = next((k for k in self.keepouts.site.active_keepouts() if inside((x, y), k['polygon'])), None)
            c = clearance(geom, grid, x, y)
            if zone:
                problem = 'inside keepout ' + (zone.get('name') or zone['id'])
            elif c is None:
                problem = 'off the map'
            elif c < need:
                problem = f'only {c} m from an obstacle, wall or keepout; needs {need} m'
            else:
                continue
            better = nearest_clear(geom, grid, x, y, need)
            problems.append({'point': i, 'x': x, 'y': y, 'problem': problem, 'clearance_m': c,
                             'nearest_clear_point': better and {'x': better[0], 'y': better[1]}})
        return problems

    async def probe(self, dest, start=None):
        """Read-only planner request from the current pose (or from `start`); no motion."""
        if not self.planner.server_is_ready():
            return {'ok': False, 'reason': 'planner_unavailable'}
        goal = ComputePathToPose.Goal()
        goal.goal = self.target(dest)
        goal.use_start = start is not None
        if start is not None:
            goal.start = self.target(start)
        # One retry after a short pause: a single planner miss while the costmap updates is not a blocked route.
        for attempt in range(2):
            handle = await response(self.planner.send_goal_async(goal), 5.)
            if not handle.accepted:
                return {'ok': False, 'reason': 'planner_rejected_request'}
            result = await response(handle.get_result_async(), 30.)
            poses = [(p.pose.position.x, p.pose.position.y) for p in result.result.path.poses]
            ok = result.status == GoalStatus.STATUS_SUCCEEDED and len(poses) >= 2
            if ok or attempt:
                break
            await asyncio.sleep(2.0)
        length = sum(math.dist(a, b) for a, b in zip(poses, poses[1:]))
        out = {'ok': ok, 'reason': 'path_found' if ok else 'no_valid_path', 'points': len(poses),
               'length_m': round(length, 2), 'path': thin(poses, 80)}
        got = self.costmap() if ok else None
        if got:
            # Where the path passes closest to obstacles, excluding its first and last half metre (start and dock).
            geom, grid = got
            travelled, gaps = 0.0, []
            for a, b in zip(poses, poses[1:]):
                travelled += math.dist(a, b)
                if .5 < travelled < length - .5:
                    gaps.append((clearance(geom, grid, *b), b))
            gaps = [(c, p) for c, p in gaps[::3] if c is not None]
            if gaps:
                c, (x, y) = min(gaps)
                out.update(min_clearance_m=c, tight_spot={'x': round(x, 2), 'y': round(y, 2)})
            out['waypoints'] = [{'x': round(x, 2), 'y': round(y, 2)} for x, y in thin(poses, 8)]
        return out

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

    async def dispatch(self, key, dest, authorization_id, reason, attempt='command', via=None):
        if self.active():
            stopped = await self.cancel('replaced by a new command')
            if stopped['status'] != 'ok':
                return {'status': 'failed', 'reason': 'could not stop the current goal: ' + stopped['reason']}
        failures = self.checks(dest)
        if failures:
            return {'status': 'denied', 'reason': 'dispatch checks failed: ' + ', '.join(failures),
                    'failed_checks': failures}
        target = {k: dest[k] for k in ('frame', 'x', 'y', 'yaw')}
        legs = []
        for i, (x, y) in enumerate(via or []):
            nx, ny = via[i + 1] if i + 1 < len(via) else (target['x'], target['y'])
            legs.append({'frame': target['frame'], 'x': x, 'y': y, 'yaw': math.atan2(ny - y, nx - x)})
        legs.append(target)
        # Every leg must plan before the robot moves; each later leg is planned from the previous point.
        length, start = 0.0, None
        for i, leg in enumerate(legs):
            probe = await self.probe(leg, start)
            if not probe['ok']:
                where = f' on leg {i + 1} of {len(legs)}' if len(legs) > 1 else ''
                return {'status': 'denied', 'reason': 'route probe failed' + where + ': ' + probe['reason'],
                        'failed_checks': ['route_probe_ok'], 'probe': {k: v for k, v in probe.items() if k != 'path'}}
            length += probe['length_m']
            start = leg
        record = dict(id=None, destination=key, label=dest['label'], target=target,
                      authorization_id=authorization_id, reason=reason, attempt=attempt,
                      started_at=now_iso(), status='SENDING', distance_remaining=None, final_pose=None,
                      verified=False, outcome_reason=None, started=time.monotonic(),
                      route_length_m=round(length, 2), via=[{'x': x, 'y': y} for x, y in via] if via else None,
                      leg=0, legs=legs, leg_ids=[])
        self.goal = record
        try:
            accepted, why = await self._send(record, legs[0])
        except Exception as exc:
            record['status'], record['outcome_reason'] = 'UNCERTAIN', str(exc)
            return {'status': 'unknown', 'reason': 'goal send uncertain; do not resend blindly: ' + str(exc)}
        if not accepted:
            record['status'], record['outcome_reason'] = 'REJECTED', why
            record['stopped'] = True
            self._finish(record)
            return {'status': 'failed', 'reason': why}
        record['status'] = 'EXECUTING'
        out = {'status': 'ok', 'reason': 'Nav2 accepted the goal', 'goal_id': record['id'],
               'route_length_m': record['route_length_m']}
        if via:
            out.update(reason=f'Nav2 accepted leg 1 of {len(legs)}', via=record['via'])
        return out

    async def _send(self, record, leg):
        goal = NavigateToPose.Goal()
        goal.pose = self.target(leg)
        handle = await response(self.client.send_goal_async(
            goal, feedback_callback=lambda m, r=record: self._feedback(r, m)), 5.)
        if not handle.accepted:
            return False, 'Nav2 rejected the goal'
        gid = bytes(handle.goal_id.uuid).hex()
        record['id'] = record['id'] or gid  # the record keeps its first goal ID across legs
        record['handle'] = gid
        record['leg_ids'].append(gid)
        self.handles[gid] = handle
        self.owned(gid, handle)
        index = record['leg']
        handle.get_result_async().add_done_callback(lambda f, r=record, i=index: self._result(r, f, i))
        return True, 'accepted'

    def _feedback(self, record, msg):
        d = msg.feedback.distance_remaining
        if math.isfinite(d):
            record['distance_remaining'] = round(float(d), 2)

    def _result(self, record, future, leg=0):
        try:
            status = OUTCOME.get(future.result().status, 'UNCERTAIN')
        except Exception as exc:
            status, record['outcome_reason'] = 'UNCERTAIN', str(exc)
        if status == 'SUCCEEDED' and leg + 1 < len(record.get('legs') or ()) and record['status'] == 'EXECUTING':
            record['leg'] = leg + 1  # a via point reached: drive the next leg
            then = self._next_leg
        else:
            record['status'] = status
            then = self._settle
        if self.loop:
            self.loop.call_soon_threadsafe(lambda: asyncio.ensure_future(then(record)))

    async def _next_leg(self, record):
        if record is not self.goal or record['status'] != 'EXECUTING':
            return
        try:
            accepted, why = await self._send(record, record['legs'][record['leg']])
        except Exception as exc:
            accepted, why = False, str(exc)
        if not accepted:
            record['status'] = 'ABORTED'
            record['outcome_reason'] = f"leg {record['leg'] + 1} of {len(record['legs'])} not accepted: {why}"
            await self._settle(record)

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
        self.handles.pop(record.get('handle') or record.get('id'), None)
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
        handle = self.handles.get(record.get('handle') or record['id']) if record['id'] else None
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
