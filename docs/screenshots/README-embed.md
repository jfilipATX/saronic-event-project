# UI Screenshots

Captured from a live server (`uvicorn app.main:create_app --factory`) against
real workflow state — an event walked through the full decision chain, a signed
invite scanned once (valid), replayed (already), and a forged code rejected
(tampered). Not mockups.

`07-*` scan states pending reshoot of `06`/slides once slide copy is
Claude-generated (see board); everything below is final.

## Embed block (ready for README.md)

```markdown
## Screenshots

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
```
