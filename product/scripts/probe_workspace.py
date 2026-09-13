"""Create and read back agent-owned workspace artifacts. No message/email send."""
import json
from pathlib import Path
from dotenv import dotenv_values
from ripple_edge.journal import ActionJournal
from ripple_agent.workspace import AmbiguousCLI,WorkspaceTools
root=Path(__file__).resolve().parents[2]
cli=AmbiguousCLI(root,'00000000-0000-4000-8000-000000000002','00000000-0000-4000-8000-000000000001')
journal=ActionJournal(dotenv_values('/home/zuci/Ripple/.env')['DATABASE_URL'],root)
tools=WorkspaceTools(cli,journal)
results={}
def run(name,payload,key):
    result=tools.execute(name,payload,'workspace-acceptance-20260912-'+key)
    results[key]=result
    (root/'product/evidence/workspace-tools-live.json').write_text(json.dumps(results,indent=2)+'\n')
    print(key,result['status'],(result.get('data') or {}).get('id'),flush=True)
    return result.get('data') or (result.get('prior') or {}).get('result',{}).get('data') or {}
try:
    report=run('workspace_report_create',{'title':'Ripple engineering status — 12 September 2026',
        'content':'Ripple engineering status\n\nVerified: read-only MCP observations, RosScope diagnostics, PostgreSQL recovery budgets, a live local-costmap clear, and two-way Ambiguous session delivery.\n\nRemaining: live stall and escape scenarios, the always-on recovery agent, full human-assisted recovery, goal/map control through the edge, and a second Nav2 profile.\n\nThis is an integration report, not a claim that the full product is complete.'},'report')
    sheet=run('workspace_sheet_create',{'title':'Ripple verification ledger'},'sheet')
    if sheet.get('id'):
        run('workspace_sheet_append',{'id':sheet['id'],'rows':[{'A':'Capability','B':'Status'},
            {'A':'Live costmap clear','B':'Verified'}, {'A':'Full recovery demo','B':'Pending'}]},'rows')
    task=run('workspace_task_create',{'title':'Verify Ripple workspace tool integration',
        'description':'Integration-test task owned by Ripple Agent. Verify report, sheet, task and draft readback.'},'task')
    draft=run('workspace_email_draft',{'to':['engineer@example.com'],'subject':'Ripple tools integration — engineering update',
        'body_markdown':'Hi Prabal,\n\nRipple now has typed tools for tasks, reports, spreadsheets, email drafts and communications. I am verifying the integrations against Ambiguous.\n\nThe robot edge has passed live costmap-clear and durable budget tests. The complete autonomous recovery demo remains unfinished.\n\nRipple Agent'},'draft')
finally:journal.close()
