"""Turning a dimension off must leave a trace the verdict carries (Round 39 站2).

Three dimensions can be switched off in `.methodology/harness_config.json`
(`core/harness_config.py::_DIM_TO_FEATURE`):

    mutation_testing   -> mutation_testing
    architecture       -> crg_architecture
    adversarial_review -> phase4_llm_review

What a `false` does, measured before this round:

  * `harness_bridge` drops the dimension from the gate config's list AND from
    the scored dimensions — it is not measured, not compared, not blocking
  * `cmd_crg_arch_check` returns 0 immediately — **CI's absolute architecture
    floor becomes a pass**
  * `cli/gate_cmds.py`'s Gate 4 B3 check (CRG reconnaissance must exist) is
    skipped entirely

and the only trace of any of it was a `print()`. Nothing reached the
degradation ledger, the quality manifest, the gate result, or Round 38 站4's
`gate_verify.jsonl`. A committed boolean could change the verdict while the
verdict itself showed no sign of it.

Round 30's rule is that abstaining is not passing. Round 38 站4 made a verdict
carry the tree it was measured on; this makes it carry the dimensions it was
measured over — the same "the denominator travels with the number" discipline
Round 37 applied to file sets, applied to the dimension set.

The switch itself stays: a project may genuinely have no mutmut or no
code-review-graph. What goes is its invisibility.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = [pytest.mark.core]


def _project(tmp_path: Path, features: dict) -> Path:
    (tmp_path / ".methodology").mkdir()
    (tmp_path / ".methodology" / "harness_config.json").write_text(
        json.dumps({"version": 1, "features": features}), encoding="utf-8")
    (tmp_path / "mod.py").write_text("x = 1\n", encoding="utf-8")
    return tmp_path


def test_disabled_dimensions_reports_the_switch_and_its_key(tmp_path: Path) -> None:
    from core.quality_gate.dimension_scope import disabled_dimensions

    project = _project(tmp_path, {"crg_architecture": False})
    assert disabled_dimensions(project) == {"architecture": "crg_architecture"}


def test_nothing_disabled_is_the_common_case(tmp_path: Path) -> None:
    from core.quality_gate.dimension_scope import disabled_dimensions

    assert disabled_dimensions(_project(tmp_path, {})) == {}


def test_the_map_is_the_one_in_harness_config(tmp_path: Path) -> None:
    """One definition of dim→feature, not two. `scripts/plangen/blocks.py`
    carried a hand-written mirror of it; a second copy is how a dimension
    ends up disabled in the plan and enabled in the gate."""
    from core.harness_config import _DIM_TO_FEATURE
    from core.quality_gate.dimension_scope import disabled_dimensions

    all_off = {feat: False for feat in _DIM_TO_FEATURE.values()}
    got = disabled_dimensions(_project(tmp_path, all_off))
    assert got == dict(_DIM_TO_FEATURE)


def test_a_disabled_dimension_is_recorded_not_just_printed(tmp_path: Path) -> None:
    """The ledger entry is the point: a print is gone the moment the run is."""
    from core.quality_gate.dimension_scope import record_dimension_scope

    project = _project(tmp_path, {"crg_architecture": False})
    disabled = record_dimension_scope(project, gate=4)
    assert disabled == ["architecture"]

    ledger = project / ".methodology" / "degradations.jsonl"
    assert ledger.is_file(), "a disabled dimension left no degradation record"
    text = ledger.read_text(encoding="utf-8")
    assert "architecture" in text
    assert "crg_architecture" in text, (
        "the record must name the config key that caused it, or a reader "
        "cannot find the switch to turn back on")


def test_recording_nothing_writes_nothing(tmp_path: Path) -> None:
    """A project with every dimension enabled must not accrue ledger noise."""
    from core.quality_gate.dimension_scope import record_dimension_scope

    project = _project(tmp_path, {})
    assert record_dimension_scope(project, gate=4) == []
    assert not (project / ".methodology" / "degradations.jsonl").exists()


def test_the_verdict_carries_the_dimensions_it_was_measured_over(
    tmp_path: Path,
) -> None:
    """Round 38 站4's ledger gains the dimension set, for the same reason it
    carries the tree digest: a verdict that hides what it skipped is a verdict
    a later reader cannot re-derive."""
    from core.quality_gate.gate_verify import PASS, record_verdict

    project = _project(tmp_path, {"crg_architecture": False})
    record_verdict(project, gate=4, phase=6,
                   checks={"last_gate_ok": True, "spec_coverage_rc": 0,
                           "crg_rc": 0},
                   verdict=PASS)
    row = json.loads(
        (project / ".methodology" / "gate_verify.jsonl")
        .read_text(encoding="utf-8").splitlines()[0]
    )
    assert row["dimensions_disabled"] == ["architecture"]


def test_a_fully_enabled_verdict_records_an_empty_set(tmp_path: Path) -> None:
    """Present-and-empty, not absent: a reader must be able to tell
    "nothing was skipped" from "this record predates the field"."""
    from core.quality_gate.gate_verify import PASS, record_verdict

    project = _project(tmp_path, {})
    record_verdict(project, gate=2, phase=3, checks={}, verdict=PASS)
    row = json.loads(
        (project / ".methodology" / "gate_verify.jsonl")
        .read_text(encoding="utf-8").splitlines()[0]
    )
    assert row["dimensions_disabled"] == []


def test_crg_arch_check_records_its_own_skip(tmp_path: Path, capsys) -> None:
    """The one that matters most: this is the command CI runs as an absolute
    floor, and disabling it turns that floor into an unconditional pass."""
    import argparse

    from cli.check_cmds import cmd_crg_arch_check

    project = _project(tmp_path, {"crg_architecture": False})
    rc = cmd_crg_arch_check(argparse.Namespace(
        project=str(project), threshold=None, baseline=None,
        drift_threshold=0.4))
    assert rc == 0  # behaviour unchanged — the switch still works
    out = capsys.readouterr().out + capsys.readouterr().err
    assert "DEGRADED" in out or (
        project / ".methodology" / "degradations.jsonl").is_file(), (
        "crg-arch-check skipped the architecture floor without recording it")
