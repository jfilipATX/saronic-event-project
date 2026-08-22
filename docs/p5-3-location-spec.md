# P5-3 — Location Precision (state + country)

## Requirement (user)
"Include state and country, default the country selection to the US. We want to
avoid any confusion with cities that have the same names in different
locations." The location line must render fully (`City, ST, Country`) wherever
the city currently appears — same-name cities (Springfield, Alexandria,
Richmond…) must never collapse to just the city.

## Locked decisions (from PM + Brian)
- `state` + `country` columns on `events` (migration via `_ADDED_COLUMNS`).
- **`country` defaults to `US` at the model/validation layer**, not just the
  form — a blank import, API-fed event, or missing form value still resolves to
  `US`, never an empty country.
- A single canonical `Event.location` property on the **model** returns the
  formatted line via one helper (`location_line`), so every display consumes
  the same string and cannot drift. Templates use `{{ event.location }}`
  everywhere a city is shown.
- The form (event create/edit) shows `City` + `State` (text or 2-letter, free
  input is fine for now) + `Country` pre-selected to `US`.

## Display contract — every surface that shows a city
Replace the bare `{{ event.city }}` (or equivalent) with `{{ event.location }}`
on:
1. **Event form** — City / State / Country(=US default) inputs.
2. **`base.html` nav-sub** — the event header line under the title.
3. **Venue cards** — the "where" line.
4. **Playbook** — event facts block.
5. **Check-in desk** — event identity header (so a desk operator knows which
   event/city they're at).
6. **Run of show** — header (day-of document).
7. **Slides / PPTX / printable playbook** — the location line on title/overview.
8. **Visuals base layer caption** (if it names the city).

## Formatting rules (`location_line` helper)
- Order: `City, ST, Country` when all present.
- Drop missing middle pieces without leaving dangling commas: `City, Country`
  (no state), `City, ST` (no country — should be rare given US default),
  `City` (neither).
- `state` shown as entered (2-letter or full both acceptable; do not invent
  abbreviations).
- `country` shown as the ISO code or full name consistently — pick one and use
  it everywhere (recommend **ISO alpha-2 / alpha-3**, e.g. "US", since the UI
  defaults to US and it's compact on cards). Document the choice.
- Never render `, ,` or a trailing comma.

## Behaviour contract
- Blank `country` → `US` (model default, not form-only).
- Blank `state` → line omits state (no fake "—" filler; just `City, US`).
- Legacy events with no `state`/`country` columns migrate cleanly (default US,
  null state) — no 500 on old databases.

## Acceptance checks (TDD)
1. Event with city+state+country renders `City, ST, Country`.
2. Blank country resolves to `US` (model level — test the model property, not
   just the form).
3. Blank state renders `City, US` (no dangling comma).
4. Same-name city (e.g. "Springfield, IL, US" vs "Springfield, MO, US") is
   distinguishable on every surface listed above (no surface shows bare
   "Springfield").
5. Legacy event (pre-migration, no state/country) renders `City, US`, no 500.
6. `location_line` helper drops nothing incorrectly: no `, ,`, no trailing
   comma, across all 8 blank-combination cases.

## Design notes
- This is a consistency refactor more than a new surface: the work is replacing
  `event.city` references with `event.location` and adding two form fields.
- The US-default is a product decision (Saronic is US-based, multi-state), not
  a geo assumption — keep it a model default so it holds for any entry path.
- Font: the location line inherits body type; on cards it's the existing
  `muted` treatment. No new token.
