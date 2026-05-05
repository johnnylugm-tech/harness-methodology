"""
Tests for gate-injected phase plan output in scripts/generate_full_plan.py.

Verifies:
  - Phase 3, 4, 5, 6, 7, 8 plans contain CHECKPOINT labels
  - Correct harness_cli.py commands are embedded per phase
  - Gate 1 (per-FR) and phase-exit gates appear in correct phases
  - Phase 6 uses Gate 4 only (no FR loop)
  - generate_full_plan() returns a non-None string
"""

import json
import sys
from pathlib import Path
import pytest

# Make repo root importable
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from generate_full_plan import (
    _gate1_checkpoint,
    _gate_exit_checkpoint,
    _checkpoint_index,
    _load_manifest_fr_ids,
    _preflight_steps,
    _fr_dev_steps,
    _phase_advance_step,
    generate_phase3_tasks,
    generate_phase4_tasks,
    generate_phase5_tasks,
    generate_phase6_tasks,
    generate_phase7_tasks,
    generate_phase8_tasks,
    generate_full_plan,
)


# ─── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture()
def project(tmp_path: Path) -> Path:
    """Minimal project directory with manifest + placeholder SRS."""
    m_dir = tmp_path / ".methodology"
    m_dir.mkdir()
    manifest = {
        "fr_ids": ["FR-01", "FR-02"],
        "gate_results": {},
    }
    (m_dir / "quality_manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    # Minimal SRS so phases 3/4 can parse (they'll fall back to manifest fr_ids anyway)
    (tmp_path / "SRS.md").write_text("# SRS\n", encoding="utf-8")
    return tmp_path


# ─── _gate1_checkpoint ────────────────────────────────────────────────────────

class TestGate1Checkpoint:
    def test_contains_checkpoint_label(self):
        lines = _gate1_checkpoint("FR-01", 3, 1)
        joined = "\n".join(lines)
        assert "CHECKPOINT-1" in joined
        assert "Gate 1" in joined
        assert "FR-01" in joined

    def test_heading_level_h3(self):
        """GAP-G fix: Gate 1 must use ### heading (not #####)."""
        lines = _gate1_checkpoint("FR-01", 3, 1)
        assert any(l.startswith("### 🔒 CHECKPOINT-1") for l in lines)
        assert not any(l.startswith("#####") for l in lines)

    def test_contains_run_gate_command(self):
        lines = _gate1_checkpoint("FR-02", 4, 2)
        joined = "\n".join(lines)
        assert "run-gate --gate 1 --phase 4 --fr-id FR-02" in joined

    def test_contains_finalize_gate_command(self):
        lines = _gate1_checkpoint("FR-01", 5, 3)
        joined = "\n".join(lines)
        assert "finalize-gate --gate 1 --phase 5 --fr-id FR-01" in joined

    def test_contains_push_step(self):
        lines = _gate1_checkpoint("FR-01", 3, 1)
        joined = "\n".join(lines)
        assert "git push" in joined

    def test_evaluate_dimension_reference(self):
        lines = _gate1_checkpoint("FR-01", 3, 1)
        joined = "\n".join(lines)
        assert "evaluate_dimension.md" in joined

    def test_result_json_reference(self):
        lines = _gate1_checkpoint("FR-01", 3, 1)
        joined = "\n".join(lines)
        assert "gate1_result.json" in joined

    def test_fail_retry_instruction(self):
        """GAP-C fix: FAIL retry loop must appear in Gate 1 checklist."""
        lines = _gate1_checkpoint("FR-01", 3, 1)
        joined = "\n".join(lines)
        assert "FAIL" in joined
        assert "repeat" in joined.lower() or "G1a" in joined

    def test_result_json_overwrite_note(self):
        """GAP-B fix: note about gate1_result.json being overwritten per FR."""
        lines = _gate1_checkpoint("FR-01", 3, 1)
        joined = "\n".join(lines)
        assert "overwritten" in joined or "overwrite" in joined


# ─── _gate_exit_checkpoint ────────────────────────────────────────────────────

class TestGateExitCheckpoint:
    def test_gate2_label(self):
        lines = _gate_exit_checkpoint(2, 3, 5)
        joined = "\n".join(lines)
        assert "CHECKPOINT-5" in joined
        assert "Gate 2" in joined
        assert "Phase 3 Exit" in joined

    def test_gate3_command(self):
        lines = _gate_exit_checkpoint(3, 4, 4)
        joined = "\n".join(lines)
        assert "run-gate --gate 3 --phase 4" in joined
        assert "finalize-gate --gate 3 --phase 4" in joined

    def test_gate4_hermes_note(self):
        lines = _gate_exit_checkpoint(4, 6, 1)
        joined = "\n".join(lines)
        assert "Hermes" in joined

    def test_gate4_result_json(self):
        lines = _gate_exit_checkpoint(4, 6, 1)
        joined = "\n".join(lines)
        assert "gate4_result.json" in joined

    def test_early_stop_cases_present(self):
        """GAP-E fix: early-stop cases must appear in exit gate checklist."""
        for gate_num, phase in [(2, 3), (3, 4), (4, 6)]:
            lines = _gate_exit_checkpoint(gate_num, phase, 1)
            joined = "\n".join(lines)
            assert "CASE 1" in joined, f"Gate {gate_num} missing CASE 1"
            assert "CASE 2" in joined, f"Gate {gate_num} missing CASE 2"
            assert "CASE 3" in joined, f"Gate {gate_num} missing CASE 3"
            assert "CASE 4" in joined, f"Gate {gate_num} missing CASE 4"
            assert "BLOCKED" in joined, f"Gate {gate_num} missing BLOCKED"

    def test_crg_note_for_gates_3_and_4(self):
        """GAP-D fix: CRG recon is automatic — noted as inside run-gate."""
        for gate_num, phase in [(3, 4), (4, 6)]:
            lines = _gate_exit_checkpoint(gate_num, phase, 1)
            joined = "\n".join(lines)
            assert "CRG" in joined
            assert "inside run-gate" in joined or "automatically" in joined

    def test_no_crg_note_for_gate_2(self):
        lines = _gate_exit_checkpoint(2, 3, 1)
        joined = "\n".join(lines)
        assert "CRG" not in joined


# ─── _checkpoint_index ────────────────────────────────────────────────────────

class TestCheckpointIndex:
    def test_phase3_has_fr_and_gate2(self):
        lines = _checkpoint_index(["FR-01", "FR-02"], phase=3)
        joined = "\n".join(lines)
        assert "CHECKPOINT-1" in joined
        assert "Gate 1 / FR-01" in joined
        assert "CHECKPOINT-2" in joined
        assert "Gate 1 / FR-02" in joined
        assert "Gate 2" in joined

    def test_phase6_has_gate4_only(self):
        lines = _checkpoint_index([], phase=6)
        joined = "\n".join(lines)
        assert "Gate 4" in joined
        # No FR entries for P6
        assert "FR-" not in joined

    def test_phase5_no_exit_gate(self):
        lines = _checkpoint_index(["FR-01"], phase=5)
        joined = "\n".join(lines)
        # P5 has Gate 1 per-FR but no phase-exit gate in _PHASE_EXIT_GATES
        assert "Gate 1 / FR-01" in joined
        assert "Gate 2" not in joined
        assert "Gate 3" not in joined


# ─── _preflight_steps ────────────────────────────────────────────────────────

class TestPreflightSteps:
    def test_contains_run_phase_command(self):
        """GAP-J fix: preflight step must include run-phase command."""
        lines = _preflight_steps(3)
        joined = "\n".join(lines)
        assert "run-phase --phase 3" in joined

    def test_contains_preflight_label(self):
        lines = _preflight_steps(5)
        joined = "\n".join(lines)
        assert "[PREFLIGHT]" in joined

    def test_phase_number_injected(self):
        for phase in [3, 4, 5, 6, 7, 8]:
            joined = "\n".join(_preflight_steps(phase))
            assert f"--phase {phase}" in joined


# ─── _fr_dev_steps ───────────────────────────────────────────────────────────

class TestFrDevSteps:
    def test_contains_agent_a_and_b(self):
        """GAP-A fix: A/B steps must appear in FR development."""
        lines = _fr_dev_steps("FR-01", 3)
        joined = "\n".join(lines)
        assert "Agent A" in joined
        assert "Agent B" in joined

    def test_contains_sessions_spawn_log(self):
        """GAP-A / HR-10: sessions_spawn.log entries must be in steps."""
        joined = "\n".join(_fr_dev_steps("FR-01", 3))
        assert "sessions_spawn.log" in joined

    def test_contains_fr_id_in_log_entry(self):
        joined = "\n".join(_fr_dev_steps("FR-02", 3))
        assert "FR-02" in joined

    def test_roles_differ_by_phase(self):
        p3 = "\n".join(_fr_dev_steps("FR-01", 3))
        p4 = "\n".join(_fr_dev_steps("FR-01", 4))
        assert "DEVELOPER" in p3
        assert "QA_ENGINEER" in p4

    def test_hr01_note_present(self):
        """HR-01: A≠B must be called out."""
        joined = "\n".join(_fr_dev_steps("FR-01", 3))
        assert "HR-01" in joined or "DIFFERENT agent" in joined

    def test_hr12_reject_loop_present(self):
        """HR-12: max 5 rounds guard."""
        joined = "\n".join(_fr_dev_steps("FR-01", 3))
        assert "HR-12" in joined or "5 rounds" in joined


# ─── _phase_advance_step ─────────────────────────────────────────────────────

class TestPhaseAdvanceStep:
    def test_phase3_points_to_phase4(self):
        """GAP-F fix: phase advance step must reference next phase."""
        joined = "\n".join(_phase_advance_step(3))
        assert "Phase 4" in joined
        assert "plan-phase --phase 4" in joined

    def test_phase8_pipeline_complete(self):
        joined = "\n".join(_phase_advance_step(8))
        assert "Pipeline Complete" in joined or "complete" in joined.lower()

    def test_hr03_no_skip_warning(self):
        """HR-03: phase order must be sequential."""
        joined = "\n".join(_phase_advance_step(5))
        assert "HR-03" in joined or "no skips" in joined.lower() or "skip" in joined.lower()


# ─── _load_manifest_fr_ids ───────────────────────────────────────────────────

class TestLoadManifestFrIds:
    def test_reads_fr_ids(self, project: Path):
        fr_ids = _load_manifest_fr_ids(project)
        assert fr_ids == ["FR-01", "FR-02"]

    def test_missing_manifest_returns_empty(self, tmp_path: Path):
        fr_ids = _load_manifest_fr_ids(tmp_path)
        assert fr_ids == []

    def test_malformed_manifest_returns_empty(self, tmp_path: Path):
        m_dir = tmp_path / ".methodology"
        m_dir.mkdir()
        (m_dir / "quality_manifest.json").write_text("NOT JSON", encoding="utf-8")
        fr_ids = _load_manifest_fr_ids(tmp_path)
        assert fr_ids == []


# ─── Phase generator integration: checkpoint injection ───────────────────────

class TestPhase3GateInjection:
    def test_has_checkpoint_labels(self, project: Path):
        lines = generate_phase3_tasks(project, project / "SRS.md")
        joined = "\n".join(lines)
        assert "CHECKPOINT-" in joined

    def test_has_gate1_per_fr(self, project: Path):
        lines = generate_phase3_tasks(project, project / "SRS.md")
        joined = "\n".join(lines)
        assert "run-gate --gate 1 --phase 3 --fr-id FR-01" in joined
        assert "run-gate --gate 1 --phase 3 --fr-id FR-02" in joined

    def test_has_gate2_exit(self, project: Path):
        lines = generate_phase3_tasks(project, project / "SRS.md")
        joined = "\n".join(lines)
        assert "run-gate --gate 2 --phase 3" in joined
        assert "finalize-gate --gate 2 --phase 3" in joined

    def test_gate2_after_all_gate1(self, project: Path):
        lines = generate_phase3_tasks(project, project / "SRS.md")
        joined = "\n".join(lines)
        idx_g1_fr02 = joined.rfind("run-gate --gate 1 --phase 3 --fr-id FR-02")
        idx_g2 = joined.find("run-gate --gate 2 --phase 3")
        assert idx_g1_fr02 < idx_g2, "Gate 2 must appear after all Gate 1 per-FR steps"

    def test_has_preflight(self, project: Path):
        """GAP-J fix: preflight step must appear before FR development."""
        joined = "\n".join(generate_phase3_tasks(project, project / "SRS.md"))
        assert "run-phase --phase 3" in joined

    def test_has_ab_dev_steps(self, project: Path):
        """GAP-A fix: A/B protocol must appear before Gate 1."""
        joined = "\n".join(generate_phase3_tasks(project, project / "SRS.md"))
        assert "Agent A" in joined
        assert "sessions_spawn.log" in joined

    def test_has_phase_advance(self, project: Path):
        """GAP-F fix: phase advance instruction at end."""
        joined = "\n".join(generate_phase3_tasks(project, project / "SRS.md"))
        assert "Phase 4" in joined
        assert "plan-phase --phase 4" in joined

    def test_early_stop_in_gate2(self, project: Path):
        """GAP-E fix: early-stop cases in Gate 2."""
        joined = "\n".join(generate_phase3_tasks(project, project / "SRS.md"))
        assert "CASE 1" in joined
        assert "CASE 4" in joined


class TestPhase4GateInjection:
    def test_has_gate1_per_fr(self, project: Path):
        joined = "\n".join(generate_phase4_tasks(project, project / "SRS.md"))
        assert "run-gate --gate 1 --phase 4 --fr-id FR-01" in joined

    def test_has_gate3_exit(self, project: Path):
        joined = "\n".join(generate_phase4_tasks(project, project / "SRS.md"))
        assert "run-gate --gate 3 --phase 4" in joined
        assert "finalize-gate --gate 3 --phase 4" in joined

    def test_has_preflight(self, project: Path):
        joined = "\n".join(generate_phase4_tasks(project, project / "SRS.md"))
        assert "run-phase --phase 4" in joined

    def test_has_ab_dev_steps(self, project: Path):
        joined = "\n".join(generate_phase4_tasks(project, project / "SRS.md"))
        assert "QA_ENGINEER" in joined
        assert "sessions_spawn.log" in joined

    def test_has_phase_advance(self, project: Path):
        joined = "\n".join(generate_phase4_tasks(project, project / "SRS.md"))
        assert "Phase 5" in joined


class TestPhase5GateInjection:
    def test_has_gate1_per_fr(self, project: Path):
        joined = "\n".join(generate_phase5_tasks(project))
        assert "run-gate --gate 1 --phase 5 --fr-id FR-01" in joined
        assert "run-gate --gate 1 --phase 5 --fr-id FR-02" in joined

    def test_no_exit_gate(self, project: Path):
        joined = "\n".join(generate_phase5_tasks(project))
        assert "run-gate --gate 3 --phase 5" not in joined
        assert "run-gate --gate 2 --phase 5" not in joined

    def test_has_preflight(self, project: Path):
        joined = "\n".join(generate_phase5_tasks(project))
        assert "run-phase --phase 5" in joined

    def test_has_ab_dev_steps(self, project: Path):
        joined = "\n".join(generate_phase5_tasks(project))
        assert "sessions_spawn.log" in joined

    def test_has_phase_advance(self, project: Path):
        joined = "\n".join(generate_phase5_tasks(project))
        assert "Phase 6" in joined


class TestPhase6GateInjection:
    def test_gate4_only_no_fr_loop(self, project: Path):
        joined = "\n".join(generate_phase6_tasks(project))
        assert "run-gate --gate 4 --phase 6" in joined
        assert "finalize-gate --gate 4 --phase 6" in joined

    def test_no_per_fr_gate1(self, project: Path):
        joined = "\n".join(generate_phase6_tasks(project))
        assert "run-gate --gate 1 --phase 6" not in joined

    def test_hermes_approve_note(self, project: Path):
        joined = "\n".join(generate_phase6_tasks(project))
        assert "Hermes" in joined

    def test_single_checkpoint(self, project: Path):
        joined = "\n".join(generate_phase6_tasks(project))
        assert "CHECKPOINT-1" in joined
        assert "CHECKPOINT-2" not in joined

    def test_has_preflight(self, project: Path):
        joined = "\n".join(generate_phase6_tasks(project))
        assert "run-phase --phase 6" in joined

    def test_has_early_stop_cases(self, project: Path):
        joined = "\n".join(generate_phase6_tasks(project))
        assert "CASE 1" in joined
        assert "CASE 4" in joined

    def test_has_phase_advance(self, project: Path):
        joined = "\n".join(generate_phase6_tasks(project))
        assert "Phase 7" in joined


class TestPhase7GateInjection:
    def test_has_gate1_per_fr(self, project: Path):
        joined = "\n".join(generate_phase7_tasks(project))
        assert "run-gate --gate 1 --phase 7 --fr-id FR-01" in joined

    def test_no_exit_gate(self, project: Path):
        joined = "\n".join(generate_phase7_tasks(project))
        assert "run-gate --gate 2 --phase 7" not in joined
        assert "run-gate --gate 3 --phase 7" not in joined

    def test_has_preflight(self, project: Path):
        joined = "\n".join(generate_phase7_tasks(project))
        assert "run-phase --phase 7" in joined

    def test_has_ab_dev_steps(self, project: Path):
        joined = "\n".join(generate_phase7_tasks(project))
        assert "DEVOPS" in joined
        assert "sessions_spawn.log" in joined

    def test_has_phase_advance(self, project: Path):
        joined = "\n".join(generate_phase7_tasks(project))
        assert "Phase 8" in joined


class TestPhase8GateInjection:
    def test_has_gate1_per_fr(self, project: Path):
        joined = "\n".join(generate_phase8_tasks(project))
        assert "run-gate --gate 1 --phase 8 --fr-id FR-02" in joined

    def test_no_exit_gate(self, project: Path):
        joined = "\n".join(generate_phase8_tasks(project))
        assert "run-gate --gate 4 --phase 8" not in joined

    def test_has_preflight(self, project: Path):
        joined = "\n".join(generate_phase8_tasks(project))
        assert "run-phase --phase 8" in joined

    def test_has_ab_dev_steps(self, project: Path):
        joined = "\n".join(generate_phase8_tasks(project))
        assert "DEVOPS" in joined
        assert "sessions_spawn.log" in joined

    def test_has_pipeline_complete(self, project: Path):
        joined = "\n".join(generate_phase8_tasks(project))
        assert "Pipeline Complete" in joined or "complete" in joined.lower()


# ─── generate_full_plan integration ──────────────────────────────────────────

class TestGenerateFullPlan:
    def test_returns_string_for_phase3(self, project: Path):
        result = generate_full_plan(3, project)
        assert isinstance(result, str)
        assert len(result) > 100

    def test_plan_header_contains_version(self, project: Path):
        result = generate_full_plan(3, project)
        assert "v6.50.0" in result

    def test_plan_has_checkpoint_index(self, project: Path):
        result = generate_full_plan(3, project)
        assert "Checkpoint Index" in result

    def test_phase6_plan_has_gate4(self, project: Path):
        result = generate_full_plan(6, project)
        assert "Gate 4" in result
        assert "Hermes" in result

    def test_unknown_phase_returns_none(self, project: Path):
        result = generate_full_plan(99, project)
        assert result is None

    def test_output_written_to_file(self, project: Path, tmp_path: Path):
        out = tmp_path / "plan.md"
        result = generate_full_plan(3, project, out)
        assert result is not None
        assert out.exists()
        assert out.read_text(encoding="utf-8") == result
