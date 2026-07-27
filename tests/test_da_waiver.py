"""DA score-threshold waiver adjudication (Round 21).

The defect these pin is not a typo — it is an ordering mistake. Until Round 21
the "is this waiver needed?" question was answered in cli/gate_cmds.py at
gate-prerequisite time, which is before finalize_gate runs the framework's own
independent CRG pass. The only waivable dimension is CRG-only, so at that point
its score is JSON null: the check compared a null against a threshold key that
never existed (`tool_score`, later `target`), and the safeguard was dead in both
directions.

taskq's P6 is the case that surfaced it. Its gate4_result.json requested
`da_waiver: {"architecture": true}` on the stated premise that CRG scores the
hub-and-spoke layout 0. The framework's own crg_baseline_p6.json for the same
tree records **100.0** — the premise was false, the waiver was unnecessary, and
`da_waiver_needs_human_review` was raised over nothing.
"""

import json
from pathlib import Path

import pytest

from core.quality_gate.da_waiver import (
    CRG_ONLY_DIMENSIONS,
    WAIVABLE_DIMENSIONS,
    adjudicate_waivers,
)

_FIXTURE = Path(__file__).parent / "fixtures" / "gate_results" / "taskq_gate4.json"


class TestAdjudicateWaivers:
    def test_waiver_not_applied_when_framework_score_clears_threshold(self):
        """taskq's exact situation: requested waiver, framework says 100 ≥ 80."""
        verdict = adjudicate_waivers(
            ["architecture"], [("architecture", 100.0, 80.0)]
        )
        assert verdict.applied == frozenset()
        assert not verdict.blocked
        assert "not needed" in " ".join(verdict.notes)

    def test_waiver_not_applied_at_exact_threshold(self):
        verdict = adjudicate_waivers(["architecture"], [("architecture", 80.0, 80.0)])
        assert verdict.applied == frozenset()
        assert not verdict.blocked

    def test_waiver_applied_when_framework_score_is_below_threshold(self):
        """The genuine Orchestrator false positive still gets its waiver."""
        verdict = adjudicate_waivers(["architecture"], [("architecture", 0.0, 80.0)])
        assert verdict.applied == frozenset({"architecture"})
        assert not verdict.blocked

    def test_unscored_dimension_blocks_rather_than_waiving_an_unknown(self):
        verdict = adjudicate_waivers(["architecture"], [("architecture", None, 80.0)])
        assert verdict.applied == frozenset()
        assert verdict.blocked
        joined = " ".join(verdict.notes)
        assert "BLOCKED" in joined
        assert "Fix:" in joined

    def test_dimension_absent_from_gate_is_a_no_op(self):
        verdict = adjudicate_waivers(["architecture"], [("linting", 100.0, 90.0)])
        assert verdict.applied == frozenset()
        assert not verdict.blocked

    def test_no_requests_is_a_no_op(self):
        verdict = adjudicate_waivers([], [("architecture", 0.0, 80.0)])
        assert verdict.applied == frozenset()
        assert not verdict.blocked
        assert verdict.notes == ()

    def test_waivable_set_is_the_crg_set(self):
        """One definition, two consumers.

        The permission check (cli/gate_cmds.py) and the CRG override path
        (harness/harness_bridge.py) must not drift into two lists: a waiver's
        only rationale is a CRG measurement artifact, so a dimension CRG does
        not score can never have one.
        """
        assert WAIVABLE_DIMENSIONS == CRG_ONLY_DIMENSIONS
        assert "architecture" in WAIVABLE_DIMENSIONS
        for dim in ("security", "secrets_scanning", "test_coverage", "readability"):
            assert dim not in WAIVABLE_DIMENSIONS


class TestAgainstRealTaskqGateResult:
    """Adjudicate against a gate result an actual pipeline run produced.

    Round 30's regression test hand-built its fixture from score.py's breakdown
    assembly — but score.py writes .sessi-work/round_N/ scoring output, not
    gate{N}_result.json. Both sides of that comparison came from the same head,
    so the suite stayed green while the check stayed dead. This class reads a
    verbatim copy of taskq's committed gate4_result.json instead.
    """

    @pytest.fixture
    def gate4(self) -> dict:
        return json.loads(_FIXTURE.read_text(encoding="utf-8"))

    def test_real_breakdown_uses_threshold_not_target(self, gate4):
        """The field name the dead check kept missing."""
        for dim, row in gate4["breakdown"].items():
            assert "threshold" in row, f"{dim} has no 'threshold'"
            assert "target" not in row, f"{dim} unexpectedly carries 'target'"

    def test_real_architecture_score_is_null(self, gate4):
        """CRG-only dimension: the agent writes null, the framework supplies it."""
        assert gate4["breakdown"]["architecture"]["score"] is None

    def test_taskq_waiver_is_rejected_once_the_framework_score_is_used(self, gate4):
        """End of the causal chain: framework score 100.0 → waiver unnecessary.

        100.0 is taskq's own crg_baseline_p6.json value for the same commit —
        6/6 healthy communities. The DA challenge claimed CRG would report 0.
        """
        requested = [d for d, on in gate4["da_waiver"].items() if on]
        assert requested == ["architecture"]  # what the run actually asked for
        threshold = float(gate4["breakdown"]["architecture"]["threshold"])
        verdict = adjudicate_waivers(requested, [("architecture", 100.0, threshold)])
        assert verdict.applied == frozenset()
        assert not verdict.blocked

    def test_agent_reported_null_would_block_not_silently_waive(self, gate4):
        """If the framework score never arrives, refuse — do not waive blind."""
        row = gate4["breakdown"]["architecture"]
        verdict = adjudicate_waivers(
            ["architecture"],
            [("architecture", row["score"], float(row["threshold"]))],
        )
        assert verdict.blocked
        assert verdict.applied == frozenset()
