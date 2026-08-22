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
    #: P4-3 event window. Optional: "not scheduled yet" is a real state.
    starts_at: Optional[str] = None
    ends_at: Optional[str] = None
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
    #: Roster detail (P2-5). Optional: a walk-in may supply them, a QR invite
    #: may not.
    title: Optional[str] = None
    company: Optional[str] = None
    #: VIP flag — surfaced at the desk and alerted on, never used to rank.
    is_vip: bool = False
    #: Cancelled invitee. Reversible; the name is deliberately kept.
    withdrawn_at: Optional[str] = None
    #: PII erasure. Irreversible; name/email/code are destroyed in place while
    #: attendance survives as an anonymous tally.
    erased_at: Optional[str] = None

    @property
    def is_withdrawn(self) -> bool:
        return self.withdrawn_at is not None

    @property
    def is_erased(self) -> bool:
        return self.erased_at is not None

    @property
    def on_roster(self) -> bool:
        return not self.is_withdrawn and not self.is_erased


@dataclass
class EventVariable:
    id: Optional[int] = None
    event_id: int = 0
    kind: str = ""
    value: str = ""
    notes: Optional[str] = None


@dataclass
class LibraryImage:
    """One image available to the visuals composer (P5-1).

    Uploads and blog imagery share this table: by the time an image is a base
    layer the composer does not care where it came from, but the SIDEBAR does —
    provenance has to survive into any exported asset, so origin, article title
    and source URL are stored rather than inferred later.
    """

    id: Optional[int] = None
    event_id: int = 0
    path: str = ""
    source_url: str = ""
    article_title: Optional[str] = None
    article_url: Optional[str] = None
    #: "uploaded" | "blog"
    origin: str = "uploaded"
    width: int = 0
    height: int = 0
    created_at: Optional[str] = None


@dataclass
class Staff:
    """A person who owns part of the run of show (P4-4).

    PII-scoped exactly like Attendee: erasure anonymises rather than deletes,
    because who was on shift is a safety record.
    """

    id: Optional[int] = None
    event_id: int = 0
    name: Optional[str] = None
    role: Optional[str] = None
    erased_at: Optional[str] = None

    @property
    def is_erased(self) -> bool:
        return self.erased_at is not None

    @property
    def display_name(self) -> str:
        # Explicit placeholder, never a blank: a blank name reads as a data bug
        # someone will try to "fix", while this reads as a deliberate erasure.
        return "[removed]" if self.is_erased else (self.name or "")


@dataclass
class Segment:
    """One block of the run of show (P4-4).

    Operational data, not a chain decision: edited freely like the roster. The
    playbook embeds the current version rather than a decision history, because
    decision-level granularity here would bury the real decisions in noise.
    """

    id: Optional[int] = None
    event_id: int = 0
    title: str = ""
    start: str = ""
    end: str = ""
    track: str = "Logistics"
    kind: str = "logistics"
    location: Optional[str] = None
    notes: Optional[str] = None
    owner_ids: List[int] = field(default_factory=list)
    #: Set by the day grouping when a segment began on an earlier day.
    continues_from_previous: bool = False


@dataclass
class SpendEntry:
    """One Claude API call, priced (P3-1).

    ``event_id`` is None for calls not tied to an event (model probes, harness
    runs). They are logged anyway: a ledger that only records the attributable
    part does not reconcile with the bill.

    ``error`` records why a call was unusable. A call that failed before billing
    carries usd=0; a call that billed and returned nothing usable carries its
    real cost, because an empty response is our bug, not a discount.
    """

    id: Optional[int] = None
    event_id: Optional[int] = None
    surface: str = ""
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    usd: float = 0.0
    error: Optional[str] = None
    created_at: Optional[str] = None


@dataclass
class VipAlert:
    """A VIP arrival the coordinator should know about (P2-5).

    ``delivered`` stays False until real email is configured: logging what WOULD
    be sent is honest, whereas recording it as sent would have the coordinator
    believe a notification went out when none did.
    """

    id: Optional[int] = None
    event_id: int = 0
    attendee_id: Optional[int] = None
    attendee_name: str = ""
    company: Optional[str] = None
    arrived_at: Optional[str] = None
    delivered: bool = False


@dataclass
class VenueUse:
    """A record that a venue hosted one of our events (P2-3).

    Keyed on ``venue_ref`` (a stable id) rather than the display name, so a
    renamed venue keeps its history and two venues sharing a name in different
    cities never merge.
    """

    id: Optional[int] = None
    venue_ref: str = ""
    event_id: Optional[int] = None
    event_name: str = ""
    used_on: Optional[str] = None
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
    #: Coordinator-supplied value for an option that asks for one (data.requires_value).
    #: The chosen_key guard stays intact — the option WAS offered — while the number
    #: or text the human typed rides here.
    chosen_value: Optional[str] = None
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

    @property
    def display_label(self) -> str:
        """Label as the coordinator should see it, everywhere.

        A supplied value must read identically to a preset one in the playbook,
        the deck and the history — the audit trail should not betray whether the
        number was offered or typed.
        """
        chosen = self.chosen_option
        if chosen is None:
            return ""
        if not self.chosen_value:
            return chosen.label
        template = chosen.data.get("value_label")
        if template:
            try:
                return template.format(value=int(self.chosen_value))
            except (ValueError, TypeError, KeyError):
                return template.replace("{value:,}", self.chosen_value)
        return f"{chosen.label}: {self.chosen_value}"
