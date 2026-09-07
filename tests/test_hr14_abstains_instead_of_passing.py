"""Round 108 站A — an integrity score nobody measured is not full marks.

`AutoFixEngine.check_escalation` answers HR-14 ("Integrity < 40 → FREEZE",
`constitution/CONSTITUTION.md:251`, `SKILL.md:365`) by reading
`state.json["integrity"]`. Measured 2026-09-08:

    readers of state["integrity"] : 1  (that branch)
    writers of state["integrity"] : 0  (nothing in the tree)
    corpus projects carrying it   : 0 / 15

The reader defaulted to `100.0`. So on every project this framework has ever
touched, HR-14 was evaluated against a fabricated perfect score and could not
fire — and it looked, from the outside and from the constitution, like a rule
that was being honoured.

That is Round 32/35's shape: a could-not-measure reported as a result. The
fix is not to make HR-14 fire (that needs someone to decide who computes
integrity and in which phase, which is policy, recorded in
docs/PROPOSAL_ADJUDICATIONS.md with its re-open condition). The fix is that
the absence stops being answered as a pass: `_check_integrity` returns `None`,
and the caller writes the abstention to the degradation ledger instead of
stepping over it.

The two directions are both real here. Escalating on the absence would be as
wrong as passing on it — it would block all fifteen corpus projects over a
number the framework does not produce — so the absence must escalate to
nothing AND be recorded, and both halves are asserted below.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.auto_fix import (
    AutoFixEngine,
    EscalationCondition,
    FixContext,
    FixResult,
    FixStrategy,
)

pytestmark = [pytest.mark.core]


def _project(tmp_path: Path, state: dict) -> Path:
    root = tmp_path / "proj"
    (root / ".methodology").mkdir(parents=True)
    (root / ".methodology" / "state.json").write_text(
        json.dumps(state), encoding="utf-8")
    return root


def _engine(root: Path) -> AutoFixEngine:
    return AutoFixEngine(project_root=root, phase=1)


def _context(root: Path) -> FixContext:
    return FixContext(source="test", problem_type="missing_artifact",
                      severity="medium", phase=1, project_root=root)


def _result() -> FixResult:
    """Confidence at the ceiling and no gate, so the only escalation this
    engine can reach is HR-14 — the branch under test."""
    return FixResult(success=True, strategy=FixStrategy.AUTO_FIX,
                     problem_type="missing_artifact", confidence=100.0,
                     action_taken="none")


def _degradations(root: Path) -> list[dict]:
    path = root / ".methodology" / "degradations.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line.strip()]


# ---- the absence ----------------------------------------------------------

def test_a_missing_integrity_score_reads_as_unmeasured_not_as_100(tmp_path):
    """The state every corpus project is in."""
    root = _project(tmp_path, {"state": "RUNNING", "current_phase": 1})
    assert _engine(root)._check_integrity() is None, (
        "an absent integrity key came back as a number, so HR-14 was "
        "evaluated against a score nobody produced")


def test_the_absence_is_written_down_rather_than_stepped_over(tmp_path):
    """Round 43's rule: computing a verdict and dropping it is the defect.

    Not escalating is correct here; not SAYING SO is what made this invisible
    for the framework's whole life.
    """
    root = _project(tmp_path, {"state": "RUNNING", "current_phase": 1})
    engine = _engine(root)
    assert engine.check_escalation(_context(root), _result()) is not \
        EscalationCondition.HR14_INTEGRITY, (
        "an unmeasured integrity score escalated — that blocks every project "
        "in the corpus over a number the framework does not produce")

    rows = _degradations(root)
    assert any(r["component"] == "auto_fix" and "HR-14" in r["what"]
               for r in rows), (
        f"HR-14 went unanswered and left no record: {rows}")
    row = next(r for r in rows if "HR-14" in r["what"])
    assert row["owner"] == "harness", (
        f"a missing producer is the framework's debt, not the project's: {row}")


# ---- reverse controls: the rule still works when there IS a number --------

def test_a_real_low_score_still_escalates(tmp_path):
    """The rule is not being retired. If someone ever writes the key, HR-14
    fires exactly as the constitution says."""
    root = _project(tmp_path, {"state": "RUNNING", "current_phase": 1,
                               "integrity": 12.5})
    engine = _engine(root)
    assert engine._check_integrity() == 12.5
    assert engine.check_escalation(_context(root), _result()) is \
        EscalationCondition.HR14_INTEGRITY


def test_a_real_high_score_does_not_escalate_and_records_nothing(tmp_path):
    """The other reverse control: a measured pass is silent. Without this the
    ledger row above could be written on every call and mean nothing."""
    root = _project(tmp_path, {"state": "RUNNING", "current_phase": 1,
                               "integrity": 99.0})
    engine = _engine(root)
    assert engine._check_integrity() == 99.0
    assert engine.check_escalation(_context(root), _result()) is not \
        EscalationCondition.HR14_INTEGRITY
    assert not [r for r in _degradations(root) if "HR-14" in r.get("what", "")], (
        "a measured, passing integrity score was reported as unmeasured")


def test_an_unparseable_score_is_also_unmeasured(tmp_path):
    """`"integrity": "high"` is not 100.0 either. The old code caught the
    ValueError and returned full marks, which is the same substitution one
    layer down."""
    root = _project(tmp_path, {"state": "RUNNING", "current_phase": 1,
                               "integrity": "high"})
    assert _engine(root)._check_integrity() is None
