"""The receipt names a gate result; nobody ever went and looked for it.

Round 45 站0. taskq-advance's last commit, `30638d9 feat(FR-09): Gate1 PASS —
score=100.0 [phase=7]`, deleted five sibling FRs' Gate 1 evidence:

    .methodology/gate_results/gate1/FR-03.json   | 100 ------
    .methodology/gate_results/gate1/FR-05.json   | 107 ------
    .methodology/gate_results/gate1/FR-06.json   | 107 ------
    .methodology/gate_results/gate1/FR-08.json   |  48 ------
    .methodology/gate_results/gate1/FR-10.json   | 107 ------

At the time of writing, all three score registers still say those five FRs
passed:

    fr_progress.json        FR-03 … "status": "gate1_pass", "score": 100.0
    .gate1_scores.json      "7": {… "FR-03": 100.0 …}
    CLAUDE.md               | FR-03 | 100.0 | ✅ COMPLETE |

`doctor` says nothing, because `_check_gate1_evidence` corroborates the
manifest against the finalize receipt and `gate_timestamps.jsonl` — two
channels that both survived. The per-FR result file is written by
`cli/gate_cmds.py` and read, outside reporting, by no one.

No new register is needed. Round 32 站1 already put `result_sha256` in the
receipt precisely so that a verdict names the artifact it was taken on. The
pointer has simply never been dereferenced.

Known limit, stated rather than papered over: the receipt lives in
`.sessi-work/sentinels/`, which is gitignored, so on a fresh clone there is no
receipt to dereference and this check is silent. That is the same disease as
station 1's — evidence kept where it cannot survive — and it is recorded in
the adjudication ledger rather than fixed by widening this station.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.quality_gate.gate1_evidence import (
    GATE_TIMESTAMPS_FILE,
    verify_finalize_evidence,
    write_finalize_receipt,
)

pytestmark = [pytest.mark.core]


def _per_fr_path(project: Path, gate: int, fr_id: str) -> Path:
    from core.quality_gate.gate1_evidence import per_fr_result_path
    return per_fr_result_path(project, gate, fr_id)


@pytest.fixture()
def finalized(tmp_path: Path):
    """One FR finalized the way cli/gate_cmds.py leaves the disk.

    `.methodology/gate1_result.json` deliberately holds a DIFFERENT FR's
    payload: it is a rolling alias every finalize overwrites, so after a phase
    it carries whichever FR went last. That is the state taskq-advance is in,
    and it is what makes the per-FR copy the only durable per-FR artifact.
    """
    meth = tmp_path / ".methodology"
    meth.mkdir()
    payload = json.dumps({
        "gate": 1, "phase": 7, "fr_id": "FR-03", "verdict": "PASS",
        "overall_score": 100.0, "quality_complete": True,
    }, indent=2)

    (meth / "gate1_result.json").write_text(json.dumps({
        "gate": 1, "phase": 7, "fr_id": "FR-09", "verdict": "PASS",
        "overall_score": 100.0, "quality_complete": True,
    }, indent=2), encoding="utf-8")
    per_fr = _per_fr_path(tmp_path, 1, "FR-03")
    per_fr.parent.mkdir(parents=True, exist_ok=True)
    per_fr.write_text(payload, encoding="utf-8")

    (meth / GATE_TIMESTAMPS_FILE).write_text(json.dumps({
        "phase": 7, "gate": 1, "fr_id": "FR-03", "ts": 1786400000.0,
        "iso": "2026-08-11T00:00:00+00:00", "source": "finalize",
    }) + "\n", encoding="utf-8")
    (meth / ".gate1_scores.json").write_text(
        json.dumps({"7": {"FR-03": 100.0}}), encoding="utf-8")

    write_finalize_receipt(
        tmp_path, gate=1, phase=7, fr_id="FR-03", score=100.0,
        result_path=per_fr,
    )
    return tmp_path, per_fr


def test_a_finalized_fr_with_its_result_on_disk_is_clean(finalized):
    project, _unused = finalized
    assert verify_finalize_evidence(project, 1, 7, "FR-03") == []


def test_a_deleted_per_fr_result_is_named(finalized):
    """`30638d9`'s five deletions, one of them, replayed."""
    project, per_fr = finalized
    per_fr.unlink()

    problems = verify_finalize_evidence(project, 1, 7, "FR-03")

    assert problems, (
        "FR-03's Gate 1 evidence is gone and three registers still say it "
        "scored 100.0 — the framework must be able to say so"
    )
    joined = " ".join(str(p) for p in problems)
    assert "FR-03" in joined
    assert "gate_results/gate1/FR-03.json" in joined


def test_a_rewritten_per_fr_result_is_named(finalized):
    """Present but not the artifact the verdict was taken on. Round 26 站2's
    rule — evidence must not be rewritable underneath a recorded verdict."""
    project, per_fr = finalized
    per_fr.write_text(json.dumps({
        "gate": 1, "phase": 7, "fr_id": "FR-03", "verdict": "PASS",
        "overall_score": 100.0, "quality_complete": True,
        "breakdown": {"linting": {"score": 100.0}},
    }, indent=2), encoding="utf-8")

    problems = verify_finalize_evidence(project, 1, 7, "FR-03")

    joined = " ".join(str(p) for p in problems)
    assert "FR-03" in joined, problems
    assert "sha256" in joined or "digest" in joined, problems


def test_a_later_phase_rerunning_the_fr_is_not_a_rewrite(finalized):
    """Found by measuring the live project after station 3 had shipped.

    `gate_results/gate1/{fr}.json` carries no phase — it is ONE slot per FR,
    rewritten by every phase that re-runs that FR's gate. taskq-advance
    advanced to Phase 9 during this round, and its P8 run left FR-03, FR-05,
    FR-06, FR-08 and FR-10 holding phase-8 results while their phase-7
    receipts still exist.

    That is a legitimate later run, not evidence rewritten under a verdict.
    Comparing across it would fire for every FR at every phase boundary
    forever — the same false-accusation machine station 2 removed, rebuilt one
    station later.
    """
    project, per_fr = finalized
    per_fr.write_text(json.dumps({
        "gate": 1, "phase": 8, "fr_id": "FR-03", "verdict": "PASS",
        "overall_score": 100.0, "quality_complete": True,
    }, indent=2), encoding="utf-8")

    assert verify_finalize_evidence(project, 1, 7, "FR-03") == []


def test_a_receipt_that_names_no_result_keeps_its_existing_complaint(tmp_path):
    """Round 32 站1 already refuses a receipt with no `result_sha256`. This
    station must not turn that into a different (or a second) complaint."""
    meth = tmp_path / ".methodology"
    meth.mkdir()
    (meth / GATE_TIMESTAMPS_FILE).write_text(json.dumps({
        "phase": 7, "gate": 1, "fr_id": "FR-03", "ts": 1786400000.0,
        "iso": "2026-08-11T00:00:00+00:00", "source": "finalize",
    }) + "\n", encoding="utf-8")
    (meth / ".gate1_scores.json").write_text(
        json.dumps({"7": {"FR-03": 100.0}}), encoding="utf-8")
    write_finalize_receipt(
        tmp_path, gate=1, phase=7, fr_id="FR-03", score=100.0,
        result_path=None,
    )

    problems = verify_finalize_evidence(tmp_path, 1, 7, "FR-03")

    assert any("names no gate result" in str(p) for p in problems), problems
