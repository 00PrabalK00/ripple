"""Operator authorizations. Host channel adapters record them; models can only cite them.

An authenticated operator's message is the approval for the actions it asks for
("command-as-approval"). Each record is bounded: it expires, it has a small use
budget, and every use is kept for the journal.
"""
import time
from dataclasses import dataclass, field
from uuid import uuid4


@dataclass
class Authorization:
    id: str
    channel: str
    operator_id: str
    operator: str
    text: str
    message_id: str | None
    created: float
    uses: list[str] = field(default_factory=list)

    def public(self):
        return {'id': self.id, 'channel': self.channel, 'operator': self.operator,
                'text': self.text, 'uses': list(self.uses)}


class Authorizations:
    def __init__(self, operators, ttl_s=600.0, max_uses=4, clock=time.monotonic):
        # operators: host-configured {operator_id: display name}; never model-supplied.
        self.operators = dict(operators)
        self.ttl_s, self.max_uses, self.clock = ttl_s, max_uses, clock
        self.items = {}

    def record(self, channel, operator_id, text, message_id=None):
        """Return an authorization for an allowlisted operator, else None."""
        if operator_id not in self.operators:
            return None
        auth = Authorization(id='auth-' + uuid4().hex[:12], channel=channel, operator_id=operator_id,
                             operator=self.operators[operator_id], text=text[:4000],
                             message_id=message_id, created=self.clock())
        self.items[auth.id] = auth
        return auth

    def check(self, auth_id, action):
        """None when the authorization may be used for action, else the denial reason."""
        auth = self.items.get(auth_id or '')
        if auth is None:
            return 'unknown_authorization'
        if self.clock() - auth.created > self.ttl_s:
            return 'authorization_expired'
        if len(auth.uses) >= self.max_uses:
            return 'authorization_used_up'
        return None

    def consume(self, auth_id, action):
        reason = self.check(auth_id, action)
        if reason:
            raise PermissionError(reason)
        auth = self.items[auth_id]
        auth.uses.append(action)
        return auth

    def get(self, auth_id):
        return self.items.get(auth_id or '')
