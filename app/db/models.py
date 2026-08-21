"""Domain models as plain dataclasses (stdlib) so the app has no ORM dependency.

Using dataclasses (not Pydantic) keeps the skeleton importable and testable with
zero third-party packages. FastAPI can still return these via .__dict__ adapters
when the web layer lands.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Event:
    id: Optional[int] = None
    name: str = ""
    city: Optional[str] = None
    audience_estimate: Optional[int] = None
    event_type: Optional[str] = None


@dataclass
class Attendee:
    id: Optional[int] = None
    event_id: int = 0
    full_name: Optional[str] = None
    email: Optional[str] = None
    checkin_code: Optional[str] = None
    attended_at: Optional[str] = None
    self_reported: bool = False


@dataclass
class EventVariable:
    id: Optional[int] = None
    event_id: int = 0
    kind: str = ""
    value: str = ""
    notes: Optional[str] = None
