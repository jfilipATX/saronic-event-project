"""End-to-end: drive the full coordinator workflow (T6–T8, T7.5, T11.5).

Simulates a human coordinator answering each staged question, changing their
mind mid-plan, and exporting the playbook.

Run: .venv/bin/python scripts/smoke_workflow.py
"""
from __future__ import annotations

import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db import repository as repo, schema_sql_text as sql
from app.db.models import EventVariable
from app.features.playbook import compose_playbook, render_markdown
from app.features.workflow import CoordinatorWorkflow


def show_question(wf: CoordinatorWorkflow, event_id: int) -> None:
    for d in wf.pending(event_id):
        print(f"\n?  {d.question}")
        for o in d.options:
            badge = o.data.get("fit") or o.data.get("sanity") or ""
            badge = f"  [{badge}]" if badge else ""
            print(f"   - {o.label}{badge}")
            print(f"     {o.reasoning}")


conn = sqlite3.connect(":memory:")
conn.row_factory = sqlite3.Row
conn.executescript(sql.SCHEMA)
wf = CoordinatorWorkflow(conn)

print("=" * 70)
print("COORDINATOR SESSION — Saronic Fleet Week, Austin")
print("=" * 70)

eid = wf.start_event(name="Saronic Fleet Week", city="Austin")
show_question(wf, eid)

print("\n>> coordinator picks: convention")
wf.choose(eid, step="event_type", key="convention")
show_question(wf, eid)

print("\n>> coordinator picks: baseline")
wf.choose(eid, step="audience", key="baseline")
show_question(wf, eid)

print("\n>> coordinator picks: austin-convention-center")
wf.choose(eid, step="venue", key="austin-convention-center")
assert wf.pending(eid) == [], "chain should be complete"
print("   chain complete.")

print("\n>> BUDGET CUT — coordinator revises audience to conservative")
wf.revise(eid, step="audience", key="conservative", note="Budget cut in half.")
print("   downstream re-staged:", [d.step for d in wf.pending(eid)])
show_question(wf, eid)

print("\n>> coordinator picks: palmer-events-center")
wf.choose(eid, step="venue", key="palmer-events-center")

repo.add_variable(conn, EventVariable(
    event_id=eid, kind="vip", value="DoD delegation",
    notes="Needs escort + badging lead time."))

pb = compose_playbook(conn, eid)
md = render_markdown(pb)

out_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "generated")
os.makedirs(out_dir, exist_ok=True)
path = os.path.join(out_dir, "playbook-workflow.md")
with open(path, "w") as f:
    f.write(md)

hist = repo.decision_history(conn, eid)
live = repo.current_decisions(conn, eid)
print("\n" + "=" * 70)
print(f"complete={pb.is_complete}  sections={[s.step for s in pb.sections]}")
print(f"audit trail: {len(hist)} total decisions, {len(live)} live")
print(f"wrote {path}")
print("=" * 70)
print(md)
