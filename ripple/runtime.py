"""One owner thread for ROS events, mission mutations and SQLite writes."""
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
from .store import Store
from .supervisor import Supervisor
from .camera import Camera

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
        self.s = Supervisor(self.adapter, self.store)
        self.message = 'Ready for a mission. Packing B requires live camera CLEAR.'
        self.instruction = ''
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
        self.s.observe('camera.B', 'UNKNOWN', 'camera not connected', 1)
        self.camera = Camera(self.events)
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
                        and not self.actions_active):
                    for p in self.s.proposals.values():
                        if p.id not in self.attempted and p.state in ('APPROVED', 'EXPIRED') and p.operator:
                            self.attempted.add(p.id)
                            self.s.dispatch(p.id, self.targets()[p.target.station])
        finally:
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
        if event.kind == 'camera':
            old = self.s.facts['camera.B'].value
            self.s.observe('camera.B', d['state'], 'D435i fixed region depth', 1)
            if (old != 'CLEAR' and d['state'] == 'CLEAR' and self.s.proposals
                    and not self.pending_model and not self.s.interpreting
                    and self.s.state in ('HELD', 'CANCELLING') and os.environ.get('OPENROUTER_API_KEY')):
                self.begin_interpretation('Reconsider a repair for the existing mission using current facts.',
                                          automatic=True)
        elif event.kind == 'odometry':
            valid = all(math.isfinite(d[k]) for k in ('linear_speed', 'angular_speed'))
            self.s.observe('robot.odom_fresh', valid, '/diff_cont/odom', 1)
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
            self.s.observe('robot.localized', valid, '/amcl_pose', 5)
        elif event.kind == 'mode':
            self.s.observe('robot.mode', d['value'], '/control_mode', 1)
        elif event.kind == 'safety':
            self.s.observe('robot.safety_clear', d['clear'], '/safety/status', 1)
            # Service reports the controller's observed mode, even for late joiners.
            lines = [line.strip() for line in d['raw'].splitlines() if line.strip().startswith('Control Mode:')]
            mode = lines[0].split(':', 1)[1].strip() if len(lines) == 1 else 'unknown'
            self.s.observe('robot.mode', mode, '/safety/status', 1)
        elif event.kind == 'accepted':
            self.s.accepted(event.goal_id)
        elif event.kind == 'terminal':
            self.s.terminal(event.goal_id, d['outcome'], d['final_pose'])
        elif event.kind == 'rejected':
            self.s.terminal(event.goal_id, 'REJECTED')
        elif event.kind == 'uncertain':
            self.s.state = 'HELD'
            self.s.reconciliation_required = True
            self.store.append('adapter_uncertain', goal_id=event.goal_id, reason=d['reason'])

    def snapshot(self):
        now = time.monotonic()
        return dict(state=self.s.state, goal_id=self.s.goal_id, message=self.message,
            paused=self.s.paused, interpreting=self.s.interpreting,
            reconciliation_required=self.s.reconciliation_required,
            model_configured=bool(os.environ.get('OPENROUTER_API_KEY')),
            camera=self.camera.status(),
            vision_description=self.vision_description,
            pose=self.adapter.pose, targets={k: asdict(v) for k, v in self.targets().items()},
            facts={k: dict(**asdict(f), fresh=f.fresh(now)) for k, f in self.s.facts.items()},
            proposals=[asdict(p) for p in self.s.proposals.values()],
            receipts=self.store.receipts()[-60:])

    def command(self, name, data):
        if name == 'state':
            return self.snapshot()
        if name == 'instruction':
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
            self.camera.calibrate(data['roi'])
            self.s.observe('camera.B', 'UNKNOWN', 'camera recalibration', 1)
            self.store.append('camera_calibrated', roi=data['roi'])
        elif name == 'describe':
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
        snapshot = self.snapshot()
        context = {k: snapshot[k] for k in ('state', 'goal_id', 'targets', 'facts', 'proposals')}
        self.pending_versions = {k: f.version for k, f in self.s.facts.items()}
        self.pending_model = self.pool.submit(agent.interpret, text, context)

    def finish_interpretation(self):
        future, self.pending_model = self.pending_model, None
        try:
            result = future.result()
            if self.automatic_interpretation and result.updates:
                raise ValueError('Automatic reconsideration cannot change operator facts')
            self.store.append('interpretation', **result.model_dump())
            self.message = result.clarification or result.explanation
            if result.kind == 'clarify':
                return  # remains held until a resolving operator input
            for update in result.updates:
                self.s.observe(f'station.{update.station}.available', update.available,
                               'operator: ' + update.evidence)
            if result.destination:
                target = self.targets()[result.destination]
                available = self.s.facts[f'station.{target.station}.available']
                camera = self.s.facts['camera.B']
                sensor_changed = (target.station == 'B' and
                                  camera.version != self.pending_versions.get('camera.B'))
                if sensor_changed or not available.value or (target.station == 'B' and
                    (camera.value != 'CLEAR' or not camera.fresh(time.monotonic()))):
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
