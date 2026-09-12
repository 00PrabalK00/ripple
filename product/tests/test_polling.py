from concurrent.futures import Future
import unittest
from ripple_edge.polling import ReadRequests

class ReadRequestsTests(unittest.TestCase):
    def setUp(self):
        self.now = 0.
        self.requests = ReadRequests(lambda: self.now)

    def test_hung_request_expires_and_replacement_ignores_old_reply(self):
        old = Future()
        old.set_running_or_notify_cancel()
        self.requests.track('safety', old, 1.)
        self.assertFalse(self.requests.ready('safety'))
        self.now = 1.
        self.assertTrue(self.requests.ready('safety'))
        new = Future()
        self.requests.track('safety', new, 1.)
        old.set_result('late healthy')
        self.assertFalse(self.requests.accept('safety', old))
        new.set_result('holding')
        self.assertTrue(self.requests.accept('safety', new))
        self.assertFalse(self.requests.accept('safety', new))

    def test_late_completion_without_poll_does_not_become_fresh(self):
        future = Future()
        self.requests.track('lifecycle', future, 5.)
        self.now = 5.
        future.set_result('active')
        self.assertFalse(self.requests.accept('lifecycle', future))
        self.assertTrue(self.requests.ready('lifecycle'))
