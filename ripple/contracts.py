"""Data exchanged by the supervisor, interpretation layer and robot adapter.

Monotonic timestamps are process-local. Persisted approvals never survive restart.
"""
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class Target:
    station: str
    frame: str
    x: float
    y: float
    yaw: float


@dataclass
class Fact:
    key: str
    value: Any
    version: int
    source: str
    observed_at: float
    max_age: float | None = None

    def fresh(self, now: float) -> bool:
        return self.max_age is None or 0 <= now - self.observed_at <= self.max_age


@dataclass
class Proposal:
    id: str
    mission_id: str
    revision: int
    target: Target
    dependencies: dict[str, int]
    reason: str
    state: str = 'AWAITING APPROVAL'
    operator: str | None = None
    expires_at: float | None = None


@dataclass(frozen=True)
class RobotEvent:
    kind: str  # accepted, rejected, terminal, odometry, localization, safety, mode
    goal_id: str | None = None
    data: dict = field(default_factory=dict)


class RobotAdapter(Protocol):
    def send(self, goal_id: str, target: Target) -> None:
        """Nonblocking send; report acceptance and terminal result as queued events."""

    def cancel(self, goal_id: str) -> None:
        """Request cancellation of the owned goal; acknowledgement is not terminal."""
