# P5-5 / P5-9 — People Pool & Per-Event Assignment UI

## Context
P5-5 made staff a **global pool** (`Person`): add once, assign per event. P5-9
(locked) says `role` persists on the Person (pre-fills on re-add) but the
**check-in capability is per event** via `event_staff.can_check_in`. This spec
captures the UI surfaces for both so they render against contract. The
templates already exist and match; this documents the bar for the visual QA.

## Surfaces
### 1. People pool (`/people`, global)
- Header copy states the model in one line: "Add a person once; assign per
  event. Role carries over; can-check-in is set per event." (already present —
  keep it; it's the single most important piece of user education.)
- **Add to pool** form: `Name` (required) + `Role (carries to new events)`
  (optional hint in placeholder). On submit → `POST /people` → `add_person`.
- **Roster** (`staff-roster` / `roster-row card-surface`): each row shows
  `display_name` + `· role` (muted) and an **Erase** action.
  - Erase is the irreversible anonymizing path (`erase_person`) — keep it
    `btn-quiet` (it's destructive but rare), and the Erased section below
    explains *why* the record remains ("safety record that a shift was
    covered").
- **Erased** section: anonymised rows, no names, muted — proves erasure worked
  without deleting the safety record.

### 2. Run-of-show per-event assignment (`/events/{id}/run-of-show`)
Two groups, clearly separated:
- **Assigned (this event)** — roster rows showing `display_name · role`, a
  neutral **`tag-checkin`** chip when `can_check_in` is true *for this event*,
  and Remove / Erase actions.
- **Assign** form — pick from the pool (a select or checkbox list), optional
  per-event role override, and a **`can_check_in` checkbox** ("Grant check-in
  for this event"). Submit → `POST /events/{id}/run-of-show/staff/assign`.

## Design rules (already implemented — verify, don't re-litigate)
- **`tag-checkin` is neutral/steel, never amber.** It is a role capability, not
  an alert. Confirm the CSS uses the tertiary/neutral token, not the warning
  amber.
- **Per-event correctness is visible:** a person check-in on Event A must NOT
  show the `tag-checkin` chip on Event B. QA must open two events for the same
  person to confirm isolation renders.
- **Role pre-fill:** when assigning a pool person who has a persistent `role`,
  the form/row shows it as the default; the per-event override, if given, wins
  on the row but does not change the pool `role`.
- **Empty states:** pool empty → "No people yet — add the first one below."
  No assigned staff on an event → the Assign form still works; the assigned
  list shows its empty note.

## Acceptance checks (visual QA)
1. `/people` lists pool with `· role`, Erase action, and the Erased section
   renders anonymised.
2. Add-to-pool with a role; re-assign to a second event and confirm the role
   pre-fills (no re-typing).
3. On Event A, grant `can_check_in` → `tag-checkin` shows on A's assigned row.
4. On Event B (same person), `tag-checkin` does NOT show → per-event isolation
   is visible in the UI, not just the DB.
5. `tag-checkin` chip is neutral (steel/tertiary), not amber — token check.
6. Erase a person → name gone from pool, appears anonymised in Erased section;
   safety record intact (segment owner history unaffected).

## Hand-off
This spec is documentation of an already-shipped surface (P5-5 at `fd6d736`).
It exists so the designer's visual QA has a checklist and the contract is
explicit for any future edit. No code change implied unless QA finds a gap.
