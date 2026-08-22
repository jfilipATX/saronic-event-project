# P5-6 — Executive PowerPoint Export

## Problem (user's words)
"The slides section is useless as it stands. Those need to be a PowerPoint
slide summary of the event covering key details to inform executives or get
sign off."

## Scope
- The on-screen `slides` view (HTML/markdown) stays as-is. This adds a **PPTX
  export** built with `python-pptx` (already a dependency) for executive
  sign-off. It is manually triggered (a "Download PowerPoint" button), never
  auto-generated.
- Content is a coherent event summary, not a dump of every screen. Executive
  readers want: what/where/when, the decisions made, the venue, who's running
  it, the run of show, and the spend. Not the reasoning apparatus.

## Deck structure (fixed, 16:9)
1. **Title slide** — event name, "Event Playbook — Executive Summary",
   owner + dates + location (city, ST, country).
2. **Overview** — event type, audience, city/state/country, date window,
   estimated attendance, event owner.
3. **Key decisions** — one row/bullet per decision in the playbook: the
   question, the chosen option, and its one-line reasoning. Source-attributed
   (e.g. "Venue: Port Alpha — fits 1,200 cap"). Skip pending/undecided with a
   single "Open questions: N" line.
4. **Venue** — chosen venue (or "established externally" for opt-out), fit
   badge, capacity, key amenities (yes/no/unknown stated honestly, not
   collapsed). If multiple venues, list them.
5. **Run of show** — the seeded/entered segments as a compact timeline table:
   time | segment | track | owner(s) | location. Group by day. Flag any
   double-booked owner in amber text (never block).
6. **Attendance & access** — headcount, VIP count, check-in staffing assigned
   (from P5-9 tags), manual-check-in note.
7. **Spend** — Claude API spend (or "none — ran offline"), sourced from the
   ledger. Whole cents on the slide; full precision lives in the ledger.
8. **Appendix (optional)** — generated visuals thumbnails? Keep out of v1;
   the deck is the summary, not the booth art.

## Brand & layout rules (from DESIGN.md)
- **Monochrome only.** Saronic wordmark is strictly monochrome — no signal-blue
  fills, no color beyond ink/neutral/steel. Slide background = neutral
  (`#F2F6FA`) or ink (`#162029`) for the title slide only.
- **Type:** headings = Archivo Expanded, uppercase, tracked. Body = Inter.
  python-pptx can set font name; embed via the same font files loaded for
  visuals (verify the name registers — fall back to system sans if not).
- **Logo:** monochrome wordmark top-left or bottom-right, role-named asset
  (not a colored lockup). No generative AI imagery — owned press-kit assets
  only.
- **No hex literals in code** — pull from the token module / DESIGN.md values.
- Slide count target: ≤ 10. Executives skim; density hurts.

## Trigger & cost
- Button on the `slides` view: "Download PowerPoint (.pptx)".
- No Claude call for the deck itself (content is already in the playbook).
  So **no spend, no cost estimate** for P5-6 — but if a future iteration uses
  Claude to *write* exec copy, it must go through the P3-1 ledger like
  everything else. v1 is template-driven from existing data → $0.
- Filename: `{event_slug}-executive-summary.pptx`, served as a download
  (`response_class=FileResponse`, `media_type` for pptx).

## Behaviour contract
- `GET /events/{event_id}/slides/export.pptx` → builds and returns the file.
- Reads the same `compose_playbook` + run-of-show + ledger the web views use, so
  the deck can never disagree with the on-screen playbook.
- Empty/missing data renders as "—" or "Not set", never a crash.

## Acceptance checks (TDD)
1. Export returns a valid .pptx (openable, correct slide count 7–10).
2. Deck title slide shows event name + owner + location with state/country.
3. Each playbook decision appears with its chosen option + reasoning.
4. Run-of-show segments appear grouped by day with owner names.
5. Double-booked owner flagged in amber text on the ROS slide.
6. Spend line shows ledger value (or "none — ran offline").
7. Missing venue/owner renders "Not set", not a 500.
8. Monochrome only — assert no fill uses signal-blue (token check), wordmark
   present and monochrome.

## Design notes
- This is the artifact an executive opens. The bar is "could this go to a
  three-star without embarrassment" — clean type, no reasoning clutter, honest
  "unknown" states. The on-screen slides remain the working view; this is the
  deliverable.
- python-pptx font embedding: set `slide.shapes.add_textbox(...).text_frame`
  runs' `.font.name = "Archivo Expanded"` and confirm the rendered file carries
  the font (a quick assert on run font name post-build). If embedding fails on
  the box, document the fallback.
