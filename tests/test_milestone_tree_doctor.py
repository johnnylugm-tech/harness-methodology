"""A recorded milestone can be checked against the verdict that produced it.

Round 44 站0/站4. Two comparisons, and both stay inside one digest
definition:

  1. `phase_completed[N].delivered_tree_sha256` (station 2) against the tree
     of `phase_completed[N].sha`.
  2. For records predating that field: the phase's last PASS verdict, which
     since station 1 carries both `delivered_tree_sha256` (the tree measured)
     and `head_tree_sha256` (the tree git held). Different values mean the
     verdict was measured on content nobody had committed.

Comparing across the two — a verdict's recorded digest against a freshly
computed one — is what the first draft did, and it would have reported every
project as broken the day station 1 changed what the digest covers. The
`_stale_definition` guard below pins that.

This is diagnosis, never a re-judgement. Same standing as Round 43 站4's
`phase_verdict_staleness`: the score does not move, nothing is waived, and
Round 38's rule that no threshold may be waived is untouched. What changes is
that a milestone which certifies a tree nobody committed stops being
invisible.

Silence when neither comparison is available — Round 39/40: a record
predating a field is not a violation; Round 32/35: could-not-measure is not a
finding.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True,
        check=True,
    )


@pytest.fixture
def milestone_project(tmp_path: Path) -> Path:
    """A repo whose Phase 3 milestone commit predates a later edit."""
    proj = tmp_path / "proj"
    proj.mkdir()
    _git(proj, "init", "-q")
    _git(proj, "config", "user.email", "t@example.com")
    _git(proj, "config", "user.name", "t")
    (proj / ".methodology").mkdir()
    (proj / "mod.py").write_text("x = 1\n", encoding="utf-8")
    _git(proj, "add", "-A")
    _git(proj, "commit", "-q", "-m", "phase3 milestone")
    return proj


def _head(proj: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(proj), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()


def _write_state(proj: Path, entry: dict) -> None:
    (proj / ".methodology" / "state.json").write_text(
        json.dumps({"state": "RUNNING", "current_phase": 4,
                    "phase_completed": {"3": entry}}),
        encoding="utf-8",
    )


def _record_verdict(proj: Path) -> dict:
    from core.quality_gate.gate_verify import record_verdict
    return record_verdict(
        proj, gate=2, phase=3,
        checks={"last_gate_ok": True, "spec_coverage_rc": 0, "crg_rc": 0},
        verdict="PASS",
    )


# ── the taskq-advance shape, on records that predate the new field ──────────

def test_a_milestone_certified_on_an_uncommitted_tree_is_named(
    milestone_project: Path,
) -> None:
    from core.doctor import _check_milestone_tree_matches_verdict

    proj = milestone_project
    # The evidence lands in the working tree but never in the commit — the
    # `@given` that unblocked taskq-advance's P3→P4 at 13:14 and entered git
    # at 13:32, eighteen minutes after the phase had turned over.
    (proj / "test_property.py").write_text("from hypothesis import given\n",
                                           encoding="utf-8")
    _record_verdict(proj)
    _write_state(proj, {"sha": _head(proj)})

    findings = _check_milestone_tree_matches_verdict(proj)

    assert len(findings) == 1, [f.message for f in findings]
    assert findings[0].severity == "WARN", (
        "the verdict is not re-judged — Round 38's rule is that nothing is "
        "waived, not that the phase failed"
    )
    assert "Phase 3" in findings[0].message
    assert "uncommitted content" in findings[0].message


def test_a_milestone_whose_commit_carries_the_judged_tree_is_silent(
    milestone_project: Path,
) -> None:
    proj = milestone_project
    _record_verdict(proj)
    _write_state(proj, {"sha": _head(proj)})

    assert _check_milestone(proj) == []


def test_a_recorded_tree_digest_is_compared_directly(
    milestone_project: Path,
) -> None:
    """Station 2's forward path: no verdict lookup needed."""
    proj = milestone_project
    _write_state(proj, {"sha": _head(proj),
                        "delivered_tree_sha256": "0" * 64})

    findings = _check_milestone(proj)
    assert len(findings) == 1
    assert "Phase 3" in findings[0].message


def test_nothing_to_compare_says_nothing(milestone_project: Path) -> None:
    """No recorded digest and no verdict for the exit gate."""
    proj = milestone_project
    _write_state(proj, {"sha": _head(proj)})

    assert _check_milestone(proj) == []


def test_a_verdict_predating_the_head_tree_field_says_nothing(
    milestone_project: Path,
) -> None:
    """The guard against comparing across two digest definitions.

    A verdict recorded before station 1 has `delivered_tree_sha256` computed
    under the old scope and no `head_tree_sha256` at all. Comparing that
    digest with a freshly computed one would report every project as broken
    the day the definition moved — which is what the first draft of this
    check did, and what it reported on a real taskq-advance copy.
    """
    proj = milestone_project
    (proj / "test_property.py").write_text("from hypothesis import given\n",
                                           encoding="utf-8")
    row = _record_verdict(proj)
    assert "head_tree_sha256" in row, "station 1 records it"

    ledger = proj / ".methodology" / "gate_verify.jsonl"
    legacy = {k: v for k, v in row.items() if k != "head_tree_sha256"}
    ledger.write_text(json.dumps(legacy) + "\n", encoding="utf-8")
    _write_state(proj, {"sha": _head(proj)})

    assert _check_milestone(proj) == []


def test_a_phase_with_no_exit_gate_is_not_examined(
    milestone_project: Path,
) -> None:
    """`EXIT_GATE_MAP` is `{3: 2, 4: 3, 6: 4}` — Phase 1 has no exit gate, so
    there is no verdict its milestone could contradict."""
    proj = milestone_project
    (proj / ".methodology" / "state.json").write_text(
        json.dumps({"current_phase": 2,
                    "phase_completed": {"1": {"sha": _head(proj)}}}),
        encoding="utf-8",
    )
    (proj / "stray.py").write_text("y = 2\n", encoding="utf-8")

    assert _check_milestone(proj) == []


def test_an_unresolvable_sha_says_nothing(milestone_project: Path) -> None:
    """A rebased or garbage-collected milestone cannot be measured, and
    could-not-measure is not a finding (Round 32/35)."""
    proj = milestone_project
    _record_verdict(proj)
    _write_state(proj, {"sha": "0" * 40})

    assert _check_milestone(proj) == []


# ── the wiring, not just the function (Round 43 站4's lesson) ───────────────

def test_run_doctor_actually_runs_the_check(milestone_project: Path) -> None:
    """Counter-proof discipline in Round 43 caught a check that nobody ran
    guarded by tests that all called it directly. Not twice."""
    from core.doctor import run_doctor

    proj = milestone_project
    (proj / "test_property.py").write_text("from hypothesis import given\n",
                                           encoding="utf-8")
    _record_verdict(proj)
    _write_state(proj, {"sha": _head(proj)})

    findings = run_doctor(proj)

    assert any("measured on uncommitted content" in f.message
               for f in findings), (
        "run_doctor did not run the milestone-tree check: "
        f"{[f.message[:60] for f in findings]}"
    )


def _check_milestone(proj: Path) -> list:
    from core.doctor import _check_milestone_tree_matches_verdict
    return _check_milestone_tree_matches_verdict(proj)
