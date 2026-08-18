"""`prepare_gate` filters flag-disabled dimensions, and keeps the rest whole.

Round 57 站0 / 站4 / 站6. Commit 22e2471 moved the feature-flag filter into
`prepare_gate` so `evaluation_prompt()` stops advertising a dimension the
orchestrator will not score — a real cost (a Gate 2 orchestrator spent its
wall budget attempting mutation_testing for a project whose flag was false).
It shipped with no test at all, and the guard has to exist before the next
round can change anything near it.

Two facts are pinned here, and they pull in opposite directions:

* a disabled dimension must be **gone** from the config the prompt renders;
* every other field of the surviving dimensions must be **intact**.

The second is not decoration. `prepare_gate` rebuilds `GateConfig` from a
hand-written four-key dict (`name/tier/threshold/weight`), and
`finalize_gate`'s `_s4_verifiable` selects on `tool` and
`requires_tool_execution` — fields the YAML declares and nothing between the
YAML and that selection carries. Measured at station 0::

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


def test_a_disabled_dimension_never_reaches_the_evaluation_prompt(
    project_with_mutation_off,
):
    ctx = _prepare(project_with_mutation_off, 2)

    names = [d.name for d in ctx.config.dimensions]
    assert "mutation_testing" not in names, (
        "the orchestrator reads evaluation_prompt() and attempts what it lists"
    )
    assert "mutation_testing" not in ctx.evaluation_prompt()
    assert "test_coverage" in names, "only the disabled one goes"


def test_the_disabled_dimension_stays_visible_to_the_ledger(
    project_with_mutation_off,
):
    """Round 39 站2 must survive the filter.

    `finalize_gate` derives `_disabled_dims` from `_DIM_TO_FEATURE` plus
    harness_config, not from `ctx.config`, so removing the dimension from the
    config does not hide the abstention. Pinned because the obvious next
    refactor — deriving the disabled set from the config diff — would.
    """
    from core.quality_gate.dimension_scope import disabled_dimensions

    assert disabled_dimensions(str(project_with_mutation_off)) == {
        "mutation_testing": "mutation_testing"
    }


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
