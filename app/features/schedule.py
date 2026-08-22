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


def _looks_like_hour(value: str) -> bool:
    """Validate an 'HH:MM' (or 'H:MM') time without pulling in datetime
    exceptions into the form layer."""
    if not value:
        return False
    parts = value.split(":")
    if len(parts) != 2:
        return False
    hh, mm = parts
    if not (hh.isdigit() and mm.isdigit()):
        return False
    return 0 <= int(hh) <= 23 and 0 <= int(mm) <= 59


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


@dataclass
class DayWindow:
    """One calendar day of an event (P5-2).

    ``open``/``close`` are optional "HH:MM" strings — times are optional while
    the coordinator is still scoping, so a day can be "on" without decided
    hours. ``day_index`` is the ordinal position (0 = first day), used for
    ordering and for deriving the legacy starts_at/ends_at span.
    """
    date: str
    open: Optional[str] = None
    close: Optional[str] = None
    day_index: int = 0

    @property
    def is_set(self) -> bool:
        return bool(self.date)


def replace_event_days(conn: sqlite3.Connection, event_id: int,
                       days: List[DayWindow]) -> None:
    """Set an event's day windows, replacing any prior set. Idempotent enough
    to call repeatedly: the old rows are removed and the new ones written."""
    conn.execute("DELETE FROM event_days WHERE event_id=?", (event_id,))
    for i, day in enumerate(days):
        conn.execute(
            "INSERT INTO event_days (event_id, day_index, date, open, close) "
            "VALUES (?,?,?,?,?)",
            (event_id, i, day.date, day.open, day.close),
        )
    _sync_legacy_span(conn, event_id, days)


def _sync_legacy_span(conn: sqlite3.Connection, event_id: int,
                      days: List[DayWindow]) -> None:
    """Keep events.starts_at/ends_at populated from day 1 / last day so the
    board, playbook and slides — which read a single span — stay correct."""
    if not days:
        conn.execute(
            "UPDATE events SET starts_at=NULL, ends_at=NULL WHERE id=?",
            (event_id,))
        return
    first, last = days[0], days[-1]
    start = f"{first.date}T{first.open}" if first.open else first.date
    close = last.close or "23:59"
    end = f"{last.date}T{close}" if last.open or last.close else last.date
    conn.execute(
        "UPDATE events SET starts_at=?, ends_at=? WHERE id=?",
        (start, end, event_id))


def event_days(conn: sqlite3.Connection, event_id: int) -> List[DayWindow]:
    """Raw day windows in ordinal order."""
    rows = conn.execute(
        "SELECT date, open, close, day_index FROM event_days "
        "WHERE event_id=? ORDER BY day_index", (event_id,)).fetchall()
    return [DayWindow(date=r["date"], open=r["open"], close=r["close"],
                      day_index=r["day_index"]) for r in rows]


def event_day_windows(conn: sqlite3.Connection, event_id: int) -> List[DayWindow]:
    """Authoritative day windows. Falls back to the legacy starts_at/ends_at
    span when no event_days rows exist, so a pre-P5-2 event still resolves."""
    days = event_days(conn, event_id)
    if days:
        return days
    row = conn.execute(
        "SELECT starts_at, ends_at FROM events WHERE id=?", (event_id,)
    ).fetchone()
    if row and row["starts_at"]:
        start = _parse(row["starts_at"])
        end = _parse(row["ends_at"]) if row["ends_at"] else None
        return [DayWindow(
            date=start.strftime("%Y-%m-%d"),
            open=start.strftime("%H:%M"),
            close=end.strftime("%H:%M") if end else None,
        )]
    return []


def event_window(conn: sqlite3.Connection, event_id: int) -> EventWindow:
    """Backward-compatible single span for the board/playbook/slides.

    Derived from the day windows: start = day 1 open (or date), end = last day
    close (or date). For a single-day event this is exactly the old behaviour.
    """
    days = event_day_windows(conn, event_id)
    if not days:
        return EventWindow()
    first, last = days[0], days[-1]
    start = f"{first.date}T{first.open}" if first.open else first.date
    close = last.close or "23:59"
    end = f"{last.date}T{close}" if (last.open or last.close) else last.date
    return EventWindow(start=start, end=end)


def describe_schedule(conn: sqlite3.Connection, event_id: int) -> str:
    """Human phrasing for the multi-day schedule (P5-2)."""
    days = event_day_windows(conn, event_id)
    if not days:
        return "Not scheduled yet"
    if len(days) == 1:
        d = days[0]
        if d.open and d.close:
            return f"{_fmt_day(d.date)}, {d.open}–{d.close}"
        return _fmt_day(d.date)
    parts = []
    for d in days:
        if d.open and d.close:
            parts.append(f"{_fmt_day(d.date)} {d.open}–{d.close}")
        else:
            parts.append(_fmt_day(d.date))
    return "; ".join(parts)


def _fmt_day(date: str) -> str:
    return _parse(date).strftime("%-d %B %Y")
