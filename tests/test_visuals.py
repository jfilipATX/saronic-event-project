"""P2-4 — event visual composer (Pillow composition from owned imagery).

Tests written against the acceptance checks in
``docs/p2-4-visual-composer-spec.md``. The ones that matter most are the
*computed* checks: contrast is sampled from the actual rendered pixels behind
the text rather than assumed from the design, and safe zones are measured on the
output rather than trusted from the layout code. A composition can satisfy every
rule in the source and still be unreadable.
"""
from __future__ import annotations

import json

import pytest
from PIL import Image

from app.features.visuals import (
    CANVAS_16X9,
    CANVAS_1X1,
    SAFE_MARGIN,
    VARIANTS,
    VisualRequest,
    contrast_ratio,
    render_variant,
    render_all,
    sample_region_luminance,
    strip_exif,
)


def _city_image(tmp_path, size=(1920, 1080), color=(90, 110, 130)):
    path = tmp_path / "city.png"
    Image.new("RGB", size, color).save(path)
    return str(path)


@pytest.fixture()
def request_with_city(tmp_path):
    return VisualRequest(
        event_name="Saronic Fleet Week 2026",
        city="Rotterdam",
        dates="14-16 March 2026",
        city_image=_city_image(tmp_path),
        out_dir=str(tmp_path / "out"),
    )


@pytest.fixture()
def request_no_city(tmp_path):
    return VisualRequest(
        event_name="Saronic Fleet Week 2026",
        city="Rotterdam",
        out_dir=str(tmp_path / "out"),
    )


class TestVariantAvailability:
    """Check 1 — A/C need a city image; D always renders."""

    def test_all_four_render_with_a_city_image(self, request_with_city):
        results = render_all(request_with_city)
        assert {r.variant for r in results} == set(VARIANTS)

    def test_a_and_c_are_absent_not_broken_without_a_city_image(self, request_no_city):
        variants = {r.variant for r in render_all(request_no_city)}
        assert "A" not in variants and "C" not in variants
        assert variants  # something still rendered

    def test_d_always_renders(self, request_no_city):
        assert "D" in {r.variant for r in render_all(request_no_city)}

    def test_b_renders_without_an_upload_using_press_kit_imagery(self, request_no_city):
        assert "B" in {r.variant for r in render_all(request_no_city)}

    def test_every_render_produces_a_real_png(self, request_with_city):
        for result in render_all(request_with_city):
            with Image.open(result.path_16x9) as im:
                assert im.format == "PNG"
                assert im.size == CANVAS_16X9

    def test_the_social_square_is_also_produced(self, request_with_city):
        for result in render_all(request_with_city):
            with Image.open(result.path_1x1) as im:
                assert im.size == CANVAS_1X1


class TestComputedContrast:
    """Check 2 — sample the ACTUAL pixels behind the headline."""

    def test_contrast_ratio_maths_is_right(self):
        assert contrast_ratio(1.0, 0.0) == pytest.approx(21.0, abs=0.01)
        assert contrast_ratio(0.0, 0.0) == pytest.approx(1.0, abs=0.01)

    @pytest.mark.parametrize("variant", VARIANTS)
    def test_headline_contrast_is_at_least_aa(self, request_with_city, variant):
        result = render_variant(request_with_city, variant)
        if result is None:
            pytest.skip(f"variant {variant} not available")
        with Image.open(result.path_16x9) as im:
            bg = sample_region_luminance(im, result.headline_box)
        ratio = contrast_ratio(result.headline_luminance, bg)
        assert ratio >= 4.5, f"variant {variant}: {ratio:.2f}:1 behind the headline"

    def test_a_bright_upload_still_yields_readable_text(self, tmp_path):
        """A white-sky photo is the case that breaks a fixed overlay."""
        req = VisualRequest(
            event_name="Bright Day", city="Austin",
            city_image=_city_image(tmp_path, color=(250, 250, 250)),
            out_dir=str(tmp_path / "out"))
        result = render_variant(req, "A")
        with Image.open(result.path_16x9) as im:
            bg = sample_region_luminance(im, result.headline_box)
        assert contrast_ratio(result.headline_luminance, bg) >= 4.5


class TestSafeZones:
    """Check 3 — no text within 80px of any edge, at either aspect."""

    @pytest.mark.parametrize("variant", VARIANTS)
    def test_text_respects_the_safe_margin(self, request_with_city, variant):
        result = render_variant(request_with_city, variant)
        if result is None:
            pytest.skip(f"variant {variant} not available")
        for box in result.text_boxes:
            left, top, right, bottom = box
            assert left >= SAFE_MARGIN, f"{variant}: text {left}px from left"
            assert top >= SAFE_MARGIN, f"{variant}: text {top}px from top"
            assert right <= CANVAS_16X9[0] - SAFE_MARGIN
            assert bottom <= CANVAS_16X9[1] - SAFE_MARGIN

    def test_a_very_long_event_name_still_fits(self, tmp_path):
        req = VisualRequest(
            event_name="Saronic International Autonomous Maritime Systems "
                       "Exposition And Fleet Demonstration Week",
            city="Rotterdam", out_dir=str(tmp_path / "out"))
        result = render_variant(req, "D")
        for left, top, right, bottom in result.text_boxes:
            assert left >= SAFE_MARGIN and top >= SAFE_MARGIN
            assert right <= CANVAS_16X9[0] - SAFE_MARGIN
            assert bottom <= CANVAS_16X9[1] - SAFE_MARGIN

    def test_a_long_name_shrinks_the_headline(self, tmp_path):
        short = VisualRequest(event_name="Demo Day", city="Austin",
                              out_dir=str(tmp_path / "s"))
        long = VisualRequest(
            event_name="Saronic International Autonomous Maritime Systems Expo",
            city="Austin", out_dir=str(tmp_path / "l"))
        assert (render_variant(long, "D").headline_size
                < render_variant(short, "D").headline_size)


class TestProductCutoutRules:
    """Check 4 — never mirrored, never wider than 55% of the canvas."""

    def test_the_cutout_is_within_the_width_cap(self, request_with_city):
        for variant in ("B", "C"):
            result = render_variant(request_with_city, variant)
            if result is None or result.cutout_box is None:
                continue
            width = result.cutout_box[2] - result.cutout_box[0]
            assert width <= CANVAS_16X9[0] * 0.55

    def test_the_cutout_is_never_mirrored(self, request_with_city):
        for variant in ("B", "C"):
            result = render_variant(request_with_city, variant)
            if result is None:
                continue
            assert result.cutout_mirrored is False


class TestSidecar:
    """Check 5 — attribution honesty, same principle as copy_source."""

    def test_a_sidecar_exists_for_every_render(self, request_with_city):
        for result in render_all(request_with_city):
            assert result.sidecar_path
            with open(result.sidecar_path) as fh:
                json.load(fh)

    def test_the_sidecar_names_the_real_base_source(self, request_with_city):
        by_variant = {r.variant: r for r in render_all(request_with_city)}
        with open(by_variant["A"].sidecar_path) as fh:
            assert json.load(fh)["base_source"] == "uploaded"
        with open(by_variant["D"].sidecar_path) as fh:
            assert json.load(fh)["base_source"] == "ink"

    def test_the_sidecar_records_the_template_and_font_size(self, request_with_city):
        result = render_variant(request_with_city, "D")
        with open(result.sidecar_path) as fh:
            data = json.load(fh)
        assert data["template"]
        assert data["headline_size"] == result.headline_size

    def test_the_sidecar_is_honest_when_the_brand_font_is_missing(
            self, request_with_city):
        """If Archivo Expanded is unavailable we must say so, not imply brand
        type was used."""
        result = render_variant(request_with_city, "D")
        with open(result.sidecar_path) as fh:
            data = json.load(fh)
        assert "font_family" in data
        assert isinstance(data["font_is_brand"], bool)


class TestExifHygiene:
    """Check 6 — a coordinator's phone photo carries GPS."""

    def test_gps_is_stripped_from_a_saved_upload(self, tmp_path):
        from PIL import ExifTags

        source = tmp_path / "phone.jpg"
        im = Image.new("RGB", (1920, 1080), (100, 120, 140))
        exif = im.getexif()
        exif[0x0110] = "TestPhone"
        # A real GPS IFD, not a bare pointer: a pointer alone cannot be
        # serialised, and a fixture that cannot save proves nothing.
        gps = exif.get_ifd(ExifTags.IFD.GPSInfo)
        gps[1] = "N"
        gps[2] = (30.0, 15.0, 0.0)
        gps[3] = "W"
        gps[4] = (97.0, 44.0, 0.0)
        im.save(source, exif=exif)

        with Image.open(source) as check:
            assert check.getexif().get_ifd(ExifTags.IFD.GPSInfo), \
                "fixture failed to record GPS — the test would prove nothing"

        cleaned = strip_exif(str(source), str(tmp_path / "clean.png"))
        with Image.open(cleaned) as out:
            assert not out.getexif().get_ifd(ExifTags.IFD.GPSInfo)
            assert 0x0110 not in dict(out.getexif())

    def test_stripping_preserves_the_picture(self, tmp_path):
        source = tmp_path / "photo.png"
        Image.new("RGB", (1920, 1080), (10, 200, 30)).save(source)
        cleaned = strip_exif(str(source), str(tmp_path / "clean.png"))
        with Image.open(cleaned) as out:
            assert out.size == (1920, 1080)
            assert out.getpixel((5, 5))[1] > 150

    def test_an_undersized_upload_is_rejected_with_a_reason(self, tmp_path):
        small = _city_image(tmp_path, size=(800, 450))
        req = VisualRequest(event_name="E", city="Austin", city_image=small,
                            out_dir=str(tmp_path / "out"))
        with pytest.raises(ValueError, match="too small"):
            render_variant(req, "A")


class TestPressKitPathIsCanonical:
    """SARONIC_PRESS_KIT must redirect composites, not just slide imagery.

    Third instance of "helper module reimplements app config": visuals.py derived
    its own press-kit path, so pointing the env var at a real client checkout
    would correctly move slide imagery while silently leaving composites on the
    bundled copy — a mismatch nobody would notice until the wrong vessel shipped
    on a booth display.
    """

    def test_visuals_uses_the_same_root_as_images(self):
        from app.features import images, visuals

        assert visuals.PRESS_KIT_ROOT == images.PRESS_KIT_ROOT

    def test_the_env_var_redirects_composites(self, tmp_path, monkeypatch):
        """Reloading with the env var set must move the product source."""
        import importlib

        from app.features import images, visuals

        custom = tmp_path / "client-press-kit"
        (custom / "Images").mkdir(parents=True)
        Image.new("RGB", (1800, 900), (7, 9, 11)).save(custom / "Images" / "Boat.png")

        monkeypatch.setenv("SARONIC_PRESS_KIT", str(custom))
        importlib.reload(images)
        importlib.reload(visuals)
        try:
            assert visuals.PRESS_KIT_ROOT == str(custom)
            visuals._PRODUCT_CACHE.clear()
            product = visuals._press_kit_product()
            assert product is not None
            assert product.size == (1800, 900)
        finally:
            monkeypatch.delenv("SARONIC_PRESS_KIT", raising=False)
            importlib.reload(images)
            importlib.reload(visuals)
            visuals._PRODUCT_CACHE.clear()
