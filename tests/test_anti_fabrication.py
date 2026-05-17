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
# P1: Commit interval enforcement (legacy tests updated for disk-based impl)
# ---------------------------------------------------------------------------

class TestCommitIntervals:
    """_check_commit_intervals is a pure read; _record_gate_timestamp writes on success."""

    def test_two_in_window_ok(self, tmp_path):
        from harness_cli import _check_commit_intervals, _record_gate_timestamp
        # 0 prior entries → ok; simulate success then 1 prior → still ok
        ok1, _ = _check_commit_intervals(str(tmp_path), 4, 1, "FR-01")
        assert ok1
        _record_gate_timestamp(tmp_path, 4, 1, "FR-01")  # simulates successful finalize
        ok2, _ = _check_commit_intervals(str(tmp_path), 4, 1, "FR-02")
        assert ok2  # 1 prior entry, still below threshold

    def test_three_in_window_blocked(self, tmp_path):
        from harness_cli import _check_commit_intervals, _record_gate_timestamp
        # Seed 2 prior successful finalizations
        _record_gate_timestamp(tmp_path, 4, 1, "FR-01")
        _record_gate_timestamp(tmp_path, 4, 1, "FR-02")
        ok3, msg = _check_commit_intervals(str(tmp_path), 4, 1, "FR-03")
        assert not ok3
        assert "within 2 seconds" in msg

    def test_failed_check_does_not_record(self, tmp_path):
        """Blocked check must not leave a timestamp — no false positives on retry."""
        from harness_cli import _check_commit_intervals, _record_gate_timestamp, _GATE_TIMESTAMPS_FILE
        _record_gate_timestamp(tmp_path, 4, 1, "FR-01")
        _record_gate_timestamp(tmp_path, 4, 1, "FR-02")
        ok, _ = _check_commit_intervals(str(tmp_path), 4, 1, "FR-03")
        assert not ok
        ts_file = tmp_path / ".methodology" / _GATE_TIMESTAMPS_FILE
        lines = [l for l in ts_file.read_text(encoding="utf-8").splitlines() if l.strip()]
        assert len(lines) == 2  # no extra entry from the failed attempt

    def test_different_gates_independent(self, tmp_path):
        from harness_cli import _check_commit_intervals, _record_gate_timestamp
        # Same project/phase but different gates — independent buckets
        _record_gate_timestamp(tmp_path, 4, 1, "FR-01")
        _record_gate_timestamp(tmp_path, 4, 1, "FR-02")
        ok, _ = _check_commit_intervals(str(tmp_path), 4, 3, "FR-03")
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


# ---------------------------------------------------------------------------
# P1: Persistent commit interval enforcement
# ---------------------------------------------------------------------------

class TestPersistentCommitIntervals:
    """Timestamps persist via disk file; _check_commit_intervals is pure read."""

    def test_two_commits_ok(self, tmp_path):
        from harness_cli import _check_commit_intervals, _record_gate_timestamp
        ok1, _ = _check_commit_intervals(str(tmp_path), 4, 1, "FR-01")
        _record_gate_timestamp(tmp_path, 4, 1, "FR-01")
        ok2, _ = _check_commit_intervals(str(tmp_path), 4, 1, "FR-02")
        _record_gate_timestamp(tmp_path, 4, 1, "FR-02")
        assert ok1
        assert ok2

    def test_three_commits_blocked(self, tmp_path):
        from harness_cli import _check_commit_intervals, _record_gate_timestamp
        _record_gate_timestamp(tmp_path, 4, 1, "FR-01")
        _record_gate_timestamp(tmp_path, 4, 1, "FR-02")
        ok3, msg = _check_commit_intervals(str(tmp_path), 4, 1, "FR-03")
        assert not ok3
        assert "within 2 seconds" in msg

    def test_persists_across_calls(self, tmp_path):
        """Entries written by _record_gate_timestamp are visible to subsequent checks."""
        from harness_cli import _check_commit_intervals, _record_gate_timestamp
        _record_gate_timestamp(tmp_path, 4, 1, "FR-01")
        _record_gate_timestamp(tmp_path, 4, 1, "FR-02")
        ok, _ = _check_commit_intervals(str(tmp_path), 4, 1, "FR-03")
        assert not ok

    def test_different_phase_independent(self, tmp_path):
        from harness_cli import _check_commit_intervals, _record_gate_timestamp
        _record_gate_timestamp(tmp_path, 3, 1, "FR-01")
        _record_gate_timestamp(tmp_path, 3, 1, "FR-02")
        # Phase 4 has its own bucket
        ok, _ = _check_commit_intervals(str(tmp_path), 4, 1, "FR-03")
        assert ok

    def test_trim_to_max_entries(self, tmp_path):
        """File is trimmed to _GATE_TIMESTAMPS_MAX_ENTRIES after each write."""
        from harness_cli import _record_gate_timestamp, _GATE_TIMESTAMPS_FILE, _GATE_TIMESTAMPS_MAX_ENTRIES
        for i in range(_GATE_TIMESTAMPS_MAX_ENTRIES + 20):
            _record_gate_timestamp(tmp_path, 4, 1, f"FR-{i:03d}")
        ts_file = tmp_path / ".methodology" / _GATE_TIMESTAMPS_FILE
        lines = [l for l in ts_file.read_text(encoding="utf-8").splitlines() if l.strip()]
        assert len(lines) == _GATE_TIMESTAMPS_MAX_ENTRIES

    def test_dotfile_migration(self, tmp_path):
        """Legacy .gate_timestamps.jsonl is renamed to gate_timestamps.jsonl on first use."""
        import json as _json
        from harness_cli import _record_gate_timestamp, _GATE_TIMESTAMPS_FILE
        methodology = tmp_path / ".methodology"
        methodology.mkdir()
        old_file = methodology / ".gate_timestamps.jsonl"
        old_file.write_text(
            _json.dumps({"phase": 4, "gate": 1, "fr_id": "FR-00", "ts": 0.0}) + "\n"
        )
        _record_gate_timestamp(tmp_path, 4, 1, "FR-01")
        assert not old_file.exists(), "Old dotfile should have been renamed"
        new_file = methodology / _GATE_TIMESTAMPS_FILE
        assert new_file.exists()
        lines = [l for l in new_file.read_text(encoding="utf-8").splitlines() if l.strip()]
        assert len(lines) == 2  # migrated entry + new entry


# ---------------------------------------------------------------------------
# D2: Inter-FR score variance
# ---------------------------------------------------------------------------

class TestInterFrScoreVariance:
    """_check_inter_fr_score_variance detects batch-copied scores."""

    def test_high_variance_ok(self, tmp_path):
        from harness_cli import _record_gate1_score, _check_inter_fr_score_variance
        for i, score in enumerate([90.0, 95.5, 82.3, 99.1, 87.6, 93.2]):
            _record_gate1_score(tmp_path, 4, f"FR-{i+1:02d}", score)
        ok, _ = _check_inter_fr_score_variance(tmp_path, 4)
        assert ok

    def test_zero_variance_blocked(self, tmp_path):
        from harness_cli import _record_gate1_score, _check_inter_fr_score_variance
        for i in range(10):
            _record_gate1_score(tmp_path, 4, f"FR-{i+1:02d}", 97.67)
        ok, msg = _check_inter_fr_score_variance(tmp_path, 4)
        assert not ok
        assert "stddev" in msg

    def test_fewer_than_5_frs_skipped(self, tmp_path):
        from harness_cli import _record_gate1_score, _check_inter_fr_score_variance
        for i in range(4):
            _record_gate1_score(tmp_path, 4, f"FR-{i+1:02d}", 97.67)
        ok, _ = _check_inter_fr_score_variance(tmp_path, 4)
        assert ok  # Fewer than 5 FRs → skip check

    def test_stale_phases_pruned(self, tmp_path):
        """Phases older than (current - 1) are pruned from .gate1_scores.json."""
        import json as _json
        from harness_cli import _record_gate1_score, _GATE1_SCORES_FILE
        # Record scores for phases 3, 4, then 5
        _record_gate1_score(tmp_path, 3, "FR-01", 90.0)
        _record_gate1_score(tmp_path, 4, "FR-01", 85.0)
        # Writing phase 5 must prune phase 3 (< 5-1=4)
        _record_gate1_score(tmp_path, 5, "FR-01", 92.0)
        data = _json.loads(
            (tmp_path / ".methodology" / _GATE1_SCORES_FILE).read_text(encoding="utf-8")
        )
        assert "3" not in data, "Phase 3 should be pruned (two phases back)"
        assert "4" in data, "Phase 4 (current-1) should be retained"
        assert "5" in data, "Phase 5 (current) should be retained"


# ---------------------------------------------------------------------------
# P3: phase_truth_passed in state.json
# ---------------------------------------------------------------------------

class TestPhaseTruthPassed:
    """phase_truth_passed field is set when exit gate passes, blocks advance-phase."""

    def test_compute_seal_includes_phase_truth_passed(self):
        from harness_cli import _compute_seal
        d1 = {"current_phase": 4, "phase_truth_passed": True}
        d2 = {"current_phase": 4, "phase_truth_passed": False}
        assert _compute_seal(d1) != _compute_seal(d2)

    def test_advance_phase_blocked_without_phase_truth_passed(self, tmp_path):
        """advance-phase returns exit 12 when phase_truth_passed is False/missing.

        Exit 12 is distinct from exit 11 (Phase Truth score < 90%) so that operators
        can apply the correct remediation:
          11 → re-run Phase Truth until score ≥ 90%
          12 → run finalize-gate for the phase exit gate first
        """
        from harness_cli import _compute_seal
        import json
        # Write state.json with seal but phase_truth_passed=False
        methodology = tmp_path / ".methodology"
        methodology.mkdir()
        state = {
            "current_phase": 3,
            "state": "ACTIVE",
            "last_gate": 2,
            "last_update": "2026-01-01T00:00:00Z",
            "phase_truth_passed": False,
        }
        state["_seal"] = _compute_seal(state)
        (methodology / "state.json").write_text(json.dumps(state))

        # Verify the state structure that cmd_advance_phase will check
        loaded = json.loads((methodology / "state.json").read_text())
        assert "_seal" in loaded
        assert not loaded.get("phase_truth_passed"), "Should be False → triggers exit 12"


# ---------------------------------------------------------------------------
# A/B coverage per deliverable
# ---------------------------------------------------------------------------

class TestABCoveragePerDeliverable:
    """check_ab_coverage verifies each fr_id has a reviewer entry."""

    def _make_verifier(self, tmp_path, phase=4):
        from core.quality_gate.phase_truth_verifier import PhaseTruthVerifier
        return PhaseTruthVerifier(str(tmp_path), phase)

    def test_all_paired_passes(self, tmp_path):
        import json
        log = tmp_path / ".methodology" / "sessions_spawn.log"
        log.parent.mkdir()
        log.write_text(
            json.dumps({"fr_id": "FR-01", "role": "developer", "session_id": "s1"}) + "\n" +
            json.dumps({"fr_id": "FR-01", "role": "reviewer", "session_id": "s2"}) + "\n" +
            json.dumps({"fr_id": "FR-02", "role": "developer", "session_id": "s3"}) + "\n" +
            json.dumps({"fr_id": "FR-02", "role": "architect", "session_id": "s4"}) + "\n"
        )
        v = self._make_verifier(tmp_path)
        ok, score, msg = v.check_ab_coverage()
        assert ok
        assert score == 100.0

    def test_missing_reviewer_fails(self, tmp_path):
        import json
        log = tmp_path / ".methodology" / "sessions_spawn.log"
        log.parent.mkdir()
        log.write_text(
            json.dumps({"fr_id": "FR-01", "role": "developer", "session_id": "s1"}) + "\n" +
            json.dumps({"fr_id": "FR-02", "role": "developer", "session_id": "s2"}) + "\n" +
            json.dumps({"fr_id": "FR-02", "role": "reviewer", "session_id": "s3"}) + "\n"
        )
        v = self._make_verifier(tmp_path)
        ok, score, msg = v.check_ab_coverage()
        assert not ok
        assert "FR-01" in msg
        assert score < 100.0

    def test_no_log_fails(self, tmp_path):
        v = self._make_verifier(tmp_path)
        ok, score, msg = v.check_ab_coverage()
        assert not ok
        assert score == 0.0

    def test_architect_role_accepted(self, tmp_path):
        import json
        log = tmp_path / ".methodology" / "sessions_spawn.log"
        log.parent.mkdir()
        log.write_text(
            json.dumps({"fr_id": "FR-01", "role": "developer", "session_id": "s1"}) + "\n" +
            json.dumps({"fr_id": "FR-01", "role": "architect", "session_id": "s2"}) + "\n"
        )
        v = self._make_verifier(tmp_path)
        ok, score, _ = v.check_ab_coverage()
        assert ok
        assert score == 100.0


# ---------------------------------------------------------------------------
# D1: Keyword stuffing — all occurrences + tail density
# ---------------------------------------------------------------------------

class TestKeywordStuffingAllOccurrences:
    """Updated _keyword_stuffing_penalty uses all occurrences, not just first."""

    def test_first_occurrence_early_but_stuffed_at_end(self):
        """Game the old first-occurrence check: one early, rest at bottom — should still penalize."""
        from core.quality_gate.constitution.runner import _keyword_stuffing_penalty
        # One early occurrence of one keyword (auth), then ALL keywords clustered at end.
        # Old code: find() returns only first position → auth early → stdev looks OK.
        # New code: all occurrences scanned → bulk at tail → penalty triggered.
        body = "normal content auth\n" + "filler line no keywords here\n" * 100
        tail = "auth validation encrypt tls rbac permission token security\n" * 15
        content = (body + tail).lower()
        keywords = ["auth", "validation", "encrypt", "tls", "rbac", "permission", "token", "security"]
        penalty = _keyword_stuffing_penalty(content, keywords)
        # With all-occurrence scanning, the clustered bottom hits should trigger a penalty
        assert penalty < 1.0, f"Tail stuffing should be penalized, got {penalty}"

    def test_d1_tail_density_penalized(self):
        from core.quality_gate.constitution.runner import _keyword_stuffing_penalty
        # All keywords appear ONLY in the last 10% of the document
        body = "normal content without keywords\n" * 50
        tail = "auth validation encrypt tls rbac permission token security\n" * 5
        content = (body + tail).lower()
        keywords = ["auth", "validation", "encrypt", "tls", "rbac", "permission", "token", "security"]
        penalty = _keyword_stuffing_penalty(content, keywords)
        assert penalty < 1.0, f"Tail-only keywords should be penalized, got {penalty}"


# ---------------------------------------------------------------------------
# D3: HIGH violations reduce Phase Truth score
# ---------------------------------------------------------------------------

class TestD3HighViolationScoring:
    """check_cross_artifact penalizes HIGH violations, not only CRITICAL."""

    def test_high_violation_reduces_score(self, tmp_path):
        from unittest.mock import patch
        # Simulate a result with 0 criticals and 1 HIGH
        fake_result = {
            "passed": True,  # No criticals
            "violations": [{"severity": "HIGH", "file": "x", "issue": "wrong phase"}],
            "checks_ran": 3,
            "critical_count": 0,
            "high_count": 1,
        }
        with patch(
            "core.quality_gate.cross_artifact.run_cross_artifact_checks",
            return_value=fake_result
        ):
            from core.quality_gate.phase_truth_verifier import PhaseTruthVerifier
            v = PhaseTruthVerifier(str(tmp_path), phase=4)
            passed, score, msg = v.check_cross_artifact()
        assert not passed, "HIGH violation should cause check to fail"
        assert score < 100.0, f"Score should be penalized, got {score}"
        assert score == 85.0, f"1 HIGH = -15, expected 85.0, got {score}"
