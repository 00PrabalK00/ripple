import unittest
from pathlib import Path
from ripple_edge.contracts import Event,Observation
from ripple_edge.profile import load_profile
from ripple_edge.policy import RecoveryPolicy

class JournalFake:
    def __init__(self):self.claims=[]
    def request(self,**data):self.claims.append(data);return {'claimed':True}

class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.profile=load_profile(Path(__file__).resolve().parents[1]/'profiles/smr300.yaml')
        self.journal=JournalFake()
        self.policy=RecoveryPolicy(self.profile,self.journal,lambda:True)
        self.policy.register_incident(Event(id='incident',robot=self.profile.robot,kind='no_progress',cause='nav2_stall',evidence={},observed_at='test'))
        values={'mode':'zones','safety':{'known':True,'emergency':False,'obstacle':False,'localization':False,'override':False},
            'localization':True,'goal':{'active':False},'odometry':{'linear':0,'angular':0}}
        self.facts={k:Observation(value=v,source='test',age_s=0,fresh=True) for k,v in values.items()}
        self.request={'request_id':'r','tool':'clear_costmap','incident_id':'incident','target':'local'}
    def test_unknown_incident_and_missing_owner_deny_before_claim(self):
        self.request['incident_id']='invented'
        self.assertEqual(self.policy.claim(self.request,self.facts)['reason'],'unknown_incident')
        self.policy.exclusive_owner=lambda:False
        self.assertEqual(self.policy.claim(self.request,self.facts)['reason'],'exclusive_owner_required')
        self.assertEqual(self.journal.claims,[])
    def test_held_stop_manual_stale_and_undeclared_targets_deny(self):
        for key,value in [('safety',{'known':True,'obstacle':True}),('mode','manual'),('localization',False)]:
            saved=self.facts[key].value;self.facts[key].value=value
            self.assertFalse(self.policy.claim(self.request,self.facts)['claimed'])
            self.facts[key].value=saved
        self.facts['safety'].fresh=False
        self.assertEqual(self.policy.claim(self.request,self.facts)['reason'],'safety_unknown')
        self.assertEqual(self.journal.claims,[])
    def test_policy_supplies_fixed_budget_and_requires_human_for_reset(self):
        self.assertTrue(self.policy.claim(self.request,self.facts)['claimed'])
        self.assertEqual(self.journal.claims[0]['limit'],1)
        self.request.update(tool='lifecycle_reset',target='/controller_server')
        self.assertEqual(self.policy.claim(self.request,self.facts)['reason'],'human_authorization_required')
        self.request['target']='/safety'
        self.assertEqual(self.policy.claim(self.request,self.facts)['reason'],'undeclared_lifecycle_node')
    def test_owned_stop_does_not_require_fresh_safety(self):
        self.request.update(tool='cancel_goal',target='owned')
        self.policy.owned_goals.add('owned')
        self.assertTrue(self.policy.claim(self.request,{})['claimed'])
