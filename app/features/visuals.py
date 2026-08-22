"""Event visual composer — deterministic Pillow composition (P2-4).

Composited 16:9 assets for kiosks, booth displays and social, built only from
owned imagery: coordinator uploads and the Saronic press kit. No generative
imagery, per the brand-fidelity ruling — a model inventing hull details on a
defense product is a risk no review step fully closes.

The interesting engineering here is not the layout, it is **verification**. A
composition can satisfy every rule in this file and still be unreadable, so the
ink overlay is not a fixed 60%: it is computed against the actual sampled
luminance behind the headline and deepened until the contrast clears AA. Text
placement is likewise measured on the rendered result rather than assumed, and
reported so tests can assert on it.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

from PIL import Image, ImageDraw, ImageFont

from app.features import images

CANVAS_16X9: Tuple[int, int] = (1920, 1080)
CANVAS_1X1: Tuple[int, int] = (1080, 1080)

#: Text never within this many pixels of an edge — kiosk displays overscan.
SAFE_MARGIN = 80

VARIANTS = ("A", "B", "C", "D")

INK = (12, 20, 27)
NEUTRAL = (242, 246, 250)
STEEL = (157, 167, 175)
SIGNAL = (76, 159, 216)

HEADLINE_MAX = 128
HEADLINE_MIN = 88
SUBLINE_SIZE = 40
EYEBROW_SIZE = 28

MIN_UPLOAD = (1600, 900)

#: The brand faces, then progressively less-right fallbacks. Whichever is used
#: is recorded in the sidecar: claiming brand type we did not have would be the
#: same dishonesty the copy_source badge exists to prevent.
_HEADLINE_FONTS = (
    ("Archivo Expanded", "ArchivoExpanded-Bold.ttf", True),
    ("Archivo", "Archivo-Bold.ttf", False),
    ("DejaVu Sans Bold", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", False),
)
_BODY_FONTS = (
    ("Inter", "Inter-Regular.ttf", True),
    ("DejaVu Sans", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", False),
)

_ASSETS = os.path.normpath(
    os.path.join(os.path.dirname(os.path.dirname(__file__)), "..", "assets"))

#: Re-exported from images.py rather than derived again. Deriving it separately
#: meant SARONIC_PRESS_KIT redirected slide imagery but NOT composites, so a
#: client checkout would silently ship the bundled vessel on a booth display.
#: Third instance of this shape in the codebase; images.PRESS_KIT_ROOT is the
#: single source.
PRESS_KIT_ROOT = images.PRESS_KIT_ROOT

_BRAND_DIR = os.path.normpath(
    os.path.join(os.path.dirname(os.path.dirname(__file__)), "ui", "static", "brand"))


@dataclass
class VisualRequest:
    event_name: str
    city: str = ""
    dates: str = ""
    city_image: Optional[str] = None
    out_dir: str = "generated/visuals"


@dataclass
class VisualResult:
    variant: str
    path_16x9: str = ""
    path_1x1: str = ""
    sidecar_path: str = ""
    base_source: str = "ink"
    template: str = ""
    headline_size: int = 0
    headline_luminance: float = 1.0
    headline_box: Tuple[int, int, int, int] = (0, 0, 0, 0)
    text_boxes: List[Tuple[int, int, int, int]] = field(default_factory=list)
    cutout_box: Optional[Tuple[int, int, int, int]] = None
    cutout_mirrored: bool = False
    font_family: str = ""
    font_is_brand: bool = False


# ── colour maths ─────────────────────────────────────────────────────────────


def _srgb_channel(value: float) -> float:
    value = value / 255.0
    return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4


def relative_luminance(rgb: Sequence[float]) -> float:
    r, g, b = (_srgb_channel(c) for c in rgb[:3])
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(lum_a: float, lum_b: float) -> float:
    lighter, darker = max(lum_a, lum_b), min(lum_a, lum_b)
    return (lighter + 0.05) / (darker + 0.05)


def sample_region_luminance(image: Image.Image, box: Tuple[int, int, int, int]) -> float:
    """Luminance of the pixels actually *behind* the text.

    Uses the median rather than the mean. Measuring a rendered composite means
    the sample contains the glyphs themselves, and light type over a dark scrim
    drags a mean upward until a genuinely readable slide measures as failing.
    Glyph strokes are a minority of the pixels in a text box, so the median
    reports the background — which is the thing the contrast rule is about.
    """
    left, top, right, bottom = (int(v) for v in box)
    left, top = max(0, left), max(0, top)
    right, bottom = min(image.width, right), min(image.height, bottom)
    if right <= left or bottom <= top:
        return 0.0
    region = image.convert("RGB").crop((left, top, right, bottom))
    region = region.resize((min(64, region.width), min(64, region.height)))
    values = sorted(relative_luminance(p) for p in region.getdata())
    return values[len(values) // 2]


# ── assets ───────────────────────────────────────────────────────────────────


def _load_font(candidates, size: int):
    for family, path, is_brand in candidates:
        for full in (path, os.path.join(_ASSETS, "fonts", path)):
            try:
                return ImageFont.truetype(full, size), family, is_brand
            except (OSError, ValueError):
                continue
    return ImageFont.load_default(), "PIL default", False


_PRODUCT_CACHE: dict = {}


def _press_kit_product() -> Optional[Image.Image]:
    """The press-kit vessel shot used as a base or cutout.

    Cached: decoding a 2MB PNG on every variant made rendering four variants
    noticeably slow, and the file never changes at runtime. Returns a copy so a
    caller resizing it cannot corrupt the cache.
    """
    if "image" not in _PRODUCT_CACHE:
        found = None
        for root, _dirs, files in os.walk(PRESS_KIT_ROOT):
            for name in sorted(files):
                if (name.lower().endswith((".jpg", ".jpeg", ".png"))
                        and "logo" not in name.lower()):
                    try:
                        found = Image.open(os.path.join(root, name)).convert("RGBA")
                        break
                    except OSError:
                        continue
            if found is not None:
                break
        _PRODUCT_CACHE["image"] = found
    cached = _PRODUCT_CACHE["image"]
    return cached.copy() if cached is not None else None


def _brand_mark(symbol: bool = False) -> Optional[Image.Image]:
    name = "mark-on-dark.png" if symbol else "logo-on-dark.png"
    path = os.path.join(_BRAND_DIR, name)
    try:
        return Image.open(path).convert("RGBA")
    except OSError:
        return None


def strip_exif(source: str, destination: str) -> str:
    """Re-encode an upload without metadata.

    Coordinator uploads come off phones, and phone photos carry GPS. Copying
    pixels into a fresh image is the reliable way to drop every tag rather than
    the ones we remembered to name.
    """
    with Image.open(source) as im:
        im = im.convert("RGB")
        clean = Image.new("RGB", im.size)
        clean.putdata(list(im.getdata()))
    os.makedirs(os.path.dirname(destination) or ".", exist_ok=True)
    clean.save(destination, format="PNG")
    return destination


def _cover_fit(image: Image.Image, size: Tuple[int, int]) -> Image.Image:
    """Fill the canvas without distorting — crop, never stretch."""
    target_ratio = size[0] / size[1]
    ratio = image.width / image.height
    if ratio > target_ratio:
        new_width = int(image.height * target_ratio)
        left = (image.width - new_width) // 2
        image = image.crop((left, 0, left + new_width, image.height))
    else:
        new_height = int(image.width / target_ratio)
        # Anchor slightly above centre: horizons and skylines live in the upper
        # half, and a centre crop tends to cut the tops off buildings.
        top = int((image.height - new_height) * 0.35)
        image = image.crop((0, top, image.width, top + new_height))
    return image.resize(size, Image.LANCZOS)


# ── text ─────────────────────────────────────────────────────────────────────


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "event"


def _wrap(text: str, font, draw, max_width: int, max_lines: int = 2) -> List[str]:
    words, lines, current = text.split(), [], ""
    for word in words:
        trial = f"{current} {word}".strip()
        if draw.textlength(trial, font=font) <= max_width or not current:
            current = trial
        else:
            lines.append(current)
            current = word
            if len(lines) == max_lines:
                break
    if current and len(lines) < max_lines:
        lines.append(current)
    return lines


def _fit_headline(draw, text: str, max_width: int, fonts):
    """Shrink until it fits; below the floor, fall back to a shorter string."""
    size = HEADLINE_MAX
    while size >= HEADLINE_MIN:
        font, family, is_brand = _load_font(fonts, size)
        lines = _wrap(text, font, draw, max_width)
        if all(draw.textlength(line, font=font) <= max_width for line in lines):
            joined = " ".join(lines).replace(" ", "")
            if joined and len(joined) >= len(text.replace(" ", "")) * 0.6:
                return font, lines, size, family, is_brand
        size -= 4
    font, family, is_brand = _load_font(fonts, HEADLINE_MIN)
    return font, _wrap(text, font, draw, max_width), HEADLINE_MIN, family, is_brand


def _headline_for(request: VisualRequest, variant: str) -> Tuple[str, str]:
    if variant == "C" and request.city:
        return "{EVENT} · {CITY}", f"{request.event_name} · {request.city}".upper()
    return "WELCOME TO {EVENT}", f"WELCOME TO {request.event_name}".upper()


# ── composition ──────────────────────────────────────────────────────────────


def _base_layer(request: VisualRequest, variant: str):
    """Returns (image, base_source) or raises if an upload is unusable."""
    if variant in ("A", "C"):
        if not request.city_image:
            return None, None
        with Image.open(request.city_image) as raw:
            if raw.width < MIN_UPLOAD[0] or raw.height < MIN_UPLOAD[1]:
                raise ValueError(
                    f"That image is too small for a 16:9 display "
                    f"({raw.width}x{raw.height}; needs at least "
                    f"{MIN_UPLOAD[0]}x{MIN_UPLOAD[1]})."
                )
            return _cover_fit(raw.convert("RGB"), CANVAS_16X9), "uploaded"
    if variant == "B":
        product = _press_kit_product()
        if product is not None:
            return _cover_fit(product.convert("RGB"), CANVAS_16X9), "press-kit"
        return Image.new("RGB", CANVAS_16X9, INK), "ink"
    return Image.new("RGB", CANVAS_16X9, INK), "ink"


def _apply_scrim(canvas: Image.Image, box, target_luminance: float) -> Image.Image:
    """Deepen the ink overlay until the headline clears AA where it actually sits.

    The spec's 60% is the starting point, not the answer: a bright upload needs
    more, and guessing is what produces unreadable kiosk slides.

    Only the headline region is composited while searching — the full canvas is
    composited once, at the chosen opacity. Compositing 1920x1080 five times per
    variant was most of the render cost.
    """
    left, top, right, bottom = (int(v) for v in box)
    region = canvas.convert("RGBA").crop((left, top, right, bottom))
    chosen = 0.92
    for opacity in (0.60, 0.70, 0.78, 0.85, 0.92):
        overlay = Image.new("RGBA", region.size, INK + (int(255 * opacity),))
        trial = Image.alpha_composite(region, overlay).convert("RGB")
        if contrast_ratio(target_luminance,
                          sample_region_luminance(trial, (0, 0) + trial.size)) >= 4.5:
            chosen = opacity
            break
    overlay = Image.new("RGBA", canvas.size, INK + (int(255 * chosen),))
    return Image.alpha_composite(canvas.convert("RGBA"), overlay).convert("RGB")


def render_variant(request: VisualRequest, variant: str) -> Optional[VisualResult]:
    """Compose one variant. Returns None when the variant needs an absent asset."""
    if variant not in VARIANTS:
        raise ValueError(f"Unknown variant {variant!r}.")

    base, base_source = _base_layer(request, variant)
    if base is None:
        return None

    canvas = base.copy()
    draw = ImageDraw.Draw(canvas)
    max_text_width = CANVAS_16X9[0] - (SAFE_MARGIN * 2)

    template, headline_text = _headline_for(request, variant)
    font, lines, size, family, is_brand = _fit_headline(
        draw, headline_text, max_text_width, _HEADLINE_FONTS)

    line_height = int(size * 1.12)
    block_height = line_height * len(lines)
    top = (CANVAS_16X9[1] - block_height) // 2
    if variant in ("B",):
        top = CANVAS_16X9[1] - SAFE_MARGIN - block_height - 140
    top = max(SAFE_MARGIN, min(top, CANVAS_16X9[1] - SAFE_MARGIN - block_height))

    headline_box = (SAFE_MARGIN, top, CANVAS_16X9[0] - SAFE_MARGIN,
                    top + block_height)
    headline_luminance = relative_luminance(NEUTRAL)

    cutout_box = None
    if variant in ("B", "C"):
        product = _press_kit_product()
        if product is not None:
            max_width = int(CANVAS_16X9[0] * 0.55)
            scale = min(max_width / product.width, (CANVAS_16X9[1] * 0.5) / product.height)
            sized = product.resize(
                (max(1, int(product.width * scale)), max(1, int(product.height * scale))),
                Image.LANCZOS)
            x = CANVAS_16X9[0] - sized.width - SAFE_MARGIN
            y = CANVAS_16X9[1] - sized.height - SAFE_MARGIN
            canvas.paste(sized, (x, y), sized)
            cutout_box = (x, y, x + sized.width, y + sized.height)
            draw = ImageDraw.Draw(canvas)

    # Scrim AFTER the cutout: the cutout is part of what sits behind the
    # headline, so measuring before pasting it measures the wrong picture.
    # Found by the computed-contrast test — B and C failed at 2.96:1 with the
    # scrim applied first, which is exactly the bug this check exists to catch.
    if base_source != "ink" or cutout_box is not None:
        canvas = _apply_scrim(canvas, headline_box, headline_luminance)
        draw = ImageDraw.Draw(canvas)

    text_boxes: List[Tuple[int, int, int, int]] = []
    y = top
    for line in lines:
        width = draw.textlength(line, font=font)
        draw.text((SAFE_MARGIN, y), line, font=font, fill=NEUTRAL)
        text_boxes.append((SAFE_MARGIN, y, int(SAFE_MARGIN + width), y + line_height))
        y += line_height

    if request.dates:
        sub_font, _, _ = _load_font(_BODY_FONTS, SUBLINE_SIZE)
        sub_y = min(y + 16, CANVAS_16X9[1] - SAFE_MARGIN - SUBLINE_SIZE)
        width = draw.textlength(request.dates, font=sub_font)
        draw.text((SAFE_MARGIN, sub_y), request.dates, font=sub_font, fill=STEEL)
        text_boxes.append((SAFE_MARGIN, sub_y, int(SAFE_MARGIN + width),
                           sub_y + SUBLINE_SIZE))

    mark = _brand_mark(symbol=(variant == "D"))
    if mark is not None:
        target_width = 280 if variant != "D" else 96
        scale = target_width / mark.width
        mark = mark.resize((target_width, max(1, int(mark.height * scale))), Image.LANCZOS)
        canvas.paste(mark, (SAFE_MARGIN, CANVAS_16X9[1] - SAFE_MARGIN - mark.height), mark)

    slug = _slug(request.event_name)
    out_dir = os.path.join(request.out_dir, slug)
    os.makedirs(out_dir, exist_ok=True)
    path_16x9 = os.path.join(out_dir, f"{variant}-16x9.png")
    canvas.save(path_16x9, format="PNG")

    square = _cover_fit(canvas, CANVAS_1X1)
    path_1x1 = os.path.join(out_dir, f"{variant}-1x1.png")
    square.save(path_1x1, format="PNG")

    result = VisualResult(
        variant=variant, path_16x9=path_16x9, path_1x1=path_1x1,
        base_source=base_source, template=template, headline_size=size,
        headline_luminance=headline_luminance, headline_box=headline_box,
        text_boxes=text_boxes, cutout_box=cutout_box, cutout_mirrored=False,
        font_family=family, font_is_brand=is_brand,
    )

    sidecar = os.path.join(out_dir, f"{variant}-16x9.json")
    with open(sidecar, "w") as fh:
        json.dump({
            "variant": variant,
            "base_source": base_source,
            "template": template,
            "headline_size": size,
            "font_family": family,
            "font_is_brand": is_brand,
            "canvas": list(CANVAS_16X9),
            "cutout_mirrored": False,
        }, fh, indent=2)
    result.sidecar_path = sidecar
    return result


def render_all(request: VisualRequest) -> List[VisualResult]:
    """Render every available variant. An unavailable one is absent, not broken."""
    results = []
    for variant in VARIANTS:
        try:
            result = render_variant(request, variant)
        except ValueError:
            continue
        if result is not None:
            results.append(result)
    return results
