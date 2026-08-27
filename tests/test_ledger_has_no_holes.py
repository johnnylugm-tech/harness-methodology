"""Every round that shipped commits left an adjudication behind.

Round 80 站9. `docs/PROPOSAL_ADJUDICATIONS.md` opens by stating its own
protocol:

    任何新 Gap 分析報告或優化提案,執行前**先查此賬本**。若主張已有條目,且該條目的
    re-open condition 尚未滿足,**引用條目編號駁回,不重複查證**。

That protocol is the repo's defence against re-deriving what it has already
judged, and it has exactly one prerequisite: the entry has to exist. Round 58
shipped `f4be095c` and `e37151ee` and wrote none — the only such round of the
72 that have commits. This is what stops a second.

ROUND 80 IS THE PROTOCOL'S OWN COUNTEREXAMPLE, AND IT IS RECORDED AS ONE

Three of the findings this round opened with were already adjudicated:
`spec_coverage`'s row layer had been made single-source by R74 站3 (which
harness_bridge.py imports from, measurably), the four FR-test-filename
derivations had been MEASURED by R77 站4 as agreeing on every id
`canonical_form` can produce, and the "expectation comes from the code under
test" population is 23 occurrences rather than the 302 a coarse first pass
reported. All three were re-derived from scratch and then withdrawn. The ledger
had the answers; nothing made anyone read them.

That is the argument for making the ledger queryable rather than only
navigable, which this file does not attempt — the 23-plus deferred items with
re-open conditions are prose spread over 5500 lines, and lifting them into a
machine-readable index is an extraction with a real risk of putting words in
past rounds' mouths. Recorded in Round 80's own "not doing" table with its
re-open condition instead.

WHY ROUNDS 1-13 ARE EXEMPT

The ledger begins at Round 14 — its earliest section carries the `{#round-14}`
anchor and the title "Gap 報告 #1". The rounds before it predate the document;
their absence is where the ledger starts, not a hole in it.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

pytestmark = [pytest.mark.core]

REPO = Path(__file__).resolve().parents[1]
LEDGER = REPO / "docs" / "PROPOSAL_ADJUDICATIONS.md"

#: The ledger starts at Round 14. Everything before it predates the document.
_LEDGER_STARTS_AT = 14

#: Highest round number a commit subject may plausibly name. Guards against a
#: version string or a byte count being read as a round.
_MAX_ROUND = 200

_HEADING = re.compile(r"^#{1,3} Round (\d+)", re.MULTILINE)
_IN_SUBJECT = re.compile(r"[Rr]ound ?-?(\d+)\b|\bR(\d+) 站")


def _rounds_with_sections() -> "set[int]":
    return {int(m.group(1)) for m in _HEADING.finditer(
        LEDGER.read_text(encoding="utf-8"))}


def _rounds_with_commits() -> "set[int]":
    log = subprocess.run(  # nosec B603 B607
        ["git", "-C", str(REPO), "log", "--pretty=format:%s"],
        capture_output=True, text=True, check=True, timeout=120,
    ).stdout
    found: set[int] = set()
    for match in _IN_SUBJECT.finditer(log):
        number = int(match.group(1) or match.group(2))
        if _LEDGER_STARTS_AT <= number <= _MAX_ROUND:
            found.add(number)
    return found


def test_every_round_that_shipped_commits_was_adjudicated():
    missing = sorted(_rounds_with_commits() - _rounds_with_sections())
    assert not missing, (
        "these rounds shipped commits and left no adjudication section in "
        f"{LEDGER.relative_to(REPO)}:\n  "
        + "\n  ".join(f"Round {n}" for n in missing)
        + "\n\nThe ledger's own protocol is to consult it before re-opening a "
          "question. A round with no entry is a question that will be asked "
          "again from scratch — write the section, and if a fact is no longer "
          "recoverable say so rather than inventing it."
    )


def test_the_ledger_is_not_running_ahead_of_the_work():
    """A section for a round that shipped nothing is the same defect mirrored.

    It would mean the record describes work no commit carries — the shape
    Rounds 36, 39 and 64 each measured in a different layer (a statement that
    outlived, or preceded, the thing it described).
    """
    phantom = sorted(
        n for n in _rounds_with_sections() - _rounds_with_commits()
        if n >= _LEDGER_STARTS_AT
    )
    assert not phantom, (
        "these rounds have an adjudication section and no commit names them:\n"
        "  " + "\n  ".join(f"Round {n}" for n in phantom)
    )


def test_the_scan_reads_both_heading_depths():
    """The hole this file closes was nearly missed by reading only `## Round`.

    Round 80's first pass grepped `^## Round` and reported four unadjudicated
    rounds. Three of them (42, 43 and — inside Round 77's section — 76) were
    adjudicated under a single `#`. A guard that sees one spelling of the
    heading would have made a false hole permanent and hidden the real one.
    """
    sections = _rounds_with_sections()
    assert {42, 43}.issubset(sections), (
        "Rounds 42 and 43 are written under a single-# heading; a scan that "
        "misses them is reading the document's formatting, not its content"
    )
