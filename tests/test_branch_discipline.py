"""H2 — branch-discipline gate.

The ratified rule: behavioral changes must land on main via a merge of a
reviewed branch, not as a direct commit. Doc/asset-only direct commits are
allowed; merges are always allowed. This test drives the actual
scripts/check_branch_discipline.py against throwaway git repos so it pins the
real gate behavior, not a mock.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile

import pytest

SCRIPT = os.path.join(
    os.path.dirname(__file__), "..", "scripts", "check_branch_discipline.py"
)


def _git(repo: str, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", repo, *args],
        capture_output=True, text=True, check=True,
    ).stdout


def _make_repo() -> str:
    d = tempfile.mkdtemp()
    _git(d, "init", "-q")
    _git(d, "config", "user.name", "t")
    _git(d, "config", "user.email", "t@t")
    _git(d, "checkout", "-q", "-b", "main")
    return d


def _commit(repo: str, name: str, content: str, path: str = "app/x.py") -> str:
    full = os.path.join(repo, path)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w") as f:
        f.write(content)
    _git(repo, "add", path)
    _git(repo, "commit", "-q", "-m", name)
    return _git(repo, "rev-parse", "HEAD").strip()


def _run(local_ref: str, local_sha: str, remote_ref: str, remote_sha: str,
         repo: str) -> int:
    env = dict(os.environ, GIT_DIR=os.path.join(repo, ".git"))
    out = subprocess.run(
        ["python3", SCRIPT, local_ref, local_sha, remote_ref, remote_sha],
        capture_output=True, text=True, cwd=repo, env=env,
    )
    return out.returncode


@pytest.fixture
def repo():
    d = _make_repo()
    yield d
    shutil.rmtree(d, ignore_errors=True)


class TestGate:
    def test_behavioral_direct_commit_to_main_blocked(self, repo):
        sha = _commit(repo, "change app code", "x=1", "app/features/foo.py")
        assert _run("refs/heads/main", sha, "refs/heads/main",
                   "0" * 40, repo) == 1

    def test_doc_only_direct_commit_to_main_allowed(self, repo):
        # Seed one commit so there's a base, then a docs-only commit.
        _commit(repo, "base", "x=1", "app/features/foo.py")
        sha = _commit(repo, "docs only", "words", "docs/phase-5-summary.md")
        base = _git(repo, "rev-parse", "HEAD~1").strip()
        assert _run("refs/heads/main", sha, "refs/heads/main", base, repo) == 0

    def test_readme_only_direct_commit_to_main_allowed(self, repo):
        _commit(repo, "base", "x=1", "app/features/foo.py")
        sha = _commit(repo, "readme fix", "# hi", "README.md")
        base = _git(repo, "rev-parse", "HEAD~1").strip()
        assert _run("refs/heads/main", sha, "refs/heads/main", base, repo) == 0

    def test_asset_font_direct_commit_to_main_allowed(self, repo):
        _commit(repo, "base", "x=1", "app/features/foo.py")
        sha = _commit(repo, "add font", "TTF", "assets/fonts/Archivo.ttf")
        base = _git(repo, "rev-parse", "HEAD~1").strip()
        assert _run("refs/heads/main", sha, "refs/heads/main", base, repo) == 0

    def test_merge_commit_to_main_allowed(self, repo):
        # main has a base; feature branch has a behavioral commit; merge it.
        base = _commit(repo, "base", "x=1", "app/features/foo.py")
        _git(repo, "checkout", "-q", "-b", "feature")
        _commit(repo, "behavioral", "y=2", "app/features/bar.py")
        _git(repo, "checkout", "-q", "main")
        _git(repo, "merge", "-q", "--no-ff", "feature", "-m", "merge feature")
        sha = _git(repo, "rev-parse", "HEAD").strip()
        assert _run("refs/heads/main", sha, "refs/heads/main", base, repo) == 0

    def test_non_main_branch_not_gated(self, repo):
        sha = _commit(repo, "behavioral on feature", "x=1",
                      "app/features/foo.py")
        assert _run("refs/heads/feature", sha, "refs/heads/feature",
                   "0" * 40, repo) == 0
