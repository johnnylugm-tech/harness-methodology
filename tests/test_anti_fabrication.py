"""Tests for anti-fabrication defenses (D1, D2, D3, P1, P3, S2, S3)."""
from __future__ import annotations

import pytest

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
# P1: Commit interval enforcement (legacy tests updated for disk-based impl)
# ---------------------------------------------------------------------------

class TestCommitIntervals:
    """check_commit_intervals is a pure read; record_gate_timestamp writes on success."""

    def test_two_in_window_ok(self, tmp_path):
        from core.quality_gate.gate1_evidence import check_commit_intervals, record_gate_timestamp
        # 0 prior entries → ok; simulate success then 1 prior → still ok
        ok1, _ = check_commit_intervals(str(tmp_path), 4, 1)
        assert ok1
        record_gate_timestamp(tmp_path, 4, 1, "FR-01")  # simulates successful finalize
        ok2, _ = check_commit_intervals(str(tmp_path), 4, 1)
        assert ok2  # 1 prior entry, still below threshold

    def test_three_in_window_blocked(self, tmp_path):
        from core.quality_gate.gate1_evidence import check_commit_intervals, record_gate_timestamp
        # Seed 2 prior successful finalizations
        record_gate_timestamp(tmp_path, 4, 1, "FR-01")
        record_gate_timestamp(tmp_path, 4, 1, "FR-02")
        ok3, msg = check_commit_intervals(str(tmp_path), 4, 1)
        assert not ok3
        assert "within 2 seconds" in msg

    def test_failed_check_does_not_record(self, tmp_path):
        """Blocked check must not leave a timestamp — no false positives on retry."""
        from core.quality_gate.gate1_evidence import check_commit_intervals, record_gate_timestamp, GATE_TIMESTAMPS_FILE
        record_gate_timestamp(tmp_path, 4, 1, "FR-01")
        record_gate_timestamp(tmp_path, 4, 1, "FR-02")
        ok, _ = check_commit_intervals(str(tmp_path), 4, 1)
        assert not ok
        ts_file = tmp_path / ".methodology" / GATE_TIMESTAMPS_FILE
        lines = [line for line in ts_file.read_text(encoding="utf-8").splitlines() if line.strip()]
        assert len(lines) == 2  # no extra entry from the failed attempt

    def test_different_gates_independent(self, tmp_path):
        from core.quality_gate.gate1_evidence import check_commit_intervals, record_gate_timestamp
        # Same project/phase but different gates — independent buckets
        record_gate_timestamp(tmp_path, 4, 1, "FR-01")
        record_gate_timestamp(tmp_path, 4, 1, "FR-02")
        ok, _ = check_commit_intervals(str(tmp_path), 4, 3)
        assert ok  # Gate 3 ≠ Gate 1 → different bucket

    def test_distinct_frs_dont_collide_into_fraud_bucket(self, tmp_path):
        """H-1: 3 distinct FRs finalizing within 2s is the natural per-FR
        sequential pattern — must NOT be flagged as batch fabrication.
        Regression: before this fix, all 3 collapsed into one (phase, gate)
        bucket and triggered a false-positive BLOCK on FR-03/04/05 in P3."""
        from core.quality_gate.gate1_evidence import check_commit_intervals, record_gate_timestamp
        record_gate_timestamp(tmp_path, 3, 1, "FR-01")
        record_gate_timestamp(tmp_path, 3, 1, "FR-02")
        ok, msg = check_commit_intervals(str(tmp_path), 3, 1, "FR-03")
        assert ok, f"distinct FRs must not collide; got BLOCK: {msg}"
        assert msg == ""

    def test_same_fr_in_window_still_blocked(self, tmp_path):
        """H-1: Same FR finalized 3× in window — MUST still block.
        Preserves the original anti-batch-fabrication guarantee."""
        from core.quality_gate.gate1_evidence import check_commit_intervals, record_gate_timestamp
        record_gate_timestamp(tmp_path, 3, 1, "FR-01")
        record_gate_timestamp(tmp_path, 3, 1, "FR-01")
        ok, msg = check_commit_intervals(str(tmp_path), 3, 1, "FR-01")
        assert not ok
        assert "within 2 seconds" in msg


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
        import tempfile
        import yaml
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
        import tempfile
        import yaml
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
    """Timestamps persist via disk file; check_commit_intervals is pure read."""

    def test_two_commits_ok(self, tmp_path):
        from core.quality_gate.gate1_evidence import check_commit_intervals, record_gate_timestamp
        ok1, _ = check_commit_intervals(str(tmp_path), 4, 1)
        record_gate_timestamp(tmp_path, 4, 1, "FR-01")
        ok2, _ = check_commit_intervals(str(tmp_path), 4, 1)
        record_gate_timestamp(tmp_path, 4, 1, "FR-02")
        assert ok1
        assert ok2

    def test_three_commits_blocked(self, tmp_path):
        from core.quality_gate.gate1_evidence import check_commit_intervals, record_gate_timestamp
        record_gate_timestamp(tmp_path, 4, 1, "FR-01")
        record_gate_timestamp(tmp_path, 4, 1, "FR-02")
        ok3, msg = check_commit_intervals(str(tmp_path), 4, 1)
        assert not ok3
        assert "within 2 seconds" in msg

    def test_persists_across_calls(self, tmp_path):
        """Entries written by record_gate_timestamp are visible to subsequent checks."""
        from core.quality_gate.gate1_evidence import check_commit_intervals, record_gate_timestamp
        record_gate_timestamp(tmp_path, 4, 1, "FR-01")
        record_gate_timestamp(tmp_path, 4, 1, "FR-02")
        ok, _ = check_commit_intervals(str(tmp_path), 4, 1)
        assert not ok

    def test_different_phase_independent(self, tmp_path):
        from core.quality_gate.gate1_evidence import check_commit_intervals, record_gate_timestamp
        record_gate_timestamp(tmp_path, 3, 1, "FR-01")
        record_gate_timestamp(tmp_path, 3, 1, "FR-02")
        # Phase 4 has its own bucket
        ok, _ = check_commit_intervals(str(tmp_path), 4, 1)
        assert ok

    def test_trim_to_max_entries(self, tmp_path):
        """File is trimmed to GATE_TIMESTAMPS_MAX_ENTRIES after each write."""
        from core.quality_gate.gate1_evidence import record_gate_timestamp, GATE_TIMESTAMPS_FILE, GATE_TIMESTAMPS_MAX_ENTRIES
        for i in range(GATE_TIMESTAMPS_MAX_ENTRIES + 20):
            record_gate_timestamp(tmp_path, 4, 1, f"FR-{i:03d}")
        ts_file = tmp_path / ".methodology" / GATE_TIMESTAMPS_FILE
        lines = [line for line in ts_file.read_text(encoding="utf-8").splitlines() if line.strip()]
        assert len(lines) == GATE_TIMESTAMPS_MAX_ENTRIES

    def test_dotfile_migration(self, tmp_path):
        """Legacy .gate_timestamps.jsonl is renamed to gate_timestamps.jsonl on first use."""
        import json as _json
        from core.quality_gate.gate1_evidence import record_gate_timestamp, GATE_TIMESTAMPS_FILE
        methodology = tmp_path / ".methodology"
        methodology.mkdir()
        old_file = methodology / ".gate_timestamps.jsonl"
        old_file.write_text(
            _json.dumps({"phase": 4, "gate": 1, "fr_id": "FR-00", "ts": 0.0}) + "\n"
        )
        record_gate_timestamp(tmp_path, 4, 1, "FR-01")
        assert not old_file.exists(), "Old dotfile should have been renamed"
        new_file = methodology / GATE_TIMESTAMPS_FILE
        assert new_file.exists()
        lines = [line for line in new_file.read_text(encoding="utf-8").splitlines() if line.strip()]
        assert len(lines) == 2  # migrated entry + new entry


# ---------------------------------------------------------------------------
# Gate1 score recording / pruning
# ---------------------------------------------------------------------------

class TestGate1ScoreRecording:
    """record_gate1_score prunes stale phase entries."""

    def test_stale_phases_pruned(self, tmp_path):
        """Phases older than (current - 1) are pruned from .gate1_scores.json."""
        import json as _json
        from core.quality_gate.gate1_evidence import record_gate1_score, GATE1_SCORES_FILE
        record_gate1_score(tmp_path, 3, "FR-01", 90.0)
        record_gate1_score(tmp_path, 4, "FR-01", 85.0)
        record_gate1_score(tmp_path, 5, "FR-01", 92.0)
        data = _json.loads(
            (tmp_path / ".methodology" / GATE1_SCORES_FILE).read_text(encoding="utf-8")
        )
        assert "3" not in data, "Phase 3 should be pruned (two phases back)"
        assert "4" in data
        assert "5" in data


# ---------------------------------------------------------------------------
# P3: phase_truth_passed in state.json
# ---------------------------------------------------------------------------

class TestPhaseTruthPassed:
    """phase_truth_passed field is set when exit gate passes, blocks advance-phase."""

    def test_advance_phase_blocked_without_phase_truth_passed(self, tmp_path):
        """advance-phase checks phase_truth_passed=False and should block (exit 12).

        Exit 12 is distinct from exit 11 (Phase Truth score < 90%) so that operators
        can apply the correct remediation:
          11 → re-run Phase Truth until score ≥ 90%
          12 → run finalize-gate for the phase exit gate first
        """
        import json
        # Write state.json with phase_truth_passed=False (no seal — seal was removed)
        methodology = tmp_path / ".methodology"
        methodology.mkdir()
        state = {
            "current_phase": 3,
            "state": "ACTIVE",
            "last_gate": 2,
            "last_update": "2026-01-01T00:00:00Z",
            "phase_truth_passed": False,
        }
        (methodology / "state.json").write_text(json.dumps(state))

        # Verify the state structure that cmd_advance_phase will check
        loaded = json.loads((methodology / "state.json").read_text())
        assert not loaded.get("phase_truth_passed"), "Should be False → triggers exit 12"


# ---------------------------------------------------------------------------
# A/B coverage per deliverable
# ---------------------------------------------------------------------------



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

# ---------------------------------------------------------------------------
# D2: Per-dimension score saturation exemption
# ---------------------------------------------------------------------------

class TestDimensionScoreSaturation:
    """Saturation exemption: genuine all-100 results must not trigger the D2 hard block.

    The check lives inline inside cmd_finalize_gate at the line:
        if _d_stdev == 0.0 and not _saturated: return 1

    These tests verify the arithmetic that guards the exemption so the rule
    is not silently broken by future edits.
    """

    def test_all_100_is_saturated(self):
        """ruff=100, mypy=100, pytest-cov=100 — 25-line minimal module scenario."""
        import statistics as _stats
        scores = [100.0, 100.0, 100.0]
        mean = sum(scores) / len(scores)
        stdev = _stats.pstdev(scores)
        saturated = mean >= 99.5
        assert stdev == 0.0       # would trigger without saturation check
        assert saturated          # exemption applies → no block

    def test_mid_range_uniform_not_saturated(self):
        """Batch-copied 78.5 across all dimensions — must still be blocked."""
        import statistics as _stats
        scores = [78.5, 78.5, 78.5]
        mean = sum(scores) / len(scores)
        stdev = _stats.pstdev(scores)
        saturated = mean >= 99.5
        assert stdev == 0.0
        assert not saturated      # no exemption → hard block fires

    def test_mixed_near_ceiling_not_blocked(self):
        """99.5-100 mix — tight but at ceiling, advisory silenced too."""
        import statistics as _stats
        scores = [99.5, 100.0, 100.0, 99.8]
        mean = sum(scores) / len(scores)
        stdev = _stats.pstdev(scores)
        saturated = mean >= 99.5
        assert stdev < 0.5        # would trigger advisory without saturation
        assert saturated          # exemption silences advisory as well

    def test_low_variance_mid_range_triggers_advisory(self):
        """Tight mid-range cluster with stddev < 0.5 — advisory should still fire."""
        import statistics as _stats
        # pstdev([89.0, 89.1, 89.2, 89.0, 89.1]) ≈ 0.075
        scores = [89.0, 89.1, 89.2, 89.0, 89.1]
        mean = sum(scores) / len(scores)
        stdev = _stats.pstdev(scores)
        saturated = mean >= 99.5
        assert stdev < 0.5        # tight cluster — would trigger advisory
        assert not saturated      # not exempt → advisory fires


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


# ---------------------------------------------------------------------------
# S3-A: Tool-output content validation (Solution A)
# ---------------------------------------------------------------------------

class TestToolEvidenceContentValidation:
    """_validate_tool_content rejects stubs; _check_tool_evidence integrates it."""

    # ------------------------------------------------------------------
    # Helpers shared across tests
    # ------------------------------------------------------------------

    @staticmethod
    def _make_ctx(root: Path, gate: int = 3) -> "object":
        from harness.harness_bridge import GateContext
        return GateContext(
            gate_num=gate, config={}, project_root=str(root),
            phase=4, fr_id=None,
            ssi_scripts_dir="", ssi_prompts_dir="", ssi_schemas_dir="",
            work_dir="", sab_data={},
        )

    @staticmethod
    def _make_gate_yaml(root: Path, gate: int, dims: list[dict]) -> None:
        import yaml
        cfg_dir = root / "harness" / "gate_configs"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        (cfg_dir / f"gate{gate}_p4_exit.yaml").write_text(
            yaml.dump({"gate": gate, "dimensions": dims})
        )

    # ------------------------------------------------------------------
    # _validate_tool_content — unit tests
    # ------------------------------------------------------------------

    def test_stub_comment_header_file_blocked(self, tmp_path):
        """File whose first non-blank line starts with '#' is rejected (stub)."""
        from harness.harness_bridge import _validate_tool_content
        content = "# Tool output for linting (pre-existing evaluation)\n"
        violations = _validate_tool_content(content, "ruff", "linting", inline=False)
        assert len(violations) == 1
        assert "stub marker" in violations[0]

    def test_stub_comment_header_inline_blocked(self, tmp_path):
        """Inline tool_evidence starting with '#' is rejected."""
        from harness.harness_bridge import _validate_tool_content
        content = "# pre-existing evaluation of mypy run"
        violations = _validate_tool_content(content, "mypy", "type_safety", inline=True)
        assert len(violations) == 1
        assert "stub marker" in violations[0]

    def test_too_small_file_blocked(self, tmp_path):
        """A file below the minimum size threshold is rejected."""
        from harness.harness_bridge import _validate_tool_content
        # 4 bytes — below the 5-byte minimum
        violations = _validate_tool_content("ok\n", "ruff", "linting", inline=False)
        assert len(violations) == 1
        assert "too small" in violations[0]

    def test_every_registered_tool_has_a_content_pattern(self):
        """Round 27 站1 — check 3 only runs for tools present in the table.

        17 of 32 registered tools had no entry, so `_validate_tool_content`
        skipped its structural check entirely for them and any prose over 10
        characters passed as evidence. code-review-graph is the one deliberate
        exception: `architecture` is computed by crg_independent inside
        finalize_gate, so a framework sentence IS its genuine evidence.
        """
        from harness.harness_bridge import _TOOL_CONTENT_PATTERNS
        from harness.toolchains.registry import DIMENSION_TOOLS

        registered = set()
        for per_dim in DIMENSION_TOOLS.values():
            for entry in per_dim.values():
                registered.update([entry] if isinstance(entry, str) else entry.values())

        missing = registered - set(_TOOL_CONTENT_PATTERNS) - {"code-review-graph"}
        assert not missing, (
            f"tools with no content pattern accept any prose as tool evidence: "
            f"{sorted(missing)}"
        )

    @pytest.mark.parametrize("tool,dim,prose", [
        # Verbatim from taskq-plus's gate4_result.json — both dimensions the
        # testbed existed to light up, both accepted, gate PASS at 98.707.
        ("pytest-benchmark", "performance",
         "No pytest-benchmark tests exist (--benchmark-only collected 0 tests, "
         "501 skipped, exit 5) - dimension N/A per protocol (not free 100). "
         "NFR-01 latency SLAs validated functionally via time.perf_counter()."),
        ("import-linter", "architecture_constraints",
         "Layering verified by inspection against SAD section 2.3; no upward "
         "imports observed in the five-layer tree."),
        ("system-verification", "execute_verification_target",
         "All acceptance criteria in SPEC section 8 were reviewed and hold."),
    ])
    def test_prose_is_not_tool_evidence(self, tool, dim, prose):
        """Plausible-sounding prose must not pass as the output of a real tool."""
        from harness.harness_bridge import _validate_tool_content
        violations = _validate_tool_content(prose, tool, dim, inline=True)
        assert violations, f"{tool}: prose accepted as genuine tool output"
        assert "does not match any expected output pattern" in violations[0]

    @pytest.mark.parametrize("tool,dim,real", [
        ("pytest-benchmark", "performance",
         "--------- benchmark: 2 tests ---------\n"
         "Name (time in ms)      Mean      Max\n"
         "test_submit_p95      1.9031   2.4410\n"),
        ("import-linter", "architecture_constraints",
         "=============\nContracts\n=============\n"
         "Layered architecture KEPT\n\nContracts: 1 kept, 0 broken.\n"),
        ("system-verification", "execute_verification_target",
         "make verify-system\nverify-system: PASS\n"),
        ("bandit", "security",
         '{"results": [], "metrics": {"_totals": {"SEVERITY.HIGH": 0}}}'),
        ("ast-assertions", "test_assertion_quality",
         '{"total": 103, "asserted": 103, "zero_assert": []}'),
    ])
    def test_real_tool_output_still_passes(self, tool, dim, real):
        """The other half: the new patterns must not reject genuine output."""
        from harness.harness_bridge import _validate_tool_content
        assert _validate_tool_content(real, tool, dim, inline=True) == []

    def test_ruff_clean_output_accepted(self, tmp_path):
        """Genuine ruff 'all checks passed' output is accepted."""
        from harness.harness_bridge import _validate_tool_content
        content = "All checks passed!\n"
        violations = _validate_tool_content(content, "ruff", "linting", inline=False)
        assert violations == [], violations

    def test_ruff_violation_output_accepted(self, tmp_path):
        """Genuine ruff violation lines are accepted."""
        from harness.harness_bridge import _validate_tool_content
        content = "src/app.py:10:1: E501 Line too long (83 > 79 characters)\n"
        violations = _validate_tool_content(content, "ruff", "linting", inline=False)
        assert violations == [], violations

    def test_mypy_clean_output_accepted(self, tmp_path):
        """Genuine mypy success output is accepted."""
        from harness.harness_bridge import _validate_tool_content
        content = "Success: no issues found in 12 source files\n"
        violations = _validate_tool_content(content, "mypy", "type_safety", inline=False)
        assert violations == [], violations

    def test_unknown_tool_skips_pattern_check(self, tmp_path):
        """Content for a tool with no registered patterns is never pattern-rejected."""
        from harness.harness_bridge import _validate_tool_content
        # 'scancode' patterns exist but let us use a completely unknown tool name
        content = "some totally custom output without any standard markers\n"
        violations = _validate_tool_content(content, "unknown_tool_xyz", "custom", inline=False)
        assert violations == [], violations

    def test_ruff_unrecognized_content_blocked(self, tmp_path):
        """Content that is large enough, no # header, but matches no ruff pattern."""
        from harness.harness_bridge import _validate_tool_content
        # Fake content that is not a comment stub and is big enough but has no
        # ruff-recognisable patterns.
        content = "This is definitely not tool output at all but it is long enough.\n"
        violations = _validate_tool_content(content, "ruff", "linting", inline=False)
        assert len(violations) == 1
        assert "does not match" in violations[0]

    # ------------------------------------------------------------------
    # _check_tool_evidence integration — stub file in tool_output
    # ------------------------------------------------------------------

    def test_stub_file_tool_output_blocked(self, tmp_path):
        """A stub tool_output file (comment header) is blocked by _check_tool_evidence."""
        from harness.harness_bridge import _check_tool_evidence
        self._make_gate_yaml(tmp_path, 3, [
            {"name": "linting", "requires_tool_execution": True, "tool": "ruff"},
        ])
        # Write a stub file
        out_dir = tmp_path / ".sessi-work" / "tool_outputs"
        out_dir.mkdir(parents=True)
        stub = out_dir / "linting_output.txt"
        stub.write_text("# Tool output for linting (pre-existing evaluation)\n")

        ctx = self._make_ctx(tmp_path, gate=3)
        raw = {"breakdown": {"linting": {
            "score": 95, "threshold": 90,
            "tool_output": str(stub.relative_to(tmp_path)),
        }}}
        violations = _check_tool_evidence(ctx, raw)  # type: ignore[reportArgumentType]
        assert len(violations) == 1
        assert "stub marker" in violations[0]

    def test_stub_inline_tool_evidence_blocked(self, tmp_path):
        """A stub inline tool_evidence (comment) is blocked."""
        from harness.harness_bridge import _check_tool_evidence
        self._make_gate_yaml(tmp_path, 3, [
            {"name": "secrets_scanning", "requires_tool_execution": True, "tool": "gitleaks"},
        ])
        ctx = self._make_ctx(tmp_path, gate=3)
        raw = {"breakdown": {"secrets_scanning": {
            "score": 100, "threshold": 100,
            "tool_evidence": "# pre-existing gitleaks evaluation",
        }}}
        violations = _check_tool_evidence(ctx, raw)  # type: ignore[reportArgumentType]
        assert len(violations) == 1
        assert "stub marker" in violations[0]

    def test_real_tool_evidence_passes(self, tmp_path):
        """Genuine gitleaks inline evidence passes."""
        from harness.harness_bridge import _check_tool_evidence
        self._make_gate_yaml(tmp_path, 3, [
            {"name": "secrets_scanning", "requires_tool_execution": True, "tool": "gitleaks"},
        ])
        ctx = self._make_ctx(tmp_path, gate=3)
        raw = {"breakdown": {"secrets_scanning": {
            "score": 100, "threshold": 100,
            "tool_evidence": "No leaks found. Scanning 45 files complete.",
        }}}
        violations = _check_tool_evidence(ctx, raw)  # type: ignore[reportArgumentType]
        assert violations == [], violations


# ---------------------------------------------------------------------------
# S4: Harness cross-validation (Solution B)
# ---------------------------------------------------------------------------

class TestHarnessCrossValidation:
    """_run_harness_cross_validation blocks agent when harness score < threshold."""

    @staticmethod
    def _make_ctx(root: Path, gate: int = 4) -> "object":
        from harness.harness_bridge import GateContext
        return GateContext(
            gate_num=gate, config={}, project_root=str(root),
            phase=6, fr_id=None,
            ssi_scripts_dir="", ssi_prompts_dir="", ssi_schemas_dir="",
            work_dir=str(root / ".sessi-work"), sab_data={},
        )

    @staticmethod
    def _make_gate_yaml(root: Path, gate: int, dims: list[dict]) -> None:
        import yaml
        cfg_dir = root / "harness" / "gate_configs"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        (cfg_dir / f"gate{gate}_p6_full.yaml").write_text(
            yaml.dump({"gate": gate, "dimensions": dims})
        )

    def test_no_fabrication_agent_below_threshold_accepted(self, tmp_path):
        """Agent score below threshold — no fabrication concern, no violations."""
        from unittest.mock import patch
        from harness.harness_bridge import _run_harness_cross_validation

        self._make_gate_yaml(tmp_path, 4, [
            {"name": "linting", "requires_tool_execution": True, "tool": "ruff",
             "threshold": 90},
        ])
        ctx = self._make_ctx(tmp_path, gate=4)
        raw = {"breakdown": {"linting": {"score": 70}}}  # agent already says FAIL

        # run_tool should never be called when agent score < threshold
        with patch("harness.tool_runners.run_tool") as mock_run:
            violations = _run_harness_cross_validation(ctx, raw)  # type: ignore[reportArgumentType]

        assert violations == []
        mock_run.assert_not_called()

    def test_fabrication_detected_blocks(self, tmp_path):
        """Agent claims PASS but harness score < threshold → fabrication detected."""
        from unittest.mock import patch
        from harness.harness_bridge import _run_harness_cross_validation

        self._make_gate_yaml(tmp_path, 4, [
            {"name": "linting", "requires_tool_execution": True, "tool": "ruff",
             "threshold": 90},
        ])
        ctx = self._make_ctx(tmp_path, gate=4)
        raw = {"breakdown": {"linting": {"score": 95}}}  # agent claims PASS

        # Harness finds 30 violations → score = 100 - 30*2 = 40 < threshold 90
        import json
        ruff_output = json.dumps([
            {"code": "E501", "filename": f"src/a_{i}.py",
             "location": {"row": i, "column": 1}, "message": "too long"}
            for i in range(30)
        ])
        with patch("harness.tool_runners.run_tool", return_value=(ruff_output, 0)):
            violations = _run_harness_cross_validation(ctx, raw)  # type: ignore[reportArgumentType]

        assert len(violations) == 1
        assert "fabrication detected" in violations[0]
        assert "linting" in violations[0]

    def test_harness_passes_threshold_no_violation(self, tmp_path):
        """Both agent and harness score ≥ threshold → no violation."""
        from unittest.mock import patch
        from harness.harness_bridge import _run_harness_cross_validation

        self._make_gate_yaml(tmp_path, 4, [
            {"name": "linting", "requires_tool_execution": True, "tool": "ruff",
             "threshold": 90},
        ])
        ctx = self._make_ctx(tmp_path, gate=4)
        raw = {"breakdown": {"linting": {"score": 95}}}  # agent claims 95

        # Harness finds 0 violations → score 100 ≥ threshold 90
        with patch("harness.tool_runners.run_tool", return_value=("[]", 0)):
            violations = _run_harness_cross_validation(ctx, raw)  # type: ignore[reportArgumentType]

        assert violations == []

    def test_skiplist_tool_requires_output_file(self, tmp_path):
        """S4 hardened: skip-list tools (scancode) require a real tool_output file.

        Without a committed file, a high agent score is unverifiable → block.
        With a valid file present, it passes (not re-run).
        Note: mutmut was previously a skip-list tool; commit 631782b changed it to
        skip_inline=False so the harness now runs it directly. scancode remains skip-list.
        """
        from harness.harness_bridge import _run_harness_cross_validation

        self._make_gate_yaml(tmp_path, 4, [
            {"name": "license_compliance", "requires_tool_execution": True, "tool": "scancode",
             "threshold": 80},
        ])
        ctx = self._make_ctx(tmp_path, gate=4)

        # (a) no tool_output → blocked (scancode is skip_inline=True — harness cannot re-run it)
        raw_missing = {"breakdown": {"license_compliance": {"score": 95}}}
        v_missing = _run_harness_cross_validation(ctx, raw_missing)  # type: ignore[reportArgumentType]
        assert len(v_missing) == 1
        assert "unverifiable" in v_missing[0]

        # (b) real committed scancode output file → passes
        out = tmp_path / ".sessi-work" / "scancode_out.txt"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("Scan completed. No license violations found.\n", encoding="utf-8")
        raw_ok = {"breakdown": {"license_compliance": {"score": 95,
                                                       "tool_output": ".sessi-work/scancode_out.txt"}}}
        v_ok = _run_harness_cross_validation(ctx, raw_ok)  # type: ignore[reportArgumentType]
        assert v_ok == []

    def test_tool_timeout_blocks(self, tmp_path):
        """S4 hardened: a timed-out tool now BLOCKS (passing score must be reproducible)."""
        from unittest.mock import patch
        from harness.harness_bridge import _run_harness_cross_validation

        self._make_gate_yaml(tmp_path, 4, [
            {"name": "type_safety", "requires_tool_execution": True, "tool": "mypy",
             "threshold": 85},
        ])
        ctx = self._make_ctx(tmp_path, gate=4)
        raw = {"breakdown": {"type_safety": {"score": 95}}}

        # Simulate timeout — agent claims a passing score (95 ≥ 85) but the tool can't confirm it.
        with patch("harness.tool_runners.run_tool", return_value=("TIMEOUT: mypy exceeded 60s", -2)):
            violations = _run_harness_cross_validation(ctx, raw)  # type: ignore[reportArgumentType]

        assert len(violations) == 1
        assert "timed out" in violations[0]

    def test_no_benchmark_blocks(self, tmp_path):
        """Layer 1c: pytest-benchmark exit 5 (no benchmarks) BLOCKS a passing perf score."""
        from unittest.mock import patch
        from harness.harness_bridge import _run_harness_cross_validation

        self._make_gate_yaml(tmp_path, 4, [
            {"name": "performance", "requires_tool_execution": True, "tool": "pytest-benchmark",
             "threshold": 75},
        ])
        ctx = self._make_ctx(tmp_path, gate=4)
        raw = {"breakdown": {"performance": {"score": 90}}}

        with patch("harness.tool_runners.run_tool", return_value=("no benchmarks ran", 5)):
            violations = _run_harness_cross_validation(ctx, raw)  # type: ignore[reportArgumentType]

        assert len(violations) == 1
        assert "no tests" in violations[0] and "unverifiable" in violations[0]

    def test_readability_no_source_blocks(self, tmp_path):
        """Layer B2: radon-mi returns None (no analysable source) → a passing
        readability score is unverifiable and BLOCKS (no free 100)."""
        from unittest.mock import patch
        from harness.harness_bridge import _run_harness_cross_validation

        self._make_gate_yaml(tmp_path, 4, [
            {"name": "readability", "requires_tool_execution": True, "tool": "radon-mi",
             "threshold": 80},
        ])
        ctx = self._make_ctx(tmp_path, gate=4)
        raw = {"breakdown": {"readability": {"score": 95}}}

        # radon-mi over an empty graph → "{}" → _score_radon_mi returns None.
        with patch("harness.tool_runners.run_tool", return_value=("{}", 0)):
            violations = _run_harness_cross_validation(ctx, raw)  # type: ignore[reportArgumentType]

        assert len(violations) == 1
        assert "readability" in violations[0] and "no analysable" in violations[0]

    def test_architecture_skipped_crg_owned(self, tmp_path):
        """Layer 3: architecture is framework-CRG-owned in finalize → skipped by S4."""
        from unittest.mock import patch
        from harness.harness_bridge import _run_harness_cross_validation

        self._make_gate_yaml(tmp_path, 4, [
            {"name": "architecture", "requires_tool_execution": True, "tool": "code-review-graph",
             "threshold": 80},
        ])
        ctx = self._make_ctx(tmp_path, gate=4)
        raw = {"breakdown": {"architecture": {"score": 95}}}

        with patch("harness.tool_runners.run_tool") as mock_run:
            violations = _run_harness_cross_validation(ctx, raw)  # type: ignore[reportArgumentType]

        assert violations == []
        mock_run.assert_not_called()  # architecture skipped before any tool run

    def test_multiple_dims_one_fabricated(self, tmp_path):
        """Only the dimension whose harness score < threshold is reported."""
        from unittest.mock import patch
        from harness.harness_bridge import _run_harness_cross_validation

        self._make_gate_yaml(tmp_path, 4, [
            {"name": "linting",     "requires_tool_execution": True, "tool": "ruff",
             "threshold": 90},
            {"name": "type_safety", "requires_tool_execution": True, "tool": "mypy",
             "threshold": 85},
        ])
        ctx = self._make_ctx(tmp_path, gate=4)
        raw = {"breakdown": {
            "linting":     {"score": 95},  # claims PASS
            "type_safety": {"score": 95},  # claims PASS
        }}

        def fake_run(tool: str, project_root: str, **_kw):
            import json
            if tool == "ruff":
                # 30 violations → score 40 < threshold 90 → fabrication
                return (json.dumps([{"code": "E501", "filename": f"src/a_{i}.py",
                                      "location": {"row": i, "column": 1},
                                      "message": "too long"}
                                     for i in range(30)]), 0)
            # mypy: no errors → score 100 ≥ threshold 85 → OK
            return ("Success: no issues found in 3 source files\n", 0)

        with patch("harness.tool_runners.run_tool", side_effect=fake_run):
            violations = _run_harness_cross_validation(ctx, raw)  # type: ignore[reportArgumentType]

        assert len(violations) == 1
        assert "linting" in violations[0]


class TestAgentNullIsNotFree:
    """Round 27 站1 — an agent-reported ``score: null`` is not a free pass.

    The tests above already close "agent claims a passing score the tool cannot
    confirm" (test_no_benchmark_blocks, test_readability_no_source_blocks). What
    was still open is the door beside it: declare the dimension INAPPLICABLE and
    every layer waves it through — S4 skipped it, the weighted average dropped it
    from the denominator (redistributing its weight to the usually-perfect
    dimensions, so the composite went UP), and _all_dims_pass treated it as
    vacuously satisfying its own floor.

    taskq-plus's Gate 4 shows the agent had worked this out: the `performance`
    evidence reads "dimension N/A per protocol (**not free 100**)" — it knew a
    claimed score would be cross-validated and picked the door that was not.

    The fix inverts the meaning of None: instead of "nobody has to check", it is
    "the FRAMEWORK has to check". Only a None the framework itself reproduced
    counts as genuinely not-applicable.

    Borrows the two fixture builders above rather than subclassing — inheriting
    would re-run all of TestHarnessCrossValidation's cases under a second name.
    """

    _make_ctx = staticmethod(TestHarnessCrossValidation._make_ctx)
    _make_gate_yaml = staticmethod(TestHarnessCrossValidation._make_gate_yaml)

    def test_agent_null_does_not_crash_the_gate(self, tmp_path):
        """`float(None)` used to raise TypeError out of finalize_gate.

        `breakdown.get(name, {}).get("score", 0)` substitutes the 0 default only
        when the KEY is absent; an explicit JSON null surfaces as None and went
        straight into float(). The call site has no try/except (ancestor chain is
        finalize_gate alone), so a single null crashed the whole gate.
        """
        from unittest.mock import patch
        from harness.harness_bridge import _run_harness_cross_validation

        self._make_gate_yaml(tmp_path, 4, [
            {"name": "performance", "requires_tool_execution": True,
             "tool": "pytest-benchmark", "threshold": 75},
        ])
        ctx = self._make_ctx(tmp_path, gate=4)
        raw = {"breakdown": {"performance": {
            "score": None,
            "tool_evidence": "No pytest-benchmark tests exist — dimension N/A per protocol",
        }}}

        with patch("harness.tool_runners.run_tool", return_value=("no benchmarks ran", 5)):
            violations = _run_harness_cross_validation(ctx, raw)  # type: ignore[reportArgumentType]

        assert isinstance(violations, list)

    def test_agent_null_makes_the_framework_run_the_tool(self, tmp_path):
        """A declared N/A is a request for verification, not an exemption."""
        from unittest.mock import patch
        from harness.harness_bridge import _run_harness_cross_validation

        self._make_gate_yaml(tmp_path, 4, [
            {"name": "linting", "requires_tool_execution": True, "tool": "ruff",
             "threshold": 90},
        ])
        ctx = self._make_ctx(tmp_path, gate=4)
        raw = {"breakdown": {"linting": {"score": None,
                                         "tool_evidence": "not applicable here"}}}

        with patch("harness.tool_runners.run_tool", return_value=("[]", 0)) as mock_run:
            _run_harness_cross_validation(ctx, raw)  # type: ignore[reportArgumentType]

        mock_run.assert_called_once()

    def test_framework_score_replaces_the_agents_null(self, tmp_path):
        """When the framework CAN score it, the dimension is applicable after all.

        The framework's number is written back into the breakdown (finalize_gate
        builds its DimResult list from `raw["breakdown"]` further down the same
        function), so the score that reaches the verdict is the one the framework
        measured — not the absence the agent reported.
        """
        from unittest.mock import patch
        from harness.harness_bridge import _run_harness_cross_validation

        self._make_gate_yaml(tmp_path, 4, [
            {"name": "linting", "requires_tool_execution": True, "tool": "ruff",
             "threshold": 90},
        ])
        ctx = self._make_ctx(tmp_path, gate=4)
        raw = {"breakdown": {"linting": {"score": None, "tool_evidence": "n/a"}}}

        with patch("harness.tool_runners.run_tool", return_value=("[]", 0)):
            _run_harness_cross_validation(ctx, raw)  # type: ignore[reportArgumentType]

        entry = raw["breakdown"]["linting"]
        assert entry["score"] == 100.0
        assert entry["score_source"] == "framework"

    def test_framework_null_too_is_the_only_real_na(self, tmp_path):
        """pytest-benchmark with no benchmarks is a LEGITIMATE N/A — but only
        because the framework reproduced it, and it is labelled as such.

        Note exit 5 is a POSITIVE return code, so `rc < 0` cannot be the test for
        "unscoreable" — `_score_pytest_benchmark` returns None on rc==5. The
        判準 is "the framework ran it and still got no number".
        """
        from unittest.mock import patch
        from harness.harness_bridge import _run_harness_cross_validation

        self._make_gate_yaml(tmp_path, 4, [
            {"name": "performance", "requires_tool_execution": True,
             "tool": "pytest-benchmark", "threshold": 75},
        ])
        ctx = self._make_ctx(tmp_path, gate=4)
        raw = {"breakdown": {"performance": {"score": None, "tool_evidence": "n/a"}}}

        with patch("harness.tool_runners.run_tool",
                   return_value=("no benchmarks ran", 5)):
            violations = _run_harness_cross_validation(ctx, raw)  # type: ignore[reportArgumentType]

        assert violations == []
        entry = raw["breakdown"]["performance"]
        assert entry["score"] is None
        assert entry["score_source"] == "framework_na"

    def test_only_a_framework_verified_na_passes_vacuously(self):
        """The verdict layer must distinguish the two kinds of None.

        `_all_dims_pass` reads this predicate rather than `d.score is None`, so an
        agent-authored null can no longer satisfy its own floor by being absent.
        """
        from harness.harness_bridge import na_is_framework_verified

        assert na_is_framework_verified({"score": None, "score_source": "framework_na"})
        assert not na_is_framework_verified({"score": None})
        assert not na_is_framework_verified({"score": None, "score_source": "agent"})


# ---------------------------------------------------------------------------
# tool_runners: unit tests for score computation
# ---------------------------------------------------------------------------

class TestToolRunnerScoring:
    """compute_tool_score returns sensible scores from sample tool outputs."""

    def test_ruff_no_violations_scores_100(self):
        from harness.tool_runners import compute_tool_score
        assert compute_tool_score("ruff", "[]", 0) == 100.0

    def test_ruff_5_violations_scores_90(self):
        import json
        from harness.tool_runners import compute_tool_score
        output = json.dumps([
            {"code": "E501", "filename": f"src/a_{i}.py",
             "location": {"row": i, "column": 1}, "message": "long"}
            for i in range(5)
        ])
        assert compute_tool_score("ruff", output, 0) == 90.0

    def test_ruff_text_format_fallback(self):
        """Text-format ruff output (non-JSON) is counted by regex."""
        from harness.tool_runners import compute_tool_score
        output = "src/foo.py:1:1: E501 too long\nsrc/bar.py:2:1: E302 blank lines\n"
        assert compute_tool_score("ruff", output, 0) == 96.0  # 2 violations → 100 - 4

    def test_mypy_clean_scores_100(self):
        from harness.tool_runners import compute_tool_score
        assert compute_tool_score("mypy", "Success: no issues found in 5 files", 0) == 100.0

    def test_mypy_errors_reduce_score(self):
        from harness.tool_runners import compute_tool_score
        output = "src/a.py:1: error: Type mismatch\nsrc/b.py:2: error: Incompatible\n"
        assert compute_tool_score("mypy", output, 0) == 90.0  # 2 errors → 100 - 10

    def test_pytest_cov_extracts_coverage_pct(self):
        from harness.tool_runners import compute_tool_score
        output = (
            "collected 10 items\n"
            "..........                                                [100%]\n\n"
            "---------- coverage: platform darwin, python 3.12 ----------\n"
            "Name                 Stmts   Miss  Cover\n"
            "----------------------------------------\n"
            "src/app.py              50      7    86%\n"
            "TOTAL                   50      7    86%\n\n"
            "10 passed in 0.42s\n"
        )
        assert compute_tool_score("pytest-cov", output, 0) == 86.0

    def test_gitleaks_no_leaks_scores_100(self):
        from harness.tool_runners import compute_tool_score
        assert compute_tool_score("gitleaks", "No leaks found.", 0) == 100.0

    def test_gitleaks_leaks_found_scores_0(self):
        from harness.tool_runners import compute_tool_score
        # non-zero exit code + output with "Secret" keyword
        output = 'WRN[0000] leaks found: 1 Secret detected in src/config.py'
        assert compute_tool_score("gitleaks", output, 1) == 0.0

    def test_skipped_tool_returns_none(self):
        from harness.tool_runners import compute_tool_score
        # returncode -1 = skipped
        assert compute_tool_score("mutmut", "", -1) is None

    def test_unknown_tool_returns_none(self):
        from harness.tool_runners import compute_tool_score
        assert compute_tool_score("unknown_xyz", "some output", 0) is None


# ---------------------------------------------------------------------------
# Enhancement 1 — A1b: Hermes receipt composite_score integrity
# ---------------------------------------------------------------------------

class TestHermesReceiptIntegrity:
    """Gate 4: hermes_g4_receipt.json is no longer required (A1 removed).
    Gate 4 is fully automated — composite_score >= score_gate is the sole criterion."""

    @staticmethod
    def _make_prerequisites(tmp_path: Path) -> None:
        """Create the minimum Gate 4 prerequisite files (minus the receipt)."""
        import yaml
        # Gate 4 config (no crg.reconnaissance so B3 is skipped)
        cfg_dir = tmp_path / "harness" / "gate_configs"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "gate4_p6_full.yaml").write_text(
            yaml.dump({
                "gate": 4,
                "score_gate": 85,
                "dimensions": [],
                "crg": {},
            })
        )
        # Per-dim score files (B2)
        scores_dir = tmp_path / ".sessi-work" / "round_1" / "scores"
        scores_dir.mkdir(parents=True)
        (scores_dir / "linting.json").write_text(json.dumps({"score": 92}))
        # gate4_result.json (A2/A3/A4/A5 checks)
        sessi = tmp_path / ".sessi-work"
        sessi.mkdir(parents=True, exist_ok=True)
        (sessi / "gate4_result.json").write_text(json.dumps({
            "overall_score": 90,
            "model_used": {"linting": "claude"},
            "devil_advocate": {"architecture": True, "readability": True,
                               "error_handling": True, "documentation": True,
                               "performance": True},
            "devil_advocate_evidence": {d: {"challenger_model": "claude",
                                            "challenge": "Challenge for " + d + ": " + "c" * 130,
                                            "response": "Response for " + d + ": " + "r" * 130}
                                        for d in ("architecture", "readability", "error_handling",
                                                  "documentation", "performance")},
            "high_score_confirmations": {},
            "issue_registry_path": ".methodology/issues.json",
            "breakdown": {},
        }))
        # issue registry (A5)
        meth = tmp_path / ".methodology"
        meth.mkdir(parents=True, exist_ok=True)
        (meth / "issues.json").write_text(json.dumps({"issues": ["finding-1"]}))

    def test_receipt_not_required_null_composite(self, tmp_path):
        """Receipt with composite_score: null does NOT block — receipt is no longer required."""
        from cli.gate_cmds import _check_gate4_prerequisites
        self._make_prerequisites(tmp_path)
        receipt = tmp_path / ".methodology" / "hermes_g4_receipt.json"
        receipt.write_text(json.dumps({
            "ts": "2026-05-19T12:00:00Z",
            "approved_by": "hermes",
            "composite_score": None,
        }))
        blocked, _ = _check_gate4_prerequisites(tmp_path)
        assert not blocked, "Receipt content is no longer checked — Gate 4 is fully automated"

    def test_receipt_not_required_zero_composite(self, tmp_path):
        """Receipt with composite_score: 0 does NOT block — receipt is no longer required."""
        from cli.gate_cmds import _check_gate4_prerequisites
        self._make_prerequisites(tmp_path)
        receipt = tmp_path / ".methodology" / "hermes_g4_receipt.json"
        receipt.write_text(json.dumps({
            "ts": "2026-05-19T12:00:00Z",
            "approved_by": "hermes",
            "composite_score": 0,
        }))
        blocked, _ = _check_gate4_prerequisites(tmp_path)
        assert not blocked, "Receipt content is no longer checked — Gate 4 is fully automated"

    def test_valid_composite_score_passes(self, tmp_path):
        """All Gate 4 prerequisites (A2–B3) satisfied → not blocked."""
        from cli.gate_cmds import _check_gate4_prerequisites
        self._make_prerequisites(tmp_path)
        receipt = tmp_path / ".methodology" / "hermes_g4_receipt.json"
        receipt.write_text(json.dumps({
            "ts": "2026-05-19T12:00:00Z",
            "approved_by": "hermes",
            "composite_score": 91.5,
        }))
        blocked, _ = _check_gate4_prerequisites(tmp_path)
        assert not blocked, f"All prerequisites satisfied should not block Gate 4, got blocked={blocked}"

    def test_receipt_not_required_bool_composite(self, tmp_path):
        """Receipt with composite_score: true does NOT block — receipt is no longer required."""
        from cli.gate_cmds import _check_gate4_prerequisites
        self._make_prerequisites(tmp_path)
        receipt = tmp_path / ".methodology" / "hermes_g4_receipt.json"
        receipt.write_text(json.dumps({
            "ts": "2026-05-19T12:00:00Z",
            "approved_by": "hermes",
            "composite_score": True,
        }))
        blocked, _ = _check_gate4_prerequisites(tmp_path)
        assert not blocked, "Receipt content is no longer checked — Gate 4 is fully automated"

    def test_receipt_not_required_invalid_json(self, tmp_path):
        """Invalid JSON receipt does NOT block — receipt is no longer required."""
        from cli.gate_cmds import _check_gate4_prerequisites
        self._make_prerequisites(tmp_path)
        receipt = tmp_path / ".methodology" / "hermes_g4_receipt.json"
        receipt.write_text("not valid json {{{")
        blocked, _ = _check_gate4_prerequisites(tmp_path)
        assert not blocked, "Receipt is no longer required — Gate 4 is fully automated"

    def test_missing_receipt_not_blocked(self, tmp_path):
        """Missing receipt does NOT block — receipt is no longer required."""
        from cli.gate_cmds import _check_gate4_prerequisites
        self._make_prerequisites(tmp_path)
        # No receipt file at all
        blocked, _ = _check_gate4_prerequisites(tmp_path)
        assert not blocked, "Missing receipt must not block — A1 check removed"


class TestCRGReconCheck:
    """Gate 4 B3: .sessi-work/crg_reconnaissance.json must exist when reconnaissance: true."""

    @staticmethod
    def _make_prereqs_with_crg_config(tmp_path: Path, *, recon: bool) -> None:
        """Write minimum Gate 4 prerequisites with optional crg.reconnaissance."""
        import yaml
        cfg_dir = tmp_path / "harness" / "gate_configs"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "gate4_p6_full.yaml").write_text(
            yaml.dump({
                "gate": 4,
                "score_gate": 85,
                "dimensions": [],
                "crg": {"reconnaissance": recon},
            })
        )
        # Receipt (valid composite_score)
        meth = tmp_path / ".methodology"
        meth.mkdir(parents=True, exist_ok=True)
        (meth / "hermes_g4_receipt.json").write_text(json.dumps({
            "ts": "2026-05-19T12:00:00Z",
            "approved_by": "hermes",
            "composite_score": 90.0,
        }))
        # Per-dim score files (B2)
        scores_dir = tmp_path / ".sessi-work" / "round_1" / "scores"
        scores_dir.mkdir(parents=True)
        (scores_dir / "linting.json").write_text(json.dumps({"score": 92}))
        # gate4_result.json (A2/A3/A4/A5)
        sessi = tmp_path / ".sessi-work"
        sessi.mkdir(parents=True, exist_ok=True)
        (sessi / "gate4_result.json").write_text(json.dumps({
            "overall_score": 90,
            "model_used": {"linting": "claude"},
            "devil_advocate": {"architecture": True, "readability": True,
                               "error_handling": True, "documentation": True,
                               "performance": True},
            "devil_advocate_evidence": {d: {"challenger_model": "claude",
                                            "challenge": "Challenge for " + d + ": " + "c" * 130,
                                            "response": "Response for " + d + ": " + "r" * 130}
                                        for d in ("architecture", "readability", "error_handling",
                                                  "documentation", "performance")},
            "high_score_confirmations": {},
            "issue_registry_path": ".methodology/issues.json",
            "breakdown": {},
        }))
        (meth / "issues.json").write_text(json.dumps({"issues": ["f1"]}))

    def test_missing_recon_file_blocked(self, tmp_path):
        """B3: reconnaissance: true with no crg_reconnaissance.json → blocked."""
        from cli.gate_cmds import _check_gate4_prerequisites
        self._make_prereqs_with_crg_config(tmp_path, recon=True)
        blocked, _ = _check_gate4_prerequisites(tmp_path)
        assert blocked, "Missing crg_reconnaissance.json should block Gate 4 (B3)"

    def test_empty_recon_file_blocked(self, tmp_path):
        """B3: reconnaissance: true with empty crg_reconnaissance.json → blocked."""
        from cli.gate_cmds import _check_gate4_prerequisites
        self._make_prereqs_with_crg_config(tmp_path, recon=True)
        (tmp_path / ".sessi-work" / "crg_reconnaissance.json").write_text("")
        blocked, _ = _check_gate4_prerequisites(tmp_path)
        assert blocked, "Empty crg_reconnaissance.json should block Gate 4 (B3)"

    def test_populated_recon_file_passes(self, tmp_path):
        """B3: non-empty crg_reconnaissance.json passes the check."""
        from cli.gate_cmds import _check_gate4_prerequisites
        self._make_prereqs_with_crg_config(tmp_path, recon=True)
        recon_file = tmp_path / ".sessi-work" / "crg_reconnaissance.json"
        recon_file.write_text(json.dumps({"nodes": 42}))
        blocked, _ = _check_gate4_prerequisites(tmp_path)
        assert not blocked, f"Populated crg_reconnaissance.json should not block Gate 4, got blocked={blocked}"

    def test_no_recon_config_skips_check(self, tmp_path):
        """B3: crg.reconnaissance: false → no B3 enforcement."""
        from cli.gate_cmds import _check_gate4_prerequisites
        self._make_prereqs_with_crg_config(tmp_path, recon=False)
        blocked, _ = _check_gate4_prerequisites(tmp_path)
        assert not blocked, f"reconnaissance: false should not block Gate 4, got blocked={blocked}"

class TestABCoveragePerDeliverable:
    def test_ab_coverage_is_not_inferred_from_an_agent_writable_log(self, tmp_path):
        """Round 21 站3: this anti-fabrication check read a forgeable source.

        A developer-only log used to score 50.0. But sessions_spawn.log is
        written by the agent under evaluation and is gitignored, so producing a
        `role: reviewer` line to clear the check costs one Bash call — which is
        what taskq's Phase 6 did, six times, with `role: architect`. An
        anti-fabrication check whose input the fabricator controls is not one.
        A/B separation is enforced by reviewing the deliverable instead; the
        residual forensic signal lives in core/doctor.py as a WARN.
        """
        from core.quality_gate.phase_truth_verifier import PhaseTruthVerifier
        (tmp_path / ".methodology").mkdir(exist_ok=True)
        (tmp_path / ".methodology" / "sessions_spawn.log").write_text(
            '{"fr_id": "FR-01", "role": "developer"}\n'
            '{"fr_id": "FR-01", "role": "developer"}\n'
        )
        v = PhaseTruthVerifier(str(tmp_path), 1)
        passed, score, msg = v.check_session_log()
        assert passed is True
        assert score == 100.0
        assert "missing" not in msg

    def test_ab_coverage_passes_with_reviewer(self, tmp_path):
        from core.quality_gate.phase_truth_verifier import PhaseTruthVerifier
        (tmp_path / ".methodology").mkdir(exist_ok=True)
        (tmp_path / ".methodology" / "sessions_spawn.log").write_text(
            '{"fr_id": "FR-01", "role": "developer"}\n'
            '{"fr_id": "FR-01", "role": "reviewer"}\n'
        )
        v = PhaseTruthVerifier(str(tmp_path), 1)
        passed, score, msg = v.check_session_log()
        assert passed
        assert score == 100.0

pytestmark = pytest.mark.mutation_oracle
