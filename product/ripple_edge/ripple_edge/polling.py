"""Bound read-only service requests and reject delayed or superseded replies."""
import time

class ReadRequests:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.pending = {}

    def ready(self, key):
        entry = self.pending.get(key)
        if entry is None:
            return True
        future, deadline = entry
        if self.clock() < deadline and not future.done():
            return False
        self.pending.pop(key)
        if not future.done():
            future.cancel()
        return True

    def track(self, key, future, timeout_s):
        self.pending[key] = (future, self.clock() + timeout_s)

    def accept(self, key, future):
        entry = self.pending.get(key)
        if entry is None or entry[0] is not future:
            return False
        self.pending.pop(key)
        return not future.cancelled() and self.clock() < entry[1]
