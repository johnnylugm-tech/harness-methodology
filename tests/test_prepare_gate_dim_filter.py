"""`prepare_gate` hands the prompt the dimension list the YAML declares.

Round 57 站0/站4/站6 wrote this file for `22e2471`, which moved a feature-flag
filter into `prepare_gate` so `evaluation_prompt()` would stop advertising a
dimension the orchestrator was not going to score. Round 60 站2 retired the
flags, so the filter has nothing to remove and is gone; what the file pins now
is the other half, which was always the more important one:

    every field of every declared dimension reaches the prompt intact.

`prepare_gate` used to rebuild `GateConfig` from a hand-written four-key dict
(`name/tier/threshold/weight`) while `finalize_gate`'s `_s4_verifiable` selects
on `tool` and `requires_tool_execution` — fields the YAML declares and nothing
between the YAML and that selection carried. Measured at Round 57 station 0::

    _load_config(1).dimensions[0] keys -> ['name','threshold','tier','weight']
    _s4_verifiable                     -> set()          (for every gate)
    the YAML's own answer for gate 1    -> {architecture_constraints, linting,
                                            test_coverage, type_safety}

An empty `_s4_verifiable` makes `_dim_passes` return True for **any**
dimension whose score is None — Round 27 站1's "N/A is not free" and Round 35
站2 are both empty at that layer.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import pytest


@pytest.fixture
def project_with_mutation_off(tmp_path: Path) -> Path:
    methodology = tmp_path / ".methodology"
    methodology.mkdir()
    (methodology / "harness_config.json").write_text(
        json.dumps({"version": 1, "features": {"mutation_testing": False}}),
        encoding="utf-8",
    )
    (methodology / "quality_manifest.json").write_text(
        json.dumps({"fr_ids": []}), encoding="utf-8")
    return tmp_path


def _prepare(project: Path, gate_num: int):
    from harness.harness_bridge import HarnessBridge

    bridge = HarnessBridge()
    # The CRG bridge reaches for an external tool; this test is about the
    # dimension list, and `bridge.crg` is a public attribute by design
    # (`self.crg = CRGBridge()  # gracefully degrades if CRG unavailable`).
    bridge.crg = mock.MagicMock()
    return bridge.prepare_gate(
        gate_num=gate_num, project_root=str(project), phase=3, fr_id=None,
    )


def test_every_declared_dimension_reaches_the_evaluation_prompt(
    project_with_mutation_off,
):
    """No config can shrink the list — not even one still asking to.

    The fixture's project is the shape three corpus projects had on
    2026-08-19: `features.mutation_testing: false`. run-gate refuses it
    outright (tests/test_dimension_cannot_be_disabled.py); if something calls
    `prepare_gate` directly anyway, the dimension is still there to be scored.
    """
    import yaml

    from core.quality_gate.gate_thresholds import gate_config_path

    ctx = _prepare(project_with_mutation_off, 2)
    declared = [
        d["name"]
        for d in yaml.safe_load(
            gate_config_path(2).read_text(encoding="utf-8"))["dimensions"]
    ]
    assert [d.name for d in ctx.config.dimensions] == declared
    assert "mutation_testing" in ctx.evaluation_prompt()


@pytest.mark.parametrize("gate_num", [1, 2, 3, 4])
def test_the_config_carries_the_fields_the_verdict_selects_on(tmp_path, gate_num):
    """`tool` and `requires_tool_execution` survive YAML → GateConfig → ctx.

    Without this, `_s4_verifiable` is the empty set and every unscored
    dimension passes its own floor vacuously.
    """
    import yaml

    from core.quality_gate.gate_thresholds import gate_config_path

    (tmp_path / ".methodology").mkdir()
    ctx = _prepare(tmp_path, gate_num)

    raw = yaml.safe_load(gate_config_path(gate_num).read_text(encoding="utf-8"))
    expected = {
        d["name"]: (d.get("tool"), bool(d.get("requires_tool_execution", False)))
        for d in raw["dimensions"]
    }
    got = {
        d.name: (d.tool or None, d.requires_tool_execution)
        for d in ctx.config.dimensions
    }
    assert got == expected


@pytest.mark.parametrize("gate_num", [1, 2, 3, 4])
def test_the_set_finalize_selects_on_is_not_empty(tmp_path, gate_num):
    """`_s4_verifiable`'s derivation, reproduced from the config the gate holds.

    This is the assertion the missing fields defeated. `_dim_passes` reads
    `d.name not in _s4_verifiable -> True`, so an empty set means every
    unscored dimension passes its own floor vacuously — measured `set()` for
    all four gates before this round.

    Blocking consequence, measured across the corpus at 站6: of seven
    null-score dimensions in committed gate results, three would newly block
    (`performance`, in taskq-api's gate 3 and taskq-plus's gates 3 and 4, all
    with no `score_source`). The other four already could not: `architecture`
    is in `_CRG_OWNED_DIMENSIONS`, and taskq-plus's three `mutation_testing`
    nulls sit behind `features.mutation_testing: false`, which removes the
    dimension before the verdict sees it. An honest N/A on a fresh run still
    passes — S4 runs the tool, gets no number, and writes `framework_na`.
    """
    import dataclasses

    from harness.harness_bridge import _CRG_OWNED_DIMENSIONS
    from core.quality_gate.gate_thresholds import load_gate_dimensions

    (tmp_path / ".methodology").mkdir()
    ctx = _prepare(tmp_path, gate_num)

    derived = {
        d.get("name")
        for d in (dataclasses.asdict(x) for x in ctx.config.dimensions)
        if d.get("requires_tool_execution", False) and d.get("tool")
        and d.get("name") not in _CRG_OWNED_DIMENSIONS
    }
    expected = {
        d["name"] for d in load_gate_dimensions(gate_num)
        if d.get("requires_tool_execution") and d.get("tool")
        and d["name"] not in _CRG_OWNED_DIMENSIONS
    }
    assert derived == expected
    assert derived, "an empty set makes every unscored dimension pass"
