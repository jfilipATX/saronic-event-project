"""P8-1 — Concierge: a natural-language intake assistant for event managers.

A client event manager opens the Concierge chat and answers in plain language;
the Concierge extracts structured fields and drives the existing venue + run-of-show
machinery. It also accepts free-form edits to an existing event
("move doors to 11am", "change venue to Port Alpha").

Design contract (mirrors the rest of the app):
- All Claude calls go through the same client + spend ledger (`surface="concierge"`).
- Mock mode (no client) is supported: the Concierge still parses when given a
  deterministic extractor, and reports "I need the model" honestly rather than
  inventing data.
- The Concierge only calls the app's own repo/feature layer — it never reaches
  into UI routes. That keeps it decoupled from templates.
- Scope of the first cut: VENUE and RUN OF SHOW only (the two richest, most
  error-prone sections), per the product decision. Everything else returns a
  "not in this assistant's scope yet" message.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from app.claude.client import get_client
from app.db import repository as repo
from app.features import run_of_show as ros
from app.features.venue_scrape import venue_from_facts

# Sections this assistant can handle in the first cut.
SCOPES = ("venue", "run_of_show")

EXTRACT_SYSTEM = """You are an intake assistant for an event planning tool. \
The coordinator replies in natural language. Extract structured fields and \
return ONLY a JSON object (no prose, no markdown fences).

For scope "venue", return:
  {"scope":"venue","action":"add","name":str|null,"city":str|null,
   "state":str|null,"country":str|null,"capacity":int|null,
   "url":str|null,"amenities":[str]}

For scope "run_of_show", return ONE segment:
  {"scope":"run_of_show","action":"add","title":str,"day":str|null,
   "start":str,"end":str,"track":str|null,"kind":str|null,
   "owners":[str],"location":str|null}

If the message is an EDIT to an existing segment (e.g. "move doors open to 11am"),
return:
  {"scope":"run_of_show","action":"edit","match_title":str,
   "field":str,"value":str}
  where field is one of: start, end, title, track, kind, location, owners, day.

If the message is out of scope (audience, slides, check-in, etc.), return:
  {"scope":null,"action":"out_of_scope","note":str}

Never invent a capacity or amenity the coordinator did not state. If a required \
field is missing, return it as null and the assistant will ask a follow-up."""

EDIT_SYSTEM = """You map a free-form edit request on a run-of-show segment to one \
field change. Return ONLY JSON:
  {"match_title":str,"field":str,"value":str}
field is one of: start, end, title, track, kind, location, owners, day.
If the request does not name a segment, match_title is the empty string."""


@dataclass
class ConciergeMessage:
    role: str                       # "user" | "assistant"
    text: str
    data: Optional[Dict[str, Any]] = None


@dataclass
class ConciergeSession:
    """Holds one event manager's conversation. Cheap to construct; callers
    persist messages if they want history across requests."""
    event_id: int
    messages: List[ConciergeMessage] = field(default_factory=list)

    def ask(self, user_text: str, client=None, conn=None) -> ConciergeMessage:
        """Process one user turn. `client` may be None (mock): we then attempt a
        tiny heuristic parse so the flow is exercisable without spend, but we
        surface an honest 'needs model' note for anything non-trivial."""
        self.messages.append(ConciergeMessage(role="user", text=user_text))
        reply = _handle(user_text, self.event_id, client=client, conn=conn)
        self.messages.append(reply)
        return reply


def _handle(text: str, event_id: int, client=None, conn=None) -> ConciergeMessage:
    if client is None:
        # Mock mode: no model. Use a minimal heuristic so the interview can be
        # driven in tests without spend; anything ambiguous returns honest text.
        extracted = _heuristic_extract(text)
        if extracted is None:
            return ConciergeMessage(
                role="assistant",
                text="(Model off — I can't interpret free text without Claude. "
                     "Start the server with a key, or use the forms directly.)")
    else:
        raw = client.complete(
            system=EXTRACT_SYSTEM, prompt=text, max_tokens=400,
            event_id=event_id, surface="concierge")
        extracted = _parse_json(raw)
        if extracted is None:
            return ConciergeMessage(
                role="assistant",
                text="I couldn't parse that. Could you rephrase? For example: "
                     "'Add venue Port Alpha, San Diego, capacity 1200, with "
                     "catering and security' or 'Doors open at 11am on day 1'.")

    scope = extracted.get("scope")
    if scope is None or scope not in SCOPES:
        return ConciergeMessage(
            role="assistant",
            text=extracted.get("note") or "That's outside what I can set up yet "
                 "(venue and run-of-show only for now). You can use the forms "
                 "for the rest.", data=extracted)

    if scope == "venue":
        return _apply_venue(extracted, event_id, conn)
    if scope == "run_of_show":
        return _apply_ros(extracted, event_id, conn)
    return ConciergeMessage(role="assistant", text="Hmm, I didn't catch that.")


# --- venue ---------------------------------------------------------------

def _apply_venue(extracted: Dict[str, Any], event_id: int, conn) -> ConciergeMessage:
    if conn is None:
        return ConciergeMessage(role="assistant",
                                text="(No database connection in this call.)")
    facts: Dict[str, Any] = {}
    if extracted.get("name"):
        facts["venue_name"] = extracted["name"]
    for k in ("city", "state", "country", "url"):
        if extracted.get(k):
            facts[k] = extracted[k]
    cap = extracted.get("capacity")
    if cap is not None:
        try:
            facts["capacity"] = int(cap)
        except (TypeError, ValueError):
            facts["capacity"] = None
    amenities = extracted.get("amenities") or []
    if amenities:
        facts["amenities"] = {a: "yes" for a in amenities}
    try:
        venue = venue_from_facts(facts, facts.get("url", ""))
        repo.add_custom_venue(conn, event_id, venue)
        CoordinatorWorkflow_restage(conn, event_id)
        conn.commit()
    except ValueError as exc:
        return ConciergeMessage(
            role="assistant",
            text=f"I couldn't add that venue: {exc}", data=extracted)
    name = venue.name or "the venue"
    return ConciergeMessage(
        role="assistant",
        text=f"Added {name} to the venue slate"
              + (f" (capacity {venue.capacity})." if venue.capacity else ".")
              + " It's now in the running for the best fit.",
        data=extracted)


def CoordinatorWorkflow_restage(conn, event_id: int) -> None:
    """Thin wrapper so we don't import the whole workflow module at module top."""
    from app.features.workflow import CoordinatorWorkflow
    CoordinatorWorkflow(conn).restage_venue(event_id)


# --- run of show ---------------------------------------------------------

def _apply_ros(extracted: Dict[str, Any], event_id: int, conn) -> ConciergeMessage:
    action = extracted.get("action")
    if action == "edit":
        return _apply_ros_edit(extracted, event_id, conn)
    if conn is None:
        return ConciergeMessage(role="assistant", text="(No database connection.)")
    seg = ros.Segment(
        event_id=event_id,
        title=str(extracted.get("title") or "Untitled"),
        start=_anchor_time(conn, event_id, extracted.get("day"), extracted.get("start") or ""),
        end=_anchor_time(conn, event_id, extracted.get("day"), extracted.get("end") or ""),
        track=str(extracted.get("track") or "Logistics"),
        kind=str(extracted.get("kind") or "logistics"),
        location=str(extracted.get("location") or "") or None,
        notes="",
        owner_ids=_resolve_owners(conn, event_id, extracted.get("owners") or []),
    )
    try:
        ros.validate_segment(seg)
    except ValueError as exc:
        return ConciergeMessage(role="assistant",
                                text=f"That segment didn't validate: {exc}",
                                data=extracted)
    repo.add_segment(conn, seg)
    conn.commit()
    return ConciergeMessage(
        role="assistant",
        text=f"Added run-of-show segment \"{seg.title}\" "
              f"({seg.start}–{seg.end}, {seg.kind}).",
        data=extracted)


def _apply_ros_edit(extracted: Dict[str, Any], event_id: int, conn) -> ConciergeMessage:
    if conn is None:
        return ConciergeMessage(role="assistant", text="(No database connection.)")
    match = (extracted.get("match_title") or "").strip().lower()
    field = extracted.get("field")
    value = extracted.get("value")
    segs = repo.list_segments(conn, event_id)
    hit = None
    for s in segs:
        if match and match in (s.title or "").lower():
            hit = s
            break
    if hit is None:
        return ConciergeMessage(
            role="assistant",
            text="I couldn't find a segment matching that description. "
                 "Try naming it, e.g. 'move Doors open to 11am'.",
            data=extracted)
    if field in ("start", "end", "title", "track", "kind", "location", "day"):
        if field in ("start", "end"):
            # Keep the edit on the segment's existing calendar day when present,
            # so "move doors to 11am" stays on the same date as the original.
            existing_day = ""
            other = hit.end if field == "start" else hit.start
            if other:
                existing_day = other.split(" ")[0]
            if existing_day and " " not in str(value):
                value = f"{existing_day} {_norm_time(value)}" if value else value
            elif value:
                value = _anchor_time(conn, event_id, None, value)
        setattr(hit, field, value)
        if field == "day":
            # day edits re-anchor the segment via event_days if needed
            _maybe_set_day(conn, event_id, int(value) if str(value).isdigit() else value, hit)
    elif field == "owners":
        hit.owner_ids = _resolve_owners(conn, event_id,
                                        value if isinstance(value, list) else [value])
    else:
        return ConciergeMessage(role="assistant",
                                text=f"I don't know how to change '{field}'.",
                                data=extracted)
    try:
        ros.validate_segment(hit)
    except ValueError as exc:
        return ConciergeMessage(role="assistant",
                                text=f"That change didn't validate: {exc}",
                                data=extracted)
    repo.update_segment(conn, hit)
    conn.commit()
    return ConciergeMessage(
        role="assistant",
        text=f"Updated \"{hit.title}\": {field} is now {value}.",
        data=extracted)


def _maybe_set_day(conn, event_id: int, day_index, seg) -> None:
    """If a day edit references a day index, ensure that event day exists."""
    try:
        idx = int(day_index)
    except (TypeError, ValueError):
        return
    days = repo.event_days(conn, event_id)
    if not any(d.get("day_index") == idx for d in days):
        # create a placeholder day so the segment has somewhere to live
        from app.features.schedule import DayWindow
        repo.replace_event_days(conn, event_id,
                                list(days) + [DayWindow(day_index=idx,
                                                        date="", open="", close="")])


def _resolve_owners(conn, event_id: int, names: List[str]) -> List[int]:
    ids: List[int] = []
    for n in names:
        if isinstance(n, int):
            ids.append(n)
            continue
        n = str(n).strip()
        if not n:
            continue
        existing = conn.execute(
            "SELECT id FROM people WHERE name=? AND erased_at IS NULL",
            (n,)).fetchone()
        if existing:
            pid = existing["id"]
        else:
            pid = repo.add_person(conn, repo.Person(name=n, role=""))
        repo.assign_staff(conn, event_id, pid, role="", can_check_in=False)
        ids.append(pid)
    return ids


# --- helpers -------------------------------------------------------------

def _anchor_time(conn, event_id: int, day_index, time_str: str) -> str:
    """Turn a bare NL time ('9am', '11:00') into a full datetime the segment
    model accepts, anchored to the event's day (default day 0). If the event
    has no days yet, day 0 is created with today's date so times have an anchor."""
    time_str = _norm_time(time_str)
    if not time_str:
        return ""
    idx = 0
    if day_index is not None:
        try:
            idx = int(day_index)
        except (TypeError, ValueError):
            idx = 0
    days = repo.event_days(conn, event_id)
    day = next((d for d in days if d.day_index == idx), None)
    if day is None:
        from datetime import date as _date
        day = type("D", (), {"date": _date.today().isoformat(), "day_index": idx})()
        repo.replace_event_days(conn, event_id,
                                list(days) + [__import__("app.features.schedule",
                                fromlist=["DayWindow"]).DayWindow(
                                    day_index=idx, date=day.date)])
    return f"{day.date} {time_str}"


def _norm_time(time_str: str) -> str:
    t = (time_str or "").strip().lower()
    if not t:
        return ""
    m = re.match(r"^(\d{1,2})(?::(\d{2}))?\s*(am|pm)?$", t)
    if not m:
        # already a HH:MM-ish string?
        return t
    h = int(m.group(1))
    mm = int(m.group(2) or 0)
    ap = m.group(3)
    if ap == "pm" and h != 12:
        h += 12
    elif ap == "am" and h == 12:
        h = 0
    return f"{h:02d}:{mm:02d}"


def _parse_json(raw: str) -> Optional[Dict[str, Any]]:
    if raw is None:
        return None
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z]*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # tolerate a JSON object buried in prose
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                return None
        return None


def _heuristic_extract(text: str) -> Optional[Dict[str, Any]]:
    """Mock-mode fallback: only handles a couple of obvious phrasings so tests
    can exercise the flow without a model. Returns None for anything unclear."""
    t = text.lower()
    if "add venue" in t or "venue" in t and ("capacity" in t or "san diego" in t):
        m = re.search(r"capacity\s*(\d+)", t)
        return {"scope": "venue", "action": "add",
                "name": _cap_after(t, "venue"), "capacity": int(m.group(1)) if m else None,
                "amenities": ["catering"] if "catering" in t else []}
    if "doors open" in t and "11" in t:
        return {"scope": "run_of_show", "action": "edit",
                "match_title": "doors", "field": "start", "value": "11:00"}
    return None


def _cap_after(t: str, marker: str) -> Optional[str]:
    i = t.find(marker)
    if i < 0:
        return None
    rest = t[i + len(marker):]
    return rest.strip().split(",")[0].strip().title() or None
