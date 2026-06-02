"""Unit tests for core/auto_fix/ — AutoFixEngine, classifier, strategies, guardrails."""

import pytest
from pathlib import Path

from core.auto_fix import (
    AutoFixEngine,
    FixContext,
    FixStrategy,
    EscalationCondition,
)
from core.auto_fix.classifier import (
    classify,
    CLASSIFICATION_TABLE,
    DIMENSION_CONFIDENCE,
    is_actual_secret,
    is_hard_rule_violation,
)
from core.auto_fix.strategies import (
    STRATEGY_REGISTRY,
    fix_missing_artifact,
    fix_missing_spec_tracking,
    fix_missing_traceability,
    fix_keyword_density,
    fix_section_headers,
    fix_hollow_content,
)
from core.auto_fix.guardrails import (
    pre_fix_safety_check,
    post_fix_drift_check,
    regression_check,
    verify_no_secrets_introduced,
    rollback_if_unsafe,
)

pytestmark = pytest.mark.auto_fix


# ── Classifier tests ────────────────────────────────────────────────────────


class TestClassifier:
    def test_all_problem_types_have_valid_strategy(self):
        for key, entry in CLASSIFICATION_TABLE.items():
            assert entry["strategy"] in FixStrategy, f"{key}: bad strategy"
            assert 0 <= entry["confidence"] <= 100, f"{key}: bad confidence"
            assert entry["max_rounds"] >= 0, f"{key}: bad max_rounds"
            assert isinstance(entry["problem_type"], str), f"{key}: bad problem_type"

    def test_non_human_required_have_strategy_fn(self):
        for key, entry in CLASSIFICATION_TABLE.items():
            if entry["strategy"] != FixStrategy.HUMAN_REQUIRED:
                pt = entry["problem_type"]
                assert pt in STRATEGY_REGISTRY, f"{pt} missing from STRATEGY_REGISTRY"

    def test_classify_hardcoded_secrets_human_required(self):
        strat, conf, max_r, pt, *_ = classify(
            "constitution/hardcoded_secrets",
            {"content": 'password = "secret123"', "problem_type": "hardcoded_secrets"},
        )
        assert strat == FixStrategy.HUMAN_REQUIRED

    def test_classify_missing_keyword_auto_fix(self):
        strat, conf, max_r, pt, *_ = classify(
            "constitution/low_keyword_density",
            {"problem_type": "low_keyword_density"},
        )
        assert strat == FixStrategy.AUTO_FIX

    def test_classify_constitution_low_score_auto_fix_verify(self):
        strat, conf, max_r, pt, *_ = classify(
            "constitution/low_score",
            {"problem_type": "low_constitution_score"},
        )
        assert strat == FixStrategy.AUTO_FIX_WITH_VERIFICATION
        assert 0 <= conf <= 100

    def test_classify_gate4_auto_fix_verify(self):
        strat, conf, max_r, pt, *_ = classify(
            "gate/gate4_blocked",
            {"gate_num": 4, "problem_type": "low_constitution_score"},
        )
        assert strat == FixStrategy.AUTO_FIX_WITH_VERIFICATION
        assert max_r == 3

    def test_gate4_error_class_and_max_rounds(self):
        from core.auto_fix.error_class import ErrorClass
        # Even if source is generic or non-gate, as long as details show gate_num=4,
        # it should resolve to ErrorClass.GATE_FAILURE to prevent regression
        _, _, max_r, _, err_cls = classify(
            "constitution_runner",
            {"gate_num": 4, "problem_type": "low_constitution_score"},
        )
        assert err_cls == ErrorClass.GATE_FAILURE
        assert max_r == 3


    def test_classify_hard_rule_never_auto_fix(self):
        strat, conf, max_r, pt, *_ = classify(
            "constitution_as_code/hard_rule_violation",
            {"rule_id": "R001", "hard_rule": True},
        )
        assert strat == FixStrategy.HUMAN_REQUIRED

    def test_missing_commit_task_id_is_human_required(self):
        strat, conf, max_r, pt, *_ = classify(
            "policy_engine/missing_commit_task_id",
            {"problem_type": "missing_commit_task_id"},
        )
        assert strat == FixStrategy.HUMAN_REQUIRED
        assert max_r == 0

    def test_dimension_confidence_modifies_security(self):
        _, conf, _, _, _ = classify(
            "constitution/low_score",
            {"dimension": "security", "problem_type": "low_constitution_score"},
        )
        assert conf == DIMENSION_CONFIDENCE["security"]

    def test_dimension_confidence_modifies_coverage(self):
        _, conf, _, _, _ = classify(
            "constitution/low_score",
            {"dimension": "coverage", "problem_type": "low_constitution_score"},
        )
        assert conf == DIMENSION_CONFIDENCE["coverage"]

    def test_classify_unknown_source_returns_sensible_default(self):
        strat, conf, max_r, pt, *_ = classify(
            "completely/unknown_source",
            {},
        )
        assert strat == FixStrategy.AUTO_FIX_WITH_VERIFICATION
        assert conf == 65.0

    def test_is_actual_secret_detects_password(self):
        assert is_actual_secret('password = "admin123"')
        assert not is_actual_secret("missing encryption keyword")

    def test_is_actual_secret_detects_api_key(self):
        assert is_actual_secret("api_key = 'sk-abc123'")

    def test_is_hard_rule_violation_by_rule_id(self):
        assert is_hard_rule_violation({"rule_id": "R001"})
        assert is_hard_rule_violation({"hard_rule": True})
        assert not is_hard_rule_violation({"rule_id": "CUSTOM"})

    def test_strategy_registry_complete(self):
        """Every non-HUMAN_REQUIRED problem_type in CLASSIFICATION_TABLE has a strategy."""
        for key, entry in CLASSIFICATION_TABLE.items():
            if entry["strategy"] != FixStrategy.HUMAN_REQUIRED:
                pt = entry["problem_type"]
                fn = STRATEGY_REGISTRY.get(pt)
                assert fn is not None, f"{pt} missing strategy function for key={key}"
                assert callable(fn), f"{pt} strategy is not callable"


# ── Strategy tests ───────────────────────────────────────────────────────────


class TestStrategies:
    def test_fix_missing_artifact_generates_stub(self, tmp_path: Path):
        context = FixContext(
            source="constitution/runner",
            problem_type="missing_artifact",
            severity="critical",
            phase=1,
            project_root=tmp_path,
            details={"artifact_name": "TEST_ARTIFACT"},
        )
        success, action, conf = fix_missing_artifact(context, tmp_path)
        assert success
        assert conf == 95.0
        assert (tmp_path / "01-requirements" / "TEST_ARTIFACT.md").exists()
        assert "01-requirements" in action

    def test_fix_missing_artifact_skips_existing(self, tmp_path: Path):
        (tmp_path / "01-requirements").mkdir(parents=True)
        existing = tmp_path / "01-requirements" / "EXISTING.md"
        existing.write_text("# Existing")

        context = FixContext(
            source="constitution/runner",
            problem_type="missing_artifact",
            severity="critical",
            phase=1,
            project_root=tmp_path,
            details={"artifact_name": "EXISTING"},
        )
        success, action, conf = fix_missing_artifact(context, tmp_path)
        assert success
        assert "already exists" in action

    def test_fix_missing_spec_tracking_generates_file(self, tmp_path: Path):
        (tmp_path / "01-requirements").mkdir(parents=True)
        context = FixContext(
            source="framework_enforcer",
            problem_type="missing_spec_tracking",
            severity="high",
            phase=1,
            project_root=tmp_path,
            details={},
        )
        success, action, conf = fix_missing_spec_tracking(context, tmp_path)
        assert success
        assert (tmp_path / "01-requirements" / "SPEC_TRACKING.md").exists()

    def test_fix_missing_traceability_generates_file(self, tmp_path: Path):
        (tmp_path / "01-requirements").mkdir(parents=True)
        context = FixContext(
            source="framework_enforcer",
            problem_type="missing_traceability",
            severity="high",
            phase=1,
            project_root=tmp_path,
            details={},
        )
        success, action, conf = fix_missing_traceability(context, tmp_path)
        assert success
        content = (tmp_path / "01-requirements" / "TRACEABILITY_MATRIX.md").read_text()
        assert "TRACEABILITY" in content

    def test_fix_keyword_density_adds_keywords(self, tmp_path: Path):
        test_file = tmp_path / "test.md"
        test_file.write_text("# Test\n\nNo security content here.", encoding="utf-8")

        context = FixContext(
            source="constitution/runner",
            problem_type="low_keyword_density",
            severity="medium",
            phase=3,
            project_root=tmp_path,
            details={
                "dimension": "security",
                "keywords": ["auth", "encrypt", "sanitize"],
                "files": [str(test_file)],
            },
        )
        success, action, conf = fix_keyword_density(context, tmp_path)
        assert success
        content = test_file.read_text()
        assert "auth" in content.lower()

    def test_fix_section_headers_adds_sections(self, tmp_path: Path):
        test_file = tmp_path / "doc.md"
        test_file.write_text("# Doc\nSome content.", encoding="utf-8")

        context = FixContext(
            source="constitution/runner",
            problem_type="missing_section_headers",
            severity="medium",
            phase=3,
            project_root=tmp_path,
            details={"files": [str(test_file)]},
        )
        success, action, conf = fix_section_headers(context, tmp_path)
        assert success
        content = test_file.read_text()
        assert "## Overview" in content

    def test_fix_hollow_content_expands_short_file(self, tmp_path: Path):
        test_file = tmp_path / "short.md"
        test_file.write_text("# Short", encoding="utf-8")

        (tmp_path / ".methodology").mkdir(parents=True)
        import json
        (tmp_path / ".methodology" / "quality_manifest.json").write_text(
            json.dumps({"functional_requirements": [
                {"id": "FR-001"}, {"id": "FR-002"}, {"id": "FR-003"},
                {"id": "FR-004"}, {"id": "FR-005"},
            ]}),
            encoding="utf-8",
        )

        context = FixContext(
            source="constitution/runner",
            problem_type="hollow_content",
            severity="medium",
            phase=3,
            project_root=tmp_path,
            details={"files": [str(test_file)]},
        )
        success, action, conf = fix_hollow_content(context, tmp_path)
        assert success
        content = test_file.read_text()
        assert len(content) > 200

    def test_fix_hollow_content_skips_long_file(self, tmp_path: Path):
        test_file = tmp_path / "long.md"
        test_file.write_text("# " + "A" * 300, encoding="utf-8")

        context = FixContext(
            source="constitution/runner",
            problem_type="hollow_content",
            severity="medium",
            phase=3,
            project_root=tmp_path,
            details={"files": [str(test_file)]},
        )
        success, action, conf = fix_hollow_content(context, tmp_path)
        assert "Expanded 0" in action  # skipped because content > 200


# ── Guardrails tests ─────────────────────────────────────────────────────────


class TestGuardrails:
    def test_pre_fix_safety_blocks_outside_project(self, tmp_path: Path):
        outside = Path("/tmp/outside_file.md")
        result = pre_fix_safety_check(tmp_path, [outside])
        assert not result["safe"]

    def test_pre_fix_safety_blocks_git_files(self, tmp_path: Path):
        git_file = tmp_path / ".git" / "config"
        git_file.parent.mkdir()
        git_file.touch()
        result = pre_fix_safety_check(tmp_path, [git_file])
        assert not result["safe"]

    def test_pre_fix_safety_allows_project_files(self, tmp_path: Path):
        safe_file = tmp_path / "docs" / "test.md"
        safe_file.parent.mkdir()
        safe_file.touch()
        result = pre_fix_safety_check(tmp_path, [safe_file])
        assert result["safe"]

    def test_post_fix_drift_checks_syntax(self, tmp_path: Path):
        py_file = tmp_path / "test.py"
        py_file.write_text("def foo():\n    pass\n", encoding="utf-8")
        result = post_fix_drift_check(tmp_path, [py_file])
        assert not result["drifted"]

    def test_post_fix_drift_detects_broken_python(self, tmp_path: Path):
        py_file = tmp_path / "broken.py"
        py_file.write_text("def foo(\n", encoding="utf-8")
        result = post_fix_drift_check(tmp_path, [py_file])
        assert result["drifted"]

    def test_post_fix_drift_detects_empty_markdown(self, tmp_path: Path):
        md_file = tmp_path / "empty.md"
        md_file.write_text("   \n", encoding="utf-8")
        result = post_fix_drift_check(tmp_path, [md_file])
        assert result["drifted"]

    def test_regression_check_detects_score_drop(self, tmp_path: Path):
        result = regression_check(
            tmp_path,
            {"coverage": 80.0, "security": 95.0},
            {"coverage": 60.0, "security": 95.0},
        )
        assert result["regressed"]
        assert "coverage" in result["message"]

    def test_regression_check_no_regression(self, tmp_path: Path):
        result = regression_check(
            tmp_path,
            {"coverage": 80.0},
            {"coverage": 82.0},
        )
        assert not result["regressed"]

    def test_verify_no_secrets_introduced_clean(self):
        assert verify_no_secrets_introduced("Normal content without secrets.")

    def test_verify_no_secrets_introduced_detects_password(self):
        assert not verify_no_secrets_introduced('password = "secret"')

    def test_rollback_if_unsafe_restores_files(self, tmp_path: Path):
        original = tmp_path / "rollback.md"
        original.write_text("original", encoding="utf-8")
        original.write_text("modified", encoding="utf-8")

        backup = {original: "original"}
        count = rollback_if_unsafe(tmp_path, backup)
        assert count == 1
        assert original.read_text() == "original"


# ── AutoFixEngine tests ─────────────────────────────────────────────────────


class TestAutoFixEngine:
    def test_engine_creation(self, tmp_path: Path):
        engine = AutoFixEngine(project_root=tmp_path, phase=3)
        assert engine.phase == 3
        assert engine.max_rounds == 5
        assert engine.confidence_threshold == 70.0

    def test_human_required_escalates_immediately(self, tmp_path: Path):
        engine = AutoFixEngine(project_root=tmp_path, phase=3)
        context = FixContext(
            source="constitution/hardcoded_secrets",
            problem_type="hardcoded_secrets",
            severity="critical",
            phase=3,
            project_root=tmp_path,
            details={"content": 'password = "secret"'},
        )
        result = engine.fix(context)
        assert not result.success
        assert result.strategy == FixStrategy.HUMAN_REQUIRED
        assert result.escalation == EscalationCondition.HARDCODED_SECRETS

    def test_gate4_escalates_after_max_rounds(self, tmp_path: Path):
        engine = AutoFixEngine(project_root=tmp_path, phase=6)
        context = FixContext(
            source="gate",
            problem_type="gate4_blocked",
            severity="critical",
            phase=6,
            project_root=tmp_path,
            gate_num=4,
            details={"gate_num": 4, "score": 70.0, "problem_type": "low_constitution_score", "dimension": "correctness", "files": [str(tmp_path / "test.py")]},
            retry_count=3,
        )
        (tmp_path / "test.py").write_text("dummy", encoding="utf-8")
        result = engine.fix(context)
        assert result.strategy == FixStrategy.AUTO_FIX_WITH_VERIFICATION
        assert result.escalation == EscalationCondition.GATE4_BLOCKED

    def test_hr12_escalation_after_max_rounds(self, tmp_path: Path):
        engine = AutoFixEngine(project_root=tmp_path, phase=3, max_rounds=1)
        context = FixContext(
            source="constitution/low_keyword_density",
            problem_type="low_keyword_density",
            severity="medium",
            phase=3,
            project_root=tmp_path,
            details={},
            retry_count=3,
        )
        result = engine.fix(context)
        assert result.escalation == EscalationCondition.HR12_MAX_ROUNDS

    def test_start_phase_timer(self, tmp_path: Path):
        engine = AutoFixEngine(project_root=tmp_path, phase=3)
        engine.start_phase_timer(60.0)
        assert engine._phase_start_time is not None
        assert engine._phase_estimate == 60.0

    def test_reset_rounds(self, tmp_path: Path):
        engine = AutoFixEngine(project_root=tmp_path, phase=3)
        engine._round_counters["test:key"] = 5
        engine.reset_rounds("test:key")
        assert "test:key" not in engine._round_counters

    def test_hr14_integrity_freeze(self, tmp_path: Path):
        engine = AutoFixEngine(project_root=tmp_path, phase=3, integrity_threshold=40.0)
        (tmp_path / ".methodology").mkdir(parents=True)
        import json
        (tmp_path / ".methodology" / "state.json").write_text(
            json.dumps({"current_phase": 3, "state": "RUNNING", "integrity": 25.0}),
            encoding="utf-8",
        )
        context = FixContext(
            source="constitution/low_keyword_density",
            problem_type="low_keyword_density",
            severity="medium",
            phase=3,
            project_root=tmp_path,
            details={},
            retry_count=1,
        )
        result = engine.fix(context)
        assert result.escalation == EscalationCondition.HR14_INTEGRITY

    def test_hr14_integrity_normal_no_escalation(self, tmp_path: Path):
        engine = AutoFixEngine(project_root=tmp_path, phase=3, integrity_threshold=40.0)
        (tmp_path / ".methodology").mkdir(parents=True)
        import json
        (tmp_path / ".methodology" / "state.json").write_text(
            json.dumps({"current_phase": 3, "state": "RUNNING", "integrity": 85.0}),
            encoding="utf-8",
        )
        context = FixContext(
            source="constitution/low_keyword_density",
            problem_type="low_keyword_density",
            severity="medium",
            phase=3,
            project_root=tmp_path,
            details={},
            retry_count=1,
        )
        result = engine.fix(context)
        assert result.escalation is None

    def test_summary_tracks_results(self, tmp_path: Path):
        engine = AutoFixEngine(project_root=tmp_path, phase=3)
        assert engine.summary()["total_fixes"] == 0

    def test_hr13_timeout_escalation(self, tmp_path: Path):
        """HR-13: phase runs >3x estimate → human escalation."""
        import time
        engine = AutoFixEngine(project_root=tmp_path, phase=3,
                               max_phase_time_multiplier=3.0)
        engine.start_phase_timer(10.0)  # estimate = 10s
        engine._phase_start_time = time.time() - 31.0  # elapsed 31s > 3×10
        context = FixContext(
            source="gate/gate1", problem_type="low_score",
            severity="high", phase=3, project_root=tmp_path, details={},
        )
        result = engine.fix(context)
        assert result.escalation == EscalationCondition.HR13_TIMEOUT

    def test_gate_score_below_min_escalates(self, tmp_path: Path):
        """Gate score < gate_min_score (60) after gate_min_rounds (3) → escalation."""
        engine = AutoFixEngine(project_root=tmp_path, phase=3,
                               gate_min_score=60.0, gate_min_rounds=3)
        context = FixContext(
            source="gate/gate1", problem_type="low_score",
            severity="high", phase=3, project_root=tmp_path,
            details={"score": 45.0}, gate_num=1, retry_count=3,
        )
        result = engine.fix(context)
        assert result.escalation == EscalationCondition.GATE_SCORE_LOW

    def test_gate_score_ok_no_escalation(self, tmp_path: Path):
        """Gate score >= gate_min_score → no GATE_SCORE_LOW escalation."""
        engine = AutoFixEngine(project_root=tmp_path, phase=3,
                               gate_min_score=60.0, gate_min_rounds=3,
                               confidence_threshold=0.0)  # suppress LOW_CONFIDENCE
        context = FixContext(
            source="gate/gate1", problem_type="low_score",
            severity="medium", phase=3, project_root=tmp_path,
            details={"score": 75.0}, gate_num=1, retry_count=3,
        )
        result = engine.fix(context)
        assert result.escalation != EscalationCondition.GATE_SCORE_LOW

    def test_confidence_below_70_escalates(self, tmp_path: Path):
        """Confidence < 70 after max_rounds for problem type → escalation."""
        engine = AutoFixEngine(project_root=tmp_path, phase=3,
                               confidence_threshold=70.0)
        # Must use a known problem_type so _max_rounds_for returns a value
        engine._round_counters["constitution/low_keyword_density:low_keyword_density"] = 3
        context = FixContext(
            source="constitution/low_keyword_density",
            problem_type="low_keyword_density",
            severity="medium", phase=3, project_root=tmp_path, details={},
            retry_count=3,
        )
        # fix() will run auto-fix → gets low confidence → check_escalation triggers
        result = engine.fix(context)
        # LOW_CONFIDENCE triggers when rounds >= _max_rounds_for and confidence < threshold
        # _max_rounds_for uses CLASSIFICATION_TABLE — default max_rounds=3, so rounds=3 ≥ 3
        assert result.escalation == EscalationCondition.LOW_CONFIDENCE

    def test_kill_switch_open_escalates(self, tmp_path: Path):
        """source contains 'kill_switch' → _human_condition_for returns KILL_SWITCH."""
        engine = AutoFixEngine(project_root=tmp_path, phase=3)
        context = FixContext(
            source="kill_switch/detector", problem_type="blocked_operation",
            severity="critical", phase=3, project_root=tmp_path, details={},
        )
        result = engine.fix(context)
        assert result.escalation == EscalationCondition.KILL_SWITCH

    def test_hard_rule_violation_escalates(self, tmp_path: Path):
        """details['hard_rule']=True → classify HUMAN_REQUIRED → _human_condition_for → HARD_RULE_VIOLATION."""
        engine = AutoFixEngine(project_root=tmp_path, phase=3)
        context = FixContext(
            source="constitution/runner", problem_type="unknown",
            severity="critical", phase=3, project_root=tmp_path,
            details={"hard_rule": True, "rule_id": "HR-06"},
        )
        result = engine.fix(context)
        assert result.escalation == EscalationCondition.HARD_RULE_VIOLATION
