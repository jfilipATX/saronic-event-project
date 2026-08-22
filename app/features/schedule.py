"""Event start/end date-times (P4-3).

Small on its own; it exists because the run of show needs a time axis with real
bounds. The invariants are what matter:

* **An end before its start is refused, never stored.** A negative-duration
  event does not look odd in one view — it breaks every downstream calculation,
  and by then the cause is three screens away.
* **Dates are optional.** A coordinator scoping an event for March does not yet
  know the hour, and forcing a placeholder manufactures confidently wrong data.
  "Not scheduled yet" is a real state and is stated rather than hidden.
* Times are stored exactly as entered (naive local, ISO-ish). The coordinator,
  the venue and the staff are all in one place; introducing timezone conversion
  would create a class of off-by-hours bug this tool has no way to resolve.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Optional

#: Accepted inputs. ``datetime-local`` yields the first; a plain date input the
#: second; the third is what SQLite hands back if a value was ever normalised.
_FORMATS = ("%Y-%m-%dT%H:%M", "%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S")


@dataclass
class EventWindow:
    start: Optional[str] = None
    end: Optional[str] = None

    @property
    def is_set(self) -> bool:
        return self.start is not None

    def _dt(self, value: Optional[str]) -> Optional[datetime]:
        return _parse(value) if value else None

    @property
    def duration_hours(self) -> Optional[float]:
        start, end = self._dt(self.start), self._dt(self.end)
        if start is None or end is None:
            return None
        return (end - start).total_seconds() / 3600.0

    @property
    def days(self) -> List[str]:
        """Every calendar day the event touches — the run of show groups by these."""
        start, end = self._dt(self.start), self._dt(self.end)
        if start is None:
            return []
        if end is None:
            return [start.strftime("%Y-%m-%d")]
        out, cursor = [], start.date()
        while cursor <= end.date():
            out.append(cursor.strftime("%Y-%m-%d"))
            cursor += timedelta(days=1)
        return out


def _parse(value: str) -> datetime:
    text = (value or "").strip()
    for fmt in _FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    raise ValueError(f"{value!r} is not a date we recognise (use YYYY-MM-DD).")


def parse_window(starts_at: str, ends_at: str) -> EventWindow:
    """Validate a submitted window. Raises ValueError with a readable message."""
    start_text = (starts_at or "").strip()
    end_text = (ends_at or "").strip()

    if not start_text and not end_text:
        return EventWindow()
    if not start_text:
        raise ValueError(
            "An end date needs a start date — a finish time alone cannot anchor "
            "the schedule."
        )

    start = _parse(start_text)
    if not end_text:
        return EventWindow(start=start_text)

    end = _parse(end_text)
    if end <= start:
        raise ValueError(
            f"The event cannot end before it starts ({end_text} is not after "
            f"{start_text})."
        )
    return EventWindow(start=start_text, end=end_text)


def window_for_event(conn, event_id: int) -> EventWindow:
    row = conn.execute(
        "SELECT starts_at, ends_at FROM events WHERE id=?", (event_id,)
    ).fetchone()
    if row is None:
        return EventWindow()
    return EventWindow(start=row["starts_at"], end=row["ends_at"])


def describe_window(window: EventWindow) -> str:
    """Human phrasing for the playbook and the schedule screen."""
    if not window.is_set:
        return "Not scheduled yet"
    start = _parse(window.start)
    if not window.end:
        return start.strftime("From %-d %B %Y, %H:%M")
    end = _parse(window.end)
    if start.date() == end.date():
        return start.strftime("%-d %B %Y, %H:%M") + end.strftime(" – %H:%M")
    if start.year == end.year and start.month == end.month:
        return (start.strftime("%-d") + end.strftime("–%-d %B %Y") +
                start.strftime(", %H:%M") + end.strftime(" – %H:%M"))
    return start.strftime("%-d %B %Y, %H:%M") + end.strftime(" – %-d %B %Y, %H:%M")
