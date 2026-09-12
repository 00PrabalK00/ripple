"""Typed read-only queries; missing observations are explicitly unknown."""
from .contracts import ObserveRequest, Observation, ToolResult

FIELDS = {
    'get_robot_health': ('safety','mode','localization','lifecycle','odometry'),
    'get_pose': ('pose',),
    'get_nav_status': ('goal','distance_remaining'),
    'get_diagnostics': ('lifecycle','safety','rosscope'),
    'get_recent_logs': ('findings',),
    'get_events': ('events',),
}

def query(snapshot, request):
    request = ObserveRequest.model_validate(request)
    raw = snapshot['observations']
    selected = {}
    for name in FIELDS[request.tool]:
        if name in ('events','findings'):
            selected[name] = Observation(value=snapshot[name],source='edge bounded history',age_s=0,fresh=True)
        elif name in raw:
            selected[name] = Observation.model_validate(raw[name])
        else:
            selected[name] = Observation(value=None,source='not observed',fresh=False)
    missing = [name for name,obs in selected.items() if not obs.fresh]
    return ToolResult(robot=snapshot['robot'],request_id=request.request_id,
        status='unknown' if missing else 'ok',
        reason='Missing or stale: '+', '.join(missing) if missing else 'Fresh observations returned; values may report faults',
        observations=selected,event_ids=[e['id'] for e in snapshot['events']],verified=False)
