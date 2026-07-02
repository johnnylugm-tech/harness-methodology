"""Tests for PhaseHooks.preflight_manifest_integrity (Fix IV).

Pattern B (empty gate1 at Phase 3+) must distinguish:
  - fresh P3 entry (post-reset / right after P2 manifest generation):
    gate1 legitimately empty, NO block;
  - corruption (manifest results wiped while Gate 1 evidence exists in the
    FSM state.json or residual per-FR artifacts): BLOCK.
Regression for the pre-push false positive on the integration-test P3
baseline reset, 2026-07-02.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.phase_hooks import PhaseHooks


def _write_manifest(project: Path, *, gate1: dict) -> None:
    md = project / ".methodology"
    md.mkdir(parents=True, exist_ok=True)
    (md / "quality_manifest.json").write_text(json.dumps({
        "fr_ids": ["FR-01", "FR-02"],
        "fr_module_traceability": {"FR-01": "app.a", "FR-02": "app.b"},
        "gate_results": {"gate1": gate1},
    }), encoding="utf-8")


def _write_state(project: Path, *, last_gate=None, last_fr=None) -> None:
    md = project / ".methodology"
    md.mkdir(parents=True, exist_ok=True)
    (md / "state.json").write_text(json.dumps({
        "state": "RUNNING", "current_phase": 3,
        "last_gate": last_gate, "last_fr": last_fr,
        "language": "python",
    }), encoding="utf-8")


@pytest.fixture
def hooks_factory(tmp_path):
    def make(phase=3):
        return PhaseHooks(str(tmp_path), phase=phase, enable_kill_switch=False)
    return make


class TestManifestIntegrityPatternB:
    def test_fresh_p3_empty_gate1_passes(self, tmp_path, hooks_factory):
        """No gate finalized yet (last_gate/last_fr null, no residual gate1
        artifacts): empty gate1 is the true state, not corruption."""
        _write_manifest(tmp_path, gate1={})
        _write_state(tmp_path, last_gate=None, last_fr=None)

        result = hooks_factory().preflight_manifest_integrity()

        assert result["passed"] is True, result

    def test_empty_gate1_with_fsm_evidence_blocks(self, tmp_path, hooks_factory):
        """state.json says Gate 1 was finalized — an empty manifest gate1
        means results were wiped: block."""
        _write_manifest(tmp_path, gate1={})
        _write_state(tmp_path, last_gate="Gate 1", last_fr="FR-01")

        result = hooks_factory().preflight_manifest_integrity()

        assert result["passed"] is False
        assert "gate1" in result["reason"]

    def test_empty_gate1_with_residual_artifact_blocks(self, tmp_path, hooks_factory):
        """gate1_result.json on disk proves Gate 1 ran even if state.json
        says otherwise: block."""
        _write_manifest(tmp_path, gate1={})
        _write_state(tmp_path, last_gate=None, last_fr=None)
        (tmp_path / ".methodology" / "gate1_result.json").write_text("{}", encoding="utf-8")

        result = hooks_factory().preflight_manifest_integrity()

        assert result["passed"] is False
        assert "gate1" in result["reason"]

    def test_populated_gate1_passes(self, tmp_path, hooks_factory):
        _write_manifest(tmp_path, gate1={"FR-01": {"score": 99.0}})
        _write_state(tmp_path, last_gate="Gate 1", last_fr="FR-01")

        result = hooks_factory().preflight_manifest_integrity()

        assert result["passed"] is True, result
