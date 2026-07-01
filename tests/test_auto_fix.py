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
    _fix_assertion_error,
    fix_missing_artifact,
    fix_missing_spec_tracking,
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

    def test_fix_assertion_error_preserves_space_and_fixes_all_matches(self):
        """Bug fix: `replace(..., 1)` only fixed the first match.

        Source has `assert x == 5` on TWO lines; failure message says
        expected value is `3` (real pytest --tb=line format). The fix
        must rewrite BOTH occurrences to `assert x == 3`, not just the
        first.
        """
        content = (
            "def test_one():\n"
            "    assert x == 5\n"
            "\n"
            "def test_two():\n"
            "    assert x == 5\n"
        )
        message = "assert x == 3"
        out = _fix_assertion_error(content, message, "test_dummy")
        assert out.count("assert x == 3") == 2, (
            f"Expected both assertions fixed to `assert x == 3`, got:\n{out}"
        )
        assert "assert x == 5" not in out, (
            f"Unfixed `assert x == 5` still present, got:\n{out}"
        )

    def test_fix_keyword_density_does_not_append_header_to_files_with_no_additions(self, tmp_path: Path):
        """Bug fix: `added` counter was shared across files. After the
        first file added any keyword, subsequent files also got a
        `## Compliance` section header even when they themselves added
        zero keywords.
        """
        # File 1: missing all keywords → should get header + bullets
        f1 = tmp_path / "needs.md"
        f1.write_text("# Doc\n\nNo security content here.", encoding="utf-8")

        # File 2: already contains all keywords → must NOT be touched
        f2 = tmp_path / "ok.md"
        f2.write_text(
            "# Doc\n\nWe use auth, encrypt, sanitize and audit and rotate.",
            encoding="utf-8",
        )
        original_f2 = f2.read_text(encoding="utf-8")

        context = FixContext(
            source="constitution/runner",
            problem_type="low_keyword_density",
            severity="medium",
            phase=3,
            project_root=tmp_path,
            details={
                "dimension": "security",
                "keywords": ["auth", "encrypt", "sanitize", "audit", "rotate"],
                "files": [str(f1), str(f2)],
            },
        )
        fix_keyword_density(context, tmp_path)

        f1_content = f1.read_text(encoding="utf-8")
        assert "## Security Compliance" in f1_content, (
            f"File 1 should have section header, got:\n{f1_content}"
        )

        assert f2.read_text(encoding="utf-8") == original_f2, (
            f"File 2 should be untouched (already had all keywords), got:\n{f2.read_text()}"
        )
        assert "## Security Compliance" not in f2.read_text(encoding="utf-8"), (
            "File 2 got a Compliance section header despite adding zero keywords"
        )


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

    def test_ast_mutation_guard_per_file_allowed_node_multi_file_fix(self, tmp_path: Path):
        """Multi-file fix: each file must be checked against its own top-level node name.

        Bug: allowed_node_name was computed from files[0] only. If a.py has class A
        and b.py has class B, checking b.py with allowed_node_name=A (from files[0])
        would incorrectly flag B as out-of-bounds.

        The fix: ast_mutation_guard accepts a per-file allowed_node_name, so b.py is
        checked against its own top-level name B.
        """
        from core.auto_fix.guardrails import ast_mutation_guard

        # files[0] = a.py with class A
        a_py = tmp_path / "a.py"
        a_pre = "class A:\n    def method_a(self):\n        pass\n"
        a_post = "class A:\n    def method_a(self):\n        pass\n    def new_method(self):\n        pass\n"

        # files[1] = b.py with class B (different top-level name)
        b_py = tmp_path / "b.py"
        b_pre = "class B:\n    def method_b(self):\n        pass\n"
        b_post = "class B:\n    def method_b(self):\n        pass\n    def new_method(self):\n        pass\n"

        # Simulate the bug: guard called with allowed_node_name="A" for BOTH files
        # This is what the buggy code did (files[0]'s allowed_node used for all files).
        # a.py should pass with allowed_node_name="A"
        assert ast_mutation_guard(a_py, a_pre, a_post, allowed_node_name="A") is True
        # b.py should ALSO pass when checked against its OWN node name "B"
        assert ast_mutation_guard(b_py, b_pre, b_post, allowed_node_name="B") is True
        # But b.py would FAIL if we wrongly used allowed_node_name="A" (the bug scenario)
        assert ast_mutation_guard(b_py, b_pre, b_post, allowed_node_name="A") is False

    def test_auto_fix_engine_multi_file_allowed_node_from_each_file(self, tmp_path: Path):
        """AutoFixEngine computes per-file allowed_node for AST guard.

        Simulates the full engine flow with two files having different top-level names.
        The AST guard must not reject b.py just because its top-level class B
        differs from a.py's top-level class A (which was incorrectly extracted from files[0]).

        Bug reproduction:
        - files=[a.py(class A), b.py(class B)], error_line in b.py
        - extract_minimal_viable_context returns allowed_node="A" if called on files[0]
        - The guard loop then checks b.py with allowed_node="A" and wrongly rejects it
        """
        from core.auto_fix.segment_slicing import extract_minimal_viable_context
        from core.auto_fix.guardrails import ast_mutation_guard

        # Set up two files with different top-level names
        a_py = tmp_path / "a.py"
        a_py.write_text('class A:\n    def foo(self):\n        pass\n', encoding='utf-8')

        b_py = tmp_path / "b.py"
        b_py.write_text('class B:\n    def bar(self):\n        pass\n', encoding='utf-8')

        # Simulate: error line is in b.py (line 1 = class B:), so extract returns "B"
        _mvc_text, allowed_node_from_b = extract_minimal_viable_context(
            b_py, error_line=1, project_root=tmp_path
        )
        assert allowed_node_from_b == "B", (
            f"extract_minimal_viable_context(b.py) returned {allowed_node_from_b!r}, expected 'B'"
        )

        # Simulate the BUG scenario: if the guard loop uses allowed_node="A" (from files[0])
        # to check b.py, it should be rejected (because "B" != "A")
        b_pre = b_py.read_text()
        b_post = 'class B:\n    def bar(self):\n        pass\n    def new_method(self):\n        pass\n'
        b_py.write_text(b_post, encoding="utf-8")

        # Bug path: b.py wrongly checked with allowed_node="A" (from files[0])
        bug_result = ast_mutation_guard(b_py, b_pre, b_post, allowed_node_name="A")
        assert bug_result is False, "Bug not reproduced: b.py should be rejected with allowed_node='A'"

        # Restore b.py
        b_py.write_text(b_pre, encoding="utf-8")

        # Fix path: b.py correctly checked with allowed_node="B" (its own node)
        fix_result = ast_mutation_guard(b_py, b_pre, b_post, allowed_node_name="B")
        assert fix_result is True, "Fix failed: b.py should be accepted with allowed_node='B'"


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


# ── Bug B fix: fix_over_interpretation_gap strategy (added 2026-06-28) ──────


class TestFixOverInterpretationGap:
    """Tests for the new `over_interpretation_gap` strategy registered alongside
    Bug B (HR-12 regression guard). Strategy records a proposal file rather than
    auto-applying semantic fixes — see strategies.py docstring for rationale."""

    def test_strategy_is_registered(self):
        from core.auto_fix.strategies import STRATEGY_REGISTRY, fix_over_interpretation_gap
        assert STRATEGY_REGISTRY["over_interpretation_gap"] is fix_over_interpretation_gap

    def test_records_proposal_with_canonical_ref(self, tmp_path: Path):
        from core.auto_fix.strategies import fix_over_interpretation_gap
        ctx = FixContext(
            source="review_schema_validator",
            problem_type="over_interpretation_gap",
            severity="medium",
            phase=1,
            project_root=tmp_path,
            details={
                "canonical_ref": "SPEC.md:58",
                "gap_message": "Ambiguous 'excluding subprocess execution'",
                "fr_id": "NFR-01",
                "deliverable": "01-requirements/SRS.md",
            },
        )
        success, action, confidence = fix_over_interpretation_gap(ctx, tmp_path)
        assert success is True
        assert confidence == 60.0
        proposal = tmp_path / ".methodology" / "trace" / "over_interpretation_proposal.md"
        assert proposal.exists()
        text = proposal.read_text()
        assert "SPEC.md:58" in text
        assert "NFR-01" in text
        assert "DERIVED" in text
        assert "NFR-99" in text

    def test_returns_false_when_canonical_ref_missing(self, tmp_path: Path):
        from core.auto_fix.strategies import fix_over_interpretation_gap
        ctx = FixContext(
            source="review_schema_validator",
            problem_type="over_interpretation_gap",
            severity="medium",
            phase=1,
            project_root=tmp_path,
            details={"canonical_ref": "", "gap_message": "x"},
        )
        success, action, confidence = fix_over_interpretation_gap(ctx, tmp_path)
        assert success is False
        assert confidence == 0.0

    def test_idempotent_on_duplicate_canonical_ref(self, tmp_path: Path):
        from core.auto_fix.strategies import fix_over_interpretation_gap
        ctx = FixContext(
            source="review_schema_validator",
            problem_type="over_interpretation_gap",
            severity="medium",
            phase=1,
            project_root=tmp_path,
            details={"canonical_ref": "SPEC.md:11", "gap_message": "x"},
        )
        fix_over_interpretation_gap(ctx, tmp_path)
        fix_over_interpretation_gap(ctx, tmp_path)
        proposal = tmp_path / ".methodology" / "trace" / "over_interpretation_proposal.md"
        text = proposal.read_text()
        # Only one header line for SPEC.md:11
        assert text.count("## over_interpretation: SPEC.md:11") == 1

# ── AutoFixEngine.fix() integration ──────────────────────────────────────────


def test_fix_uses_caller_problem_type(tmp_path: Path):
    """When caller sets context.problem_type, fix() must not silently drop it."""
    from core.auto_fix import AutoFixEngine, FixContext, FixStrategy
    import core.auto_fix.classifier as classifier_module

    engine = AutoFixEngine(project_root=tmp_path)
    ctx = FixContext(
        source="phase_hooks",
        details={"message": "FR-01 not tested"},  # no problem_type in details
        problem_type="missing_traceability",       # set by caller on the dataclass field
        severity="medium",
        phase=1,
        retry_count=0,
        project_root=tmp_path,
    )

    # Track what classify() receives
    received_details = []
    orig_classify = classifier_module.classify

    def tracking_classify(source, details):
        received_details.append(details)
        # Return a valid tuple to avoid crashing fix()
        return FixStrategy.HUMAN_REQUIRED, 0.8, 3, "low_constitution_score", "other"

    classifier_module.classify = tracking_classify
    try:
        engine.fix(ctx)
        assert received_details, "classify() was never called"
        assert "problem_type" in received_details[0], \
            f"classifier received details without problem_type: {received_details}"
        assert received_details[0]["problem_type"] == "missing_traceability", \
            f"problem_type was dropped; classifier got: {received_details[0]}"
    finally:
        classifier_module.classify = orig_classify


pytestmark = pytest.mark.auto_fix
