"""Real-Claude evidence pass.

Runs every Claude-driven surface once against the live API, captures the actual
outputs to ``generated/claude-pass/``, and reports SpendMeter totals.

Safety: refuses to run unless USE_REAL_CLAUDE=1 and a key are both present, and
runs under a caller-supplied spend cap (default $5) that is far below the
project limit — an evidence pass should never be able to burn the budget.

    USE_REAL_CLAUDE=1 .venv/bin/python scripts/real_claude_pass.py [--limit 5]
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.claude.client import RealClaudeClient, get_client
from app.claude.errors import ClaudeError
from app.claude.meter import SpendMeter
from app.config import load_config
from app.db import repository as repo, schema_sql_text as sql
from app.features.playbook import compose_playbook, render_markdown
from app.features.workflow import CoordinatorWorkflow

OUT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "generated", "claude-pass",
)

SYSTEM = (
    "You are assisting a human event coordinator planning a corporate event for "
    "Saronic, a defense-technology company building autonomous surface vessels "
    "(Corsair, Marauder). You never make the decision. You present options with "
    "the reasoning behind each so the coordinator can choose. Be concise, "
    "concrete, and honest about trade-offs."
)


def _surfaces(playbook_md: str) -> list[tuple[str, str]]:
    """(name, prompt) for each Claude-driven surface in the product."""
    return [
        ("event_type_classification",
         "An internal request says: 'We want to show the Corsair to defense "
         "primes and DoD program offices in Austin this fall, ideally with press "
         "present.' Classify this into one of: convention, company-hosted, panel, "
         "other. Give the classification, then one sentence of reasoning, then "
         "name the strongest alternative classification and why someone might "
         "choose it instead."),
        ("audience_estimate_reasoning",
         "For a company-hosted Saronic product launch in Austin, Texas targeting "
         "defense primes, DoD program offices, and trade press: propose a "
         "conservative, a baseline, and an ambitious attendance estimate. For "
         "each, give the number and one sentence justifying it. Note explicitly "
         "what would have to be true for the ambitious number to hold."),
        ("venue_fit_reasoning",
         "A coordinator is choosing between the Austin Convention Center "
         "(capacity 9,000) and Palmer Events Center (capacity 3,000) for an "
         "event with an estimated 3,600 attendees. Palmer is under capacity but "
         "materially cheaper. Lay out the trade-off in under 120 words. Do not "
         "recommend one; present what each choice costs the coordinator."),
        ("slide_copy",
         "Write title-slide copy for a Saronic event deck: an event named "
         "'Saronic Fleet Week' in Austin for 3,600 attendees. Give a headline of "
         "at most six words and a one-line subhead. The brand voice is spare, "
         "technical, and understated - no marketing superlatives."),
        ("playbook_summary",
         "Here is an event playbook assembled from a coordinator's decisions:\n\n"
         f"{playbook_md}\n\n"
         "Write a 3-sentence executive summary for a VP who has 30 seconds. "
         "Lead with what was decided, then the single biggest open risk."),
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=float, default=5.0,
                        help="Hard spend cap for this pass in USD (default 5).")
    args = parser.parse_args()

    cfg = load_config()
    if not cfg.anthropic_api_key:
        print("ANTHROPIC_API_KEY is not set — nothing to do.", file=sys.stderr)
        return 2
    if not cfg.use_real_claude:
        print("USE_REAL_CLAUDE is not enabled. Re-run with USE_REAL_CLAUDE=1.",
              file=sys.stderr)
        return 2

    # Deliberately tighter than the project limit: an evidence pass must not be
    # able to spend the budget even if a prompt loops.
    meter = SpendMeter(limit_usd=min(args.limit, cfg.anthropic_spend_limit))
    client = get_client(cfg, meter)
    if not isinstance(client, RealClaudeClient):
        print("Factory did not return the real client; aborting.", file=sys.stderr)
        return 2

    # Build a real playbook from the mock-path workflow to feed the summary prompt.
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(sql.SCHEMA)
    wf = CoordinatorWorkflow(conn)
    eid = wf.start_event(name="Saronic Fleet Week", city="Austin")
    wf.choose(eid, step="event_type", key="convention")
    wf.choose(eid, step="audience", key="conservative")
    wf.choose(eid, step="venue", key="austin-convention-center")
    playbook_md = render_markdown(compose_playbook(conn, eid))

    os.makedirs(OUT_DIR, exist_ok=True)
    results = []
    print(f"model={cfg.anthropic_model}  cap=${meter.limit}")
    print("=" * 70)

    for name, prompt in _surfaces(playbook_md):
        started = time.time()
        try:
            # Reasoning models spend part of max_tokens on thinking blocks, so
            # this budget must cover thinking AND the answer.
            text = client.complete(system=SYSTEM, prompt=prompt, max_tokens=2500)
            status = "ok"
        except ClaudeError as exc:
            text = f"[FAILED] {type(exc).__name__}: {exc}"
            status = "failed"
        elapsed = round(time.time() - started, 2)

        path = os.path.join(OUT_DIR, f"{name}.md")
        with open(path, "w") as fh:
            fh.write(f"# {name}\n\n**Prompt**\n\n{prompt}\n\n**Response**\n\n{text}\n")
        results.append({"surface": name, "status": status, "seconds": elapsed,
                        "chars": len(text), "spent_after": meter.spent})
        print(f"[{status}] {name}  {elapsed}s  {len(text)} chars  "
              f"spent=${meter.spent}")
        if status == "failed":
            print(f"   {text[:160]}")

    summary = {
        "model": cfg.anthropic_model,
        "cap_usd": meter.limit,
        "spent_usd": meter.spent,
        "remaining_usd": meter.remaining,
        "surfaces": results,
    }
    with open(os.path.join(OUT_DIR, "summary.json"), "w") as fh:
        json.dump(summary, fh, indent=2)

    print("=" * 70)
    print(f"total spend ${meter.spent} of ${meter.limit} cap "
          f"(${meter.remaining} left)")
    print(f"evidence written to {OUT_DIR}")
    return 0 if all(r["status"] == "ok" for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
