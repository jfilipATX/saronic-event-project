"""The README's checkable numbers must match reality.

Written after a commit whose whole purpose was fixing a stale test count fixed
only one of its two occurrences. A human re-reading the file is not a reliable
check: the number appears in more than one section, and the person editing is
the same person who already believes it is correct.

Scope is deliberately narrow — claims the repository can verify about itself.
Prose is not the target; assertable integers are.
"""
from __future__ import annotations

import pathlib
import re
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
README = REPO / "README.md"


#: Lines that mention a count while NOT claiming the suite size — prose about a
#: past bug ("33 tests passed while a real scrape returned 500") is history, not
#: a claim about this checkout. Matching it would make the guard cry wolf, and a
#: guard that cries wolf gets deleted.
_HISTORICAL = ("while a real scrape", "exercised the empty")


def _stated_test_counts() -> list[int]:
    """Every claim about how large THIS suite is."""
    counts: list[int] = []
    for line in README.read_text(encoding="utf-8").splitlines():
        if any(marker in line for marker in _HISTORICAL):
            continue
        counts += [int(m) for m in
                   re.findall(r"\*?\*?(\d{2,5})\*?\*? (?:tests|passed)", line)]
    return counts


def _actual_test_count() -> int:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q",
         str(REPO / "tests")],
        capture_output=True, text=True, cwd=REPO, timeout=300,
    )
    match = re.search(r"(\d+) tests? collected", result.stdout)
    if match:
        return int(match.group(1))
    raise AssertionError(f"could not read a collected count:\n{result.stdout[-500:]}")


class TestReadmeTestCount:
    def test_the_readme_states_a_count_at_all(self):
        assert _stated_test_counts(), \
            "the README should state the suite size — an unstated claim cannot go stale"

    def test_every_stated_count_matches_the_suite(self):
        """ALL of them, not the first one — the point of this test."""
        actual = _actual_test_count()
        stated = _stated_test_counts()
        wrong = [n for n in stated if n != actual]
        assert not wrong, (
            f"README claims {wrong} tests in {len(wrong)} place(s); the suite "
            f"collects {actual}. Update every occurrence, not just the first."
        )

    def test_the_counts_agree_with_each_other(self):
        stated = _stated_test_counts()
        assert len(set(stated)) <= 1, (
            f"the README states conflicting counts {sorted(set(stated))} — "
            f"a reader cannot tell which is true"
        )


class TestReadmeSpendClaim:
    """Spend is the other checkable number, and it is evidence-backed."""

    def test_the_stated_spend_matches_the_evidence(self):
        import json

        summary = REPO / "generated" / "claude-pass" / "summary.json"
        if not summary.exists():
            return  # evidence pass not run in this checkout
        recorded = json.loads(summary.read_text())
        total = recorded.get("total_usd") or recorded.get("total_spend_usd")
        if total is None:
            return
        text = README.read_text(encoding="utf-8")
        claims = re.findall(r"\$(\d+\.\d{2,4})", text)
        assert f"{total:.4f}" in claims or f"{total:.2f}" in claims, (
            f"README spend claims {claims} but the evidence records ${total}"
        )
