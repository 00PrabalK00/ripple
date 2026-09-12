"""Single-writer approval core. Call methods only from the supervisor event loop.

ROS callbacks must enqueue events, never invoke these methods directly.
"""
from dataclasses import asdict
from time import monotonic
from uuid import uuid4
from .contracts import Fact, Proposal
from .guards import dispatch_rejection


class Supervisor:
    def __init__(self, adapter, store, clock=monotonic):
        self.adapter, self.store, self.clock = adapter, store, clock
        self.facts, self.proposals = {}, {}
        self.mission_id, self.revision = str(uuid4()), 0
        self.goal_id = None
        self.active_proposal = None
        self.terminal_at = None
        self.settled_since = None
        self.last_settled_sample = None
        self.paused = False
        self.interpreting = False
        # Persisted process-local clocks/approvals cannot be trusted after restart.
        self.reconciliation_required = bool(store.receipts())
        self.state = 'HELD' if self.reconciliation_required else 'IDLE'
        store.append('startup', reconciliation_required=self.reconciliation_required)

    def observe(self, key, value, source, max_age=None, observed_at=None):
        old = self.facts.get(key)
        version = 1 if old is None else old.version + (old.value != value)
        self.facts[key] = Fact(key, value, version, source,
                              self.clock() if observed_at is None else observed_at, max_age)
        if old is None or version != old.version:
            self.store.append('fact_changed', **asdict(self.facts[key]))
        self.tick()

    def propose(self, target, reason):
        if target.station not in ('A', 'B'):
            raise ValueError('unknown station')
        for proposal in self.proposals.values():
            if proposal.state in ('AWAITING APPROVAL', 'APPROVED'):
                proposal.state = 'EXPIRED'
        self.revision += 1
        keys = [f'station.{target.station}.available']
        if target.station == 'B':
            keys.append('camera.B')
        if any(key not in self.facts for key in keys):
            raise ValueError('missing station evidence')
        proposal = Proposal(str(uuid4()), self.mission_id, self.revision, target,
                            {key: self.facts[key].version for key in keys}, reason)
        self.proposals[proposal.id] = proposal
        self.store.append('proposal', **asdict(proposal))
        return proposal

    def approve(self, proposal_id, operator):
        proposal = self.proposals[proposal_id]
        if proposal.state != 'AWAITING APPROVAL':
            return False
        if not operator.strip():
            raise ValueError('operator identity required')
        proposal.operator = operator
        proposal.expires_at = self.clock() + 60
        proposal.state = 'APPROVED'
        self.store.append('approved', **asdict(proposal))
        self.tick()
        return proposal.state == 'APPROVED'

    def reject(self, proposal_id):
        proposal = self.proposals[proposal_id]
        if proposal.state in ('AWAITING APPROVAL', 'APPROVED'):
            proposal.state = 'REJECTED'
            self.store.append('proposal_rejected', proposal_id=proposal_id)

    def tick(self):
        now = self.clock()
        for proposal in self.proposals.values():
            if proposal.state not in ('AWAITING APPROVAL', 'APPROVED'):
                continue
            changed = [key for key, version in proposal.dependencies.items()
                       if key not in self.facts or self.facts[key].version != version
                       or not self.facts[key].fresh(now)]
            if changed or (proposal.expires_at is not None and now >= proposal.expires_at):
                proposal.state = 'EXPIRED'
                self.store.append('approval_expired', proposal_id=proposal.id,
                                  changed=changed, versions={k: f.version for k, f in self.facts.items()})
        if self.goal_id and self.active_proposal and self.state == 'EXECUTING':
            target = self.active_proposal.target
            available = self.facts.get(f'station.{target.station}.available')
            camera = self.facts.get('camera.B')
            if (available is None or available.value is not True or not available.fresh(now)
                    or (target.station == 'B' and
                        (camera is None or camera.value != 'CLEAR' or not camera.fresh(now)))):
                self.cancel('destination conditions invalidated')

    def dispatch(self, proposal_id, current_target):
        self.tick()
        proposal = self.proposals[proposal_id]
        if proposal.target != current_target and proposal.state in ('AWAITING APPROVAL', 'APPROVED'):
            proposal.state = 'EXPIRED'
            self.store.append('approval_expired', proposal_id=proposal_id,
                              changed=['destination pose'], target=asdict(current_target))
        reason = dispatch_rejection(proposal, mission_id=self.mission_id,
                                    revision=self.revision, target=current_target,
                                    facts=self.facts, now=self.clock())
        if reason is None and (self.reconciliation_required or self.goal_id is not None):
            reason = 'previous goal requires reconciliation or confirmed stop'
        if reason is None and (self.paused or self.interpreting):
            reason = 'dispatch paused'
        if reason:
            self.store.append('dispatch_rejected', proposal_id=proposal_id, reason=reason,
                              send_attempted=False, versions={k: f.version for k, f in self.facts.items()})
            return False
        goal_id = str(uuid4())
        self.store.append('send_claimed', proposal_id=proposal_id, goal_id=goal_id,
                          target=asdict(current_target), dependencies=proposal.dependencies)
        proposal.state = 'CONSUMED'
        self.goal_id, self.active_proposal = goal_id, proposal
        self.state = 'DISPATCHING'
        try:
            self.adapter.send(goal_id, current_target)
        except Exception as error:
            self.state = 'HELD'
            self.reconciliation_required = True
            self.store.append('send_uncertain', goal_id=goal_id, error=str(error))
        return True

    def accepted(self, goal_id):
        if goal_id == self.goal_id and self.state == 'DISPATCHING':
            self.state = 'EXECUTING'
            self.store.append('goal_accepted', goal_id=goal_id)
            self.tick()

    def cancel(self, reason):
        if self.goal_id is None or self.state == 'CANCELLING':
            return
        self.state = 'CANCELLING'
        self.store.append('cancel_requested', goal_id=self.goal_id, reason=reason)
        try:
            self.adapter.cancel(self.goal_id)
        except Exception as error:
            self.state = 'HELD'
            self.reconciliation_required = True
            self.store.append('cancel_uncertain', goal_id=self.goal_id, error=str(error))

    def terminal(self, goal_id, outcome, final_pose=None):
        if goal_id != self.goal_id or self.terminal_at is not None:
            return
        if outcome not in ('SUCCEEDED', 'CANCELED', 'ABORTED', 'REJECTED'):
            raise ValueError('unsupported terminal outcome')
        self.store.append('goal_terminal', goal_id=goal_id, outcome=outcome, final_pose=final_pose)
        self.state = 'ARRIVED' if outcome == 'SUCCEEDED' else 'HELD'
        self.terminal_at = self.clock()
        self.settled_since = None
        self.last_settled_sample = None

    def odometry(self, linear_speed, angular_speed, received_at):
        # Samples must arrive after terminal confirmation, remain fresh and settle
        # for 0.3 seconds. ROS adapter supplies receipt times, not ROS clock stamps.
        import math
        now = self.clock()
        if self.terminal_at is None:
            return
        if (not all(math.isfinite(v) for v in (linear_speed, angular_speed, received_at))
                or not self.terminal_at < received_at <= now
                or now - received_at > 0.5
                or abs(linear_speed) > 0.02 or abs(angular_speed) > 0.02):
            self.settled_since = None
            self.last_settled_sample = None
            return
        if self.last_settled_sample is not None and (
                received_at <= self.last_settled_sample or received_at - self.last_settled_sample > 0.5):
            self.settled_since = None
        self.last_settled_sample = received_at
        if self.settled_since is None:
            self.settled_since = received_at
        elif received_at - self.settled_since >= 0.3:
            self.store.append('stop_confirmed', goal_id=self.goal_id)
            self.goal_id = self.active_proposal = self.terminal_at = self.settled_since = None
