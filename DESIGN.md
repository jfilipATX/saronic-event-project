---
version: alpha
name: Saronic Event
description: >-
  Brand-accurate design tokens for the Saronic Live-Event Planning Tool, seeded
  from the official Saronic Press Kit (July 2026). Dark, high-contrast event
  theme tuned for 16:9 on-site projector/monitor displays, with a light
  print/invite variant for exported playbook and invite material. Scan-state
  tokens map 1:1 to QR check-in outcomes (valid / already scanned / tampered).
colors:
  primary: "#162029"
  secondary: "#55636E"
  tertiary: "#4C9FD8"
  neutral: "#F2F6FA"
  background: "#0C141B"
  surface: "#1E2A35"
  steel: "#9DA7AF"
  success: "#27C281"
  warning: "#F2B100"
  danger: "#E5484D"
typography:
  display-xl:
    fontFamily: Archivo Expanded
    fontSize: 6rem
    fontWeight: 800
    lineHeight: 1.05
    letterSpacing: "0.01em"
  h1:
    fontFamily: Archivo Expanded
    fontSize: 3rem
    fontWeight: 700
    lineHeight: 1.1
    letterSpacing: "0.01em"
  h2:
    fontFamily: Archivo Expanded
    fontSize: 2rem
    fontWeight: 700
    lineHeight: 1.15
  body-lg:
    fontFamily: Inter
    fontSize: 1.25rem
    fontWeight: 400
    lineHeight: 1.5
  body-md:
    fontFamily: Inter
    fontSize: 1rem
    fontWeight: 400
    lineHeight: 1.5
  label:
    fontFamily: Archivo Expanded
    fontSize: 0.875rem
    fontWeight: 600
    lineHeight: 1.2
    letterSpacing: "0.10em"
rounded:
  sm: 4px
  md: 8px
  lg: 16px
  xl: 24px
spacing:
  sm: 8px
  md: 16px
  lg: 24px
  xl: 40px
components:
  button-primary:
    backgroundColor: "{colors.tertiary}"
    textColor: "#0C141B"
    typography: "{typography.label}"
    rounded: "{rounded.md}"
    padding: 16px
  button-primary-hover:
    backgroundColor: "{colors.neutral}"
    textColor: "#0C141B"
    typography: "{typography.label}"
    rounded: "{rounded.md}"
    padding: 16px
  card-surface:
    backgroundColor: "{colors.surface}"
    textColor: "{colors.neutral}"
    typography: "{typography.body-md}"
    rounded: "{rounded.lg}"
    padding: 24px
  muted-text:
    backgroundColor: "{colors.background}"
    textColor: "{colors.steel}"
    typography: "{typography.body-md}"
    rounded: "{rounded.sm}"
    padding: 0px
  scan-valid:
    backgroundColor: "{colors.success}"
    textColor: "#0C141B"
    typography: "{typography.h2}"
    rounded: "{rounded.md}"
    padding: 16px
  scan-already:
    backgroundColor: "{colors.warning}"
    textColor: "#0C141B"
    typography: "{typography.h2}"
    rounded: "{rounded.md}"
    padding: 16px
  scan-tampered:
    backgroundColor: "{colors.danger}"
    textColor: "#0C141B"
    typography: "{typography.h2}"
    rounded: "{rounded.md}"
    padding: 16px
  slide-title-onsite:
    backgroundColor: "{colors.background}"
    textColor: "{colors.neutral}"
    typography: "{typography.display-xl}"
    rounded: "{rounded.sm}"
    padding: 40px
  slide-body-onsite:
    backgroundColor: "{colors.background}"
    textColor: "{colors.neutral}"
    typography: "{typography.body-lg}"
    rounded: "{rounded.sm}"
    padding: 24px
  playbook-page:
    backgroundColor: "{colors.neutral}"
    textColor: "{colors.primary}"
    typography: "{typography.body-md}"
    rounded: "{rounded.sm}"
    padding: 40px
  playbook-heading:
    backgroundColor: "{colors.neutral}"
    textColor: "{colors.primary}"
    typography: "{typography.h2}"
    rounded: "{rounded.sm}"
    padding: 0px
---

# Overview

This token set defines the visual identity for the **Saronic Live-Event Planning
Tool** — a decision-support app for a *human* event coordinator: it researches
venues, estimates audience, classifies event type, collects VIP/clearance/speaker
variables, generates on-site + invite slides, runs QR check-in, and composes
every confirmed decision into an exportable **event playbook**.

Values are sourced from the official **Saronic Press Kit (July 2026)**:

- Wordmark ink sampled from `Saronic_Logo_Full--Dark.png` → `#162029`
- Light counterpart sampled from `Saronic_Logo_Full--Light.png` → `#F2F6FA`
- Steel/secondary tones sampled from Corsair/Marauder product imagery
- The Saronic mark is **strictly monochrome** — a heavy, extended geometric
  uppercase sans with a slashed hull-form symbol. The brand supplies *no* accent
  color, so the tertiary signal blue is a product-UI addition, kept singular and
  restrained.

# Colors

- **Primary (`#162029`)** — Saronic ink. Exact wordmark color. Text on light
  surfaces (playbook/invite exports) and chrome base.
- **Secondary (`#55636E`)** — hull steel, sampled from vessel imagery. Dividers,
  supporting UI on light surfaces.
- **Tertiary (`#4C9FD8`)** — signal blue, the *only* interactive accent. Tuned
  steelier than a consumer blue to sit with the maritime palette. Always carries
  **dark text** (`#0C141B`) — it fails AA under white text.
- **Neutral (`#F2F6FA`)** — press-kit light. Text on dark surfaces; the playbook
  page background.
- **Background (`#0C141B`)** — deepest ink. On-site display canvas (one step
  darker than primary so the logo-ink chrome still reads as a layer).
- **Surface (`#1E2A35`)** — raised panel/card fill on the dark canvas.
- **Steel (`#9DA7AF`)** — muted caption/metadata text on dark surfaces, sampled
  from overcast-sea imagery.
- **Success / Warning / Danger** — scan outcomes only (valid / already-scanned /
  tampered), all with dark text for cross-room legibility.

# Typography

Two families, mirroring the brand's voice:

- **Archivo Expanded** (display/headings/labels) — free Google variable font;
  its wide, heavy uppercase forms are the closest open match to the extended
  geometric wordmark. Use **uppercase** for `display-xl`, `h1`, and `label` to
  echo the mark. Positive tracking (`0.01–0.10em`) because expanded forms need
  air, not the negative tracking of the placeholder set.
- **Inter** (body) — neutral, screen-optimized for dense coordinator UI and
  playbook body copy.

Fallback stack when self-hosting isn't set up yet:
`"Archivo Expanded", Archivo, "Arial Black", sans-serif` and
`Inter, system-ui, sans-serif`.

# Layout

- On-site canvas is fixed **16:9 landscape**, safe-area inset `spacing.xl`
  (40px) all edges; title block top, detail block bottom.
- Invite + playbook exports are **light-surface** (`playbook-page`): neutral
  background, primary-ink text — matches the press kit's light logo lockup.
- Single-column on-site; playbook uses one column with `spacing.lg` rhythm and
  a decision-log table per section.

# Elevation & Depth

- Surfaces step up by color (`surface` over `background`), not shadows —
  projector-safe.
- Interactive emphasis comes from the single tertiary accent, never elevation.

# Shapes

`sm` inline chips · `md` buttons and scan banners · `lg` cards · `xl` large
on-site panels. The brand mark's slash motif may be echoed as a thin diagonal
divider on slide titles — decorative use only, never on data UI.

# Components

- **button-primary** — the only high-emphasis action ("Generate slides",
  "Confirm venue", "Check in"). Signal-blue fill, dark text; hover flips to
  neutral fill (monochrome brand behavior) rather than a second color.
- **card-surface** — decision/option cards. Every research result the tool
  presents (venue option, audience estimate, slide draft) renders in one of
  these with *options + reasoning*, and a `button-primary` to confirm.
- **muted-text** — captions, metadata, "why we suggest this" reasoning lines.
- **scan-valid / scan-already / scan-tampered** — the three QR outcomes, 1:1
  with the signed-token validator, dark text on saturated fills.
- **slide-title-onsite / slide-body-onsite** — 16:9 display text blocks.
- **playbook-page / playbook-heading** — the exportable playbook document:
  light surface, ink text, Archivo Expanded headings, full-logo dark lockup in
  the header, symbol-only mark as a footer glyph.

## Image roles (press-kit asset contract)

Slide and playbook templates request images by **role**, never by filename:

| Role | Asset | Use |
|------|-------|-----|
| `logo-on-dark` | `Saronic_Logo_Full--Light.png` | On-site slide header/footer |
| `logo-on-light` | `Saronic_Logo_Full--Dark.png` | Playbook + invite header |
| `mark-on-dark` | `Saronic_Logo_Symbol--Light.png` | Favicons, QR badge corner |
| `mark-on-light` | `Saronic_Logo_Symbol--Dark.png` | Playbook footer glyph |
| `hero-16x9` | `SAR_Corsair_Hero.png` | On-site title-slide background (under a `#0C141B` 60% overlay so text stays AA) |
| `imagery-alt` | `Corsair-*.jpg`, `Marauder-0*.jpeg` | Section dividers, invite art |

# Do's and Don'ts

**Do**
- Keep the on-site canvas dark and high-contrast; it is projected, not browsed.
- Drive every color/type change through tokens — no hard-coded hex in components.
- Put a dark overlay behind any text placed on product imagery.
- Use uppercase Archivo Expanded for titles to echo the wordmark.
- Keep on-site body copy at `body-lg` or larger.

**Don't**
- Don't recolor, skew, or add effects to the logo — the mark is monochrome only
  (ink on light, light on ink).
- Don't introduce a second accent — tertiary blue is the sole interactive cue.
- Don't put white text on tertiary blue (AA failure); always `#0C141B`.
- Don't reuse warning/danger fills decoratively; they mean check-in states.
- Don't stretch the full wordmark; it ships at its correct 4.97:1 ratio.
