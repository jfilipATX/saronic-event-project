"""P6-4 — example imagery for the demo and quick starts.

The repo ships a small set of brand-correct (monochrome, Saronic-style) example
images under ``assets/images/example/``. They are OUR assets — not stock — so
using them as backdrops never trips the "brand falls through to stock" rule.
``seed_example_images`` copies them into an event's library so a freshly loaded
demo (or a coordinator who wants a starting point) has something to compose
against without leaving the app or hitting the network.
"""
from __future__ import annotations

import os
import shutil

from app.db import repository as repo
from app.db.models import LibraryImage

_EXAMPLE_DIR = os.path.join(
    os.path.dirname(__file__), "..", "..", "assets", "images", "example"
)


def example_image_dir() -> str:
    return os.path.abspath(_EXAMPLE_DIR)


def seed_example_images(conn, event_id: int, visuals_dir: str) -> int:
    """Copy the bundled example images into an event's library.

    Idempotent: an image whose source path is already recorded is skipped, so
    re-seeding (or loading the demo twice) does not duplicate entries. Returns
    the number of new images added.
    """
    src = example_image_dir()
    if not os.path.isdir(src):
        return 0
    dest_dir = os.path.join(visuals_dir, "library")
    os.makedirs(dest_dir, exist_ok=True)
    existing = {im.source_url for im in repo.library_images(conn, event_id)}
    added = 0
    for name in sorted(os.listdir(src)):
        if not name.lower().endswith((".png", ".jpg", ".jpeg")):
            continue
        src_path = os.path.join(src, name)
        rel = f"example/{name}"
        if rel in existing:
            continue
        dest = os.path.join(dest_dir, f"example-{name}")
        shutil.copyfile(src_path, dest)
        repo.add_library_image(conn, LibraryImage(
            event_id=event_id, path=dest, source_url=rel,
            article_title="Example image (bundled)", article_url="",
            origin="example", width=0, height=0, backdrop_kind="photo",
        ))
        added += 1
    conn.commit()
    return added
