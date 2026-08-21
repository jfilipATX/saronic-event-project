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
import sqlite3
from dataclasses import dataclass
from typing import List, Optional

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.db import repository as repo, schema_sql_text as sql
from app.features.playbook import STEP_TITLES, compose_playbook, render_markdown
from app.features.workflow import CHAIN, CoordinatorWorkflow

_UI_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui")

#: Stepper labels for the nav. Chain steps first, then the derived views.
_NAV = [(key, STEP_TITLES.get(key, key)) for key in CHAIN] + [("playbook", "Playbook")]


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


def create_app(db_path: str = "events.db") -> FastAPI:
    app = FastAPI(title="Saronic Event Tool")
    app.mount("/static", StaticFiles(directory=os.path.join(_UI_DIR, "static")),
              name="static")
    templates = Jinja2Templates(directory=os.path.join(_UI_DIR, "templates"))

    def connect() -> sqlite3.Connection:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.executescript(sql.SCHEMA)
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

    @app.post("/events")
    def create_event(name: str = Form(...), city: str = Form(...)):
        conn = connect()
        try:
            event_id = CoordinatorWorkflow(conn).start_event(name=name, city=city)
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
            return templates.TemplateResponse(request, "step.html", {
                "event": event,
                "steps": nav_steps(conn, event_id, step_key),
                "step": step_key,
                "step_key": step_key,
                "step_index": index,
                "step_total": len(CHAIN),
                "decision": decision,
                "chosen_key": decision.chosen_key,
                "event_id": event_id,
            })
        finally:
            conn.close()

    @app.post("/events/{event_id}/decide")
    def decide(event_id: int, step: str = Form(...), key: str = Form(...)):
        conn = connect()
        try:
            load_event(conn, event_id)
            wf = CoordinatorWorkflow(conn)
            try:
                wf.choose(event_id, step=step, key=key)
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

    return app


app = create_app(os.environ.get("DB_PATH", "events.db"))
