"""Integration tests for auto-fix pipeline."""

import json
import pytest
from pathlib import Path

from core.auto_fix import (
    AutoFixEngine,
    FixContext,
    FixResult,
    FixStrategy,
    EscalationCondition,
)
from core.auto_fix.classifier import CLASSIFICATION_TABLE
from core.auto_fix.strategies import STRATEGY_REGISTRY



class TestAutoFixPipelineIntegration:
    def test_full_cycle_missing_artifact(self, tmp_path: Path):
        """Missing artifact → stub generated → preflight passes."""
        (tmp_path / ".methodology").mkdir(parents=True)
        (tmp_path / ".methodology" / "quality_manifest.json").write_text(
            json.dumps({"functional_requirements": [{"id": "FR-001"}]}),
            encoding="utf-8",
        )

        engine = AutoFixEngine(project_root=tmp_path, phase=1)
        context = FixContext(
            source="constitution/missing_artifact",
            problem_type="missing_artifact",
            severity="critical",
            phase=1,
            project_root=tmp_path,
            details={"artifact_name": "TEST_DOC"},
        )
        # Round 48 站6: inverted. This used to assert the stub was written.
        # `missing_artifact` writes a TBD stub for an absent deliverable, which
        # turns "the artifact is missing" into "the artifact is empty" — and
        # Round 42 站3 is the round that made a missing required deliverable a
        # block rather than a warning. The strategy is retired; the engine
        # refuses it and says which reason applies.
        result = engine.fix(context)
        assert not result.success
        assert result.strategy == FixStrategy.HUMAN_REQUIRED
        assert "retired" in (result.error or "")
        assert "TBD stub" in result.action_taken

    def test_full_cycle_low_keyword_density(self, tmp_path: Path):
        """Low keyword density → keywords added → score improves."""
        (tmp_path / "docs").mkdir(parents=True)
        test_file = tmp_path / "docs" / "srs.md"
        test_file.write_text("# SRS\nNo security content.", encoding="utf-8")

        engine = AutoFixEngine(project_root=tmp_path, phase=3)
        context = FixContext(
            source="constitution/low_keyword_density",
            problem_type="low_keyword_density",
            severity="high",
            phase=3,
            project_root=tmp_path,
            details={
                "dimension": "security",
                "keywords": ["auth", "encrypt", "sanitize", "rbac", "token"],
                "files": [str(test_file)],
            },
        )
        # Round 48 站6: inverted. Appending a dimension's keywords so the
        # constitution scorer counts them is score gaming performed by the
        # framework; the score would then measure the repair.
        result = engine.fix(context)
        assert not result.success
        assert "retired" in (result.error or "")
        assert test_file.read_text(encoding="utf-8") == "# SRS\nNo security content.", (
            "a refused strategy must not have written anything"
        )

    def test_full_cycle_missing_section_headers(self, tmp_path: Path):
        """A retired header-stuffing strategy is refused and writes nothing."""
        test_file = tmp_path / "doc.md"
        test_file.write_text("# Doc", encoding="utf-8")

        engine = AutoFixEngine(project_root=tmp_path, phase=3)
        context = FixContext(
            source="constitution/missing_section_headers",
            problem_type="missing_section_headers",
            severity="high",
            phase=3,
            project_root=tmp_path,
            details={"files": [str(test_file)]},
        )
        result = engine.fix(context)
        assert not result.success
        assert "retired" in (result.error or "")
        assert test_file.read_text(encoding="utf-8") == "# Doc", (
            "a refused strategy must not have written anything"
        )

    def test_escalation_gate_score_too_low(self, tmp_path: Path):
        """Gate score < 60 after min_rounds → escalate."""
        engine = AutoFixEngine(project_root=tmp_path, phase=3, gate_min_score=60.0, gate_min_rounds=2)

        context = FixContext(
            source="gate/gate1_blocked",
            problem_type="low_constitution_score",
            severity="critical",
            phase=3,
            project_root=tmp_path,
            gate_num=1,
            details={"score": 45.0, "problem_type": "low_constitution_score"},
            retry_count=3,
        )
        # Round 48 站6: driven through check_escalation. The vehicle strategy is
        # retired, and the escalation LADDER is what this test is about.
        result = FixResult(success=False, strategy=FixStrategy.AUTO_FIX_WITH_VERIFICATION,
                           problem_type=context.problem_type, confidence=80.0,
                           action_taken="synthetic result for the escalation ladder")
        assert engine.check_escalation(context, result) == EscalationCondition.GATE_SCORE_LOW

    def test_escalation_low_confidence_after_three_attempts(self, tmp_path: Path):
        """Low confidence after max rounds → escalate."""
        engine = AutoFixEngine(project_root=tmp_path, phase=3, confidence_threshold=70.0)

        # Use a problem type with inherently low confidence (coverage=60%)
        context = FixContext(
            source="framework_enforcer/coverage_low",
            problem_type="low_coverage",
            severity="critical",
            phase=3,
            project_root=tmp_path,
            details={"problem_type": "low_coverage"},
            retry_count=3,
        )
        # Round 48 站6: the confidence is supplied rather than produced. The
        # strategy that used to return 50 wrote `assert True` test stubs; after
        # its retirement no live strategy reports below 70, so LOW_CONFIDENCE is
        # unreachable through fix(). Recorded in the adjudication ledger rather
        # than kept alive by a fabricating strategy.
        engine._round_counters[f"{context.source}:{context.problem_type}"] = 3
        result = FixResult(success=True, strategy=FixStrategy.AUTO_FIX,
                           problem_type=context.problem_type, confidence=50.0,
                           action_taken="synthetic result for the escalation ladder")
        assert engine.check_escalation(context, result) is not None

    def test_no_escalation_for_auto_fix_on_first_attempt(self, tmp_path: Path):
        """A first attempt at a LIVE repair does not escalate.

        Re-pointed in R49-C. It used to drive `missing_artifact`, whose
        strategy wrote a TBD stub for an absent deliverable and was deleted;
        with that gone the source classifies as unknown and correctly
        escalates, so the old subject could no longer demonstrate the
        no-escalation path. `missing_traceability` is the one strategy that
        remains, and driving the check through the surviving path is the point
        — an assertion about the ladder needs something the ladder actually
        lets through.
        """
        (tmp_path / ".methodology").mkdir(parents=True)
        (tmp_path / ".methodology" / "quality_manifest.json").write_text(
            json.dumps({"functional_requirements": [{"id": "FR-001"}]}),
            encoding="utf-8",
        )

        engine = AutoFixEngine(project_root=tmp_path, phase=1)
        context = FixContext(
            source="framework_enforcer/missing_traceability",
            problem_type="missing_traceability",
            severity="critical",
            phase=1,
            project_root=tmp_path,
            details={"untested": [], "uncoded": []},
            retry_count=0,
        )
        result = engine.fix(context)
        assert result.escalation is None

    def test_kill_switch_always_human_required(self, tmp_path: Path):
        """Kill-switch circuit OPEN → immediate HUMAN_REQUIRED."""
        engine = AutoFixEngine(project_root=tmp_path, phase=3)
        context = FixContext(
            source="kill_switch/circuit_open",
            problem_type="circuit_open",
            severity="critical",
            phase=3,
            project_root=tmp_path,
            details={"problem_type": "hard_rule_violation"},
        )
        result = engine.fix(context)
        assert result.strategy == FixStrategy.HUMAN_REQUIRED


class TestClassificationTableIntegrity:
    def test_every_entry_maps_to_strategy(self):
        for key, entry in CLASSIFICATION_TABLE.items():
            strat = entry["strategy"]
            assert isinstance(strat, FixStrategy), f"{key}: not a FixStrategy"
            pt = entry["problem_type"]
            if strat != FixStrategy.HUMAN_REQUIRED:
                assert pt in STRATEGY_REGISTRY, f"{key}: {pt} not in STRATEGY_REGISTRY"

    def test_no_empty_keys(self):
        for key, entry in CLASSIFICATION_TABLE.items():
            assert key, "empty key"
            assert entry["problem_type"], f"{key}: empty problem_type"
            assert entry["max_rounds"] >= 0, f"{key}: negative max_rounds"


class TestStrategyRegistryConsistency:
    def test_all_strategies_return_tuple(self, tmp_path: Path):
        for problem_type, fn in STRATEGY_REGISTRY.items():
            context = FixContext(
                source="test",
                problem_type=problem_type,
                severity="high",
                phase=3,
                project_root=tmp_path,
                details={},
            )
            result = fn(context, tmp_path)
            assert isinstance(result, tuple), f"{problem_type}: expected tuple, got {type(result)}"
            assert len(result) == 3, f"{problem_type}: expected 3-tuple"
            success, action, confidence = result
            assert isinstance(success, bool), f"{problem_type}: success not bool"
            assert isinstance(action, str), f"{problem_type}: action not str"
            assert isinstance(confidence, (int, float)), f"{problem_type}: confidence not number"
            assert 0 <= confidence <= 100, f"{problem_type}: confidence out of range"


class TestNoAutoFixFlag:
    def test_no_auto_fix_prevents_auto_fix(self):
        """When auto_fix=False, run_constitution_check_with_feedback does not retry."""
        from orchestration import run_constitution_check_with_feedback

        result = run_constitution_check_with_feedback(
            check_type="all",
            docs_path="/nonexistent/docs",
            current_phase=1,
            max_retries=3,
            auto_fix=False,
        )
        assert result is not None

pytestmark = [pytest.mark.auto_fix, pytest.mark.integration]
