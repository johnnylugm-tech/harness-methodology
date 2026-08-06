"""A recorded phase PASS is a claim about an enforcer, not a timeless fact.

Round 43 站0. `state.json::phase_completed[N]` already carries `enforcer_sha`
and `enforcer_surface` — the git object IDs of `core/quality_gate`,
`harness/harness_bridge.py` and `harness/gate_configs` as they stood when
Phase N was accepted (Round 19 站3, Round 29 站4). Nothing compares them to
the present. `core/doctor.py` is the only reader, and it asks a different
question: does the recorded SHA still resolve.

So when Round 42 站3 turned a missing SRS FR Block from a warning into a P2+
block, taskq-api — whose Phase 1 was accepted at `c09fae1`, five rounds
earlier — failed a check that did not exist when it passed, with nothing in
the tooling able to say so. The operator reads "your Phase 1 artifact is
wrong"; the truth is "the bar moved".

The verdict does not change. Grandfathering a rule to artifacts accepted
before it existed would mean the framework can never raise its own bar, which
is Round 38's rule (no threshold may be waived) inverted. What changes is that
the recorded PASS stops claiming to be current.

`cli/exit_codes.py` already carries the sibling of this rule for the other
axis — EX_ADVANCE_GATE_VERDICT_MISSING: "a verdict measured on a different
tree is not a verdict for this one". A verdict measured by a different
enforcer is not a verdict under this one.
"""

from __future__ import annotations

import json


def _write_state(tmp_path, phase_completed):
    meth = tmp_path / ".methodology"
    meth.mkdir(parents=True, exist_ok=True)
    (meth / "state.json").write_text(
        json.dumps({
            "state": "RUNNING",
            "current_phase": 4,
            "phase_completed": phase_completed,
        }),
        encoding="utf-8",
    )
    return tmp_path


def test_a_moved_enforcement_surface_is_reported(tmp_path):
    from core.harness_provenance import (
        ENFORCER_SURFACE_PATHS, enforcer_surface, phase_verdict_staleness,
    )

    current = enforcer_surface()
    stale = dict(current)
    stale[ENFORCER_SURFACE_PATHS[0]] = "0" * 40

    project = _write_state(tmp_path, {
        "1": {"sha": "a" * 40, "enforcer_sha": "c" * 40,
              "enforcer_surface": stale},
    })

    moved = phase_verdict_staleness(project, 1)

    assert moved, (
        "Phase 1 was accepted under a different core/quality_gate tree and "
        "the reader did not say so"
    )
    assert ENFORCER_SURFACE_PATHS[0] in moved["moved"]
    assert moved["recorded"][ENFORCER_SURFACE_PATHS[0]] == "0" * 40
    assert moved["current"][ENFORCER_SURFACE_PATHS[0]] == \
        current[ENFORCER_SURFACE_PATHS[0]]


def test_an_unchanged_surface_reports_nothing(tmp_path):
    from core.harness_provenance import enforcer_surface, phase_verdict_staleness

    project = _write_state(tmp_path, {
        "1": {"sha": "a" * 40, "enforcer_sha": "c" * 40,
              "enforcer_surface": dict(enforcer_surface())},
    })

    assert phase_verdict_staleness(project, 1) is None


def test_a_phase_with_no_recorded_verdict_reports_nothing(tmp_path):
    """Jurisdiction (Round 40 站1): absent is not stale."""
    from core.harness_provenance import phase_verdict_staleness

    project = _write_state(tmp_path, {})
    assert phase_verdict_staleness(project, 1) is None

    project2 = _write_state(tmp_path / "b", {"1": {"sha": "a" * 40}})
    assert phase_verdict_staleness(project2, 1) is None
