import json
import subprocess
import unittest
from unittest.mock import patch
from ripple_agent.workspace import WorkspaceTools,AmbiguousCLI
from ripple_agent.communications import Contacts
ID='00000000-0000-4000-8000-000000000002'
class Journal:
    def __init__(self):self.rows={}
    def request(self,**r):
        key=r['request']
        if r['op']=='finish':self.rows[key]['result']=r['result'];return {'updated':True}
        if key in self.rows:return {'claimed':False,'reason':'duplicate_request','prior':self.rows[key]}
        self.rows[key]={};return {'claimed':True}
class CLI:
    user_id=ID;workspace_id=ID
    def __init__(self):self.calls=[];self.identity=True;self.task={}
    def verify_identity(self):
        if not self.identity:raise RuntimeError('mismatch')
    def run(self,command,payload=None):
        self.calls.append((command,payload))
        if command==('tasks','create'):self.task=dict(payload,id=ID);return self.task
        if command==('tasks','get',ID):return self.task
        return {}
class WorkspaceTests(unittest.TestCase):
    def setUp(self):self.cli=CLI();self.tools=WorkspaceTools(self.cli,Journal())
    def test_task_readback_and_duplicate_do_not_repeat_create(self):
        data={'title':'Test','description':'literal $(touch /tmp/no) `commands`'}
        first=self.tools.execute('workspace_task_create',data,'request')
        self.assertEqual(first['status'],'ok')
        second=self.tools.execute('workspace_task_create',data,'request')
        self.assertEqual(second['status'],'not_repeated')
        self.assertEqual(sum(c==('tasks','create') for c,p in self.cli.calls),1)
    def test_delivery_requires_authorized_recipient(self):
        payload={'to':['other@example.com'],'subject':'Test','body_markdown':'Report'}
        self.tools.authorize_delivery=Contacts(frozenset({'engineer@example.com'})).authorize
        result=self.tools.execute('workspace_email_send',payload,'request')
        self.assertEqual(result['status'],'needs_authorization')
        self.assertEqual(self.cli.calls,[])
        self.assertTrue(Contacts(frozenset({'engineer@example.com'})).authorize('workspace_email_send',dict(payload,to=['engineer@example.com'])))
    def test_identity_mismatch_and_extra_fields_block_writes(self):
        self.cli.identity=False
        with self.assertRaises(RuntimeError):self.tools.execute('workspace_task_create',{'title':'Test'},'x')
        with self.assertRaises(ValueError):self.tools.execute('workspace_task_create',{'title':'Test','shell':'anything'},'x')
        self.assertEqual(self.cli.calls,[])
    def test_cli_passes_content_on_stdin_without_shell(self):
        cli=AmbiguousCLI('/tmp',ID,ID)
        payload={'content':'Quotes " and `backticks` and $(echo secret)\nNew line'}
        with patch('ripple_agent.workspace.subprocess.run',return_value=subprocess.CompletedProcess([],0,'{}','')) as run:
            cli.run(('docs','create'),payload)
            self.assertNotIn('shell',run.call_args.kwargs)
            self.assertEqual(json.loads(run.call_args.kwargs['input']),payload)
            self.assertEqual(run.call_args.args[0][-2:],['docs','create'])

    def test_uncertain_write_is_not_retried(self):
        def fail(command,payload=None):raise RuntimeError('timeout')
        self.cli.run=fail
        first=self.tools.execute('workspace_task_create',{'title':'Test'},'uncertain')
        self.assertEqual(first['status'],'unknown')
        self.assertEqual(self.tools.execute('workspace_task_create',{'title':'Test'},'uncertain')['status'],'not_repeated')

    def test_email_requires_verified_send_state_and_matching_recipient(self):
        payload={'to':['engineer@example.com'],'subject':'Update','body_markdown':'Plain report'}
        current=dict(id=ID,to=[{'email':'engineer@example.com'}],subject='Update',body_markdown='Plain report',delivery_status='suppressed')
        self.cli.run=lambda command,payload=None:current
        self.assertFalse(self.tools.verify_write('workspace_email_send',payload,{'id':ID}))
        current['delivery_status']='sent'
        self.assertTrue(self.tools.verify_write('workspace_email_send',payload,{'id':ID}))
        current['to']=[{'email':'someone-else@example.com'}]
        self.assertFalse(self.tools.verify_write('workspace_email_send',payload,{'id':ID}))

    def test_nested_task_and_rich_document_are_read_back(self):
        self.cli.run=lambda command,payload=None:{'task':{'id':ID,'status':'done'}}
        self.assertTrue(self.tools.verify_write('workspace_task_update',{'status':'done'},{'id':ID}))
        document={'type':'doc','content':[{'type':'paragraph','content':[{'type':'text','text':'Report'}]}]}
        self.cli.run=lambda command,payload=None:{'title':'Status','content':json.dumps(document)}
        self.assertTrue(self.tools.verify_write('workspace_report_create',{'title':'Status','content':'Report'},{'id':ID}))
