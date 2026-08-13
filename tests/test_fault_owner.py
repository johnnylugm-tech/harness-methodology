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


def test_the_discriminated_codes_are_exactly_the_ones_the_table_calls_unknown():
    """A code the table calls UNKNOWN must either be genuinely unattributable
    (exit 1's catch-all, exit 36's repeated-failure signature) or carry a
    discriminator. An UNKNOWN with neither is an entry nobody finished."""
    from core.fault_owner import DISCRIMINATED_EXITS, OWNER_BY_EXIT, Owner

    unknown = {code for code, owner in OWNER_BY_EXIT.items() if owner == Owner.UNKNOWN}
    genuinely_unattributable = {1, 36}
    assert unknown - genuinely_unattributable == DISCRIMINATED_EXITS
    assert DISCRIMINATED_EXITS == {14, 18, 19, 20, 25}


# ---------------------------------------------------------------------------
# Round 50 站0 — the corpus.
#
# Round 48's Self-Review committed this round to a measurement: take the real
# halt messages a production run produced and see how many the classifier can
# attribute. If it cannot manage most of them, "the classifier needs more
# rules" is the wrong diagnosis and the evidence source has to change.
#
# Measured 2026-08-13 against a full P1–P8 run: nine messages, nine UNKNOWNs.
# Not a shortfall — a floor. Every one of these arrives from a layer that has
# no exit code to carry, so a table keyed on exit codes has nothing to read,
# and the free-text fallback is left guessing at prose the framework itself
# wrote and could have labelled at the source.
#
# The first message below is the sharpest: it IS a harness bug. It was fixed
# by hand four hours later, in the harness's own tree, and the project could
# not clear Phase 6 until the submodule pointer moved. The pipeline recorded
# it as `owner: unknown` and the repair workflow built for exactly this case
# never started.
# ---------------------------------------------------------------------------

# (message, expected owner). Verbatim from .methodology/degradations.jsonl and
# .methodology/workflow_blocks.jsonl of that run.
from core.fault_owner import Owner  # noqa: E402  (corpus needs it at module level)

REAL_HALT_CORPUS: tuple[tuple[str, str], ...] = (
    ("HR-08: Phase 6 Peer Review had REJECT or unresolved medium/high gaps "
     "— escalate to human (previously this was silently ignored; T1-B adds "
     "the check)", Owner.HARNESS),
    ("P5 exit blocked by 01-requirements/TRACEABILITY_MATRIX.md", Owner.PROJECT),
    ("P4 entry blocked by FR-01: declares a property invariant but no "
     "executing property-based test covers it", Owner.PROJECT),
    ("P5 entry blocked by SEC-R8: threat verification test "
     "'test_sec_t01_malformed_payload_rejected' not found", Owner.PROJECT),
    ("FR-06 GATE1: 2 consecutive no-progress fix rounds "
     "(failure_class=LOW_COVERAGE)", Owner.PROJECT),
    ("'pytest-benchmark' produced no score the harness could read",
     Owner.HARNESS),
    ("SAB scope_layers resolve to non-existent director(ies) "
     "['03-development/src/taskq/service/auth']", Owner.HARNESS),
    ("FR-01 TDD-GREEN: TURN_BUDGET", Owner.INFRA),
    ("incremental graph covered 6 of 41 delivered source file(s) — "
     "rebuilding in full", Owner.HARNESS),
)


def test_the_real_halt_corpus_is_mostly_attributable():
    """At most two of the nine may remain UNKNOWN.

    Not nine of nine: some halts genuinely carry no owner at the moment they
    are written, and forcing an answer there is the rounding-off this module
    exists to refuse. The bar is that the classifier stops being useless.
    """
    unknown = [
        msg for msg, _ in REAL_HALT_CORPUS
        if _classify(text=msg).owner == Owner.UNKNOWN
    ]
    assert len(unknown) <= 2, (
        f"{len(unknown)} of {len(REAL_HALT_CORPUS)} real halt messages carry "
        f"no attribution:\n  " + "\n  ".join(unknown)
    )


def test_the_harness_bug_that_was_fixed_by_hand_is_attributed_to_harness():
    """The single message that reached workflow_blocks.jsonl in that run.

    It was a harness defect (a Peer Review verdict parser that accepted a
    value outside its own enum). Recorded as unknown, so nothing routed it.
    """
    verdict = _classify(text=REAL_HALT_CORPUS[0][0])
    assert verdict.owner == Owner.HARNESS, verdict.evidence
    assert verdict.evidence, "a verdict must say what decided it"


def test_every_corpus_entry_gets_its_recorded_owner_or_abstains():
    """No entry may be attributed to the WRONG tree.

    A wrong owner is worse than UNKNOWN: it sends a repair loop at a tree
    that is not the one that has to change.
    """
    wrong = [
        (msg, expected, _classify(text=msg).owner)
        for msg, expected in REAL_HALT_CORPUS
        if _classify(text=msg).owner not in (expected, Owner.UNKNOWN)
    ]
    assert not wrong, wrong
