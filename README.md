# Saronic Event Planning Tool

A decision-support tool for a **human event coordinator**: it researches options, explains trade-offs, and assembles every decision into a complete, exportable **event playbook** — but it never decides for you. Built as a working prototype with Claude at the center of its reasoning surfaces.

![Slides with Claude copy](docs/screenshots/08-slides-claude.png)
*Slide deck with genuine Claude-written copy — note the `copy: Claude` attribution badge.*

## What it does

The coordinator walks a decision chain — **event type → audience estimate → venue → slides → check-in → playbook** — and at every step the tool presents a full slate of options with plain-language reasoning, records the human's choice, and carries it forward. The output is a playbook document that captures not just what was decided, but *why*, what was rejected, and what's still open.

### Phase 2 additions

- **Event URL import.** Paste a public event page URL at creation and Claude extracts dates, city, venue and other facts — presented as *proposals the coordinator confirms*, each attributed to its source host, never silently trusted. Extraction iterates a field allowlist, so an invented field can never reach the event record. Fetching is SSRF-guarded (private ranges, redirects re-validated per hop, size/timeout caps, fail-closed DNS); a refused URL is reported honestly on the first step page with a manual-entry path, never a silent no-op.
- **Custom audience number.** The audience slate offers a `custom` option carrying the coordinator's own figure (e.g. a fixed invite list); it flows through venue-fit recalculation and revision-invalidation like any estimate, and the audit trail records it exactly as displayed.
- **Venue enrichment.** Website and OpenStreetMap links per venue, favourites, and prior-use history ("Previously hosted Corsair Demo Day in 2025"). Favourites can break ties only *within* a fit band — enforced in the sort key, so a favourite can never outrank a better-fitting venue.
- **Roster & check-in desk.** CSV roster import with auto-guessed column mapping, per-row skip reasons, duplicate-email dedup, and BOM/delimiter tolerance; check-in by email (a lookup, never a registration); four-field walk-ins; VIP flags with coordinator arrival alerts (honestly labelled "No email was sent: SMTP is not configured" until SMTP exists); and in-browser invite issuance minting signed QR credentials.
- **Visual composer.** Four booth/kiosk slide variants composited from owned imagery (coordinator uploads primary, press-kit product and pure-ink fallbacks) — generative imagery deliberately excluded as a brand-fidelity risk on defense hardware. Text contrast is *computed* against the final composite (median-sampled, scrim deepens until 4.5:1), EXIF GPS is stripped on upload, and every render writes a source-attribution sidecar.

### Data handling

- **Two deletion semantics, deliberately distinct:** *withdraw* (cancelled invitee — leaves history, revokes door access on every check-in path) and *erase* (irreversible PII destruction — name, email and credential destroyed in place, attendance kept as an anonymous tally). Erasure is test-verified by grepping every table for the erased values, not just the obvious row.
- A withdrawn credential scans as its own steel-coloured administrative state — deliberately not "tampered", because a cancelled guest is an admin conversation, not a security incident.
- Invitees added via the invite form need only name + email (they're known people); door walk-ins require name, email, title and company. The asymmetry is a decision, not an accident.

## Core design principles

- **Stage, never choose.** Every step is written as a *pending* decision carrying its full option slate. Only an explicit human action sets a choice — the tool structurally cannot advance the plan on its own (`record_decision` rejects any key that was never offered).
- **Append-only decision log.** Revising a choice inserts a successor row and back-links the old one (`superseded_by`); nothing is ever overwritten. Six weeks later you can still answer "why did we move off the Convention Center?"
- **Revision invalidates downstream.** Change the audience estimate and every venue-fit verdict computed against the old number is withdrawn and re-staged — visible stale reasoning is the one thing a decision-support tool must never show.
- **Providers report, presentation classifies, the human decides.** Option sources return complete slates (test-enforced: byte-identical regardless of query parameters); fit classification (`fits`/`tight`/`under`) happens in the presentation layer; nothing is ever hidden, and a flagged venue always says why it's still offered.
- **Brand and stock imagery are disjoint sets.** All Saronic assets (press-kit product shots, logos) resolve locally by image-role and need no API key or network. `city-stock`/`venue-stock` are the only roles a stock provider may serve, and the two sets are enforced disjoint in code, so a generic stock photo can never substitute for real Corsair/Marauder imagery. **As submitted, the deck requests only brand roles (`hero-16x9`, `imagery-alt`, `logo-on-dark`), so the tool runs entirely on owned assets with no stock provider configured.** The Pexels integration is live-tested and available (`PEXELS_API_KEY` in `.env` activates it) but dormant by default — a stock outage or missing key degrades to "no city photo," never "no logo."

## Quickstart

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env && chmod 600 .env
.venv/bin/python -m pytest tests/ -q        # 567 tests, all offline
.venv/bin/uvicorn app.main:create_app --factory
```

The default mode is fully mock — no keys, no network, no spend. Two settings matter:

- **Set `EVENT_SIGNING_SECRET` in `.env`** — otherwise a random per-process secret is used and QR invites won't survive a server restart (they'll correctly scan as tampered).
- To enable real Claude: run `bash scripts/add_anthropic_key.sh` (interactive, no-echo key entry), then **`​.venv/bin/python scripts/probe_models.py`** — a zero-cost check of which models your key can actually reach, before anything spends. Set `ANTHROPIC_MODEL` to a model the probe confirms.

## Screenshots

Captured from a live server against real workflow state — not mockups.

| | |
|---|---|
| ![Home](docs/screenshots/01-home.png) | ![Event type](docs/screenshots/02-event-type.png) |
| *Home — start an event* | *Step 1: event type, options + reasoning* |
| ![Audience](docs/screenshots/03-audience.png) | ![Venue](docs/screenshots/04-venue.png) |
| *Step 2: audience estimate* | *Step 3: venue slate with fit badges — nothing hidden* |
| ![Check-in](docs/screenshots/05-checkin.png) | ![Playbook](docs/screenshots/06-playbook.png) |
| *Day-of check-in desk* | *Composed playbook with decision log* |

### QR check-in outcomes

| Valid | Replayed | Forged |
|---|---|---|
| ![Valid](docs/screenshots/07-scan-valid.png) | ![Already](docs/screenshots/07-scan-already.png) | ![Tampered](docs/screenshots/07-scan-tampered.png) |

## How we used Claude

Two distinct claims, made precisely:

1. **In the running app, Claude drives slide copy.** `app/features/slide_copy.py` grounds its prompt in the coordinator's actual recorded decisions (event name, city, type, audience, venue), so the copy cannot describe an event nobody planned. Every slide carries a visible `copy: Claude` / `copy: deterministic` badge — attribution lives in the product, not just this README. Mock scaffolding is structurally barred from decks (any `[MOCK CLAUDE]` marker in a response is rejected), and any Claude failure — budget, rate limit, bad key — degrades to deterministic copy, never a broken deck.
2. **All five reasoning surfaces are verified end-to-end** by `scripts/real_claude_pass.py`: event-type classification, audience-estimate reasoning, venue trade-off analysis, slide copy, and playbook summary. Full prompts, responses, and spend are captured as evidence in [`generated/claude-pass/`](generated/claude-pass/) — total spend **$0.2237** on `claude-opus-5` against a $5 harness cap.

The other surfaces (venue fit arithmetic, audience bracketing) stay deterministic **on purpose**: a language model would make them less testable without making them more useful. Where Claude genuinely adds judgment, it showed: the venue trade-off pass flagged that an over-capacity audience is "not a discount — a compliance problem… fire marshal sign-off is the gate, not your preference," a framing our deterministic fit logic doesn't produce.

### What building on a reasoning model taught us

The most useful finding generalizes beyond this project: **`claude-opus-5` emits thinking blocks that draw from the same `max_tokens` budget as the answer.** With a 700-token budget, the model spent it all thinking and returned an empty text block — billed, reported "ok," zero characters of output. A blank that looks like success is the most dangerous failure shape; on a title slide it ships an empty headline to a room. The client now raises `EmptyResponseError` (spend still recorded — we were billed, the meter must know), and budgets are sized for thinking *plus* output. We only caught it because the evidence pass checks character counts instead of trusting status flags.

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
├── features/        # workflow, venue_options, slide_copy, deck, playbook, qr_checkin…
├── providers/       # base Protocols; mock/ and real/ behind a registry
├── routers/         # thin FastAPI routes — pure translation to the workflow
└── ui/              # Jinja2 templates + DESIGN.md-derived token CSS
```

Every route translates to `CoordinatorWorkflow`, so the UI cannot drift from stage-never-choose. The QR check-in surface is HMAC-signed, replay-safe (a replay returns ALREADY without touching the original timestamp), fails closed on malformed input, and carries no PII in tokens (they're printed on badges).

## Verification

- **567 tests**, all green, offline by default — including adversarial QR tests (forged ids, wrong-secret signatures, never-issued codes), provider fail-soft (network errors degrade, never crash), and guard-rail tests on the spend meter.
- **Secret hygiene:** `scripts/audit_secrets.py` scans all tracked files *and full commit history* for key values (not names), wired as a pre-push hook. Verified in both directions — clean on the real repo, and a negative control with a staged key gets caught and blocked.
- Evidence artifacts are tracked deliberately: [`generated/`](generated/) holds exported playbooks and the complete Claude pass transcripts.

## Design system

[`DESIGN.md`](DESIGN.md) derives every token from the Saronic press kit — ink `#162029` sampled from the wordmark, monochrome brand behavior preserved (accent blue is product-UI-only), Archivo Expanded for the wordmark voice (composites fall back to DejaVu with an honest "(fallback)" label until font files land in `assets/fonts/` — the loader picks them up with no code change). Templates reference tokens only; `grep -rn 'style="' app/ui/templates/` returns zero hits.
