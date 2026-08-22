# P5-8 — Manual Check-in (facilitator lookup)

## Problem
Heads of state, prime ministers, presidents, and other top-tier VIPs will not
scan a QR code or fill a walk-in form. The check-in desk needs a **facilitator**
to look up an existing invitee by name/email and mark them arrived — no code,
no walk-in form — with the action explicitly logged.

## Scope
- Reuses the existing attendee roster + VIP logic. No new data model: a manual
  check-in is the same `STATE_VALID` arrival, just recorded by a person instead
  of a scan.
- The actor (who did it) is recorded. There is no login yet (P5-9 light), so the
  actor is "facilitator" from the check-in section — i.e. we record that the
  action was *manual*, not auto-scanned. If P5-9 later adds a named actor, the
  field is already there to populate.
- Distinct from walk-in: a walk-in has NO invitee record (new person). A manual
  check-in is a lookup of an EXISTING invitee who simply didn't scan.

## UI contract (checkin.html)
Two check-in paths side by side, clearly separated:

1. **Scan / code** (existing): QR + walk-in form, unchanged.
2. **Manual lookup** (new): a `card-surface` with a single search input
   (`name OR email`) and a "Find" button. On submit, render a candidate list:
   - Each candidate shows name, email, company, VIP flag, and current arrival
     state (`Arrived HH:MM` / `Not yet arrived`).
   - An "Mark arrived" button per candidate (disabled if already arrived).
   - Search is case-insensitive substring on name/email; show up to ~10 matches,
     note if truncated.
3. No candidate found → steel `pending-note`: "No matching invitee — if this is a
   walk-in, use the form below."

## Behaviour contract
- `POST /events/{event_id}/checkin/manual` with `attendee_id`.
- Records arrival exactly as a valid scan would (same `mark_arrived` path,
  `STATE_VALID`), but sets `checkin_method = "manual"` and logs the action via
  the existing audit mechanism (append to `scan_log` with method=manual).
- Re-announcing an already-arrived VIP is suppressed exactly as for scans (VIP
  banner only on NEW arrival) — reuse the existing `vip` flag logic.
- Manual check-in of a VIP still shows the VIP banner (company, name) on the
  desk — same as a scan.

## Audit / honesty
- The spend ledger is untouched (no Claude call).
- The action log must distinguish `manual` from `scan` so a later reader knows
  the arrival was recorded by a person, not a code. This is a safety/security
  record, same class as erasure.

## Acceptance checks (TDD)
1. Manual lookup by last name returns the matching invitee(s).
2. Manual check-in of an existing invitee sets arrived + method=manual; desk
   shows VIP banner for a VIP.
3. Re-checking an already-arrived invitee does NOT re-announce (vip flag off).
4. Lookup with no match shows the steel note, NOT a 500, NOT a walk-in creation.
5. `scan_log` row for a manual arrival records `method="manual"`.
6. Manual check-in reuses the SAME arrival record as a scan (no duplicate
   attendee created).

## Design notes
- Keep the manual path visually quieter than the primary scan flow — it's the
  exception, not the rule. A `btn-quiet` "Find" and a bordered `card-surface`
  for the results reads as a tool, not a second front door.
- Search box uses `.input` (dark surface). Candidate rows reuse the
  `owner-chip` / `option-card` vocabulary so they match the rest of the app.
