# P2-4 — Event Visual Composer: Composition Spec

Composited visual assets (16:9) for kiosks, expo-booth displays, panel
pre-roll rotations, and social campaigns. Deterministic Pillow composition
from owned imagery only — no generative AI (brand-fidelity ruling, see room
decision log). Every rule below derives from DESIGN.md tokens; no new colors,
faces, or type.

## Canvas & grid

- **Canvas:** 1920×1080 (16:9). Also export 1080×1080 (social square) by
  center-cropping the composition with the text lockup re-anchored.
- **Grid:** 12-col, 80px outer margin (= `spacing.xl` × 2 at 1920 scale),
  40px gutter.
- **Safe zones:** text never within 80px of any edge; kiosk displays
  overscan.

## Layer stack (bottom → top)

1. **Base layer** — one of:
   - `city` — coordinator-uploaded city/venue photo (primary source)
   - `product` — press-kit Corsair/Marauder shot (`hero-16x9` or
     `imagery-alt` role)
   - `ink` — solid `#0C141B` (no-image fallback; always available)
2. **Ink overlay** — `#0C141B` at **60% opacity** over the full base when
   any text sits on imagery (the DESIGN.md AA rule). For layouts where text
   sits in a clear zone, a **gradient scrim** (0% → 85% ink, bottom-up) is
   allowed instead.
3. **Product cutout** (layouts B/C only) — press-kit vessel shot, masked,
   never mirrored, never recolored, max 55% canvas width, waterline-aligned
   to the lower third.
4. **Slash motif** — single diagonal rule (8px @1920), from the logo's
   slash angle (≈68° from horizontal), `#F2F6FA` at 100% or `#4C9FD8` at
   100%. One per composition maximum. Decorative only.
5. **Text lockup** — see below.
6. **Brand mark** — `logo-on-dark` (full lockup) bottom-left at 280px wide,
   or `mark-on-dark` (symbol) 96px when the full lockup would crowd.

## Text lockup

- **Headline:** Archivo Expanded 800, uppercase, `#F2F6FA`,
  tracking +0.01em. Template strings: "WELCOME TO {EVENT}",
  "{EVENT} · {CITY}", "SEE YOU AT {EVENT}". Max 2 lines; auto-shrink from
  128px until it fits, floor 88px — below that, drop to the short template.
- **Subline (optional):** Inter 400, 40px, `#9DA7AF`. Dates or booth
  number. One line only.
- **Eyebrow (optional):** Archivo Expanded 600, 28px, tracking +0.10em,
  `#4C9FD8`. e.g. "SARONIC AT".

## Layout variants (the rotation set)

Generate all four per event; coordinator deselects rather than configures.

- **A — Skyline hero.** City base full-bleed, 60% ink overlay, lockup
  centered-left on the grid, logo bottom-left. Requires uploaded city image;
  hidden when absent.
- **B — Fleet forward.** Product base full-bleed (press-kit), gradient
  scrim, lockup lower-left above the logo, slash rule separating headline
  from subline.
- **C — Collage.** City base at 100% left-cropped to cols 1–7, ink panel
  cols 8–12, product cutout bridging the seam (max 55% width), lockup in
  the ink panel, right-aligned. The "drone over skyline" ask. Requires city
  image; falls back to D when absent.
- **D — Ink minimal.** Solid ink base, symbol mark large (480px) as a
  watermark at 8% opacity upper-right, lockup centered, slash rule in
  signal blue. Always available — this is the zero-upload day-one variant.

## Image handling rules

- Uploaded city images: min 1600×900; below that, reject with a friendly
  "too small for a 16:9 display" note rather than upscaling.
- Cover-fit crops only (no distortion); crop anchor at horizon line when
  detectable (fallback: center).
- Press-kit product shots are never flipped, recolored, or overlaid with
  effects other than the ink scrim (logo rules extend to vessel imagery).
- EXIF orientation honored; EXIF GPS stripped on ingest (PII hygiene —
  uploads may come from a coordinator's phone).

## Output contract

- PNG, sRGB. 1920×1080 primary; 1080×1080 social derivative.
- Filename: `{event-slug}/{variant}-{16x9|1x1}.png` under `generated/visuals/`.
- Each render also writes a JSON sidecar: variant, base source
  (uploaded/press-kit/ink), template string used, font sizes after
  auto-shrink — the same attribution honesty as `copy_source`.

## UI (visuals screen)

- New "Visuals" stepper entry after Slides. Grid of rendered variants as
  `card-surface` tiles, each with variant name, base-source attribution
  line (muted), and a Download action (`btn-quiet`).
- Upload control at top: `form-row` with file input, helper text naming
  the min size and that venue photography beats stock.
- DRAFT rule inherited from slides: if decisions are open, tiles render
  with the `pending-note` treatment and a "decisions still open" line.

## Acceptance checks (testable)

1. All four variants render for an event with an uploaded city image; A/C
   are absent (not broken) without one; D always renders.
2. Headline text contrast ≥ 4.5:1 against its actual sampled background
   region (compute, don't assume).
3. No text within 80px of any edge at either aspect.
4. Product cutout ≤ 55% canvas width; never mirrored (assert transform
   matrix identity on that axis).
5. Sidecar JSON present and accurate for every PNG.
6. Uploaded EXIF GPS stripped (assert absent from saved file).
