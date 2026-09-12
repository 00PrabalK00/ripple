import unittest
from ripple.rosscope import snapshot
from ripple.supervisor import Supervisor
from ripple.store import Store
from ripple.contracts import Target

class SimulationMonitoringTests(unittest.TestCase):
    def test_missing_and_stale_observations_never_healthy(self):
        self.assertFalse(snapshot(None, 100)['fresh'])
        o={'started_at': 10, 'observation': {'source':'RosScope'}, 'error':None}
        self.assertFalse(snapshot(o, 101)['fresh'])
        self.assertTrue(snapshot(o, 20)['fresh'])
        self.assertEqual(snapshot(o, 101)['status'], 'unavailable')

    def test_simulator_b_has_no_camera_dependency_but_requires_safety(self):
        class Adapter:
            def send(self,*args): self.sent=True
        a=Adapter();a.sent=False
        s=Supervisor(a, Store(), clock=lambda:100, camera_required=False)
        for k,v in {'station.B.available':True,'robot.mode':'zones',
                    'robot.safety_clear':False,'robot.localized':True,'robot.odom_fresh':True}.items():
            s.observe(k,v,'simulation',max_age=1)
        t=Target('B','map',4.21,2.18,0)
        p=s.propose(t,'simulator mission')
        self.assertNotIn('camera.B', p.dependencies)
        s.approve(p.id,'operator')
        self.assertFalse(s.dispatch(p.id,t))
        self.assertFalse(a.sent)
        s.observe('robot.safety_clear',True,'simulation',max_age=1)
        p=s.propose(t,'fresh mission');s.approve(p.id,'operator')
        self.assertTrue(s.dispatch(p.id,t));self.assertTrue(a.sent)
