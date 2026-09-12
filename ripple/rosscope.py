"""Bounded read-only RosScope collector; never blocks the mission owner."""
import json
import os
from pathlib import Path
import subprocess
import threading
import time
from .contracts import RobotEvent


class RosScopeObserver:
    def __init__(self, events, root):
        self.events = events
        self.binary = Path(os.environ.get('ROSSCOPE_OBSERVER', root / 'build/rosscope-observe'))
        self.stop = threading.Event()
        self.process = None
        self.thread = threading.Thread(target=self.run, name='rosscope-observer', daemon=True)
        self.thread.start()

    def run(self):
        while not self.stop.is_set():
            started = time.monotonic()
            try:
                # RosScope needs ROS environment, but no model/database credentials.
                env = {k:v for k,v in os.environ.items() if not any(
                    word in k.upper() for word in ('KEY', 'TOKEN', 'PASSWORD', 'SECRET', 'DATABASE_URL'))}
                env["ROSSCOPE_COMMAND_TIMEOUT_MS"] = "8000"
                self.process = subprocess.Popen([str(self.binary)], env=env,
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                    start_new_session=True)
                try:
                    output, _ = self.process.communicate(timeout=150)
                except subprocess.TimeoutExpired:
                    self._terminate()
                    self.process.communicate()
                    raise RuntimeError('RosScope observation exceeded 150 seconds')
                if self.process.returncode:
                    raise RuntimeError('RosScope collector failed')
                result = json.loads(output)
                if result.get('source') != 'RosScope' or result.get('schema_version') != 1:
                    raise ValueError('Unsupported RosScope observation')
                data = {'observation': result, 'error': None}
            except Exception as error:
                data = {'observation': None, 'error': str(error)}
            self.events.put(RobotEvent('rosscope', None, dict(data,
                received_at=time.monotonic(), started_at=started)))
            self.stop.wait(5)

    def _terminate(self):
        if self.process and self.process.poll() is None:
            import signal
            try:
                os.killpg(self.process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass

    def close(self):
        self.stop.set()
        self._terminate()
        self.thread.join(timeout=3)


def snapshot(observation, now):
    """Collection duration counts toward age; missing data never means healthy."""
    if not observation:
        return {'status': 'collecting', 'source': 'RosScope', 'fresh': False}
    age = now - observation['started_at']
    raw = observation.get('observation')
    return dict(source='RosScope', status='available' if raw and age < 90 else 'unavailable',
                fresh=bool(raw and age < 90), age_seconds=round(age, 1),
                error=observation.get('error'), observation=raw)
