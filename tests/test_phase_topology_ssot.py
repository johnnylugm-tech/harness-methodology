"""Anchor tests for core/phase_topology.py — the phase/gate topology SSOT.

Two jobs:
1. Pin the registry itself to the values the pipeline was designed around
   (per-FR Gate 1 membership, entry/exit gate mapping, prerequisite chain) —
   the P5 "revert incomplete removal" incident (5e18f58) is exactly what
   these pins prevent.
2. Anchor every remaining mirror of topology facts — payload maps that keep
   their own per-phase data (prompts, configs, artifact lists) and files that
   cannot import Python (JSON schema). When a phase is added or removed,
   each stale mirror fails HERE by name instead of crashing in production
   the way the P8→9 handover did.
"""

import json
from pathlib import Path

from core.phase_topology import (
    ADVANCE_GATE1_CHECK_PHASES,
    ENTRY_GATE_MAP,
    EXIT_GATE_MAP,
    MAX_PHASE,
    PER_FR_GATE1_PHASES,
    PHASE_DIRS,
    PHASE_PREREQUISITES,
    PHASES,
    VALID_PHASES,
    phase_name,
)

REPO = Path(__file__).resolve().parent.parent
ALL_PHASES = set(VALID_PHASES)


class TestRegistrySelfConsistency:
    def test_phases_are_contiguous_from_1(self):
        assert sorted(PHASES) == list(range(1, MAX_PHASE + 1))

    def test_dir_prefix_matches_phase_number(self):
        for p, spec in PHASES.items():
            assert spec.dir.startswith(f"{p:02d}-"), (p, spec.dir)
            assert spec.num == p

    def test_prerequisite_chain_is_linear(self):
        assert PHASE_PREREQUISITES == {p: p - 1 for p in range(2, MAX_PHASE + 1)}

    def test_known_incident_pins(self):
        """Design-level facts previous drift incidents were about. Changing
        any of these is a deliberate methodology change — update this test
        together with the registry in the same reviewed commit."""
        assert PER_FR_GATE1_PHASES == {3, 4, 5, 7, 8, 9}
        assert ADVANCE_GATE1_CHECK_PHASES == {3, 4, 5, 7, 8}
        assert ENTRY_GATE_MAP == {4: 2, 5: 3, 6: 3, 7: 4, 8: 4, 9: 4}
        assert EXIT_GATE_MAP == {3: 2, 4: 3, 6: 4}

    def test_phase_name_helper(self):
        assert phase_name(9) == "Maintenance"
        assert phase_name(99, default="?") == "?"


class TestPythonMirrors:
    """Module-level structures that keep per-phase payload of their own.
    Their KEY SETS must track the registry; payload stays local."""

    def test_harness_cli_constants(self):
        import harness_cli
        assert harness_cli._PER_FR_GATE1_PHASES == PER_FR_GATE1_PHASES
        assert harness_cli._PHASES_WITH_GATE1_FR_CHECK == ADVANCE_GATE1_CHECK_PHASES
        assert harness_cli._PHASE_EXIT_GATES == EXIT_GATE_MAP
        assert harness_cli._PHASE_DIRS == PHASE_DIRS
        assert set(harness_cli._PHASE_NAMES) == ALL_PHASES

    def test_generate_full_plan_gate1_set(self):
        from scripts.generate_full_plan import _PHASE_GATE1_PHASES
        assert _PHASE_GATE1_PHASES == PER_FR_GATE1_PHASES

    def test_handover_generator(self):
        from harness import handover_generator
        assert set(handover_generator._PHASE_NAMES) == ALL_PHASES

    def test_phase_auditor(self):
        from scripts.phase_auditor import _ENTRY_GATE_MAP, _PHASE_MILESTONES, PHASE_SPEC
        assert _ENTRY_GATE_MAP == ENTRY_GATE_MAP
        assert set(PHASE_SPEC) == ALL_PHASES
        assert set(_PHASE_MILESTONES) <= ALL_PHASES

    def test_bvs_prerequisites(self):
        from constitution.bvs_runner import BVSRunner
        assert BVSRunner.PHASE_PREREQUISITES == PHASE_PREREQUISITES

    def test_phase_config_keys(self):
        from core.quality_gate.phase_config import PHASE_CONFIG
        assert set(PHASE_CONFIG) == ALL_PHASES

    def test_cli_phase_prompts_keys(self):
        from core.cli_phase_prompts import PHASE_PROMPTS
        assert set(PHASE_PROMPTS) == ALL_PHASES

    def test_constitution_phase_configs(self):
        from core.quality_gate.constitution.profile import _phase_configs
        cfg = _phase_configs()
        assert set(cfg) == ALL_PHASES
        for p, phase_cfg in cfg.items():
            assert phase_cfg.per_fr_gate1 == (p in PER_FR_GATE1_PHASES), p
            assert phase_cfg.exit_gate == EXIT_GATE_MAP.get(p), p

    def test_constitution_deliverable_map(self):
        from core.quality_gate.constitution.runner import DELIVERABLE_MAP
        assert set(DELIVERABLE_MAP) <= ALL_PHASES

    def test_project_layout_prop_map_keys(self):
        from core.utils.project_layout import _PHASE_PROP_MAP
        assert set(_PHASE_PROP_MAP) == ALL_PHASES

    def test_drift_detector_artifact_keys(self):
        from detection.drift_detector import DriftDetector
        assert set(DriftDetector.PHASE_ARTIFACTS) == ALL_PHASES


class TestNonPythonMirrors:
    def test_manifest_schema_max_phase(self):
        schema = json.loads(
            (REPO / "schemas" / "quality_manifest.schema.json").read_text(encoding="utf-8")
        )
        prop = schema["properties"]["generated_at_phase"]
        assert prop["minimum"] == 1
        assert prop["maximum"] == MAX_PHASE
