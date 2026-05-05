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


class TestPhase4GateInjection:
    def test_has_gate1_per_fr(self, project: Path):
        lines = generate_phase4_tasks(project, project / "SRS.md")
        joined = "\n".join(lines)
        assert "run-gate --gate 1 --phase 4 --fr-id FR-01" in joined

    def test_has_gate3_exit(self, project: Path):
        lines = generate_phase4_tasks(project, project / "SRS.md")
        joined = "\n".join(lines)
        assert "run-gate --gate 3 --phase 4" in joined
        assert "finalize-gate --gate 3 --phase 4" in joined


class TestPhase5GateInjection:
    def test_has_gate1_per_fr(self, project: Path):
        lines = generate_phase5_tasks(project)
        joined = "\n".join(lines)
        assert "run-gate --gate 1 --phase 5 --fr-id FR-01" in joined
        assert "run-gate --gate 1 --phase 5 --fr-id FR-02" in joined

    def test_no_exit_gate(self, project: Path):
        lines = generate_phase5_tasks(project)
        joined = "\n".join(lines)
        # P5 has no phase-exit gate
        assert "run-gate --gate 3 --phase 5" not in joined
        assert "run-gate --gate 2 --phase 5" not in joined


class TestPhase6GateInjection:
    def test_gate4_only_no_fr_loop(self, project: Path):
        lines = generate_phase6_tasks(project)
        joined = "\n".join(lines)
        assert "run-gate --gate 4 --phase 6" in joined
        assert "finalize-gate --gate 4 --phase 6" in joined

    def test_no_per_fr_gate1(self, project: Path):
        lines = generate_phase6_tasks(project)
        joined = "\n".join(lines)
        assert "run-gate --gate 1 --phase 6" not in joined

    def test_hermes_approve_note(self, project: Path):
        lines = generate_phase6_tasks(project)
        joined = "\n".join(lines)
        assert "Hermes" in joined

    def test_single_checkpoint(self, project: Path):
        lines = generate_phase6_tasks(project)
        joined = "\n".join(lines)
        assert "CHECKPOINT-1" in joined
        assert "CHECKPOINT-2" not in joined


class TestPhase7GateInjection:
    def test_has_gate1_per_fr(self, project: Path):
        lines = generate_phase7_tasks(project)
        joined = "\n".join(lines)
        assert "run-gate --gate 1 --phase 7 --fr-id FR-01" in joined

    def test_no_exit_gate(self, project: Path):
        lines = generate_phase7_tasks(project)
        joined = "\n".join(lines)
        assert "run-gate --gate 2 --phase 7" not in joined
        assert "run-gate --gate 3 --phase 7" not in joined


class TestPhase8GateInjection:
    def test_has_gate1_per_fr(self, project: Path):
        lines = generate_phase8_tasks(project)
        joined = "\n".join(lines)
        assert "run-gate --gate 1 --phase 8 --fr-id FR-02" in joined

    def test_no_exit_gate(self, project: Path):
        lines = generate_phase8_tasks(project)
        joined = "\n".join(lines)
        assert "run-gate --gate 4 --phase 8" not in joined


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
