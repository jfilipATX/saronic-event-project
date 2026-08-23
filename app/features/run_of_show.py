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
SEGMENT_KINDS: tuple[str, ...] = (
    "logistics", "floor", "program", "vip",
    "booth", "presentation", "visitor", "dinner", "panel",
)

KIND_LABELS = {
    "logistics": "Logistics",
    "floor": "Expo floor",
    "program": "Program",
    "vip": "VIP",
    "booth": "Booth",
    "presentation": "Presentation",
    "visitor": "Visitors",
    "dinner": "Dinner",
    "panel": "Panel",
}

#: P6-5 — colour per block kind for the timeline / portfolio chart. Monochrome-
#: safe: each is a distinct hue, never the brand signal-blue. Keyed by kind.
KIND_COLORS = {
    "logistics": "#6E7C88",     # steel
    "floor": "#3E7C8C",         # teal
    "program": "#162029",       # ink
    "vip": "#D8A24C",           # amber (already the conflict accent)
    "booth": "#4A6FA5",         # blue-slate
    "presentation": "#7A5BA6",   # violet
    "visitor": "#5B8C5A",       # green
    "dinner": "#A65B5B",        # terracotta
    "panel": "#B07A3C",         # bronze
}

#: Board columns. Visual snapping only — stored times are never rewritten,
#: because a coordinator who typed 09:07 meant 09:07.
SNAP_MINUTES = 15

#: FIXED zoom. Percentage widths are auto-compression by another name: they
#: squeeze a 38-hour event into whatever the screen is, which produced 40px
#: blocks with titles clipped to "Load-". A readable four-hour window that
#: scrolls beats an unreadable three-day overview that fits.
PX_PER_15_MIN = 24
PX_PER_HOUR = PX_PER_15_MIN * 4

#: A short segment stays clickable and legible; its title ellipsizes rather
#: than the block shrinking to a sliver.
MIN_BLOCK_PX = 48

#: Below this a block cannot hold the conflict text, so the outline carries the
#: flag alone (and the title attribute carries the detail).
CONFLICT_LABEL_MIN_PX = 120


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


def build_timeline(conn, segments: List[Segment]) -> Dict[str, List[dict]]:
    """P6-5 — Gantt-ready layout: per day, each segment positioned by time.

    Returns {day: [{title, kind, kind_label, color, start_time, end_time,
    left_pct, width_pct, owners, location, conflict}]}. ``left_pct``/
    ``width_pct`` are fractions of that day's active window (first start → last
    end), so the template just sets CSS left/width without re-parsing times.
    """
    from app.db.repository import get_person
    bands: Dict[str, List[Segment]] = group_by_day(segments)
    out: Dict[str, List[dict]] = {}
    conflicts = conflicts_for(segments)
    for day, segs in bands.items():
        if not segs:
            out[day] = []
            continue
        parsed = [(_parse(s.start), _parse(s.end)) for s in segs]
        lo = min(p[0] for p in parsed)
        hi = max(p[1] for p in parsed)
        span = (hi - lo).total_seconds() or 1
        row = []
        for s, (st, en) in zip(segs, parsed):
            left = (st - lo).total_seconds() / span * 100
            width = max(((en - st).total_seconds() / span * 100), 1.5)
            owners = ", ".join(
                (get_person(conn, o).name if get_person(conn, o) else f"Person {o}")
                for o in s.owner_ids
            )
            row.append({
                "title": s.title,
                "kind": s.kind,
                "kind_label": KIND_LABELS.get(s.kind, s.kind),
                "color": KIND_COLORS.get(s.kind, "#6E7C88"),
                "start_time": st.strftime("%H:%M"),
                "end_time": en.strftime("%H:%M"),
                "left_pct": round(left, 2),
                "width_pct": round(width, 2),
                "location": s.location or "",
                "owners": owners,
                "conflict": conflicts.get(s.id) or {},
            })
        out[day] = row
    return out


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


def board_width_px(window: EventWindow) -> int:
    """Total board width at fixed zoom. The board scrolls; it does not shrink."""
    if not window.is_set or not window.end:
        return 0
    span_h = (_parse(window.end) - _parse(window.start)).total_seconds() / 3600
    return max(0, int(round(span_h * PX_PER_HOUR)))


def hour_ticks(window: EventWindow) -> List[dict]:
    """The time axis. Without it a block's position is uninterpretable."""
    if not window.is_set or not window.end:
        return []
    origin, finish = _parse(window.start), _parse(window.end)
    ticks: List[dict] = []
    cursor = origin.replace(minute=0, second=0, microsecond=0)
    if cursor < origin:
        cursor += timedelta(hours=1)
    while cursor <= finish:
        offset = (cursor - origin).total_seconds() / 3600 * PX_PER_HOUR
        is_day_start = cursor.hour == 0
        ticks.append({
            "label": cursor.strftime("%H:%M"),
            "left_px": int(round(offset)),
            "is_day_start": is_day_start,
            # A day boundary is where a midnight-spanning block stops being
            # confusing, so it is labelled rather than just drawn heavier.
            "date_label": cursor.strftime("%Y-%m-%d") if is_day_start else "",
        })
        cursor += timedelta(hours=1)
    return ticks


def board_lanes(segments: List[Segment], window: EventWindow,
                flags: Optional[Dict[int, Dict[int, str]]] = None) -> List[dict]:
    """Lanes for the concurrency board: one row per track, time left to right.

    Geometry is FIXED pixels per quarter hour, not percentages of the window —
    see PX_PER_15_MIN. Returns [] for an unscheduled event rather than inventing
    a window: a board with no time axis is a drawing, not a schedule.

    ``flags`` (from conflicts_for) marks double-booked blocks. The board is the
    view that exists to expose overlaps, so it has to carry the flag itself; a
    conflict visible only in the list view defeats the point.
    """
    if not window.is_set or not window.end or not segments:
        return []
    origin = _parse(window.start)
    if (_parse(window.end) - origin).total_seconds() <= 0:
        return []
    flags = flags or {}

    lanes: Dict[str, List[dict]] = {}
    for segment in segments:
        start = _parse(snap_to_quarter(segment.start))
        end = _parse(snap_to_quarter(segment.end))
        left = (start - origin).total_seconds() / 3600 * PX_PER_HOUR
        width = max(MIN_BLOCK_PX,
                    (end - start).total_seconds() / 3600 * PX_PER_HOUR)
        conflicted = bool(flags.get(segment.id))
        lanes.setdefault(segment.track, []).append({
            "segment": segment,
            "left_px": int(round(max(0, left))),
            "width_px": int(round(width)),
            "has_conflict": conflicted,
            # The outline always shows; the words only fit on a wide block.
            "show_conflict_label": conflicted and width >= CONFLICT_LABEL_MIN_PX,
            "conflict_detail": "; ".join(sorted(flags.get(segment.id, {}).values())),
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
