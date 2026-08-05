"""A gate verdict must outlive the process that produced it (Round 38 站0/站4).

The workflow JS decides `gateNPass` from three numbers:

    gate4Pass = !!(g4v && g4v.last_gate_ok === true
                       && g4v.d4_rc === 0
                       && g4v.crg_rc === 0)

All three are *transcribed by the agent* from command output, and none of them
is written anywhere. A full-text search of taskq-renew's `.methodology/` for
`crg_rc` returns zero hits across a complete P1-P8 run.

The consequence is not hypothetical. taskq-renew's P6 wrote
`crg_baseline_p6.json` with `architecture_score: 77.8` — below the floor of 80
its own gate config states — and `gate4-verify-r1` passed on the first round,
which requires `crg_rc === 0`. One of those two is wrong, and **the framework
kept no record capable of saying which**. It could be that an intervening
`code-review-graph update` moved the score over 80 between the two steps; it
could be that the RC was transcribed wrong. Both are defects; neither is
adjudicable after the fact.

`verify-gate` runs the three checks itself and appends the verdict — together
with a digest of the tree it was measured on — to
`.methodology/gate_verify.jsonl`. `advance-phase` then refuses to advance an
exit gate without a matching PASS. The tree digest is what makes "matching"
mean something: Round 37's lesson was that a number is only as good as the tree
it was measured over, so the verdict now carries that tree with it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = [pytest.mark.core]


def _project(tmp_path: Path) -> Path:
    (tmp_path / ".methodology").mkdir()
    (tmp_path / "mod.py").write_text("x = 1\n", encoding="utf-8")
    return tmp_path


# ── the tree digest ─────────────────────────────────────────────────────

def test_the_digest_changes_when_a_delivered_file_changes(tmp_path: Path) -> None:
    from core.utils.delivery_scope import delivered_tree_digest

    project = _project(tmp_path)
    before = delivered_tree_digest(project)
    (project / "mod.py").write_text("x = 2\n", encoding="utf-8")
    assert delivered_tree_digest(project) != before


def test_the_digest_is_stable_for_an_unchanged_tree(tmp_path: Path) -> None:
    from core.utils.delivery_scope import delivered_tree_digest

    project = _project(tmp_path)
    assert delivered_tree_digest(project) == delivered_tree_digest(project)


def test_the_digest_covers_new_files_not_only_edits(tmp_path: Path) -> None:
    """A verdict measured before a module was added must not match the tree
    that now contains it."""
    from core.utils.delivery_scope import delivered_tree_digest

    project = _project(tmp_path)
    before = delivered_tree_digest(project)
    (project / "extra.py").write_text("y = 1\n", encoding="utf-8")
    assert delivered_tree_digest(project) != before


# ── the ledger ──────────────────────────────────────────────────────────

def test_verdict_records_the_tree_it_was_measured_on(tmp_path: Path) -> None:
    from core.quality_gate.gate_verify import record_verdict
    from core.utils.delivery_scope import delivered_tree_digest

    project = _project(tmp_path)
    record_verdict(
        project, gate=4, phase=6,
        checks={"last_gate_ok": True, "spec_coverage_rc": 0, "crg_rc": 0},
        verdict="PASS",
    )
    rows = [
        json.loads(line)
        for line in (project / ".methodology" / "gate_verify.jsonl")
        .read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(rows) == 1
    row = rows[0]
    assert row["gate"] == 4 and row["phase"] == 6 and row["verdict"] == "PASS"
    assert row["delivered_tree_sha256"] == delivered_tree_digest(project)
    # The three numbers that used to exist only inside a JS local.
    assert row["checks"] == {
        "last_gate_ok": True, "spec_coverage_rc": 0, "crg_rc": 0}
    assert "ts" in row and "iso" in row  # Round 24: one time base, both forms


def test_the_ledger_appends_rather_than_replaces(tmp_path: Path) -> None:
    """An audit trail that overwrites is not an audit trail."""
    from core.quality_gate.gate_verify import record_verdict

    project = _project(tmp_path)
    record_verdict(project, gate=2, phase=3, checks={}, verdict="FAIL")
    record_verdict(project, gate=2, phase=3, checks={}, verdict="PASS")
    lines = [
        ln for ln in (project / ".methodology" / "gate_verify.jsonl")
        .read_text(encoding="utf-8").splitlines() if ln.strip()
    ]
    assert len(lines) == 2


# ── what advance-phase asks the ledger ──────────────────────────────────

def test_advance_phase_blocks_without_a_matching_verdict(tmp_path: Path) -> None:
    from core.quality_gate.gate_verify import has_matching_pass

    project = _project(tmp_path)
    ok, why = has_matching_pass(project, gate=4)
    assert ok is False
    assert "verify-gate" in why


def test_a_verdict_measured_on_a_different_tree_does_not_count(tmp_path: Path) -> None:
    """The Round 37 property, applied to verdicts: a PASS on yesterday's tree
    says nothing about today's."""
    from core.quality_gate.gate_verify import has_matching_pass, record_verdict

    project = _project(tmp_path)
    record_verdict(project, gate=4, phase=6, checks={}, verdict="PASS")
    assert has_matching_pass(project, gate=4)[0] is True

    (project / "mod.py").write_text("x = 999\n", encoding="utf-8")
    ok, why = has_matching_pass(project, gate=4)
    assert ok is False
    assert "changed" in why.lower() or "tree" in why.lower()


def test_a_failing_verdict_does_not_satisfy_the_check(tmp_path: Path) -> None:
    from core.quality_gate.gate_verify import has_matching_pass, record_verdict

    project = _project(tmp_path)
    record_verdict(project, gate=4, phase=6, checks={}, verdict="FAIL")
    assert has_matching_pass(project, gate=4)[0] is False


def test_a_verdict_for_another_gate_does_not_satisfy_the_check(tmp_path: Path) -> None:
    from core.quality_gate.gate_verify import has_matching_pass, record_verdict

    project = _project(tmp_path)
    record_verdict(project, gate=2, phase=3, checks={}, verdict="PASS")
    assert has_matching_pass(project, gate=4)[0] is False


def test_the_latest_verdict_wins_over_an_earlier_one(tmp_path: Path) -> None:
    """A gate that failed, was fixed and re-verified must be able to advance;
    a gate that passed and then regressed must not."""
    from core.quality_gate.gate_verify import has_matching_pass, record_verdict

    project = _project(tmp_path)
    record_verdict(project, gate=4, phase=6, checks={}, verdict="FAIL")
    record_verdict(project, gate=4, phase=6, checks={}, verdict="PASS")
    assert has_matching_pass(project, gate=4)[0] is True

    record_verdict(project, gate=4, phase=6, checks={}, verdict="FAIL")
    assert has_matching_pass(project, gate=4)[0] is False


def test_a_corrupt_ledger_blocks_rather_than_passes(tmp_path: Path) -> None:
    """Round 32/35: a record we cannot read is not a passing record."""
    from core.quality_gate.gate_verify import has_matching_pass

    project = _project(tmp_path)
    (project / ".methodology" / "gate_verify.jsonl").write_text(
        "{not json\n", encoding="utf-8")
    ok, why = has_matching_pass(project, gate=4)
    assert ok is False
    assert why
