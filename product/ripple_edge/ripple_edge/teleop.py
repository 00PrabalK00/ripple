"""Sensor-guided teleop 'unstick' for a robot that navigation cannot free.

Publishes short, bounded velocity commands on the profile's teleop input (a human-level
priority on the mux) while reading the scans each cycle. The profile may allow
overriding a safety-controller hold, but only for a declared simulation: it does so by
switching the robot's own manual mode for the duration of the move, then restoring it.
Hard floors stay even then: a speed cap, a distance cap, and a stop if the scan in the
direction of travel reports anything inside the minimum clearance.
"""
import asyncio
import math
import time
from geometry_msgs.msg import Twist
from std_msgs.msg import String

HOLDS = ('emergency', 'obstacle', 'localization', 'override')


class Teleop:
    def __init__(self, node, profile, detector, navigator, journal):
        self.cfg = profile.teleop
        self.profile, self.detector, self.navigator, self.journal = profile, detector, navigator, journal
        self.pub = node.create_publisher(Twist, self.cfg.topic, 10) if self.cfg else None
        self.mode_pub = node.create_publisher(String, self.cfg.mode_topic, 10) if self.cfg and self.cfg.mode_topic else None

    def _val(self, facts, key):
        o = facts.get(key)
        return o.value if o is not None and o.fresh else None

    def clearance(self, facts=None):
        facts = facts or self.detector.snapshot()
        out = {}
        for name, key in (('forward', 'scan.front'), ('backward', 'scan.rear')):
            scan = self._val(facts, key)
            out[name] = None if not scan or scan.get('minimum_m') is None else round(scan['minimum_m'], 3)
        return out

    def choose(self, direction, facts):
        if direction != 'auto':
            return direction
        room = self.clearance(facts)
        options = [(d, c) for d, c in room.items() if c is not None]
        if not options:
            return 'rotate_left'
        best, gap = max(options, key=lambda o: o[1])
        return best if gap > self.cfg.min_clearance_m + 0.1 else 'rotate_left'

    def _publish(self, linear, angular):
        msg = Twist()
        msg.linear.x, msg.angular.z = float(linear), float(angular)
        self.pub.publish(msg)

    def _set_mode(self, mode):
        if self.mode_pub:
            for _ in range(3):
                self.mode_pub.publish(String(data=mode))

    async def execute(self, direction, distance_m, speed, override_safety, reason, request_id, incident=None):
        cfg = self.cfg
        if cfg is None:
            return {'status': 'denied', 'reason': 'teleop_not_declared_in_profile', 'verified': False}
        speed = min(abs(speed), cfg.max_speed_mps)
        distance_m = min(abs(distance_m), cfg.max_distance_m)
        facts = self.detector.snapshot()
        safety = self._val(facts, 'safety') or {}
        held = [k for k in HOLDS if safety.get(k)]
        if held and not override_safety:
            return {'status': 'denied', 'verified': False,
                    'reason': 'safety controller holding (' + ', '.join(held) + '); pass override_safety if the profile allows it'}
        if override_safety and not (cfg.override_safety and self.profile.simulation):
            return {'status': 'denied', 'reason': 'safety override is only allowed on a declared simulation', 'verified': False}
        if safety.get('emergency'):
            return {'status': 'denied', 'reason': 'emergency stop is active; teleop never overrides an e-stop', 'verified': False}
        if self.navigator.active():
            await self.navigator.cancel('teleop unstick')
        move = self.choose(direction, facts)
        start = self._val(self.detector.snapshot(), 'odometry_pose')
        await asyncio.to_thread(self.journal.request, op='claim', robot=self.profile.robot, request=request_id,
                                incident=incident or 'teleop', action='teleop', fingerprint=request_id, limit=100)
        overrode = bool(held and override_safety)
        if override_safety:
            self._set_mode(cfg.manual_mode)  # the robot's own manual mode relaxes its obstacle stop
            await asyncio.sleep(.4)
        linear = {'forward': speed, 'backward': -speed}.get(move, 0.0)
        angular = {'rotate_left': cfg.turn_rate, 'rotate_right': -cfg.turn_rate}.get(move, 0.0)
        target = distance_m if linear else min(math.pi / 2, distance_m * 3)
        limit = time.monotonic() + min(10.0, (target / (abs(linear) or abs(angular))) + 3)
        stopped_by = None
        try:
            while time.monotonic() < limit:
                now = self._val(self.detector.snapshot(), 'odometry_pose')
                if start and now:
                    done = (math.dist((start['x'], start['y']), (now['x'], now['y'])) if linear
                            else abs(math.remainder(now['yaw'] - start['yaw'], 2 * math.pi)))
                    if done >= target:
                        break
                if linear:
                    room = self.clearance()['forward' if linear > 0 else 'backward']
                    if room is not None and room < cfg.min_clearance_m:
                        stopped_by = f'scan shows {room:.2f} m in the direction of travel'
                        break
                self._publish(linear, angular)
                await asyncio.sleep(.1)
        finally:
            for _ in range(5):
                self._publish(0.0, 0.0)
                await asyncio.sleep(.05)
            if override_safety:
                self._set_mode(cfg.restore_mode)
        await asyncio.sleep(.5)
        end = self._val(self.detector.snapshot(), 'odometry_pose')
        moved = None
        if start and end:
            moved = round(math.dist((start['x'], start['y']), (end['x'], end['y'])) if linear
                          else abs(math.remainder(end['yaw'] - start['yaw'], 2 * math.pi)), 3)
        verified = moved is not None and moved >= 0.5 * target
        outcome = {'status': 'ok' if verified else 'failed', 'verified': verified,
                   'reason': (f"teleop {move} {'overriding the safety hold ' if overrode else ''}"
                              f"moved {moved} {'m' if linear else 'rad'}") + (f'; stopped: {stopped_by}' if stopped_by else ''),
                   'data': {'direction': move, 'moved': moved, 'target': round(target, 3), 'overrode_safety': overrode,
                            'clearance_after': self.clearance()}}
        await asyncio.to_thread(self.journal.request, op='finish', robot=self.profile.robot, request=request_id,
                                status=outcome['status'], result=outcome)
        return outcome
