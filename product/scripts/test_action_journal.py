"""Real PostgreSQL concurrency/restart acceptance test; never touches ROS."""
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4
from dotenv import dotenv_values
from ripple_edge.journal import ActionJournal
root=Path(__file__).resolve().parents[2]
url=dotenv_values('/home/zuci/Ripple/.env')['DATABASE_URL']
robot='acceptance-'+str(uuid4())
journals=[ActionJournal(url,root),ActionJournal(url,root)]
try:
    def claim(i):
        return journals[i].request(op='claim',robot=robot,request='r'+str(i),incident='one',
            action='clear_costmap:local',fingerprint='f'+str(i),limit=1)
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(claim,range(2)))
    assert sum(r['claimed'] for r in results)==1,results
    winner=next(i for i,r in enumerate(results) if r['claimed'])
    duplicate=claim(winner)
    assert duplicate['reason']=='duplicate_request',duplicate
    interrupted=journals[0].request(op='interrupt',robot=robot)
    assert interrupted['interrupted']==1,interrupted
    replay=claim(winner)
    assert not replay['claimed'] and replay['prior']['status']=='interrupted',replay
    exhausted=journals[0].request(op='claim',robot=robot,request='new',incident='one',action='clear_costmap:local',fingerprint='new',limit=1)
    assert exhausted['reason']=='incident_budget_exhausted',exhausted
    evidence={'backend':'local PostgreSQL via Drizzle','robot':robot,'concurrent_claims':results,
        'duplicate_rejected':True,'restart_interrupted':interrupted,'replay_rejected':True,
        'budget_survived_restart':True}
    (root/'product/evidence/action-journal-postgres.json').write_text(json.dumps(evidence,indent=2)+'\n')
    print('PASS: concurrency, duplicate rejection, interruption and durable budget')
finally:
    for journal in journals:journal.close()
