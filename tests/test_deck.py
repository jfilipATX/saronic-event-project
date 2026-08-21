"""T11 — deck composition.

The deck is a *decision output*, not a separate artifact: it is built from the
playbook the coordinator assembled, so it can never disagree with the plan.

Slides request images by role (T10), so the deck inherits brand-first resolution
and keeps rendering when stock imagery is unavailable.
"""
from __future__ import annotations

import sqlite3

import pytest

from app.db import repository as repo, schema_sql_text as sql
from app.features.deck import build_deck, render_deck_markdown
from app.features.images import ImageResolver
from app.features.playbook import compose_playbook
from app.features.workflow import CoordinatorWorkflow


@pytest.fixture()
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.executescript(sql.SCHEMA)
    return c


@pytest.fixture()
def planned(conn):
    wf = CoordinatorWorkflow(conn)
    eid = wf.start_event(name="Saronic Fleet Week", city="Austin")
    wf.choose(eid, step="event_type", key="convention")
    wf.choose(eid, step="audience", key="conservative")
    wf.choose(eid, step="venue", key="austin-convention-center")
    return eid


@pytest.fixture()
def resolver() -> ImageResolver:
    return ImageResolver(stock_provider=None)  # offline: brand assets only


class TestDeckStructure:
    def test_deck_opens_with_a_title_slide(self, conn, planned, resolver):
        deck = build_deck(compose_playbook(conn, planned), resolver)
        assert deck.slides[0].kind == "title"
        assert "Saronic Fleet Week" in deck.slides[0].title

    def test_title_slide_uses_the_brand_hero_not_stock(self, conn, planned, resolver):
        deck = build_deck(compose_playbook(conn, planned), resolver)
        title = deck.slides[0]
        assert title.image_role == "hero-16x9"
        assert "Corsair" in title.image_url

    def test_every_settled_decision_becomes_a_slide(self, conn, planned, resolver):
        deck = build_deck(compose_playbook(conn, planned), resolver)
        steps = [s.step for s in deck.slides if s.kind == "decision"]
        assert steps == ["event_type", "audience", "venue"]

    def test_decision_slides_carry_the_reasoning(self, conn, planned, resolver):
        deck = build_deck(compose_playbook(conn, planned), resolver)
        venue = next(s for s in deck.slides if s.step == "venue")
        assert "headroom" in venue.body.lower() or "capacity" in venue.body.lower()

    def test_deck_closes_with_a_logo_slide(self, conn, planned, resolver):
        deck = build_deck(compose_playbook(conn, planned), resolver)
        assert deck.slides[-1].kind == "closing"
        assert deck.slides[-1].image_role == "logo-on-dark"


class TestDeckReflectsOpenQuestions:
    def test_pending_decisions_become_a_decisions_needed_slide(self, conn, resolver):
        wf = CoordinatorWorkflow(conn)
        eid = wf.start_event(name="E", city="Austin")
        wf.choose(eid, step="event_type", key="convention")
        deck = build_deck(compose_playbook(conn, eid), resolver)
        pending_slides = [s for s in deck.slides if s.kind == "open-questions"]
        assert len(pending_slides) == 1
        assert "audience" in pending_slides[0].body.lower()

    def test_complete_plan_has_no_open_questions_slide(self, conn, planned, resolver):
        deck = build_deck(compose_playbook(conn, planned), resolver)
        assert not [s for s in deck.slides if s.kind == "open-questions"]

    def test_deck_is_marked_draft_while_questions_remain(self, conn, resolver):
        wf = CoordinatorWorkflow(conn)
        eid = wf.start_event(name="E", city="Austin")
        deck = build_deck(compose_playbook(conn, eid), resolver)
        assert deck.is_draft is True

    def test_complete_plan_yields_a_final_deck(self, conn, planned, resolver):
        deck = build_deck(compose_playbook(conn, planned), resolver)
        assert deck.is_draft is False


class TestDeckSurvivesMissingStock:
    def test_deck_builds_with_no_stock_provider_at_all(self, conn, planned):
        deck = build_deck(compose_playbook(conn, planned), ImageResolver(None))
        assert all(s.image_url or s.image_role is None for s in deck.slides)

    def test_slides_needing_stock_degrade_without_breaking_the_deck(self, conn, planned):
        deck = build_deck(compose_playbook(conn, planned), ImageResolver(None))
        # No slide may be silently dropped just because an image was unavailable.
        assert len([s for s in deck.slides if s.kind == "decision"]) == 3


class TestRenderDeckMarkdown:
    def test_render_includes_titles_and_image_roles(self, conn, planned, resolver):
        md = render_deck_markdown(build_deck(compose_playbook(conn, planned), resolver))
        assert "Saronic Fleet Week" in md
        assert "hero-16x9" in md
        assert "Austin Convention Center" in md

    def test_draft_deck_is_labelled_draft_in_the_output(self, conn, resolver):
        wf = CoordinatorWorkflow(conn)
        eid = wf.start_event(name="E", city="Austin")
        md = render_deck_markdown(build_deck(compose_playbook(conn, eid), resolver))
        assert "DRAFT" in md
