"""One owner thread for ROS events, mission mutations and PostgreSQL writes."""
import concurrent.futures
from dataclasses import asdict
import json
import math
import os
from pathlib import Path
import queue
import threading
import time
import yaml
import rclpy
from rclpy.parameter import Parameter
from . import agent
from .contracts import Target
from .robot_adapter import Nav2Adapter
from .incidents import Incidents, diagnosis
from .map_projection import mask_png
from .store import Store
from .site import SiteMemory, rectangle, buffered_bounds
from PIL import Image
from io import BytesIO
from .supervisor import Supervisor
from .rosscope import RosScopeObserver, snapshot as rosscope_snapshot

ROOT = Path(__file__).resolve().parents[1]


class Runtime:
    def __init__(self):
        self.commands = queue.Queue()
        self.events = queue.Queue()
        self.ready = threading.Event()
        self.stopping = threading.Event()
        self.pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        self.thread = threading.Thread(target=self.run, name='ripple-owner', daemon=True)
        self.thread.start()
        if not self.ready.wait(15):
            raise RuntimeError('Ripple runtime failed to initialize')

    def call(self, name, **payload):
        future = concurrent.futures.Future()
        self.commands.put((name, payload, future))
        return future.result(timeout=8)

    def targets(self):
        registry = json.loads((ROOT / 'config/stations.json').read_text())
        zones = yaml.safe_load((ROOT / 'smr300l_gazebo_ros2control/zones.yaml').read_text())['zones']
        result = {}
        for station, entry in registry.items():
            zone = zones[entry['zone']]
            p, q = zone['position'], zone['orientation']
            yaw = math.atan2(2 * (q['w'] * q['z'] + q['x'] * q['y']),
                             1 - 2 * (q['y']**2 + q['z']**2))
            result[station] = Target(station, zone['frame_id'], p['x'], p['y'], yaw)
        return result

    def run(self):
        rclpy.init()
        self.adapter = Nav2Adapter(self.events)
        self.adapter.set_parameters([Parameter('use_sim_time', value=True)])
        self.store = Store(os.environ['DATABASE_URL'])
        self.s = Supervisor(self.adapter, self.store, camera_required=False)
        self.message = 'Monitoring SMR300 simulation. Ready for a site instruction.'
        self.instruction = ''
        self.conversation = []
        self.pending_model = None
        self.pending_vision = None
        self.vision_description = 'Request a scene description after connecting the camera.'
        self.attempted = set()
        self.actions_active = None
        self.stopped_since = None
        previous_facts = {r['key']: r for r in self.store.receipts()
                          if r['kind'] == 'fact_changed' and r['key'].startswith('station.')}
        for key in ('station.A.available', 'station.B.available'):
            previous = previous_facts.get(key)
            self.s.observe(key, previous['value'] if previous else True,
                           previous['source'] if previous else 'demo initial context')
        self.site = SiteMemory(self.store, os.environ.get('RIPPLE_LAYERS_FILE', '/home/zuci/map_layers.json'))
        self.pending_site = None
        self.camera = None
        self.rosscope_observation = None
        self.observer = RosScopeObserver(self.events, ROOT)
        self.incidents = Incidents(self.store)
        self.recovery_deadlines = {}
        self.recovery_enabled = os.environ.get('RIPPLE_AUTOMATIC_RECOVERY') == '1'
        self.recent_logs = []
        self.ready.set()
        try:
            while not self.stopping.is_set():
                rclpy.spin_once(self.adapter, timeout_sec=0.02)
                while not self.events.empty():
                    self.robot_event(self.events.get_nowait())
                while not self.commands.empty():
                    name, payload, future = self.commands.get_nowait()
                    try:
                        future.set_result(self.command(name, payload))
                    except Exception as error:
                        future.set_exception(error)
                self.s.tick()
                self.recovery_tick()
                if self.pending_site and self.pending_site.done():
                    future, self.pending_site = self.pending_site, None
                    try:
                        result = future.result()
                        self.message = result.clarification or result.explanation
                        if result.bounds is not None and not result.clarification:
                            self.site_preview(result.bounds, self.site_text, self.site_operator)
                    except Exception as error:
                        self.message = str(error)
                        self.store.append('site_interpretation_failed', reason=str(error))
                if self.pending_model and self.pending_model.done():
                    self.finish_interpretation()
                if self.pending_vision and self.pending_vision.done():
                    try:
                        self.vision_description = self.pending_vision.result()
                        self.store.append('vision_description', text=self.vision_description)
                    except Exception as error:
                        self.vision_description = str(error)
                    self.pending_vision = None
                if (not self.s.paused and not self.s.interpreting and self.s.goal_id is None
                        and not self.actions_active and self.site_ready()
                        and not any(i['state'] in ('DIAGNOSING','CLEARING COSTMAPS','REPLANNING')
                                    for i in self.incidents.items.values())):
                    for p in self.s.proposals.values():
                        if p.id not in self.attempted and p.state in ('APPROVED', 'EXPIRED') and p.operator:
                            self.attempted.add(p.id)
                            self.s.dispatch(p.id, self.targets()[p.target.station])
        finally:
            self.observer.close()
            if self.camera:
                self.camera.close()
            if self.s.goal_id:
                self.s.cancel('service shutdown')
                end = time.monotonic() + 5
                while time.monotonic() < end and self.adapter.handles:
                    rclpy.spin_once(self.adapter, timeout_sec=0.1)
            self.adapter.destroy_node()
            rclpy.shutdown()
            self.store.close()

    def robot_event(self, event):
        d = event.data
        if event.kind == 'tf_health':
            self.s.observe('robot.tf_healthy', d['healthy'], 'map to base_link TF', 2, d['received_at'])
        elif event.kind == 'navigation_log':
            self.recent_logs.append(d)
            self.recent_logs = self.recent_logs[-30:]
        elif event.kind == 'costmaps_cleared':
            item = self.incidents.items.get(event.goal_id)
            if item and item['state'] == 'CLEARING COSTMAPS':
                if d['success']:
                    self.incidents.update(item['id'], state='REPLANNING', question='Costmaps cleared; checking the route. Motion remains held.')
                    self.recovery_deadlines[item['id']] = time.monotonic()+30
                    self.adapter.probe(item['id'], item['target'])
                else: self.recovery_escalate(item['id'], 'Costmap service failed. Inspect navigation before retrying.')
        elif event.kind == 'recovery_plan':
            self.finish_recovery_plan(event.goal_id, d)
        elif event.kind == 'rosscope':
            self.rosscope_observation = d
        elif event.kind == 'camera':
            old = self.s.facts['camera.B'].value
            self.s.observe('camera.B', d['state'], 'D435i fixed region depth', 1, d['received_at'])
            if (old != 'CLEAR' and d['state'] == 'CLEAR' and self.s.proposals
                    and not self.pending_model and not self.s.interpreting
                    and self.s.state in ('HELD', 'CANCELLING') and os.environ.get('OPENROUTER_API_KEY')):
                self.begin_interpretation('Reconsider a repair for the existing mission using current facts.',
                                          automatic=True)
        elif event.kind == 'odometry':
            valid = all(math.isfinite(d[k]) for k in ('linear_speed', 'angular_speed'))
            self.s.observe('robot.odom_fresh', valid, '/diff_cont/odom', 1, d['received_at'])
            self.s.odometry(d['linear_speed'], d['angular_speed'], d['received_at'])
            if valid and abs(d['linear_speed']) < 0.02 and abs(d['angular_speed']) < 0.02:
                if self.stopped_since is None:
                    self.stopped_since = time.monotonic()
            else:
                self.stopped_since = None
        elif event.kind == 'action_status':
            self.actions_active = d['active']
        elif event.kind == 'localization':
            valid = all(math.isfinite(x) for x in d['covariance'])
            self.s.observe('robot.localized', valid, '/amcl_pose', 5, d['received_at'])
        elif event.kind == 'mode':
            self.s.observe('robot.mode', d['value'], '/control_mode', 1)
        elif event.kind == 'safety':
            self.s.observe('robot.safety_clear', d['clear'], '/safety/status', 1, d['received_at'])
            # Service reports the controller's observed mode, even for late joiners.
            lines = [line.strip() for line in d['raw'].splitlines() if line.strip().startswith('Control Mode:')]
            mode = lines[0].split(':', 1)[1].strip() if len(lines) == 1 else 'unknown'
            self.s.observe('robot.mode', mode, '/safety/status', 1)
        elif event.kind == 'accepted':
            self.s.accepted(event.goal_id)
        elif event.kind == 'terminal':
            if event.goal_id != self.s.goal_id: return
            if d['outcome'] == 'SUCCEEDED' and self.s.active_proposal:
                for item in list(self.incidents.items.values()):
                    if item.get('retry_proposal_id') == self.s.active_proposal.id:
                        self.incidents.update(item['id'], state='VERIFYING STOP', completed_goal=event.goal_id)
            self.s.terminal(event.goal_id, d['outcome'], d['final_pose'])
            if d['outcome'] == 'ABORTED':
                self.open_incident(event.goal_id, 'Nav2 aborted navigation')
        elif event.kind == 'rejected':
            if event.goal_id != self.s.goal_id: return
            self.s.terminal(event.goal_id, 'REJECTED')
            self.open_incident(event.goal_id, 'Nav2 rejected navigation')
        elif event.kind == 'uncertain':
            self.s.state = 'HELD'
            self.s.reconciliation_required = True
            self.store.append('adapter_uncertain', goal_id=event.goal_id, reason=d['reason'])

    def site_preview(self, bounds, reason, operator):
        meta = yaml.safe_load((ROOT / 'smr300l_gazebo_ros2control/maps/smr_map.yaml').read_text())
        with Image.open(ROOT / 'smr300l_gazebo_ros2control/maps/smr_map.pgm') as im:
            rectangle(bounds, im.width, im.height, meta['resolution'], meta['origin'])
            # Circumscribed 0.40 x 0.25 m half-footprint plus one map cell.
            bounds = buffered_bounds(bounds, im.width, im.height, meta['resolution'])
            shape = rectangle(bounds, im.width, im.height, meta['resolution'], meta['origin'])
        self.site.preview(shape, reason+' (Includes 0.55 m robot clearance.)', operator, bounds)
        self.message = 'Review the highlighted region, then apply the exact keepout.'

    def site_verification(self, zone):
        if zone['state'] != 'APPLIED': return 'not applied'
        shape = zone['shape']
        for label, sample in [('mask', self.adapter.keepout_mask), ('costmap', self.adapter.global_costmap)]:
            if not sample or time.monotonic()-sample[1] > 5: return label+' unavailable or stale'
            msg = sample[0]; info = msg.info
            if msg.header.frame_id != 'map' or info.resolution <= 0: return 'invalid '+label+' frame'
            ox, oy = info.origin.position.x, info.origin.position.y
            xmin = math.ceil((shape['x1']-ox)/info.resolution)
            xmax = math.floor((shape['x2']-ox)/info.resolution)-1
            ymin = math.ceil((shape['y1']-oy)/info.resolution)
            ymax = math.floor((shape['y2']-oy)/info.resolution)-1
            if xmin < 0 or ymin < 0 or xmax >= info.width or ymax >= info.height: return label+' geometry mismatch'
            cells = [msg.data[y*info.width+x] for y in range(ymin,ymax+1) for x in range(xmin,xmax+1)]
            if not cells or any(v != 100 for v in cells): return label+' does not contain restriction'
        return 'MASK AND COSTMAP OBSERVED · planner verification pending'

    def site_ready(self):
        if any(z['state'] == 'UNCERTAIN' for z in self.site.zones.values()): return False
        return all(self.site_verification(z).startswith('MASK AND COSTMAP OBSERVED')
                   for z in self.site.zones.values() if z['state'] == 'APPLIED')

    def recovery_evidence(self):
        now = time.monotonic()
        def valid(key):
            f = self.s.facts.get(key)
            return bool(f and f.value is True and f.fresh(now))
        return dict(localization=valid('robot.localized'), tf=valid('robot.tf_healthy'),
                    safety=valid('robot.safety_clear'), odometry=valid('robot.odom_fresh'),
                    rosscope=rosscope_snapshot(self.rosscope_observation, now),
                    recent_logs=self.recent_logs[-15:], pose=self.adapter.pose)

    def open_incident(self, goal_id, symptom):
        proposal = self.s.active_proposal
        for item in list(self.incidents.items.values()):
            if proposal and item.get('retry_proposal_id') == proposal.id:
                self.recovery_escalate(item['id'], 'The approved retry failed. No more automatic recovery; confirm site conditions.')
                return
        item = self.incidents.open(goal_id, symptom,
            asdict(proposal.target) if proposal else None, self.recovery_evidence())
        item = self.incidents.update(item['id'], mission_revision=self.s.revision)
        self.recovery_deadlines[item['id']] = time.monotonic()+20
        self.s.paused = True
        self.message = 'Navigation failed. Checking ROS evidence and waiting for confirmed stop.'

    def recovery_escalate(self, incident_id, question):
        self.incidents.update(incident_id, state='NEEDS HUMAN', question=question)
        self.message = question
        self.s.paused = True

    def recovery_tick(self):
        now = time.monotonic()
        for item in list(self.incidents.items.values()):
            iid = item['id']; state = item['state']
            stationary = (self.s.goal_id is None and self.actions_active == []
                          and self.stopped_since is not None and now-self.stopped_since > 1)
            if state == 'VERIFYING STOP' and stationary and self.s.state == 'ARRIVED':
                self.incidents.update(iid, state='RESOLVED', question='Nav2 SUCCEEDED and fresh stopped odometry verified.')
            elif state == 'DIAGNOSING':
                if not stationary:
                    if now > self.recovery_deadlines.get(iid, 0):
                        self.recovery_escalate(iid, 'Could not confirm a stopped robot. Inspect its state.')
                    continue
                if not self.recovery_enabled:
                    self.recovery_escalate(iid, 'Automatic recovery is disabled pending live validation. Inspect the incident evidence and site.')
                    continue
                evidence = self.recovery_evidence()
                reasons = diagnosis(evidence)
                if reasons or not item.get('target') or not self.site_ready():
                    self.incidents.update(iid, evidence=evidence)
                    self.recovery_escalate(iid, 'Recovery held: '+('; '.join(reasons) or 'target/site constraint evidence unavailable')+'. Please inspect the site.')
                else:
                    self.incidents.update(iid, evidence=evidence)
                    self.incidents.claim_clear(iid)
                    self.recovery_deadlines[iid] = now+15
                    self.adapter.clear_costmaps(iid)
            elif state in ('CLEARING COSTMAPS','REPLANNING') and now > self.recovery_deadlines.get(iid, 0):
                self.recovery_escalate(iid, 'Recovery response timed out; outcome is uncertain. Inspect before retrying.')

    def finish_recovery_plan(self, incident_id, result):
        item = self.incidents.items.get(incident_id)
        if not item or item['state'] != 'REPLANNING': return
        if not result['success']:
            self.recovery_escalate(incident_id, 'The route is still unavailable after one costmap clear. Is there an obstruction or a closed aisle?')
            return
        target = Target(**item['target'])
        available = self.s.facts.get(f'station.{target.station}.available')
        if (self.s.goal_id or self.s.reconciliation_required or self.s.interpreting
                or item.get('mission_revision') != self.s.revision
                or not available or available.value is not True or not available.fresh(time.monotonic())
                or target != self.targets().get(target.station) or not self.site_ready()):
            self.recovery_escalate(incident_id, 'Route found, but mission context changed. Review a fresh instruction.')
            return
        proposal = self.s.propose(target, 'Recovery route found after one costmap clear. Review this fresh retry destination.')
        self.incidents.update(incident_id, state='AWAITING RETRY APPROVAL', retry_proposal_id=proposal.id,
            question='A route is available. Approve the new exact destination and release dispatch pause to retry.')
        self.message = self.incidents.items[incident_id]['question']

    def snapshot(self):
        now = time.monotonic()
        return dict(state=self.s.state, goal_id=self.s.goal_id, message=self.message,
            paused=self.s.paused, interpreting=self.s.interpreting,
            reconciliation_required=self.s.reconciliation_required,
            model_configured=bool(os.environ.get('OPENROUTER_API_KEY')),
            camera={'state': 'DISABLED', 'fresh': False, 'calibrated': False},
            operating_mode='simulation',
            rosscope=rosscope_snapshot(self.rosscope_observation, now),
            incidents=list(self.incidents.items.values())[-50:],
            recent_logs=self.recent_logs,
            automatic_recovery=self.recovery_enabled,
            site_zones=[dict(z, verification=self.site_verification(z)) for z in self.site.zones.values()],
            site_interpreting=bool(self.pending_site),
            vision_description=self.vision_description,
            pose=self.adapter.pose, targets={k: asdict(v) for k, v in self.targets().items()},
            station_zones={k: v['zone'] for k, v in json.loads((ROOT / 'config/stations.json').read_text()).items()},
            facts={k: dict(**asdict(f), fresh=f.fresh(now)) for k, f in self.s.facts.items()},
            proposals=[dict(asdict(p), seconds_remaining=max(0, p.expires_at-now)
                            if p.expires_at is not None and p.state == 'APPROVED' else None)
                       for p in self.s.proposals.values()],
            receipts=self.store.receipts()[-60:])

    def command(self, name, data):
        if name == 'state':
            return self.snapshot()
        if name == 'keepout_image':
            sample = self.adapter.keepout_mask
            if not sample or time.monotonic()-sample[1] > 5:
                raise ValueError('Published keepout mask is unavailable or stale')
            msg = sample[0]; i = msg.info; p = i.origin.position; q = i.origin.orientation
            yaw = math.atan2(2*(q.w*q.z+q.x*q.y), 1-2*(q.y*q.y+q.z*q.z))
            meta = yaml.safe_load((ROOT / 'smr300l_gazebo_ros2control/maps/smr_map.yaml').read_text())
            with Image.open(ROOT / 'smr300l_gazebo_ros2control/maps/smr_map.pgm') as im:
                return mask_png(msg.data,i.width,i.height,i.resolution,[p.x,p.y,yaw],msg.header.frame_id,meta,im.size)
        if name == 'incident_context':
            if not data['text'].strip(): raise ValueError('Describe the physical site condition')
            self.incidents.update(data['incident_id'], human_context=data['text'],
                state='NEEDS HUMAN', question='Site context recorded. Draw or describe a keepout, then request a fresh mission.')
            self.message = 'Recorded engineer context; no site geometry or motion inferred without review.'
        elif name == 'site_preview':
            if data.get('bounds'):
                self.site_preview(data['bounds'], data['text'], data['operator'])
            else:
                if self.pending_site:
                    raise ValueError('Map interpretation already running')
                if not data['text'].strip(): raise ValueError('Describe a region or draw it on the map')
                output = BytesIO()
                with Image.open(ROOT / 'smr300l_gazebo_ros2control/maps/smr_map.pgm') as im:
                    im.save(output, format='PNG')
                self.site_text, self.site_operator = data['text'], data['operator']
                self.pending_site = self.pool.submit(agent.identify_map_region, data['text'], output.getvalue())
                self.message = 'Interpreting map region for review…'
        elif name in ('site_apply', 'site_remove'):
            now = time.monotonic()
            odom = self.s.facts.get('robot.odom_fresh')
            if (self.s.goal_id or self.actions_active != [] or not odom or not odom.fresh(now)
                    or not self.stopped_since or now-self.stopped_since < 1):
                raise ValueError('Hold the robot and wait for confirmed stop before changing site constraints')
            if name == 'site_apply':
                pose = self.adapter.pose
                if not pose or not self.adapter.pose_at or now-self.adapter.pose_at > 5:
                    raise ValueError('Fresh robot pose required for site changes')
                shape = self.site.zones[data['zone_id']]['shape']
                if (shape['x1']-.5 <= pose['x'] <= shape['x2']+.5
                        and shape['y1']-.5 <= pose['y'] <= shape['y2']+.5):
                    raise ValueError('Keepout overlaps the robot clearance region; select a different area')
            self.s.paused = True
            self.site.change(data['zone_id'], remove=name == 'site_remove')
            for proposal in self.s.proposals.values():
                if proposal.state in ('APPROVED','AWAITING APPROVAL'):
                    proposal.state = 'EXPIRED'
                    self.store.append('approval_expired', proposal_id=proposal.id, changed=['site constraints'])
            self.message = 'Site file updated. Dispatch paused; inspect observed mask and planner behavior before resuming.'
        elif name == 'instruction':
            if self.pending_model:
                raise ValueError('An instruction is already being interpreted')
            if not data['text'].strip():
                raise ValueError('Enter an instruction')
            self.begin_interpretation(data['text'])
        elif name == 'approve':
            self.s.approve(data['proposal_id'], data['operator'])
        elif name == 'reject':
            self.s.reject(data['proposal_id'])
        elif name == 'pause':
            self.s.paused = data['enabled']
            self.store.append('dispatch_pause', enabled=self.s.paused)
        elif name == 'cancel':
            self.s.paused = True
            self.s.cancel('operator requested hold')
        elif name == 'calibrate':
            if not self.camera:
                raise ValueError('Physical camera is disabled in simulator mode')
            self.camera.calibrate(data['roi'])
            self.s.observe('camera.B', 'UNKNOWN', 'camera recalibration', 1)
            self.store.append('camera_calibrated', roi=data['roi'])
        elif name == 'describe':
            if not self.camera:
                raise ValueError('Physical camera is disabled in simulator mode')
            if self.pending_vision:
                raise ValueError('Vision request already in progress')
            self.pending_vision = self.pool.submit(agent.describe_scene, self.camera.jpeg())
            self.vision_description = 'Describing captured frame…'
        elif name == 'reconcile':
            now = time.monotonic()
            odom = self.s.facts.get('robot.odom_fresh')
            if (self.s.goal_id or self.actions_active != [] or not odom or not odom.fresh(now)
                    or not self.stopped_since or now - self.stopped_since < 1
                    or not self.adapter.client.server_is_ready()):
                raise ValueError('Cannot reconcile: verify no active Nav2 goals and fresh stopped odometry')
            self.s.reconciliation_required = False
            self.s.state = 'HELD'
            self.store.append('operator_reconciled', operator=data['operator'])
            self.message = 'Reconciled while stationary. Previous approvals remain unusable.'
        else:
            raise ValueError('Unsupported command')
        return self.snapshot()

    def begin_interpretation(self, text, automatic=False):
        self.instruction = text
        self.automatic_interpretation = automatic
        self.s.interpreting = True
        self.message = 'Interpreting instruction; dispatch is held.'
        self.store.append('repair_reconsideration' if automatic else 'operator_input', text=text)
        self.conversation.append({'role': 'automatic' if automatic else 'operator', 'text': text})
        snapshot = self.snapshot()
        context = {k: snapshot[k] for k in ('state', 'goal_id', 'targets', 'facts', 'proposals')}
        context['camera_required'] = False
        context['recent_conversation'] = self.conversation[-8:]
        self.pending_versions = {k: f.version for k, f in self.s.facts.items()}
        self.pending_model = self.pool.submit(agent.interpret, text, context)

    def finish_interpretation(self):
        future, self.pending_model = self.pending_model, None
        try:
            result = future.result()
            if self.automatic_interpretation and result.updates:
                raise ValueError('Automatic reconsideration cannot change operator facts')
            self.store.append('interpretation', result=result.model_dump())
            self.conversation.append({'role': 'interpretation', 'result': result.model_dump()})
            self.message = result.clarification or result.explanation
            if result.kind == 'clarify':
                return  # remains held until a resolving operator input
            for update in result.updates:
                self.s.observe(f'station.{update.station}.available', update.available,
                               'operator: ' + update.evidence)
            if result.destination:
                target = self.targets()[result.destination]
                available = self.s.facts[f'station.{target.station}.available']
                if not available.value or not available.fresh(time.monotonic()):
                    self.message += ' Destination is no longer available; no proposal created.'
                else:
                    self.s.propose(target, result.explanation)
            self.s.interpreting = False
        except Exception as error:
            self.message = str(error)
            self.store.append('interpretation_failed', reason=str(error))
            # An unresolved input cannot release queued work.

    def close(self):
        self.stopping.set()
        self.thread.join(timeout=8)
        self.pool.shutdown(wait=False, cancel_futures=True)
