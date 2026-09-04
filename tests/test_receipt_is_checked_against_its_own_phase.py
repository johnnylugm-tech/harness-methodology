"""The persisted gate result must say which phase produced it.

Round 96. `finalize-gate` patches four harness-computed fields into the
committed per-FR result — `composite_score`, `quality_complete`, `verdict`,
`passed` — because the agent's copy is a floor, not the document (Round 67
站1). `phase` was not among them: it is whatever the agent's
`.sessi-work/gate{N}_result.json` happened to carry.

Measured on taskq-final, `.methodology/gate_results/gate1/FR-01.json`:

    git log            a7396c0  09-02 22:23  Gate1 PASS [phase=3]
                       70a1f7d  09-04 04:42  Gate1 PASS [phase=6]
    diff of the two    enforcer_sha 0e9ce2e9 -> f4af8962 (the Round 93 harness)
                       enforcer_surface, evidence_digest all rewritten
    "phase" field      3        <- unchanged by the Phase 6 run

So the file carries Phase-6 evidence under a Phase-3 label. That is not a
cosmetic mislabel, because one check reads it. Round 45 站3's digest
comparison skips when the result's `phase` differs from the receipt's, for a
good reason — the slot is one-per-FR and a later phase legitimately rewrites
it. With a stale label that guard inverts:

    receipt g1_p3_fr01  phase 3, sha a1df93ae   COMPARED -> mismatch, fires
    receipt g1_p6_fr01  phase 6, sha a052a115   SKIPPED  (file says 3)
    receipt g1_p7/p8    phase 7/8               SKIPPED

The one receipt that can never be satisfied is checked at every advance; the
three that describe the live verdict are never checked at all. That is 22
`doctor:gate1-evidence` rows on taskq-final (2 at the P6 advance, 10 at P7, 10
at P8, all ten FRs, every one quoting the same dead Phase-3 digest) and zero
verification of the verdict the next phase actually starts from.

The first draft of this round proposed narrowing doctor's receipt glob. That
would have silenced a check which is telling the truth — the Phase-3 evidence
WAS rewritten — while leaving the live verdict unverified. The defect is
upstream, in what the framework writes.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

pytestmark = [pytest.mark.core]

REPO = Path(__file__).resolve().parent.parent


def test_the_persisted_result_is_labelled_with_the_phase_that_ran_it():
    """The framework knows the phase; it is `args.phase`. The agent's copy is
    not the source, for the same reason its score is not."""
    src = (REPO / "cli" / "gate_cmds.py").read_text(encoding="utf-8")
    anchor = '_gp_json["quality_complete"] = result.quality_complete'
    assert anchor in src, "the harness-computed patch block moved"
    window = src[src.index(anchor):]
    window = window[:window.index("atomic_write_json")]
    assert '_gp_json["phase"] = args.phase' in window, (
        "`phase` is not patched beside the other harness-computed fields, so "
        "the committed result keeps whatever phase the agent's file carried — "
        "and Round 45's digest check reads that field to decide whether to "
        "compare at all"
    )


def _seed(project: Path, *, result_phase: int, receipt_phase: int,
          fr_id: str = "FR-01", tamper: bool = False) -> None:
    from cli._shared import _finalize_sentinel_path, per_fr_result_path

    result = per_fr_result_path(project, 1, fr_id)
    result.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps({"fr_id": fr_id, "composite_score": 97.5, "phase": result_phase})
    result.write_text(body, encoding="utf-8")
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()

    path = _finalize_sentinel_path(project, 1, fr_id, phase=receipt_phase)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "schema": 2, "gate": 1, "phase": receipt_phase, "fr_id": fr_id,
        "score": 97.5,
        "result_sha256": ("0" * 64) if tamper else digest,
        "ts": "2026-09-04T00:00:00+00:00",
    }), encoding="utf-8")


def test_a_correctly_labelled_result_lets_the_live_receipt_be_checked(tmp_path):
    """The property the stale label destroyed: the CURRENT verdict is verified."""
    from core.quality_gate.gate1_evidence import _per_fr_result_problems

    _seed(tmp_path, result_phase=6, receipt_phase=6, tamper=True)
    receipt = json.loads(
        (tmp_path / ".sessi-work" / "sentinels" / "g1_p6_fr01.finalized")
        .read_text(encoding="utf-8"))
    problems = _per_fr_result_problems(tmp_path, 1, "FR-01", receipt)
    assert problems, (
        "the receipt for the phase that produced this file names a digest the "
        "file does not have, and nothing said so"
    )


def test_a_superseded_receipt_is_still_skipped(tmp_path):
    """Round 45 站3's guard, unchanged: one slot per FR, a later phase rewrites
    it legitimately, so a historical receipt is not evidence of tampering."""
    from core.quality_gate.gate1_evidence import _per_fr_result_problems

    _seed(tmp_path, result_phase=6, receipt_phase=6)
    stale = {"schema": 2, "gate": 1, "phase": 3, "fr_id": "FR-01",
             "score": 97.5, "result_sha256": "a1df93aed8d2" + "0" * 52}
    assert _per_fr_result_problems(tmp_path, 1, "FR-01", stale) == []


def test_the_stale_label_is_what_inverted_the_check(tmp_path):
    """Negative control, and the measurement this round is built on.

    With the file mislabelled the way taskq-final's is, the live receipt is
    skipped and the dead one fires — the exact inversion, reproduced.
    """
    from core.quality_gate.gate1_evidence import _per_fr_result_problems

    _seed(tmp_path, result_phase=3, receipt_phase=3, tamper=True)
    dead = json.loads(
        (tmp_path / ".sessi-work" / "sentinels" / "g1_p3_fr01.finalized")
        .read_text(encoding="utf-8"))
    assert _per_fr_result_problems(tmp_path, 1, "FR-01", dead), (
        "fixture drift: the dead receipt should still be the one that fires"
    )
    live = {**dead, "phase": 6}
    assert _per_fr_result_problems(tmp_path, 1, "FR-01", live) == [], (
        "fixture drift: the live receipt should be the one that is skipped"
    )


def test_a_missing_result_file_is_still_reported(tmp_path):
    """The other half of `_per_fr_result_problems`, untouched: Round 45 站3
    exists because taskq-advance deleted five FRs' results and every register
    still said they scored 100."""
    from core.quality_gate.gate1_evidence import _per_fr_result_problems

    (tmp_path / ".methodology").mkdir()
    problems = _per_fr_result_problems(
        tmp_path, 1, "FR-01",
        {"schema": 2, "phase": 6, "result_sha256": "x" * 64},
    )
    assert problems and "no longer on disk" in problems[0]
