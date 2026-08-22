# UI Screenshots

Captured from a live server on `main` (post-phase-4) against real workflow
state — an event with dates, a seeded run of show carrying a genuine
double-booked owner, scraped-venue slate, and QR credentials minted and
scanned. Not mockups.

## Embed block (ready for README.md)

```markdown
## Screenshots

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
| *The composed playbook — every decision with reasoning* | *Slides with Claude-written copy, attributed* |

### QR check-in outcomes

| Valid | Replayed | Forged |
|---|---|---|
| ![Valid](docs/screenshots/07-scan-valid.png) | ![Already](docs/screenshots/07-scan-already.png) | ![Tampered](docs/screenshots/07-scan-tampered.png) |
```

Note: the board shot (`05b`) deliberately shows a conflict — an owner on two
overlapping segments with the amber outline and `⚠ shared owner` flag —
because that detection is the feature; a clean board reads as a generic
Gantt chart. Scan-state shots (`07-*`) are from phase 1 and remain accurate
(the banners are unchanged; the steel `withdrawn` state added later is not
yet captured).
