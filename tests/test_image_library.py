"""P5-1 — image library sourced from the company blog.

Pulls Saronic's own Medium articles and keeps their imagery as a reusable
library for the visuals composer, alongside coordinator uploads.

Three things this has to get right, all learned the hard way earlier:

* **The SSRF guard still applies.** A feed URL is a coordinator-supplied URL,
  and so is every image URL inside it. Both go through ``assert_fetchable``.
* **Provenance is recorded, not assumed.** Every asset carries its article
  title and URL, so the sidecar can say where a composited image came from.
  "It's our own blog" is true today and unverifiable in six months.
* **The 1600x900 minimum is enforced on real pixels**, not on the URL. Medium
  serves whatever width you ask for, so we ask for a big one and then check
  what actually arrived.
"""
from __future__ import annotations

import sqlite3

import pytest

from app.db import repository as repo, schema_sql_text as sql
from app.db.models import Event, LibraryImage
from app.features.image_library import (
    MIN_LIBRARY_SIZE,
    RSS_TEMPLATE,
    feed_url_for,
    parse_feed,
    upscale_medium_url,
)


@pytest.fixture()
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(sql.SCHEMA)
    return c


@pytest.fixture()
def event(conn) -> int:
    return repo.create_event(conn, Event(name="Fleet Week", city="Austin"))


_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title><![CDATA[Saronic Technologies - Medium]]></title>
  <item>
    <title><![CDATA[Marauder Enters On-Water Trials]]></title>
    <link>https://medium.com/saronic-technologies/marauder-trials-7bce1207cf24</link>
    <pubDate>Mon, 09 Jun 2025 12:00:00 GMT</pubDate>
    <content:encoded xmlns:content="http://purl.org/rss/1.0/modules/content/">
      &lt;img src="https://cdn-images-1.medium.com/max/1024/1*ABC.jpeg"&gt;
      &lt;img src="https://medium.com/_/stat?event=post.clientViewed&amp;postId=x"&gt;
    </content:encoded>
  </item>
  <item>
    <title><![CDATA[Selected for U.S. Navy MUSV Marketplace]]></title>
    <link>https://medium.com/saronic-technologies/musv-cbf32de05d95</link>
    <pubDate>Tue, 10 Jun 2025 12:00:00 GMT</pubDate>
    <content:encoded xmlns:content="http://purl.org/rss/1.0/modules/content/">
      &lt;img src="https://cdn-images-1.medium.com/max/1024/1*DEF.png"&gt;
    </content:encoded>
  </item>
</channel></rss>
"""


class TestFeedUrl:
    def test_a_publication_url_becomes_its_feed(self):
        assert feed_url_for("https://medium.com/saronic-technologies") == \
            RSS_TEMPLATE.format(publication="saronic-technologies")

    def test_a_trailing_slash_is_tolerated(self):
        assert "saronic-technologies" in \
            feed_url_for("https://medium.com/saronic-technologies/")

    def test_a_feed_url_is_left_alone(self):
        url = "https://medium.com/feed/saronic-technologies"
        assert feed_url_for(url) == url

    def test_a_non_medium_url_is_refused(self):
        """Scoped deliberately: a generic web crawler is a different feature."""
        with pytest.raises(ValueError, match="Medium"):
            feed_url_for("https://example.com/blog")


class TestParsing:
    def test_articles_are_found(self):
        assets = parse_feed(_FEED)
        assert len(assets) == 2

    def test_the_article_title_and_url_are_kept(self):
        asset = parse_feed(_FEED)[0]
        assert asset.article_title == "Marauder Enters On-Water Trials"
        assert "marauder-trials" in asset.article_url

    def test_tracking_pixels_are_discarded(self):
        """Medium embeds a 1x1 stat beacon in every post."""
        for asset in parse_feed(_FEED):
            assert "stat?event" not in asset.source_url

    def test_only_the_lead_image_is_taken_per_article(self):
        assert len(parse_feed(_FEED)) == 2

    def test_the_url_is_upscaled_before_download(self):
        """Medium serves whatever width you ask for; 1024 is below our floor."""
        assert "/max/2000/" in parse_feed(_FEED)[0].source_url

    def test_an_article_with_no_image_is_skipped_not_broken(self):
        feed = _FEED.replace(
            '&lt;img src="https://cdn-images-1.medium.com/max/1024/1*DEF.png"&gt;', "")
        assert len(parse_feed(feed)) == 1

    def test_junk_input_yields_nothing_rather_than_raising(self):
        assert parse_feed("<html>not a feed</html>") == []


class TestUpscaling:
    def test_a_medium_cdn_url_is_widened(self):
        assert upscale_medium_url(
            "https://cdn-images-1.medium.com/max/1024/1*ABC.jpeg") == \
            "https://cdn-images-1.medium.com/max/2000/1*ABC.jpeg"

    def test_a_url_without_a_max_segment_is_untouched(self):
        url = "https://example.com/photo.jpg"
        assert upscale_medium_url(url) == url


class TestPersistence:
    def test_an_asset_round_trips(self, conn, event):
        repo.add_library_image(conn, LibraryImage(
            event_id=event, path="generated/library/1.png",
            source_url="https://cdn-images-1.medium.com/max/2000/1*ABC.jpeg",
            article_title="Marauder Enters On-Water Trials",
            article_url="https://medium.com/saronic-technologies/x",
            origin="blog", width=2000, height=1333))
        assets = repo.library_images(conn, event)
        assert len(assets) == 1
        assert assets[0].origin == "blog"
        assert assets[0].article_title.startswith("Marauder")

    def test_the_same_source_is_not_stored_twice(self, conn, event):
        """Re-importing the feed must not multiply the library."""
        for _ in range(2):
            repo.add_library_image(conn, LibraryImage(
                event_id=event, path="generated/library/1.png",
                source_url="https://cdn.example/1.jpeg",
                article_title="X", article_url="https://x", origin="blog",
                width=2000, height=1333))
        assert len(repo.library_images(conn, event)) == 1

    def test_an_asset_can_be_removed(self, conn, event):
        aid = repo.add_library_image(conn, LibraryImage(
            event_id=event, path="p.png", source_url="https://cdn.example/1.jpeg",
            article_title="X", article_url="https://x", origin="blog",
            width=2000, height=1333))
        repo.delete_library_image(conn, aid)
        assert repo.library_images(conn, event) == []

    def test_uploads_and_blog_images_share_the_library(self, conn, event):
        repo.add_library_image(conn, LibraryImage(
            event_id=event, path="a.png", source_url="upload:city.jpg",
            origin="uploaded", width=1920, height=1080))
        repo.add_library_image(conn, LibraryImage(
            event_id=event, path="b.png", source_url="https://cdn.example/1.jpeg",
            article_title="X", article_url="https://x", origin="blog",
            width=2000, height=1333))
        assert {a.origin for a in repo.library_images(conn, event)} == \
            {"uploaded", "blog"}


class TestSizeFloor:
    def test_the_floor_matches_the_composer(self):
        from app.features.visuals import MIN_UPLOAD

        assert MIN_LIBRARY_SIZE == MIN_UPLOAD


class TestLibraryUi:
    """Populated-branch coverage — an empty library proves nothing."""

    @pytest.fixture()
    def client(self, tmp_path):
        from fastapi.testclient import TestClient
        from app.main import create_app
        return TestClient(create_app(db_path=str(tmp_path / "t.db")))

    @pytest.fixture()
    def eid(self, client):
        r = client.post("/events", data={"name": "Fleet Week", "city": "Austin"},
                        follow_redirects=False)
        return int(r.headers["location"].rstrip("/").split("/")[2])

    def _seed(self, eid, tmp_path, title="Marauder Enters On-Water Trials"):
        import sqlite3

        from PIL import Image

        import app.main as main_mod

        path = tmp_path / "blog-1.png"
        Image.new("RGB", (2000, 1000), (40, 60, 80)).save(path)
        conn = sqlite3.connect(main_mod.CURRENT_DB)
        conn.row_factory = sqlite3.Row
        try:
            image_id = repo.add_library_image(conn, LibraryImage(
                event_id=eid, path=str(path),
                source_url="https://cdn-images-1.medium.com/max/2000/1*ABC.jpeg",
                article_title=title,
                article_url="https://medium.com/saronic-technologies/x",
                origin="blog", width=2000, height=1000))
            conn.commit()
        finally:
            conn.close()
        return image_id

    def test_the_visuals_page_offers_the_import(self, client, eid):
        page = client.get(f"/events/{eid}/visuals").text
        assert "Import from the blog" in page
        assert "medium.com/saronic-technologies" in page

    def test_a_populated_library_renders_with_attribution(self, client, eid,
                                                          tmp_path):
        self._seed(eid, tmp_path)
        page = client.get(f"/events/{eid}/visuals").text
        assert "library-tile" in page
        assert "Marauder Enters On-Water Trials" in page
        assert "2000" in page

    def test_a_library_thumbnail_is_served(self, client, eid, tmp_path):
        image_id = self._seed(eid, tmp_path)
        r = client.get(f"/events/{eid}/visuals/library/{image_id}.png")
        assert r.status_code == 200
        assert r.headers["content-type"] == "image/png"

    def test_using_a_library_image_makes_it_the_backdrop(self, client, eid,
                                                         tmp_path):
        image_id = self._seed(eid, tmp_path)
        client.post(f"/events/{eid}/visuals/library/{image_id}/use",
                    follow_redirects=False)
        page = client.get(f"/events/{eid}/visuals").text
        # Variants A and C need a city image; they appear once one is chosen.
        assert "Variant A" in page

    def test_a_library_image_can_be_removed(self, client, eid, tmp_path):
        image_id = self._seed(eid, tmp_path)
        client.post(f"/events/{eid}/visuals/library/{image_id}/delete",
                    follow_redirects=False)
        assert "library-tile" not in client.get(f"/events/{eid}/visuals").text

    def test_a_non_medium_url_is_refused_with_a_reason(self, client, eid):
        page = client.post(f"/events/{eid}/visuals/library/import",
                           data={"blog_url": "https://example.com/blog"}).text
        assert "Medium" in page

    def test_an_ssrf_attempt_on_the_feed_is_refused(self, client, eid):
        page = client.post(f"/events/{eid}/visuals/library/import",
                           data={"blog_url": "http://169.254.169.254/"}).text
        assert "Medium" in page or "safely" in page.lower()

    def test_another_events_image_is_not_served(self, client, eid, tmp_path):
        image_id = self._seed(eid, tmp_path)
        r = client.post("/events", data={"name": "Other", "city": "Austin"},
                        follow_redirects=False)
        other = int(r.headers["location"].rstrip("/").split("/")[2])
        assert client.get(
            f"/events/{other}/visuals/library/{image_id}.png").status_code == 404

    def test_the_sidecar_records_blog_provenance_not_uploaded(self, client, eid,
                                                              tmp_path):
        """Designer's licensing note: provenance must survive into the export.

        Copying a library image to city.png made every composite claim
        base_source='uploaded', so an exported asset could not say which
        article its backdrop came from.
        """
        import json

        image_id = self._seed(eid, tmp_path, title="Mirage Hits the Water")
        client.post(f"/events/{eid}/visuals/library/{image_id}/use",
                    follow_redirects=False)
        client.get(f"/events/{eid}/visuals")
        import glob
        import os

        # Read the NEWEST sidecar for this event. Renders land in the repo's
        # generated/ directory, so an earlier run's file can shadow this one and
        # make the test pass or fail for reasons unrelated to the code.
        sidecars = glob.glob(f"generated/visuals/{eid}/**/A-16x9.json",
                             recursive=True)
        assert sidecars, "variant A should have rendered"
        newest = max(sidecars, key=os.path.getmtime)
        data = json.loads(open(newest).read())
        assert data["base_source"] == "blog"
        assert "Mirage" in data["base_attribution"]
