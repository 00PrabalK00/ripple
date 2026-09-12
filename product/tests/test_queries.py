import unittest
from ripple_edge.queries import query

class QueryTests(unittest.TestCase):
    def test_missing_and_stale_pose_are_unknown(self):
        snapshot={'robot':'robot','observations':{},'events':[],'findings':[]}
        request={'request_id':'x','tool':'get_pose'}
        self.assertEqual(query(snapshot,request).status,'unknown')
        snapshot['observations']['pose']={'value':{'x':1,'y':2},'source':'AMCL','age_s':20,'fresh':False}
        self.assertEqual(query(snapshot,request).status,'unknown')
        snapshot['observations']['pose']['fresh']=True
        result=query(snapshot,request)
        self.assertEqual(result.status,'ok')
        self.assertFalse(result.verified)
        self.assertEqual(result.observations['pose'].source,'AMCL')
