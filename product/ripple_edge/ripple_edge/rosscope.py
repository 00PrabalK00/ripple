"""Read-only RosScope bridge, configured by the edge operator, never a tool caller."""
import json
import os
import signal
import subprocess
import tempfile
import threading
import time
from .contracts import Observation

class RosScopeReader:
    def __init__(self, binary, domain_id, *, timeout_s=150., max_age_s=90., interval_s=5.):
        self.binary = str(binary)
        self.domain_id = domain_id
        self.timeout_s = timeout_s
        self.max_age_s = max_age_s
        self.interval_s = interval_s  # a collection shells out to the ros2 CLI; keep it off the hot path
        self.stop = threading.Event()
        self.wake = threading.Event()
        self.lock = threading.Lock()
        self.value = None
        self.started = None
        self.error = None
        self.thread = None

    def start(self):
        self.thread = threading.Thread(target=self.run, name='edge-rosscope', daemon=True)
        self.thread.start()

    def collect(self):
        started = time.monotonic()
        # Inherit only runtime discovery/library settings; no application credentials.
        names = {'PATH','HOME','LD_LIBRARY_PATH','PYTHONPATH','AMENT_PREFIX_PATH',
                 'CMAKE_PREFIX_PATH','COLCON_PREFIX_PATH','ROS_DISTRO','ROS_VERSION',
                 'ROS_PYTHON_VERSION','RMW_IMPLEMENTATION','ROS_LOCALHOST_ONLY',
                 'CYCLONEDDS_URI','FASTRTPS_DEFAULT_PROFILES_FILE'}
        env = {k:v for k,v in os.environ.items() if k in names}
        env.update(ROS_DOMAIN_ID=str(self.domain_id), ROSSCOPE_COMMAND_TIMEOUT_MS='8000')
        result = None
        error = None
        try:
            with tempfile.TemporaryFile() as output:
                process = subprocess.Popen([self.binary], env=env, stdout=output,
                    stderr=subprocess.DEVNULL, start_new_session=True)
                try:
                    while process.poll() is None:
                        if self.stop.wait(.1) or time.monotonic()-started >= self.timeout_s:
                            raise TimeoutError('RosScope collection stopped or timed out')
                    if process.returncode:
                        raise ValueError('RosScope collector failed')
                    output.seek(0)
                    data = output.read(2_000_001)
                    if len(data)>2_000_000:
                        raise ValueError('RosScope response exceeded size limit')
                    result = json.loads(data)
                    if (not isinstance(result,dict) or result.get('source')!='RosScope'
                        or result.get('schema_version')!=1
                        or str(result.get('domain'))!=str(self.domain_id)):
                        raise ValueError('RosScope response contract or domain mismatch')
                finally:
                    # Also reap any child CLI still running after the bridge exits.
                    try:os.killpg(process.pid,signal.SIGKILL)
                    except ProcessLookupError:pass
                    process.wait(timeout=2)
        except Exception as exc:
            result = None
            error = type(exc).__name__ + ': RosScope observation unavailable'
        with self.lock:
            self.value,self.started,self.error = result,started,error

    def request(self):
        """Ask for a fresh collection now (e.g. when an incident opens)."""
        self.wake.set()

    def run(self):
        while not self.stop.is_set():
            self.collect()
            self.wake.clear()
            deadline = time.monotonic() + self.interval_s
            while not self.stop.is_set() and not self.wake.is_set() and time.monotonic() < deadline:
                self.stop.wait(.5)

    def snapshot(self):
        with self.lock:
            age = None if self.started is None else time.monotonic()-self.started
            return Observation(value={'report':self.value,'error':self.error},
                source='RosScope read-only bridge',age_s=age,
                fresh=bool(self.value is not None and age < self.max_age_s))

    def close(self):
        self.stop.set()
        if self.thread:self.thread.join(timeout=3)
