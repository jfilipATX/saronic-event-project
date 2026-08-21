#!/usr/bin/env python3
"""Discover which Claude models this key can actually reach.

Uses the models *listing* endpoint, which costs nothing — no tokens, no
completion. Guessing a model id and finding out mid-pass is the failure we are
avoiding: a 404 from ``messages.create`` looks like a broken integration when it
is really an entitlement mismatch.

    .venv/bin/python scripts/probe_models.py

Prints the models available to the key, flags whether the configured
ANTHROPIC_MODEL is among them, and suggests the best available default.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import load_config

_ENDPOINT = "https://api.anthropic.com/v1/models?limit=100"

#: Preference order when suggesting a default: capability first, then cost.
_PREFERENCE = ("opus", "sonnet", "haiku")


def main() -> int:
    cfg = load_config()
    if not cfg.anthropic_api_key:
        print("ANTHROPIC_API_KEY is not set — add it to .env first.", file=sys.stderr)
        return 2

    req = urllib.request.Request(_ENDPOINT, headers={
        "x-api-key": cfg.anthropic_api_key,
        "anthropic-version": "2023-06-01",
        "accept": "application/json",
        "user-agent": "SaronicEventTool/1.0",
    })
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            payload = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        body = exc.read().decode()[:200]
        if exc.code == 401:
            print("401 — the key is invalid or expired.", file=sys.stderr)
        else:
            print(f"HTTP {exc.code}: {body}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"Could not reach the API: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    models = [m.get("id", "") for m in payload.get("data", []) if m.get("id")]
    if not models:
        print("The key authenticated but no models are listed for it.", file=sys.stderr)
        return 1

    print(f"{len(models)} model(s) available to this key:\n")
    for mid in models:
        print(f"  {mid}")

    configured = cfg.anthropic_model
    print(f"\nconfigured ANTHROPIC_MODEL: {configured}")
    if configured in models:
        print("  -> available. No change needed.")
        return 0

    print("  -> NOT available to this key; the real pass would fail with 404.")
    suggestion = None
    for family in _PREFERENCE:
        matches = sorted((m for m in models if family in m), reverse=True)
        if matches:
            suggestion = matches[0]
            break
    if suggestion:
        print(f"\nSuggested: set ANTHROPIC_MODEL={suggestion} in .env")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
