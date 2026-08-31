"""Round 83 站5 — the per-gate question could not produce a true answer.

`gate:dimension-declared-absent` fired when a dimension the project's quality
manifest pins an NFR to was not in THIS gate's config. That is a different
question from the one a manifest is asking, and it is one whose answer is
always the framework's own doing.

Measured over the eleven projects on this machine:

    union of the four gate configs      18 dimension names
    python registry (DIMENSION_TOOLS)   16
    registry - union                    EMPTY

So no declared dimension is unmeasured by the pipeline, and every row that
check emitted was gate layering reported back as a harness fault — taskq-cc-new
logged 79, sixty-six of them at Gate 1, which has four dimensions by design.

The row survives with the question its own reopen condition named: a dimension
in NO gate config at all, i.e. a requirement nothing this pipeline runs can
score. Zero rows across the corpus today, which is what makes the first one
worth reading.
"""

from __future__ import annotations

import pytest

from core.quality_gate.gate_thresholds import all_gate_dimension_names

pytestmark = [pytest.mark.core]


def test_every_scoreable_python_dimension_is_in_some_gate():
    """The measurement that makes the per-gate row unable to be true.

    If this ever fails, a dimension a manifest may legally pin an NFR to has
    left every gate config — and THAT is the finding the surviving row is for,
    surfaced here at framework-test time instead of once per project run.
    """
    from harness.toolchains.registry import DIMENSION_TOOLS

    unmeasured = sorted(set(DIMENSION_TOOLS["python"]) - all_gate_dimension_names())
    assert not unmeasured, (
        f"these dimensions are scoreable and no gate config declares them: "
        f"{unmeasured}. A project pinning an NFR to one would be pinning it to "
        f"something this pipeline never runs"
    )


def test_the_union_reads_every_gate():
    """A union built from a stale list of gates would silently shrink, and a
    smaller union makes MORE dimensions look never-measured — a false
    accusation rather than a missed one, which is the direction that wastes a
    project's round."""
    from core.quality_gate.gate_thresholds import (
        GATE_CONFIG_NAMES, load_gate_dimensions,
    )

    union = all_gate_dimension_names()
    for gate_num in GATE_CONFIG_NAMES:
        names = {d["name"] for d in load_gate_dimensions(gate_num) if d.get("name")}
        assert names, f"gate {gate_num} declares no dimensions"
        assert names <= union, (
            f"gate {gate_num} declares {sorted(names - union)}, which the union "
            f"does not contain — the union is not reading every gate")


def test_a_dimension_in_some_other_gate_is_not_reported(monkeypatch, tmp_path):
    """The subtraction, at the stage that writes the row.

    `architecture_constraints` lives only in gate1_per_fr.yaml, so at Gate 4 it
    IS declared-absent — and that is gate layering, not a gap. Six of the nine
    projects Round 73 站5 measured pin an NFR to it, which is where the bulk of
    the 79 rows came from.
    """
    from harness.gate_stages import _FinalizeStages

    rows = []
    import core.degradation_ledger as _dl
    monkeypatch.setattr(_dl, "record_degradation",
                        lambda *a, **k: rows.append((a, k)))

    class _Ctx:
        project_root = str(tmp_path)
        gate_num = 4
    _FinalizeStages._stage_declared_absent(
        ["architecture_constraints"], _Ctx(), {})
    assert rows == [], (
        "a dimension measured by another gate is not a dimension nothing "
        f"measures: {rows}")


def test_a_dimension_no_gate_measures_is_reported(monkeypatch, tmp_path):
    """The positive control. Without it the subtraction above could be a
    deletion, and the channel would be silent in the one case it exists for."""
    from harness.gate_stages import _FinalizeStages

    rows = []
    import core.degradation_ledger as _dl
    monkeypatch.setattr(_dl, "record_degradation",
                        lambda *a, **k: rows.append((a, k)))

    class _Ctx:
        project_root = str(tmp_path)
        gate_num = 4
    _FinalizeStages._stage_declared_absent(
        ["architecture_constraints", "deployability"], _Ctx(), {})

    assert len(rows) == 1, rows
    args, kwargs = rows[0]
    assert args[1] == "gate:dimension-never-measured"
    assert kwargs["data"]["dimensions"] == ["deployability"], (
        "only the dimension no gate measures belongs in the row — including "
        f"the gate-layered one would bring the 79 false rows back: {kwargs}")
    assert kwargs["owner"] == "harness", (
        "a dimension no gate config contains is the framework's gap, not the "
        "project's")
