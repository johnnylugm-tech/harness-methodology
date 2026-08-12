"""Round 48 站0 — a block names what failed; it must also name whose fault it is.

The measurement this round starts from: the eight generated phase workflows
carry 125 terminal halt sites. 13 name harness (`harness_bug_detected`), 19
name infrastructure (`dispatch_structurally_broken` / `session_limit_blocked`),
and **93 name nobody** — they read "X did not PASS in N attempts".

Worse, the 13 that do name harness fire only on an *uncaught exception*
(core/errors.py's crash boundary → exit 70). A harness **logic** bug produces
an ordinary, well-formed `[BLOCKED]` line, structurally identical to a project
defect. docs/ERROR_HANDLING.md records four such incidents — R31, R32, R33,
R45 — every one of them found by a human audit round, none by the running
pipeline.

These tests pin the behaviour, not the implementation: given the evidence a
block actually carries, who is named, and is "I cannot tell" said out loud
rather than rounded down to "the project's fault".
"""

from __future__ import annotations

from cli.exit_codes import REGISTRY


def _classify(**kwargs):
    from core.fault_owner import classify_fault

    return classify_fault(**kwargs)


def test_crash_banner_names_harness():
    """Exit 70 / `[HARNESS-BUG]` is the one signal that already existed."""
    from core.fault_owner import Owner

    verdict = _classify(exit_code=70)
    assert verdict.owner == Owner.HARNESS
    assert verdict.evidence, "a verdict must say what decided it"


def test_a_non_crash_harness_defect_still_names_harness():
    """The gap this round exists to close.

    `crg_independent_failed` is a block_reason detail key whose registered
    remediation already reads "Persistent failure is a harness defect
    (crash-triage --open-cr)" — harness could not complete its OWN
    measurement. No exception was raised, no crash bundle was written, and
    before this round nothing anywhere converted that sentence into a
    routable owner.
    """
    from core.fault_owner import Owner

    verdict = _classify(
        text="[BLOCKED] Gate 2 blocked.\n  1. crg_independent_failed\n"
        "     - the harness's own CRG measurement failed"
    )
    assert verdict.owner == Owner.HARNESS


def test_an_unattributable_block_says_unknown_not_project():
    """R30's rule, applied to attribution.

    "X did not PASS in 3 attempts" is 93 of the 125 halt sites. It carries no
    evidence about whose defect it is. Answering PROJECT here would be the
    same error as a checker that could not run returning "ran, clean" — and
    it is the answer that costs the most, because it routes a fix agent at
    code that may be healthy.
    """
    from core.fault_owner import Owner

    verdict = _classify(text="Phase 4 preflight did not PASS in 3 orchestrator attempts")
    assert verdict.owner == Owner.UNKNOWN


def test_infra_exit_codes_name_infrastructure():
    from core.fault_owner import Owner

    # 24 = spawn-substrate preflight failed; 32 = CI verdict unobtainable.
    assert _classify(exit_code=24).owner == Owner.INFRA
    assert _classify(exit_code=32).owner == Owner.INFRA


def test_a_project_quality_failure_names_the_project():
    from core.fault_owner import Owner

    # 9 = advance-phase test/coverage shortfall.
    assert _classify(exit_code=9).owner == Owner.PROJECT


def test_overloaded_exit_codes_need_their_message():
    """Measured 2026-08-12: two of the four overloaded codes carry two owners.

    18 is BOTH `ruff check` failing (the project's code) and
    `advance-phase` refusing to proceed over uncommitted harness/ submodule
    edits (the workspace's state). 19 is BOTH `sync-harness`'s
    SubmoduleSyncError (infrastructure) and mypy failing (the project's code).
    12 and 17 were measured too and do NOT conflict — all their sites are
    project-side — so they take a plain entry.

    The exit code alone therefore cannot decide those two. Asked without the
    message, the answer is UNKNOWN; asked with it, the discriminator decides.
    """
    from core.fault_owner import Owner

    assert _classify(exit_code=19).owner == Owner.UNKNOWN
    assert _classify(
        exit_code=19, text="[sync-harness] FAILED: could not fast-forward submodule"
    ).owner == Owner.INFRA
    assert _classify(
        exit_code=19, text="[BLOCKED] Type Safety (mypy) failure."
    ).owner == Owner.PROJECT


def test_every_registered_exit_code_has_an_owner():
    """Three-way completeness: REGISTRY <-> docstring (already guarded) <->
    OWNER_BY_EXIT. A code added without an owner is a block that cannot be
    routed, which is the whole defect this round is about."""
    from core.fault_owner import OWNER_BY_EXIT

    missing = sorted(set(REGISTRY) - set(OWNER_BY_EXIT))
    extra = sorted(set(OWNER_BY_EXIT) - set(REGISTRY))
    assert not missing, (
        "exit code(s) in cli/exit_codes.py's REGISTRY with no owner in "
        f"core/fault_owner.py's OWNER_BY_EXIT: {missing}"
    )
    assert not extra, f"OWNER_BY_EXIT names code(s) absent from REGISTRY: {extra}"
