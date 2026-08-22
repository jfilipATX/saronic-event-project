# P5-9 — Lightweight Owners & Roles (no auth)

## Problem (user's words)
"event owners and event staff roles so different Saronic employees can use this
tool… the event manager won't be sitting at the check-in table and needs to
ensure the right person has access to that section to check people in, but keep
the attendee list and PII secure."

## Scope decision (ratified)
**Lightweight cut only.** No login, no authentication, no passwords. The person
opening the app sees the whole event. What we add:
1. **Event owners** — an attribute on the event (a named person/role, the
   accountable lead). Surfaces on the playbook and as the default "who can see
   everything" for the gating logic.
2. **Staff role tags** — each staff member gets a `role` (already exists as a
   free-text field). We introduce a **controlled tag** `check_in` (and keep
   `role` as the human label). A staff member tagged `check_in` is permitted to
   operate the check-in section.
3. **Section gating (display only)** — the check-in *section* respects the tag:
   if no check-in-tagged staff exist yet, the full roster shows (current
   behaviour, no breakage). Once at least one check_in staff exists, the roster
   view is scoped to "check-in staff see what they need" — i.e. the desk still
   works for the coordinator who opened it, but the *intent* (only
   check-in-role people operate this) is encoded and visible.

## Data model — LOCKED DECISION (2026-08-22)
Per @user's call: **staff `role` persists between events; the ability to check
people in is granted per event.**

- `Person` (global pool, P5-5): `name`, `role` (a *default/persistent* title,
  e.g. "Booth Lead"), PII-scoped + erasure parity. **No `check_in` flag on
  Person** — removed.
- `event_staff` (join, per event): `(event_id, person_id, role_override,
  can_check_in)`. `role` here is a *per-event title override*; `can_check_in`
  is a **dedicated boolean capability**, orthogonal to role — one person can be
  both "Booth Lead" and a check-in operator, so the two axes must not be
  overloaded into one column.
- **Check-in gating reads `event_staff.can_check_in` for the current event
  only.** Being check-in staff on Event A grants nothing on Event B. This is
  the whole point of the per-event design and what the user's phrasing
  ("the manager puts the right person on the check-in section for *this*
  event") requires.
- The persistent `role` on `Person` is what carries across events (so re-adding
  someone to a new event pre-fills their title); `can_check_in` does NOT
  persist — it is re-granted per event by the manager.

## Explicitly NOT in scope
- No authentication. A visitor to the app is trusted as "the coordinator."
- No row-level PII hiding from the coordinator. The owner/role tags gate the
  *section's framing*, not data visibility, because there is no login to derive
  a viewer identity from. This is the honest first cut: we cannot scope PII to a
  logged-in user until there is a user.
- Real auth is a future phase.

## UI contract
### Event owner
- On event creation / edit: a `form-field` "Event owner" (name + optional role,
  e.g. "Lt. Cmdr. Reyes — Event Lead"). Stored as `event.owner_name` /
  `event.owner_role`.
- Shown on the playbook as "Event owner: …" and on the home/event header.

### Staff role tag
- On the run-of-show Staff form (and the new independent-staff form, P5-5), a
  checkbox/tag: "Check-in staff" → sets `event_staff.can_check_in = TRUE` for
  **this event** (the join row), and `role` (if given) as the title override.
  The persistent `role` on `Person` is shown as the default but the per-event
  capability is what the check-in view gates on.
- Staff list shows a small `fit-badge` "check-in" when `can_check_in` is true
  **for this event**.
- Gating: if ≥1 `event_staff` row with `can_check_in` exists for this event,
  the check-in section header carries a `pending-note`-style callout:
  "Check-in is assigned to: {names}. Only check-in staff should operate this
  desk." If none tagged, a muted hint: "No check-in staff tagged yet — tag
  staff on the Run of Show page."

## Behaviour contract
- `event.owner_*` is optional; blank is valid (legacy events).
- Staff `tags` is a JSON list on the staff row (add column via `_ADDED_COLUMNS`,
  default `[]`).
- Gating is **display-only** and non-blocking: it never hides data from the
  coordinator who opened the app, it only *signals* assignment. Document this in
  the UI copy so it is not mistaken for real access control.
- Eradication parity: erasing a staff member clears name/role/tags (already
  handled by `erase_staff`).

## Acceptance checks (TDD)
1. Event owner stored and shown on playbook.
2. Staff tagged `can_check_in` for this event shows the check-in badge.
3. With ≥1 `event_staff.can_check_in` row for this event, the check-in section
   shows the assignment callout.
4. With 0 check-in staff (no `can_check_in` row for this event), the muted hint
   shows (no false "assigned" claim).
5. **Per-event isolation:** a person `can_check_in` on Event A is NOT check-in
   for Event B (gating reads the join row for the current event only).
6. Gating never blocks the coordinator from seeing the roster (display-only).
7. Erased staff drop from both the roster and any check-in assignment display.
8. Persistent `role` on `Person` carries to a new event when re-added;
   `can_check_in` does NOT (re-granted per event).

## Design notes
- The assignment callout uses the existing `pending-note` surface but with
  neutral (not warning) treatment, because assignment is informational, not an
  error. Copy must say "should" not "can only" — we are signalling, not
  enforcing.
- Do NOT reuse the amber `fit-badge` warning color for the check-in tag; use the
  steel/neutral token so it reads as a role, not an alert.
