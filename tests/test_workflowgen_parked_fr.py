"""Round 102 站3 — workflow generators: parked FRs, re-attempt, rc visibility.

taskq-done's FR-01 measured the hole this closes: its Gate 1 was blocked on
whole-project SAB state, the run marked it `gate1Fail` and moved on, and even
after FR-02's own run repaired exactly that state nobody re-checked FR-01 —
it stayed Pending forever. The generated per-FR loop now PARKs an FR whose
GATE1 exits 25 (INFRA precondition) or 36 (repeated-failure refusal),
continues with the remaining FRs, and re-attempts parked FRs after the loop;
phase-exit hosts without a park/revisit loop (4/5/7/8) get rc 36 as a visible
terminal `repeated_failure` shape instead of prose, and the run-all driver
threads the owner through its phase-incomplete catch-all.

These are pure-string assertions over the generators (the shipped JS is a
byte-identical product of them, pinned by tests/test_workflowgen_golden.py).
"""

from __future__ import annotations

from scripts.workflowgen.generate_workflows import generate

_PARKED_DELTA_PHASES = (4, 5, 7, 8)


def _p3() -> str:
    return generate(3)


def _delta(phase: int) -> str:
    return generate(phase)


def test_phase3_declares_a_parked_accumulator():
    out = _p3()
    assert "const gate1Parked = []" in out, (
        "phase 3 must collect parked FRs to re-attempt after the loop"
    )


def test_phase3_parks_rc25_and_rc36_instead_of_returning():
    """25 (INFRA precondition) and 36 (repeated-failure refusal) become a
    park in phase 3: set aside, continue with remaining FRs, re-attempt
    later. 45 (PHANTOM) keeps its terminal return — its remedy is a human
    decision, and no later FR will change the declaration for it."""
    out = _p3()
    assert "parked = { fr: frId, rc: frRc }" in out, (
        "no park branch was emitted for phase 3"
    )
    assert out.count("if (frRc === 25)") == 1
    assert out.count("if (frRc === 36)") == 1
    assert "infra_abort: true" not in out, (
        "phase 3 no longer aborts the run on rc 25 — it parks the FR"
    )
    assert "phantom_abort: true" in out, (
        "rc 45 must stay a terminal abort even in the park host"
    )


def test_phase3_continues_after_a_park_and_reattempts():
    out = _p3()
    assert "stillParked" in out, "no re-attempt collector for parked FRs"
    assert "gate1-retry-" in out, (
        "parked FRs must be re-attempted after the remaining FRs run"
    )
    assert "halt('gate1-parked'" in out, (
        "the final state-blocked halt must name the still-parked FRs"
    )
    assert "resume-fr-step" in out, (
        "the halt must carry the resume command for a human"
    )


def test_delta_phases_turn_rc36_into_a_visible_terminal_shape():
    """Phase 4/5/7/8 have no park/revisit loop, so 36 must not vanish into
    prose: it returns the `repeated_failure` shape the run-all driver
    routes with owner infra."""
    for phase in _PARKED_DELTA_PHASES:
        out = _delta(phase)
        assert "if (frRc === 36)" in out, f"phase {phase}: no rc-36 detector"
        assert "repeated_failure: true" in out, (
            f"phase {phase}: rc 36 must return a visible terminal shape"
        )
        assert "parked = { fr: frId" not in out, (
            f"phase {phase}: a delta host has no park loop and must not "
            f"reference the park variables"
        )


def test_delta_phases_keep_the_existing_terminal_shapes():
    """23/70/25/45 keep their meanings everywhere they are not parked."""
    for phase in _PARKED_DELTA_PHASES:
        out = _delta(phase)
        for marker in ("dispatch_structurally_broken: true",
                       "harness_bug_detected: true",
                       "infra_abort: true", "phantom_abort: true"):
            assert marker in out, (
                f"phase {phase}: {marker} was dropped from the terminal set"
            )


def test_runall_driver_routes_repeated_failure_and_threads_the_owner():
    """The driver gets a repeated_failure branch and the phase-incomplete
    catch-all forwards the outcome's owner instead of dropping it (the
    measured `owner: unknown` mislabel of exit-25-era blocks)."""
    out = generate_composite_runall()
    assert "outcome.repeated_failure" in out, (
        "run-all has no branch for the delta phases' rc-36 shape"
    )
    assert "'repeated-failure'" in out and "'infra'" in out, (
        "run-all must record repeated-failure rows with owner infra"
    )
    assert "outcome && outcome.owner" in out, (
        "the phase-incomplete catch-all still drops the owner"
    )


def generate_composite_runall() -> str:
    from scripts.workflowgen.spec_runall import generate_runall

    return generate_runall()
