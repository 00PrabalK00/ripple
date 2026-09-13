"""Reconcile existing artifacts and send the operator-authorized report once."""
import json
from pathlib import Path
from dotenv import dotenv_values
from ripple_edge.journal import ActionJournal
from ripple_agent.workspace import AmbiguousCLI,WorkspaceTools
from ripple_agent.communications import Contacts
root=Path(__file__).resolve().parents[2]
config=json.loads((root/'product/config/communications.json').read_text())
cli=AmbiguousCLI(root,config['user_id'],config['workspace_id'])
journal=ActionJournal(dotenv_values('/home/zuci/Ripple/.env')['DATABASE_URL'],root)
tools=WorkspaceTools(cli,journal,Contacts(frozenset(config['email_recipients'])).authorize)
prior=json.loads((root/'product/evidence/workspace-tools-live.json').read_text())
try:
 cli.verify_identity()
 task=prior['task']['data']['task']
 task_ok=tools.verify_write('workspace_task_create',{k:task[k] for k in ('title','description','priority')},task)
 report=prior['report']['data']
 report_content='Ripple engineering status\n\nVerified: read-only MCP observations, RosScope diagnostics, PostgreSQL recovery budgets, a live local-costmap clear, and two-way Ambiguous session delivery.\n\nRemaining: live stall and escape scenarios, the always-on recovery agent, full human-assisted recovery, goal/map control through the edge, and a second Nav2 profile.\n\nThis is an integration report, not a claim that the full product is complete.'
 report_ok=tools.verify_write('workspace_report_create',{'title':report['title'],'content':report_content},report)
 assert task_ok and report_ok,(task_ok,report_ok)
 complete=tools.execute('workspace_task_update',{'id':task['id'],'status':'done'},'workspace-acceptance-20260912-task-done')
 body='Hi Prabal,\n\nRipple can now create and update tasks, write reports, create spreadsheets and append rows, and draft email through Ambiguous. These integrations have been checked against the live workspace.\n\nThis email is the sending test you authorized for engineer@example.com. The tools use durable request IDs to avoid duplicate writes and restrict delivery to configured contacts.\n\nRobot build status: read-only MCP, RosScope diagnostics, persistent recovery budgets, and a live costmap clear are verified. Full autonomous recovery, escape scenarios, and the complete hero demo are still in progress.\n\nRipple Agent'
 sent=tools.execute('workspace_email_send',{'to':['engineer@example.com'],'subject':'Ripple: workspace tools and engineering update','body_markdown':body},'workspace-acceptance-20260912-authorized-email')
 evidence={'report_readback':report_ok,'task_readback':task_ok,'task_completed':complete,
    'email':sent,'delivery_scope':'Verified provider send status is not proof of inbox delivery.'}
 (root/'product/evidence/workspace-verified-and-email.json').write_text(json.dumps(evidence,indent=2)+'\n')
 print(json.dumps({'report_readback':report_ok,'task_readback':task_ok,'task_update':complete['status'],
    'email_status':sent['status'],'email_id':(sent.get('data') or {}).get('id')}),flush=True)
finally:journal.close()
