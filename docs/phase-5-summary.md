# Phase 5 — Summary

Milestone tag: `Phase-5` (on `main`, `8856ae1`). All eight approved Phase 5
requests are shipped, verified, and design-cleared. **855 tests green**, nav +
readme-claims guards green, fresh-clone smoke passing, secret audit clean.

## What shipped

| ID | Feature | What it does |
|----|---------|--------------|
| P5-2 | Flexible / multi-day dates | Events can span multiple days, each with its own open/close hours (or no hours). Legacy single-window events still render. Rejected edits leave the prior schedule intact; a day closing before its open is refused. |
| P5-3 | State + country location precision | One canonical `Event.location` → `City, ST, Country` (ISO alpha-2, US default). Same-named cities are disambiguated; legacy events fall back gracefully. |
| P5-4 | Manual LLM venue search | Facilitator-triggered only. Cost estimate (tokens + USD) shown **before** the call; per-event cap ($2.00 default) blocks pre-call; every call logged to `spend_log` via the P3-1 ledger. Proposals are staged, never auto-applied. |
| P5-5 | Staff as first-class | People are a global pool; per-event assignment with a role override. Per-event `can_check_in` flag. |
| P5-9 (light) | Event owners + check-in gating signal | `owner_name`/`owner_role` on the event; the check-in desk shows who is assigned to operate it (display-only signal, no access control). |
| P5-8 | Manual check-in | Facilitator looks up an **existing** invitee by name/email and marks them arrived — no code, no walk-in form. Logged as `method=manual`, `actor=facilitator`; double-use and withdrawn cases handled; VIP banner reused. |
| P5-6 | Executive PowerPoint export | `GET /events/{id}/slides/export.pptx` → monochrome python-pptx, ≤10 slides (title, overview, decisions, venue, run of show, attendance/access, spend). Built from the same playbook/ROS/ledger as the screen. No Claude call. |
| P5-7 | Printable day-of playbook | `GET /events/{id}/playbook/print` → chrome-free, PII-scoped print document (VIP name/company only; attendee emails excluded). Amber double-booking flag carries `print-color-adjust: exact` so it survives onto paper. |

## What P5-9 (light) deliberately does NOT do

Per the approved scope ("just a lightweight version, we don't need full auth
currently"):

- **No login / authentication.** The "check-in assigned to" note is a *signal*,
  not an access control. Anyone who can open the desk can operate it. This is
  intentional and stated on the page.
- **No authorization layer.** `checkin_actor` is hardcoded to `"facilitator"`
  (a placeholder for a future named actor once auth exists). The audit record
  captures *that* a manual arrival happened and *that* it was manual, but not
  *which person* did it yet.
- **Owner is a free-text label**, not a linked account. Erased staff are
  dropped from the callout but the roster is never hidden.

When real auth lands, the upgrade path is: bind a session to a person, populate
`checkin_actor` from the session, and gate the desk behind the per-event
`can_check_in` flag — the schema and logic already support it.

## Verification

- `855 tests` green (`pytest tests/ -q`), all offline by default.
- Nav guard (`test_nav_links.py`) 3/3; readme-claims guard (catches count
  drift) green.
- Fresh-clone smoke against the pushed head: every page returns 200, the PPTX
  export serves a valid file, and the print view renders chrome-free + PII-scoped.
- `scripts/audit_secrets.py` clean on 202 tracked files + full history.

## Branch discipline note (for the record)

Phase 5 was developed on `phase-5` but several features (P5-8, P5-6) were
committed directly to `main` mid-build before being fast-forwarded onto the
branch. This was accepted at the time (green, PM-cleared) but is the recurring
drift we want to close:

- **Policy:** branch + sweep for anything that changes app behavior or makes a
  factual claim; direct-to-`main` only for pure assets. Keep the feature branch
  the source of truth until the sweep passes, then merge.
- **Mechanical guard that already helps:** the readme-claims guard caught the
  test-count drift twice. Keep it in the pre-merge checklist.
- **Action:** before the next phase, prefer committing on the feature branch,
  running both gates there, and merging once green — rather than landing on
  `main` and reconciling after.
