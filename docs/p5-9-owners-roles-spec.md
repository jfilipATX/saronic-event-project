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
  checkbox/tag: "Check-in staff" → sets `tags` containing `check_in`.
- Staff list shows a small `fit-badge` "check-in" when tagged.
- Gating: if ≥1 check_in staff exists, the check-in section header carries a
  `pending-note`-style callout: "Check-in is assigned to: {names}. Only
  check-in staff should operate this desk." If none tagged, a muted hint:
  "No check-in staff tagged yet — tag staff on the Run of Show page."

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
2. Staff tagged `check_in` shows the check-in badge.
3. With ≥1 check-in staff, the check-in section shows the assignment callout.
4. With 0 check-in staff, the muted hint shows (no false "assigned" claim).
5. Gating never blocks the coordinator from seeing the roster (display-only).
6. Erased staff drop from both the roster and any check-in assignment display.

## Design notes
- The assignment callout uses the existing `pending-note` surface but with
  neutral (not warning) treatment, because assignment is informational, not an
  error. Copy must say "should" not "can only" — we are signalling, not
  enforcing.
- Do NOT reuse the amber `fit-badge` warning color for the check-in tag; use the
  steel/neutral token so it reads as a role, not an alert.
