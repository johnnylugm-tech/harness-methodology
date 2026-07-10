"""Tests for cli/_shared.py helpers — sentinel paths / STAGE_PASS generation / post-push self-check (split from tests/test_harness_cli.py, C1f)."""

from __future__ import annotations

import subprocess

import json
from unittest import mock

import harness_cli as _hc_entry  # noqa: F401  entry-first before cli imports
from cli._shared import _post_push_self_check  # noqa: E402

def test_l1_finalize_sentinel_path_legacy_fallback(tmp_path):
    """Test L1: Legacy sentinel fallback in _finalize_sentinel_path."""
    from cli._shared import _finalize_sentinel_path
    
    fr_id = "FR-99"
    key = fr_id.replace("-", "").lower()
    gate = 1
    d = tmp_path / ".sessi-work" / "sentinels"
    d.mkdir(parents=True, exist_ok=True)
    
    std_path = d / f"g{gate}_{key}.finalized"
    legacy_path = d / f"g{gate}_{fr_id}.flag"
    
    # Neither exists -> returns std_path
    assert _finalize_sentinel_path(tmp_path, gate, fr_id) == std_path
    
    # Only legacy exists -> returns legacy_path
    legacy_path.touch()
    assert _finalize_sentinel_path(tmp_path, gate, fr_id) == legacy_path
    
    # Both exist -> returns std_path
    std_path.touch()
    assert _finalize_sentinel_path(tmp_path, gate, fr_id) == std_path


# =============================================================================
# _generate_stage_pass gate_data-empty bug (Phase 1-2 case)
# =============================================================================

class TestGenerateStagePassEmptyGateData:
    """Bug: For Phase 1-2, Gate 1 has not fired yet (Gate 1 is per-FR, fires in
    Phase 3+). quality_manifest.json gate_results.gate1 = {} (empty dict).

    _generate_stage_pass() reads this empty dict, computes quality_complete=False
    (default), and writes STAGE_PASS.md saying "Phase 1 exit gate FAIL" — even
    though the phase actually succeeded (Constitution PASS, all 4 deliverables
    APPROVED, advance-phase recorded phase_truth_passed:true in state.json).

    Fix: when gate_data is empty AND the phase is one where the gate has not
    fired yet (Phase 1-2 → Gate 1; Phase 5/7/8 → Gate 1 GATE1-DELTA logic
    applies, but quality_manifest.gate1 may still be {} before DELTA — fallback
    to state.json.phase_truth_passed to derive the verdict.
    """

    def _setup(self, tmp_path, phase_truth_passed):
        """Create tmp project: state.json + empty quality_manifest."""
        import json

        methodology = tmp_path / ".methodology"
        methodology.mkdir(parents=True)
        state = {
            "state": "RUNNING",
            "current_phase": 2,
            "phase_truth_passed": phase_truth_passed,
            "last_update": "2026-07-04T10:52:47Z",
        }
        (methodology / "state.json").write_text(json.dumps(state), encoding="utf-8")

        # Empty quality_manifest: gate1 = {} (Gate 1 not fired for Phase 1-2)
        manifest = {
            "schema_version": "1.0",
            "generated_at_phase": 1,
            "gate_results": {
                "gate1": {},
                "gate2": None,
                "gate3": None,
                "gate4": None,
            },
        }
        (methodology / "quality_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        return methodology

    def test_phase1_pass_when_phase_truth_passed_true(self, tmp_path):
        """Phase 1 with phase_truth_passed=True → STAGE_PASS.md must say PASS."""
        from cli._shared import _generate_stage_pass

        self._setup(tmp_path, phase_truth_passed=True)

        _generate_stage_pass(tmp_path, gate_num=1, phase_num=1)

        out = tmp_path / "00-summary" / "Phase1_STAGE_PASS.md"
        assert out.exists(), "STAGE_PASS.md not generated"
        content = out.read_text(encoding="utf-8")
        assert "PASS" in content, f"Expected PASS verdict for Phase 1 (phase_truth_passed=True); got:\n{content}"
        assert "FAIL" not in content.split("## Summary")[1], (
            f"Summary section must say PASS, not FAIL; got:\n{content}"
        )

    def test_phase1_fail_when_phase_truth_passed_false(self, tmp_path):
        """Phase 1 with phase_truth_passed=False → STAGE_PASS.md must say FAIL."""
        from cli._shared import _generate_stage_pass

        self._setup(tmp_path, phase_truth_passed=False)

        _generate_stage_pass(tmp_path, gate_num=1, phase_num=1)

        out = tmp_path / "00-summary" / "Phase1_STAGE_PASS.md"
        assert out.exists()
        content = out.read_text(encoding="utf-8")
        assert "FAIL" in content.split("## Summary")[1], (
            f"Summary section must say FAIL when phase_truth_passed=False; got:\n{content}"
        )

    def test_phase1_passes_when_state_json_missing(self, tmp_path):
        """No state.json + empty quality_manifest → fall back to FAIL (safe default)."""
        from cli._shared import _generate_stage_pass

        # No state.json — function should not crash; default to FAIL.
        methodology = tmp_path / ".methodology"
        methodology.mkdir(parents=True)
        manifest = {
            "schema_version": "1.0",
            "gate_results": {"gate1": {}, "gate2": None, "gate3": None, "gate4": None},
        }
        (methodology / "quality_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

        _generate_stage_pass(tmp_path, gate_num=1, phase_num=1)
        out = tmp_path / "00-summary" / "Phase1_STAGE_PASS.md"
        assert out.exists()
        content = out.read_text(encoding="utf-8")
        assert "FAIL" in content.split("## Summary")[1]

    def test_phase3_gate1_per_fr_quality_complete_false_overrides_truth(self, tmp_path):
        """Phase 3 Gate 1 per-FR with any FR quality_complete=False → FAIL,
        even if phase_truth_passed=True. Gate data takes precedence.
        """
        from cli._shared import _generate_stage_pass

        methodology = tmp_path / ".methodology"
        methodology.mkdir(parents=True)
        state = {"current_phase": 4, "phase_truth_passed": True}
        (methodology / "state.json").write_text(json.dumps(state), encoding="utf-8")

        # Gate 1 per-FR: FR-01 failed (real failure)
        manifest = {
            "schema_version": "1.0",
            "gate_results": {
                "gate1": {
                    "FR-01": {"score": 65.0, "quality_complete": False},
                    "FR-02": {"score": 92.0, "quality_complete": True},
                },
                "gate2": None,
                "gate3": None,
                "gate4": None,
            },
        }
        (methodology / "quality_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

        _generate_stage_pass(tmp_path, gate_num=1, phase_num=3)

        out = tmp_path / "00-summary" / "Phase3_STAGE_PASS.md"
        assert out.exists()
        content = out.read_text(encoding="utf-8")
        assert "FAIL" in content.split("## Summary")[1], (
            f"Phase 3 Gate 1 with FR-01 quality_complete=False must say FAIL; got:\n{content}"
        )

    def test_phase3_gate1_per_fr_all_pass(self, tmp_path):
        """Phase 3 Gate 1 per-FR all quality_complete=True → PASS."""
        from cli._shared import _generate_stage_pass

        methodology = tmp_path / ".methodology"
        methodology.mkdir(parents=True)
        state = {"current_phase": 4, "phase_truth_passed": True}
        (methodology / "state.json").write_text(json.dumps(state), encoding="utf-8")

        # Gate 1 per-FR: all pass
        manifest = {
            "schema_version": "1.0",
            "gate_results": {
                "gate1": {
                    "FR-01": {"score": 92.0, "quality_complete": True},
                    "FR-02": {"score": 88.0, "quality_complete": True},
                },
                "gate2": None,
                "gate3": None,
                "gate4": None,
            },
        }
        (methodology / "quality_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

        _generate_stage_pass(tmp_path, gate_num=1, phase_num=3)

        out = tmp_path / "00-summary" / "Phase3_STAGE_PASS.md"
        assert out.exists()
        content = out.read_text(encoding="utf-8")
        assert "PASS" in content.split("## Summary")[1], (
            f"Phase 3 Gate 1 all FRs pass → must say PASS; got:\n{content}"
        )

    def test_phase3_gate2_flat_structure_unchanged(self, tmp_path):
        """Phase 3 Gate 2 (flat) with quality_complete=True → PASS."""
        from cli._shared import _generate_stage_pass

        methodology = tmp_path / ".methodology"
        methodology.mkdir(parents=True)
        state = {"current_phase": 4, "phase_truth_passed": True}
        (methodology / "state.json").write_text(json.dumps(state), encoding="utf-8")

        manifest = {
            "schema_version": "1.0",
            "gate_results": {
                "gate1": {},
                "gate2": {"score": 95.0, "quality_complete": True},
                "gate3": None,
                "gate4": None,
            },
        }
        (methodology / "quality_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

        _generate_stage_pass(tmp_path, gate_num=2, phase_num=3)

        out = tmp_path / "00-summary" / "Phase3_STAGE_PASS.md"
        assert out.exists()
        content = out.read_text(encoding="utf-8")
        assert "PASS" in content.split("## Summary")[1], (
            f"Phase 3 Gate 2 quality_complete=True → must say PASS; got:\n{content}"
        )
        assert "95.0" in content, "Gate 2 score must be displayed"


# =============================================================================
# _post_push_self_check + 3-site dirty-warn integration
# =============================================================================
# See plan: ~/.claude/plans/abundant-stargazing-hejlsberg.md
#
# Bug class: post-push working-tree dirtiness (28864f7 family). 28864f7 fixed
# the specific state.json audit-write-after-push case at 3 sites, but did not
# add a generic post-push self-check. This module adds one (warn-only, never
# fail-fast) and wires it into the same 3 sites.


class TestPostPushSelfCheck:
    """Unit tests for the `_post_push_self_check(project)` helper."""

    def test_clean_when_status_empty(self, tmp_path, monkeypatch):
        fake_result = mock.Mock(returncode=0, stdout="")
        monkeypatch.setattr(subprocess, "run", lambda *_a, **_kw: fake_result)
        assert _post_push_self_check(tmp_path) == []

    def test_returns_modified_paths(self, tmp_path, monkeypatch):
        fake_result = mock.Mock(
            returncode=0,
            stdout=" M .methodology/state.json\n M .methodology/HANDOVER.md\n",
        )
        monkeypatch.setattr(subprocess, "run", lambda *_a, **_kw: fake_result)
        out = _post_push_self_check(tmp_path)
        assert out == [
            ".methodology/state.json",
            ".methodology/HANDOVER.md",
        ]

    def test_returns_untracked_paths(self, tmp_path, monkeypatch):
        fake_result = mock.Mock(
            returncode=0, stdout="?? new_file.py\n?? docs/scratch.md\n",
        )
        monkeypatch.setattr(subprocess, "run", lambda *_a, **_kw: fake_result)
        out = _post_push_self_check(tmp_path)
        assert out == ["new_file.py", "docs/scratch.md"]

    def test_handles_subprocess_failure(self, tmp_path, monkeypatch):

        def _raise(*_a, **_kw):
            raise OSError("git not found")
        monkeypatch.setattr(subprocess, "run", _raise)
        assert _post_push_self_check(tmp_path) == []  # best-effort

    def test_handles_nonzero_returncode(self, tmp_path, monkeypatch):
        fake_result = mock.Mock(returncode=128, stdout="fatal: not a git repo")
        monkeypatch.setattr(subprocess, "run", lambda *_a, **_kw: fake_result)
        assert _post_push_self_check(tmp_path) == []
