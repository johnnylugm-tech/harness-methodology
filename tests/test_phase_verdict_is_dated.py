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


def test_an_unmeasured_surface_path_is_not_a_moved_one(tmp_path):
    """`unknown` means git was unavailable, not that the path changed.

    Round 32/35's rule: could-not-measure is not a finding.
    """
    from core.harness_provenance import (
        ENFORCER_SURFACE_PATHS, enforcer_surface, phase_verdict_staleness,
    )

    recorded = dict(enforcer_surface())
    recorded[ENFORCER_SURFACE_PATHS[0]] = "unknown"
    project = _write_state(tmp_path, {"1": {"enforcer_surface": recorded}})

    assert phase_verdict_staleness(project, 1) is None


# ── the two consumers ───────────────────────────────────────────────────────

def _stale_project(tmp_path):
    from core.harness_provenance import ENFORCER_SURFACE_PATHS, enforcer_surface
    stale = dict(enforcer_surface())
    stale[ENFORCER_SURFACE_PATHS[0]] = "0" * 40
    return _write_state(tmp_path, {"1": {"sha": "a" * 40, "enforcer_surface": stale}})


def test_doctor_names_the_phase_whose_rules_moved(tmp_path):
    from core.doctor import _check_phase_verdict_staleness

    findings = _check_phase_verdict_staleness(_stale_project(tmp_path))

    assert len(findings) == 1, [f.message for f in findings]
    assert findings[0].severity == "WARN", (
        "a moved bar does not make the recorded verdict wrong — Round 38's "
        "rule is that nothing here is waived, not that the phase failed"
    )
    assert "Phase 1" in findings[0].message
    assert "core/quality_gate" in findings[0].message


def test_run_doctor_actually_runs_the_check(tmp_path):
    """The wiring, not just the function.

    Counter-proof discipline caught this: unwiring
    `_check_phase_verdict_staleness` from `run_doctor`'s dispatch list left
    every test above green, because they all called the function directly.
    A check nobody runs is the exact shape this round exists to close, and
    the first draft of its own guard had it.
    """
    from core.doctor import run_doctor

    findings = run_doctor(_stale_project(tmp_path))

    assert any("recorded PASS was measured under a different enforcement "
               "surface" in f.message for f in findings), (
        "run_doctor did not run the staleness check: "
        f"{[f.message[:60] for f in findings]}"
    )


def test_doctor_says_nothing_when_the_surface_has_not_moved(tmp_path):
    from core.doctor import _check_phase_verdict_staleness
    from core.harness_provenance import enforcer_surface

    project = _write_state(tmp_path, {
        "1": {"sha": "a" * 40, "enforcer_surface": dict(enforcer_surface())},
    })
    assert _check_phase_verdict_staleness(project) == []


def test_the_advance_block_note_names_the_stale_phases(tmp_path):
    """The note rides on the [BLOCKED] message, where the question arises."""
    from cli.phase_cmds import _enforcer_moved_note

    note = _enforcer_moved_note(_stale_project(tmp_path), 3)

    assert "Phase(s) 1" in note
    assert "not waived" in note


def test_the_advance_block_note_is_empty_when_there_is_nothing_to_say(tmp_path):
    """Empty, not a sentence saying nothing — otherwise it is noise on every
    block, which is the prose-nobody-reads failure this round is about."""
    from cli.phase_cmds import _enforcer_moved_note
    from core.harness_provenance import enforcer_surface

    project = _write_state(tmp_path, {
        "1": {"sha": "a" * 40, "enforcer_surface": dict(enforcer_surface())},
    })
    assert _enforcer_moved_note(project, 3) == ""
