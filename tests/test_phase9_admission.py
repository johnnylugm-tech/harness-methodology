"""Phase 9 (Maintenance) admission tests.

Covers: entry-gate acceptance of phase 9, HANDOVER generation at phase 9
(the old advance-crash path), advance-phase --completed 9 BLOCKED, BVS
phase-order prerequisite {9: 8}, and the phase→resource maps.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from constitution.bvs_runner import BVSRunner
from core.quality_gate.phase_artifact_enforcer import Phase
from core.utils.project_layout import PHASE_ARTIFACTS, ProjectLayout, phase_artifacts


class TestPhaseMaps:
    def test_phase_enum_has_maintenance(self):
        assert Phase.from_int(9) is Phase.MAINTENANCE
        with pytest.raises(KeyError):
            Phase.from_int(10)

    def test_project_layout_phase9(self, tmp_path: Path):
        layout = ProjectLayout(tmp_path)
        assert layout.get_phase_dir(9) == tmp_path / "09-maintenance"
        assert layout.maintenance_log_path == tmp_path / "09-maintenance" / "MAINTENANCE_LOG.md"
        assert layout.change_requests_dir == tmp_path / ".methodology" / "change_requests"

    def test_phase_artifacts_9(self):
        assert phase_artifacts(9) == ["09-maintenance/MAINTENANCE_LOG.md"]
        assert PHASE_ARTIFACTS[9] == ["09-maintenance/MAINTENANCE_LOG.md"]

    def test_bvs_prerequisite(self):
        assert BVSRunner.PHASE_PREREQUISITES[9] == 8


class TestEntryGate:
    def test_phase9_accepted_with_gate4_pass(self, tmp_path: Path):
        from harness_cli import _verify_entry_gate
        mdir = tmp_path / ".methodology"
        mdir.mkdir()
        (mdir / "quality_manifest.json").write_text(json.dumps({
            "schema_version": "1.0",
            "generated_at_phase": 2,
            "fr_ids": ["FR-01"],
            "gate_results": {"gate4": {"score": 90.0, "quality_complete": True}},
        }), encoding="utf-8")
        result = _verify_entry_gate(tmp_path, 9)
        assert result["passed"] is True

    def test_phase9_blocked_without_gate4(self, tmp_path: Path):
        from harness_cli import _verify_entry_gate
        mdir = tmp_path / ".methodology"
        mdir.mkdir()
        (mdir / "quality_manifest.json").write_text(json.dumps({
            "schema_version": "1.0",
            "generated_at_phase": 2,
            "fr_ids": ["FR-01"],
            "gate_results": {},
        }), encoding="utf-8")
        result = _verify_entry_gate(tmp_path, 9)
        assert result["passed"] is False

    def test_phase10_out_of_range(self, tmp_path: Path):
        from harness_cli import _verify_entry_gate
        result = _verify_entry_gate(tmp_path, 10)
        assert result["passed"] is False
        assert "out of range" in result["reason"]


class TestHandoverAtPhase9:
    def test_handover_write_phase9_no_crash(self, tmp_path: Path):
        """The old advance-phase --completed 8 crash: _advance_fsm wrote
        current_phase=9 then HandoverGenerator raised ValueError on phase=9.
        Phase 9 must now render cleanly."""
        from harness.handover_generator import HandoverGenerator
        gen = HandoverGenerator(tmp_path)
        out = gen.write(
            checkpoint_id="P9-entry",
            phase=9,
            task_background="entered maintenance",
            current_status="steady state",
            next_steps=["cr-open the first ticket"],
        )
        assert out.exists()
        assert "Maintenance" in out.read_text(encoding="utf-8")


class TestAdvanceBlockedAt9:
    def test_advance_completed_9_blocked(self, tmp_path: Path, capsys):
        from harness_cli import cmd_advance_phase
        mdir = tmp_path / ".methodology"
        mdir.mkdir()
        (mdir / "state.json").write_text(json.dumps({
            "state": "RUNNING", "current_phase": 9,
        }), encoding="utf-8")
        args = argparse.Namespace(completed_phase=9, project=str(tmp_path))
        rc = cmd_advance_phase(args)
        captured = capsys.readouterr()
        assert rc == 2
        assert "terminal" in (captured.out + captured.err).lower()
        # state must be untouched
        state = json.loads((mdir / "state.json").read_text(encoding="utf-8"))
        assert state["current_phase"] == 9
