"""Tests for constitution/bvs_runner.py — behavioral verification system runner."""

import json
from pathlib import Path
from constitution.bvs_runner import BVSRunner


class TestBVSRunnerInit:
    def test_default_phase(self):
        runner = BVSRunner("/tmp/test", phase=1)
        assert runner.phase == 1
        from core.utils.project_layout import ProjectLayout
        assert runner.state_path == ProjectLayout(Path("/tmp/test")).state_json_path

    def test_phase_prerequisites(self):
        assert BVSRunner.PHASE_PREREQUISITES[2] == 1
        assert BVSRunner.PHASE_PREREQUISITES[8] == 7
        assert len(BVSRunner.PHASE_PREREQUISITES) == 7


class TestRunPhaseOrder:
    def test_no_state_file(self, tmp_path):
        runner = BVSRunner(str(tmp_path), phase=3)
        result = runner.run()
        assert result["passed"] is True
        assert result["total_violations"] == 0

    def test_phase_order_ok(self, tmp_path):
        state_dir = tmp_path / ".methodology"
        state_dir.mkdir()
        (state_dir / "state.json").write_text(
            json.dumps({"current_phase": 2, "state": "ACTIVE"})
        )
        runner = BVSRunner(str(tmp_path), phase=3)
        result = runner.run()
        assert result["passed"] is True

    def test_phase_order_skip_prereq(self, tmp_path):
        state_dir = tmp_path / ".methodology"
        state_dir.mkdir()
        (state_dir / "state.json").write_text(
            json.dumps({"current_phase": 1, "state": "ACTIVE"})
        )
        runner = BVSRunner(str(tmp_path), phase=3)
        result = runner.run()
        assert result["passed"] is False
        assert any("HR-03" in v["rule"] for v in result["violations"])

    def test_fsm_freeze_blocks(self, tmp_path):
        state_dir = tmp_path / ".methodology"
        state_dir.mkdir()
        (state_dir / "state.json").write_text(
            json.dumps({"current_phase": 2, "state": "FREEZE"})
        )
        runner = BVSRunner(str(tmp_path), phase=3)
        result = runner.run()
        assert any("FREEZE" in v["message"] for v in result["violations"])

    def test_phase1_no_prereq(self, tmp_path):
        state_dir = tmp_path / ".methodology"
        state_dir.mkdir()
        (state_dir / "state.json").write_text(
            json.dumps({"current_phase": 0, "state": "ACTIVE"})
        )
        runner = BVSRunner(str(tmp_path), phase=1)
        result = runner.run()
        assert result["passed"] is True

    def test_malformed_state_json(self, tmp_path):
        state_dir = tmp_path / ".methodology"
        state_dir.mkdir()
        (state_dir / "state.json").write_text("not valid json")
        runner = BVSRunner(str(tmp_path), phase=3)
        result = runner.run()
        assert result["passed"] is True


class TestRunFull:
    def test_run_full_p1_no_invariants(self, tmp_path):
        """Phase 1-2 skip invariant engine entirely."""
        runner = BVSRunner(str(tmp_path), phase=1)
        result = runner.run_full()
        assert "passed" in result
        assert "phase_order_passed" in result
        assert result["invariant_passed"] is True

    def test_run_full_p2_no_invariants(self, tmp_path):
        runner = BVSRunner(str(tmp_path), phase=2)
        result = runner.run_full()
        assert result["invariant_passed"] is True

    def test_run_full_with_context(self, tmp_path):
        """P3+ should pass without exceptions even if invariant engine fails."""
        state_dir = tmp_path / ".methodology"
        state_dir.mkdir()
        (state_dir / "state.json").write_text(
            json.dumps({"current_phase": 2, "state": "ACTIVE"})
        )
        runner = BVSRunner(str(tmp_path), phase=3)
        result = runner.run_full(context={"phase": 3, "max_allowed_phase": 3})
        assert "passed" in result

    def test_run_full_p3_no_sessions_log(self, tmp_path):
        """P3 without sessions_spawn.log should still work."""
        state_dir = tmp_path / ".methodology"
        state_dir.mkdir()
        (state_dir / "state.json").write_text(
            json.dumps({"current_phase": 2, "state": "ACTIVE"})
        )
        runner = BVSRunner(str(tmp_path), phase=3)
        result = runner.run_full()
        assert "passed" in result
        assert "invariant_report" in result


class TestLoadState:
    def test_no_file(self, tmp_path):
        runner = BVSRunner(str(tmp_path), phase=1)
        assert runner._load_state() is None

    def test_valid_file(self, tmp_path):
        state_dir = tmp_path / ".methodology"
        state_dir.mkdir()
        (state_dir / "state.json").write_text(
            json.dumps({"current_phase": 3})
        )
        runner = BVSRunner(str(tmp_path), phase=1)
        state = runner._load_state()
        assert state == {"current_phase": 3}

    def test_invalid_json(self, tmp_path):
        state_dir = tmp_path / ".methodology"
        state_dir.mkdir()
        (state_dir / "state.json").write_text("{bad")
        runner = BVSRunner(str(tmp_path), phase=1)
        assert runner._load_state() is None
