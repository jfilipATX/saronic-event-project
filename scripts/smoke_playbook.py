"""End-to-end smoke: play the coordinator's workflow against mock providers and
export a real playbook. Run: .venv/bin/python scripts/smoke_playbook.py
"""
from __future__ import annotations

import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db import repository as repo, schema_sql_text as sql
from app.db.models import Decision, DecisionOption, Event, EventVariable
from app.features.playbook import compose_playbook, render_markdown
from app.providers.mock.providers import (
    MockAudienceProvider,
    MockVenueProvider,
)

conn = sqlite3.connect(":memory:")
conn.row_factory = sqlite3.Row
conn.executescript(sql.SCHEMA)

CITY = "Austin"
eid = repo.create_event(conn, Event(name="Saronic Fleet Week", city=CITY))

# ── Step 1: event type. Tool OFFERS, human PICKS. ──
repo.record_decision(conn, Decision(
    event_id=eid, step="event_type",
    question="What kind of event is this?",
    options=[
        DecisionOption("convention", "Convention",
                       "Widest reach; matches a product-launch goal."),
        DecisionOption("panel", "Panel",
                       "Cheapest, but too small for a launch."),
    ],
    chosen_key="convention", decided_by="coordinator"))

# ── Step 2: audience, computed by a provider, still confirmed by the human. ──
audience = MockAudienceProvider().base_audience(CITY, "convention")
repo.record_decision(conn, Decision(
    event_id=eid, step="audience",
    question="What audience size do we plan for?",
    options=[
        DecisionOption(str(audience), f"{audience:,} attendees",
                       f"Provider estimate for a convention in {CITY}."),
        DecisionOption(str(audience // 2), f"{audience // 2:,} attendees",
                       "Conservative first-year discount."),
    ],
    chosen_key=str(audience), decided_by="coordinator"))

# ── Step 3: venue. Offer the FULL slate, flagged for fit — the tool must not
# silently filter away an option the human might accept for budget reasons.
all_venues = MockVenueProvider().search(CITY, 0)
opts = [
    DecisionOption(
        key=v.name.lower().replace(" ", "-"),
        label=v.name,
        reasoning=(
            f"Capacity {v.capacity:,} vs estimate {audience:,} "
            f"({'fits' if v.capacity >= audience else 'UNDER capacity'}); "
            f"rated {v.rating}. {v.notes}"
        ),
        data={"capacity": v.capacity, "rating": v.rating,
              "fits": v.capacity >= audience},
    ) for v in all_venues
]
assert len(opts) > 1, "smoke needs a multi-option slate to exercise revision"
vid = repo.record_decision(conn, Decision(
    event_id=eid, step="venue",
    question="Which venue should host the event?",
    options=opts, chosen_key=opts[0].key, decided_by="coordinator"))

# ── The human changes their mind: revision must not destroy history. ──
repo.revise_decision(conn, vid, chosen_key=opts[1].key,
                     note="Budget cut; downsizing venue.")

repo.add_variable(conn, EventVariable(
    event_id=eid, kind="vip", value="DoD delegation",
    notes="Needs escort + badging lead time."))

# ── A step left deliberately undecided → must surface as an open question. ──
repo.record_decision(conn, Decision(
    event_id=eid, step="checkin",
    question="QR check-in or manual list at the door?",
    options=[
        DecisionOption("qr", "QR check-in", "Faster at the door; attendees need phones."),
        DecisionOption("manual", "Manual list", "No tech dependency; slower for 4k+."),
    ]))

pb = compose_playbook(conn, eid)
md = render_markdown(pb)

out_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "generated")
os.makedirs(out_dir, exist_ok=True)
path = os.path.join(out_dir, "playbook-smoke.md")
with open(path, "w") as f:
    f.write(md)

hist = repo.decision_history(conn, eid)
print(f"sections={[s.step for s in pb.sections]}")
print(f"open_questions={[q.step for q in pb.open_questions]} is_complete={pb.is_complete}")
print(f"history_rows={len(hist)} (append-only; live={len(repo.current_decisions(conn, eid))})")
print(f"wrote {path} ({len(md)} chars)")
print("-" * 60)
print(md)
