"""One robot's edge on one ROS node: observer, lease, journal, policy, executors, tools."""
import asyncio
import threading
import time
from .authorization import Authorizations
from .escape import Escape
from .executors import Level2Executor
from .journal import ActionJournal
from .keepouts import Keepouts
from .navigation import Navigator
from .ownership import Ownership
from .policy import RecoveryPolicy
from .site import Site
from .tools import EdgeTools


class EdgeRuntime:
    def __init__(self, profile, root, database_url, operators, rosscope_binary=None):
        from rclpy.executors import SingleThreadedExecutor
        from .observer import Observer
        self.profile = profile
        self.node = Observer(profile)
        self.ownership = Ownership(self.node)  # raises if another edge holds this robot
        self.journal = ActionJournal(database_url, root)
        # A separate connection for memory: a failed memory write must not poison action claims.
        self.memory = ActionJournal(database_url, root)
        # Actions claimed before a crash stay spent and are never replayed.
        self.journal.request(op='interrupt', robot=profile.robot)
        self.auths = Authorizations(operators)
        self.site = Site(profile, store=self.remember)
        self.site.load([(kind, r['id'], r['data']) for kind in ('area', 'keepout', 'station_state')
                        for r in reversed(self.recall(kind))])
        self.policy = RecoveryPolicy(profile, self.journal, self.ownership.available,
                                     authorized=lambda r: bool(r.authorization_id) and
                                     self.auths.check(r.authorization_id, 'recovery') is None)
        self.keepouts = Keepouts(self.node, profile, self.site)
        self.navigator = Navigator(self.node, profile, self.node.detector, self.ownership, self.keepouts)
        self.navigator.owned = self._owned
        self.escape = Escape(self.node, profile, self.node.detector, self.navigator, self.auths, self.journal)
        from .teleop import Teleop
        self.teleop = Teleop(self.node, profile, self.node.detector, self.navigator, self.journal)
        self.refresh = None
        if profile.localization_refresh_service:
            from std_srvs.srv import Empty
            self.refresh = self.node.create_client(Empty, profile.localization_refresh_service)
            self.node.create_timer(2.0, self._refresh_localization)
        self.level2 = None
        self.tools = EdgeTools(self)
        self.tool_listeners = []
        self.reader = None
        if rosscope_binary:
            from .rosscope import RosScopeReader
            # On demand: once at start, when an incident opens, otherwise every 10 minutes.
            self.reader = RosScopeReader(rosscope_binary.resolve(), profile.domain_id, interval_s=600.)
            self.reader.start()
        self.executor = SingleThreadedExecutor()
        self.executor.add_node(self.node)
        self.thread = threading.Thread(target=self._spin, name='ripple-edge-ros', daemon=True)

    def _refresh_localization(self):
        """AMCL publishes only when it updates, so a stationary robot's pose would read as stale.
        Ask it to process the current laser scan without motion; the pose it publishes is real evidence."""
        pose = self.node.detector.snapshot().get('pose')
        if pose is not None and pose.age_s is not None and pose.age_s < 2.0:
            return
        if self.refresh.service_is_ready():
            from std_srvs.srv import Empty
            self.refresh.call_async(Empty.Request())

    def _spin(self):
        try:
            self.executor.spin()
        except Exception:
            pass  # shutdown

    async def start(self):
        self.navigator.loop = asyncio.get_running_loop()
        self.thread.start()
        deadline = time.monotonic() + 20
        while self.level2 is None:
            try:
                self.level2 = Level2Executor(self.node, self.policy)
            except RuntimeError:
                if time.monotonic() > deadline:
                    raise RuntimeError('Another navigation client owns this robot; stop it before starting Ripple')
                await asyncio.sleep(1)
        asyncio.ensure_future(self.reconcile_keepouts())

    async def reconcile_keepouts(self):
        """After a restart, trust only what the layers file and live mask show."""
        deadline = time.monotonic() + 30
        while self.keepouts.mask is None and time.monotonic() < deadline:
            await asyncio.sleep(.5)
        if not self.keepouts.enabled:
            return
        try:
            present = {r.get('ripple_id') for r in self.keepouts._read().get('no_go_zones', [])}
        except Exception:
            present = set()
        for record in list(self.site.active_keepouts()):
            if record['id'] not in present:
                state = 'REMOVED' if record['state'] == 'REMOVING' else 'UNVERIFIED'
                record.update(state=state, verification='not in the site layers file after restart')
            else:
                ok, why = self.keepouts.check(record['polygon'], True)
                record.update(state='APPLIED' if ok else 'UNVERIFIED', verification=why)
            self.site.save_keepout(record)

    def geometry(self):
        return self.keepouts.geometry()

    def remember(self, kind, key, data):
        self.memory.request(op='memory_put', kind=kind, id=key, robot=self.profile.robot, data=data)

    def recall(self, kind, limit=500):
        return self.memory.request(op='memory_list', kind=kind, robot=self.profile.robot, limit=limit)

    def _owned(self, goal_id, handle):
        self.policy.owned_goals.add(goal_id)
        if self.level2:
            self.level2.handles[goal_id] = handle

    def rosscope_summary(self):
        if not self.reader:
            return {'status': 'not_configured'}
        snap = self.reader.snapshot().model_dump()
        report = (snap.get('value') or {}).get('report') or {}
        nav = ((report.get('tf_nav2') or {}).get('nav2') or {}) if isinstance(report, dict) else {}
        return {'status': 'fresh' if snap.get('fresh') else 'stale_or_missing', 'age_s': snap.get('age_s'),
                'lifecycle': nav.get('lifecycle_states', [])[:12]}

    def snapshot(self):
        data = self.node.snapshot()
        if self.reader:
            data['observations']['rosscope'] = self.reader.snapshot().model_dump()
        return data

    def record_tool(self, result, arguments):
        for listener in list(self.tool_listeners):
            try:
                listener(result, arguments)
            except Exception:
                pass

    async def close(self):
        try:
            if self.navigator.active():
                await asyncio.wait_for(self.navigator.cancel('edge shutting down'), 20)
        except Exception:
            pass
        self.executor.shutdown(timeout_sec=2)
        if self.reader:
            self.reader.close()
        self.node.destroy_node()
        self.journal.close()
        self.memory.close()
        self.ownership.close()
