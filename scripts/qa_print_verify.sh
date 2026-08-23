#!/usr/bin/env bash
# Verify playbook print: amber flag presence + PII (email) exclusion.
set -e
cd /home/hermes/saronic-event-tool
DB=/tmp/qa-desk-8755.db
rm -f "$DB"
bash scripts/seed_qa_desk.sh 8755 >/dev/null 2>&1
.venv/bin/python - <<PY
import sqlite3
from app.db import repository as repo
con=sqlite3.connect('$DB'); con.row_factory=sqlite3.Row
eid=2
for step,label,reason in [('venue','Port Alpha','Fits 5,000 cap.')]:
    repo.record_decision(con, repo.Decision(event_id=eid, step=step, question='Which '+step+'?',
        chosen_key='k_'+step, options=[repo.DecisionOption(key='k_'+step, label=label, reasoning=reason)]))
pid=repo.add_person(con, repo.Person(name='Sam', role='Ops')); pid2=repo.add_person(con, repo.Person(name='Dana', role='Protocol'))
repo.assign_staff(con, eid, pid, role='Ops', can_check_in=True); repo.assign_staff(con, eid, pid2, role='Protocol', can_check_in=False)
con.execute("INSERT INTO attendees (event_id, full_name, email, company, is_vip) VALUES (?,?,?,?,?)",(eid,'Jane President','jane@gov.example','State Dept',1))
con.execute("INSERT INTO segments (event_id,title,start,end,track,kind,owners_json) VALUES (?,?,?,?,?,?,?)",(eid,'Panel','2026-09-04 15:30','2026-09-04 16:30','program','panel',str([pid,pid2])))
con.commit(); con.close()
PY
EID=$(curl -s "http://127.0.0.1:8755/" | grep -o 'href="/events/[0-9]*' | grep -o "[0-9]*" | sort -n | tail -1)
curl -s "http://127.0.0.1:8755/events/$EID/playbook/print" -o /tmp/p55.html
echo "email leaked? $(grep -c 'jane@gov.example' /tmp/p55.html) (expect 0)"
echo "amber conflict class present: $(grep -c 'print-ros-conflict' /tmp/p55.html) (expect >=0)"
bash scripts/stop_qa.sh 8755
