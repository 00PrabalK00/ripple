import unittest
from ripple.incidents import Incidents, diagnosis
from ripple.store import Store

class IncidentTests(unittest.TestCase):
    def test_recovery_claim_is_single_use_and_restart_does_not_replay(self):
        store=Store();m=Incidents(store);i=m.open('goal','aborted',{}, {})
        m.claim_clear(i['id'])
        with self.assertRaises(ValueError):m.claim_clear(i['id'])
        restored=Incidents(store)
        self.assertEqual(restored.items[i['id']]['state'],'NEEDS HUMAN')
        self.assertEqual(restored.items[i['id']]['recovery_attempts'],1)

    def test_missing_or_stale_evidence_is_not_physical_diagnosis(self):
        self.assertTrue(diagnosis({}))
        evidence=dict(localization=True,tf=True,safety=True,odometry=True,
            rosscope={'fresh':True,'observation':{'tf_nav2':{'nav2':{'lifecycle_states':[
                {'node':n,'state':'active [3]'} for n in
                ('/amcl','/planner_server','/controller_server','/bt_navigator')]}}}})
        self.assertFalse(diagnosis(evidence))
        evidence['rosscope']['fresh']=False
        self.assertTrue(diagnosis(evidence))
        evidence['rosscope']['fresh']=True;evidence['safety']=False
        self.assertTrue(diagnosis(evidence))

    def test_failed_journal_write_does_not_claim_recovery(self):
        store=Store();m=Incidents(store);i=m.open('g','failed',{}, {})
        def fail(*a,**kw):raise RuntimeError('database unavailable')
        store.append=fail
        with self.assertRaises(RuntimeError):m.claim_clear(i['id'])
        self.assertEqual(m.items[i['id']]['recovery_attempts'],0)
