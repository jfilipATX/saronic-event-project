"""P6-4 / P7-6 — example imagery for the demo and quick starts.

The repo ships a small set of brand-correct (monochrome, Saronic-style) example
images under ``assets/images/example/`` (abstract venue/city placeholders, used
as backdrop layers) PLUS the real Saronic press-kit product shots under
``assets/press-kit/Images/`` (actual hardware — e.g. the Corsair vessel). Both
are OUR assets, not stock, so using them as backdrops never trips the "brand
falls through to stock" rule. ``seed_example_images`` copies them into an
event's library as origin='example', each with a human-readable caption so a
coordinator understands what the tile is and where to use it.
"""
from __future__ import annotations

import os
import shutil

from app.db import repository as repo
from app.db.models import LibraryImage

_EXAMPLE_DIR = os.path.join(
    os.path.dirname(__file__), "..", "..", "assets", "images", "example"
)
_PRESSKIT_DIR = os.path.join(
    os.path.dirname(__file__), "..", "..", "assets", "press-kit", "Images"
)

#: Bundled abstract placeholders -> coordinator-facing caption (what it is,
#: where it fits). These are synthetic venue/city layers, not product shots.
_EXAMPLE_CAPTIONS = {
    "expo-hall.png": "Synthetic expo-hall layout — venue floor backdrop",
    "vessel.png": "Synthetic vessel silhouette — waterfront/maritime backdrop",
    "panel.png": "Synthetic panel stage — speaker/panel backdrop",
    "kiosk.png": "Synthetic check-in kiosk — entrance/desk backdrop",
}

#: Real Saronic product shots from the press kit (actual hardware), copied in
#: as a separate captioned set so the demo shows genuine product imagery too.
_PRESSKIT_CAPTIONS = {
    "Corsair/SAR_Corsair_Hero.png": "Saronic Corsair — real product hero shot",
}


def example_image_dir() -> str:
    return os.path.abspath(_EXAMPLE_DIR)


def seed_example_images(conn, event_id: int, visuals_dir: str) -> int:
    """Copy the bundled example + real press-kit images into an event's library.

    Idempotent: an image whose source path is already recorded is skipped, so
    re-seeding (or loading the demo twice) does not duplicate entries. Returns
    the number of new images added.
    """
    dest_dir = os.path.join(visuals_dir, "library")
    os.makedirs(dest_dir, exist_ok=True)
    existing = {im.source_url for im in repo.library_images(conn, event_id)}
    added = 0

    def _copy(src_path, rel, caption):
        nonlocal added
        if rel in existing:
            return
        dest = os.path.join(dest_dir, f"example-{os.path.basename(src_path)}")
        shutil.copyfile(src_path, dest)
        repo.add_library_image(conn, LibraryImage(
            event_id=event_id, path=dest, source_url=rel,
            article_title="Example image (bundled)", article_url="",
            origin="example", width=0, height=0, backdrop_kind="photo",
            caption=caption,
        ))
        added += 1

    if os.path.isdir(_EXAMPLE_DIR):
        for name in sorted(os.listdir(_EXAMPLE_DIR)):
            if not name.lower().endswith((".png", ".jpg", ".jpeg")):
                continue
            _copy(os.path.join(_EXAMPLE_DIR, name), f"example/{name}",
                  _EXAMPLE_CAPTIONS.get(name, "Example backdrop"))

    if os.path.isdir(_PRESSKIT_DIR):
        for root, _dirs, files in os.walk(_PRESSKIT_DIR):
            for name in sorted(files):
                if not name.lower().endswith((".png", ".jpg", ".jpeg")):
                    continue
                relpath = os.path.relpath(os.path.join(root, name), _PRESSKIT_DIR)
                _copy(os.path.join(root, name), f"example/{relpath}",
                      _PRESSKIT_CAPTIONS.get(relpath, "Saronic product image"))

    conn.commit()
    return added
