"""Durable, bounded recovery policy. A retry is always a fresh motion proposal."""
from uuid import uuid4
from datetime import datetime, timezone


class Incidents:
    def __init__(self, store):
        self.store = store
        self.items = {}
        for r in store.receipts():
            if r['kind'] in ('incident_opened', 'incident_updated'):
                item = {k:v for k,v in r.items() if k not in ('kind','sequence','time')}
                self.items[item['id']] = item
        # Never resume an interrupted software recovery automatically.
        for item in list(self.items.values()):
            if item['state'] in ('DIAGNOSING','CLEARING COSTMAPS','REPLANNING','VERIFYING STOP','AWAITING RETRY APPROVAL'):
                self.update(item['id'], state='NEEDS HUMAN',
                            question='Recovery was interrupted. Inspect the robot before a fresh attempt.')

    def open(self, goal_id, symptom, target, evidence):
        item = dict(id=str(uuid4()), goal_id=goal_id, symptom=symptom, target=target,
                    state='DIAGNOSING', evidence=evidence, recovery_attempts=0,
                    created_at=datetime.now(timezone.utc).isoformat(),
                    question='Checking navigation evidence before recovery.', human_context=None)
        self.store.append('incident_opened', **item)
        self.items[item['id']] = item
        return item

    def update(self, incident_id, **changes):
        item = dict(self.items[incident_id], **changes)
        self.store.append('incident_updated', **item)
        self.items[incident_id] = item
        return item

    def claim_clear(self, incident_id):
        item = self.items[incident_id]
        if item['state'] != 'DIAGNOSING' or item['recovery_attempts'] != 0:
            raise ValueError('Automatic recovery budget exhausted')
        return self.update(incident_id, state='CLEARING COSTMAPS', recovery_attempts=1)


def diagnosis(evidence):
    """Do not turn missing evidence or an inactive lifecycle into physical blockage."""
    failures=[]
    for key in ('localization','tf','safety','odometry'):
        if evidence.get(key) is not True: failures.append(key+' unavailable or unhealthy')
    scope=evidence.get('rosscope',{})
    rows=(scope.get('observation') or {}).get('tf_nav2',{}).get('nav2',{}).get('lifecycle_states',[])
    states={r['node']:r['state'] for r in rows}
    required=('/amcl','/planner_server','/controller_server','/bt_navigator')
    if not scope.get('fresh') or any(not states.get(k,'').startswith('active') for k in required):
        failures.append('fresh active Nav2 lifecycle evidence missing')
    return failures
