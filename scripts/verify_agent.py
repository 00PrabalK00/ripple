"""Live OpenRouter semantic checks; no robot or supervisor mutation."""
import concurrent.futures
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv
from ripple.agent import interpret
load_dotenv('.env')
context={'state':'EXECUTING','goal_id':'test-fixture-A','targets':{'A':{'station':'A'},'B':{'station':'B'}},
 'proposals':[{'target':{'station':'A'},'state':'CONSUMED','reason':'handoff'}],
 'facts':{'station.A.available':{'value':True,'fresh':True},'station.B.available':{'value':True,'fresh':True},'camera.B':{'value':'CLEAR','fresh':True}}}
cases=[('closure','Packing A is being used for inspection now. Use another available handoff station.',context),
 ('alternate','A cannot accept handoffs during inspection; find a currently available alternative.',context),
 ('irrelevant','The cafeteria menu has changed today.',context),
 ('ambiguous','Close one of the stations.',dict(context,state='IDLE',goal_id=None,proposals=[]))]
def run(case):
 name,text,ctx=case
 result=interpret(text,ctx)
 if name in ('closure','alternate'):
  passed=result.destination=='B' and any(u.station=='A' and not u.available for u in result.updates)
 elif name=='irrelevant':passed=result.kind=='irrelevant' and not result.updates and result.destination is None
 else:passed=result.kind=='clarify' and bool(result.clarification) and not result.updates
 return {'case':name,'passed':passed,'result':result.model_dump()}
with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
 results=list(pool.map(run,cases))
Path('evidence').mkdir(exist_ok=True)
Path('evidence/agent-live-checks.json').write_text(json.dumps(results,indent=2)+'\n')
print(json.dumps([{'case':r['case'],'passed':r['passed']} for r in results],indent=2))
raise SystemExit(0 if all(r['passed'] for r in results) else 1)
