# Saronic Event Planning Tool

A decision-support tool for a **human event coordinator**: it researches options, explains trade-offs, and assembles every decision into a complete, exportable **event playbook** — but it never decides for you. You stay in the loop at every step; the tool stages options with plain-language reasoning, records your choice, and carries it forward into slides, a printable day-of document, and a PowerPoint deck.

![Slides with Claude copy](docs/screenshots/08-slides-claude.png)
*Slide deck with genuine model-written copy — note the `copy: Claude` attribution badge.*

## Contents

- [What it does](#what-it-does)
- [Quickstart](#quickstart)
- [Using the Concierge (natural-language intake)](#using-the-concierge-natural-language-intake)
- [Feature tour by surface](#feature-tour-by-surface)
- [Data handling](#data-handling)
- [Core design principles](#core-design-principles)
- [Screenshots](#screenshots)
- [How the model is used](#how-the-model-is-used)
- [Architecture](#architecture)
- [Verification](#verification)
- [Design system](#design-system)

## What it does

The coordinator walks a decision chain — **event type → audience estimate → venue → run-of-show → slides → check-in → playbook** — and at every step the tool presents a full slate of options with reasoning, records the human's choice, and carries it forward. The output is a playbook document that captures not just *what* was decided, but *why*, what was rejected, and what's still open.

On top of that chain, the tool adds:

- **A Concierge chat** — describe the event in plain language and the tool fills in venue, run-of-show, audience and event facts for you (or edits them later: "move doors to 11am").
- **Event lifecycle** — mark an event Complete (read-only), Archive it (recoverable, hidden from the active list), or Delete it (anonymized stub: PII wiped, decision/segment counts kept).
- **Branded exports** — a monochrome PowerPoint deck with the Saronic wordmark, and a chrome-free, print-ready playbook that also carries the per-day run-of-show gantt.
- **A Fleet Week demo** — one click seeds a fully-fleshed, clearly-labeled sample multi-day expo so you can explore the tool immediately.

### Feature matrix

| Surface | What it does | Works offline? |
|---|---|---|
| Decision chain (type / audience / venue) | Stages options + reasoning, records your choice | ✅ fully |
| Run-of-show + gantt | Color-coded timeline, owner labels, per-day bands | ✅ fully |
| Slides + PPTX export | Deck from the playbook, Saronic-branded | ✅ fully (no model needed) |
| Printable playbook | Chrome-free print view + per-day gantt | ✅ fully |
| Check-in / QR invites | Signed, replay-safe credentials | ✅ fully |
| Visuals composer | Booth/kiosk composites from owned assets | ✅ fully (stock optional) |
| **Concierge (NL intake)** | Fills the above from plain sentences | ⚠️ stages offline; *parsing* needs a model key |
| LLM venue search / playbook copy | Extra model judgment on demand | ❌ needs a key |

## Quickstart

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env && chmod 600 .env
.venv/bin/python -m pytest tests/ -q        # 908 tests, all offline
.venv/bin/uvicorn app.main:create_app --factory
```

Open `http://127.0.0.1:8000`. **The tool runs fully offline with no keys** — every feature above works except the model-assisted ones.

Two settings matter:

- **Set `EVENT_SIGNING_SECRET` in `.env`** — otherwise a random per-process secret is used and QR invites won't survive a server restart (they'll correctly scan as tampered).
- To enable the model-assisted surfaces (Concierge natural-language parsing, LLM venue search, playbook copy): run `bash scripts/add_anthropic_key.sh` (interactive, no-echo key entry), then **`.venv/bin/python scripts/probe_models.py`** — a zero-cost check of which models your key can reach. Set `ANTHROPIC_MODEL` to a model the probe confirms. No key is ever required for the rest of the tool.

### First run, in three steps

1. Click **Load Fleet Week demo** on the home page to seed a sample event, or create your own event.
2. Walk the decision chain — or open **Concierge** and describe the event in a sentence.
3. Export: **Download PowerPoint** from Slides, **Print / Save as PDF** from the Playbook.

## Using the Concierge (natural-language intake)

The Concierge is a chat surface (nav link in the shell) where an event manager talks in plain language instead of filling forms. Pick an event from the dropdown, then type.

**It can intake:**

- *"it's a convention"* → records the event type.
- *"plan for 2,000 people, mostly defense buyers"* → records the audience estimate.
- *"add venue Port Alpha, San Diego, capacity 1200, with catering and security"* → adds the venue to the slate.
- *"add Doors open 9 to 10, Program"* → creates a run-of-show segment (times are auto-anchored to the event day).
- *"add variable hotel block: Marriott cutoff May 1"* → attaches an event-level fact.

**It can edit what's there:** *"move doors to 11am"*, *"change event type to expo"*, *"audience 500"* — it finds the matching segment/decision and updates it, keeping the change on the same day.

**Scope:** venue, run-of-show, event_type, audience, and event variables. Slides and check-in are display/output surfaces, not decisions, so they stay form/button-driven (the Concierge will tell you "use the forms" honestly if you ask it about them).

**Offline behavior:** the intake *flow* works without a model — it stages your answers. The natural-language *parsing* (turning a sentence into structured fields) uses a model; if no key is configured, the Concierge returns a plain "the language model isn't reachable right now — your answers aren't lost" message rather than erroring. The 908 tests verify the sentence→decision mapping with a stand-in client, so the logic is proven; it just needs a real model at runtime for live parsing.

## Feature tour by surface

### Phase 2 additions

- **Event URL import.** Paste a public event page URL at creation and the tool extracts dates, city, venue and other facts — presented as *proposals the coordinator confirms*, each attributed to its source host, never silently trusted. Extraction iterates a field allowlist, so an invented field can never reach the event record. Fetching is SSRF-guarded (private ranges, redirects re-validated per hop, size/timeout caps, fail-closed DNS); a refused URL is reported honestly on the first step page with a manual-entry path, never a silent no-op.
- **Custom audience number.** The audience slate offers a `custom` option carrying the coordinator's own figure (e.g. a fixed invite list); it flows through venue-fit recalculation and revision-invalidation like any estimate, and the audit trail records it exactly as displayed.
- **Venue enrichment.** Website and OpenStreetMap links per venue, favourites, and prior-use history ("Previously hosted Corsair Demo Day in 2025"). Favourites can break ties only *within* a fit band — enforced in the sort key, so a favourite can never outrank a better-fitting venue.
- **Roster & check-in desk.** CSV roster import with auto-guessed column mapping, per-row skip reasons, duplicate-email dedup, and BOM/delimiter tolerance; check-in by email (a lookup, never a registration); four-field walk-ins; VIP flags with coordinator arrival alerts (honestly labelled "No email was sent: SMTP is not configured" until SMTP exists); and in-browser invite issuance minting signed QR credentials.
- **Visual composer.** Four booth/kiosk slide variants composited from owned imagery (coordinator uploads primary, press-kit product and pure-ink fallbacks) — generative imagery deliberately excluded as a brand-fidelity risk on defense hardware. Text contrast is *computed* against the final composite (median-sampled, scrim deepens until 4.5:1), EXIF GPS is stripped on upload, and every render writes a source-attribution sidecar.

### Phases 6–9 additions

- **Event lifecycle (Phase 6).** Mark an event **Complete** (read-only status flag), **Archive** it (soft-hidden, recoverable, with an Unarchive path), or **Delete** it — delete produces an *anonymized stub*: attendee/staff PII is wiped in place, but the event row and its decision/segment counts are preserved so totals stay accurate. Demo events carry an `is_demo` badge.
- **PPTX download (Phase 6).** A "Download PowerPoint (.pptx)" button on the Slides page exports the deck (monochrome, Saronic-wordmarked, ≤10 slides) — no separate tool needed.
- **Example imagery + Fleet Week demo (Phase 6).** Four bundled demo images (expo-hall, vessel, panel, kiosk) seed the visual library; **Load Fleet Week demo** builds a fully-fleshed, labeled multi-day expo (hall, presentations, kiosk, VIP lunches, panel) so you can explore immediately.
- **Run-of-show chart (Phase 6).** Nine color-coded block types (booth / presentation / visitor / dinner / panel / logistics / floor / program / VIP) as both a single-event timeline and a portfolio overview across events.
- **Discoverability + print readability (Phase 7).** Lifecycle controls render as visible side-by-side buttons; the printable playbook was rebuilt for paper (12.5pt body, raised contrast, page-constrained) with the amber conflict flag preserved via `print-color-adjust: exact` and attendee emails excluded.
- **Gantt owners + per-day (Phase 7).** Run-of-show blocks show owner "Name · Role"; multi-day events render one band per day with day-jump links, removing the blank space between non-adjacent days. The per-day gantt is also appended to the printable playbook (end of doc, so it can be skipped).
- **Branded PPTX (Phase 7).** Deck carries the full Saronic wordmark on the title slide and a footer wordmark on every content slide; content frames use the full slide space.
- **Captioned product visuals (Phase 7).** Real Saronic press-kit product shots seed the library alongside captioned synthetic venue/city layers, so generated images are identifiable.
- **Concierge NL intake (Phase 8–9).** See [Using the Concierge](#using-the-concierge-natural-language-intake). Covers venue, run-of-show, event_type, audience, and event variables, plus free-form edits, with graceful degradation when no model is configured.

## Data handling

- **Two deletion semantics, deliberately distinct:** *withdraw* (cancelled invitee — leaves history, revokes door access on every check-in path) and *erase* (irreversible PII destruction — name, email and credential destroyed in place, attendance kept as an anonymous tally). Erasure is test-verified by grepping every table for the erased values, not just the obvious row.
- A withdrawn credential scans as its own steel-coloured administrative state — deliberately not "tampered", because a cancelled guest is an admin conversation, not a security incident.
- Invitees added via the invite form need only name + email (they're known people); door walk-ins require name, email, title and company. The asymmetry is a decision, not an accident.

## Core design principles

- **Stage, never choose.** Every step is written as a *pending* decision carrying its full option slate. Only an explicit human action sets a choice — the tool structurally cannot advance the plan on its own (`record_decision` rejects any key that was never offered).
- **Append-only decision log.** Revising a choice inserts a successor row and back-links the old one (`superseded_by`); nothing is ever overwritten. Six weeks later you can still answer "why did we move off the Convention Center?"
- **Revision invalidates downstream.** Change the audience estimate and every venue-fit verdict computed against the old number is withdrawn and re-staged — visible stale reasoning is the one thing a decision-support tool must never show.
- **Providers report, presentation classifies, the human decides.** Option sources return complete slates (test-enforced: byte-identical regardless of query parameters); fit classification (`fits`/`tight`/`under`) happens in the presentation layer; nothing is ever hidden, and a flagged venue always says why it's still offered.
- **Brand and stock imagery are disjoint sets.** All Saronic assets (press-kit product shots, logos) resolve locally by image-role and need no API key or network. `city-stock`/`venue-stock` are the only roles a stock provider may serve, and the two sets are enforced disjoint in code, so a generic stock photo can never substitute for real Corsair/Marauder imagery. **As submitted, the deck requests only brand roles (`hero-16x9`, `imagery-alt`, `logo-on-dark`), so the tool runs entirely on owned assets with no stock provider configured.** The Pexels integration is live-tested and available (`PEXELS_API_KEY` in `.env` activates it) but dormant by default — a stock outage or missing key degrades to "no city photo," never "no logo."

## Screenshots

Captured from a live server against real workflow state — not mockups.

| | |
|---|---|
| ![Home](docs/screenshots/01-home.png) | ![Event type](docs/screenshots/02-event-type.png) |
| *Home — start an event* | *Step 1: event type, options + reasoning* |
| ![Audience](docs/screenshots/03-audience.png) | ![Venue](docs/screenshots/04-venue.png) |
| *Step 2: audience estimate (custom values supported)* | *Step 3: venue slate — fit badges, amenities, favourites, opt-out* |
| ![Add venue](docs/screenshots/04b-venue-add.png) | ![Run of show](docs/screenshots/05-run-of-show.png) |
| *Add a venue by URL — scraped facts are proposals* | *Run of show — printable day-of document* |
| ![Concurrency board](docs/screenshots/05b-board-conflict.png) | ![Check-in](docs/screenshots/05-checkin.png) |
| *Concurrency board — double-booked owner flagged, never blocked* | *Day-of check-in desk* |
| ![Visuals](docs/screenshots/06b-visuals.png) | ![Invitations](docs/screenshots/06c-invites.png) |
| *Composited booth/kiosk visuals from owned assets* | *Issue QR credentials from the browser* |
| ![Playbook](docs/screenshots/06-playbook.png) | ![Slides](docs/screenshots/08-slides-claude.png) |
| *The composed playbook — every decision with reasoning* | *Slides with model-written copy, attributed* |

### QR check-in outcomes

| Valid | Replayed | Forged |
|---|---|---|
| ![Valid](docs/screenshots/07-scan-valid.png) | ![Already](docs/screenshots/07-scan-already.png) | ![Tampered](docs/screenshots/07-scan-tampered.png) |

## How the model is used

Two distinct claims, made precisely:

1. **In the running app, the model drives slide copy.** `app/features/slide_copy.py` grounds its prompt in the coordinator's actual recorded decisions (event name, city, type, audience, venue), so the copy cannot describe an event nobody planned. Every slide carries a visible `copy: Claude` / `copy: deterministic` badge — attribution lives in the product, not just this README. Mock scaffolding is structurally barred from decks (any `[MOCK CLAUDE]` marker in a response is rejected), and any model failure — budget, rate limit, bad key — degrades to deterministic copy, never a broken deck.
2. **All five reasoning surfaces are verified end-to-end** by `scripts/real_claude_pass.py`: event-type classification, audience-estimate reasoning, venue trade-off analysis, slide copy, and playbook summary. Full prompts, responses, and spend are captured as evidence in [`generated/claude-pass/`](generated/claude-pass/) — total spend **$0.2237** on `claude-opus-5` against a $5 harness cap.

The other surfaces (venue fit arithmetic, audience bracketing) stay deterministic **on purpose**: a language model would make them less testable without making them more useful. Where the model genuinely adds judgment, it showed: the venue trade-off pass flagged that an over-capacity audience is "not a discount — a compliance problem… fire marshal sign-off is the gate, not your preference," a framing our deterministic fit logic doesn't produce.

### What building on a reasoning model taught us

The most useful finding generalizes beyond this project: **`claude-opus-5` emits thinking blocks that draw from the same `max_tokens` budget as the answer.** With a 700-token budget, the model spent it all thinking and returned an empty text block — billed, reported "ok," zero characters of output. A blank that looks like success is the most dangerous failure shape; on a title slide it ships an empty headline to a room. The client now raises `EmptyResponseError` (spend still recorded — we were billed, the meter must know), and budgets are sized for thinking *plus* output. We only caught it because the evidence pass checks character counts instead of trusting status flags.

### What testing this taught us

Two findings that generalize past this codebase, both discovered by a test that should have passed and didn't.

**A fixture has to be as varied as the thing it stands in for.** The backdrop classifier separates photographs from logo cards by counting distinct colours in a downscaled sample — real Saronic blog photos hold 1800–3800, partnership logo cards 214–242. Two synthetic "photographs" failed it and both were the fixture's fault: a 4×4 block pattern held 42 colours, and per-pixel random noise held 475, because the classifier's LANCZOS downscale averages high-frequency noise away. Real photographs survive downscaling because their variation is *structured at every scale*. The near-miss is the lesson: the tempting fix was lowering the threshold, which would have broken real classification to make a fake image pass.

**Empty-state UI tests prove only that a page renders when there is nothing to render.** A venue-scrape template used `selectattr('key', 'match', …)` — Jinja has no `match` test — and 33 tests passed while a real scrape returned 500, because every one of them exercised the empty-options path where the loop never runs. Any new template loop now gets a populated-branch test.

### Cost controls

- `SpendMeter` halts **before** the next call, not after (test-pinned: SDK call count stays 0 once the budget is exhausted).
- Real mode requires **both** `ANTHROPIC_API_KEY` and `USE_REAL_CLAUDE=1` — a key sitting in `.env` can never spend on its own.
- `scripts/probe_models.py` verifies model entitlement for free before any paid call.
- Failed calls record no spend; empty responses record theirs.

## Architecture

```
app/
├── claude/          # single Claude gateway: client, SpendMeter, typed errors
├── db/              # SQLite schema, repository, hydration boundary
├── features/        # workflow, venue_options, slide_copy, deck, playbook, qr_checkin, concierge…
├── providers/       # base Protocols; mock/ and real/ behind a registry
├── routers/         # thin FastAPI routes — pure translation to the workflow
└── ui/              # Jinja2 templates + DESIGN.md-derived token CSS
```

Every route translates to `CoordinatorWorkflow`, so the UI cannot drift from stage-never-choose. The QR check-in surface is HMAC-signed, replay-safe (a replay returns ALREADY without touching the original timestamp), fails closed on malformed input, and carries no PII in tokens (they're printed on badges). The Concierge translates natural language into the same `CoordinatorWorkflow` + repository calls the forms use, so there is one source of truth for every change it makes.

## Verification

- **908 tests**, all green, offline by default — including adversarial QR tests (forged ids, wrong-secret signatures, never-issued codes), provider fail-soft (network errors degrade, never crash), and guard-rail tests on the spend meter).
- **Secret hygiene:** `scripts/audit_secrets.py` scans all tracked files *and full commit history* for key values (not names), wired as a pre-push hook. Verified in both directions — clean on the real repo, and a negative control with a staged key gets caught and blocked.
- Evidence artifacts are tracked deliberately: [`generated/`](generated/) holds exported playbooks and the complete Claude pass transcripts.
- **Phase 5:** see [`docs/phase-5-summary.md`](docs/phase-5-summary.md) for the full feature list, what the lightweight P5-9 owners/gating deliberately does *not* do (no auth yet), and the branch-discipline note.

## Design system

[`DESIGN.md`](DESIGN.md) derives every token from the Saronic press kit — ink `#162029` sampled from the wordmark, monochrome brand behavior preserved (accent blue is product-UI-only), Archivo Expanded for the wordmark voice (composites fall back to DejaVu with an honest "(fallback)" label until font files land in `assets/fonts/` — the loader picks them up with no code change). Templates reference tokens only; `grep -rn 'style="' app/ui/templates/` returns zero hits.
