"""P5-2 — flexible / multi-day dates with per-day hour windows.

The original model was a single start/end span. Saronic events are often
multi-day with different hours each day (e.g. 3 days x 10:00-16:00), or have no
times at all while still scoping. This feature replaces the single span with a
list of *day windows*: each day has a date and an optional open/close hour.

Invariants pinned here:

* **A single day with no hours is valid** — the event is "on" that day but the
  hours are not yet decided. This is the "times optional" half of P5-2.
* **Multi-day with per-day hours** is the headline case: day 1 10:00-16:00,
  day 2 09:00-17:00, day 3 12:00-15:00 all store and reconstruct.
* **Backward compatibility**: an event with the old starts_at/ends_at columns
  still produces a sensible day list (one day, full span), so the board,
  playbook and slides — which read window_for_event — keep working without a
  rewrite landing on top of P5-5's just-merged staff work.
* **The board axis spans all days**, not just day 1 — a 3-day event draws a
  continuous timeline, not three disconnected boards.
* **Times optional means segments can still exist**; the day window only needs
  a date to anchor the schedule.
"""
from __future__ import annotations

import sqlite3

import pytest


@pytest.fixture()
def conn():
    import app.db.repository as repo
    from app.db import schema_sql_text as sql

    handle = sqlite3.connect(":memory:")
    handle.row_factory = sqlite3.Row
    handle.executescript(sql.SCHEMA)
    repo.apply_migrations(handle)
    return handle


def _make_event(conn, **kw):
    import app.db.repository as repo
    from app.db.models import Event

    defaults = dict(name="Fleet Week", city="Austin", state="TX", country="US")
    defaults.update(kw)
    return repo.create_event(conn, Event(**defaults))


def _set_days(conn, event_id, days):
    import app.db.repository as repo

    repo.replace_event_days(conn, event_id, days)


class TestDayWindows:
    def test_single_day_without_hours(self, conn):
        """Times optional: a day with no open/close is a valid schedule."""
        import app.db.repository as repo
        from app.features.schedule import DayWindow

        eid = _make_event(conn)
        _set_days(conn, eid, [DayWindow(date="2026-03-14")])
        days = repo.event_days(conn, eid)
        assert len(days) == 1
        assert days[0].date == "2026-03-14"
        assert days[0].open is None and days[0].close is None

    def test_multi_day_with_per_day_hours(self, conn):
        import app.db.repository as repo
        from app.features.schedule import DayWindow

        eid = _make_event(conn)
        _set_days(conn, eid, [
            DayWindow(date="2026-03-14", open="10:00", close="16:00"),
            DayWindow(date="2026-03-15", open="09:00", close="17:00"),
            DayWindow(date="2026-03-16", open="12:00", close="15:00"),
        ])
        days = repo.event_days(conn, eid)
        assert [d.date for d in days] == ["2026-03-14", "2026-03-15", "2026-03-16"]
        assert days[0].open == "10:00" and days[0].close == "16:00"
        assert days[2].open == "12:00"

    def test_replace_event_days_is_idempotent(self, conn):
        import app.db.repository as repo
        from app.features.schedule import DayWindow

        eid = _make_event(conn)
        _set_days(conn, eid, [DayWindow(date="2026-03-14", open="10:00", close="16:00")])
        # Replacing with the same day must not duplicate.
        _set_days(conn, eid, [DayWindow(date="2026-03-14", open="10:00", close="16:00")])
        assert len(repo.event_days(conn, eid)) == 1


class TestBackwardCompat:
    def test_old_single_span_yields_one_day(self, conn):
        """An event created with starts_at/ends_at (no event_days rows) must
        still produce a day list so the board/playbook/slides keep working."""
        import app.db.repository as repo
        import app.features.schedule as sched
        from app.features.schedule import EventWindow

        eid = _make_event(conn)
        # The legacy path: schedule stored via set_event_window (starts_at/ends_at).
        repo.set_event_window(conn, eid, EventWindow(
            start="2026-03-14T10:00", end="2026-03-14T16:00"))
        days = sched.event_day_windows(conn, eid)
        assert len(days) == 1
        assert days[0].date == "2026-03-14"
        assert days[0].open == "10:00" and days[0].close == "16:00"


class TestBoardAxisSpansAllDays:
    def test_board_span_covers_all_days_not_just_first(self, conn):
        """The board's continuous axis must span the full multi-day window,
        not stop at the end of day 1."""
        import app.db.repository as repo
        import app.features.schedule as sched
        import app.features.run_of_show as ros
        from app.features.schedule import DayWindow

        eid = _make_event(conn)
        _set_days(conn, eid, [
            DayWindow(date="2026-03-14", open="10:00", close="16:00"),
            DayWindow(date="2026-03-15", open="09:00", close="17:00"),
        ])
        window = sched.event_window(conn, eid)
        assert window.is_set
        # 14th 10-16 (6h) + 15th 09-17 (8h) + the overnight gap 16:00->09:00
        # (17h) = 31 hours of axis.
        px = ros.board_width_px(window)
        assert px == 31 * ros.PX_PER_HOUR

    def test_unscheduled_event_has_no_board(self, conn):
        import app.db.repository as repo
        import app.features.schedule as sched
        import app.features.run_of_show as ros

        eid = _make_event(conn)
        window = sched.event_window(conn, eid)
        assert not window.is_set
        assert ros.board_width_px(window) == 0


class TestDescribe:
    def test_describe_multi_day(self, conn):
        import app.db.repository as repo
        import app.features.schedule as sched
        from app.features.schedule import DayWindow

        eid = _make_event(conn)
        _set_days(conn, eid, [
            DayWindow(date="2026-03-14", open="10:00", close="16:00"),
            DayWindow(date="2026-03-15", open="09:00", close="17:00"),
        ])
        text = sched.describe_schedule(conn, eid)
        assert "14" in text and "15" in text

    def test_describe_not_scheduled_yet(self, conn):
        import app.db.repository as repo
        import app.features.schedule as sched

        eid = _make_event(conn)
        assert sched.describe_schedule(conn, eid) == "Not scheduled yet"
