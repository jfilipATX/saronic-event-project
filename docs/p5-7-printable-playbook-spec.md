# P5-7 — Printable Playbook (offline day-of document)

## Problem (user's words)
"The playbook section also needs to generate a simple printable document that
can be utilized during the actual live event. It's to ensure that the
specifics of the event can be deployed and utilized offline."

## Scope
- The playbook is currently a web view + a markdown endpoint. Add a **clean
  print-optimized view** — a dedicated `/playbook/print` (or `@media print`
  on the existing page) that a coordinator can Ctrl-P to PDF or print and carry
  to the venue with no network.
- Distinct from the executive PPTX (P5-6): the PPTX is for sign-off; this is the
  **day-of operational reference** — every specific an on-site person needs,
  formatted to read on paper, in any light, fast to scan.

## Content (what a day-of operator needs)
1. Event header: name, owner, location (city, ST, country), date window,
   attendance estimate.
2. **Decision summary** — compact: each decision as "Q → Choice (why)". No
   expand/collapse, no hover tooltips (paper has none).
3. **Venue** — name, fit, capacity, amenities (yes/no/unknown stated),
   opt-out noted as "venue established externally: {event}".
4. **Run of show** — full segment list grouped by day: time | segment | track |
   owner(s) | location. Double-booked owners flagged in amber text.
5. **Check-in essentials** — VIP list (name, company, tier), check-in staff
   assigned, manual-check-in note. PII-scoped: show what a desk operator needs
   (name/company/tier), not full contact dumps.
6. **Spend** — Claude API spend or "none — ran offline".
7. **QR / credentials note** — where credentials live, not the codes themselves
   (don't print attendee PII or scannable codes on a shared doc).

## Print layout rules
- `@media print` (or a print stylesheet): hide nav, stepper, buttons, the
  eyebrow chrome, the "Edit" links, the usage footer. Show only content.
- **No dark surfaces in print** — a `surface` card with dark bg wastes ink and
  reads poorly. Force a white/neutral print background (`-webkit-print-color-
  adjust: exact` only where a token color must survive, e.g. the amber flag).
- Monospace/sans body at ≥11pt; headings Archivo Expanded. One column, page
  breaks between major sections (`break-inside: avoid` on cards).
- Page size: A4/Letter auto via `@page { size: auto; margin: 1.5cm }`.
- The amber double-booking flag MUST survive print (it's the most important
  thing on the page) — use `print-color-adjust: exact` on that element only.

## Trigger
- A "Print / Save as PDF" button on the playbook view → opens the print view
  (or calls `window.print()` on the print-styled page). Keep it one click.
- The print view is also directly URL-reachable (`/events/{id}/playbook/print`)
  so it can be bookmarked/saved.

## Behaviour contract
- Reads the same `compose_playbook` + run-of-show + ledger as the screen view,
  so print and screen never disagree.
- Missing data → "Not set" / "—", never a blank gap that looks like a rendering
  bug.
- No Claude call → no spend. The button is free.

## Acceptance checks (TDD)
1. Print view renders without nav/buttons (assert those elements absent in the
   print template, or that `@media print` hides them — test the template, not
   the browser print engine).
2. Run-of-show groups by day with owners.
3. Double-booked owner flagged (amber text) and the flag survives a print-CSS
   check (`print-color-adjust: exact` present on that element).
4. Location shows state + country when set.
5. PII discipline: attendee contact details absent from the print view; only
   name/company/tier for VIPs.
6. Missing venue/owner → "Not set", not a 500.

## Design notes
- This is the artifact that goes in a binder at the registration desk. The bar
  is "a tired operator in bad light can find the load-in time in 3 seconds."
  Density is good here (unlike the exec deck) — paper is for reference, slides
  are for persuasion.
- Reuse the existing `card-surface` / `option-card` structure but strip the
  interactive chrome in print. If maintaining two templates is cleaner than
  `@media print`, prefer the separate print template — it's easier to QA.
