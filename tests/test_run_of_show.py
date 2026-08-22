"""P4-4 — run of show: staff, segments, and double-booking detection.

Two views over one dataset. The engine tests here cover the six acceptance
checks in ``docs/p4-4-run-of-show-spec.md``; the ones that carry real risk are
overlap detection and the midnight-spanning segment, because both are easy to
get subtly wrong in a way that looks right on a happy-path screen.

Design rules pinned here:

* **Double-booking is flagged, never blocked.** Putting one person on two
  concurrent segments is often deliberate (a floater), so the tool reports it
  and the coordinator decides.
* **Flags are computed at render, not stored.** Same precedent as the
  favourites overlay: segment records stay clean, and a resolved overlap clears
  without a migration.
* **Staff erasure anonymises historical segments** rather than deleting them —
  the record of who was on shift is a safety record, exactly like check-in.
"""
from __future__ import annotations

import sqlite3

import pytest

from app.db import repository as repo, schema_sql_text as sql
from app.db.models import Event, Person, Segment
from app.features.run_of_show import (
    DEFAULT_TRACKS,
    SEGMENT_KINDS,
    board_lanes,
    conflicts_for,
    group_by_day,
    seed_standard_day,
    snap_to_quarter,
    validate_segment,
)
from app.features.schedule import parse_window


@pytest.fixture()
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(sql.SCHEMA)
    return c


@pytest.fixture()
def event(conn) -> int:
    eid = repo.create_event(conn, Event(name="Fleet Week", city="Austin"))
    repo.set_event_window(conn, eid, parse_window("2026-03-14T06:00",
                                                  "2026-03-16T20:00"))
    return eid


@pytest.fixture()
def staff(conn, event) -> dict:
    out = {}
    for name, role in (("Dana Reyes", "Booth lead"),
                       ("Sam Okoye", "A/V"),
                       ("Ada Fournier", "Logistics")):
        pid = repo.add_person(conn, Person(name=name, role=role))
        repo.assign_staff(conn, event, pid, role=role)
        out[name] = pid
    return out


def _seg(event_id, title, start, end, track="Logistics", kind="logistics",
         owners=()):
    return Segment(event_id=event_id, title=title, start=start, end=end,
                   track=track, kind=kind, owner_ids=list(owners))


class TestStaff:
    """The pool is global; event membership is a join (P5-5)."""

    def test_a_person_is_added_to_the_global_pool(self, conn, event):
        pid = repo.add_person(conn, Person(name="Dana Reyes", role="Booth lead"))
        assert [p.name for p in repo.list_people(conn)] == ["Dana Reyes"]

    def test_assigning_to_an_event_is_a_join_not_a_copy(self, conn, event):
        pid = repo.add_person(conn, Person(name="Dana Reyes", role="Booth lead"))
        repo.assign_staff(conn, event, pid, role="Booth lead")
        other = repo.create_event(conn, Event(name="Other", city="Austin"))
        repo.assign_staff(conn, other, pid)
        # Same person id, one pool entry, two event assignments.
        assert [p.id for p in repo.list_people(conn)] == [pid]
        assert repo.event_staff(conn, event) == [pid]
        assert repo.event_staff(conn, other) == [pid]

    def test_a_person_needs_a_name(self, conn, event):
        with pytest.raises(ValueError, match="name"):
            repo.add_person(conn, Person(name="  ", role="X"))

    def test_event_assignment_is_not_shared_unless_added(self, conn, event):
        other = repo.create_event(conn, Event(name="Other", city="Austin"))
        pid = repo.add_person(conn, Person(name="Dana", role="Lead"))
        repo.assign_staff(conn, event, pid)
        assert repo.event_staff(conn, other) == []


class TestSegments:
    def test_a_segment_round_trips(self, conn, event):
        sid = repo.add_segment(conn, _seg(event, "Load-in",
                                          "2026-03-14T06:00", "2026-03-14T10:00"))
        segment = repo.get_segment(conn, sid)
        assert segment.title == "Load-in"
        assert segment.track == "Logistics"

    def test_segments_come_back_in_time_order(self, conn, event):
        repo.add_segment(conn, _seg(event, "Doors", "2026-03-14T09:00",
                                    "2026-03-14T17:00"))
        repo.add_segment(conn, _seg(event, "Load-in", "2026-03-14T06:00",
                                    "2026-03-14T09:00"))
        titles = [s.title for s in repo.list_segments(conn, event)]
        assert titles == ["Load-in", "Doors"]

    def test_a_segment_can_be_edited(self, conn, event):
        sid = repo.add_segment(conn, _seg(event, "Load-in", "2026-03-14T06:00",
                                          "2026-03-14T10:00"))
        segment = repo.get_segment(conn, sid)
        segment.title = "Load-in (dock B)"
        repo.update_segment(conn, segment)
        assert repo.get_segment(conn, sid).title == "Load-in (dock B)"

    def test_a_segment_can_be_deleted(self, conn, event):
        sid = repo.add_segment(conn, _seg(event, "Load-in", "2026-03-14T06:00",
                                          "2026-03-14T10:00"))
        repo.delete_segment(conn, sid)
        assert repo.list_segments(conn, event) == []

    def test_owners_survive_a_round_trip(self, conn, event, staff):
        sid = repo.add_segment(conn, _seg(
            event, "Doors", "2026-03-14T09:00", "2026-03-14T17:00",
            owners=[staff["Dana Reyes"], staff["Sam Okoye"]]))
        assert set(repo.get_segment(conn, sid).owner_ids) == {
            staff["Dana Reyes"], staff["Sam Okoye"]}


class TestValidation:
    def test_an_end_before_the_start_is_refused(self, event):
        with pytest.raises(ValueError, match="before|after"):
            validate_segment(_seg(event, "Backwards", "2026-03-14T10:00",
                                  "2026-03-14T06:00"))

    def test_a_title_is_required(self, event):
        with pytest.raises(ValueError, match="title"):
            validate_segment(_seg(event, "  ", "2026-03-14T06:00",
                                  "2026-03-14T10:00"))

    def test_every_missing_field_is_named_at_once(self, event):
        """Walk-in form precedent: one round trip, not four."""
        with pytest.raises(ValueError) as exc:
            validate_segment(_seg(event, "", "", ""))
        message = str(exc.value).lower()
        assert "title" in message and "start" in message

    def test_an_unknown_kind_is_refused(self, event):
        with pytest.raises(ValueError, match="kind"):
            validate_segment(_seg(event, "X", "2026-03-14T06:00",
                                  "2026-03-14T10:00", kind="party"))

    def test_a_zero_length_segment_is_refused(self, event):
        with pytest.raises(ValueError):
            validate_segment(_seg(event, "Instant", "2026-03-14T06:00",
                                  "2026-03-14T06:00"))


class TestDoubleBooking:
    """Acceptance check 2 — flagged on BOTH segments, in BOTH views."""

    def test_an_overlap_is_flagged_on_both_segments(self, conn, event, staff):
        dana = staff["Dana Reyes"]
        a = repo.add_segment(conn, _seg(event, "Booth", "2026-03-14T09:00",
                                        "2026-03-14T17:00", owners=[dana]))
        b = repo.add_segment(conn, _seg(event, "Panel", "2026-03-14T14:00",
                                        "2026-03-14T15:00", track="Program",
                                        kind="program", owners=[dana]))
        flags = conflicts_for(repo.list_segments(conn, event))
        assert dana in flags.get(a, {})
        assert dana in flags.get(b, {})

    def test_the_flag_names_the_other_segment(self, conn, event, staff):
        dana = staff["Dana Reyes"]
        a = repo.add_segment(conn, _seg(event, "Booth", "2026-03-14T09:00",
                                        "2026-03-14T17:00", owners=[dana]))
        repo.add_segment(conn, _seg(event, "VIP tour", "2026-03-14T14:00",
                                    "2026-03-14T15:00", kind="vip", owners=[dana]))
        flags = conflicts_for(repo.list_segments(conn, event))
        assert "VIP tour" in flags[a][dana]

    def test_resolving_the_overlap_clears_both_flags(self, conn, event, staff):
        dana = staff["Dana Reyes"]
        a = repo.add_segment(conn, _seg(event, "Booth", "2026-03-14T09:00",
                                        "2026-03-14T12:00", owners=[dana]))
        b = repo.add_segment(conn, _seg(event, "Panel", "2026-03-14T14:00",
                                        "2026-03-14T15:00", owners=[dana]))
        flags = conflicts_for(repo.list_segments(conn, event))
        assert not flags.get(a) and not flags.get(b)

    def test_touching_segments_do_not_overlap(self, conn, event, staff):
        """09:00-12:00 then 12:00-13:00 is a handover, not a conflict."""
        dana = staff["Dana Reyes"]
        repo.add_segment(conn, _seg(event, "A", "2026-03-14T09:00",
                                    "2026-03-14T12:00", owners=[dana]))
        repo.add_segment(conn, _seg(event, "B", "2026-03-14T12:00",
                                    "2026-03-14T13:00", owners=[dana]))
        assert conflicts_for(repo.list_segments(conn, event)) == {}

    def test_different_people_never_conflict(self, conn, event, staff):
        repo.add_segment(conn, _seg(event, "Booth", "2026-03-14T09:00",
                                    "2026-03-14T17:00",
                                    owners=[staff["Dana Reyes"]]))
        repo.add_segment(conn, _seg(event, "Panel", "2026-03-14T14:00",
                                    "2026-03-14T15:00",
                                    owners=[staff["Sam Okoye"]]))
        assert conflicts_for(repo.list_segments(conn, event)) == {}

    def test_an_unowned_segment_never_conflicts(self, conn, event):
        repo.add_segment(conn, _seg(event, "Doors", "2026-03-14T09:00",
                                    "2026-03-14T17:00"))
        repo.add_segment(conn, _seg(event, "Panel", "2026-03-14T14:00",
                                    "2026-03-14T15:00"))
        assert conflicts_for(repo.list_segments(conn, event)) == {}

    def test_three_way_overlap_flags_every_pair(self, conn, event, staff):
        dana = staff["Dana Reyes"]
        ids = [repo.add_segment(conn, _seg(event, f"S{i}",
                                           "2026-03-14T09:00",
                                           "2026-03-14T17:00", owners=[dana]))
               for i in range(3)]
        flags = conflicts_for(repo.list_segments(conn, event))
        assert all(dana in flags.get(i, {}) for i in ids)


class TestMidnightSpanning:
    """Acceptance check 1."""

    def test_a_segment_spanning_midnight_appears_under_both_days(self, conn, event):
        repo.add_segment(conn, _seg(event, "Overnight build",
                                    "2026-03-14T22:00", "2026-03-15T02:00"))
        days = group_by_day(repo.list_segments(conn, event))
        assert "2026-03-14" in days and "2026-03-15" in days

    def test_it_is_one_continuous_block_on_the_board(self, conn, event):
        repo.add_segment(conn, _seg(event, "Overnight build",
                                    "2026-03-14T22:00", "2026-03-15T02:00"))
        lanes = board_lanes(repo.list_segments(conn, event),
                            parse_window("2026-03-14T06:00", "2026-03-16T20:00"))
        blocks = [b for lane in lanes for b in lane["blocks"]]
        assert len(blocks) == 1
        assert blocks[0]["width_px"] > 0

    def test_the_day_view_marks_it_as_continuing(self, conn, event):
        repo.add_segment(conn, _seg(event, "Overnight build",
                                    "2026-03-14T22:00", "2026-03-15T02:00"))
        days = group_by_day(repo.list_segments(conn, event))
        continued = [s for s in days["2026-03-15"] if s.continues_from_previous]
        assert continued


class TestBoardGeometry:
    """Acceptance check 4 — visual snap, stored time unchanged."""

    def test_a_seven_past_start_snaps_visually(self):
        assert snap_to_quarter("2026-03-14T09:07") == "2026-03-14T09:00"

    def test_snapping_rounds_to_the_nearest_quarter(self):
        # 23 past is nearer :30 than :15, and 07 past is nearer :00 than :15.
        assert snap_to_quarter("2026-03-14T09:23") == "2026-03-14T09:30"
        assert snap_to_quarter("2026-03-14T09:53") == "2026-03-14T10:00"

    def test_the_stored_time_is_not_rewritten(self, conn, event):
        sid = repo.add_segment(conn, _seg(event, "Odd", "2026-03-14T09:07",
                                          "2026-03-14T10:07"))
        assert repo.get_segment(conn, sid).start == "2026-03-14T09:07"

    def test_lanes_are_one_per_track(self, conn, event):
        repo.add_segment(conn, _seg(event, "Load-in", "2026-03-14T06:00",
                                    "2026-03-14T09:00", track="Logistics"))
        repo.add_segment(conn, _seg(event, "Panel", "2026-03-14T14:00",
                                    "2026-03-14T15:00", track="Program"))
        lanes = board_lanes(repo.list_segments(conn, event),
                            parse_window("2026-03-14T06:00", "2026-03-16T20:00"))
        assert {lane["track"] for lane in lanes} == {"Logistics", "Program"}

    def test_a_block_sits_inside_the_board(self, conn, event):
        repo.add_segment(conn, _seg(event, "Doors", "2026-03-14T09:00",
                                    "2026-03-14T17:00"))
        lane = board_lanes(repo.list_segments(conn, event),
                           parse_window("2026-03-14T06:00", "2026-03-16T20:00"))[0]
        # Fixed zoom: a block sits inside the BOARD's own width, which is a
        # function of the window length, not of the viewport.
        from app.features.run_of_show import board_width_px

        block = lane["blocks"][0]
        total = board_width_px(parse_window("2026-03-14T06:00",
                                            "2026-03-16T20:00"))
        assert block["left_px"] >= 0
        assert block["left_px"] + block["width_px"] <= total

    def test_an_unscheduled_event_yields_no_board(self, conn):
        eid = repo.create_event(conn, Event(name="Undated", city="Austin"))
        from app.features.schedule import EventWindow

        assert board_lanes(repo.list_segments(conn, eid), EventWindow()) == []


class TestSeedStandardDay:
    def test_seeding_creates_the_usual_stubs(self, conn, event):
        seed_standard_day(conn, event)
        titles = [s.title for s in repo.list_segments(conn, event)]
        assert any("Load-in" in t for t in titles)
        assert any("Doors" in t for t in titles)
        assert any("Teardown" in t for t in titles)

    def test_seeding_uses_the_event_window(self, conn, event):
        seed_standard_day(conn, event)
        first = repo.list_segments(conn, event)[0]
        assert first.start.startswith("2026-03-14")

    def test_seeding_an_unscheduled_event_is_refused(self, conn):
        eid = repo.create_event(conn, Event(name="Undated", city="Austin"))
        with pytest.raises(ValueError, match="schedul"):
            seed_standard_day(conn, eid)

    def test_seeding_twice_does_not_duplicate(self, conn, event):
        seed_standard_day(conn, event)
        before = len(repo.list_segments(conn, event))
        seed_standard_day(conn, event)
        assert len(repo.list_segments(conn, event)) == before

    def test_the_default_tracks_exist(self):
        assert "Logistics" in DEFAULT_TRACKS and "Program" in DEFAULT_TRACKS


class TestStaffErasure:
    """Acceptance check 6 — anonymise, do not delete the record of the shift."""

    def test_erasing_staff_keeps_their_segments(self, conn, event, staff):
        dana = staff["Dana Reyes"]
        repo.add_segment(conn, _seg(event, "Booth", "2026-03-14T09:00",
                                    "2026-03-14T17:00", owners=[dana]))
        repo.erase_person(conn, dana)
        assert len(repo.list_segments(conn, event)) == 1

    def test_the_name_is_gone_everywhere(self, conn, event, staff):
        dana = staff["Dana Reyes"]
        repo.add_segment(conn, _seg(event, "Booth", "2026-03-14T09:00",
                                    "2026-03-14T17:00", owners=[dana]))
        repo.erase_person(conn, dana)
        dump = "\n".join(
            "|".join(str(v) for v in row)
            for table in ("people", "segments")
            for row in conn.execute(f"SELECT * FROM {table}")
        )
        assert "Dana Reyes" not in dump

    def test_the_chip_reads_as_removed_not_blank(self, conn, event, staff):
        dana = staff["Dana Reyes"]
        repo.erase_person(conn, dana)
        person = repo.get_person(conn, dana)
        assert person.is_erased
        assert "removed" in person.display_name.lower()

    def test_erasure_cannot_be_undone(self, conn, event, staff):
        dana = staff["Dana Reyes"]
        repo.erase_person(conn, dana)
        with pytest.raises(ValueError, match="erased"):
            repo.erase_person(conn, dana)


class TestKindsAndColour:
    """Acceptance check 5 — no scan-state fills anywhere near this feature."""

    def test_the_declared_kinds_are_the_spec_set(self):
        assert set(SEGMENT_KINDS) == {"logistics", "floor", "program", "vip"}


class TestRunOfShowUi:
    """Populated-branch coverage: the P4-1 lesson was that empty-state tests
    prove only that a page renders when there is nothing to render."""

    @pytest.fixture()
    def client(self, tmp_path):
        from fastapi.testclient import TestClient
        from app.main import create_app
        return TestClient(create_app(db_path=str(tmp_path / "t.db")))

    @pytest.fixture()
    def eid(self, client):
        r = client.post("/events", data={
            "name": "Fleet Week", "city": "Austin",
            "starts_at": "2026-03-14T06:00", "ends_at": "2026-03-16T20:00",
        }, follow_redirects=False)
        return int(r.headers["location"].rstrip("/").split("/")[2])

    def _staff(self, client, eid, name, role="Lead"):
        client.post(f"/events/{eid}/run-of-show/staff/assign",
                    data={"name": name, "role": role}, follow_redirects=False)
        import sqlite3

        import app.main as main_mod

        conn = sqlite3.connect(main_mod.CURRENT_DB)
        conn.row_factory = sqlite3.Row
        try:
            # People are global; find by name in the pool.
            return conn.execute(
                "SELECT id FROM people WHERE name=? AND erased_at IS NULL",
                (name,)).fetchone()["id"]
        finally:
            conn.close()

    def _segment(self, client, eid, title, start, end, owners=(), track="Logistics",
                 kind="logistics"):
        # httpx wants a dict with a LIST value for repeated fields; a list of
        # (key, value) tuples is not supported and yields a broken body, which
        # arrives as an empty form and reads as a validation failure.
        data = {"title": title, "start": start, "end": end,
                "track": track, "kind": kind}
        if owners:
            data["owners"] = [str(o) for o in owners]
        return client.post(f"/events/{eid}/run-of-show/segments", data=data,
                           follow_redirects=False)

    def test_the_page_renders_and_is_in_the_stepper(self, client, eid):
        page = client.get(f"/events/{eid}/run-of-show")
        assert page.status_code == 200
        assert "run-of-show" in client.get(f"/events/{eid}/playbook").text

    def test_the_empty_state_offers_seeding(self, client, eid):
        assert "Seed a standard day" in client.get(f"/events/{eid}/run-of-show").text

    def test_seeding_produces_a_populated_document(self, client, eid):
        client.post(f"/events/{eid}/run-of-show/seed", follow_redirects=False)
        page = client.get(f"/events/{eid}/run-of-show").text
        assert "Load-in" in page and "Doors open" in page

    def test_a_segment_can_be_added_and_shows_its_owner(self, client, eid):
        dana = self._staff(client, eid, "Dana Reyes", "Booth lead")
        self._segment(client, eid, "Doors", "2026-03-14T09:00",
                      "2026-03-14T17:00", owners=[dana])
        page = client.get(f"/events/{eid}/run-of-show").text
        assert "Doors" in page and "Dana Reyes" in page

    def test_a_double_booking_is_flagged_in_the_list_view(self, client, eid):
        dana = self._staff(client, eid, "Dana Reyes")
        self._segment(client, eid, "Booth", "2026-03-14T09:00",
                      "2026-03-14T17:00", owners=[dana])
        self._segment(client, eid, "Panel", "2026-03-14T14:00",
                      "2026-03-14T15:00", owners=[dana], kind="program")
        page = client.get(f"/events/{eid}/run-of-show").text
        assert "owner-clash" in page
        assert "also on Panel" in page or "also on Booth" in page

    def test_the_same_flag_appears_on_the_board(self, client, eid):
        """Spec: a conflict on screen but missing from the other view is a bug."""
        dana = self._staff(client, eid, "Dana Reyes")
        self._segment(client, eid, "Booth", "2026-03-14T09:00",
                      "2026-03-14T17:00", owners=[dana])
        self._segment(client, eid, "Panel", "2026-03-14T14:00",
                      "2026-03-14T15:00", owners=[dana], kind="program")
        board = client.get(f"/events/{eid}/run-of-show?view=board").text
        assert "owner-clash" in board

    def test_the_board_renders_lanes_and_blocks(self, client, eid):
        self._segment(client, eid, "Load-in", "2026-03-14T06:00",
                      "2026-03-14T09:00")
        self._segment(client, eid, "Panel", "2026-03-14T14:00",
                      "2026-03-14T15:00", track="Program", kind="program")
        board = client.get(f"/events/{eid}/run-of-show?view=board").text
        assert "board-block" in board and "board-lane" in board
        assert "Load-in" in board and "Panel" in board

    def test_an_invalid_segment_keeps_what_was_typed(self, client, eid):
        page = self._segment(client, eid, "Backwards", "2026-03-14T17:00",
                             "2026-03-14T09:00").text
        assert "Not saved" in page
        assert "Backwards" in page

    def test_a_segment_can_be_removed(self, client, eid):
        self._segment(client, eid, "Doomed", "2026-03-14T09:00",
                      "2026-03-14T10:00")
        import sqlite3

        import app.main as main_mod

        conn = sqlite3.connect(main_mod.CURRENT_DB)
        conn.row_factory = sqlite3.Row
        try:
            sid = repo.list_segments(conn, eid)[0].id
        finally:
            conn.close()
        client.post(f"/events/{eid}/run-of-show/segments/{sid}/delete",
                    follow_redirects=False)
        assert "Doomed" not in client.get(f"/events/{eid}/run-of-show").text

    def test_erased_staff_read_as_removed_on_their_segments(self, client, eid):
        dana = self._staff(client, eid, "Dana Reyes")
        self._segment(client, eid, "Booth", "2026-03-14T09:00",
                      "2026-03-14T17:00", owners=[dana])
        client.post(f"/events/{eid}/run-of-show/staff/{dana}/erase",
                    follow_redirects=False)
        page = client.get(f"/events/{eid}/run-of-show").text
        assert "Booth" in page
        assert "Dana Reyes" not in page
        assert "[removed]" in page

    def test_an_unscheduled_event_says_why_the_board_is_empty(self, client):
        r = client.post("/events", data={"name": "Undated", "city": "Austin"},
                        follow_redirects=False)
        eid = int(r.headers["location"].rstrip("/").split("/")[2])
        page = client.get(f"/events/{eid}/run-of-show").text
        assert "no dates yet" in page.lower()

    def test_seeding_an_unscheduled_event_is_refused(self, client):
        r = client.post("/events", data={"name": "Undated", "city": "Austin"},
                        follow_redirects=False)
        eid = int(r.headers["location"].rstrip("/").split("/")[2])
        assert client.post(f"/events/{eid}/run-of-show/seed").status_code == 400

    def test_the_print_layout_hides_chrome(self, client, eid):
        self._segment(client, eid, "Doors", "2026-03-14T09:00",
                      "2026-03-14T17:00")
        page = client.get(f"/events/{eid}/run-of-show").text
        assert "no-print" in page


class TestBoardIsFixedZoom:
    """Design QA found the board auto-compressing the window to fit the screen.

    Percentage widths ARE auto-compression: a 38-hour event squeezed into ~750px
    gave 40-60px blocks with clipped titles. The spec rules that out — a
    readable 4-hour window beats an unreadable 3-day overview — so geometry is
    now fixed pixels per 15-minute column and the board scrolls horizontally.
    """

    def test_positions_are_pixels_not_percentages(self, conn, event):
        repo.add_segment(conn, _seg(event, "Doors", "2026-03-14T09:00",
                                    "2026-03-14T17:00"))
        block = board_lanes(repo.list_segments(conn, event),
                            parse_window("2026-03-14T06:00",
                                         "2026-03-16T20:00"))[0]["blocks"][0]
        assert "left_px" in block and "width_px" in block

    def test_the_scale_is_independent_of_the_window_length(self, conn, event):
        """The same 8-hour segment must be the same width on a 1-day and a
        3-day event. Under percentages it was three times narrower."""
        repo.add_segment(conn, _seg(event, "Doors", "2026-03-14T09:00",
                                    "2026-03-14T17:00"))
        segments = repo.list_segments(conn, event)
        short = board_lanes(segments, parse_window("2026-03-14T06:00",
                                                   "2026-03-14T20:00"))
        long = board_lanes(segments, parse_window("2026-03-14T06:00",
                                                  "2026-03-16T20:00"))
        assert (short[0]["blocks"][0]["width_px"]
                == long[0]["blocks"][0]["width_px"])

    def test_an_hour_is_the_declared_scale(self, conn, event):
        from app.features.run_of_show import PX_PER_HOUR

        repo.add_segment(conn, _seg(event, "One hour", "2026-03-14T09:00",
                                    "2026-03-14T10:00"))
        block = board_lanes(repo.list_segments(conn, event),
                            parse_window("2026-03-14T06:00",
                                         "2026-03-16T20:00"))[0]["blocks"][0]
        assert block["width_px"] == PX_PER_HOUR

    def test_a_short_segment_keeps_a_readable_minimum(self, conn, event):
        from app.features.run_of_show import MIN_BLOCK_PX

        repo.add_segment(conn, _seg(event, "Quick", "2026-03-14T09:00",
                                    "2026-03-14T09:15"))
        block = board_lanes(repo.list_segments(conn, event),
                            parse_window("2026-03-14T06:00",
                                         "2026-03-16T20:00"))[0]["blocks"][0]
        assert block["width_px"] >= MIN_BLOCK_PX

    def test_the_board_reports_its_total_width(self, conn, event):
        repo.add_segment(conn, _seg(event, "Doors", "2026-03-14T09:00",
                                    "2026-03-14T17:00"))
        from app.features.run_of_show import board_width_px

        window = parse_window("2026-03-14T06:00", "2026-03-16T20:00")
        assert board_width_px(window) > 3000  # 38h at 96px/h scrolls


class TestHourTicks:
    """No time axis meant a block's position was uninterpretable."""

    def test_ticks_cover_the_window(self, conn, event):
        from app.features.run_of_show import hour_ticks

        ticks = hour_ticks(parse_window("2026-03-14T06:00", "2026-03-14T10:00"))
        assert [t["label"] for t in ticks] == ["06:00", "07:00", "08:00",
                                               "09:00", "10:00"]

    def test_ticks_are_positioned_in_pixels(self, conn, event):
        from app.features.run_of_show import PX_PER_HOUR, hour_ticks

        ticks = hour_ticks(parse_window("2026-03-14T06:00", "2026-03-14T10:00"))
        assert ticks[0]["left_px"] == 0
        assert ticks[1]["left_px"] == PX_PER_HOUR

    def test_a_day_boundary_is_marked(self, conn, event):
        from app.features.run_of_show import hour_ticks

        ticks = hour_ticks(parse_window("2026-03-14T22:00", "2026-03-15T02:00"))
        boundaries = [t for t in ticks if t["is_day_start"]]
        assert len(boundaries) == 1
        assert boundaries[0]["date_label"] == "2026-03-15"

    def test_an_unscheduled_event_has_no_ticks(self):
        from app.features.schedule import EventWindow
        from app.features.run_of_show import hour_ticks

        assert hour_ticks(EventWindow()) == []


class TestBoardCarriesTheConflictFlag:
    """The board exists to expose overlaps; it must show them itself."""

    def test_a_conflicted_block_is_marked(self, conn, event, staff):
        dana = staff["Dana Reyes"]
        repo.add_segment(conn, _seg(event, "Booth", "2026-03-14T09:00",
                                    "2026-03-14T17:00", owners=[dana]))
        repo.add_segment(conn, _seg(event, "VIP tour", "2026-03-14T14:00",
                                    "2026-03-14T15:00", kind="vip", owners=[dana]))
        segments = repo.list_segments(conn, event)
        lanes = board_lanes(segments, parse_window("2026-03-14T06:00",
                                                   "2026-03-16T20:00"),
                            flags=conflicts_for(segments))
        blocks = [b for lane in lanes for b in lane["blocks"]]
        assert all(b["has_conflict"] for b in blocks)

    def test_a_clean_block_is_not_marked(self, conn, event, staff):
        repo.add_segment(conn, _seg(event, "Booth", "2026-03-14T09:00",
                                    "2026-03-14T12:00",
                                    owners=[staff["Dana Reyes"]]))
        segments = repo.list_segments(conn, event)
        lanes = board_lanes(segments, parse_window("2026-03-14T06:00",
                                                   "2026-03-16T20:00"),
                            flags=conflicts_for(segments))
        assert not lanes[0]["blocks"][0]["has_conflict"]

    def test_the_flag_survives_a_narrow_block(self, conn, event, staff):
        """A 15-minute overlap is still a conflict — the outline must not
        depend on the block being wide enough for a label."""
        dana = staff["Dana Reyes"]
        repo.add_segment(conn, _seg(event, "Booth", "2026-03-14T09:00",
                                    "2026-03-14T17:00", owners=[dana]))
        repo.add_segment(conn, _seg(event, "Quick sync", "2026-03-14T14:00",
                                    "2026-03-14T14:15", kind="program",
                                    owners=[dana]))
        segments = repo.list_segments(conn, event)
        lanes = board_lanes(segments, parse_window("2026-03-14T06:00",
                                                   "2026-03-16T20:00"),
                            flags=conflicts_for(segments))
        narrow = [b for lane in lanes for b in lane["blocks"]
                  if b["segment"].title == "Quick sync"][0]
        assert narrow["has_conflict"]
        assert narrow["show_conflict_label"] is False  # too narrow for text
