"""Sentinel filename SSOT — templates, probes, and generated-plan prose agree.

Round 3 Station I: commit bea1bb1 fixed BY HAND a drift between the sentinel
filename format produced by core/quality_gate/gate1_evidence.py (the SSOT:
``g{gate}_p{phase}_{key}.flag`` since the v2.13 phase-scoping fix) and the
prose generate_full_plan.py renders into every per-FR phase plan (it still
said ``g1_<fr>.flag``, telling agents to look for files that never exist).
Nothing pinned the prose to the producer, so the drift could silently return.
These tests pin every consumer of the format to the exported
SENTINEL_*_TEMPLATE constants so the next drift fails at birth.
"""

from core.quality_gate.gate1_evidence import (
    SENTINEL_FINALIZED_TEMPLATE,
    SENTINEL_FLAG_TEMPLATE,
    _finalize_sentinel_path,
    _sentinel_path,
    gate1_evidence_exists,
)
from scripts.generate_full_plan import _milestone_push_steps


def test_sentinel_paths_match_templates(tmp_path):
    flag = _sentinel_path(tmp_path, 1, "FR-01", phase=3)
    assert flag.name == SENTINEL_FLAG_TEMPLATE.format(gate=1, phase=3, key="fr01")
    fin = _finalize_sentinel_path(tmp_path, 1, "FR-01", phase=3)
    assert fin.name == SENTINEL_FINALIZED_TEMPLATE.format(gate=1, phase=3, key="fr01")


def test_evidence_probe_accepts_template_named_files(tmp_path):
    """Behavior-level: files named via the templates satisfy the O2 probe."""
    sentinels = tmp_path / ".sessi-work" / "sentinels"
    sentinels.mkdir(parents=True)
    assert gate1_evidence_exists(tmp_path, "FR-01", phase=3) is False

    flag = sentinels / SENTINEL_FLAG_TEMPLATE.format(gate=1, phase=3, key="fr01")
    flag.touch()
    assert gate1_evidence_exists(tmp_path, "FR-01", phase=3) is True

    flag.unlink()
    (sentinels / SENTINEL_FINALIZED_TEMPLATE.format(gate=1, phase=3, key="fr01")).touch()
    assert gate1_evidence_exists(tmp_path, "FR-01", phase=3) is True


def test_plan_prose_renders_sentinel_from_template():
    """bea1bb1 drift guard: the plan-prose filename token IS the SSOT render.

    Phases 3 and 4 are the ones whose plans render a post-gate milestone block
    (exit gates 2 and 3 respectively — see core.phase_topology.EXIT_GATE_MAP);
    that block is where the sentinel path is quoted to the agent.
    """
    for phase, exit_gate in ((3, 2), (4, 3)):
        prose = "\n".join(
            _milestone_push_steps(["FR-01", "FR-02"], phase, post_gate=exit_gate)
        )
        token = SENTINEL_FLAG_TEMPLATE.format(gate=1, phase=phase, key="<fr>")
        assert f".sessi-work/sentinels/{token}" in prose
