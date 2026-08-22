"""Domain models as plain dataclasses (stdlib) so the app has no ORM dependency.

Using dataclasses (not Pydantic) keeps the skeleton importable and testable with
zero third-party packages. FastAPI can still return these via .__dict__ adapters
when the web layer lands.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class Event:
    id: Optional[int] = None
    name: str = ""
    city: Optional[str] = None
    audience_estimate: Optional[int] = None
    event_type: Optional[str] = None
    created_at: Optional[str] = None


@dataclass
class Attendee:
    id: Optional[int] = None
    event_id: int = 0
    full_name: Optional[str] = None
    email: Optional[str] = None
    checkin_code: Optional[str] = None
    attended_at: Optional[str] = None
    self_reported: bool = False
    created_at: Optional[str] = None


@dataclass
class EventVariable:
    id: Optional[int] = None
    event_id: int = 0
    kind: str = ""
    value: str = ""
    notes: Optional[str] = None


# ─────────────────────────────────────────────────────────────────────────────
# T11.5 — decision persistence
#
# The tool never auto-decides. Each feature offers the human coordinator a set
# of options with reasoning; the human picks; we record the whole slate, not
# just the winner. The playbook later replays this as an audit trail.
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class DecisionOption:
    """One choice offered to the coordinator, with the reasoning behind it."""

    key: str
    label: str
    reasoning: str = ""
    #: Feature-specific payload (capacity, cost, url, ...). Kept opaque on purpose
    #: so a new feature can attach its own fields without a schema migration.
    data: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Decision:
    """A decision point in the coordinator's workflow.

    ``chosen_key is None`` means the options are staged but the human has not
    decided yet — that is a legitimate state, and the playbook surfaces it as an
    open question rather than silently guessing.
    """

    id: Optional[int] = None
    event_id: int = 0
    step: str = ""
    question: str = ""
    options: List[DecisionOption] = field(default_factory=list)
    chosen_key: Optional[str] = None
    decided_by: Optional[str] = None
    decided_at: Optional[str] = None
    note: Optional[str] = None
    #: id of the decision that replaced this one; None means this is the live one.
    superseded_by: Optional[int] = None
    #: Set when the tool genuinely has no options to offer (e.g. no venue data for
    #: the city). "We have nothing for you, and here is why" is information the
    #: coordinator needs — it is a legitimate state, not a failure.
    blocked_reason: Optional[str] = None

    @property
    def is_blocked(self) -> bool:
        return bool(self.blocked_reason)

    @property
    def is_pending(self) -> bool:
        return self.chosen_key is None

    @property
    def chosen_option(self) -> Optional[DecisionOption]:
        if self.chosen_key is None:
            return None
        return next((o for o in self.options if o.key == self.chosen_key), None)

    @property
    def alternatives(self) -> List[DecisionOption]:
        """Roads not taken — preserved so the human can revisit the trade-off."""
        return [o for o in self.options if o.key != self.chosen_key]
