"""P6-6 — Fleet Week demo seed (hybrid: scraped context + synthesized specifics).

Real Fleet Week structure (from public Fleet Week SF/LA/Houston programs): a
multi-day free public expo with ship tours via virtual queue, an expo hall with
booths, a VIP lounge onboard a battleship, a parade of ships, career fair, K9
demos, free concert series, food trucks, and a STEM pavilion. We use that
authentic skeleton and fill in synthesized, clearly-demo specifics (names of
booths/presenters are plausible, not real people's PII).

The seeded event is flagged ``is_demo=True`` so it shows a "Demo" badge and is
unmistakable from real data. It also pulls in the bundled example images so the
visuals composer has something to render.
"""
from __future__ import annotations

from app.db import repository as repo
from app.db.models import Attendee, Decision, DecisionOption, Event, Person, Segment
from app.features.example_images import seed_example_images


def seed_fleet_week_demo(conn) -> int:
    """Create and fully populate a labeled Fleet Week demo event. Returns its id."""
    event = Event(
        name="Fleet Week — Demo", city="San Francisco", state="CA",
        country="US", audience_estimate=60000, event_type="Public expo",
        owner_name="Lt. Cmdr. Reyes", owner_role="Event Lead", is_demo=True,
    )
    eid = repo.create_event(conn, event)
    # Window: a 3-day expo (matches the multi-day P5-2 model).
    repo.set_event_window(conn, eid, _window("2026-05-08", "2026-05-10"))
    # Multi-day hours per the scrape (festival 10–18, ship tours by queue).
    repo.add_event_day(conn, eid, "2026-05-08", "10:00", "18:00")
    repo.add_event_day(conn, eid, "2026-05-09", "10:00", "18:00")
    repo.add_event_day(conn, eid, "2026-05-10", "10:00", "18:00")

    # --- Decisions (settle the chain so the playbook/deck have content) ------
    repo.record_decision(conn, Decision(
        event_id=eid, step="event_type", question="What kind of event?",
        chosen_key="public_expo", chosen_value="Public expo",
        options=[DecisionOption(key="public_expo", label="Public expo",
                                reasoning="Free, public-facing; max reach.")]))
    repo.record_decision(conn, Decision(
        event_id=eid, step="audience", question="Who attends?",
        chosen_key="mixed_civilian", chosen_value="~60,000 mixed civilian/military",
        options=[DecisionOption(key="mixed_civilian",
                                label="~60,000 mixed civilian/military",
                                reasoning="Public expo draws families + service members.")]))
    repo.record_decision(conn, Decision(
        event_id=eid, step="venue", question="Where is it held?",
        chosen_key="waterfront", chosen_value="SF Waterfront (PIER 39 + USS Iowa)",
        options=[DecisionOption(key="waterfront",
                                label="SF Waterfront (PIER 39 + USS Iowa)",
                                reasoning="Parade of ships berths at the pier; "
                                          "VIP lounge onboard the battleship.")]))

    # --- People / staff (global pool; assigned per event) -------------------
    ops = repo.add_person(conn, Person(name="Sam Okafor", role="Operations Lead"))
    vip_coord = repo.add_person(conn, Person(name="Dana Reyes", role="VIP Coordinator"))
    stage = repo.add_person(conn, Person(name="Lt. Miguel Cruz", role="Stage Manager"))
    kiosk = repo.add_person(conn, Person(name="Priya Nair", role="Kiosk Lead"))
    repo.assign_staff(conn, eid, ops, role="Operations Lead", can_check_in=True)
    repo.assign_staff(conn, eid, vip_coord, role="VIP Coordinator", can_check_in=True)
    repo.assign_staff(conn, eid, stage, role="Stage Manager", can_check_in=False)
    repo.assign_staff(conn, eid, kiosk, role="Kiosk Lead", can_check_in=True)

    # --- Run of show across 3 days, color-coded by kind --------------------
    # Day 1
    repo.add_segment(conn, Segment(event_id=eid, title="Gates + expo hall open",
        start="2026-05-08 10:00", end="2026-05-08 18:00", kind="floor",
        track="Expo", owner_ids=[ops]))
    repo.add_segment(conn, Segment(event_id=eid, title="Parade of Ships",
        start="2026-05-08 11:00", end="2026-05-08 12:30", kind="program",
        track="Waterfront", owner_ids=[ops]))
    repo.add_segment(conn, Segment(event_id=eid, title="Opening keynote",
        start="2026-05-08 13:00", end="2026-05-08 14:00", kind="presentation",
        track="Main Stage", owner_ids=[stage]))
    repo.add_segment(conn, Segment(event_id=eid,
        title="VIP luncheon — Battleship Iowa lounge",
        start="2026-05-08 12:00", end="2026-05-08 13:30", kind="dinner",
        track="VIP", owner_ids=[vip_coord]))
    repo.add_segment(conn, Segment(event_id=eid, title="Ship tours (virtual queue)",
        start="2026-05-08 10:30", end="2026-05-08 17:30", kind="visitor",
        track="Pier", owner_ids=[ops]))
    # Day 2
    repo.add_segment(conn, Segment(event_id=eid, title="STEM pavilion opens",
        start="2026-05-09 10:00", end="2026-05-09 18:00", kind="booth",
        track="Expo", owner_ids=[ops]))
    repo.add_segment(conn, Segment(event_id=eid, title="Industry panel: maritime AI",
        start="2026-05-09 14:00", end="2026-05-09 15:30", kind="panel",
        track="Main Stage", owner_ids=[stage]))
    repo.add_segment(conn, Segment(event_id=eid, title="Kiosk check-in live",
        start="2026-05-09 10:00", end="2026-05-09 18:00", kind="booth",
        track="Entrance", owner_ids=[kiosk]))
    repo.add_segment(conn, Segment(event_id=eid, title="Free concert series",
        start="2026-05-09 16:00", end="2026-05-09 18:00", kind="program",
        track="Pier Stage", owner_ids=[stage]))
    # Day 3
    repo.add_segment(conn, Segment(event_id=eid, title="Career fair",
        start="2026-05-10 10:00", end="2026-05-10 15:00", kind="booth",
        track="Expo", owner_ids=[ops]))
    repo.add_segment(conn, Segment(event_id=eid, title="Awards dinner",
        start="2026-05-10 19:00", end="2026-05-10 21:00", kind="dinner",
        track="VIP", owner_ids=[vip_coord]))
    repo.add_segment(conn, Segment(event_id=eid, title="Closing ceremony",
        start="2026-05-10 17:00", end="2026-05-10 18:00", kind="program",
        track="Main Stage", owner_ids=[stage]))

    # --- A few attendees (clearly demo; one VIP to exercise the desk) -------
    repo.add_attendee(conn, Attendee(event_id=eid, full_name="Adm. Jane Sterling",
        email="demo+vip@example.com", company="US Navy (demo)", is_vip=True))
    repo.add_attendee(conn, Attendee(event_id=eid, full_name="Marcus Lee",
        email="demo+guest@example.com", company="Local Resident (demo)"))
    repo.add_attendee(conn, Attendee(event_id=eid, full_name="Sofia Ramos",
        email="demo+guest2@example.com", company="STEM Teacher (demo)"))

    # Example imagery so the visuals composer has inputs.
    from app.features.example_images import example_image_dir
    seed_example_images(conn, eid, f"generated/visuals/{eid}")
    conn.commit()
    return eid


def _window(start: str, end: str):
    from app.features.schedule import parse_window
    return parse_window(start, end)
