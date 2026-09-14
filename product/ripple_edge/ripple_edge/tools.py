"""The edge's typed tool API. Every call is validated and policy-checked here, whoever calls.

Levels: observe (always), stop (always), command (needs an operator authorization),
recovery (bounded by incident budgets and profile autonomy). Level 4 actions — raw
velocity, safety overrides, disabling collision checks, shell — do not exist here.
"""
import asyncio
import hashlib
import json
from typing import Literal
from uuid import uuid4
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from .contracts import Event, RecoveryRequest
from .geometry import Region, distance
from .site import now_iso


class Args(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)


class NoArgs(Args):
    pass


class Destination(Args):
    destination: str = Field(min_length=1, max_length=80, description='Registered station or named area, e.g. "B" or "Loading Dock"')


class Navigate(Destination):
    authorization_id: str = Field(min_length=1, description='ID of the operator message that asked for this move')
    reason: str = Field(default='', max_length=300)


class Point(Args):
    x: float = Field(description='Map x, metres')
    y: float = Field(description='Map y, metres')


class NavigateVia(Args):
    destination: str | None = Field(default=None, max_length=80,
                                    description="Final station or area; in an incident, defaults to the incident's goal")
    via: list[Point] = Field(min_length=1, max_length=5,
                             description='Map points to pass through, in order, in open floor away from the tight spot')
    authorization_id: str | None = Field(default=None, description='ID of the operator message (not needed in an incident)')
    incident_id: str | None = None
    reason: str = Field(default='', max_length=300)


class Cancel(Args):
    reason: str = Field(min_length=1, max_length=300)


class RegionSpec(Args):
    area: str | None = Field(default=None, description='A named area')
    image_bounds: list[float] | None = Field(default=None, min_length=4, max_length=4,
                                             description='[left, top, right, bottom], normalized 0..1 on the map image')
    box_m: list[float] | None = Field(default=None, min_length=4, max_length=4,
                                      description='[x1, y1, x2, y2] in map-frame metres')

    @model_validator(mode='after')
    def exactly_one(self):
        if sum(v is not None for v in (self.area, self.image_bounds, self.box_m)) != 1:
            raise ValueError('Give exactly one of area, image_bounds or box_m')
        return self


class AddKeepout(Args):
    region: RegionSpec
    reason: str = Field(min_length=1, max_length=300)
    authorization_id: str = Field(min_length=1)


class RemoveKeepout(Args):
    keepout_id: str = Field(min_length=1)
    authorization_id: str = Field(min_length=1)


class DefineArea(Args):
    name: str = Field(min_length=1, max_length=60)
    region: RegionSpec
    authorization_id: str = Field(min_length=1)


class StationState(Args):
    destination: str = Field(min_length=1, max_length=80)
    available: bool
    reason: str = Field(min_length=1, max_length=300)
    authorization_id: str = Field(min_length=1)


class ClearCostmap(Args):
    incident_id: str = Field(min_length=1)
    costmap: Literal['local', 'global']


class LifecycleReset(Args):
    incident_id: str = Field(min_length=1)
    node: str = Field(min_length=1)
    authorization_id: str | None = None


class EscapeArgs(Args):
    incident_id: str = Field(min_length=1)
    primitive: Literal['backup', 'spin']
    amount: float = Field(description='Metres to back up (positive), or radians to spin (signed)')
    speed: float = Field(default=0.1, gt=0, le=0.3, description='m/s for backup')
    authorization_id: str | None = None


class Retry(Args):
    incident_id: str = Field(min_length=1)


class Logs(Args):
    limit: int = Field(default=15, ge=1, le=50)


class TeleopArgs(Args):
    direction: Literal['auto', 'forward', 'backward', 'rotate_left', 'rotate_right'] = 'auto'
    distance_m: float = Field(default=0.3, gt=0, le=1.0, description='Metres to drive, or for rotations about a third of the radians')
    speed: float = Field(default=0.12, gt=0, le=0.3)
    override_safety: bool = Field(default=False, description='Simulation only: move even while the safety controller holds')
    reason: str = Field(min_length=1, max_length=300)
    incident_id: str | None = None


def inline(schema):
    """Resolve $ref/$defs and drop titles so every model provider accepts the schema."""
    defs = schema.pop('$defs', {})

    def walk(node, names=False):
        if isinstance(node, dict):
            if names:  # a 'properties' map: keys are field names (a field may be called "title"), not keywords
                return {k: walk(v) for k, v in node.items()}
            if '$ref' in node:
                return walk(dict(defs[node['$ref'].split('/')[-1]]))
            return {k: walk(v, k == 'properties') for k, v in node.items() if k != 'title'}
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node
    return walk(schema)


class EdgeTools:
    def __init__(self, rt):
        self.rt = rt
        self.incidents = {}
        self.specs = {
            'robot_status': (NoArgs, self.robot_status, 'observe',
                'Summarized robot state: derived cause of any stop, safety, mode, localization, pose, lifecycle, goal, scans, stale facts.'),
            'get_diagnostics': (NoArgs, self.diagnostics, 'observe',
                'Deeper evidence: Nav2 lifecycle states, RosScope report summary, classified log findings, raw safety fields.'),
            'get_recent_logs': (Logs, self.recent_logs, 'observe',
                'Classified /rosout findings from declared nodes, newest last.'),
            'get_site': (NoArgs, self.get_site, 'observe',
                'Registered stations and areas with availability, and active keepouts with verification.'),
            'probe_route': (Destination, self.probe_route, 'observe',
                'Ask the Nav2 planner for a path from the current pose to a destination. Read-only; no motion.'),
            'navigate_to': (Navigate, self.navigate_to, 'command',
                'Send the robot to a station or area. The operator message that asked for it is the approval. '
                'Dispatch checks and a route probe run first; a failed check returns the reason.'),
            'cancel_navigation': (Cancel, self.cancel_navigation, 'stop',
                'Stop the current goal and wait for a confirmed stop. Always allowed.'),
            'add_keepout': (AddKeepout, self.add_keepout, 'command',
                'Keep the robot out of a region (named area, map-image rectangle, or metre box). '
                'Robot clearance is added automatically. Verified against the published mask and costmap.'),
            'remove_keepout': (RemoveKeepout, self.remove_keepout, 'command',
                'Reopen a region. Requires an operator message that asks for it.'),
            'define_area': (DefineArea, self.define_area, 'command',
                'Name a region of the map so people and Ripple can refer to it later.'),
            'set_station_availability': (StationState, self.set_station_availability, 'command',
                'Mark a station unavailable (or available again), e.g. under inspection.'),
            'clear_costmap': (ClearCostmap, self.clear_costmap, 'recovery',
                'Incident recovery: clear the local or global costmap once each per incident.'),
            'lifecycle_reset': (LifecycleReset, self.lifecycle_reset, 'recovery',
                'Incident recovery: reset a declared Nav2 node. Needs an operator authorization; robot must be stopped.'),
            'escape': (EscapeArgs, self.escape, 'recovery',
                'Incident recovery: bounded collision-checked BackUp or Spin. Refused while the safety controller '
                'is holding the robot, in manual mode, or beyond profile limits.'),
            'retry_navigation': (Retry, self.retry_navigation, 'recovery',
                "Incident recovery: re-send the incident's commanded goal once, after a successful route probe."),
            'navigate_via': (NavigateVia, self.navigate_via, 'command',
                'Replan through exact map points you choose, e.g. around a tight spot where the robot keeps stopping '
                '(probe_route reports tight_spot and min_clearance_m). Each point is checked for clearance and keepouts; '
                'a rejected point comes back with nearest_clear_point. Every leg must plan before the robot moves, and '
                'only the final arrival is verified. With incident_id it re-sends the incident goal on the retry budget.'),
            'teleop': (TeleopArgs, self.teleop, 'recovery',
                'Sensor-guided manual drive to free a stuck robot: a short bounded move on the teleop input. '
                'direction=auto picks the side with the most scan clearance. override_safety (simulation only) moves '
                'even while the safety controller holds; it still stops inside the minimum clearance. Retry navigation afterwards.'),
        }

    def definitions(self, names=None):
        return [{'type': 'function', 'function': {'name': n, 'description': d,
                                                  'parameters': inline(m.model_json_schema())}}
                for n, (m, _, _, d) in self.specs.items() if names is None or n in names]

    def level(self, name):
        return self.specs[name][2] if name in self.specs else None

    async def call(self, name, arguments, request_id=None):
        request_id = request_id or 'req-' + uuid4().hex[:12]
        spec = self.specs.get(name)
        if spec is None:
            out = {'status': 'denied', 'reason': 'unknown_tool'}
        else:
            try:
                args = spec[0].model_validate(arguments or {})
            except ValidationError as exc:
                out = {'status': 'denied', 'reason': 'invalid_arguments: ' + '; '.join(
                    '.'.join(map(str, e['loc'])) + ' ' + e['msg'] for e in exc.errors()[:4])}
            else:
                try:
                    out = await spec[1](args, request_id)
                except PermissionError as exc:
                    out = {'status': 'denied', 'reason': str(exc)}
                except Exception as exc:
                    out = {'status': 'failed', 'reason': type(exc).__name__ + ': ' + str(exc)}
        result = {'tool': name, 'request_id': request_id, 'status': out.get('status', 'ok'),
                  'reason': out.get('reason', ''), 'verified': bool(out.get('verified', False)),
                  'data': out.get('data', {})}
        self.rt.record_tool(result, arguments or {})
        return result

    # ----- helpers
    def _facts(self):
        return self.rt.node.detector.snapshot()

    def _val(self, key, facts=None):
        o = (facts or self._facts()).get(key)
        return o.value if o is not None and o.fresh else None

    def _authorize(self, auth_id, action):
        reason = self.rt.auths.check(auth_id, action)
        if reason:
            raise PermissionError(reason + ' — command actions need the ID of the operator message that asked for them')
        return self.rt.auths.get(auth_id)

    def _region(self, spec, geom):
        if spec.area is not None:
            area = self.rt.site.area(spec.area)
            if area is None:
                raise ValueError('unknown area "' + spec.area + '"; known areas: ' +
                                 (', '.join(a['name'] for a in self.rt.site.areas.values()) or 'none'))
            return Region.load(area['region']), area['name']
        if spec.image_bounds is not None:
            return Region.from_image(geom, spec.image_bounds), None
        return Region.from_box(spec.box_m), None

    def open_incident(self, kind, cause, goal=None):
        """Host-only: registers an incident the recovery tools can reference."""
        event = Event(id='inc-' + uuid4().hex[:10], robot=self.rt.profile.robot, kind=kind, cause=cause,
                      evidence=self._facts(), observed_at=now_iso())
        self.rt.policy.register_incident(event)
        self.incidents[event.id] = {'goal': goal, 'kind': kind, 'cause': cause}
        if self.rt.reader:
            self.rt.reader.request()  # fresh RosScope evidence for this incident
        return event.id

    # ----- observe
    async def robot_status(self, a, rid):
        facts = self._facts()
        detector = self.rt.node.detector
        safety = self._val('safety', facts)
        stale = sorted(k for k, o in facts.items() if not o.fresh)
        data = dict(
            cause=detector.cause(facts), velocity_winner=detector.winner(facts),
            safety_required=self.rt.profile.safety.required,
            safety=None if not safety else {k: safety.get(k) for k in
                                            ('known', 'emergency', 'obstacle', 'localization', 'override', 'mode')},
            mode=self._val('mode', facts), localization_ok=self._val('localization', facts),
            pose=self._val('pose', facts), odometry=self._val('odometry', facts),
            lifecycle=self._val('lifecycle', facts), goal=self.rt.navigator.public(),
            scans={k[5:]: self._val(k, facts) for k in facts if k.startswith('scan.')},
            stale_or_missing=stale,
            recent_events=[{'kind': e.kind, 'cause': e.cause, 'at': e.observed_at}
                           for e in list(self.rt.node.events)[-5:]])
        return {'status': 'ok', 'reason': 'Missing or stale facts are unknown, not healthy' if stale else 'Fresh observations',
                'data': data}

    async def diagnostics(self, a, rid):
        facts = self._facts()
        scope = self.rt.rosscope_summary()
        safety = self._val('safety', facts)
        return {'status': 'ok', 'reason': 'Heuristic evidence; verify with probes before acting',
                'data': {'lifecycle': self._val('lifecycle', facts), 'rosscope': scope, 'safety_raw': safety,
                         'findings': list(self.rt.node.findings)[-10:],
                         'failure_window': len(self.rt.node.detector.failures)}}

    async def recent_logs(self, a, rid):
        return {'status': 'ok', 'reason': 'Classified findings only; raw lines are not exported',
                'data': {'findings': list(self.rt.node.findings)[-a.limit:]}}

    async def get_site(self, a, rid):
        geom = self.rt.geometry()
        dests = self.rt.site.destinations(geom)
        return {'status': 'ok', 'reason': '', 'data': {
            'destinations': [{'name': k, 'label': d['label'], 'kind': d['kind'], 'available': d['available'],
                              'unavailable_reason': d['unavailable_reason'], 'x': round(d['x'], 2), 'y': round(d['y'], 2)}
                             for k, d in dests.items()],
            'areas': [a['name'] for a in self.rt.site.areas.values()],
            'keepouts': [{k: v for k, v in r.items() if k in ('id', 'name', 'reason', 'author', 'state', 'verification', 'created_at')}
                         for r in self.rt.site.listed_keepouts()],
            'keepouts_supported': self.rt.keepouts.enabled,
            'map': geom.public() if geom else None}}

    async def probe_route(self, a, rid):
        key, dest = self.rt.site.resolve(a.destination, self.rt.geometry())
        if not key:
            return {'status': 'denied', 'reason': 'unknown destination; ask get_site for names'}
        probe = await self.rt.navigator.probe(dest)
        return {'status': 'ok' if probe['ok'] else 'failed', 'reason': probe['reason'],
                'data': {'destination': key, **{k: v for k, v in probe.items() if k != 'path'}}}

    # ----- command (operator message is the approval)
    async def navigate_to(self, a, rid):
        key, dest = self.rt.site.resolve(a.destination, self.rt.geometry())
        if not key:
            return {'status': 'denied', 'reason': 'unknown destination "' + a.destination + '"; known: ' +
                    ', '.join(self.rt.site.destinations(self.rt.geometry()))}
        auth = self._authorize(a.authorization_id, 'navigate')
        out = await self.rt.navigator.dispatch(key, dest, auth.id, a.reason or auth.text)
        if out['status'] == 'ok':
            self.rt.auths.consume(auth.id, 'navigate:' + key)
        return {'status': out['status'], 'reason': out['reason'],
                'data': {'destination': key, 'label': dest['label'],
                         **{k: v for k, v in out.items() if k not in ('status', 'reason')}}}

    async def cancel_navigation(self, a, rid):
        out = await self.rt.navigator.cancel(a.reason)
        return {'status': out['status'], 'reason': out['reason'], 'verified': out['status'] == 'ok'}

    async def add_keepout(self, a, rid):
        auth = self._authorize(a.authorization_id, 'keepout')
        if not self.rt.keepouts.enabled:
            return {'status': 'denied', 'reason': 'keepout_unsupported_by_profile'}
        geom = self.rt.geometry()
        if geom is None:
            return {'status': 'failed', 'reason': 'map not received yet'}
        drawn, name = self._region(a.region, geom)
        nav = self.rt.profile.navigation
        buffered = drawn.buffered(geom, nav.keepout_clearance_m)
        polygon = buffered.polygon(geom)
        pose = self._val('pose')
        if pose is None:
            return {'status': 'denied', 'reason': 'robot pose unknown; cannot confirm the region is clear of the robot'}
        # A keepout under the robot would leave Nav2 unable to plan out of it: it waits, and the edge applies it
        # as soon as the robot has left the region.
        overlaps = distance((pose['x'], pose['y']), polygon) < nav.footprint_radius_m
        record = dict(id='ko-' + uuid4().hex[:8], name=name, reason=a.reason, author=auth.operator,
                      authorization_id=auth.id, created_at=now_iso(), drawn=drawn.public(),
                      buffered=buffered.public(), polygon=[list(p) for p in polygon],
                      state='PENDING' if overlaps else 'APPLYING',
                      verification='waiting for the robot to leave the region' if overlaps else 'writing site layers')
        self.rt.site.save_keepout(record)
        self.rt.auths.consume(auth.id, 'keepout')
        if overlaps:
            return {'status': 'ok', 'reason': 'pending: the region overlaps the robot, so the edge applies it '
                    'automatically once the robot has left it; move the robot away',
                    'data': {'keepout_id': record['id'], 'state': 'PENDING', 'name': name,
                             'clearance_m': nav.keepout_clearance_m}}
        ok, why = await self.rt.keepouts.apply(record)
        record.update(state='APPLIED' if ok else 'UNVERIFIED', verification=why)
        self.rt.site.save_keepout(record)
        goal = self.rt.navigator.public()
        note = ''
        if goal and self.rt.navigator.active() and distance((goal['target']['x'], goal['target']['y']), polygon) == 0:
            note = ' The active goal lies inside this keepout.'
        return {'status': 'ok' if ok else 'unknown', 'reason': why + note, 'verified': ok,
                'data': {'keepout_id': record['id'], 'state': record['state'], 'name': name,
                         'clearance_m': nav.keepout_clearance_m}}

    async def remove_keepout(self, a, rid):
        auth = self._authorize(a.authorization_id, 'remove_keepout')
        record = self.rt.site.keepouts.get(a.keepout_id)
        if record is not None and record['state'] == 'PENDING':
            # Never applied, so there is nothing in the site layers to undo.
            record.update(state='REMOVED', removed_by=auth.operator, removed_at=now_iso(),
                          verification='cancelled before it was applied')
            self.rt.site.save_keepout(record)
            self.rt.auths.consume(auth.id, 'remove_keepout')
            return {'status': 'ok', 'reason': record['verification'], 'verified': True,
                    'data': {'keepout_id': record['id'], 'state': 'REMOVED'}}
        # REMOVING is retried: a removal whose verification failed must not strand the keepout.
        if record is None or record['state'] not in ('APPLIED', 'UNVERIFIED', 'APPLYING', 'REMOVING'):
            return {'status': 'denied', 'reason': 'no active keepout with that ID'}
        record.update(state='REMOVING', removed_by=auth.operator)
        self.rt.site.save_keepout(record)
        self.rt.auths.consume(auth.id, 'remove_keepout')
        ok, why = await self.rt.keepouts.remove(record)
        record.update(state='REMOVED' if ok else 'REMOVING', verification=why, removed_at=now_iso())
        self.rt.site.save_keepout(record)
        return {'status': 'ok' if ok else 'unknown', 'reason': why, 'verified': ok,
                'data': {'keepout_id': record['id'], 'state': record['state']}}

    async def define_area(self, a, rid):
        auth = self._authorize(a.authorization_id, 'define_area')
        geom = self.rt.geometry()
        if geom is None:
            return {'status': 'failed', 'reason': 'map not received yet'}
        region, _ = self._region(a.region, geom)
        record = self.rt.site.define_area(a.name, region, auth.operator)
        self.rt.auths.consume(auth.id, 'define_area')
        return {'status': 'ok', 'reason': 'area saved to site memory', 'verified': True,
                'data': {'name': record['name']}}

    async def set_station_availability(self, a, rid):
        auth = self._authorize(a.authorization_id, 'station_state')
        key, dest = self.rt.site.resolve(a.destination, self.rt.geometry())
        if not key:
            return {'status': 'denied', 'reason': 'unknown destination'}
        self.rt.site.set_available(key, a.available, a.reason, auth.operator)
        self.rt.auths.consume(auth.id, 'station_state')
        return {'status': 'ok', 'reason': 'saved to site memory', 'verified': True,
                'data': {'destination': key, 'available': a.available}}

    # ----- recovery (bounded per incident)
    def _incident(self, incident_id):
        if incident_id not in self.incidents:
            raise PermissionError('unknown_incident: recovery tools act only on incidents the edge opened')
        return self.incidents[incident_id]

    async def _level2(self, rid, tool, incident_id, target, authorization_id=None):
        if self.rt.level2 is None:
            return {'status': 'failed', 'reason': 'recovery executor unavailable (no exclusive ownership)'}
        result = await self.rt.level2.execute(RecoveryRequest(request_id=rid, tool=tool, incident_id=incident_id,
                                                              target=target, authorization_id=authorization_id).model_dump())
        return {'status': result.status, 'reason': result.reason, 'verified': result.verified}

    async def clear_costmap(self, a, rid):
        self._incident(a.incident_id)
        if a.costmap == 'local' and self.rt.navigator.active():
            # The local costmap lives inside the controller; clearing it mid-control has preceded controller hangs.
            return {'status': 'denied', 'reason': 'cancel the active goal before clearing the local costmap'}
        return await self._level2(rid, 'clear_costmap', a.incident_id, a.costmap)

    async def lifecycle_reset(self, a, rid):
        self._incident(a.incident_id)
        # Profiles name nodes fully qualified; the model often drops the leading slash ("planner_server").
        node = a.node if a.node.startswith('/') else '/' + a.node
        return await self._level2(rid, 'lifecycle_reset', a.incident_id, node, a.authorization_id)

    async def escape(self, a, rid):
        self._incident(a.incident_id)
        out = await self.rt.escape.execute(a.incident_id, a.primitive, a.amount, a.speed, a.authorization_id, rid)
        return out

    async def teleop(self, a, rid):
        return await self.rt.teleop.execute(a.direction, a.distance_m, a.speed, a.override_safety, a.reason, rid,
                                            a.incident_id)

    async def retry_navigation(self, a, rid):
        incident = self._incident(a.incident_id)
        goal = incident.get('goal')
        if not goal:
            return {'status': 'denied', 'reason': 'this incident has no commanded goal to retry'}
        policy = self.rt.profile.recovery.retry_goal
        if policy.autonomy != 'auto':
            return {'status': 'denied', 'reason': 'autonomy_' + policy.autonomy}
        key, dest = self.rt.site.resolve(goal['destination'], self.rt.geometry())
        fingerprint = hashlib.sha256(json.dumps([a.incident_id, key]).encode()).hexdigest()
        claim = await asyncio.to_thread(self.rt.journal.request, op='claim', robot=self.rt.profile.robot,
                                        request=rid, incident=a.incident_id, action='retry_goal',
                                        fingerprint=fingerprint, limit=policy.per_incident)
        if not claim['claimed']:
            return {'status': 'denied', 'reason': claim['reason']}
        out = await self.rt.navigator.dispatch(key, dest, goal.get('authorization_id'),
                                               'retry after recovery for ' + a.incident_id, attempt='retry')
        await asyncio.to_thread(self.rt.journal.request, op='finish', robot=self.rt.profile.robot,
                                request=rid, status=out['status'], result=out)
        return {'status': out['status'], 'reason': out['reason'],
                'data': {k: v for k, v in out.items() if k not in ('status', 'reason')}}

    async def navigate_via(self, a, rid):
        geom = self.rt.geometry()
        incident = self._incident(a.incident_id) if a.incident_id else None
        goal = (incident or {}).get('goal') or {}
        wanted = a.destination or goal.get('destination')
        if not wanted:
            return {'status': 'denied', 'reason': 'destination is required: no open incident has a goal to reroute. Pass '
                    'destination (a station name, e.g. "' + next(iter(self.rt.site.destinations(geom)), 'B') + '") with the via points'}
        key, dest = self.rt.site.resolve(wanted, geom)
        if not key:
            return {'status': 'denied', 'reason': 'unknown destination "' + wanted + '"; known: ' +
                    ', '.join(self.rt.site.destinations(geom))}
        points = [(p.x, p.y) for p in a.via]
        problems = self.rt.navigator.check_via(points)
        if problems:
            return {'status': 'denied', 'data': {'problems': problems}, 'reason': 'via points rejected: ' +
                    '; '.join(f"point {p['point']} {p['problem']}" for p in problems)}
        if incident is not None:
            # Recovery: the incident's commanded goal, rerouted; shares the bounded retry budget.
            if not goal:
                return {'status': 'denied', 'reason': 'this incident has no commanded goal to reroute'}
            policy = self.rt.profile.recovery.retry_goal
            if policy.autonomy != 'auto':
                return {'status': 'denied', 'reason': 'autonomy_' + policy.autonomy}
            fingerprint = hashlib.sha256(json.dumps([a.incident_id, key, points]).encode()).hexdigest()
            claim = await asyncio.to_thread(self.rt.journal.request, op='claim', robot=self.rt.profile.robot,
                                            request=rid, incident=a.incident_id, action='retry_goal',
                                            fingerprint=fingerprint, limit=policy.per_incident)
            if not claim['claimed']:
                return {'status': 'denied', 'reason': claim['reason']}
            out = await self.rt.navigator.dispatch(key, dest, goal.get('authorization_id'),
                                                   'rerouted via chosen points for ' + a.incident_id,
                                                   attempt='reroute', via=points)
            await asyncio.to_thread(self.rt.journal.request, op='finish', robot=self.rt.profile.robot,
                                    request=rid, status=out['status'], result=out)
        else:
            auth = self._authorize(a.authorization_id, 'navigate')
            out = await self.rt.navigator.dispatch(key, dest, auth.id, a.reason or auth.text, via=points)
            if out['status'] == 'ok':
                self.rt.auths.consume(auth.id, 'navigate:' + key)
        return {'status': out['status'], 'reason': out['reason'],
                'data': {'destination': key, 'label': dest['label'],
                         **{k: v for k, v in out.items() if k not in ('status', 'reason')}}}
