"""Run of show: chronological document and concurrency board (P4-4).

Two views over one dataset. The interesting logic is not the rendering, it is
the two things that are easy to get subtly wrong:

**Overlap detection.** A staff member on two segments whose times overlap is
*flagged, never blocked* — double-booking a floater is often deliberate, so the
tool reports it and the coordinator decides. Touching segments (09:00–12:00 then
12:00–13:00) are a handover, not a conflict, so the comparison is strict.

**Flags are computed at render, never stored.** Same precedent as the favourites
overlay: segment records stay clean, and resolving an overlap clears the flag
without a migration or a stale row.

Times are naive local throughout, matching P4-3 — the coordinator, the venue and
the staff are all in one place.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Dict, List, Optional

from app.db import repository as repo
from app.db.models import Segment
from app.features.schedule import EventWindow, _parse, window_for_event

#: Lane names seeded on a new event. Coordinator-created tracks join these.
DEFAULT_TRACKS: tuple[str, ...] = ("Logistics", "Program", "Expo floor", "VIP")

#: Drives colour accents. `track` drives lane placement; the two correlate but
#: stay independent, so a VIP moment can sit in the Program lane.
SEGMENT_KINDS: tuple[str, ...] = ("logistics", "floor", "program", "vip")

KIND_LABELS = {
    "logistics": "Logistics",
    "floor": "Expo floor",
    "program": "Program",
    "vip": "VIP",
}

#: Board columns. Visual snapping only — stored times are never rewritten,
#: because a coordinator who typed 09:07 meant 09:07.
SNAP_MINUTES = 15


def validate_segment(segment: Segment) -> Segment:
    """Check a segment before it is stored. Names every problem at once."""
    problems: List[str] = []
    if not (segment.title or "").strip():
        problems.append("a title")
    if not (segment.start or "").strip():
        problems.append("a start time")
    if not (segment.end or "").strip():
        problems.append("an end time")
    if problems:
        raise ValueError("A segment needs " + ", ".join(problems) + ".")

    if segment.kind not in SEGMENT_KINDS:
        raise ValueError(
            f"{segment.kind!r} is not a segment kind "
            f"({', '.join(SEGMENT_KINDS)})."
        )
    start, end = _parse(segment.start), _parse(segment.end)
    if end <= start:
        raise ValueError(
            f"A segment must end after it starts ({segment.end} is not after "
            f"{segment.start})."
        )
    return segment


def _overlaps(a: Segment, b: Segment) -> bool:
    """Strict overlap. Touching ranges are a handover, not a conflict."""
    a_start, a_end = _parse(a.start), _parse(a.end)
    b_start, b_end = _parse(b.start), _parse(b.end)
    return a_start < b_end and b_start < a_end


def conflicts_for(segments: List[Segment]) -> Dict[int, Dict[int, str]]:
    """Map segment id -> {staff id: 'why'} for every double-booked owner.

    Computed, never stored. Returns only genuine conflicts, so an empty dict
    means the schedule is clean rather than unchecked.
    """
    flags: Dict[int, Dict[int, str]] = {}
    for i, first in enumerate(segments):
        for second in segments[i + 1:]:
            if not _overlaps(first, second):
                continue
            shared = set(first.owner_ids) & set(second.owner_ids)
            for staff_id in shared:
                flags.setdefault(first.id, {})[staff_id] = (
                    f"also on {second.title}")
                flags.setdefault(second.id, {})[staff_id] = (
                    f"also on {first.title}")
    return flags


def group_by_day(segments: List[Segment]) -> Dict[str, List[Segment]]:
    """Group for the chronological view.

    A segment spanning midnight appears under BOTH days: someone reading the
    15th's sheet needs to know the overnight build is still running, and a
    segment filed only under its start date is invisible to them.
    """
    days: Dict[str, List[Segment]] = {}
    for segment in segments:
        start, end = _parse(segment.start), _parse(segment.end)
        cursor = start.date()
        first = True
        while cursor <= end.date():
            key = cursor.strftime("%Y-%m-%d")
            copy = Segment(
                id=segment.id, event_id=segment.event_id, title=segment.title,
                start=segment.start, end=segment.end, track=segment.track,
                kind=segment.kind, location=segment.location,
                notes=segment.notes, owner_ids=list(segment.owner_ids),
                continues_from_previous=not first,
            )
            days.setdefault(key, []).append(copy)
            cursor += timedelta(days=1)
            first = False
    for key in days:
        days[key].sort(key=lambda s: (_parse(s.start), s.id or 0))
    return dict(sorted(days.items()))


def snap_to_quarter(value: str) -> str:
    """Nearest quarter hour — a board column, not a stored time."""
    moment = _parse(value)
    minute = int(round(moment.minute / SNAP_MINUTES) * SNAP_MINUTES)
    if minute == 60:
        moment = moment.replace(minute=0) + timedelta(hours=1)
    else:
        moment = moment.replace(minute=minute)
    return moment.strftime("%Y-%m-%dT%H:%M")


def board_lanes(segments: List[Segment], window: EventWindow) -> List[dict]:
    """Lanes for the concurrency board: one row per track, time left to right.

    Positions are percentages of the event window so the template needs no
    arithmetic. Returns [] for an unscheduled event rather than inventing a
    window — a board with no time axis would be a drawing, not a schedule.
    """
    if not window.is_set or not window.end or not segments:
        return []
    origin, finish = _parse(window.start), _parse(window.end)
    span = (finish - origin).total_seconds()
    if span <= 0:
        return []

    lanes: Dict[str, List[dict]] = {}
    for segment in segments:
        start = _parse(snap_to_quarter(segment.start))
        end = _parse(snap_to_quarter(segment.end))
        left = max(0.0, (start - origin).total_seconds() / span * 100)
        right = min(100.0, (end - origin).total_seconds() / span * 100)
        if right <= left:
            right = min(100.0, left + 0.5)  # keep a hairline visible
        lanes.setdefault(segment.track, []).append({
            "segment": segment,
            "left_pct": round(left, 3),
            "width_pct": round(right - left, 3),
        })
    return [{"track": track, "blocks": blocks} for track, blocks in lanes.items()]


def now_line_pct(window: EventWindow, now: Optional[datetime] = None
                 ) -> Optional[float]:
    """Position of the live 'now' marker, or None when the event is not running."""
    if not window.is_set or not window.end:
        return None
    moment = now or datetime.now()
    origin, finish = _parse(window.start), _parse(window.end)
    if not (origin <= moment <= finish):
        return None
    span = (finish - origin).total_seconds()
    if span <= 0:
        return None
    return round((moment - origin).total_seconds() / span * 100, 3)


#: Stubs for the one-click seed. Offsets are from the event start; the
#: coordinator renames rather than invents.
_STANDARD_DAY = (
    ("Load-in", "logistics", "Logistics", 0, 3),
    ("Setup and booth build", "logistics", "Logistics", 3, 6),
    ("Vendor arrival", "logistics", "Logistics", 6, 7),
    ("Doors open", "floor", "Expo floor", 7, 15),
    ("Teardown", "logistics", "Logistics", 15, 18),
)


def seed_standard_day(conn, event_id: int) -> int:
    """Create the usual skeleton so the coordinator edits instead of inventing.

    Refuses on an unscheduled event: without a window there is nothing to hang
    the offsets off, and guessing a date would produce confidently wrong times.
    Idempotent — seeding twice does not duplicate.
    """
    window = window_for_event(conn, event_id)
    if not window.is_set:
        raise ValueError(
            "Set the event's schedule first — the standard day is built from "
            "its start and end, and without them the times would be a guess."
        )
    if repo.list_segments(conn, event_id):
        return 0

    origin = _parse(window.start)
    created = 0
    for title, kind, track, start_h, end_h in _STANDARD_DAY:
        repo.add_segment(conn, Segment(
            event_id=event_id, title=title, kind=kind, track=track,
            start=(origin + timedelta(hours=start_h)).strftime("%Y-%m-%dT%H:%M"),
            end=(origin + timedelta(hours=end_h)).strftime("%Y-%m-%dT%H:%M"),
            notes="Seeded stub — rename and adjust.",
        ))
        created += 1
    return created
