"""Pure approval guards, also run immediately before an adapter send."""
import math
from .contracts import Fact, Proposal, Target


def parse_safety_status(success: bool, message: str) -> bool:
    if not success:
        return False
    fields = {}
    required = {'E-Stop', 'Low Confidence Stop', 'Obstacle Stop', 'Manual Override', 'Control Mode'}
    for line in message.splitlines():
        if ':' in line:
            key, value = line.strip().split(':', 1)
            if key not in required:
                continue
            if key in fields:
                return False
            fields[key] = value.strip()
    return all(fields.get(key) == value for key, value in {
        'E-Stop': 'inactive', 'Low Confidence Stop': 'inactive',
        'Obstacle Stop': 'inactive', 'Manual Override': 'disabled',
        'Control Mode': 'zones',
    }.items())


def dispatch_rejection(proposal: Proposal, *, mission_id: str, revision: int,
                       target: Target, facts: dict[str, Fact], now: float) -> str | None:
    if proposal.state != 'APPROVED':
        return 'approval is not unused and valid'
    if proposal.expires_at is None or now >= proposal.expires_at:
        return 'approval lifetime expired'
    if (proposal.mission_id, proposal.revision) != (mission_id, revision):
        return 'mission revision changed'
    if proposal.target != target:
        return 'destination pose changed'
    if not target.frame or not all(math.isfinite(v) for v in (target.x, target.y, target.yaw)):
        return 'invalid destination pose'
    for key, version in proposal.dependencies.items():
        fact = facts.get(key)
        if fact is None or fact.version != version or not fact.fresh(now):
            return f'dependency changed or stale: {key}'
    requirements = {'robot.mode': 'zones', 'robot.safety_clear': True,
                    'robot.localized': True, 'robot.odom_fresh': True,
                    f'station.{target.station}.available': True}
    if target.station == 'B':
        requirements['camera.B'] = 'CLEAR'
    for key, expected in requirements.items():
        fact = facts.get(key)
        if fact is None or type(fact.value) is not type(expected) or fact.value != expected or not fact.fresh(now):
            return f'condition unavailable or stale: {key}'
        if key.startswith('robot.') or key.startswith('camera.'):
            if fact.max_age is None:
                return f'freshness limit missing: {key}'
    required_dependencies = {f'station.{target.station}.available'}
    if target.station == 'B':
        required_dependencies.add('camera.B')
    if not required_dependencies.issubset(proposal.dependencies):
        return 'proposal is missing required dependencies'
    return None
