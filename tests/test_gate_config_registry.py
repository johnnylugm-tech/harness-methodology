"""Every dimension CI enforces must be declared by the gate that scores it
(Round 38 站0/站1).

The measured contradiction, taskq-renew 2026-08-05 — one dimension, three
enforcers, and they did not agree:

  enforcer                                            architecture floor
  --------------------------------------------------  ------------------
  templates/harness_quality_gate.yml
    job `crg-architecture-check` ("CRG Architecture     absolute, PHASE >= 3
    Gate (P3+)"), step guarded by PHASE >= 3
  .claude/workflows/phase3-implementation.js           absolute
    `gate2Pass = … && g2v.crg_rc === 0`
  harness/gate_configs/gate2_p3_exit.yaml              **not declared at all**

So Gate 2 could pass locally on a tree CI would reject on the very next push,
and the gate's own config — the thing Round 18 站2 made the single authority on
thresholds — was the one participant that never stated the rule.

This module derives the requirement instead of asserting it: it reads the phase
condition out of the CI workflow, walks `EXIT_GATE_MAP`, and demands that every
exit gate reachable at or after that phase declares the dimension. Changing the
CI condition changes what this test demands, which is the point — the two can no
longer drift apart silently.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

pytestmark = [pytest.mark.core]

REPO = Path(__file__).resolve().parent.parent
CI_TEMPLATE = REPO / "templates" / "harness_quality_gate.yml"

# The CI job that enforces the architecture floor, and the dimension it enforces.
_CRG_JOB = "crg-architecture-check"
_CRG_DIMENSION = "architecture"

_PHASE_GE = re.compile(r"PHASE\s*\)?\s*>=\s*(\d+)")


def _ci_workflow() -> dict:
    return yaml.safe_load(CI_TEMPLATE.read_text(encoding="utf-8"))


def _crg_job_min_phase() -> int:
    """The lowest phase at which CI runs the architecture check.

    Read from the step's `if:` expression rather than hard-coded, so that
    moving the CI gate earlier or later moves this test with it.
    """
    job = _ci_workflow()["jobs"][_CRG_JOB]
    phases = [
        int(m.group(1))
        for step in job.get("steps", [])
        if (cond := step.get("if")) and (m := _PHASE_GE.search(str(cond)))
    ]
    assert phases, (
        f"the {_CRG_JOB} job has no PHASE >= N guard — this test can no "
        "longer derive which phases CI holds to the architecture floor"
    )
    return min(phases)


def test_the_ci_job_that_enforces_architecture_still_exists() -> None:
    """Positive control. If the job were renamed or removed, every assertion
    below would pass vacuously."""
    jobs = _ci_workflow()["jobs"]
    assert _CRG_JOB in jobs, (
        f"'{_CRG_JOB}' is gone from the CI template. If the architecture floor "
        "moved elsewhere, point this module at its new enforcer; if it was "
        "withdrawn, the gate configs' architecture entries need re-adjudicating."
    )
    assert _crg_job_min_phase() >= 1


def test_every_exit_gate_ci_holds_to_the_floor_declares_the_dimension() -> None:
    """The Round 38 defect, as an executable rule.

    CI measures the architecture floor on every push from `min_phase` onward.
    A phase whose exit gate does not declare `architecture` is a phase where
    the local gate and CI can reach opposite verdicts on the same tree.
    """
    from core.phase_topology import EXIT_GATE_MAP
    from core.quality_gate.gate_thresholds import load_gate_thresholds

    min_phase = _crg_job_min_phase()
    undeclared: list[str] = []
    for phase, gate in sorted(EXIT_GATE_MAP.items()):
        if phase < min_phase:
            continue
        if _CRG_DIMENSION not in load_gate_thresholds(gate):
            undeclared.append(f"phase {phase} → gate {gate}")
    assert not undeclared, (
        f"CI enforces '{_CRG_DIMENSION}' from phase {min_phase} onward, but these "
        f"exit gates never declare it: {undeclared}. The local gate would pass a "
        "tree CI rejects on the next push."
    )


def test_a_declared_dimension_blocks_even_at_zero_weight() -> None:
    """gate 2 declares architecture at weight 0.00 so the composite score and
    the existing weight split are untouched — the same shape
    `adversarial_review` already uses. That only works because the pass check
    is over thresholds, not weights."""
    import inspect

    from harness import harness_bridge

    source = inspect.getsource(harness_bridge)
    assert "_all_dims_pass = all(_dim_passes(d) for d in dims)" in source, (
        "finalize_gate no longer decides dimension pass/fail with a weight-blind "
        "all(); a zero-weight blocking dimension may have become unenforced"
    )


def test_zero_weight_blocking_dimensions_have_a_precedent() -> None:
    """Not a new pattern: the same gate config already blocks on a
    zero-weighted dimension, so the reader of gate 2's architecture entry has
    somewhere to look for the semantics."""
    raw = yaml.safe_load(
        (REPO / "harness" / "gate_configs" / "gate3_p4_exit.yaml").read_text(
            encoding="utf-8")
    )
    zero_weight_blocking = [
        d["name"] for d in raw["dimensions"]
        if float(d.get("weight", 0)) == 0.0 and float(d.get("threshold", 0)) > 0
    ]
    assert zero_weight_blocking, (
        "no zero-weight blocking dimension left in gate 3 — gate 2's "
        "architecture entry would be the only instance of the pattern"
    )


def test_gate2_architecture_threshold_matches_the_gates_that_follow() -> None:
    """A floor that tightens at P4 and P6 would mean a tree that passes Gate 2
    can fail Gate 3 on architecture alone with no code change in between."""
    from core.quality_gate.gate_thresholds import load_gate_thresholds

    floors = {g: load_gate_thresholds(g)[_CRG_DIMENSION] for g in (2, 3, 4)}
    assert len(set(floors.values())) == 1, (
        f"the architecture floor differs between exit gates: {floors}. CI applies "
        "one number from phase 3 onward; the gates must agree with it and with "
        "each other."
    )
