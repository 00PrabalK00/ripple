"""Opt-in integration tests against a local PostgreSQL test database."""
import os
import unittest
from uuid import uuid4
from ripple.store import Store
from ripple.supervisor import Supervisor
from ripple.contracts import Target


@unittest.skipUnless(os.environ.get('RIPPLE_TEST_DATABASE_URL'), 'local PostgreSQL test URL not configured')
class PostgresTests(unittest.TestCase):
    def test_receipt_survives_reconnection(self):
        url = os.environ['RIPPLE_TEST_DATABASE_URL']
        marker = str(uuid4())
        store = Store(url)
        seq = store.append('durability_test', marker=marker)
        store.close()
        store = Store(url)
        try:
            self.assertTrue(any(r['sequence'] == seq and r.get('marker') == marker for r in store.receipts()))
        finally:
            store.close()

    def test_database_disconnect_prevents_send(self):
        class Robot:
            def __init__(self): self.sent = []
            def send(self, *args): self.sent.append(args)
        store = Store(os.environ['RIPPLE_TEST_DATABASE_URL'])
        self.addCleanup(store.close)
        robot = Robot()
        s = Supervisor(robot, store)
        s.reconciliation_required = False  # Test fixture has no ROS goals.
        for key,value in {'robot.mode':'zones','robot.safety_clear':True,
                          'robot.localized':True,'robot.odom_fresh':True,
                          'station.A.available':True}.items():
            s.observe(key,value,'test fixture',10)
        target=Target('A','map',1,1,0)
        p=s.propose(target,'Database failure acceptance test')
        s.approve(p.id,'test operator')
        store.process.terminate()
        store.process.wait(timeout=3)
        with self.assertRaises(RuntimeError): s.dispatch(p.id,target)
        self.assertEqual(robot.sent,[])
        self.assertTrue(store.failed)
