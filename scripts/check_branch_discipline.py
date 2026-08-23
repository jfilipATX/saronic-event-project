#!/usr/bin/env python3
"""Branch-discipline gate (H2).

Enforces the ratified rule: a *behavioral* change (anything outside the
allowed direct-to-main paths) must land on main via a merge of a reviewed
branch, not as a direct commit. This turns the policy from a prose note into a
gate — the recurring drift this phase kept paying for when it was manual.

What is allowed as a DIRECT (non-merge) commit to main:
  - docs/ (the phase summaries, specs, etc.)
  - README.md and other root *.md
  - .gitignore, .env.example
  - assets/fonts/, assets/press-kit/, assets/images/ (brand assets)
  - screenshots/  (evidence imagery)

Everything else (app/, tests/ touching behavior, scripts/ logic, etc.) must come
through a merge commit. Merge commits are always allowed regardless of paths,
because they represent a reviewed branch landing — the sweep already ran there.

Usage (called by the pre-push hook):
  check_branch_discipline.py <local_ref> <local_sha> <remote_ref> <remote_sha>
Returns exit 0 (allowed) or 1 (blocked, with a reason on stderr).
"""
import subprocess
import sys

# Paths that may be committed DIRECTLY to main (docs, brand assets, the
# readme-claims fix itself). A change touching any OTHER path is behavioral
# and must arrive via a merge of a branch.
ALLOWED_PREFIXES = (
    "docs/",
    "assets/fonts/",
    "assets/press-kit/",
    "assets/images/",
    "screenshots/",
)
ALLOWED_EXACT = (
    "README.md",
    ".gitignore",
    ".env.example",
)


def _changed_files(remote_sha: str, local_sha: str) -> list[str]:
    # Commits in remote_sha..local_sha (the ones about to be pushed).
    base = remote_sha if remote_sha != "0" * 40 else ""
    if base:
        rng = f"{base}..{local_sha}"
    else:
        rng = local_sha
    out = subprocess.run(
        ["git", "diff", "--name-only", rng],
        capture_output=True, text=True, check=True,
    ).stdout.splitlines()
    # Also catch files in the very first commit (no base).
    if not base:
        out = subprocess.run(
            ["git", "show", "--name-only", "--pretty=format:", local_sha],
            capture_output=True, text=True, check=True,
        ).stdout.splitlines()
    return [f for f in out if f]


def _is_merge(sha: str) -> bool:
    parents = subprocess.run(
        ["git", "rev-list", "--parents", "-n", "1", sha],
        capture_output=True, text=True, check=True,
    ).stdout.split()
    return len(parents) > 2  # first token is the commit itself


def _is_allowed_path(path: str) -> bool:
    if path in ALLOWED_EXACT:
        return True
    return any(path.startswith(p) for p in ALLOWED_PREFIXES)


def main(argv: list[str]) -> int:
    if len(argv) < 5:
        # Not enough args (e.g. a tag push) — defer to other checks.
        return 0
    local_ref, local_sha, remote_ref, remote_sha = argv[1:5]
    if remote_ref != "refs/heads/main":
        return 0  # only gate direct-to-main pushes
    if local_sha == "0" * 40:
        return 0  # branch deletion, harmless

    commits = subprocess.run(
        ["git", "rev-list", f"{remote_sha}..{local_sha}"],
        capture_output=True, text=True, check=True,
    ).stdout.splitlines()

    blocked = []
    for sha in commits:
        if _is_merge(sha):
            continue  # reviewed branch landing — allowed
        files = _changed_files(sha, sha)
        bad = [f for f in files if not _is_allowed_path(f)]
        if bad:
            blocked.append((sha[:7], bad))

    if blocked:
        print("PUSH BLOCKED: direct-to-main behavioral commits detected.",
              file=sys.stderr)
        print("Behavioral changes must land on main via a merge of a reviewed "
              "branch (e.g. feature/hardening), not as direct commits.",
              file=sys.stderr)
        for sha, bad in blocked:
            print(f"  {sha}: {', '.join(bad)}", file=sys.stderr)
        print("Allowed direct-to-main paths: docs/, assets/fonts/, "
              "assets/images/, screenshots/, README.md, .gitignore, "
              ".env.example.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
