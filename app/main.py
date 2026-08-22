"""FastAPI application — the coordinator's workflow shell.

Thin by design: every route is a translation between HTTP and
``CoordinatorWorkflow``. No decision logic lives here, so the UI cannot drift
from the domain rules (stage-never-choose, revision invalidates downstream).

Templates come from the design system as-is; where their expected shape differs
from the domain objects, this module adapts in a view-model rather than bending
either side to match.
"""
from __future__ import annotations

import os
import secrets
import sqlite3
from dataclasses import dataclass
from typing import List, Optional

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.db import repository as repo, schema_sql_text as sql
from app.db.models import Attendee, EventVariable
from app.features.deck import build_deck, render_deck_markdown
from app.features.event_facts import build_fact_options, extract_facts
from app.features.images import ImageResolver
from app.features.playbook import STEP_TITLES, compose_playbook, render_markdown
from app.features.roster_import import (
    MAPPABLE_FIELDS,
    apply_roster,
    preview_csv,
)
from app.features.url_fetch import fetch_url
from app.features.url_guard import UnsafeUrlError, assert_fetchable
from app.features.qr_checkin import (
    STATE_ALREADY,
    STATE_TAMPERED,
    STATE_VALID,
    check_in,
    mint_code,
    self_check_in,
)
from app.features.workflow import CHAIN, CoordinatorWorkflow

_UI_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui")

#: Stepper labels for the nav. Chain steps first, then the derived views.
_NAV = (
    [(key, STEP_TITLES.get(key, key)) for key in CHAIN]
    + [("slides", "Slides"), ("checkin", "Check-in"), ("playbook", "Playbook")]
)

#: Set by create_app so helpers/tests can reach the active database.
CURRENT_DB = "events.db"


def _scrape_client():
    """Claude client for URL extraction, or None when real Claude is off.

    A separate seam from the deck's client so tests can stub extraction without
    touching slide copy, and so mock mode never fabricates event facts.
    """
    from app.claude.client import get_client
    from app.config import load_config

    cfg = load_config()
    return get_client(cfg) if cfg.claude_enabled else None


def _signing_secret() -> str:
    """HMAC secret for check-in tokens.

    Server-side only and never rendered. A dev fallback keeps the prototype
    runnable without configuration, but it is derived per-process so tokens
    minted in one run cannot be replayed against another.
    """
    from app.config import CONFIG

    if CONFIG.event_signing_secret:
        return CONFIG.event_signing_secret
    global _DEV_SECRET
    if _DEV_SECRET is None:
        _DEV_SECRET = secrets.token_hex(32)
    return _DEV_SECRET


_DEV_SECRET: Optional[str] = None


def issue_invite(db_path: str, event_id: int, full_name: str,
                 email: Optional[str] = None) -> str:
    """Mint a signed credential for an invitee and store the attendee row.

    Exposed at module level because inviting people is a pre-event admin action,
    not part of the coordinator's decision chain.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(sql.SCHEMA)
        placeholder = repo.add_attendee(conn, Attendee(
            event_id=event_id, full_name=full_name, email=email))
        code = mint_code(_signing_secret(), invite_id=placeholder)
        conn.execute("UPDATE attendees SET checkin_code=? WHERE id=?",
                     (code, placeholder))
        conn.commit()
        return code
    finally:
        conn.close()


@dataclass
class NavStep:
    key: str
    label: str
    url: str
    state: str  # active | done | pending | todo


@dataclass
class PlaybookSectionVM:
    """Adapter: the playbook template asks for ``.decision``/``.rejected``/``.url``."""

    title: str
    decision: object
    rejected: list
    url: str


def create_app(db_path: Optional[str] = None) -> FastAPI:
    """Build the app.

    ``db_path`` defaults to ``$DB_PATH`` (then ``events.db``) rather than a bare
    literal: uvicorn's ``--factory`` mode calls this with no arguments, so a
    default baked in at the call site would silently ignore the environment and
    quietly write to a different database than the rest of the tooling.
    """
    db_path = db_path or os.environ.get("DB_PATH", "events.db")
    global CURRENT_DB
    CURRENT_DB = db_path
    app = FastAPI(title="Saronic Event Tool")
    app.mount("/static", StaticFiles(directory=os.path.join(_UI_DIR, "static")),
              name="static")
    templates = Jinja2Templates(directory=os.path.join(_UI_DIR, "templates"))

    def connect() -> sqlite3.Connection:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.executescript(sql.SCHEMA)
        # CREATE TABLE IF NOT EXISTS does not add columns to a database that
        # already exists, so an events.db created before a schema change would
        # otherwise 500 on the first query naming a new column. Idempotent.
        repo.apply_migrations(conn)
        conn.commit()
        return conn

    def load_event(conn, event_id: int):
        event = repo.get_event(conn, event_id)
        if event is None:
            raise HTTPException(status_code=404, detail=f"No event {event_id}")
        return event

    def nav_steps(conn, event_id: int, current: str) -> List[NavStep]:
        live = {d.step: d for d in repo.current_decisions(conn, event_id)}
        steps: List[NavStep] = []
        for key, label in _NAV:
            if key == "playbook":
                state = "active" if current == "playbook" else "todo"
                url = f"/events/{event_id}/playbook"
            elif key in ("slides", "checkin"):
                state = "active" if current == key else "todo"
                url = f"/events/{event_id}/{key}"
            else:
                d = live.get(key)
                if current == key:
                    state = "active"
                elif d is None:
                    state = "todo"
                elif d.is_pending:
                    state = "pending"
                else:
                    state = "done"
                url = f"/events/{event_id}/steps/{key}"
            steps.append(NavStep(key=key, label=label, url=url, state=state))
        return steps

    # ── CSV roster import (P2-5) ─────────────────────────────────────────────

    @app.get("/events/{event_id}/roster", response_class=HTMLResponse)
    def roster_form(request: Request, event_id: int):
        conn = connect()
        try:
            event = load_event(conn, event_id)
            attendees = repo.list_attendees(conn, event_id)
            return templates.TemplateResponse(request, "roster.html", {
                "event": event,
                "steps": nav_steps(conn, event_id, None),
                "event_id": event_id,
                "attendees": attendees,
                "preview": None,
                "outcome": None,
                "mappable_fields": MAPPABLE_FIELDS,
            })
        finally:
            conn.close()

    @app.post("/events/{event_id}/roster/preview", response_class=HTMLResponse)
    async def roster_preview(request: Request, event_id: int,
                             roster: UploadFile = File(...)):
        """Show what WOULD be imported. Writes nothing."""
        raw = await roster.read()
        text = raw.decode("utf-8", errors="replace")
        preview = preview_csv(text)
        conn = connect()
        try:
            event = load_event(conn, event_id)
            return templates.TemplateResponse(request, "roster.html", {
                "event": event,
                "steps": nav_steps(conn, event_id, None),
                "event_id": event_id,
                "attendees": repo.list_attendees(conn, event_id),
                "preview": preview,
                "csv_text": text,
                "outcome": None,
                "mappable_fields": MAPPABLE_FIELDS,
            })
        finally:
            conn.close()

    @app.post("/events/{event_id}/roster/import", response_class=HTMLResponse)
    async def roster_commit(request: Request, event_id: int):
        """Commit the import using the mapping the coordinator confirmed."""
        form = await request.form()
        text = form.get("csv_text") or ""
        mapping = {
            key[len("map_"):]: value
            for key, value in form.items() if key.startswith("map_")
        }
        conn = connect()
        try:
            event = load_event(conn, event_id)
            try:
                outcome = apply_roster(conn, event_id, text, mapping)
                conn.commit()
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from None
            return templates.TemplateResponse(request, "roster.html", {
                "event": event,
                "steps": nav_steps(conn, event_id, None),
                "event_id": event_id,
                "attendees": repo.list_attendees(conn, event_id),
                "preview": None,
                "outcome": outcome,
                "mappable_fields": MAPPABLE_FIELDS,
            })
        finally:
            conn.close()

    # ── routes ───────────────────────────────────────────────────────────────

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request):
        conn = connect()
        try:
            events = repo.list_events(conn)
        finally:
            conn.close()
        return templates.TemplateResponse(
            request, "home.html",
            {"event": None, "steps": [], "events": events},
        )

    @app.post("/events/scrape", response_class=HTMLResponse)
    def scrape_event_url(request: Request, event_url: str = Form("")):
        """Fetch a URL and PROPOSE facts. Creates nothing.

        Every failure path renders the same page with the manual form intact, so
        a refused or broken URL costs the coordinator a detour, never the flow.
        """
        problem = ""
        options = []
        source_url = ""
        if not event_url.strip():
            problem = "Enter an event URL, or fill in the details manually below."
        else:
            try:
                result = fetch_url(event_url)
            except UnsafeUrlError as exc:
                result = None
                problem = (
                    f"{exc} Couldn't fetch this URL safely — enter the details "
                    "manually below."
                )
            if result is not None:
                if not result.ok:
                    problem = result.error
                else:
                    source_url = result.final_url
                    facts = extract_facts(_scrape_client(), result.text,
                                          source_url=source_url)
                    options = build_fact_options(facts)
                    if not options:
                        problem = (
                            "Nothing could be extracted from that page. Enter the "
                            "details manually below."
                        )
        return templates.TemplateResponse(request, "scrape_confirm.html", {
            "event": None,
            "steps": [],
            "options": options,
            "source_url": source_url,
            "event_url": event_url,
            "problem": problem,
        })

    @app.post("/events")
    async def create_event(request: Request):
        """Create an event from confirmed values.

        Reads the form directly: the confirmable fact fields are dynamic
        (``fact_<field>``), and only what the coordinator actually submitted is
        stored — nothing carries over from the scrape implicitly.
        """
        form = await request.form()
        name = (form.get("name") or "").strip()
        city = (form.get("city") or "").strip()
        if not name or not city:
            raise HTTPException(status_code=400,
                                detail="An event needs a name and a city.")

        conn = connect()
        try:
            event_id = CoordinatorWorkflow(conn).start_event(name=name, city=city)
            source_url = (form.get("source_url") or "").strip()
            # A URL supplied here (rather than via /events/scrape) was never
            # fetched. Silently dropping it is the worst answer: the coordinator
            # reasonably believes the details were imported when nothing was. So
            # record the refusal and surface it on the first step.
            raw_url = (form.get("event_url") or "").strip()
            if raw_url and not source_url:
                try:
                    assert_fetchable(raw_url)
                    reason = "It was not fetched — enter the details manually."
                except UnsafeUrlError as exc:
                    reason = str(exc)
                repo.add_variable(conn, EventVariable(
                    event_id=event_id, kind="url_refused", value=raw_url,
                    notes=reason))
            if source_url:
                repo.add_variable(conn, EventVariable(
                    event_id=event_id, kind="source_url", value=source_url,
                    notes="Event page the details were extracted from."))
            for key, raw in form.items():
                if not key.startswith("fact_"):
                    continue
                value = (raw or "").strip()
                if not value:
                    continue          # a blank field is a decision not to record it
                repo.add_variable(conn, EventVariable(
                    event_id=event_id, kind=key[len("fact_"):], value=value,
                    notes=f"Confirmed by the coordinator from {source_url}"
                          if source_url else "Entered by the coordinator."))
            conn.commit()
        finally:
            conn.close()
        return RedirectResponse(f"/events/{event_id}/steps/{CHAIN[0]}", status_code=303)

    @app.get("/events/{event_id}/steps/{step_key}", response_class=HTMLResponse)
    def step_page(request: Request, event_id: int, step_key: str):
        conn = connect()
        try:
            event = load_event(conn, event_id)
            wf = CoordinatorWorkflow(conn)
            decision = next(
                (d for d in repo.current_decisions(conn, event_id) if d.step == step_key),
                None,
            )
            if decision is None:
                # Not staged yet — send the coordinator to the question that is.
                pending = wf.pending(event_id)
                target = pending[0].step if pending else "playbook"
                dest = (f"/events/{event_id}/playbook" if target == "playbook"
                        else f"/events/{event_id}/steps/{target}")
                return RedirectResponse(dest, status_code=303)
            index = CHAIN.index(step_key) + 1 if step_key in CHAIN else 1
            # Favourites are live state, not part of the recorded decision. The
            # stored options are a snapshot from when the step was staged, so a
            # star toggled afterwards would otherwise never appear. Overlay the
            # current set at render time — display only; the decision log is
            # untouched.
            # A URL refused at creation is reported once, on the first step —
            # a note about creation, not a permanent banner.
            url_refused = None
            if step_key == CHAIN[0]:
                for var in repo.list_variables(conn, event_id):
                    if var.kind == "url_refused":
                        url_refused = {"url": var.value, "reason": var.notes}
                        break
            if step_key == "venue":
                favs = repo.favourites(conn)
                for opt in decision.options:
                    ref = opt.data.get("venue_ref")
                    if ref:
                        opt.data["favourite"] = ref in favs
            return templates.TemplateResponse(request, "step.html", {
                "event": event,
                "steps": nav_steps(conn, event_id, step_key),
                "step": step_key,
                "step_key": step_key,
                "step_index": index,
                "step_total": len(CHAIN),
                "decision": decision,
                "chosen_key": decision.chosen_key,
                "chosen_value": decision.chosen_value,
                "url_refused": url_refused,
                "event_id": event_id,
            })
        finally:
            conn.close()

    @app.post("/events/{event_id}/decide")
    def decide(event_id: int, step: str = Form(...), key: str = Form(...),
               value: Optional[str] = Form(None)):
        conn = connect()
        try:
            load_event(conn, event_id)
            wf = CoordinatorWorkflow(conn)
            try:
                wf.choose(event_id, step=step, key=key, value=value)
            except ValueError as exc:
                # An unoffered key is a bad request, not a server fault.
                raise HTTPException(status_code=400, detail=str(exc))
            except LookupError as exc:
                raise HTTPException(status_code=404, detail=str(exc))
            conn.commit()
            pending = wf.pending(event_id)
            dest = (f"/events/{event_id}/steps/{pending[0].step}" if pending
                    else f"/events/{event_id}/playbook")
        finally:
            conn.close()
        return RedirectResponse(dest, status_code=303)

    @app.get("/events/{event_id}/playbook", response_class=HTMLResponse)
    def playbook_view(request: Request, event_id: int):
        conn = connect()
        try:
            event = load_event(conn, event_id)
            live = {d.step: d for d in repo.current_decisions(conn, event_id)}
            sections = [
                PlaybookSectionVM(
                    title=STEP_TITLES.get(key, key),
                    decision=live.get(key),
                    rejected=(live[key].alternatives if key in live else []),
                    url=f"/events/{event_id}/steps/{key}",
                )
                for key in CHAIN
            ]
            return templates.TemplateResponse(request, "playbook.html", {
                "event": event,
                "steps": nav_steps(conn, event_id, "playbook"),
                "sections": sections,
                "event_id": event_id,
                "markdown_url": f"/events/{event_id}/playbook.md",
            })
        finally:
            conn.close()

    @app.get("/events/{event_id}/playbook.md", response_class=PlainTextResponse)
    def playbook_markdown(event_id: int):
        conn = connect()
        try:
            load_event(conn, event_id)
            return render_markdown(compose_playbook(conn, event_id))
        finally:
            conn.close()

    # ── venue favourites (P2-3) ──────────────────────────────────────────────

    @app.post("/events/{event_id}/venues/{venue_ref}/favourite")
    def toggle_favourite(event_id: int, venue_ref: str, on: str = Form("1")):
        """Mark or unmark a venue as a favourite.

        Deliberately does NOT re-stage the venue decision: a favourite is a
        marker, not an answer, and toggling it must not disturb a choice the
        coordinator already made.
        """
        conn = connect()
        try:
            load_event(conn, event_id)
            repo.set_favourite(conn, venue_ref, on.strip() not in ("0", "", "false"))
            conn.commit()
        finally:
            conn.close()
        return RedirectResponse(f"/events/{event_id}/steps/venue", status_code=303)

    # ── city revision (recovery path when a city has no venue data) ──────────

    @app.get("/events/{event_id}/city", response_class=HTMLResponse)
    def city_form(request: Request, event_id: int):
        conn = connect()
        try:
            event = load_event(conn, event_id)
            return templates.TemplateResponse(request, "city.html", {
                "event": event,
                "steps": nav_steps(conn, event_id, "venue"),
                "event_id": event_id,
            })
        finally:
            conn.close()

    @app.post("/events/{event_id}/city")
    def city_update(event_id: int, city: str = Form(...)):
        """Change the event's city and re-stage the venue step against it.

        Withdraws the blocked venue decision rather than deleting it: the log
        should still show that we looked and found nothing for the old city.
        """
        conn = connect()
        try:
            load_event(conn, event_id)
            conn.execute("UPDATE events SET city=? WHERE id=?", (city.strip(), event_id))
            wf = CoordinatorWorkflow(conn)
            live_venue = next(
                (d for d in repo.current_decisions(conn, event_id) if d.step == "venue"),
                None,
            )
            if live_venue is not None:
                conn.execute("UPDATE decisions SET superseded_by=id WHERE id=?",
                             (live_venue.id,))
            wf._stage_venue(event_id)
            conn.commit()
        finally:
            conn.close()
        return RedirectResponse(f"/events/{event_id}/steps/venue", status_code=303)

    # ── slides (T10/T11 in the shell) ────────────────────────────────────────

    def _deck(conn, event_id: int):
        # Stock imagery is optional; brand roles resolve offline either way.
        from app.claude.client import get_client
        from app.config import CONFIG
        from app.providers.registry import get_image_provider

        stock = get_image_provider(CONFIG) if CONFIG.pexels_api_key else None
        # Only hand the deck a client when real Claude is explicitly enabled.
        # In mock mode the deck uses deterministic copy rather than rendering
        # mock scaffolding onto a title slide.
        claude = get_client(CONFIG) if CONFIG.claude_enabled else None
        return build_deck(compose_playbook(conn, event_id), ImageResolver(stock),
                          claude_client=claude)

    @app.get("/events/{event_id}/slides", response_class=HTMLResponse)
    def slides_view(request: Request, event_id: int):
        conn = connect()
        try:
            event = load_event(conn, event_id)
            deck = _deck(conn, event_id)
            return templates.TemplateResponse(request, "slides.html", {
                "event": event,
                "steps": nav_steps(conn, event_id, "slides"),
                "deck": deck,
                "event_id": event_id,
                "markdown_url": f"/events/{event_id}/slides.md",
            })
        finally:
            conn.close()

    @app.get("/events/{event_id}/slides.md", response_class=PlainTextResponse)
    def slides_markdown(event_id: int):
        conn = connect()
        try:
            load_event(conn, event_id)
            return render_deck_markdown(_deck(conn, event_id))
        finally:
            conn.close()

    # ── check-in (day-of operation) ──────────────────────────────────────────

    def _checkin_page(request: Request, conn, event_id: int, event,
                      scan_state: Optional[str] = None, scan_name: str = ""):
        return templates.TemplateResponse(request, "checkin.html", {
            "event": event,
            "steps": nav_steps(conn, event_id, "checkin"),
            "event_id": event_id,
            "attendees": repo.list_attendees(conn, event_id),
            "scan_state": scan_state,
            "scan_name": scan_name,
        })

    @app.get("/events/{event_id}/checkin", response_class=HTMLResponse)
    def checkin_view(request: Request, event_id: int):
        conn = connect()
        try:
            event = load_event(conn, event_id)
            return _checkin_page(request, conn, event_id, event)
        finally:
            conn.close()

    @app.post("/events/{event_id}/checkin", response_class=HTMLResponse)
    def checkin_scan(request: Request, event_id: int, code: str = Form("")):
        conn = connect()
        try:
            event = load_event(conn, event_id)
            if not code.strip():
                # Fat-fingered/empty scan: re-render the desk with the tampered
                # banner instead of FastAPI's raw 422 JSON, which a door
                # operator can't act on.
                return _checkin_page(request, conn, event_id, event,
                                     scan_state="tampered", scan_name="")
            state, attendee = check_in(conn, _signing_secret(), code)
            conn.commit()
            name = attendee.full_name if attendee else ""
            return _checkin_page(request, conn, event_id, event,
                                 scan_state=state, scan_name=name)
        finally:
            conn.close()

    @app.post("/events/{event_id}/checkin/walkin")
    def checkin_walkin(event_id: int, full_name: str = Form(...)):
        conn = connect()
        try:
            load_event(conn, event_id)
            self_check_in(conn, event_id, full_name)
            conn.commit()
        finally:
            conn.close()
        return RedirectResponse(f"/events/{event_id}/checkin", status_code=303)

    return app


app = create_app(os.environ.get("DB_PATH", "events.db"))
