"""Two registers of one fact, two retention policies, one checker in between.

Round 45 站0. `doctor` on a copy of taskq-advance at Phase 7 emitted 37
findings, of which **30 were ERROR-level accusations of fabrication**:

    [ERROR] gate1-evidence: g1_p3_fr01.finalized: no FR-01 entry for phase 3
            in .gate1_scores.json — the same run writes both

Every one of them is false. The project passed Gate 2, Gate 3 and Gate 4 and
advanced six times. What actually happened is that two registers of "FR-01
passed Gate 1 in phase 3" have different lifetimes and nobody wrote that down:

  * `record_gate1_score` prunes every phase older than `phase - 1`
    (gate1_evidence.py, "Prunes phases older than … to bound file growth").
  * `.sessi-work/sentinels/*.finalized` are never pruned — taskq-advance has
    120 of them.
  * `verify_finalize_evidence` demands the two agree, and `doctor` runs it for
    every sentinel it can find.

So a project is guaranteed to be accused the moment it is two phases past a
sentinel. `advance-phase` is unaffected — it only ever asks about
`completed_phase`, which is always inside the window (`cli/phase_cmds.py:2418`).

Station 0 measured the premise behind the pruning: the whole unpruned file is
**1,706 bytes** at 10 FRs × 8 phases, and 5,519 at 30 × 9. "Bound file growth"
does not survive contact with the numbers, so the window goes rather than the
checker learning to live with it — and a checker that cannot corroborate says
so instead of accusing (Round 32/35: could-not-measure is not a finding;
Round 39/40: a record that predates a mechanism is not a violation).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.quality_gate.gate1_evidence import (
    GATE1_SCORES_FILE,
    GATE_TIMESTAMPS_FILE,
    record_gate1_score,
    verify_finalize_evidence,
    write_finalize_receipt,
)

pytestmark = [pytest.mark.core]


def _finalize_row(project: Path, phase: int, fr_id: str) -> None:
    path = project / ".methodology" / GATE_TIMESTAMPS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "phase": phase, "gate": 1, "fr_id": fr_id,
            "ts": 1786400000.0, "iso": "2026-08-11T00:00:00+00:00",
            "source": "finalize",
        }) + "\n")


def _receipt(project: Path, phase: int, fr_id: str, score: float = 100.0) -> None:
    result = project / ".methodology" / "gate1_result.json"
    result.parent.mkdir(parents=True, exist_ok=True)
    result.write_text(json.dumps({
        "gate": 1, "phase": phase, "fr_id": fr_id, "verdict": "PASS",
        "overall_score": score, "quality_complete": True,
    }), encoding="utf-8")
    write_finalize_receipt(
        project, gate=1, phase=phase, fr_id=fr_id, score=score,
        result_path=result,
    )


# ── the register keeps what it is asked to keep ─────────────────────────────

def test_a_score_from_five_phases_ago_is_still_on_record(tmp_path):
    """Station 0 premise 2: the file is 1.7 KB unpruned. There is no growth to
    bound, and the pruning is what manufactures the 30 false accusations."""
    (tmp_path / ".methodology").mkdir()
    for phase in range(1, 8):
        record_gate1_score(tmp_path, phase, "FR-01", 100.0)

    scores = json.loads(
        (tmp_path / ".methodology" / GATE1_SCORES_FILE).read_text(encoding="utf-8")
    )
    assert sorted(scores, key=int) == ["1", "2", "3", "4", "5", "6", "7"], (
        "phases were dropped — a sentinel for any of them now reads as a "
        "fabricated result"
    )


# ── the checker distinguishes "cannot corroborate" from "contradicts" ───────

def test_a_phase_the_register_never_knew_is_not_an_accusation(tmp_path):
    """taskq-advance's shape: the whole phase key is absent, so the register
    has nothing to say. Silence is the honest answer, not an ERROR."""
    (tmp_path / ".methodology").mkdir()
    _finalize_row(tmp_path, 3, "FR-01")
    _receipt(tmp_path, 3, "FR-01")
    (tmp_path / ".methodology" / GATE1_SCORES_FILE).write_text(
        json.dumps({"6": {"FR-01": 100.0}, "7": {"FR-01": 100.0}}),
        encoding="utf-8",
    )

    problems = verify_finalize_evidence(tmp_path, 1, 3, "FR-01")

    assert not [p for p in problems if GATE1_SCORES_FILE in str(p)], problems


def test_an_fr_missing_from_a_phase_the_register_does_know_is_an_accusation(
    tmp_path,
):
    """The negative control. When the phase key exists, other FRs finalized in
    it and wrote the register — so this FR's absence is a real contradiction
    and must keep its ERROR."""
    (tmp_path / ".methodology").mkdir()
    _finalize_row(tmp_path, 3, "FR-01")
    _receipt(tmp_path, 3, "FR-01")
    (tmp_path / ".methodology" / GATE1_SCORES_FILE).write_text(
        json.dumps({"3": {"FR-02": 100.0, "FR-03": 100.0}}), encoding="utf-8",
    )

    problems = verify_finalize_evidence(tmp_path, 1, 3, "FR-01")

    assert [p for p in problems if GATE1_SCORES_FILE in str(p)], (
        "FR-02 and FR-03 recorded scores for phase 3 and FR-01 did not — that "
        "is the inconsistency the check exists for"
    )


def test_a_score_that_disagrees_with_the_receipt_is_still_an_accusation(tmp_path):
    """Round 32 站2's content contract must survive this round untouched."""
    (tmp_path / ".methodology").mkdir()
    _finalize_row(tmp_path, 3, "FR-01")
    _receipt(tmp_path, 3, "FR-01", score=100.0)
    (tmp_path / ".methodology" / GATE1_SCORES_FILE).write_text(
        json.dumps({"3": {"FR-01": 62.0}}), encoding="utf-8",
    )

    problems = verify_finalize_evidence(tmp_path, 1, 3, "FR-01")

    assert any("62.0" in str(p) for p in problems), problems
