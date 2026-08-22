"""CSV roster import with a column-mapping preview (P2-5).

Rosters arrive as spreadsheets from other people's systems, so the governing
rule is that **bad rows are reported, never fatal**. An all-or-nothing import
punishes the coordinator for someone else's data entry, and the realistic
outcome is that they give up and type 200 names by hand.

Two properties beyond parsing:

* Nothing is written until the coordinator commits. ``preview_csv`` proposes;
  ``apply_roster`` acts.
* A duplicate email is not a new person. Roster files get re-sent with additions,
  and re-importing must not double the room.
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from typing import Dict, List

from app.db import repository as repo
from app.db.models import Attendee

#: Fields a row can be mapped onto. "ignore" is explicit rather than absent, so
#: the UI can show a coordinator that a column was deliberately dropped.
MAPPABLE_FIELDS = ("full_name", "email", "title", "company", "vip", "ignore")

#: Without these an attendee cannot be invited or checked in.
REQUIRED_FIELDS = ("full_name", "email")

#: Header spellings seen in real exports, normalised for matching.
_HEADER_HINTS = {
    "full_name": ("fullname", "name", "attendee", "attendeename", "guest",
                  "guestname", "firstnamelastname"),
    "email": ("email", "emailaddress", "mail", "workemail", "contactemail"),
    "title": ("title", "jobtitle", "role", "position"),
    "company": ("company", "organisation", "organization", "org", "employer",
                "affiliation"),
    "vip": ("vip", "isvip", "vipflag", "priority"),
}

_TRUTHY = {"yes", "y", "true", "1", "vip", "x"}

#: Deliberately permissive. Rejecting unusual but valid addresses is worse than
#: accepting a typo: the coordinator can see and fix a typo, but a silently
#: dropped guest is discovered at the door.
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@dataclass
class SkippedRow:
    line: int
    reason: str
    values: Dict[str, str] = field(default_factory=dict)


@dataclass
class RosterPreview:
    headers: List[str] = field(default_factory=list)
    sample_rows: List[List[str]] = field(default_factory=list)
    mapping: Dict[str, str] = field(default_factory=dict)
    total_rows: int = 0
    missing_required: List[str] = field(default_factory=list)

    @property
    def can_import(self) -> bool:
        return self.total_rows > 0 and not self.missing_required


@dataclass
class ImportOutcome:
    imported: int = 0
    duplicates: int = 0
    skipped: List[SkippedRow] = field(default_factory=list)

    @property
    def summary(self) -> str:
        parts = [f"{self.imported} attendee{'s' if self.imported != 1 else ''} imported"]
        if self.duplicates:
            parts.append(f"{self.duplicates} already on the roster")
        if self.skipped:
            parts.append(f"{len(self.skipped)} row"
                         f"{'s' if len(self.skipped) != 1 else ''} skipped")
        return ", ".join(parts) + "."


def _normalise(header: str) -> str:
    return re.sub(r"[^a-z0-9]", "", header.strip().lower())


def guess_mapping(headers: List[str]) -> Dict[str, str]:
    """Propose a field for each header. First match wins; the rest are ignored.

    Auto-guessing then letting the coordinator correct beats making them set
    every dropdown — same contract, far less clicking.
    """
    mapping: Dict[str, str] = {}
    taken: set = set()
    for header in headers:
        norm = _normalise(header)
        chosen = "ignore"
        for field_name, hints in _HEADER_HINTS.items():
            if field_name in taken:
                continue
            if norm in hints:
                chosen = field_name
                taken.add(field_name)
                break
        mapping[header] = chosen
    return mapping


def _reader(text: str):
    """A csv reader that copes with what spreadsheets actually emit."""
    text = text.lstrip("\ufeff")          # Excel's UTF-8 BOM
    if not text.strip():
        return None
    sample = text[:2048]
    delimiter = ","
    try:
        delimiter = csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        pass
    return csv.reader(io.StringIO(text), delimiter=delimiter)


def preview_csv(text: str, sample_size: int = 5) -> RosterPreview:
    """Parse enough of the file to show the coordinator what will happen."""
    reader = _reader(text)
    if reader is None:
        return RosterPreview(missing_required=list(REQUIRED_FIELDS))
    try:
        headers = next(reader)
    except StopIteration:
        return RosterPreview(missing_required=list(REQUIRED_FIELDS))

    headers = [h.strip() for h in headers]
    mapping = guess_mapping(headers)
    sample: List[List[str]] = []
    total = 0
    for row in reader:
        if not any(cell.strip() for cell in row):
            continue
        total += 1
        if len(sample) < sample_size:
            sample.append([cell.strip() for cell in row])

    mapped = set(mapping.values())
    missing = [f for f in REQUIRED_FIELDS if f not in mapped]
    return RosterPreview(headers=headers, sample_rows=sample, mapping=mapping,
                         total_rows=total, missing_required=missing)


def apply_roster(conn, event_id: int, text: str,
                 mapping: Dict[str, str]) -> ImportOutcome:
    """Import the roster. Skips bad rows, reports them, never raises on them."""
    mapped = set(mapping.values())
    missing = [f for f in REQUIRED_FIELDS if f not in mapped]
    if missing:
        raise ValueError(
            "Map a column to " + " and ".join(missing) + " before importing."
        )

    reader = _reader(text)
    outcome = ImportOutcome()
    if reader is None:
        return outcome
    try:
        headers = [h.strip() for h in next(reader)]
    except StopIteration:
        return outcome

    # Existing emails, so a re-sent roster does not double the room. Erased
    # people have no email, so they cannot (and must not) be matched.
    existing = {
        a.email.lower()
        for a in repo.list_attendees(conn, event_id, include_withdrawn=True)
        if a.email
    }

    for line_number, row in enumerate(reader, start=2):
        if not any(cell.strip() for cell in row):
            continue
        values = {
            mapping.get(header, "ignore"): (row[i].strip() if i < len(row) else "")
            for i, header in enumerate(headers)
        }
        values.pop("ignore", None)

        name = values.get("full_name", "")
        email = values.get("email", "")
        if not name:
            outcome.skipped.append(SkippedRow(line_number, "No name in this row.",
                                              values))
            continue
        if not email:
            outcome.skipped.append(SkippedRow(line_number, "No email address.", values))
            continue
        if not _EMAIL.match(email):
            outcome.skipped.append(
                SkippedRow(line_number, f"{email!r} is not a valid email address.",
                           values))
            continue
        if email.lower() in existing:
            outcome.duplicates += 1
            continue

        repo.add_attendee(conn, Attendee(
            event_id=event_id,
            full_name=name,
            email=email,
            title=values.get("title") or None,
            company=values.get("company") or None,
            is_vip=values.get("vip", "").strip().lower() in _TRUTHY,
        ))
        existing.add(email.lower())
        outcome.imported += 1

    return outcome
