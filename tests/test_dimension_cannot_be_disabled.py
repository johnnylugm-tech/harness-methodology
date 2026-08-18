"""No dimension may be switched off, and none may go missing unnoticed.

Round 60 站0/站2/站4. Three feature flags used to remove a dimension from the
judgement entirely — `mutation_testing`, `crg_architecture` (architecture),
`phase4_llm_review` (adversarial_review). The switch was legitimate on paper
and its use was recorded (Round 39 站2), but it made "this dimension was never
measured" resolve to "the composite is fine": the file that removes a
dimension is committed by the project being judged, and removing one RAISES
the mean.

Measured 2026-08-19 across the eight corpus projects: three carry
`mutation_testing: false`; none disables the other two. `f4be095` then added
a prompt rule teaching the Gate 2/3/4 orchestrator how to behave around a
disabled dimension — including the sentence "The flag was flipped on purpose
(e.g. to sidestep a wall-time budget)", a motive the framework has no field to
record and therefore invented.

The ruling is that the state itself should not exist. A dimension the gate
declares is measured, or the gate blocks and the run routes to repair; a tool
that cannot run is an INFRA fact (Round 32 站4), never a quiet subtraction
from the denominator.

The second half is the hole the first half was covering. `_all_dims_pass`
iterates the dimensions the agent REPORTED; `_cfg_dims` is read only to build
`_s4_verifiable`. Nothing compares the two sets, so a dimension the gate
config declares and the result omits entirely is not a failure — it is
invisible. Measured over 8 projects × 4 gate results: 10 omissions, of which
5 are historical (the dimension entered the YAML after that result was
written), 3 are the mutation flag, and **2 are neither** — taskq (2026-07-27)
and taskq-plus (2026-08-01) both published a Gate 1 result with no
`architecture_constraints` entry, a dimension in `gate1_per_fr.yaml` since
2026-06-22.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = [pytest.mark.core]


# ── the switch is gone, and a config still asking for it is refused ─────────

def test_no_dimension_maps_to_a_feature_flag():
    """The mechanism, not just its uses: nothing may re-derive the mapping."""
    import core.harness_config as hc

    assert not hasattr(hc, "_DIM_TO_FEATURE"), (
        "a dimension→flag mapping is a way to remove a dimension from the "
        "judgement; the ruling is that no such way exists"
    )


def test_a_retired_flag_set_to_false_is_named():
    """The predicate is pure so the block and its test share no seam."""
    from core.harness_config import retired_disabling_keys

    assert retired_disabling_keys({"mutation_testing": False}) == ["mutation_testing"]
    assert retired_disabling_keys(
        {"crg_architecture": False, "phase4_llm_review": False}
    ) == ["crg_architecture", "phase4_llm_review"]
    # `true` asks for nothing that is not already the rule — the existing
    # unknown-key WARN covers a stale key, and blocking on it would refuse a
    # config that agrees with us.
    assert retired_disabling_keys({"mutation_testing": True}) == []
    assert retired_disabling_keys({}) == []


def test_run_gate_blocks_a_project_that_still_disables_a_dimension(tmp_path, capsys):
    """Ahead of the tool check: the config is wrong before anything is missing."""
    import argparse

    from cli.exit_codes import EX_RETIRED_FEATURE_FLAG
    from cli.gate_cmds import _cmd_run_gate_impl

    meth = tmp_path / ".methodology"
    meth.mkdir()
    (meth / "harness_config.json").write_text(
        json.dumps({"version": 1, "features": {"mutation_testing": False}}),
        encoding="utf-8",
    )
    (meth / "quality_manifest.json").write_text(
        json.dumps({"fr_ids": []}), encoding="utf-8")

    args = argparse.Namespace(
        gate=2, phase=3, project=str(tmp_path), fr_id=None, delta=False,
        auto_amend_sab=False,
    )
    rc = _cmd_run_gate_impl(args)

    assert rc == EX_RETIRED_FEATURE_FLAG
    out = capsys.readouterr()
    assert "mutation_testing" in (out.out + out.err), (
        "the block must name the key the project has to remove"
    )


# ── a declared dimension that never came back ──────────────────────────────

def test_absent_declared_dimensions_names_what_was_not_reported():
    from harness.harness_bridge import absent_declared_dimensions

    declared = ["linting", "type_safety", "architecture_constraints"]
    assert absent_declared_dimensions(declared, ["linting", "type_safety"]) == [
        "architecture_constraints"
    ]
    assert absent_declared_dimensions(declared, declared) == []
    # An extra dimension the agent volunteered is not this check's business.
    assert absent_declared_dimensions(["linting"], ["linting", "security"]) == []


def _gate_yaml(tmp_path: Path, gate: int, dims: list[dict], monkeypatch) -> None:
    """Same shape as tests/test_verdict_evidence_survives.py's — one idiom."""
    import yaml

    import core.quality_gate.gate_thresholds as _gt
    cfg_path = tmp_path / f"gate{gate}_cfg.yaml"
    cfg_path.write_text(yaml.dump({"gate": gate, "dimensions": dims}))
    monkeypatch.setattr(_gt, "gate_config_path", lambda g: cfg_path)


def test_finalize_blocks_when_a_declared_dimension_is_missing(tmp_path, monkeypatch):
    """The taskq / taskq-plus Gate 1 shape, replayed and refused."""
    from core.quality_gate.constitution.profile import DimensionConfig, GateConfig
    from harness.harness_bridge import GateBlockedError, GateContext, HarnessBridge

    _gate_yaml(tmp_path, 2, [
        {"name": "linting", "threshold": 90},
        {"name": "type_safety", "threshold": 85},
    ], monkeypatch)

    work = tmp_path / ".sessi-work"
    work.mkdir(parents=True)
    (work / "gate2_result.json").write_text(json.dumps({
        "overall_score": 95.0, "meets_target": True, "quality_complete": True,
        "open_critical_count": 0, "open_high_count": 0,
        "breakdown": {"linting": {"score": 100.0, "threshold": 90}},
    }), encoding="utf-8")

    ssi = Path(__file__).parent.parent / "harness" / "ssi"
    ctx = GateContext(
        gate_num=2,
        config=GateConfig(
            gate_num=2, score_gate=80.0, max_rounds=3,
            dimensions=[
                DimensionConfig(name="linting", threshold=90.0),
                DimensionConfig(name="type_safety", threshold=85.0),
            ],
        ),
        project_root=str(tmp_path), phase=3, fr_id=None,
        ssi_scripts_dir=str(ssi / "scripts"),
        ssi_prompts_dir=str(ssi / "prompts"),
        ssi_schemas_dir=str(ssi / "schemas"),
        work_dir=str(work),
    )

    with pytest.raises(GateBlockedError) as excinfo:
        HarnessBridge().finalize_gate(ctx)

    absent = excinfo.value.details.get("dimension_absent") or []
    assert "type_safety" in absent, (
        f"a declared dimension the result never mentions must be named; "
        f"details={excinfo.value.details}"
    )


def test_dimension_absent_has_a_registered_remediation():
    """Round 24 站1: a block reason nobody registered prints no way out."""
    from core.quality_gate.block_reason import _DETAIL_REGISTRY

    assert "dimension_absent" in _DETAIL_REGISTRY, (
        "a new details key without a registry entry leaves the agent with a "
        "block and no instruction — tests/test_block_reason_registry.py is the "
        "completeness meta-test this pairs with"
    )
    summary, remediation = _DETAIL_REGISTRY["dimension_absent"]
    assert summary and remediation
