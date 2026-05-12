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
import re
import sys
from pathlib import Path
from typing import Dict
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
    _entry_gate_check,
    _human_checkpoint,
    _fr_dev_steps,
    _phase_advance_step,
    _decomposition_section,
    _deliverable_ab_block,
    _PHASE_DELIVERABLE_DEPS,
    _GATE_META,
    generate_phase1_tasks,
    generate_phase2_tasks,
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
        assert any(line.startswith("### 🔒 CHECKPOINT-1") for line in lines)
        assert not any(line.startswith("#####") for line in lines)

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


# ─── _entry_gate_check ───────────────────────────────────────────────────────

class TestEntryGateCheck:
    def test_phase3_references_p2_human_approve(self):
        """GAP-M fix: P3 entry requires P2 human review (not harness gate)."""
        joined = "\n".join(_entry_gate_check(3))
        assert "ENTRY-CHECK" in joined
        assert "Phase 2" in joined
        assert "human" in joined.lower() or "Human" in joined

    def test_phase4_references_gate2(self):
        """GAP-M fix: P4 entry requires Gate 2 PASS from P3."""
        joined = "\n".join(_entry_gate_check(4))
        assert "Gate 2" in joined
        assert "Phase 3" in joined
        assert "quality_manifest.json" in joined

    def test_phase5_references_gate3(self):
        joined = "\n".join(_entry_gate_check(5))
        assert "Gate 3" in joined
        assert "Phase 4" in joined

    def test_phase6_references_gate3(self):
        joined = "\n".join(_entry_gate_check(6))
        assert "Gate 3" in joined
        assert "Phase 5" in joined

    def test_phase7_references_gate4(self):
        joined = "\n".join(_entry_gate_check(7))
        assert "Gate 4" in joined
        assert "Phase 6" in joined

    def test_phase8_references_gate4(self):
        joined = "\n".join(_entry_gate_check(8))
        assert "Gate 4" in joined
        assert "Phase 7" in joined

    def test_phase1_returns_empty(self):
        """P1 has no entry gate."""
        assert _entry_gate_check(1) == []

    def test_phase2_references_p1_human_approve(self):
        """P2 entry check confirms P1 human review APPROVE."""
        joined = "\n".join(_entry_gate_check(2))
        assert "ENTRY-CHECK" in joined
        assert "Phase 1" in joined

    def test_contains_hr03_reference(self):
        joined = "\n".join(_entry_gate_check(4))
        assert "HR-03" in joined or "no phase skips" in joined.lower()

    def test_contains_return_instruction(self):
        """Agent must know to return to previous phase if check fails."""
        for phase in [4, 5, 6, 7, 8]:
            joined = "\n".join(_entry_gate_check(phase))
            assert "return to Phase" in joined or "return" in joined.lower()


# ─── _human_checkpoint ───────────────────────────────────────────────────────

class TestHumanCheckpoint:
    def test_phase1_lists_srs_deliverables(self):
        """GAP-K3 fix: P1 human checkpoint lists SRS deliverables."""
        joined = "\n".join(_human_checkpoint(1, 1))
        assert "SRS.md" in joined
        assert "CHECKPOINT-1" in joined

    def test_phase2_lists_sad_deliverables(self):
        """GAP-K3 fix: P2 human checkpoint lists SAD/ADR deliverables."""
        joined = "\n".join(_human_checkpoint(2, 1))
        assert "SAD.md" in joined
        assert "ADR.md" in joined

    def test_contains_approve_reject(self):
        joined = "\n".join(_human_checkpoint(1, 1))
        assert "APPROVE" in joined
        assert "REJECT" in joined

    def test_contains_git_push(self):
        joined = "\n".join(_human_checkpoint(2, 1))
        assert "push-checkpoint" in joined
        assert "HANDOVER.md" in joined

    def test_heading_h3(self):
        lines = _human_checkpoint(1, 1)
        assert any(line.startswith("### 🔒 CHECKPOINT-1") for line in lines)

    def test_not_harness_gate(self):
        """GAP-K fix: P1/P2 checkpoint must clarify it's NOT harness run-gate."""
        joined = "\n".join(_human_checkpoint(1, 1))
        assert "NOT" in joined
        assert "run-gate" in joined

    def test_hr12_max_rounds(self):
        """HR-12: max 5 rounds applies to human review loop too."""
        joined = "\n".join(_human_checkpoint(2, 1))
        assert "5 rounds" in joined or "HR-12" in joined


# ─── _GATE_META dim names (GAP-Q) ─────────────────────────────────────────────

class TestGateMetaDimNames:
    def test_gate1_has_named_dims(self):
        """GAP-Q fix: Gate 1 dim names must be explicit."""
        assert "linting" in _GATE_META[1][2]
        assert "type_safety" in _GATE_META[1][2]
        assert "test_coverage" in _GATE_META[1][2]

    def test_gate2_has_all_7_dims_named(self):
        meta = _GATE_META[2][2]
        for dim in ["linting", "type_safety", "test_coverage",
                    "security", "secrets_scanning", "license_compliance", "mutation_testing"]:
            assert dim in meta, f"Gate 2 missing dim: {dim}"

    def test_gate3_has_all_12_dims_named(self):
        meta = _GATE_META[3][2]
        for dim in ["linting", "type_safety", "test_coverage", "security",
                    "secrets_scanning", "license_compliance", "mutation_testing",
                    "architecture", "readability", "error_handling", "documentation", "performance"]:
            assert dim in meta, f"Gate 3 missing dim: {dim}"

    def test_gate4_references_gate3_dims(self):
        """Gate 4 uses same 12 dims — must say so."""
        meta = _GATE_META[4][2]
        assert "12" in meta or "Gate 3" in meta or "same" in meta.lower()


# ─── _phase_advance_step: P1/P2 labels ───────────────────────────────────────

class TestPhaseAdvanceStep12:
    def test_phase1_points_to_phase2(self):
        """GAP-K fix: P1 advance must reference Phase 2 Architecture Design."""
        joined = "\n".join(_phase_advance_step(1))
        assert "Phase 2" in joined
        assert "plan-phase --phase 2" in joined

    def test_phase2_points_to_phase3(self):
        joined = "\n".join(_phase_advance_step(2))
        assert "Phase 3" in joined
        assert "plan-phase --phase 3" in joined


# ─── _decomposition_section ──────────────────────────────────────────────────

class TestDecompositionSection:
    def test_phase1_returns_non_empty(self):
        lines = _decomposition_section(1)
        assert len(lines) > 0

    def test_phase1_has_dependency_table(self):
        lines = _decomposition_section(1)
        joined = "\n".join(lines)
        assert "Task Decomposition" in joined
        assert "SRS.md" in joined
        assert "CONSTRAINTS.md" in joined
        assert "SPEC_TRACKING.md" in joined
        assert "TRACEABILITY_MATRIX.md" in joined

    def test_phase1_lists_sequential_order(self):
        lines = _decomposition_section(1)
        joined = "\n".join(lines)
        idx_srs = joined.find("SRS.md")
        idx_constraints = joined.find("CONSTRAINTS.md")
        idx_spec = joined.find("SPEC_TRACKING.md")
        idx_trace = joined.find("TRACEABILITY_MATRIX.md")
        assert idx_srs < idx_constraints < idx_spec < idx_trace, "Deliverables must be in dependency order"

    def test_phase2_returns_non_empty(self):
        lines = _decomposition_section(2)
        assert len(lines) > 0

    def test_phase2_has_all_deliverables(self):
        lines = _decomposition_section(2)
        joined = "\n".join(lines)
        assert "SAD.md" in joined
        assert "ADR.md" in joined
        assert "ARCHITECTURE_DIAGRAM.md" in joined

    def test_phase2_lists_sequential_order(self):
        lines = _decomposition_section(2)
        joined = "\n".join(lines)
        idx_sad = joined.find("SAD.md")
        idx_adr = joined.find("ADR.md")
        idx_diag = joined.find("ARCHITECTURE_DIAGRAM.md")
        assert idx_sad < idx_adr < idx_diag, "Deliverables must be in dependency order"

    def test_execution_rule_present(self):
        for phase in [1, 2]:
            joined = "\n".join(_decomposition_section(phase))
            assert "Execution rule" in joined
            assert "REJECTED" in joined

    def test_phase3_returns_empty(self):
        """P3-P8 use FR-based A/B, not deliverable decomposition."""
        for phase in [3, 4, 5, 6, 7, 8]:
            assert _decomposition_section(phase) == []


# ─── _deliverable_ab_block ────────────────────────────────────────────────────

class TestDeliverableAbBlock:
    @pytest.fixture()
    def srs_deliverable(self) -> Dict:
        return _PHASE_DELIVERABLE_DEPS[1][0]

    @pytest.fixture()
    def sad_deliverable(self) -> Dict:
        return _PHASE_DELIVERABLE_DEPS[2][0]

    def test_contains_sub_task_label(self, srs_deliverable: Dict):
        lines = _deliverable_ab_block(1, srs_deliverable, 1, 4)
        joined = "\n".join(lines)
        assert "Sub-Task 1/4" in joined
        assert "SRS.md" in joined

    def test_contains_agent_a_and_b(self, srs_deliverable: Dict):
        lines = _deliverable_ab_block(1, srs_deliverable, 1, 4)
        joined = "\n".join(lines)
        assert "Agent A" in joined
        assert "Agent B" in joined

    def test_contains_depends_on(self, srs_deliverable: Dict):
        lines = _deliverable_ab_block(1, srs_deliverable, 1, 4)
        joined = "\n".join(lines)
        assert "Depends on" in joined

    def test_first_deliverable_no_dependency(self, srs_deliverable: Dict):
        lines = _deliverable_ab_block(1, srs_deliverable, 1, 4)
        joined = "\n".join(lines)
        assert "none — starting point" in joined

    def test_later_deliverable_shows_dependency(self):
        spec_deliverable = _PHASE_DELIVERABLE_DEPS[1][2]  # depends on SRS.md
        lines = _deliverable_ab_block(1, spec_deliverable, 3, 4)
        joined = "\n".join(lines)
        assert "SRS.md" in joined

    def test_contains_sessions_spawn_log(self, srs_deliverable: Dict):
        lines = _deliverable_ab_block(1, srs_deliverable, 1, 4)
        joined = "\n".join(lines)
        assert "sessions_spawn.log" in joined

    def test_contains_hr12_max_rounds(self, srs_deliverable: Dict):
        lines = _deliverable_ab_block(1, srs_deliverable, 1, 4)
        joined = "\n".join(lines)
        assert "5 rounds" in joined or "HR-12" in joined

    def test_not_last_subtask_shows_next(self, srs_deliverable: Dict):
        lines = _deliverable_ab_block(1, srs_deliverable, 1, 4)
        joined = "\n".join(lines)
        assert "Sub-Task 2/4" in joined

    def test_last_subtask_shows_human_review(self):
        trace_deliverable = _PHASE_DELIVERABLE_DEPS[1][3]
        lines = _deliverable_ab_block(1, trace_deliverable, 4, 4)
        joined = "\n".join(lines)
        assert "Human Peer Review" in joined

    def test_contains_stateless_sandbox_warning(self, srs_deliverable: Dict):
        lines = _deliverable_ab_block(1, srs_deliverable, 1, 4)
        joined = "\n".join(lines)
        assert "STATELESS SANDBOX" in joined

    def test_deliverable_specific_checks_appear(self, srs_deliverable: Dict):
        lines = _deliverable_ab_block(1, srs_deliverable, 1, 4)
        joined = "\n".join(lines)
        assert "testable" in joined.lower()

    def test_phase2_deliverable_has_correct_roles(self, sad_deliverable: Dict):
        lines = _deliverable_ab_block(2, sad_deliverable, 1, 3)
        joined = "\n".join(lines)
        assert "ARCHITECT" in joined
        assert "TECH_LEAD" in joined

    def test_b2_three_branch_low_gap_approve(self, srs_deliverable: Dict):
        """[B-2] first branch: APPROVE + all gaps low → continue."""
        lines = _deliverable_ab_block(1, srs_deliverable, 1, 4)
        joined = "\n".join(lines)
        assert "all gaps are `low`" in joined, "Missing low-gap APPROVE branch"
        assert "Sub-Task 2/4" in joined, "Low-gap branch must reference next sub-task"

    def test_b2_three_branch_medium_gap_redispatch(self, srs_deliverable: Dict):
        """[B-2] second branch: APPROVE + medium+ gap → fix → re-dispatch B as round 2."""
        lines = _deliverable_ab_block(1, srs_deliverable, 1, 4)
        joined = "\n".join(lines)
        assert "medium" in joined, "Missing medium-gap re-dispatch branch"
        assert "re-dispatch B as round 2" in joined, "Missing round-2 re-dispatch instruction"
        assert "round-2 APPROVE" in joined, "Must require round-2 APPROVE before continuing"

    def test_b2_blocking_note_present(self, srs_deliverable: Dict):
        """[B-2] must include BLOCKING note preventing early advance to next sub-task."""
        lines = _deliverable_ab_block(1, srs_deliverable, 1, 4)
        joined = "\n".join(lines)
        assert "BLOCKING" in joined, "Missing BLOCKING enforcement note"
        assert "before proceeding" in joined.lower(), "Must say to log before proceeding"

    def test_b2_log_includes_round2_example(self, srs_deliverable: Dict):
        """[LOG] must show round-2 entry example for when medium gap is fixed."""
        lines = _deliverable_ab_block(1, srs_deliverable, 1, 4)
        joined = "\n".join(lines)
        assert '"round":2' in joined, "Missing round-2 entry in LOG example"
        assert "GAP-XX fixes verified" in joined, "Round-2 note field example missing"

    def test_b2_last_subtask_three_branches(self):
        """Last sub-task [B-2] also has three branches (not just APPROVE → Human Review)."""
        trace_deliverable = _PHASE_DELIVERABLE_DEPS[1][3]
        lines = _deliverable_ab_block(1, trace_deliverable, 4, 4)
        joined = "\n".join(lines)
        assert "all gaps are `low`" in joined
        assert "re-dispatch B as round 2" in joined
        # APPROVE branch still references human review
        assert "Human Peer Review" in joined

    def test_b2_three_branch_phase2(self, sad_deliverable: Dict):
        """Phase 2 deliverables also get three-branch [B-2] (not just phase 1)."""
        lines = _deliverable_ab_block(2, sad_deliverable, 1, 3)
        joined = "\n".join(lines)
        assert "all gaps are `low`" in joined
        assert "re-dispatch B as round 2" in joined
        assert "BLOCKING" in joined

    def test_b2_round2_embed_instruction_references_b1(self, srs_deliverable: Dict):
        """Round-2 re-dispatch embed instruction reuses B-1 docs (not a bespoke list)."""
        lines = _deliverable_ab_block(1, srs_deliverable, 1, 4)
        joined = "\n".join(lines)
        assert "same docs as B-1" in joined, (
            "Round-2 embed instruction must say 'same docs as B-1' to avoid context drift"
        )


# ─── Phase 1 generator ───────────────────────────────────────────────────────

class TestPhase1Generator:
    def test_has_preflight(self, project: Path):
        """GAP-K fix: P1 plan must include preflight."""
        joined = "\n".join(generate_phase1_tasks(project, project / "SRS.md"))
        assert "run-phase --phase 1" in joined

    def test_has_decomposition_section(self, project: Path):
        """P1 plan must include task decomposition before A/B work."""
        joined = "\n".join(generate_phase1_tasks(project, project / "SRS.md"))
        assert "Task Decomposition" in joined
        assert "Execution rule" in joined

    def test_has_serial_per_deliverable_ab(self, project: Path):
        """P1 plan must have 4 serial sub-tasks with individual A/B loops."""
        joined = "\n".join(generate_phase1_tasks(project, project / "SRS.md"))
        assert "Sub-Task 1/4" in joined
        assert "Sub-Task 2/4" in joined
        assert "Sub-Task 3/4" in joined
        assert "Sub-Task 4/4" in joined
        assert "SRS.md" in joined
        assert "CONSTRAINTS.md" in joined
        assert "SPEC_TRACKING.md" in joined
        assert "TRACEABILITY_MATRIX.md" in joined

    def test_has_ab_steps(self, project: Path):
        """GAP-K fix: P1 plan must include A/B authoring steps."""
        joined = "\n".join(generate_phase1_tasks(project, project / "SRS.md"))
        assert "REQUIREMENTS_ENGINEER" in joined
        assert "BUSINESS_ANALYST" in joined
        assert "sessions_spawn.log" in joined

    def test_has_human_checkpoint(self, project: Path):
        """GAP-K3 fix: P1 plan must end with human review checkpoint."""
        joined = "\n".join(generate_phase1_tasks(project, project / "SRS.md"))
        assert "Human Peer Review" in joined
        assert "APPROVE" in joined

    def test_has_phase_advance_to_p2(self, project: Path):
        """GAP-K fix: P1 plan must advance to Phase 2."""
        joined = "\n".join(generate_phase1_tasks(project, project / "SRS.md"))
        assert "plan-phase --phase 2" in joined

    def test_exit_gate_clarification(self, project: Path):
        """GAP-K2 fix: P1 must clarify exit gate is human review not harness."""
        joined = "\n".join(generate_phase1_tasks(project, project / "SRS.md"))
        assert "human" in joined.lower() or "Human" in joined
        assert "NOT" in joined or "not" in joined.lower()

    def test_no_harness_run_gate(self, project: Path):
        """P1 must NOT call harness run-gate --gate 1."""
        joined = "\n".join(generate_phase1_tasks(project, project / "SRS.md"))
        assert "run-gate --gate 1 --phase 1" not in joined

    def test_traceability_depends_on_srs_and_spec(self, project: Path):
        """Sub-Task 3 (TRACEABILITY) must declare dependency on SRS + SPEC_TRACKING."""
        joined = "\n".join(generate_phase1_tasks(project, project / "SRS.md"))
        idx_trace = joined.find("Sub-Task 4/4: TRACEABILITY_MATRIX.md")
        assert idx_trace != -1, "TRACEABILITY sub-task heading not found"
        section = joined[idx_trace:idx_trace + 600]
        assert "SRS.md" in section, "SRS.md not referenced in TRACEABILITY section"
        assert "SPEC_TRACKING.md" in section, "SPEC_TRACKING.md not referenced in TRACEABILITY section"

    def test_b2_review_chain_follows_depends_on(self, project: Path):
        """Sub-Task 2 (CONSTRAINTS) depends on SRS.md → dep_note references Sub-Task 1/4."""
        joined = "\n".join(generate_phase1_tasks(project, project / "SRS.md"))
        idx = joined.find("Sub-Task 2/4: CONSTRAINTS.md")
        assert idx != -1, "CONSTRAINTS sub-task heading not found"
        section = joined[idx:idx + 400]
        assert "+ Sub-Task 1/4 review" in section, (
            "CONSTRAINTS dep_note must reference SRS.md review (Sub-Task 1/4), "
            "got: " + section[section.find("Depends on"):section.find("Depends on") + 100]
        )

    def test_b2_review_chain_spec_tracking_not_constraints(self, project: Path):
        """Sub-Task 3 (SPEC_TRACKING) depends on SRS.md, NOT CONSTRAINTS → references 1/4 not 2/4."""
        joined = "\n".join(generate_phase1_tasks(project, project / "SRS.md"))
        idx = joined.find("Sub-Task 3/4: SPEC_TRACKING.md")
        assert idx != -1, "SPEC_TRACKING sub-task heading not found"
        section = joined[idx:idx + 2000]
        # Must reference the SRS.md review (Sub-Task 1/4), not CONSTRAINTS (2/4)
        assert "Sub-Task 1/4" in section, (
            "SPEC_TRACKING dep_note/embed_docs must reference SRS.md (Sub-Task 1/4)"
        )
        assert "Sub-Task 2/4" not in section, (
            "SPEC_TRACKING must NOT reference CONSTRAINTS review (Sub-Task 2/4) — "
            "it does not depend on CONSTRAINTS.md"
        )
        # embed_docs should reference SRS.md B-2 review, not CONSTRAINTS
        assert "SRS.md (Sub-Task 1/4" in section, (
            "SPEC_TRACKING embed_docs must include SRS.md B-2 review"
        )

    def test_b2_review_chain_traceability_multi_dep(self, project: Path):
        """Sub-Task 4 (TRACEABILITY) depends on SRS.md + SPEC_TRACKING → references both."""
        joined = "\n".join(generate_phase1_tasks(project, project / "SRS.md"))
        idx = joined.find("Sub-Task 4/4: TRACEABILITY_MATRIX.md")
        assert idx != -1, "TRACEABILITY sub-task heading not found"
        section = joined[idx:idx + 2500]
        assert "Sub-Task 1/4" in section, (
            "TRACEABILITY dep_note/embed_docs must reference SRS.md (Sub-Task 1/4)"
        )
        assert "Sub-Task 3/4" in section, (
            "TRACEABILITY dep_note/embed_docs must reference SPEC_TRACKING.md (Sub-Task 3/4)"
        )
        # Must NOT reference CONSTRAINTS (Sub-Task 2/4) — TRACEABILITY doesn't depend on it
        assert "Sub-Task 2/4" not in section, (
            "TRACEABILITY must NOT reference CONSTRAINTS (Sub-Task 2/4) — "
            "it does not depend on CONSTRAINTS.md"
        )


# ─── Phase 2 generator ───────────────────────────────────────────────────────

class TestPhase2Generator:
    def test_has_entry_gate_check(self, project: Path):
        """GAP-M fix: P2 checks P1 human approval before starting."""
        joined = "\n".join(generate_phase2_tasks(project, project / "SRS.md"))
        assert "ENTRY-CHECK" in joined
        assert "Phase 1" in joined

    def test_has_preflight(self, project: Path):
        """GAP-K fix: P2 plan must include preflight."""
        joined = "\n".join(generate_phase2_tasks(project, project / "SRS.md"))
        assert "run-phase --phase 2" in joined

    def test_has_decomposition_section(self, project: Path):
        """P2 plan must include task decomposition before A/B work."""
        joined = "\n".join(generate_phase2_tasks(project, project / "SRS.md"))
        assert "Task Decomposition" in joined
        assert "Execution rule" in joined

    def test_has_serial_per_deliverable_ab(self, project: Path):
        """P2 plan must have 3 serial sub-tasks with individual A/B loops."""
        joined = "\n".join(generate_phase2_tasks(project, project / "SRS.md"))
        assert "Sub-Task 1/3" in joined
        assert "Sub-Task 2/3" in joined
        assert "Sub-Task 3/3" in joined
        assert "SAD.md" in joined
        assert "ADR.md" in joined
        assert "ARCHITECTURE_DIAGRAM.md" in joined

    def test_has_ab_steps(self, project: Path):
        """GAP-K fix: P2 plan must include A/B architecture steps."""
        joined = "\n".join(generate_phase2_tasks(project, project / "SRS.md"))
        assert "ARCHITECT" in joined
        assert "TECH_LEAD" in joined
        assert "sessions_spawn.log" in joined

    def test_sessions_spawn_log_six_entries(self, project: Path):
        """P2 has 3 sub-tasks × 2 entries = 6 sessions_spawn.log entries."""
        joined = "\n".join(generate_phase2_tasks(project, project / "SRS.md"))
        assert "6 entries" in joined

    def test_has_human_checkpoint(self, project: Path):
        """GAP-K3 fix: P2 plan must end with human review checkpoint."""
        joined = "\n".join(generate_phase2_tasks(project, project / "SRS.md"))
        assert "Human Peer Review" in joined
        assert "SAD.md" in joined

    def test_has_phase_advance_to_p3(self, project: Path):
        """GAP-K fix: P2 plan must advance to Phase 3."""
        joined = "\n".join(generate_phase2_tasks(project, project / "SRS.md"))
        assert "plan-phase --phase 3" in joined

    def test_no_harness_run_gate(self, project: Path):
        """P2 must NOT call harness run-gate --gate 1."""
        joined = "\n".join(generate_phase2_tasks(project, project / "SRS.md"))
        assert "run-gate --gate 1 --phase 2" not in joined


# ─── Entry gate check in P3-P8 generators ─────────────────────────────────────

class TestEntryGateInGenerators:
    def test_phase3_has_entry_check(self, project: Path):
        """GAP-M: P3 must verify P2 human APPROVE before preflight."""
        joined = "\n".join(generate_phase3_tasks(project, project / "SRS.md"))
        assert "ENTRY-CHECK" in joined

    def test_phase4_has_entry_check(self, project: Path):
        """GAP-M: P4 must verify Gate 2 PASS."""
        joined = "\n".join(generate_phase4_tasks(project, project / "SRS.md"))
        assert "ENTRY-CHECK" in joined
        assert "Gate 2" in joined

    def test_phase5_has_entry_check(self, project: Path):
        joined = "\n".join(generate_phase5_tasks(project))
        assert "ENTRY-CHECK" in joined
        assert "Gate 3" in joined

    def test_phase6_has_entry_check(self, project: Path):
        joined = "\n".join(generate_phase6_tasks(project))
        assert "ENTRY-CHECK" in joined
        assert "Gate 3" in joined

    def test_phase7_has_entry_check(self, project: Path):
        joined = "\n".join(generate_phase7_tasks(project))
        assert "ENTRY-CHECK" in joined
        assert "Gate 4" in joined

    def test_phase8_has_entry_check(self, project: Path):
        joined = "\n".join(generate_phase8_tasks(project))
        assert "ENTRY-CHECK" in joined
        assert "Gate 4" in joined

    def test_entry_check_before_preflight_p4(self, project: Path):
        """GAP-M: entry check must appear BEFORE preflight in plan."""
        lines = generate_phase4_tasks(project, project / "SRS.md")
        joined = "\n".join(lines)
        idx_entry = joined.find("ENTRY-CHECK")
        idx_preflight = joined.find("[PREFLIGHT]")
        assert idx_entry < idx_preflight, "Entry check must precede preflight"


# ─── GAP-N: test_plans cross-reference note in P4 ─────────────────────────────

class TestPhase4TestPlanCrossRef:
    def test_cross_ref_note_when_no_test_plan(self, project: Path):
        """Even without TEST_PLAN.md, A/B task hint references TEST_PLAN.md."""
        joined = "\n".join(generate_phase4_tasks(project, project / "SRS.md"))
        assert "TEST_PLAN.md" in joined

    def test_phase4_ab_role_references_test_plan(self, project: Path):
        """GAP-L fix: QA_ENGINEER task hint must reference TEST_PLAN.md."""
        joined = "\n".join(generate_phase4_tasks(project, project / "SRS.md"))
        assert "TEST_PLAN.md" in joined


# ─── generate_full_plan integration ──────────────────────────────────────────

class TestGenerateFullPlan:
    def test_returns_string_for_phase3(self, project: Path):
        result = generate_full_plan(3, project)
        assert isinstance(result, str)
        assert len(result) > 100

    def test_plan_header_contains_version(self, project: Path):
        result = generate_full_plan(3, project)
        assert re.search(r"harness-methodology v\d+\.\d+", result), f"version not found in: {result[:200]}"

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
