"""Tests for anti-fabrication defenses (D1, D2, D3, P1, P3, S2, S3)."""
from __future__ import annotations

import json
from pathlib import Path

# ---------------------------------------------------------------------------
# D1: Keyword stuffing penalty
# ---------------------------------------------------------------------------

class TestKeywordStuffingPenalty:
    """_keyword_stuffing_penalty detects unnatural keyword clustering."""

    def test_natural_distribution_no_penalty(self):
        from core.quality_gate.constitution.runner import _keyword_stuffing_penalty
        # Keywords spread across paragraphs
        content = (
            "# Security Review\n\n"
            "All inputs require validation and auth checks.\n\n"
            "## Encryption\n\n"
            "We use TLS for transport and encrypt sensitive data at rest.\n\n"
            "## Access Control\n\n"
            "RBAC permissions are enforced with token verification.\n\n"
            "## Monitoring\n\n"
            "Rate limiting prevents abuse; security audits run weekly.\n\n"
            "PII is masked in logs. HMAC signatures verify integrity.\n"
        )
        keywords = ["auth", "validation", "encrypt", "tls", "rbac",
                    "permission", "token", "security", "rate limit",
                    "pii", "hmac", "signature", "verify"]
        penalty = _keyword_stuffing_penalty(content.lower(), keywords)
        assert penalty == 1.0, f"Natural distribution should have no penalty, got {penalty}"

    def test_stuffed_keywords_penalty(self):
        from core.quality_gate.constitution.runner import _keyword_stuffing_penalty
        # All keywords dumped in one paragraph at the bottom — classic stuffing
        content = (
            "# Test Plan\n\n"
            "This document describes the testing strategy.\n\n"
            "## Test Cases\n\n"
            "Each FR has at least one test case.\n\n" * 5
            + "Keywords: auth validation encrypt tls rbac permission token "
              "security rate limit pii hmac signature verify\n"
        )
        keywords = ["auth", "validation", "encrypt", "tls", "rbac",
                    "permission", "token", "security", "rate limit",
                    "pii", "hmac", "signature", "verify"]
        penalty = _keyword_stuffing_penalty(content.lower(), keywords)
        assert penalty < 1.0, f"Stuffed keywords should get penalty, got {penalty}"

    def test_too_few_keywords_no_penalty(self):
        from core.quality_gate.constitution.runner import _keyword_stuffing_penalty
        content = "# Short doc\n\nJust auth here.\n" * 10
        penalty = _keyword_stuffing_penalty(content.lower(), ["auth", "tls"])
        assert penalty == 1.0  # Only 2 keywords found → <3 → skip

    def test_short_content_no_penalty(self):
        from core.quality_gate.constitution.runner import _keyword_stuffing_penalty
        content = "short"
        penalty = _keyword_stuffing_penalty(content, ["auth", "tls", "rbac"])
        assert penalty == 1.0  # Content < 200 chars → skip


# ---------------------------------------------------------------------------
# D3: Cross-artifact consistency
# ---------------------------------------------------------------------------

class TestCheckPhaseTitle:
    """check_phase_title catches wrong-phase copy-paste."""

    def test_wrong_phase_detected(self, tmp_path):
        from core.quality_gate.cross_artifact import check_phase_title
        (tmp_path / "04-testing").mkdir(parents=True)
        (tmp_path / "04-testing" / "TEST_PLAN.md").write_text(
            "# Phase 3 Testing Plan\n\nContent here.\n"
        )
        violations = check_phase_title(tmp_path, phase=4)
        highs = [v for v in violations if v["severity"] == "HIGH"]
        mediums = [v for v in violations if v["severity"] == "MEDIUM"]
        assert len(highs) == 1
        assert "Phase 3" in highs[0]["issue"]
        # elif prevents double-report: MEDIUM should NOT also fire
        assert len(mediums) == 0, (
            f"elif should prevent MEDIUM when HIGH already fired, got {mediums}"
        )

    def test_correct_phase_no_violations(self, tmp_path):
        from core.quality_gate.cross_artifact import check_phase_title
        (tmp_path / "04-testing").mkdir(parents=True)
        (tmp_path / "04-testing" / "TEST_PLAN.md").write_text(
            "# Phase 4 Testing Plan\n\nContent here.\n"
        )
        violations = check_phase_title(tmp_path, phase=4)
        assert len(violations) == 0, f"Correct title should have no violations, got {violations}"

    def test_no_h1_no_violations(self, tmp_path):
        from core.quality_gate.cross_artifact import check_phase_title
        (tmp_path / "04-testing").mkdir(parents=True)
        (tmp_path / "04-testing" / "TEST_PLAN.md").write_text(
            "No H1 heading here.\n"
        )
        violations = check_phase_title(tmp_path, phase=4)
        assert len(violations) == 0

    def test_missing_phase_reference(self, tmp_path):
        from core.quality_gate.cross_artifact import check_phase_title
        (tmp_path / "04-testing").mkdir(parents=True)
        (tmp_path / "04-testing" / "TEST_PLAN.md").write_text(
            "# Testing Plan\n\nNo phase number in title.\n"
        )
        violations = check_phase_title(tmp_path, phase=4)
        mediums = [v for v in violations if v["severity"] == "MEDIUM"]
        assert len(mediums) == 1
        assert "does not reference Phase 4" in mediums[0]["issue"]


class TestCheckFrCoverage:
    """check_fr_coverage validates FR claims against session logs."""

    def test_unverified_fr_detected(self, tmp_path):
        from core.quality_gate.cross_artifact import check_fr_coverage
        (tmp_path / "04-testing").mkdir(parents=True)
        (tmp_path / ".methodology").mkdir(parents=True)
        (tmp_path / "04-testing" / "TEST_RESULTS.md").write_text(
            "# Test Results\n\nFR-01 passed.\nFR-02 passed.\nFR-03 passed.\n"
        )
        # sessions_spawn.log only has FR-01
        (tmp_path / ".methodology" / "sessions_spawn.log").write_text(
            json.dumps({"fr_id": "FR-01", "role": "developer", "session_id": "s1"}) + "\n"
        )
        violations = check_fr_coverage(tmp_path, 4)
        unverified = [v["fr_id"] for v in violations]
        assert "FR-02" in unverified
        assert "FR-03" in unverified

    def test_no_sessions_log(self, tmp_path):
        from core.quality_gate.cross_artifact import check_fr_coverage
        (tmp_path / "04-testing").mkdir(parents=True)
        (tmp_path / "04-testing" / "TEST_RESULTS.md").write_text(
            "# Test Results\n\nFR-01 passed.\n"
        )
        violations = check_fr_coverage(tmp_path, 4)
        assert len(violations) == 1
        assert "sessions_spawn.log not found" in violations[0]["issue"]


# ---------------------------------------------------------------------------
# P3: State integrity seal
# ---------------------------------------------------------------------------

class TestStateIntegrity:
    """_compute_seal and _verify_state_integrity detect tampered state.json."""

    def test_seal_roundtrip(self, tmp_path):
        from harness_cli import _compute_seal
        data = {"current_phase": 4, "state": "ACTIVE", "last_gate": 3}
        seal = _compute_seal(data)
        assert len(seal) == 16
        # Same data → same seal
        assert _compute_seal(data) == seal
        # Different data → different seal
        data2 = {"current_phase": 5, "state": "ACTIVE", "last_gate": 3}
        assert _compute_seal(data2) != seal

    def test_legacy_state_no_seal(self, tmp_path):
        from harness_cli import _verify_state_integrity
        (tmp_path / ".methodology").mkdir(parents=True)
        (tmp_path / ".methodology" / "state.json").write_text(
            json.dumps({"current_phase": 3, "state": "ACTIVE"})
        )
        ok, diag = _verify_state_integrity(tmp_path)
        assert not ok
        assert diag == "LEGACY"

    def test_tampered_seal_detected(self, tmp_path):
        from harness_cli import _compute_seal, _verify_state_integrity
        (tmp_path / ".methodology").mkdir(parents=True)
        data = {"current_phase": 3, "state": "ACTIVE", "last_gate": 2}
        data["_seal"] = _compute_seal(data)
        (tmp_path / ".methodology" / "state.json").write_text(json.dumps(data))
        # Verify original is valid
        ok, _ = _verify_state_integrity(tmp_path)
        assert ok, "Valid seal should pass"
        # Tamper: change current_phase without updating seal
        data["current_phase"] = 7
        (tmp_path / ".methodology" / "state.json").write_text(json.dumps(data))
        ok, diag = _verify_state_integrity(tmp_path)
        assert not ok
        assert diag == "TAMPERED"

    def test_no_state_file_passes(self, tmp_path):
        from harness_cli import _verify_state_integrity
        ok, diag = _verify_state_integrity(tmp_path)
        assert ok
        assert diag == ""


# ---------------------------------------------------------------------------
# P1: Commit interval enforcement
# ---------------------------------------------------------------------------

class TestCommitIntervals:
    """_check_commit_intervals blocks batch commits."""

    def test_two_in_window_ok(self):
        from harness_cli import _check_commit_intervals, _GATE_COMMIT_LOG
        # Clear in-memory log from other tests
        _GATE_COMMIT_LOG.clear()
        ok1, _ = _check_commit_intervals("/test/proj", 4, 1, "FR-01")
        assert ok1
        ok2, _ = _check_commit_intervals("/test/proj", 4, 1, "FR-02")
        assert ok2  # 2 in window still OK

    def test_three_in_window_blocked(self):
        from harness_cli import _check_commit_intervals, _GATE_COMMIT_LOG
        _GATE_COMMIT_LOG.clear()
        _check_commit_intervals("/test/proj2", 4, 1, "FR-01")
        _check_commit_intervals("/test/proj2", 4, 1, "FR-02")
        ok3, msg = _check_commit_intervals("/test/proj2", 4, 1, "FR-03")
        assert not ok3
        assert "within 2 seconds" in msg

    def test_different_gates_independent(self):
        from harness_cli import _check_commit_intervals, _GATE_COMMIT_LOG
        _GATE_COMMIT_LOG.clear()
        # Same project/phase but different gates — independent buckets
        _check_commit_intervals("/test/proj3", 4, 1, "FR-01")
        _check_commit_intervals("/test/proj3", 4, 1, "FR-02")
        ok, _ = _check_commit_intervals("/test/proj3", 4, 3, "FR-03")
        assert ok  # Gate 3 ≠ Gate 1 → different bucket


# ---------------------------------------------------------------------------
# S3: Tool evidence enforcement
# ---------------------------------------------------------------------------

class TestToolEvidence:
    """_check_tool_evidence validates tool_output / tool_evidence in result JSON."""

    def test_missing_tool_evidence_blocked(self):
        from harness.harness_bridge import _check_tool_evidence
        from harness.harness_bridge import GateContext
        ctx = GateContext(
            gate_num=3, config={}, project_root="/nonexistent",
            phase=4, fr_id=None,
            ssi_scripts_dir="", ssi_prompts_dir="", ssi_schemas_dir="",
            work_dir="", sab_data={},
        )
        # No tool_output or tool_evidence → violation
        raw = {"breakdown": {"secrets_scanning": {"score": 90, "threshold": 100}}}
        # This will fail to find the YAML config since /nonexistent doesn't exist
        # — empty violations list is expected for missing config
        violations = _check_tool_evidence(ctx, raw)
        # With no config file, returns [] (cannot enforce)
        assert violations == []

    def test_tool_evidence_accepted(self):
        from harness.harness_bridge import _check_tool_evidence, GateContext
        import tempfile, yaml
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "harness" / "gate_configs").mkdir(parents=True)
            (root / "harness" / "gate_configs" / "gate3_p4_exit.yaml").write_text(
                yaml.dump({
                    "gate": 3, "dimensions": [
                        {"name": "secrets_scanning", "requires_tool_execution": True},
                    ]
                })
            )
            ctx = GateContext(
                gate_num=3, config={}, project_root=str(root),
                phase=4, fr_id=None,
                ssi_scripts_dir="", ssi_prompts_dir="", ssi_schemas_dir="",
                work_dir="", sab_data={},
            )
            # Has tool_evidence
            raw = {"breakdown": {"secrets_scanning": {
                "score": 90, "threshold": 100,
                "tool_evidence": "gitleaks detect: 0 secrets found in 45 files",
            }}}
            violations = _check_tool_evidence(ctx, raw)
            assert violations == [], f"Expected no violations, got {violations}"

    def test_short_tool_evidence_rejected(self):
        from harness.harness_bridge import _check_tool_evidence, GateContext
        import tempfile, yaml
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "harness" / "gate_configs").mkdir(parents=True)
            (root / "harness" / "gate_configs" / "gate3_p4_exit.yaml").write_text(
                yaml.dump({
                    "gate": 3, "dimensions": [
                        {"name": "secrets_scanning", "requires_tool_execution": True},
                    ]
                })
            )
            ctx = GateContext(
                gate_num=3, config={}, project_root=str(root),
                phase=4, fr_id=None,
                ssi_scripts_dir="", ssi_prompts_dir="", ssi_schemas_dir="",
                work_dir="", sab_data={},
            )
            # tool_evidence too short (3 chars)
            raw = {"breakdown": {"secrets_scanning": {
                "score": 90, "threshold": 100,
                "tool_evidence": "ok",
            }}}
            violations = _check_tool_evidence(ctx, raw)
            assert len(violations) == 1
            assert "too short" in violations[0]
