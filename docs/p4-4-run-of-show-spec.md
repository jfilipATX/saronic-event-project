# P4-4 — Run of Show: Design Spec

Two views over one dataset: a chronological **run-of-show** (the printable
day-of document handed to staff) and a **concurrency board** (lanes on a time
axis, so overlapping tracks are visible). Both derive from the same segments;
neither is ever edited independently of the other.

Depends on P4-3 (event start/end date-times give the time axis its bounds)
and a lightweight **staff** entity (name + role; contact optional, PII-scoped
and erasable exactly like attendees).

## Data shape (design contract, not schema)

Segment: `title`, `start`, `end`, `track`, `owners[]` (staff refs),
`location` (free text — "Loading dock B", "Booth", "Panel room"),
`notes` (optional), `kind` (one of: `logistics`, `floor`, `program`, `vip`).

- `track` is a named lane, coordinator-created ("Expo floor", "Panels",
  "VIP", "Logistics"). Default event gets `Logistics` + `Program` seeded.
- `kind` drives color accents; `track` drives lane placement. They usually
  correlate but must stay independent — a VIP moment can sit in the Program
  lane.

## Color usage (strict — the scan-state rule extends here)

Segments are *not* colored with success/warning/danger — those remain
reserved for scan states. Lane/kind accents use:

- `logistics` — steel `#9DA7AF`
- `floor` — signal blue `#4C9FD8`
- `program` — neutral `#F2F6FA`
- `vip` — Archivo-weight ink chip on neutral fill (visual inversion, no new
  color)

Accents render as a **4px left edge + kind label**, never full saturated
fills — the board must survive projection and grayscale printing.

## View 1 — Run of show (chronological)

- Single column, ordered by `start`. Each segment is a `card-surface` row:
  time range (Archivo Expanded, tabular), title, location, owner chips,
  notes in `muted-text`.
- Day breaks get an `h2` date header (multi-day events are the norm: load-in
  often precedes doors by a day).
- **Owner chips** are steel pills with the staff member's name; hovering
  (or the print layout) appends their role.
- **Print layout** (`@media print`): light surface (playbook tokens — ink on
  neutral), one segment per row, no chrome, no stepper; times bold, owners
  right-aligned. This document gets taped to a wall backstage — it must read
  at arm's length in bad light: `body-lg` minimum, generous row spacing.

## View 2 — Concurrency board

- **Lanes are horizontal rows** (one per track), **time flows left→right**
  between the event's P4-3 bounds, snapped to 15-minute columns.
- Segment = rounded block (`rounded.md`) spanning its time range, kind-accent
  left edge, title truncated with full detail on click/tap.
- A **now line** (signal blue, 2px vertical) when the event is live —
  the board is a day-of operations surface, not just a plan.
- **Zoom is fixed, scroll is horizontal** — do not auto-compress a 3-day
  event to fit one screen; a readable 4-hour window beats an unreadable
  3-day overview. Sticky lane labels on the left.

## Double-booking detection (the board's reason to exist)

- A staff member owning two segments whose time ranges overlap gets flagged
  on **both** segments: the owner chip switches to a warning-*text* treatment
  (amber text + "also on {other segment}" in `muted-text`) — never a filled
  amber banner (reserved), and never blocking. The coordinator decides;
  double-booking a floater is often intentional.
- The run-of-show view carries the same flag inline, so the print copy
  shows it too — a conflict visible on screen but missing from the wall
  copy is a failure.
- Flag logic is presentation-layer (computed at render), consistent with
  the favourites-overlay precedent: segment records stay clean.

## Editing model

- Segments are **not** chain decisions — they're operational data, edited
  freely (add/edit/delete) like the roster, not staged/chosen/revised.
  The playbook embeds the *current* run of show as a section; history
  granularity at the decision level would bury real decisions in noise.
- Segment form: `form-row` fields — title, track (select), kind (select),
  start/end (time inputs bounded by event dates), location, owners
  (multi-select of staff), notes. Sticky on validation error, missing
  fields listed at once (walk-in form precedent).
- Empty state: `pending-note` — "No run of show yet. Add the first segment
  — load-in is usually where it starts." Plus a one-click "seed standard
  day" that creates load-in / setup / doors / teardown stubs from the
  event's date bounds (minimal-lift principle; coordinator renames rather
  than invents).

## Navigation

- One stepper entry: **Run of show** between Venue and Slides (it's planned
  before collateral is generated). Chronological view is the default tab;
  board is a second tab on the same page (`btn-quiet` tab pair) — one nav
  slot, not two, because they're one dataset.

## Acceptance checks

1. Segment spanning midnight renders under both day headers in view 1 and
   as one continuous block in view 2.
2. Double-booked owner flagged on both segments in both views; resolving
   the overlap clears both flags.
3. Print stylesheet: board chrome absent, light surface, `body-lg`+ type.
4. Board renders 15-min snap correctly for a segment starting at :07
   (visual position snaps, stored time doesn't).
5. No use of success/warning/danger fills anywhere on either view.
6. Staff erasure (PII) anonymizes owner chips on historical segments
   rather than deleting the segments.
