#!/usr/bin/env python3
"""Pre-push secret audit.

Going public is one-way: once a commit with a key is pushed, rotating the key is
the only real remedy. This scans the ENTIRE commit history, not just the working
tree, because `git ls-files` says nothing about what an earlier commit contains.

    .venv/bin/python scripts/audit_secrets.py

Exit 0 = clean, 1 = findings. Intended to run before every push.
"""
from __future__ import annotations

import re
import subprocess
import sys

#: Patterns for *values*, not variable names. Matching `ANTHROPIC_API_KEY=` alone
#: would flag config.py and .env.example, which are supposed to name the vars.
PATTERNS: list[tuple[str, str]] = [
    ("Anthropic key", r"sk-ant-[A-Za-z0-9_\-]{20,}"),
    ("OpenAI-style key", r"\bsk-[A-Za-z0-9]{32,}"),
    ("GitHub token", r"\b(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}"),
    ("GitHub fine-grained token", r"\bgithub_pat_[A-Za-z0-9_]{50,}"),
    ("AWS access key", r"\bAKIA[0-9A-Z]{16}\b"),
    ("Private key block", r"-----BEGIN (RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----"),
    ("Assigned secret value", r"(?i)(api_key|apikey|secret|token|password)\s*[=:]\s*['\"][A-Za-z0-9/+_\-]{24,}['\"]"),
    ("Env-file secret value", r"(?i)^\+?\s*[A-Z_]*(API_KEY|SECRET|TOKEN|PASSWORD)=[A-Za-z0-9/+_\-]{16,}"),
    ("git-credentials URL", r"https://[^/\s:]+:[^@\s]{8,}@"),
]

#: Paths whose content is documentation of the *shape* of secrets, not secrets.
ALLOWLIST_PATHS = (".env.example",)

#: Substrings that mark a match as an obvious non-secret (test fixtures,
#: placeholders). Deliberately narrow: a real key would never contain these, and
#: anything broader would start hiding genuine leaks.
NON_SECRET_MARKERS = (
    "not-a-real", "test-", "-test", "example", "dummy", "fake", "placeholder",
    "your-key-here", "changeme", "xxxxx",
)


def _is_obvious_placeholder(snippet: str) -> bool:
    low = snippet.lower()
    return any(marker in low for marker in NON_SECRET_MARKERS)


def run(args: list[str]) -> str:
    return subprocess.run(args, capture_output=True, text=True, check=False).stdout


def scan(label: str, text: str) -> list[str]:
    findings = []
    for name, pattern in PATTERNS:
        for match in re.finditer(pattern, text, re.MULTILINE):
            snippet = match.group(0)
            if _is_obvious_placeholder(snippet):
                continue
            # Never print the secret itself; a leaked audit log is still a leak.
            redacted = snippet[:6] + "…" + f"[{len(snippet)} chars]"
            findings.append(f"{label}: {name} -> {redacted}")
    return findings


def main() -> int:
    findings: list[str] = []

    # 1. Working tree (tracked files only).
    for path in run(["git", "ls-files"]).splitlines():
        if path.endswith(ALLOWLIST_PATHS):
            continue
        try:
            with open(path, "r", errors="ignore") as fh:
                findings += scan(f"worktree:{path}", fh.read())
        except (OSError, UnicodeDecodeError):
            continue

    # 2. Every commit's full diff — the part people forget.
    history = run(["git", "log", "--all", "-p", "--no-color"])
    findings += scan("history", history)

    # 3. Files that must never be tracked at all.
    tracked = set(run(["git", "ls-files"]).splitlines())
    for forbidden in (".env", ".git-credentials"):
        if forbidden in tracked:
            findings.append(f"FATAL: {forbidden} is tracked by git")

    if findings:
        print("SECRET AUDIT: FINDINGS\n")
        for f in sorted(set(findings)):
            print(" ", f)
        print(f"\n{len(set(findings))} finding(s). Do not push until resolved.")
        return 1

    print("SECRET AUDIT: clean")
    print(f"  scanned {len(tracked)} tracked files + full commit history")
    print("  no key material, tokens, or credential URLs found")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
