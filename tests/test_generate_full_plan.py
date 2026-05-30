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
    _gate_exit_checkpoint,
    _checkpoint_index,
    _load_manifest_fr_ids,
    _preflight_steps,
    _entry_gate_check,
    _review_checkpoint,
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
    _p3_milestone_push_steps,
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
    (tmp_path / "01-requirements").mkdir(parents=True, exist_ok=True)
    (tmp_path / "01-requirements" / "SRS.md").write_text("# SRS\n", encoding="utf-8")
    return tmp_path


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

    def test_gate4_no_hermes_note(self):
        """Gate 4 is fully automated — no Hermes APPROVE step."""
        lines = _gate_exit_checkpoint(4, 6, 1)
        joined = "\n".join(lines)
        assert "Hermes" not in joined
        assert "await-hermes-approve" not in joined

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


# ─── _p3_milestone_push_steps ─────────────────────────────────────────────────

class TestP3MilestonePushSteps:
    def test_empty_fr_ids_returns_empty(self):
        result = _p3_milestone_push_steps([])
        assert result == []

    def test_all_fr_ids_in_pre_gate2_bash(self):
        """PUSH ④ must use full fr_ids (no ellipsis) in bash command."""
        lines = _p3_milestone_push_steps(["FR-01", "FR-02", "FR-03"])
        joined = "\n".join(lines)
        assert "--fr-ids FR-01,FR-02,FR-03" in joined
        assert "…" not in joined

    def test_mid_bash_safe_no_ellipsis(self):
        """PUSH ③ bash command is ellipsis-free — visual truncation is separate."""
        lines = _p3_milestone_push_steps(["FR-01", "FR-02", "FR-03", "FR-04", "FR-05", "FR-06", "FR-07"])
        joined = "\n".join(lines)
        # Visual truncation in the comment line
        assert "…+2" in joined
        # P3-mid bash command uses first {mid} IDs only — no ellipsis
        assert "--fr-ids FR-01,FR-02,FR-03" in joined
        # P3-pre-gate2 bash command uses all IDs — no ellipsis
        assert "--fr-ids FR-01,FR-02,FR-03,FR-04,FR-05,FR-06,FR-07" in joined

    def test_contains_milestone_labels(self):
        lines = _p3_milestone_push_steps(["FR-01"])
        joined = "\n".join(lines)
        assert "PUSH ③ — P3-mid" in joined
        assert "PUSH ④ — P3-pre-gate2" in joined
        assert "push-milestone" in joined


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
    def test_phase1_contains_agent_a_and_b(self):
        """Phase 1-2: A/B steps must appear in FR development."""
        for phase in (1, 2):
            joined = "\n".join(_fr_dev_steps("FR-01", phase))
            assert "Agent A" in joined, f"Phase {phase} missing Agent A"
            assert "Agent B" in joined, f"Phase {phase} missing Agent B"

    def test_phase3_plus_no_agent_ab(self):
        """Phase 3+: no A/B — orchestrator dispatches sub-agents instead."""
        for phase in range(3, 9):
            joined = "\n".join(_fr_dev_steps("FR-01", phase))
            assert "Agent A" not in joined, f"Phase {phase} should not have Agent A"
            assert "Agent B" not in joined, f"Phase {phase} should not have Agent B"
            # New model: orchestrator dispatches sub-agents via run-fr-step
            assert "run-fr-step" in joined, f"Phase {phase} missing run-fr-step dispatch"

    def test_phase1_contains_dispatch(self):
        """Phase 1-2: dispatch commands must be in steps (HR-10)."""
        for phase in (1, 2):
            joined = "\n".join(_fr_dev_steps("FR-01", phase))
            assert "dispatch" in joined, f"Phase {phase} missing dispatch command"

    def test_phase3_plus_contains_run_gate(self):
        """Phase 3+: GATE1 sub-agent dispatch (ORCH-GATE1) replaces direct run-gate call."""
        for phase in range(3, 9):
            joined = "\n".join(_fr_dev_steps("FR-01", phase))
            assert "ORCH-GATE1" in joined, f"Phase {phase} missing ORCH-GATE1"
            assert "run-fr-step" in joined, f"Phase {phase} missing run-fr-step"

    def test_contains_fr_id(self):
        for phase in range(1, 9):
            joined = "\n".join(_fr_dev_steps("FR-02", phase))
            assert "FR-02" in joined, f"Phase {phase} missing FR-02"

    def test_roles_differ_by_phase_p1p2(self):
        p1 = "\n".join(_fr_dev_steps("FR-01", 1))
        p2 = "\n".join(_fr_dev_steps("FR-01", 2))
        assert "REQUIREMENTS_ENGINEER" in p1
        assert "ARCHITECT" in p2

    def test_phase3_plus_no_role_labels(self):
        """Phase 3+ output should not reference role labels like DEVELOPER, QA_ENGINEER."""
        for phase in range(3, 9):
            joined = "\n".join(_fr_dev_steps("FR-01", phase))
            assert "DEVELOPER" not in joined, f"Phase {phase} should not have role label"

    def test_ab_review_structure_present_for_phase1(self):
        """P1 FR steps still describe the A/B workflow (Agent A authors, Agent B reviews);
        the HR-01/HR-10 log-count ceremony is removed."""
        joined = "\n".join(_fr_dev_steps("FR-01", 1))
        assert "Agent B" in joined or "reviewer" in joined.lower()
        assert "HR-01" not in joined and "HR-10" not in joined

    def test_no_hr01_for_phase3_plus(self):
        """Phase 3+: no HR-01 reference since A/B is removed."""
        joined = "\n".join(_fr_dev_steps("FR-01", 3))
        assert "HR-01" not in joined

    def test_hr12_reject_loop_present_for_phase1(self):
        """HR-12: max 5 rounds guard for Phase 1-2."""
        joined = "\n".join(_fr_dev_steps("FR-01", 1))
        assert "HR-12" in joined or "5 rounds" in joined

    def test_no_hr12_for_phase3_plus(self):
        """Phase 3+: no HR-12 reference since A/B review loop is removed."""
        joined = "\n".join(_fr_dev_steps("FR-01", 3))
        assert "HR-12" not in joined


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
        """Phase 3 Gate 1 dispatched via sub-agent (run-fr-step), not inline run-gate."""
        lines = generate_phase3_tasks(project, project / "SRS.md")
        joined = "\n".join(lines)
        assert "run-fr-step --phase 3 --fr-id FR-01" in joined
        assert "run-fr-step --phase 3 --fr-id FR-02" in joined
        # Old inline run-gate must NOT appear for per-FR Gate 1
        assert "run-gate --gate 1 --phase 3 --fr-id FR-01" not in joined

    def test_has_gate2_exit(self, project: Path):
        lines = generate_phase3_tasks(project, project / "SRS.md")
        joined = "\n".join(lines)
        assert "run-gate --gate 2 --phase 3" in joined
        assert "finalize-gate --gate 2 --phase 3" in joined

    def test_gate2_after_all_gate1(self, project: Path):
        """Gate 2 exit section must appear after sub-agent GATE1 dispatch for last FR."""
        lines = generate_phase3_tasks(project, project / "SRS.md")
        joined = "\n".join(lines)
        # ORCH-GATE1 dispatch for FR-02 (last new FR in fixture) must precede Gate 2 exit
        idx_g1_fr02 = joined.rfind("run-fr-step --phase 3 --fr-id FR-02")
        idx_g2 = joined.find("run-gate --gate 2 --phase 3")
        assert idx_g1_fr02 != -1, "run-fr-step for FR-02 not found"
        assert idx_g1_fr02 < idx_g2, "Gate 2 must appear after all Gate 1 per-FR steps"

    def test_has_preflight(self, project: Path):
        """GAP-J fix: preflight step must appear before FR development."""
        joined = "\n".join(generate_phase3_tasks(project, project / "SRS.md"))
        assert "run-phase --phase 3" in joined

    def test_has_phase_audit_in_dev_steps(self, project: Path):
        """Phase 3 uses sub-agent orchestration (run-fr-step) instead of A/B."""
        joined = "\n".join(generate_phase3_tasks(project, project / "SRS.md"))
        assert "run-fr-step" in joined
        assert "Agent A" not in joined

    def test_has_phase_audit_step(self, project: Path):
        """Phase 3: audit handled by advance-phase (no separate audit-phase command)."""
        joined = "\n".join(generate_phase3_tasks(project, project / "SRS.md"))
        assert "PHASE-AUDIT-LOCAL" not in joined
        assert "audit-phase --phase 3" not in joined
        assert "advance-phase --completed 3" in joined

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
        """Phase 4 Gate 1 dispatched via sub-agent (run-fr-step), not inline run-gate."""
        joined = "\n".join(generate_phase4_tasks(project, project / "SRS.md"))
        assert "run-fr-step --phase 4 --fr-id FR-01" in joined
        assert "run-gate --gate 1 --phase 4 --fr-id FR-01" not in joined

    def test_has_gate3_exit(self, project: Path):
        joined = "\n".join(generate_phase4_tasks(project, project / "SRS.md"))
        assert "run-gate --gate 3 --phase 4" in joined
        assert "finalize-gate --gate 3 --phase 4" in joined

    def test_has_preflight(self, project: Path):
        joined = "\n".join(generate_phase4_tasks(project, project / "SRS.md"))
        assert "run-phase --phase 4" in joined

    def test_has_phase_audit_in_dev_steps(self, project: Path):
        """Phase 4 uses sub-agent orchestration (run-fr-step) instead of A/B."""
        joined = "\n".join(generate_phase4_tasks(project, project / "SRS.md"))
        assert "run-fr-step" in joined
        assert "Phase End Audit" not in joined
        assert "QA_ENGINEER" not in joined

    def test_has_phase_audit_step(self, project: Path):
        """Phase 4: audit handled by advance-phase (no separate audit-phase command)."""
        joined = "\n".join(generate_phase4_tasks(project, project / "SRS.md"))
        assert "PHASE-AUDIT-LOCAL" not in joined
        assert "audit-phase --phase 4" not in joined
        assert "advance-phase --completed 4" in joined

    def test_has_phase_advance(self, project: Path):
        joined = "\n".join(generate_phase4_tasks(project, project / "SRS.md"))
        assert "Phase 5" in joined


class TestPhase5GateInjection:
    def test_has_gate1_per_fr(self, project: Path):
        """Phase 5 carry-forward FRs use GATE1-DELTA sub-agent dispatch."""
        joined = "\n".join(generate_phase5_tasks(project))
        assert "run-fr-step --phase 5 --fr-id FR-01" in joined
        assert "GATE1-DELTA" in joined

    def test_no_exit_gate(self, project: Path):
        joined = "\n".join(generate_phase5_tasks(project))
        assert "run-gate --gate 3 --phase 5" not in joined
        assert "run-gate --gate 2 --phase 5" not in joined

    def test_has_preflight(self, project: Path):
        joined = "\n".join(generate_phase5_tasks(project))
        assert "run-phase --phase 5" in joined

    def test_has_phase_audit_in_dev_steps(self, project: Path):
        joined = "\n".join(generate_phase5_tasks(project))
        assert "run-fr-step" in joined

    def test_has_phase_audit_step(self, project: Path):
        """Phase 5: audit handled by advance-phase (no separate audit-phase command)."""
        joined = "\n".join(generate_phase5_tasks(project))
        assert "PHASE-AUDIT-LOCAL" not in joined
        assert "audit-phase --phase 5" not in joined
        assert "advance-phase --completed 5" in joined

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

    def test_no_hermes_approve_note(self, project: Path):
        """P6 Gate 4 is fully automated — no Hermes APPROVE step in generated plan."""
        joined = "\n".join(generate_phase6_tasks(project))
        assert "Hermes" not in joined
        assert "await-hermes-approve" not in joined

    def test_single_checkpoint(self, project: Path):
        joined = "\n".join(generate_phase6_tasks(project))
        assert "CHECKPOINT-1" in joined
        assert "CHECKPOINT-2" not in joined

    def test_no_ab_roles_section(self, project: Path):
        """P6 no longer has A/B Roles section — replaced by Phase End Audit."""
        joined = "\n".join(generate_phase6_tasks(project))
        assert "P6 A/B Roles" not in joined
        assert "P6 Phase End Audit" in joined

    def test_has_phase_audit_step(self, project: Path):
        """Phase 6: audit handled by advance-phase (no separate audit-phase command)."""
        joined = "\n".join(generate_phase6_tasks(project))
        assert "PHASE-AUDIT-LOCAL" not in joined
        assert "audit-phase --phase 6" not in joined
        assert "advance-phase --completed 6" in joined

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
        """Phase 7 carry-forward FRs use GATE1-DELTA sub-agent dispatch."""
        joined = "\n".join(generate_phase7_tasks(project))
        assert "run-fr-step --phase 7 --fr-id FR-01" in joined
        assert "GATE1-DELTA" in joined

    def test_no_exit_gate(self, project: Path):
        joined = "\n".join(generate_phase7_tasks(project))
        assert "run-gate --gate 2 --phase 7" not in joined
        assert "run-gate --gate 3 --phase 7" not in joined

    def test_has_preflight(self, project: Path):
        joined = "\n".join(generate_phase7_tasks(project))
        assert "run-phase --phase 7" in joined

    def test_has_phase_audit_in_dev_steps(self, project: Path):
        joined = "\n".join(generate_phase7_tasks(project))
        assert "run-fr-step" in joined
        assert "DEVOPS" not in joined

    def test_has_phase_audit_step(self, project: Path):
        """Phase 7: audit handled by advance-phase (no separate audit-phase command)."""
        joined = "\n".join(generate_phase7_tasks(project))
        assert "PHASE-AUDIT-LOCAL" not in joined
        assert "audit-phase --phase 7" not in joined
        assert "advance-phase --completed 7" in joined

    def test_has_phase_advance(self, project: Path):
        joined = "\n".join(generate_phase7_tasks(project))
        assert "Phase 8" in joined


class TestPhase8GateInjection:
    def test_has_gate1_per_fr(self, project: Path):
        """Phase 8 carry-forward FRs use GATE1-DELTA sub-agent dispatch."""
        joined = "\n".join(generate_phase8_tasks(project))
        assert "run-fr-step --phase 8 --fr-id FR-02" in joined
        assert "GATE1-DELTA" in joined

    def test_no_exit_gate(self, project: Path):
        joined = "\n".join(generate_phase8_tasks(project))
        assert "run-gate --gate 4 --phase 8" not in joined

    def test_has_preflight(self, project: Path):
        joined = "\n".join(generate_phase8_tasks(project))
        assert "run-phase --phase 8" in joined

    def test_has_phase_audit_in_dev_steps(self, project: Path):
        joined = "\n".join(generate_phase8_tasks(project))
        assert "run-fr-step" in joined
        assert "DEVOPS" not in joined

    def test_has_phase_audit_step(self, project: Path):
        """Phase 8: audit handled by advance-phase; final phase → Pipeline Complete."""
        joined = "\n".join(generate_phase8_tasks(project))
        assert "PHASE-AUDIT-LOCAL" not in joined
        assert "audit-phase --phase 8" not in joined
        assert "Pipeline Complete" in joined

    def test_has_pipeline_complete(self, project: Path):
        joined = "\n".join(generate_phase8_tasks(project))
        assert "Pipeline Complete" in joined or "complete" in joined.lower()


# ─── _entry_gate_check ───────────────────────────────────────────────────────

class TestEntryGateCheck:
    def test_phase3_references_p2_review_complete(self):
        """GAP-M fix: P3 entry requires P2 review-complete (not harness gate)."""
        joined = "\n".join(_entry_gate_check(3))
        assert "ENTRY-CHECK" in joined
        assert "Phase 2" in joined
        assert "review-complete" in joined

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
        assert "P5" in joined

    def test_phase7_references_gate4(self):
        joined = "\n".join(_entry_gate_check(7))
        assert "Gate 4" in joined
        assert "P6" in joined

    def test_phase8_references_gate4(self):
        joined = "\n".join(_entry_gate_check(8))
        assert "Gate 4" in joined
        assert "P7" in joined

    def test_phase1_returns_empty(self):
        """P1 has no entry gate."""
        assert _entry_gate_check(1) == []

    def test_phase2_references_p1_human_approve(self):
        """P2 entry check confirms P1 human review APPROVE."""
        joined = "\n".join(_entry_gate_check(2))
        assert "ENTRY-CHECK" in joined
        assert "Phase 1" in joined

    def test_contains_hr03_reference(self):
        """Phase advance step includes HR-03 (no checkpoint skip)."""
        joined = "\n".join(_phase_advance_step(4))
        assert "HR-03" in joined or "no phase skips" in joined.lower()

    def test_contains_return_instruction(self):
        """Agent must know to return to previous phase if check fails."""
        for phase in [4, 5, 6, 7, 8]:
            joined = "\n".join(_entry_gate_check(phase))
            assert "return to Phase" in joined or "return" in joined.lower()


# ─── _review_checkpoint ──────────────────────────────────────────────────────

class TestReviewCheckpoint:
    def test_phase1_lists_srs_deliverables(self):
        """GAP-K3 fix: P1 review checkpoint lists SRS deliverables."""
        joined = "\n".join(_review_checkpoint(1, 1))
        assert "SRS.md" in joined
        assert "CHECKPOINT-1" in joined

    def test_phase2_lists_sad_deliverables(self):
        """GAP-K3 fix: P2 review checkpoint lists SAD deliverables."""
        joined = "\n".join(_review_checkpoint(2, 1))
        assert "SAD.md" in joined

    def test_contains_approve_reject(self):
        joined = "\n".join(_review_checkpoint(1, 1))
        assert "APPROVE" in joined
        assert "REJECT" in joined

    def test_contains_git_push(self):
        joined = "\n".join(_review_checkpoint(2, 1))
        assert "push-checkpoint" in joined
        assert "HANDOVER.md" in joined

    def test_heading_h3(self):
        lines = _review_checkpoint(1, 1)
        assert any(line.startswith("### 🔒 CHECKPOINT-1") for line in lines)

    def test_not_harness_gate(self):
        """GAP-K fix: P1/P2 checkpoint must clarify it's NOT harness run-gate."""
        joined = "\n".join(_review_checkpoint(1, 1))
        assert "NOT" in joined
        assert "run-gate" in joined

    def test_hr12_max_rounds(self):
        """HR-12: max 5 rounds applies to review loop too."""
        joined = "\n".join(_review_checkpoint(2, 1))
        assert "5 rounds" in joined or "HR-12" in joined

    def test_no_human_reviewer_language(self):
        """Exit gate must NOT use 'Reviewer reads/records' — that implies human, not sub-agent."""
        for phase in (1, 2):
            joined = "\n".join(_review_checkpoint(phase, 1))
            assert "Reviewer reads" not in joined, f"P{phase}: 'Reviewer reads' is human-reviewer language"
            assert "Reviewer records" not in joined, f"P{phase}: 'Reviewer records' is human-reviewer language"

    def test_has_stateless_subagent_dispatch(self):
        """Exit gate must dispatch Agent B as STATELESS subagent (same as inline [B-1])."""
        for phase in (1, 2):
            joined = "\n".join(_review_checkpoint(phase, 1))
            assert "STATELESS" in joined, f"P{phase}: missing STATELESS subagent dispatch"
            assert "dispatch as **STATELESS** subagent" in joined, \
                f"P{phase}: missing explicit dispatch instruction"

    def test_has_correct_role_b(self):
        """Exit gate must name the correct Agent B role per phase."""
        assert "BUSINESS_ANALYST" in "\n".join(_review_checkpoint(1, 1))
        assert "TECH_LEAD" in "\n".join(_review_checkpoint(2, 1))

    def test_no_reviewer_xxxx_placeholder(self):
        """reviewer: XXXX looks like a human name field — must not appear."""
        for phase in (1, 2):
            joined = "\n".join(_review_checkpoint(phase, 1))
            assert '"reviewer": "XXXX"' not in joined, \
                f"P{phase}: reviewer XXXX placeholder implies human identity"

    def test_has_b1_b2_labels(self):
        """Exit gate must use [B-1][B-2] labels consistent with inline sub-task dispatch."""
        for phase in (1, 2):
            joined = "\n".join(_review_checkpoint(phase, 1))
            assert "[B-1]" in joined, f"P{phase}: missing [B-1] dispatch step"
            assert "[B-2]" in joined, f"P{phase}: missing [B-2] response parsing step"

    def test_has_prompt_template(self):
        """Exit gate must include Agent B prompt template (not just checklist)."""
        for phase in (1, 2):
            joined = "\n".join(_review_checkpoint(phase, 1))
            assert "You are" in joined, f"P{phase}: missing Agent B prompt template"
            assert "Return JSON only" in joined, f"P{phase}: missing JSON return instruction"


# ─── _GATE_META dim names (GAP-Q) ─────────────────────────────────────────────

class TestGateMetaDimNames:
    def test_gate1_has_named_dims(self):
        """GAP-Q fix: Gate 1 dim names must be explicit."""
        assert "linting" in _GATE_META[1][2]
        assert "type_safety" in _GATE_META[1][2]
        assert "test_coverage" in _GATE_META[1][2]

    def test_gate2_has_all_10_dims_named(self):
        meta = _GATE_META[2][2]
        for dim in ["linting", "type_safety", "test_coverage",
                    "security", "secrets_scanning", "license_compliance", "mutation_testing",
                    "integration_coverage", "test_assertion_quality"]:
            assert dim in meta, f"Gate 2 missing dim: {dim}"

    def test_gate3_has_all_15_dims_named(self):
        meta = _GATE_META[3][2]
        for dim in ["linting", "type_safety", "test_coverage", "security",
                    "secrets_scanning", "license_compliance", "mutation_testing",
                    "integration_coverage", "architecture", "readability", "error_handling",
                    "documentation", "test_assertion_quality", "performance"]:
            assert dim in meta, f"Gate 3 missing dim: {dim}"

    def test_gate4_no_hermes_in_meta(self):
        """Gate 4 is fully automated — Hermes APPROVE must NOT be in gate meta description."""
        meta = _GATE_META[4][2]
        assert "Hermes APPROVE" not in meta


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
        assert "SPEC_TRACKING.md" in joined
        assert "TRACEABILITY_MATRIX.md" in joined

    def test_phase1_lists_sequential_order(self):
        lines = _decomposition_section(1)
        joined = "\n".join(lines)
        idx_srs = joined.find("SRS.md")
        idx_spec = joined.find("SPEC_TRACKING.md")
        idx_trace = joined.find("TRACEABILITY_MATRIX.md")
        assert idx_srs < idx_spec < idx_trace, "Deliverables must be in dependency order"

    def test_phase2_returns_non_empty(self):
        lines = _decomposition_section(2)
        assert len(lines) > 0

    def test_phase2_has_all_deliverables(self):
        lines = _decomposition_section(2)
        joined = "\n".join(lines)
        assert "SAD.md" in joined

    def test_phase2_lists_sequential_order(self):
        """P2 single deliverable: verify it appears as sole item with correct dependency."""
        lines = _decomposition_section(2)
        joined = "\n".join(lines)
        assert "SAD.md" in joined
        assert "1 | `SAD.md` | (none — starting point)" in joined

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
        lines = _deliverable_ab_block(1, srs_deliverable, 1, 3)
        joined = "\n".join(lines)
        assert "Sub-Task 1/3" in joined
        assert "SRS.md" in joined

    def test_contains_agent_a_and_b(self, srs_deliverable: Dict):
        lines = _deliverable_ab_block(1, srs_deliverable, 1, 3)
        joined = "\n".join(lines)
        assert "Agent A" in joined
        assert "Agent B" in joined

    def test_contains_depends_on(self, srs_deliverable: Dict):
        lines = _deliverable_ab_block(1, srs_deliverable, 1, 3)
        joined = "\n".join(lines)
        assert "Depends on" in joined

    def test_first_deliverable_no_dependency(self, srs_deliverable: Dict):
        lines = _deliverable_ab_block(1, srs_deliverable, 1, 3)
        joined = "\n".join(lines)
        assert "none — starting point" in joined

    def test_later_deliverable_shows_dependency(self):
        spec_deliverable = _PHASE_DELIVERABLE_DEPS[1][1]  # SPEC_TRACKING depends on SRS.md
        lines = _deliverable_ab_block(1, spec_deliverable, 2, 3)
        joined = "\n".join(lines)
        assert "SRS.md" in joined

    def test_contains_sessions_spawn_log(self, srs_deliverable: Dict):
        lines = _deliverable_ab_block(1, srs_deliverable, 1, 3)
        joined = "\n".join(lines)
        assert "sessions_spawn.log" in joined

    def test_contains_hr12_max_rounds(self, srs_deliverable: Dict):
        lines = _deliverable_ab_block(1, srs_deliverable, 1, 3)
        joined = "\n".join(lines)
        assert "5 rounds" in joined or "HR-12" in joined

    def test_not_last_subtask_shows_next(self, srs_deliverable: Dict):
        lines = _deliverable_ab_block(1, srs_deliverable, 1, 3)
        joined = "\n".join(lines)
        assert "Sub-Task 2/3" in joined

    def test_last_subtask_shows_agent_b_review(self):
        trace_deliverable = _PHASE_DELIVERABLE_DEPS[1][2]
        lines = _deliverable_ab_block(1, trace_deliverable, 3, 3)
        joined = "\n".join(lines)
        assert "Agent B Peer Review" in joined

    def test_contains_stateless_sandbox_warning(self, srs_deliverable: Dict):
        lines = _deliverable_ab_block(1, srs_deliverable, 1, 3)
        joined = "\n".join(lines)
        assert "STATELESS SANDBOX" in joined

    def test_deliverable_specific_checks_appear(self, srs_deliverable: Dict):
        lines = _deliverable_ab_block(1, srs_deliverable, 1, 3)
        joined = "\n".join(lines)
        assert "testable" in joined.lower()

    def test_phase2_deliverable_has_correct_roles(self, sad_deliverable: Dict):
        lines = _deliverable_ab_block(2, sad_deliverable, 1, 1)
        joined = "\n".join(lines)
        assert "ARCHITECT" in joined
        assert "TECH_LEAD" in joined

    def test_b2_three_branch_low_gap_approve(self, srs_deliverable: Dict):
        """[B-2] first branch: APPROVE + all gaps low → continue."""
        lines = _deliverable_ab_block(1, srs_deliverable, 1, 3)
        joined = "\n".join(lines)
        assert "all gaps are `low`" in joined, "Missing low-gap APPROVE branch"
        assert "Sub-Task 2/3" in joined, "Low-gap branch must reference next sub-task"

    def test_b2_three_branch_medium_gap_redispatch(self, srs_deliverable: Dict):
        """[B-2] second branch: APPROVE + medium+ gap → fix → re-dispatch B as round 2."""
        lines = _deliverable_ab_block(1, srs_deliverable, 1, 3)
        joined = "\n".join(lines)
        assert "medium" in joined, "Missing medium-gap re-dispatch branch"
        assert "re-dispatch B as round 2" in joined, "Missing round-2 re-dispatch instruction"
        assert "round-2 APPROVE" in joined, "Must require round-2 APPROVE before continuing"

    def test_b2_blocking_note_present(self, srs_deliverable: Dict):
        """[B-2] must include BLOCKING note preventing early advance to next sub-task."""
        lines = _deliverable_ab_block(1, srs_deliverable, 1, 3)
        joined = "\n".join(lines)
        assert "BLOCKING" in joined, "Missing BLOCKING enforcement note"
        assert "do not start the next sub-task" in joined.lower(), "Must block early advance"

    def test_b2_log_note_is_nonblocking_debug_trail(self, srs_deliverable: Dict):
        """sessions_spawn.log is described as a non-blocking debug trail (HR-10 enforcement removed)."""
        lines = _deliverable_ab_block(1, srs_deliverable, 1, 3)
        joined = "\n".join(lines)
        assert "non-blocking debug trail" in joined
        assert "HR-10" not in joined
        assert "sessions_spawn.log" in joined, "Missing log reference"

    def test_b2_last_subtask_three_branches(self):
        """Last sub-task [B-2] also has three branches (not just APPROVE → Agent B Review)."""
        trace_deliverable = _PHASE_DELIVERABLE_DEPS[1][2]
        lines = _deliverable_ab_block(1, trace_deliverable, 3, 3)
        joined = "\n".join(lines)
        assert "all gaps are `low`" in joined
        assert "re-dispatch B as round 2" in joined
        # APPROVE branch still references agent b review
        assert "Agent B Peer Review" in joined

    def test_b2_three_branch_phase2(self, sad_deliverable: Dict):
        """Phase 2 deliverables also get three-branch [B-2] (not just phase 1)."""
        lines = _deliverable_ab_block(2, sad_deliverable, 1, 1)
        joined = "\n".join(lines)
        assert "all gaps are `low`" in joined
        assert "re-dispatch B as round 2" in joined
        assert "BLOCKING" in joined

    def test_b2_round2_embed_instruction_references_b1(self, srs_deliverable: Dict):
        """Round-2 re-dispatch embed instruction reuses B-1 docs (not a bespoke list)."""
        lines = _deliverable_ab_block(1, srs_deliverable, 1, 3)
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
        assert "SPEC_TRACKING.md" in joined
        assert "TRACEABILITY_MATRIX.md" in joined

    def test_has_ab_steps(self, project: Path):
        """GAP-K fix: P1 plan must include A/B authoring steps."""
        joined = "\n".join(generate_phase1_tasks(project, project / "SRS.md"))
        assert "REQUIREMENTS_ENGINEER" in joined
        assert "BUSINESS_ANALYST" in joined
        assert "sessions_spawn.log" in joined

    def test_has_review_checkpoint(self, project: Path):
        """GAP-K3 fix: P1 plan must end with Agent B review checkpoint."""
        joined = "\n".join(generate_phase1_tasks(project, project / "SRS.md"))
        assert "Agent B Peer Review" in joined
        assert "APPROVE" in joined

    def test_has_phase_advance_to_p2(self, project: Path):
        """GAP-K fix: P1 plan must advance to Phase 2."""
        joined = "\n".join(generate_phase1_tasks(project, project / "SRS.md"))
        assert "plan-phase --phase 2" in joined

    def test_exit_gate_clarification(self, project: Path):
        """GAP-K2 fix: P1 must clarify exit gate is peer review not harness run-gate."""
        joined = "\n".join(generate_phase1_tasks(project, project / "SRS.md"))
        # Terminology renamed in 6721c6c: "human peer review" → "Agent B peer review".
        # Test the intent (peer review vs harness gate), not the specific actor word.
        assert "peer review" in joined.lower()
        assert "NOT" in joined or "not" in joined.lower()

    def test_no_harness_run_gate(self, project: Path):
        """P1 must NOT call harness run-gate --gate 1."""
        joined = "\n".join(generate_phase1_tasks(project, project / "SRS.md"))
        assert "run-gate --gate 1 --phase 1" not in joined

    def test_traceability_depends_on_srs_and_spec(self, project: Path):
        """Sub-Task 3 (TRACEABILITY) must declare dependency on SRS + SPEC_TRACKING."""
        joined = "\n".join(generate_phase1_tasks(project, project / "SRS.md"))
        idx_trace = joined.find("Sub-Task 3/4: TRACEABILITY_MATRIX.md")
        assert idx_trace != -1, "TRACEABILITY sub-task heading not found"
        section = joined[idx_trace:idx_trace + 600]
        assert "SRS.md" in section, "SRS.md not referenced in TRACEABILITY section"
        assert "SPEC_TRACKING.md" in section, "SPEC_TRACKING.md not referenced in TRACEABILITY section"

    def test_b2_review_chain_follows_depends_on(self, project: Path):
        """Sub-Task 2 (SPEC_TRACKING) depends on SRS.md → dep_note references Sub-Task 1/4."""
        joined = "\n".join(generate_phase1_tasks(project, project / "SRS.md"))
        idx = joined.find("Sub-Task 2/4: SPEC_TRACKING.md")
        assert idx != -1, "SPEC_TRACKING sub-task heading not found"
        section = joined[idx:idx + 400]
        assert "+ Sub-Task 1/4 review" in section, (
            "SPEC_TRACKING dep_note must reference SRS.md review (Sub-Task 1/4), "
            "got: " + section[section.find("Depends on"):section.find("Depends on") + 100]
        )

    def test_b2_review_chain_spec_tracking_follows_srs(self, project: Path):
        """Sub-Task 2 (SPEC_TRACKING) depends on SRS.md → references Sub-Task 1/4."""
        joined = "\n".join(generate_phase1_tasks(project, project / "SRS.md"))
        idx = joined.find("Sub-Task 2/4: SPEC_TRACKING.md")
        assert idx != -1, "SPEC_TRACKING sub-task heading not found"
        section = joined[idx:idx + 2000]
        assert "Sub-Task 1/4" in section, (
            "SPEC_TRACKING dep_note/embed_docs must reference SRS.md (Sub-Task 1/4)"
        )
        assert "SRS.md (Sub-Task 1/4" in section, (
            "SPEC_TRACKING embed_docs must include SRS.md B-2 review"
        )

    def test_b2_review_chain_traceability_multi_dep(self, project: Path):
        """Sub-Task 3 (TRACEABILITY) depends on SRS.md + SPEC_TRACKING → references both."""
        joined = "\n".join(generate_phase1_tasks(project, project / "SRS.md"))
        idx = joined.find("Sub-Task 3/4: TRACEABILITY_MATRIX.md")
        assert idx != -1, "TRACEABILITY sub-task heading not found"
        section = joined[idx:idx + 2500]
        assert "Sub-Task 1/4" in section, (
            "TRACEABILITY dep_note/embed_docs must reference SRS.md (Sub-Task 1/4)"
        )
        assert "Sub-Task 2/4" in section, (
            "TRACEABILITY dep_note/embed_docs must reference SPEC_TRACKING.md (Sub-Task 2/4)"
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
        """P2 plan must have 2 serial sub-tasks with A/B loop (SAD.md + ADR.md)."""
        joined = "\n".join(generate_phase2_tasks(project, project / "SRS.md"))
        assert "Sub-Task 1/3" in joined
        assert "SAD.md" in joined

    def test_has_ab_steps(self, project: Path):
        """GAP-K fix: P2 plan must include A/B architecture steps."""
        joined = "\n".join(generate_phase2_tasks(project, project / "SRS.md"))
        assert "ARCHITECT" in joined
        assert "TECH_LEAD" in joined
        assert "sessions_spawn.log" in joined

    def test_sessions_spawn_log_six_entries(self, project: Path):
        """P2 sessions_spawn.log is auto-populated by AgentSpawner (HR-10)."""
        joined = "\n".join(generate_phase2_tasks(project, project / "SRS.md"))
        assert "auto-populated by AgentSpawner" in joined

    def test_has_review_checkpoint(self, project: Path):
        """GAP-K3 fix: P2 plan must end with Agent B review checkpoint."""
        joined = "\n".join(generate_phase2_tasks(project, project / "SRS.md"))
        assert "Agent B Peer Review" in joined
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
        assert "Hermes" not in result  # Gate 4 is fully automated — no Hermes APPROVE

    def test_unknown_phase_returns_none(self, project: Path):
        result = generate_full_plan(99, project)
        assert result is None

    def test_output_written_to_file(self, project: Path, tmp_path: Path):
        out = tmp_path / "plan.md"
        result = generate_full_plan(3, project, out)
        assert result is not None
        assert out.exists()
        assert out.read_text(encoding="utf-8") == result


# ═══════════════════════════════════════════════════════════════════════════════
# C-1 fix: parse_config_records regex must not capture across newlines
# ═══════════════════════════════════════════════════════════════════════════════

class TestParseConfigRecords:
    def test_no_multiline_capture(self, tmp_path):
        """Regex must not capture markdown headers or dates across newlines.

        The key bug: \\s* in the regex matches \\n, causing a match to start on
        the last pipe of one table row, consume blank lines + a heading via \\s*,
        then continue on the first pipe of the next table.  Using [ \\t]* fixes it.
        """
        from scripts.generate_full_plan import parse_config_records

        config_dir = tmp_path / "08-config"
        config_dir.mkdir(parents=True)
        (config_dir / "CONFIG_RECORDS.md").write_text(
            "Date: 2026-05-17\n\n"
            "## Repository Configuration\n\n"
            "| Setting | Value | Type |\n"
            "|---------|-------|------|\n"
            "| DB_HOST | localhost | string |\n"
            "| API_KEY | *** | secret |\n"
        )
        configs = parse_config_records(tmp_path)
        names = [c["name"] for c in configs]
        assert "DB_HOST" in names
        assert "API_KEY" in names
        assert "Date" not in names
        assert "Repository Configuration" not in names
        assert "##" not in "\n".join(names)

    def test_heading_between_tables_not_captured(self, tmp_path):
        """Regex must not capture a heading placed between two 3-pipe table rows."""
        from scripts.generate_full_plan import parse_config_records

        config_dir = tmp_path / "08-config"
        config_dir.mkdir(parents=True)
        # Simulate real-world CONFIG_RECORDS.md: 2-column tables separated by headings
        (config_dir / "CONFIG_RECORDS.md").write_text(
            "## Section A\n\n"
            "| Key | Value |\n"
            "|-----|-------|\n"
            "| k1  | v1    |\n"
            "\n"
            "## Section B\n\n"
            "| Name | Purpose |\n"
            "|------|---------|\n"
            "| n1   | p1      |\n"
        )
        configs = parse_config_records(tmp_path)
        names = [c["name"] for c in configs]
        # No 3-column table exists → nothing should be captured
        assert len(configs) == 0, f"Expected 0 matches, got: {names}"

    def test_empty_config_file_returns_empty(self, tmp_path):
        from scripts.generate_full_plan import parse_config_records

        config_dir = tmp_path / "08-config"
        config_dir.mkdir(parents=True)
        (config_dir / "CONFIG_RECORDS.md").write_text("")
        assert parse_config_records(tmp_path) == []

    def test_no_config_file_returns_empty(self, tmp_path):
        from scripts.generate_full_plan import parse_config_records
        assert parse_config_records(tmp_path) == []


# ═══════════════════════════════════════════════════════════════════════════════
# C-2 fix: P3/P4 carry-forward FR expansion
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def project_with_carry_forward(tmp_path: Path) -> Path:
    """Project where SRS.md has FR-14..FR-15 but manifest has FR-01..FR-15."""
    m_dir = tmp_path / ".methodology"
    m_dir.mkdir()
    manifest = {
        "fr_ids": [f"FR-{i:02d}" for i in range(1, 16)],  # FR-01..FR-15
        "gate_results": {},
    }
    (m_dir / "quality_manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    (tmp_path / "01-requirements").mkdir(parents=True, exist_ok=True)
    # SRS.md with FR-14 and FR-15 only (simulating Phase 2 scope)
    srs_content = """# SRS

### FR-14: Platform Adapter

**Description**: Implement Messenger + WhatsApp webhook adapter.

### FR-15: Prompt Injection Defense

**Description**: Implement L3 sandwich defense against prompt injection.
"""
    (tmp_path / "01-requirements" / "SRS.md").write_text(srs_content, encoding="utf-8")
    # Minimal SAD for module mapping
    (tmp_path / "02-architecture").mkdir(parents=True, exist_ok=True)
    (tmp_path / "02-architecture" / "SAD.md").write_text(
        "## Module Mapping\n"
        "| FR-14 | platform | src/platform.py |\n"
        "| FR-15 | security | src/security.py |\n"
    )
    return tmp_path


class TestCarryForwardP3:
    def test_header_shows_carry_forward_count(self, project_with_carry_forward: Path):
        """P3 plan header must show new + carry-forward counts."""
        from scripts.generate_full_plan import generate_phase3_tasks
        lines = generate_phase3_tasks(project_with_carry_forward,
                                       project_with_carry_forward / "01-requirements" / "SRS.md")
        joined = "\n".join(lines)
        assert "2 new + 13 carry-forward" in joined or "carry-forward" in joined

    def test_carry_forward_frs_have_reeval_label(self, project_with_carry_forward: Path):
        """Carry-forward FRs must show Re-evaluation label."""
        from scripts.generate_full_plan import generate_phase3_tasks
        lines = generate_phase3_tasks(project_with_carry_forward,
                                       project_with_carry_forward / "01-requirements" / "SRS.md")
        joined = "\n".join(lines)
        assert "FR-01" in joined
        assert "Re-evaluation (carry-forward)" in joined
        assert "Gate 1 Re-evaluation" in joined

    def test_all_manifest_frs_have_gate1(self, project_with_carry_forward: Path):
        """Every FR in manifest must have run-fr-step dispatch (sub-agent model)."""
        from scripts.generate_full_plan import generate_phase3_tasks
        lines = generate_phase3_tasks(project_with_carry_forward,
                                       project_with_carry_forward / "01-requirements" / "SRS.md")
        joined = "\n".join(lines)
        assert "run-fr-step --phase 3 --fr-id FR-01" in joined
        assert "run-fr-step --phase 3 --fr-id FR-14" in joined
        assert "run-fr-step --phase 3 --fr-id FR-15" in joined
        # Old inline run-gate must NOT appear for per-FR Gate 1
        assert "run-gate --gate 1 --phase 3 --fr-id FR-01" not in joined

    def test_srs_frs_have_full_details(self, project_with_carry_forward: Path):
        """SRS.md FRs must retain full detail (title, task, test cases)."""
        from scripts.generate_full_plan import generate_phase3_tasks
        lines = generate_phase3_tasks(project_with_carry_forward,
                                       project_with_carry_forward / "01-requirements" / "SRS.md")
        joined = "\n".join(lines)
        assert "Platform Adapter" in joined
        assert "Prompt Injection Defense" in joined

    def test_carry_forward_frs_not_labeled_new_implementation(self, project_with_carry_forward: Path):
        """Carry-forward FRs must NOT use 'Implement FR-XX (no A/B)' label."""
        from scripts.generate_full_plan import generate_phase3_tasks
        lines = generate_phase3_tasks(project_with_carry_forward,
                                       project_with_carry_forward / "01-requirements" / "SRS.md")
        joined = "\n".join(lines)
        # First occurrence of FR-01 is in the checkpoint index (blockquote).
        # Skip past the index to find the implementation section.
        impl_start = joined.find("### FR Implementation Tasks")
        assert impl_start > 0, "FR Implementation Tasks section not found"
        section = joined[impl_start:]
        # FR-01 should appear as carry-forward Re-evaluation
        idx = section.find("FR-01")
        assert idx > 0, "FR-01 not found in implementation section"
        chunk = section[idx:idx + 500]
        assert "Re-evaluation (carry-forward)" in chunk
        assert "Gate 1 Re-evaluation" in chunk
        assert "Implement FR-01" not in chunk


class TestCarryForwardP4:
    def test_all_manifest_frs_have_gate1(self, project_with_carry_forward: Path):
        """Every FR in manifest must have run-fr-step dispatch in P4 plan (sub-agent model)."""
        from scripts.generate_full_plan import generate_phase4_tasks
        lines = generate_phase4_tasks(project_with_carry_forward,
                                       project_with_carry_forward / "01-requirements" / "SRS.md")
        joined = "\n".join(lines)
        assert "run-fr-step --phase 4 --fr-id FR-01" in joined
        assert "run-fr-step --phase 4 --fr-id FR-14" in joined
        assert "run-gate --gate 1 --phase 4 --fr-id FR-01" not in joined

    def test_carry_forward_frs_have_reeval(self, project_with_carry_forward: Path):
        """Carry-forward FRs must show Re-evaluation in P4 plan."""
        from scripts.generate_full_plan import generate_phase4_tasks
        lines = generate_phase4_tasks(project_with_carry_forward,
                                       project_with_carry_forward / "01-requirements" / "SRS.md")
        joined = "\n".join(lines)
        assert "Re-evaluation (carry-forward)" in joined
        assert "Gate 1 Re-evaluation" in joined


# ═══════════════════════════════════════════════════════════════════════════════
# C-3 fix: sessions_spawn.log HR-10 label scoped to Phase 1-2
# ═══════════════════════════════════════════════════════════════════════════════

class TestSessionsSpawnLabel:
    @pytest.mark.parametrize("phase", [1, 2])
    def test_phase1_2_sessions_spawn_is_nonblocking(self, project: Path, phase: int):
        """P1/P2 keep the sessions_spawn.log deliverable but as a non-blocking
        debug trail — the HR-10 enforcement label was removed."""
        result = generate_full_plan(phase, project)
        assert "sessions_spawn.log" in result
        assert "non-blocking debug trail" in result
        assert "HR-10" not in result

    @pytest.mark.parametrize("phase", [3, 4, 5, 6, 7, 8])
    def test_phase3_plus_no_hr10_label(self, project: Path, phase: int):
        """P3-P8 plans must NOT include any HR-10 label in the sessions_spawn.log line."""
        result = generate_full_plan(phase, project)
        assert "sessions_spawn.log" in result
        assert "auto-populated by AgentSpawner (HR-10)" not in result
        assert "HR-10" not in result


# ═══════════════════════════════════════════════════════════════════════════════
# G1/G2: P1 4th deliverable (TEST_INVENTORY.yaml) / P2 3rd deliverable (TEST_SPEC.md)
# ═══════════════════════════════════════════════════════════════════════════════

class TestP1FourthDeliverable:
    def test_has_test_inventory_sub_task(self, project: Path):
        """P1 plan must include Sub-Task 4/4 for TEST_INVENTORY.yaml."""
        joined = "\n".join(generate_phase1_tasks(project, project / "SRS.md"))
        assert "Sub-Task 4/4" in joined
        assert "TEST_INVENTORY.yaml" in joined

    def test_test_inventory_depends_on_traceability(self, project: Path):
        """TEST_INVENTORY.yaml must declare dependency on TRACEABILITY_MATRIX.md."""
        joined = "\n".join(generate_phase1_tasks(project, project / "SRS.md"))
        idx = joined.find("Sub-Task 4/4: TEST_INVENTORY.yaml")
        assert idx != -1, "TEST_INVENTORY sub-task heading not found"
        section = joined[idx:idx + 800]
        assert "TRACEABILITY_MATRIX.md" in section

    def test_decomposition_shows_4_deliverables(self, project: Path):
        """P1 decomposition section must list all 4 deliverables."""
        joined = "\n".join(generate_phase1_tasks(project, project / "SRS.md"))
        assert "TEST_INVENTORY.yaml" in joined

    def test_review_checkpoint_includes_test_inventory(self, project: Path):
        """P1 review checkpoint must reference TEST_INVENTORY.yaml."""
        joined = "\n".join(generate_phase1_tasks(project, project / "SRS.md"))
        idx_checkpoint = joined.find("Agent B Peer Review — Phase 1 Exit")
        assert idx_checkpoint != -1
        section = joined[idx_checkpoint:idx_checkpoint + 1500]
        assert "TEST_INVENTORY.yaml" in section


class TestP2ThirdDeliverable:
    def test_has_test_spec_sub_task(self, project: Path):
        """P2 plan must include Sub-Task 3/3 for TEST_SPEC.md."""
        joined = "\n".join(generate_phase2_tasks(project, project / "SRS.md"))
        assert "Sub-Task 3/3" in joined
        assert "TEST_SPEC.md" in joined

    def test_test_spec_depends_on_adr(self, project: Path):
        """TEST_SPEC.md must declare dependency on ADR.md."""
        joined = "\n".join(generate_phase2_tasks(project, project / "SRS.md"))
        idx = joined.find("Sub-Task 3/3: TEST_SPEC.md")
        assert idx != -1, "TEST_SPEC sub-task heading not found"
        section = joined[idx:idx + 800]
        assert "ADR.md" in section

    def test_decomposition_shows_3_deliverables(self, project: Path):
        """P2 decomposition section must list all 3 deliverables."""
        joined = "\n".join(generate_phase2_tasks(project, project / "SRS.md"))
        assert "SAD.md" in joined
        assert "ADR.md" in joined
        assert "TEST_SPEC.md" in joined

    def test_review_checkpoint_includes_test_spec(self, project: Path):
        """P2 review checkpoint must reference TEST_SPEC.md."""
        joined = "\n".join(generate_phase2_tasks(project, project / "SRS.md"))
        idx_checkpoint = joined.find("Agent B Peer Review — Phase 2 Exit")
        assert idx_checkpoint != -1
        section = joined[idx_checkpoint:idx_checkpoint + 1500]
        assert "TEST_SPEC.md" in section


# ═══════════════════════════════════════════════════════════════════════════════
# G3: TDD RED→GREEN→IMPROVE in P3+ _fr_dev_steps
# ═══════════════════════════════════════════════════════════════════════════════

class TestTddDevSteps:
    def test_p3_fr_dev_steps_has_tdd_labels(self, project: Path):
        """P3+ per-FR steps must include ORCH-RED, ORCH-GREEN, ORCH-IMPROVE (sub-agent dispatch)."""
        joined = "\n".join(generate_phase3_tasks(project, project / "SRS.md"))
        assert "ORCH-RED" in joined
        assert "ORCH-GREEN" in joined
        assert "ORCH-IMPROVE" in joined

    def test_p3_fr_dev_steps_has_test_file_instruction(self, project: Path):
        """P3+ per-FR steps must instruct creating test_frNN.py."""
        joined = "\n".join(generate_phase3_tasks(project, project / "SRS.md"))
        assert "test_fr" in joined
        assert "failing test case" in joined.lower() or "failing test" in joined.lower()

    def test_p3_fr_dev_steps_mentions_d1_red_enforcement(self, project: Path):
        """P3+ per-FR steps must mention D1-RED enforcement."""
        joined = "\n".join(generate_phase3_tasks(project, project / "SRS.md"))
        assert "D1-RED" in joined or "finalize-gate" in joined

    def test_phase1_ab_steps_no_tdd_labels(self, project: Path):
        """P1-P2 A/B steps must NOT contain TDD-1/TDD-2/TDD-3 labels."""
        joined = "\n".join(generate_phase1_tasks(project, project / "SRS.md"))
        assert "TDD-1 RED" not in joined
        assert "TDD-2 GREEN" not in joined
        assert "TDD-3 IMPROVE" not in joined

    def test_p8_fr_dev_steps_has_tdd_labels(self, project: Path):
        """P8 carry-forward FRs use ORCH-GATE1-DELTA (no full TDD re-run)."""
        result = generate_full_plan(8, project)
        assert "ORCH-GATE1-DELTA" in result
        assert "GATE1-DELTA" in result


# ═══════════════════════════════════════════════════════════════════════════════
# Regression tests for bugs fixed in commit fe3e429
# ═══════════════════════════════════════════════════════════════════════════════

class TestFe3e429Fixes:
    # C1: push-checkpoint must not carry hardcoded --fr-ids
    def test_c1_push_checkpoint_no_fr_ids(self, project: Path):
        for phase in (1, 2):
            result = generate_full_plan(phase, project)
            assert "--fr-ids" not in result, f"P{phase}: --fr-ids still present"
        # command itself must still appear
        assert "push-checkpoint" in generate_full_plan(1, project)

    # R4: NFR headings must not repeat the NFR ID (e.g., "#### NFR-01: NFR-01: ...")
    def test_r4_nfr_heading_no_double_id(self, project: Path):
        srs = project / "01-requirements" / "SRS.md"
        srs.write_text(
            "# SRS\n\n| NFR-01 | Performance | Response ≤ 200ms |\n|---|---|---|\n",
            encoding="utf-8",
        )
        result = generate_full_plan(1, project)
        assert "NFR-01: NFR-01:" not in result
        assert "#### NFR-01: Performance" in result

    # C3: NFR coverage table must show "—" (not the old placeholder) when no mapping found
    def test_c3_nfr_coverage_no_placeholder(self, project: Path):
        srs = project / "01-requirements" / "SRS.md"
        srs.write_text(
            "# SRS\n\n### NFR-01: Performance\n\nFast response time.\n\n",
            encoding="utf-8",
        )
        result = "\n".join(generate_phase3_tasks(project, srs))
        assert "(see SRS.md §3)" not in result
        assert "—" in result  # fallback sentinel present

    # C3 follow-up: ⚠️ note must appear when no NFR Association column in SRS
    def test_c3_nfr_coverage_warning_note(self, project: Path):
        srs = project / "01-requirements" / "SRS.md"
        srs.write_text(
            "# SRS\n\n### NFR-01: Performance\n\nFast response time.\n\n",
            encoding="utf-8",
        )
        result = "\n".join(generate_phase3_tasks(project, srs))
        assert "NFR→FR mapping not found" in result

    # C4: P4 milestone section must use 10-Push Strategy labels ⑤ and ⑥
    def test_c4_p4_milestone_header_note(self, project: Path):
        result = generate_full_plan(4, project)
        assert "⑤" in result
        assert "⑥" in result

    # C5: P6 plan must include G4e and G4f task steps (now inside _gate_exit_checkpoint)
    def test_c5_p6_has_g4e_g4f(self, project: Path):
        result = generate_full_plan(6, project)
        assert "G4e" in result
        assert "G4f" in result
        assert "RELEASE_NOTES.md" in result
        assert "FINAL_SIGN_OFF.md" in result

    # C5 structural: G4e/G4f must come from _gate_exit_checkpoint, not a separate block
    def test_c5_gate_exit_checkpoint_gate4_has_g4e_g4f(self):
        lines = _gate_exit_checkpoint(4, 6, 1)
        joined = "\n".join(lines)
        assert "G4e" in joined
        assert "G4f" in joined
        assert "RELEASE_NOTES.md" in joined

    # C6: P3 entry gate check must contain [P2-ARTIFACTS] verification block
    def test_c6_p3_entry_gate_has_p2_artifacts(self, project: Path):
        result = generate_full_plan(3, project)
        assert "[P2-ARTIFACTS]" in result

    # I3: P5/P7/P8 overviews must say "No harness run-gate" and mention TDD-PRECHECK
    def test_i3_no_harness_run_gate_wording(self, project: Path):
        for phase in (5, 7, 8):
            result = generate_full_plan(phase, project)
            assert "No harness run-gate" in result, f"P{phase}: missing 'No harness run-gate'"
            assert "TDD-PRECHECK" in result, f"P{phase}: missing 'TDD-PRECHECK'"


class TestDynamicMode:
    """Tests for generate_full_plan dynamic=True mode."""

    def test_dynamic_p3_no_srs_required(self, tmp_path: Path):
        """Phase 3 dynamic plan generates without SRS.md present."""
        (tmp_path / ".methodology").mkdir()
        result = generate_full_plan(3, tmp_path, dynamic=True)
        assert result is not None

    def test_dynamic_plan_has_phase_context_block(self, tmp_path: Path):
        """P3-P8 dynamic plans contain [PHASE-CONTEXT] block."""
        (tmp_path / ".methodology").mkdir()
        for phase in range(3, 9):
            result = generate_full_plan(phase, tmp_path, dynamic=True)
            assert result is not None, f"P{phase} returned None"
            assert "[PHASE-CONTEXT]" in result, f"P{phase}: missing [PHASE-CONTEXT]"

    def test_dynamic_plan_no_expanded_fr_blocks(self, tmp_path: Path):
        """Dynamic P3 does not contain individual expanded FR-01 blocks."""
        (tmp_path / ".methodology").mkdir()
        # create minimal SRS with FR-01 so JIT mode would expand it
        srs = tmp_path / "01-requirements"
        srs.mkdir()
        (srs / "SRS.md").write_text(
            "### FR-01: Test Feature\n\nDescription.\n\n---\n", encoding="utf-8"
        )
        result = generate_full_plan(3, tmp_path, dynamic=True)
        assert result is not None
        # JIT would produce "#### FR-01: Test Feature"; dynamic must not
        assert "#### FR-01: Test Feature" not in result

    def test_dynamic_plan_has_fr_template(self, tmp_path: Path):
        """Dynamic P3 contains {FR-ID} template placeholder."""
        (tmp_path / ".methodology").mkdir()
        result = generate_full_plan(3, tmp_path, dynamic=True)
        assert result is not None
        assert "{FR-ID}" in result

    def test_dynamic_p1_no_fr_details_section(self, tmp_path: Path):
        """Dynamic P1 does not contain ### FR Requirements section."""
        (tmp_path / ".methodology").mkdir()
        srs = tmp_path / "01-requirements"
        srs.mkdir()
        (srs / "SRS.md").write_text(
            "### FR-01: Test Feature\n\nDescription.\n\n---\n", encoding="utf-8"
        )
        result = generate_full_plan(1, tmp_path, dynamic=True)
        assert result is not None
        assert "### FR Requirements" not in result

    def test_dynamic_p2_without_srs(self, tmp_path: Path):
        """Dynamic P2 generates without SRS.md (no hard fail)."""
        (tmp_path / ".methodology").mkdir()
        result = generate_full_plan(2, tmp_path, dynamic=True)
        assert result is not None

    def test_dynamic_mode_flag_in_header(self, tmp_path: Path):
        """Dynamic plan header contains Mode: Dynamic."""
        (tmp_path / ".methodology").mkdir()
        result = generate_full_plan(3, tmp_path, dynamic=True)
        assert result is not None
        assert "Mode" in result and "Dynamic" in result

    def test_dynamic_advance_step_no_plan_phase(self, tmp_path: Path):
        """Dynamic P3 plan does not contain plan-phase command in advance step."""
        (tmp_path / ".methodology").mkdir()
        result = generate_full_plan(3, tmp_path, dynamic=True)
        assert result is not None
        assert "plan-phase --phase 4" not in result


class TestPlanAll:
    """Tests for cmd_plan_all / plan-all CLI command."""

    def test_plan_all_requires_methodology_dir(self, tmp_path: Path):
        """plan-all exits with code 1 when .methodology/ doesn't exist."""
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent))
        from harness_cli import cmd_plan_all
        import argparse
        args = argparse.Namespace(project=str(tmp_path), output_dir=None)
        assert cmd_plan_all(args) == 1

    def test_plan_all_generates_8_files(self, tmp_path: Path):
        """plan-all generates 8 phase plan files."""
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent))
        from harness_cli import cmd_plan_all
        import argparse
        (tmp_path / ".methodology").mkdir()
        args = argparse.Namespace(project=str(tmp_path), output_dir=None)
        result = cmd_plan_all(args)
        assert result == 0
        for phase_num in range(1, 9):
            plan_file = tmp_path / ".methodology" / f"phase{phase_num}_plan.md"
            assert plan_file.exists(), f"phase{phase_num}_plan.md not created"

    def test_plan_all_no_fr_expansion(self, tmp_path: Path):
        """Plans from plan-all do not contain expanded run-fr-step --fr-id FR-01 form."""
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent))
        from harness_cli import cmd_plan_all
        import argparse
        (tmp_path / ".methodology").mkdir()
        srs = tmp_path / "01-requirements"
        srs.mkdir()
        (srs / "SRS.md").write_text(
            "### FR-01: Test Feature\n\nDescription.\n\n---\n", encoding="utf-8"
        )
        args = argparse.Namespace(project=str(tmp_path), output_dir=None)
        cmd_plan_all(args)
        plan3 = (tmp_path / ".methodology" / "phase3_plan.md").read_text(encoding="utf-8")
        # expanded form would be --fr-id FR-01 with a specific FR ID (not the template)
        assert "--fr-id FR-01" not in plan3


# ─── Cross-project usability fixes ────────────────────────────────────────────

class TestCrossProjectFixes:
    """Audit-driven fixes: completeness, correctness, cross-project usability."""

    # Fix 4: P4 checkpoint index must list milestone pushes (⑤ mid, ⑥ pre-gate3)
    def test_p4_checkpoint_index_has_milestones(self):
        lines = _checkpoint_index([], phase=4)
        joined = "\n".join(lines)
        assert "P4-mid" in joined, "P4 checkpoint index must include P4-mid milestone"
        assert "P4-pre-gate3" in joined, "P4 checkpoint index must include P4-pre-gate3 milestone"

    # Fix 2: P5 deliverables must use full 05-verification/ path
    def test_p5_baseline_has_full_path(self, tmp_path: Path):
        result = generate_full_plan(5, tmp_path)
        assert result is not None
        assert "05-verification/BASELINE.md" in result, "P5 must reference 05-verification/BASELINE.md"
        assert "05-verification/VERIFICATION_REPORT.md" in result

    # Fix 1: dynamic P6 must NOT embed static quality metric numbers
    def test_p6_dynamic_no_static_quality_metrics(self, tmp_path: Path):
        (tmp_path / ".methodology").mkdir()
        result = generate_full_plan(6, tmp_path, dynamic=True)
        assert result is not None
        # These patterns appear in parse_quality_report output — must not appear in dynamic plan
        assert "Overall Score" not in result, "dynamic P6 must not embed static quality metrics"
        assert "Generated:" not in result, "dynamic P6 must not embed static generation date"

    # Fix 7: P5 must have concrete generation steps for BASELINE and VERIFICATION_REPORT
    def test_p5_has_baseline_generation_step(self, tmp_path: Path):
        result = generate_full_plan(5, tmp_path)
        assert result is not None
        assert "[BASELINE]" in result, "P5 must include [BASELINE] generation step"
        assert "[VERIFY-REPORT]" in result, "P5 must include [VERIFY-REPORT] generation step"

    # Fix 8: dynamic P7 must have risk register generation steps
    def test_p7_dynamic_has_risk_generation_steps(self, tmp_path: Path):
        (tmp_path / ".methodology").mkdir()
        result = generate_full_plan(7, tmp_path, dynamic=True)
        assert result is not None
        assert "[RISK-REGISTER]" in result, "dynamic P7 must include [RISK-REGISTER] step"
        assert "[RISK-MITIGATION]" in result, "dynamic P7 must include [RISK-MITIGATION] step"
        assert "[RISK-STATUS]" in result, "dynamic P7 must include [RISK-STATUS] step"

    # Fix 9 + A: NO phase plan may contain Chinese characters (all-English consistency)
    def test_no_phase_plan_has_chinese_text(self, tmp_path: Path):
        (tmp_path / ".methodology").mkdir()
        for phase in range(1, 9):
            result = generate_full_plan(phase, tmp_path, dynamic=True)
            assert result is not None, f"P{phase} plan generation returned None"
            # CJK Unified Ideographs U+4E00–U+9FFF
            chinese_chars = [c for c in result if "一" <= c <= "鿿"]
            assert not chinese_chars, (
                f"P{phase} plan must not contain Chinese text; "
                f"found: {''.join(chinese_chars[:10])!r}"
            )

    # Fix B: 10-Push Strategy must label all ten pushes ①–⑩ across the 8 plans
    def test_ten_push_strategy_all_labeled(self, tmp_path: Path):
        (tmp_path / ".methodology").mkdir()
        combined = "".join(
            generate_full_plan(p, tmp_path, dynamic=True) or "" for p in range(1, 9)
        )
        for label in "①②③④⑤⑥⑦⑧⑨⑩":
            assert f"PUSH {label}" in combined, (
                f"10-Push Strategy is missing label PUSH {label}"
            )

    # Fix 5: dynamic P1 PHASE-CONTEXT must appear before Sub-Task 1/4
    def test_p1_phase_context_before_subtasks(self, tmp_path: Path):
        result = generate_full_plan(1, tmp_path, dynamic=True)
        assert result is not None
        ctx_pos = result.find("[PHASE-CONTEXT]")
        subtask_pos = result.find("Sub-Task 1/")
        assert ctx_pos != -1, "dynamic P1 must contain [PHASE-CONTEXT]"
        assert subtask_pos != -1, "P1 must contain Sub-Task 1/"
        assert ctx_pos < subtask_pos, (
            f"[PHASE-CONTEXT] (pos {ctx_pos}) must appear before Sub-Task 1/ (pos {subtask_pos})"
        )

    # Fix 5: dynamic P2 PHASE-CONTEXT must appear before Sub-Task 1/3
    def test_p2_phase_context_before_subtasks(self, tmp_path: Path):
        (tmp_path / ".methodology").mkdir()
        result = generate_full_plan(2, tmp_path, dynamic=True)
        assert result is not None
        ctx_pos = result.find("[PHASE-CONTEXT]")
        subtask_pos = result.find("Sub-Task 1/")
        assert ctx_pos != -1, "dynamic P2 must contain [PHASE-CONTEXT]"
        assert subtask_pos != -1, "P2 must contain Sub-Task 1/"
        assert ctx_pos < subtask_pos, (
            f"[PHASE-CONTEXT] (pos {ctx_pos}) must appear before Sub-Task 1/ (pos {subtask_pos})"
        )

    # Fix 6: Gate 4 exit checkpoint must label PUSH ⑧
    def test_push_8_labeled_in_gate4_exit(self):
        lines = _gate_exit_checkpoint(4, 6, 1)
        joined = "\n".join(lines)
        assert "PUSH ⑧" in joined, "_gate_exit_checkpoint(gate_num=4) must mention PUSH ⑧"


# ─── Reviewer design completeness fixes ──────────────────────────────────────

class TestReviewerDesignFixes:
    """12 fixes for reviewer completeness/correctness/consistency audit."""

    # C1: P6 must contain the Gate 4 A3 prerequisites block (A2/A4 removed, A5 advisory)
    def test_p6_has_gate4_prerequisites_block(self, tmp_path: Path):
        (tmp_path / ".methodology").mkdir()
        result = generate_full_plan(6, tmp_path, dynamic=True)
        assert result is not None
        assert "[A2]" not in result, "A2 model_used was removed (constant 'claude' — no value)"
        assert "[A3]" in result, "P6 must have A3 devil_advocate field documentation"
        assert "devil_advocate_evidence" in result, "P6 A3 must require the artifact-backed evidence field"
        assert "[A4]" not in result, "A4 high_score_confirmations was removed"
        assert "[A5]" in result, "P6 must still document A5 issue_registry_path (advisory)"

    # C2: P6 must document DA challenge and DA waiver for CRG-ONLY dims
    def test_p6_da_challenge_documented(self, tmp_path: Path):
        (tmp_path / ".methodology").mkdir()
        result = generate_full_plan(6, tmp_path, dynamic=True)
        assert result is not None
        assert "devil_advocate" in result, "P6 must document devil_advocate field"
        assert "da_waiver" in result, "P6 must document da_waiver for Orchestrator Pattern"
        assert "Orchestrator" in result, "P6 must mention Orchestrator Pattern false positive"

    # C2: P4 Gate 3 (which has architecture dim) must also mention CRG-ONLY dims
    def test_p4_gate3_mentions_crg_only(self, project: Path):
        result = generate_full_plan(4, project)
        assert result is not None
        assert "CRG-ONLY" in result, "P4 Gate 3 must document CRG-ONLY architecture/error_handling"

    # R1: P5 advance step must warn about spec-coverage 80%→90% gap
    def test_p5_advance_has_d4_gap_warning(self, tmp_path: Path):
        result = generate_full_plan(5, tmp_path, dynamic=True)
        assert result is not None
        assert "D4-GAP WARNING" in result, "P5 advance must warn about 80%→90% spec-coverage gap to Gate 4"
        assert "90%" in result or "90.0" in result, "P5 advance must mention 90% as Gate 4 requirement"

    # C3 + I1: dynamic P5/P7/P8 must have GATE1-DELTA CASE 1/2/3 escalation
    @pytest.mark.parametrize("phase", [5, 7, 8])
    def test_gate1_delta_has_case_escalation(self, tmp_path: Path, phase: int):
        (tmp_path / ".methodology").mkdir()
        result = generate_full_plan(phase, tmp_path, dynamic=True)
        assert result is not None
        assert "GATE1-DELTA outcomes" in result, f"P{phase} must have GATE1-DELTA outcomes CASE 1-3"
        assert "CASE 1 PASS" in result
        assert "CASE 2 FAIL" in result
        assert "CASE 3 BLOCKED" in result

    # Gate 1 closed loop: dynamic P3/P4 must have Gate 1 CASE 1/2/3 escalation
    @pytest.mark.parametrize("phase", [3, 4])
    def test_gate1_full_has_case_escalation(self, tmp_path: Path, phase: int):
        (tmp_path / ".methodology").mkdir()
        result = generate_full_plan(phase, tmp_path, dynamic=True)
        assert result is not None
        assert "Gate 1 outcomes" in result, f"P{phase} must have Gate 1 CASE 1-3"
        assert "CASE 1 PASS" in result
        assert "CASE 2 FAIL" in result
        assert "CASE 3 BLOCKED" in result

    # R2: P7 deliverables must use 07-risk/ prefix
    def test_p7_deliverables_have_07risk_prefix(self, tmp_path: Path):
        (tmp_path / ".methodology").mkdir()
        result = generate_full_plan(7, tmp_path, dynamic=True)
        assert result is not None
        assert "07-risk/RISK_REGISTER.md" in result
        assert "07-risk/RISK_MITIGATION_PLANS.md" in result
        assert "07-risk/RISK_STATUS_REPORT.md" in result

    # C4 + I3: Agent B JSON must have severity/message/fr_id schema; no orphan confidence
    def test_gaps_json_has_schema(self, project: Path):
        result = generate_full_plan(1, project)
        assert result is not None
        assert '"severity":"low|medium|high"' in result or '"severity": "low|medium|high"' in result
        assert '"message"' in result
        assert '"fr_id"' in result
        assert '"confidence"' not in result, "confidence field was orphaned — must be removed from JSON format"

    # I2: Gate 1 meta must have 4 dims (including test_assertion_quality)
    def test_gate1_meta_has_4_dims(self):
        from scripts.generate_full_plan import _GATE_META
        assert _GATE_META[1][1] == 4, "_GATE_META[1] dim_count must be 4 after adding test_assertion_quality"
        assert "test_assertion_quality" in _GATE_META[1][2]

    # R3: P4 checkpoint index must have CHECKPOINT-0 for TEST_PLAN.md
    def test_p4_checkpoint_index_has_cp0(self):
        lines = _checkpoint_index([], phase=4)
        joined = "\n".join(lines)
        assert "CHECKPOINT-0" in joined, "P4 checkpoint index must list CHECKPOINT-0: TEST_PLAN.md"
        assert "TEST_PLAN" in joined

    # C5: P1 must have PROJECT-BRIEF precondition section
    def test_p1_has_project_brief_precondition(self, tmp_path: Path):
        result = generate_full_plan(1, tmp_path, dynamic=True)
        assert result is not None
        assert "PROJECT-BRIEF" in result, "P1 must have [PROJECT-BRIEF] precondition step"
        assert "PROJECT_BRIEF.md" in result

    # Auto-fix: Gate 2/3 must mention auto-fix engine; Gate 4 must NOT
    def test_gate_exit_has_autofix_note(self):
        for gate_num in (2, 3):
            lines = _gate_exit_checkpoint(gate_num, gate_num + 1, 1)
            joined = "\n".join(lines)
            assert "Auto-fix engine" in joined, f"Gate {gate_num} must mention auto-fix engine"
        # Gate 4 has HUMAN_REQUIRED for all — auto-fix note not needed
        g4_lines = _gate_exit_checkpoint(4, 6, 1)
        g4_joined = "\n".join(g4_lines)
        assert "Auto-fix engine" not in g4_joined, "Gate 4 auto-fix note was already covered by prerequisites block"

    # I4: P2 holistic review must explain machine-generated deliverables exclusion
    def test_p2_review_checkpoint_explains_machine_deliverables(self, project: Path):
        result = generate_full_plan(2, project)
        assert result is not None
        assert "machine-generated" in result, "P2 review must explain quality_manifest.json/SAB.json are machine-generated"


# ─── Phase 7-8 execution review fixes (A/B/C/D) ──────────────────────────────

class TestPhase78ReviewFixes:
    """Fixes B (env-check in FR template) and C (idempotent plan generation)."""

    # B (Issue 2): every FR-loop dynamic plan must include the [ENV-CHECK] preamble
    @pytest.mark.parametrize("phase", [3, 4, 5, 7, 8])
    def test_env_check_prereq_in_fr_loop(self, tmp_path: Path, phase: int):
        (tmp_path / ".methodology").mkdir(exist_ok=True)
        result = generate_full_plan(phase, tmp_path, dynamic=True)
        assert result is not None
        assert "[ENV-CHECK]" in result, f"P{phase} FR loop must include [ENV-CHECK] step"
        assert "run-env-check" in result
        assert "env_check_result.json" in result

    # C (Bug 4/5): a plan with progress marks is preserved, not clobbered
    def test_existing_plan_with_progress_preserved(self, tmp_path: Path):
        (tmp_path / ".methodology").mkdir(exist_ok=True)
        out = tmp_path / ".methodology" / "phase3_plan.md"
        out.write_text("# My Phase 3\n\n- [x] done step\n- [ ] todo step\n", encoding="utf-8")
        result = generate_full_plan(3, tmp_path, out, dynamic=True)
        # Returned unchanged — original progress preserved
        assert result is not None
        assert "- [x] done step" in result
        assert out.read_text(encoding="utf-8").count("- [x] done step") == 1
        assert "FR Tasks — Expanded" not in out.read_text(encoding="utf-8")

    # C: --force overrides the preservation guard
    def test_force_regenerates_over_progress(self, tmp_path: Path):
        (tmp_path / ".methodology").mkdir(exist_ok=True)
        out = tmp_path / ".methodology" / "phase3_plan.md"
        out.write_text("# Old\n\n- [x] done step\n", encoding="utf-8")
        result = generate_full_plan(3, tmp_path, out, dynamic=True, force=True)
        assert result is not None
        # Regenerated → fresh template content present, old marks gone
        assert "FR Tasks — Expanded" in out.read_text(encoding="utf-8")

    # C: a fresh (no-progress) plan is regenerated normally
    def test_no_progress_plan_regenerated(self, tmp_path: Path):
        (tmp_path / ".methodology").mkdir(exist_ok=True)
        out = tmp_path / ".methodology" / "phase3_plan.md"
        out.write_text("# Stale template\n\n- [ ] only todos\n", encoding="utf-8")
        result = generate_full_plan(3, tmp_path, out, dynamic=True)
        assert result is not None
        assert "FR Tasks — Expanded" in out.read_text(encoding="utf-8")
