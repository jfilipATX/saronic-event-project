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
import re
import secrets
import sqlite3
import tempfile
from dataclasses import dataclass
from typing import List, Optional

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import (
    HTMLResponse,
    PlainTextResponse,
    RedirectResponse,
    Response,
    FileResponse,
)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.db import repository as repo, schema_sql_text as sql
from app.db.models import (
    Attendee,
    EventVariable,
    LibraryImage,
    Segment,
    Person,
)
from app.features.deck import build_deck, render_deck_markdown
from app.features.event_facts import build_fact_options, extract_facts
from app.features.exec_pptx import build_exec_pptx
from app.features.images import ImageResolver
from app.features.playbook import STEP_TITLES, compose_playbook, render_markdown
from app.features.visuals import VisualRequest, render_all, strip_exif
from app.features.venue_scrape import (
    AMENITIES,
    AMENITY_LABELS,
    build_venue_options as build_scraped_options,
    extract_venue,
    venue_from_facts,
)
from app.features.venue_search import (
    build_search_prompt,
    estimate_search_cost,
    search_venues,
)
from app.claude.errors import BudgetExceededError, ClaudeError
from app.claude.ledger import SpendLedger
from app.config import CONFIG
from app.features.image_library import (
    classify_backdrop,
    fetch_feed,
    fetch_image,
    parse_feed,
)
from app.features.run_of_show import (
    DEFAULT_TRACKS,
    KIND_LABELS,
    SEGMENT_KINDS,
    board_lanes,
    board_width_px,
    conflicts_for,
    hour_ticks,
    group_by_day,
    now_line_pct,
    seed_standard_day,
    validate_segment,
)
from app.features.schedule import (
    describe_schedule,
    event_day_windows,
    parse_window,
    window_for_event,
)
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
    check_in_by_email,
    find_invitee_truncated,
    issue_invitation,
    manual_check_in,
    mint_code,
    register_walk_in,
    self_check_in,
)
from app.features.workflow import CHAIN, CoordinatorWorkflow

_UI_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui")

#: Stepper labels for the nav. Chain steps first, then the derived views.
#: Nav entries served from /events/{id}/{key}, NOT from the decision chain.
#: This MUST list every non-chain entry in _NAV. An entry missing here silently
#: receives a /steps/{key} URL, which route-mismatches into another page and
#: still returns 200 - the af5227f bug, which a merge reintroduced for
#: "schedule" because the two edits touched the same line from different
#: branches. Derived below rather than hand-listed so they cannot drift again.
_CHAIN_KEYS = set(CHAIN)

_NAV = (
    [(key, STEP_TITLES.get(key, key)) for key in CHAIN]
    + [("people", "People"), ("schedule", "Schedule"), ("run-of-show", "Run of show"),
       ("slides", "Slides"), ("visuals", "Visuals"),
       ("invites", "Invitations"),
       ("checkin", "Check-in"), ("playbook", "Playbook")]
)

#: (key, label) pairs for the manual amenity form.
AMENITY_DEFAULTS = [(k, AMENITY_LABELS[k]) for k in AMENITIES]

#: Set by create_app so helpers/tests can reach the active database.
CURRENT_DB = "events.db"


def _scrape_client(conn=None):
    """Claude client for URL extraction, or None when real Claude is off.

    A separate seam from the deck's client so tests can stub extraction without
    touching slide copy, and so mock mode never fabricates event facts.
    """
    from app.claude.client import get_client
    from app.config import load_config

    cfg = load_config()
    if not cfg.claude_enabled:
        return None

    return get_client(cfg, ledger=SpendLedger(conn) if conn is not None else None)


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
            if key in ("playbook", "people"):
                # Global views with no event scope: not /events/{id}/{key}.
                state = "active" if current == key else "todo"
                url = f"/{key}" if key == "people" else f"/events/{event_id}/playbook"
            elif key not in _CHAIN_KEYS:
                # Anything that is not a decision-chain step is a derived view
                # at /events/{id}/{key}. Deriving this from CHAIN rather than
                # listing keys means adding a nav entry cannot silently produce
                # a broken /steps/ link.
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
            ledger_conn = connect()
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
                    client = _scrape_client(ledger_conn)
                    facts = extract_facts(client, result.text,
                                          source_url=source_url)
                    options = build_fact_options(facts)
                    if not options:
                        problem = (
                            "Nothing could be extracted from that page. Enter the "
                            "details manually below."
                        )
            ledger_conn.close()
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
        state = (form.get("state") or "").strip()
        # Country defaults to US (Joseph's standing default); a blank form field
        # must not produce a broken location line.
        country = (form.get("country") or "US").strip() or "US"
        owner_name = (form.get("owner_name") or "").strip() or None
        owner_role = (form.get("owner_role") or "").strip() or None
        if not name or not city:
            raise HTTPException(status_code=400,
                                detail="An event needs a name and a city.")

        try:
            window = parse_window(form.get("starts_at") or "",
                                  form.get("ends_at") or "")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None

        conn = connect()
        try:
            event_id = CoordinatorWorkflow(conn).start_event(
                name=name, city=city, state=state, country=country,
                owner_name=owner_name, owner_role=owner_role)
            if window.is_set:
                repo.set_event_window(conn, event_id, window)
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
                "claude_spend": repo.spend_total(conn, event_id=event_id),
                "schedule": describe_schedule(conn, event_id),
                "owner": (event.owner_name +
                          (f" — {event.owner_role}" if event.owner_role else ""))
                         if event.owner_name else None,
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

    @app.get("/events/{event_id}/playbook/print", response_class=HTMLResponse)
    def playbook_print(request: Request, event_id: int):
        """P5-7 — printable day-of operational reference. Standalone, chrome-free
        document that reads the same playbook + run-of-show + ledger as the
        screen view, so print and screen never disagree. No Claude call."""
        conn = connect()
        try:
            event = load_event(conn, event_id)
            pb = compose_playbook(conn, event_id)
            # Decisions from the settled chain, in chain order.
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
            # Venue facts from the settled venue decision.
            venue_label = venue_capacity = None
            venue_amenities = []
            venue_opt_out = repo.get_event_opt_out(conn, event_id) \
                if hasattr(repo, "get_event_opt_out") else None
            for sec in pb.sections:
                if sec.step == "venue" or "venue" in sec.title.lower():
                    venue_label = sec.chosen_label
            # Run of show, grouped by day, with date stripped from row times.
            segments = repo.list_segments(conn, event_id)
            conflicts = conflicts_for(segments)
            pool = {p.id: p for p in repo.list_people(conn, include_erased=True)}

            def owner_label_for(pid):
                p = pool.get(pid)
                return p.display_name if p and not p.is_erased else "—"

            ros_days = []
            for day, segs in group_by_day(segments).items():
                day_segs = []
                for s in segs:
                    def _t(val, day=day):
                        if not val:
                            return "—"
                        # Strip the date prefix when it matches the day header.
                        if val.startswith(day + " "):
                            return val[len(day) + 1:]
                        if "T" in val and val.startswith(day + "T"):
                            return val[len(day) + 1:].replace("T", " ")
                        return val
                    day_segs.append({
                        "id": s.id,
                        "title": s.title,
                        "track": s.track,
                        "location": s.location,
                        "start_time": _t(s.start),
                        "end_time": _t(s.end),
                        "owners": ", ".join(owner_label_for(o)
                                            for o in s.owner_ids) or "—",
                    })
                ros_days.append({"day": day, "segments": day_segs})
            # Check-in essentials: VIPs (name/company only) + assigned staff.
            attendees = repo.list_attendees(conn, event_id)
            vips = [{"name": a.full_name or "(unnamed)",
                     "company": a.company or ""}
                    for a in attendees if a.is_vip]
            checkin_staff = []
            for row in repo.event_staff_rows(conn, event_id):
                if not row["can_check_in"]:
                    continue
                p = pool.get(row["person_id"])
                if p and not p.is_erased:
                    checkin_staff.append(p.display_name)
            spend = repo.spend_total(conn, event_id=event_id)
            owner = (event.owner_name +
                     (f" — {event.owner_role}" if event.owner_role else "")) \
                if event.owner_name else None
            return templates.TemplateResponse(request, "playbook_print.html", {
                "event": event,
                "schedule": describe_schedule(conn, event_id),
                "owner": owner,
                "sections": sections,
                "venue_label": venue_label,
                "venue_capacity": venue_capacity,
                "venue_amenities": venue_amenities,
                "venue_opt_out": venue_opt_out,
                "ros_days": ros_days,
                "conflicts": conflicts,
                "owner_label_for": owner_label_for,
                "vips": vips,
                "checkin_staff": sorted(set(checkin_staff)),
                "spend": spend if spend else None,
            })
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
        claude = None
        if CONFIG.claude_enabled:
            claude = get_client(CONFIG, ledger=SpendLedger(conn))
        return build_deck(compose_playbook(conn, event_id), ImageResolver(stock),
                          claude_client=claude, event_id=event_id)

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

    @app.get("/events/{event_id}/slides/export.pptx",
             response_class=FileResponse)
    def slides_export_pptx(event_id: int):
        """P5-6 — executive PowerPoint export. Builds from the same playbook +
        run-of-show + ledger the web views use, so the .pptx can never disagree
        with the on-screen playbook. No Claude call; content is already settled.
        """
        conn = connect()
        try:
            event = load_event(conn, event_id)
            pb = compose_playbook(conn, event_id)
            segments = repo.list_segments(conn, event_id)
            blob = build_exec_pptx(conn, event_id, pb, segments, event=event)
            slug = re.sub(r"[^a-z0-9]+", "-", (event.name or "event").lower()).strip("-") or "event"
            path = os.path.join(tempfile.gettempdir(), f"{slug}-executive-summary.pptx")
            with open(path, "wb") as fh:
                fh.write(blob)
            return FileResponse(
                path,
                media_type=("application/vnd.openxmlformats-officedocument."
                            "presentationml.presentation"),
                filename=f"{slug}-executive-summary.pptx",
            )
        finally:
            conn.close()

    # ── venue opt-out (P4-2) ─────────────────────────────────────────────────

    @app.post("/events/{event_id}/venues/opt-out")
    def venue_opt_out(event_id: int, host_event: str = Form("")):
        """Record that the venue is established by a host event, not chosen."""
        conn = connect()
        try:
            load_event(conn, event_id)
            try:
                CoordinatorWorkflow(conn).opt_out_of_venue(
                    event_id, host_event=host_event)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from None
            conn.commit()
        finally:
            conn.close()
        return RedirectResponse(f"/events/{event_id}/steps/venue", status_code=303)

    # ── add a venue by URL (P4-1) ────────────────────────────────────────────

    @app.post("/events/{event_id}/venues/scrape", response_class=HTMLResponse)
    def venue_scrape(request: Request, event_id: int, venue_url: str = Form("")):
        """Read a venue's own page and PROPOSE it. Adds nothing on its own."""
        conn = connect()
        try:
            event = load_event(conn, event_id)
            url = (venue_url or "").strip()
            options, problem, facts = [], "", {}
            try:
                result = fetch_url(url)
            except UnsafeUrlError as exc:
                problem = (f"Could not fetch that URL safely ({exc}). Add the "
                           f"venue manually below.")
            except FetchError as exc:
                problem = (f"That page could not be read ({exc}). Add the venue "
                           f"manually below.")
            else:
                facts = extract_venue(_scrape_client(conn), result.text,
                                      source_url=result.final_url,
                                      event_id=event_id)
                options = build_scraped_options(facts, result.final_url)
                if not facts.get("venue_name"):
                    problem = ("Nothing usable could be read from that page. "
                               "Add the venue manually below.")
            return templates.TemplateResponse(request, "venue_add.html", {
                "event": event,
                "steps": nav_steps(conn, event_id, "venue"),
                "event_id": event_id,
                "options": options,
                "source_url": url,
                "problem": problem,
                "amenity_defaults": AMENITY_DEFAULTS,
            })
        finally:
            conn.close()

    @app.get("/events/{event_id}/venues/add", response_class=HTMLResponse)
    def venue_add_form(request: Request, event_id: int):
        conn = connect()
        try:
            return templates.TemplateResponse(request, "venue_add.html", {
                "event": load_event(conn, event_id),
                "steps": nav_steps(conn, event_id, "venue"),
                "event_id": event_id,
                "options": [], "source_url": "", "problem": "",
                "amenity_defaults": AMENITY_DEFAULTS,
            })
        finally:
            conn.close()

    @app.get("/events/{event_id}/venues/search", response_class=HTMLResponse)
    def venue_search_form(request: Request, event_id: int):
        """Manual-trigger entry point. Shows the cost estimate BEFORE the
        coordinator pulls the trigger — you cannot spend what you cannot see."""
        conn = connect()
        try:
            event = load_event(conn, event_id)
            # A conservative pre-call estimate from the event's own facts, so the
            # number is real (not a constant) and visible before any call.
            capacity = event.audience_estimate or 0
            amenity_keys = [k for k, _ in AMENITY_DEFAULTS]
            prompt = build_search_prompt(
                event.city or "", event.state or "", capacity, amenity_keys)
            in_t, out_t, usd = estimate_search_cost(len(prompt))
            cap = CONFIG.venue_search_cap_usd
            spent = repo.spend_total(conn, event_id=event_id)
            return templates.TemplateResponse(request, "venue_search.html", {
                "event": event,
                "steps": nav_steps(conn, event_id, "venue"),
                "event_id": event_id,
                "est_input_tokens": in_t,
                "est_output_tokens": out_t,
                "est_usd": usd,
                "cap_usd": cap,
                "spent_usd": spent,
                "over_cap": spent >= cap,
                "problem": "",
            })
        finally:
            conn.close()

    @app.post("/events/{event_id}/venues/search", response_class=HTMLResponse)
    def venue_search_run(request: Request, event_id: int,
                         city: str = Form(""), state: str = Form(""),
                         capacity: int = Form(0),
                         amenities: list = Form([])):
        """Trigger the LLM venue search. Capped (per-event) and logged.

        Returns proposed venues as staged options — the coordinator confirms
        before anything lands on the slate.
        """
        conn = connect()
        try:
            event = load_event(conn, event_id)
            city = (city or event.city or "").strip()
            state = (state or event.state or "").strip()
            capacity = int(capacity or event.audience_estimate or 0)
            if not city or not capacity:
                return templates.TemplateResponse(request, "venue_search.html", {
                    "event": event,
                    "steps": nav_steps(conn, event_id, "venue"),
                    "event_id": event_id,
                    "problem": "Need a city and a target capacity to search.",
                    "est_usd": 0.0, "cap_usd": CONFIG.venue_search_cap_usd,
                    "spent_usd": repo.spend_total(conn, event_id=event_id),
                    "over_cap": False,
                })
            ledger = SpendLedger(conn)
            client = _scrape_client(conn)  # None in mock mode -> offline path
            try:
                options = search_venues(
                    client, event_id=event_id, city=city, state=state,
                    capacity=capacity, amenities=list(amenities),
                    ledger=ledger,
                    per_event_cap_usd=CONFIG.venue_search_cap_usd)
            except BudgetExceededError:
                return templates.TemplateResponse(request, "venue_search.html", {
                    "event": event,
                    "steps": nav_steps(conn, event_id, "venue"),
                    "event_id": event_id,
                    "problem": (f"Venue search is capped at "
                                f"${CONFIG.venue_search_cap_usd:.2f} per event "
                                f"to control cost. This event has reached its "
                                f"limit — no call was made."),
                    "est_usd": 0.0, "cap_usd": CONFIG.venue_search_cap_usd,
                    "spent_usd": repo.spend_total(conn, event_id=event_id),
                    "over_cap": True,
                })
            except ClaudeError as exc:
                # Provider fail-soft: a bad key / rate limit / outage must not
                # 500 the desk. Surface it as a problem; the coordinator can
                # retry or add venues manually.
                return templates.TemplateResponse(request, "venue_search.html", {
                    "event": event,
                    "steps": nav_steps(conn, event_id, "venue"),
                    "event_id": event_id,
                    "problem": (f"The venue search could not run "
                                f"({type(exc).__name__}). Nothing was charged "
                                f"for a failed call — add venues manually "
                                f"below, or retry."),
                    "est_usd": 0.0, "cap_usd": CONFIG.venue_search_cap_usd,
                    "spent_usd": repo.spend_total(conn, event_id=event_id),
                    "over_cap": False,
                })
            # Stage the proposals: render them as confirmable cards (proposed,
            # never auto-applied) on the search results surface. Each card posts
            # its facts to /venues/add when the coordinator accepts it.
            return templates.TemplateResponse(request, "venue_search.html", {
                "event": event,
                "steps": nav_steps(conn, event_id, "venue"),
                "event_id": event_id,
                "options": options,
                "est_usd": 0.0, "cap_usd": CONFIG.venue_search_cap_usd,
                "spent_usd": repo.spend_total(conn, event_id=event_id),
                "over_cap": False,
                "searched": True,
            })
        finally:
            conn.close()

    @app.post("/events/{event_id}/venues/add")
    async def venue_add(request: Request, event_id: int):
        """Confirm the proposals and add the venue to this event's slate."""
        form = await request.form()
        facts = {k: v for k, v in form.items() if v}
        amenities = {k[len("amenity_"):]: v for k, v in form.items()
                     if k.startswith("amenity_")}
        facts["amenities"] = amenities
        conn = connect()
        try:
            event = load_event(conn, event_id)
            try:
                venue = venue_from_facts(facts, facts.get("source_url", ""))
            except ValueError as exc:
                return templates.TemplateResponse(request, "venue_add.html", {
                    "event": event,
                    "steps": nav_steps(conn, event_id, "venue"),
                    "event_id": event_id, "options": [],
                    "source_url": facts.get("source_url", ""),
                    "problem": str(exc),
                    "amenity_defaults": AMENITY_DEFAULTS,
                }, status_code=400)
            repo.add_custom_venue(conn, event_id, venue)
            # Re-stage so the new venue is rated and sorted with the rest.
            CoordinatorWorkflow(conn).restage_venue(event_id)
            conn.commit()
        finally:
            conn.close()
        return RedirectResponse(f"/events/{event_id}/steps/venue", status_code=303)

    # ── run of show (P4-4) ───────────────────────────────────────────────────

    def _run_of_show_page(request: Request, conn, event_id: int, event,
                          problem: str = "", form=None, view: str = "list"):
        segments = repo.list_segments(conn, event_id)
        # The owner picker draws from the global people pool (P5-5), reading each
        # person's per-event assignment (role override + can_check_in) for this
        # event. People not yet assigned to this event can still be picked as an
        # owner — attaching is implicit on selection. Erased people stay in the
        # pool mapping because a past segment can still reference them.
        pool = {p.id: p for p in repo.list_people(conn, include_erased=True)}
        assigned = {r["person_id"]: r for r in
                    repo.event_staff_rows(conn, event_id)}
        window = window_for_event(conn, event_id)
        flags = conflicts_for(segments)
        return templates.TemplateResponse(request, "run_of_show.html", {
            "event": event,
            "steps": nav_steps(conn, event_id, "run-of-show"),
            "event_id": event_id,
            "segments": segments,
            "days": group_by_day(segments),
            "lanes": board_lanes(segments, window, flags=flags),
            "ticks": hour_ticks(window),
            "board_px": board_width_px(window),
            "now_pct": now_line_pct(window),
            "flags": flags,
            "pool": pool,
            "assigned": assigned,
            "staff": [pool[i] for i in sorted(assigned)
                      if not pool[i].is_erased],
            "staff_by_id": pool,
            "tracks": sorted({s.track for s in segments} | set(DEFAULT_TRACKS)),
            "kinds": [(k, KIND_LABELS[k]) for k in SEGMENT_KINDS],
            "window": window,
            "schedule_text": describe_schedule(conn, event_id),
            "view": view,
            "problem": problem,
            "form": form or {},
        })

    @app.get("/events/{event_id}/run-of-show", response_class=HTMLResponse)
    def run_of_show_view(request: Request, event_id: int, view: str = "list"):
        conn = connect()
        try:
            return _run_of_show_page(request, conn, event_id,
                                     load_event(conn, event_id), view=view)
        finally:
            conn.close()

    @app.post("/events/{event_id}/run-of-show/staff/assign")
    def run_of_show_assign_staff(event_id: int, name: str = Form(""),
                                 role: str = Form(""),
                                 can_check_in: bool = Form(False)):
        """Add a person to the global pool if new, then attach them to this
        event (P5-5/P5-9). ``can_check_in`` is the per-event grant, not a global
        trait — it never bleeds into other events for this person."""
        conn = connect()
        try:
            load_event(conn, event_id)
            name = (name or "").strip()
            if not name:
                raise HTTPException(status_code=400,
                                    detail="A staff member needs a name.")
            # Reuse an existing pool person with the same name rather than
            # duplicating them across events.
            existing = conn.execute(
                "SELECT id FROM people WHERE name=? AND erased_at IS NULL",
                (name,)).fetchone()
            person_id = (existing["id"] if existing
                         else repo.add_person(conn, Person(name=name, role=role)))
            repo.assign_staff(conn, event_id, person_id, role=role,
                              can_check_in=can_check_in)
            conn.commit()
        finally:
            conn.close()
        return RedirectResponse(f"/events/{event_id}/run-of-show",
                                status_code=303)

    @app.post("/events/{event_id}/run-of-show/staff/{staff_id}/remove")
    def run_of_show_remove_staff(event_id: int, staff_id: int):
        """Remove the person from THIS event only (P5-9). The Person stays in
        the global pool — this is not erasure."""
        conn = connect()
        try:
            load_event(conn, event_id)
            repo.remove_staff(conn, event_id, staff_id)
            conn.commit()
        finally:
            conn.close()
        return RedirectResponse(f"/events/{event_id}/run-of-show",
                                status_code=303)

    @app.post("/events/{event_id}/run-of-show/staff/{staff_id}/erase")
    def run_of_show_erase_staff(event_id: int, staff_id: int):
        conn = connect()
        try:
            load_event(conn, event_id)
            try:
                repo.erase_person(conn, staff_id)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from None
            conn.commit()
        finally:
            conn.close()
        return RedirectResponse(f"/events/{event_id}/run-of-show",
                                status_code=303)

    # ── global people pool (P5-5) ──────────────────────────────────────────────

    @app.get("/people", response_class=HTMLResponse)
    def people_pool_view(request: Request):
        conn = connect()
        try:
            people = repo.list_people(conn, include_erased=True)
            return templates.TemplateResponse(request, "people.html", {
                "people": [p for p in people if not p.is_erased],
                "erased": [p for p in people if p.is_erased],
            })
        finally:
            conn.close()

    @app.post("/people")
    def people_add(name: str = Form(""), role: str = Form("")):
        """Create a person in the global pool (P5-5). Reuses an existing pool
        person with the same name rather than duplicating them."""
        conn = connect()
        try:
            name = (name or "").strip()
            if not name:
                raise HTTPException(status_code=400,
                                    detail="A staff member needs a name.")
            existing = conn.execute(
                "SELECT id FROM people WHERE name=? AND erased_at IS NULL",
                (name,)).fetchone()
            if not existing:
                repo.add_person(conn, Person(name=name, role=role))
            conn.commit()
        finally:
            conn.close()
        return RedirectResponse("/people", status_code=303)

    @app.post("/people/{person_id}/erase")
    def people_erase(person_id: int):
        conn = connect()
        try:
            try:
                repo.erase_person(conn, person_id)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from None
            conn.commit()
        finally:
            conn.close()
        return RedirectResponse("/people", status_code=303)

    @app.post("/events/{event_id}/run-of-show/seed")
    def run_of_show_seed(event_id: int):
        conn = connect()
        try:
            load_event(conn, event_id)
            try:
                seed_standard_day(conn, event_id)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from None
            conn.commit()
        finally:
            conn.close()
        return RedirectResponse(f"/events/{event_id}/run-of-show",
                                status_code=303)

    @app.post("/events/{event_id}/run-of-show/segments", response_class=HTMLResponse)
    async def run_of_show_add_segment(request: Request, event_id: int):
        form = await request.form()
        typed = {k: v for k, v in form.items()}
        owners = [int(v) for v in form.getlist("owners") if str(v).isdigit()]
        segment = Segment(
            event_id=event_id,
            title=str(form.get("title") or ""),
            start=str(form.get("start") or ""),
            end=str(form.get("end") or ""),
            track=str(form.get("track") or "Logistics"),
            kind=str(form.get("kind") or "logistics"),
            location=str(form.get("location") or "") or None,
            notes=str(form.get("notes") or "") or None,
            owner_ids=owners,
        )
        conn = connect()
        try:
            event = load_event(conn, event_id)
            try:
                validate_segment(segment)
            except ValueError as exc:
                # Sticky: re-render with what was typed rather than an empty
                # form (walk-in precedent).
                return _run_of_show_page(request, conn, event_id, event,
                                         problem=str(exc), form=typed)
            segment_id = str(form.get("segment_id") or "")
            if segment_id.isdigit():
                segment.id = int(segment_id)
                repo.update_segment(conn, segment)
            else:
                repo.add_segment(conn, segment)
            conn.commit()
        finally:
            conn.close()
        return RedirectResponse(f"/events/{event_id}/run-of-show",
                                status_code=303)

    @app.post("/events/{event_id}/run-of-show/segments/{segment_id}/delete")
    def run_of_show_delete_segment(event_id: int, segment_id: int):
        conn = connect()
        try:
            load_event(conn, event_id)
            repo.delete_segment(conn, segment_id)
            conn.commit()
        finally:
            conn.close()
        return RedirectResponse(f"/events/{event_id}/run-of-show",
                                status_code=303)

    # ── event schedule (P4-3) ────────────────────────────────────────────────

    def _parse_day_form(form) -> list:
        """Build DayWindow list from the schedule form.

        The form sends repeated day_N_date / day_N_open / day_N_close fields.
        A day with a date but blank hours is valid (times optional). A day with
        no date is skipped. Raises ValueError with a readable message on a bad
        hour or an end-before-start within a day.
        """
        from app.features.schedule import DayWindow, _looks_like_hour

        days: list = []
        i = 0
        while True:
            date = (form.get(f"day_{i}_date") or "").strip()
            if not date and i >= 20:
                break
            if not date:
                i += 1
                if i >= 20:
                    break
                continue
            open_h = (form.get(f"day_{i}_open") or "").strip() or None
            close_h = (form.get(f"day_{i}_close") or "").strip() or None
            if open_h and not _looks_like_hour(open_h):
                raise ValueError(
                    f"Day {i + 1} open time '{open_h}' is not a time (HH:MM).")
            if close_h and not _looks_like_hour(close_h):
                raise ValueError(
                    f"Day {i + 1} close time '{close_h}' is not a time (HH:MM).")
            if open_h and close_h and close_h <= open_h:
                raise ValueError(
                    f"Day {i + 1} cannot close ({close_h}) before it opens "
                    f"({open_h}).")
            days.append(DayWindow(date=date, open=open_h, close=close_h,
                                  day_index=len(days)))
            i += 1
            if i >= 20:
                break
        return days

    def _schedule_page(request: Request, conn, event_id: int, event,
                       problem: str = "", days=None):
        if days is None:
            days = event_day_windows(conn, event_id)
        return templates.TemplateResponse(request, "schedule.html", {
            "event": event,
            "steps": nav_steps(conn, event_id, None),
            "event_id": event_id,
            "days": days,
            "description": describe_schedule(conn, event_id),
            "problem": problem,
        })

    @app.get("/events/{event_id}/schedule", response_class=HTMLResponse)
    def schedule_view(request: Request, event_id: int):
        conn = connect()
        try:
            return _schedule_page(request, conn, event_id, load_event(conn, event_id))
        finally:
            conn.close()

    @app.post("/events/{event_id}/schedule", response_class=HTMLResponse)
    async def schedule_set(request: Request, event_id: int):
        conn = connect()
        try:
            event = load_event(conn, event_id)
            form = await request.form()
            try:
                days = _parse_day_form(form)
            except ValueError as exc:
                # Re-render with the reason and the EXISTING days intact: a
                # rejected edit must never destroy a schedule that was correct.
                return _schedule_page(request, conn, event_id, event,
                                      problem=str(exc),
                                      days=repo.event_days(conn, event_id))
            repo.replace_event_days(conn, event_id, days)
            conn.commit()
            return _schedule_page(request, conn, event_id, event)
        finally:
            conn.close()

    # ── spend ledger (P3-1) ──────────────────────────────────────────────────

    @app.get("/usage", response_class=HTMLResponse)
    def usage_view(request: Request):
        """Global Claude API spend. Bookkeeping, not a dashboard."""
        conn = connect()
        try:
            names = {e.id: e.name for e in repo.list_events(conn)}
            entries = repo.spend_entries(conn)
            return templates.TemplateResponse(request, "usage.html", {
                "event": None,
                "steps": [],
                "entries": [e for e in entries if e.event_id is not None],
                "unattributed": [e for e in entries if e.event_id is None],
                "event_names": names,
                "total": repo.spend_total(conn),
            })
        finally:
            conn.close()

    # ── visuals (P2-4) ───────────────────────────────────────────────────────

    def _visuals_dir(event_id: int) -> str:
        return os.path.join("generated", "visuals", str(event_id))

    def _visuals_page(request: Request, conn, event_id: int, event,
                      problem: str = ""):
        playbook = compose_playbook(conn, event_id)
        upload = os.path.join(_visuals_dir(event_id), "city.png")
        # Which library image is currently the backdrop, so provenance reaches
        # the sidecar rather than defaulting to "uploaded".
        origin, attribution = "uploaded", ""
        chosen_image_id = None
        for var in repo.list_variables(conn, event_id):
            if var.kind == "backdrop_image_id" and var.value.isdigit():
                chosen = repo.get_library_image(conn, int(var.value))
                if chosen is not None:
                    chosen_image_id = chosen.id
                    origin = chosen.origin
                    attribution = chosen.article_title or chosen.source_url
                break
        results = []
        try:
            results = render_all(VisualRequest(
                event_name=event.name,
                city=event.city or "",
                dates=_event_dates(conn, event_id),
                city_image=upload if os.path.exists(upload) else None,
                city_image_origin=origin,
                city_image_attribution=attribution,
                out_dir=_visuals_dir(event_id),
            ))
        except ValueError as exc:
            problem = problem or str(exc)
        return templates.TemplateResponse(request, "visuals.html", {
            "event": event,
            "steps": nav_steps(conn, event_id, "visuals"),
            "event_id": event_id,
            "results": results,
            "has_upload": os.path.exists(upload),
            "library": repo.library_images(conn, event_id),
            "chosen_image_id": chosen_image_id,
            "open_questions": playbook.open_questions,
            "problem": problem,
        })

    def _event_dates(conn, event_id: int) -> str:
        variables = {v.kind: v.value for v in repo.list_variables(conn, event_id)}
        start, end = variables.get("start_date", ""), variables.get("end_date", "")
        if start and end:
            return f"{start} – {end}"
        return start or ""

    @app.get("/events/{event_id}/visuals", response_class=HTMLResponse)
    def visuals_view(request: Request, event_id: int):
        conn = connect()
        try:
            return _visuals_page(request, conn, event_id, load_event(conn, event_id))
        finally:
            conn.close()

    @app.post("/events/{event_id}/visuals/library/import", response_class=HTMLResponse)
    def visuals_import_blog(request: Request, event_id: int,
                            blog_url: str = Form("")):
        """Import lead images from the company blog into this event's library."""
        conn = connect()
        try:
            event = load_event(conn, event_id)
            problem, imported = "", 0
            try:
                assets = parse_feed(fetch_feed(blog_url))
            except (UnsafeUrlError, ValueError) as exc:
                assets, problem = [], str(exc)
            except Exception:
                assets = []
                problem = ("That blog could not be reached. Check the URL, or "
                           "upload images directly below.")
            for index, asset in enumerate(assets, 1):
                destination = os.path.join(_visuals_dir(event_id), "library",
                                           f"blog-{index}.png")
                try:
                    width, height = fetch_image(asset.source_url, destination)
                except Exception:
                    # One unusable image (too small, moved, hotlink-blocked)
                    # must not abandon the rest of the import.
                    continue
                repo.add_library_image(conn, LibraryImage(
                    event_id=event_id, path=destination,
                    source_url=asset.source_url,
                    article_title=asset.article_title,
                    article_url=asset.article_url,
                    origin="blog", width=width, height=height,
                    backdrop_kind=classify_backdrop(destination)))
                imported += 1
            conn.commit()
            if not problem and imported == 0:
                problem = "No usable images were found on that blog."
            return _visuals_page(request, conn, event_id, event, problem=problem)
        finally:
            conn.close()

    @app.post("/events/{event_id}/visuals/library/{image_id}/use")
    def visuals_use_library_image(event_id: int, image_id: int):
        """Make a library image the base layer for this event's composites."""
        conn = connect()
        try:
            load_event(conn, event_id)
            image = repo.get_library_image(conn, image_id)
            if image is None or image.event_id != event_id:
                raise HTTPException(status_code=404, detail="No such image.")
            target = os.path.join(_visuals_dir(event_id), "city.png")
            os.makedirs(os.path.dirname(target), exist_ok=True)
            import shutil

            shutil.copyfile(image.path, target)
            # Remember WHICH image, not just that a file exists — the sidecar
            # has to name the article, and the copied file cannot say.
            repo.set_variable(conn, event_id, "backdrop_image_id", str(image_id))
            conn.commit()
        finally:
            conn.close()
        return RedirectResponse(f"/events/{event_id}/visuals", status_code=303)

    @app.post("/events/{event_id}/visuals/library/{image_id}/delete")
    def visuals_delete_library_image(event_id: int, image_id: int):
        conn = connect()
        try:
            load_event(conn, event_id)
            repo.delete_library_image(conn, image_id)
            conn.commit()
        finally:
            conn.close()
        return RedirectResponse(f"/events/{event_id}/visuals", status_code=303)

    @app.post("/events/{event_id}/visuals/upload", response_class=HTMLResponse)
    async def visuals_upload(request: Request, event_id: int,
                             city_image: UploadFile = File(...)):
        """Ingest a city photo. EXIF (including GPS) is stripped on the way in."""
        conn = connect()
        try:
            event = load_event(conn, event_id)
            raw = await city_image.read()
            os.makedirs(_visuals_dir(event_id), exist_ok=True)
            tmp = os.path.join(_visuals_dir(event_id), "_incoming")
            with open(tmp, "wb") as fh:
                fh.write(raw)
            problem = ""
            try:
                strip_exif(tmp, os.path.join(_visuals_dir(event_id), "city.png"))
            except Exception:
                problem = ("That file could not be read as an image. "
                           "Upload a JPEG or PNG.")
            finally:
                if os.path.exists(tmp):
                    os.remove(tmp)
            return _visuals_page(request, conn, event_id, event, problem=problem)
        finally:
            conn.close()

    @app.get("/events/{event_id}/visuals/library/{image_id}.png")
    def visuals_library_png(event_id: int, image_id: int):
        conn = connect()
        try:
            load_event(conn, event_id)
            image = repo.get_library_image(conn, image_id)
        finally:
            conn.close()
        if image is None or image.event_id != event_id:
            raise HTTPException(status_code=404, detail="No such image.")
        if not os.path.exists(image.path):
            raise HTTPException(status_code=404, detail="Image file is missing.")
        with open(image.path, "rb") as handle:
            return Response(content=handle.read(), media_type="image/png")

    @app.get("/events/{event_id}/visuals/{variant}-{aspect}.png")
    def visual_png(event_id: int, variant: str, aspect: str):
        path = os.path.join(_visuals_dir(event_id), _slugify_event(event_id),
                            f"{variant}-{aspect}.png")
        if not os.path.exists(path):
            raise HTTPException(status_code=404, detail="Not rendered yet.")
        with open(path, "rb") as fh:
            return Response(content=fh.read(), media_type="image/png")

    def _slugify_event(event_id: int) -> str:
        conn = connect()
        try:
            from app.features.visuals import _slug

            return _slug(load_event(conn, event_id).name)
        finally:
            conn.close()

    # ── invite issuance (P2-5 / phase-1 gap) ─────────────────────────────────

    def _invites_page(request: Request, conn, event_id: int, event,
                      issued=None, problem: str = "", form=None):
        """Render the invite desk. ``form`` carries back what was typed, so a
        validation failure never makes the coordinator retype everything."""
        return templates.TemplateResponse(request, "invites.html", {
            "event": event,
            "steps": nav_steps(conn, event_id, "invites"),
            "event_id": event_id,
            "attendees": repo.list_attendees(conn, event_id),
            "issued": issued,
            "problem": problem,
            "form": form or {},
        })

    @app.get("/events/{event_id}/invites", response_class=HTMLResponse)
    def invites_view(request: Request, event_id: int):
        conn = connect()
        try:
            return _invites_page(request, conn, event_id,
                                 load_event(conn, event_id))
        finally:
            conn.close()

    @app.post("/events/{event_id}/invites", response_class=HTMLResponse)
    def invites_issue(request: Request, event_id: int,
                      full_name: str = Form(""), email: str = Form(""),
                      title: str = Form(""), company: str = Form(""),
                      is_vip: str = Form("")):
        typed = {"full_name": full_name, "email": email, "title": title,
                 "company": company, "is_vip": is_vip}
        conn = connect()
        try:
            event = load_event(conn, event_id)
            try:
                person = issue_invitation(
                    conn, _signing_secret(), event_id, full_name=full_name,
                    email=email, title=title, company=company,
                    is_vip=is_vip.strip() not in ("", "0", "false"))
            except ValueError as exc:
                return _invites_page(request, conn, event_id, event,
                                     problem=str(exc), form=typed)
            conn.commit()
            return _invites_page(request, conn, event_id, event, issued=person)
        finally:
            conn.close()

    @app.get("/events/{event_id}/invites/{attendee_id}/qr.png")
    def invite_qr(event_id: int, attendee_id: int):
        """The credential as a scannable image, so it can be emailed or printed."""
        conn = connect()
        try:
            load_event(conn, event_id)
            person = repo.get_attendee(conn, attendee_id)
            if person is None or person.event_id != event_id:
                raise HTTPException(status_code=404, detail="No such invitee.")
            if not person.checkin_code:
                raise HTTPException(status_code=404,
                                    detail="No credential has been issued yet.")
        finally:
            conn.close()
        import io

        import qrcode

        buf = io.BytesIO()
        qrcode.make(person.checkin_code).save(buf, format="PNG")
        return Response(content=buf.getvalue(), media_type="image/png")

    # ── check-in (day-of operation) ──────────────────────────────────────────

    def _checkin_page(request: Request, conn, event_id: int, event,
                      scan_state: Optional[str] = None, scan_name: str = "",
                      attendee=None, problem: str = "",
                      manual_matches=None, manual_truncated: bool = False,
                      manual_query: str = ""):
        # A VIP banner only on a NEW arrival: re-announcing on a repeat scan
        # would train the desk to ignore it.
        vip = bool(attendee and attendee.is_vip and scan_state == STATE_VALID)
        # P5-9-light: display-only gating. Name the check-in staff for THIS
        # event (per-event can_check_in), excluding erased people. This signals
        # assignment; it never hides the roster from the coordinator.
        assignees = []
        for row in repo.event_staff_rows(conn, event_id):
            if not row["can_check_in"]:
                continue
            person = repo.get_person(conn, row["person_id"])
            if person and not person.is_erased:
                assignees.append(person.display_name)
        return templates.TemplateResponse(request, "checkin.html", {
            "event": event,
            "steps": nav_steps(conn, event_id, "checkin"),
            "event_id": event_id,
            "attendees": repo.list_attendees(conn, event_id),
            "scan_state": scan_state,
            "scan_name": scan_name,
            "scan_vip": vip,
            "scan_company": getattr(attendee, "company", None) if vip else None,
            "checkin_assignees": sorted(assignees),
            "manual_matches": manual_matches or [],
            "manual_truncated": manual_truncated,
            "manual_query": manual_query,
            "problem": problem,
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
                                 scan_state=state, scan_name=name,
                                 attendee=attendee)
        finally:
            conn.close()

    @app.post("/events/{event_id}/checkin/email", response_class=HTMLResponse)
    def checkin_email(request: Request, event_id: int, email: str = Form("")):
        """Check in an invited guest who arrived without their code."""
        conn = connect()
        try:
            event = load_event(conn, event_id)
            try:
                state, attendee = check_in_by_email(conn, event_id, email)
            except ValueError as exc:
                return _checkin_page(request, conn, event_id, event,
                                     problem=str(exc))
            conn.commit()
            return _checkin_page(request, conn, event_id, event,
                                 scan_state=state,
                                 scan_name=attendee.full_name if attendee else "",
                                 attendee=attendee)
        finally:
            conn.close()

    @app.post("/events/{event_id}/checkin/walkin", response_class=HTMLResponse)
    def checkin_walkin(request: Request, event_id: int,
                       full_name: str = Form(""), email: str = Form(""),
                       title: str = Form(""), company: str = Form(""),
                       is_vip: str = Form("")):
        conn = connect()
        try:
            event = load_event(conn, event_id)
            try:
                attendee = register_walk_in(
                    conn, event_id, full_name=full_name, email=email,
                    title=title, company=company,
                    is_vip=is_vip.strip() not in ("", "0", "false"))
            except ValueError as exc:
                # Render the desk with the reason rather than a raw 400: the
                # person is standing there and the operator needs to fix it now.
                return _checkin_page(request, conn, event_id, event,
                                     problem=str(exc))
            conn.commit()
            return _checkin_page(request, conn, event_id, event,
                                 scan_state=STATE_VALID,
                                 scan_name=attendee.full_name or "",
                                 attendee=attendee)
        finally:
            conn.close()

    @app.post("/events/{event_id}/checkin/manual/lookup",
              response_class=HTMLResponse)
    def checkin_manual_lookup(request: Request, event_id: int,
                              query: str = Form("")):
        """Facilitator lookup of an EXISTING invitee (P5-8).

        Returns a candidate list rendered on the desk. Never creates a record;
        an empty result is a steel 'no match' note, not a walk-in.
        """
        conn = connect()
        try:
            event = load_event(conn, event_id)
            matches, truncated = find_invitee_truncated(
                conn, event_id, query, limit=10)
            return _checkin_page(request, conn, event_id, event,
                                 manual_matches=matches,
                                 manual_truncated=truncated,
                                 manual_query=query)
        finally:
            conn.close()

    @app.post("/events/{event_id}/checkin/manual",
              response_class=HTMLResponse)
    def checkin_manual_mark(request: Request, event_id: int,
                           attendee_id: int = Form(0)):
        """Mark an existing invitee arrived, recorded as method=manual (P5-8)."""
        conn = connect()
        try:
            event = load_event(conn, event_id)
            try:
                state, attendee = manual_check_in(
                    conn, event_id, attendee_id, actor="facilitator")
            except ValueError as exc:
                return _checkin_page(request, conn, event_id, event,
                                     problem=str(exc))
            conn.commit()
            return _checkin_page(request, conn, event_id, event,
                                 scan_state=state,
                                 scan_name=attendee.full_name if attendee else "",
                                 attendee=attendee)
        finally:
            conn.close()

    return app


app = create_app(os.environ.get("DB_PATH", "events.db"))
