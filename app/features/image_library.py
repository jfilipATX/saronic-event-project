"""Image library sourced from the company blog (P5-1).

Saronic publishes product and announcement imagery on its own Medium
publication. Those are the right images for event collateral — real vessels,
real trials, owned outright — and pulling them in beats a coordinator
right-clicking through a blog.

Three constraints carried over from earlier features rather than rediscovered:

* **The SSRF guard applies to every hop.** A feed URL is coordinator-supplied,
  and so is every image URL inside it. Both go through ``assert_fetchable``,
  because "it's our own blog" is a claim about the string someone typed.
* **Provenance is stored, never inferred.** Each asset keeps its article title
  and URL so an exported composite can say where its base layer came from.
  Ownership is obvious today and unverifiable in six months.
* **The size floor is checked on real pixels.** Medium's CDN serves whatever
  width the URL asks for, so we ask for 2000 and then measure what arrived —
  the URL is a request, not a guarantee.

Scoped deliberately to Medium. A general blog crawler is a different feature
with a different risk profile, and pretending otherwise would mean shipping a
web scraper behind a button labelled "import from our blog".
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from html import unescape
from typing import List, Optional
from urllib.request import Request, urlopen

from app.features.url_guard import assert_fetchable
from app.features.visuals import MIN_UPLOAD, strip_exif

RSS_TEMPLATE = "https://medium.com/feed/{publication}"

#: Same floor as a coordinator upload — a blog image that cannot fill a 16:9
#: display is no more usable than a small photo.
MIN_LIBRARY_SIZE = MIN_UPLOAD

#: Medium serves arbitrary widths from /max/{n}/. 1024 (what the feed embeds)
#: is below our floor; 2000 clears it with room for a cover-fit crop.
_TARGET_WIDTH = 2000

#: Medium embeds a 1x1 tracking beacon in every post's content.
_BEACON = "stat?event"

#: Downloads are bounded: an image library is not a reason to stream 200MB.
MAX_IMAGE_BYTES = 12 * 1024 * 1024

_UA = ("Mozilla/5.0 (compatible; SaronicEventTool/1.0; "
       "+https://github.com/jfilipATX/saronic-event-project)")


@dataclass
class FeedAsset:
    source_url: str
    article_title: str
    article_url: str


def feed_url_for(url: str) -> str:
    """Turn a publication URL into its RSS feed.

    Medium returns 403 to a plain GET of the HTML page but serves the feed
    happily, so the feed is the supported interface rather than a workaround.
    """
    text = (url or "").strip().rstrip("/")
    if "medium.com" not in text:
        raise ValueError(
            "This imports from a Medium publication — paste the publication "
            "URL, e.g. https://medium.com/saronic-technologies."
        )
    if "/feed/" in text:
        return text
    publication = text.rsplit("/", 1)[-1]
    if not publication:
        raise ValueError("That Medium URL has no publication name in it.")
    return RSS_TEMPLATE.format(publication=publication)


def upscale_medium_url(url: str) -> str:
    """Ask the CDN for a usable width. Non-Medium URLs pass through."""
    return re.sub(r"/max/\d+/", f"/max/{_TARGET_WIDTH}/", url)


def parse_feed(raw: str) -> List[FeedAsset]:
    """Extract one lead image per article. Returns [] on junk rather than raising."""
    assets: List[FeedAsset] = []
    for item in re.findall(r"<item>(.*?)</item>", raw or "", re.S):
        title = re.search(r"<title><!\[CDATA\[(.*?)\]\]></title>", item, re.S)
        link = re.search(r"<link>(.*?)</link>", item, re.S)
        if not link:
            continue
        images = [
            src for src in re.findall(r'<img[^>]+src="([^"]+)"', unescape(item))
            if _BEACON not in src
        ]
        if not images:
            continue
        # The lead image only: later images in a Medium post are usually
        # diagrams and headshots, which make poor event backdrops.
        assets.append(FeedAsset(
            source_url=upscale_medium_url(images[0]),
            article_title=(title.group(1).strip() if title else "Untitled"),
            article_url=link.group(1).strip(),
        ))
    return assets


def fetch_image(url: str, destination: str, resolver=None) -> tuple[int, int]:
    """Download one image through the SSRF guard, strip metadata, measure it.

    Returns (width, height). Raises ValueError if the real pixels fall below the
    floor — the URL asked for 2000px, but only the file can confirm it.
    """
    assert_fetchable(url, resolver=resolver)
    request = Request(url, headers={"User-Agent": _UA})
    with urlopen(request, timeout=30) as response:  # noqa: S310 - guarded above
        payload = response.read(MAX_IMAGE_BYTES + 1)
    if len(payload) > MAX_IMAGE_BYTES:
        raise ValueError("That image is too large to import.")

    os.makedirs(os.path.dirname(destination) or ".", exist_ok=True)
    staging = destination + ".incoming"
    with open(staging, "wb") as handle:
        handle.write(payload)
    try:
        # Same hygiene as an upload: re-encode pixels into a fresh image, which
        # drops every tag rather than the ones we remembered to name.
        strip_exif(staging, destination)
    finally:
        if os.path.exists(staging):
            os.remove(staging)

    from PIL import Image

    with Image.open(destination) as image:
        width, height = image.size
    if width < MIN_LIBRARY_SIZE[0] or height < MIN_LIBRARY_SIZE[1]:
        os.remove(destination)
        raise ValueError(
            f"That image is too small for a 16:9 display ({width}x{height}; "
            f"needs at least {MIN_LIBRARY_SIZE[0]}x{MIN_LIBRARY_SIZE[1]})."
        )
    return width, height


def fetch_feed(url: str, resolver=None) -> str:
    """Retrieve the RSS feed itself, through the same guard."""
    feed = feed_url_for(url)
    assert_fetchable(feed, resolver=resolver)
    request = Request(feed, headers={"User-Agent": _UA})
    with urlopen(request, timeout=30) as response:  # noqa: S310 - guarded above
        return response.read(4 * 1024 * 1024).decode("utf-8", errors="replace")
