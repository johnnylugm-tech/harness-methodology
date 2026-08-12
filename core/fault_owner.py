"""fault_owner.py — whose tree has to change to clear this block (Round 48 站1).

`cli/exit_codes.py` is the single source of truth for WHAT failed. Nothing was
the single source of truth for WHOSE FAULT it was, so a block could not be
routed. Measured across the eight generated phase workflows: 125 terminal halt
sites, of which 93 name no owner at all, 19 name infrastructure, and 13 name
harness — and those 13 fire only on an uncaught exception (core/errors.py's
crash boundary → exit 70). A harness LOGIC bug does not crash; it emits an
ordinary, well-formed `[BLOCKED]` line that is structurally identical to a
project defect. docs/ERROR_HANDLING.md records four such incidents (R31, R32,
R33, R45); every one was found by a human audit round, none by the pipeline.

THE QUESTION THE FIELD ANSWERS

    Whose tree must change before this block can clear?

  HARNESS   harness-methodology's own tree      → repair-harness
  PROJECT   the target project's tree           → the ordinary fix loop
  INFRA     neither tree — the environment      → stop; a human or the
                                                  environment must change
  UNKNOWN   the evidence does not say           → stop and record. Never
                                                  rounded down to PROJECT.
  NONE      nothing failed                      → not a fault at all

NONE is the fifth value the plan did not have. Exit 0, exit 130 (Ctrl-C) and
the exit-16 tombstone are not failures, and forcing them into UNKNOWN would
claim "we could not tell" about cases where there is nothing to tell. Recorded
in docs/PROPOSAL_ADJUDICATIONS.md rather than folded into an existing bucket —
the same rule Round 47 followed when `install_step` turned out to need five
provenances instead of three.

ON READING TEXT (the Round 41 站3 hazard)

`classify_fault(text=...)` expects text the FRAMEWORK wrote — a `[BLOCKED]` /
`[FATAL]` line, `.methodology/last_block.md`, or a workflow's own halt message.
It must NOT be handed a sub-agent's verbatim reply. Round 41 站3 measured the
cost of the opposite: four replies drawn from taskq-api's own HTTP domain all
classified as INFRA_ERROR, because no registry can distinguish "the API
returned 401" from "the test asserts 401" when the strings are identical and
only their provenance differs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = [
    "Owner",
    "FaultVerdict",
    "OWNER_BY_EXIT",
    "DISCRIMINATED_EXITS",
    "classify_fault",
    "routes_to_harness_repair",
]


class Owner:
    """Whose tree must change. Values are the strings written to the ledger."""

    HARNESS = "harness"
    PROJECT = "project"
    INFRA = "infra"
    UNKNOWN = "unknown"
    NONE = "none"


ALL_OWNERS = frozenset(
    {Owner.HARNESS, Owner.PROJECT, Owner.INFRA, Owner.UNKNOWN, Owner.NONE}
)


@dataclass(frozen=True)
class FaultVerdict:
    """An owner, and what decided it. `evidence` is never empty — a verdict
    whose basis cannot be stated is the thing this module exists to replace."""

    owner: str
    evidence: str


# ---------------------------------------------------------------------------
# The table. One entry per cli/exit_codes.py REGISTRY code; completeness in
# both directions is enforced by tests/test_fault_owner.py.
#
# A code whose owner the MESSAGE decides is listed here as UNKNOWN — that is
# what the number alone is worth — and refined by _DISCRIMINATORS below.
# ---------------------------------------------------------------------------
OWNER_BY_EXIT: dict[int, str] = {
    0: Owner.NONE,  # success
    # The catch-all. It is returned from dozens of unrelated sites and carries
    # no shared meaning, so it carries no shared owner either. Saying UNKNOWN
    # here is the honest answer, not a gap to be filled later.
    1: Owner.UNKNOWN,
    2: Owner.PROJECT,  # run-gap-analysis: critical gaps in the project's specs
    5: Owner.PROJECT,  # Gate 4 prerequisites: the project's own schema/score files
    # Measured 2026-08-12 (cli/gate_cmds.py:2544): the commit that should have
    # recorded a passing gate did not land, and the in-code diagnostic names a
    # prepare-commit-msg hook rejection (stale trace attestation) as the usual
    # cause — the project's tree. KNOWN LIMIT: a git-level failure (disk full,
    # index lock) would be misattributed here. The git stderr is printed but
    # not carried in a field this function can read, so a discriminator would
    # have to guess. Recorded in the ledger rather than guessed.
    6: Owner.PROJECT,
    8: Owner.PROJECT,  # missing deliverables
    9: Owner.PROJECT,  # coverage shortfall / red suite
    # The REGISTRY description still reads "PAUSE — Claude must evaluate gate",
    # which predates its live sites. Measured: all five returns are an entry
    # gate failing (phase_cmds.py:208/582/693/2049) or spec-coverage below
    # threshold (:2864). Both are the project's tree. The stale description is
    # reported, not rewritten — out of this round's scope.
    10: Owner.PROJECT,
    11: Owner.PROJECT,  # Phase Truth < 90%
    # Measured: all four sites (621 / 2572 / 2887 / 2941) are project-side —
    # phase_truth_passed missing, push-milestone requirements, a SAB-declared
    # file absent, a malformed SAB block. Overloaded, but not in owner.
    12: Owner.PROJECT,
    13: Owner.PROJECT,  # Agent B approvals incomplete
    # 14/18/19/20/25 carry TWO owners; the message decides. The table lists
    # them as UNKNOWN because that is what the NUMBER ALONE is worth — asking
    # without the message gets exactly this answer. See _DISCRIMINATORS.
    14: Owner.UNKNOWN,
    15: Owner.PROJECT,  # next phase's plan file missing
    16: Owner.NONE,  # RETIRED tombstone (減法 T3) — never returned
    # Measured: all three sites (2531 / 2546 / 3307) are project-side.
    17: Owner.PROJECT,
    18: Owner.UNKNOWN,  # ruff (project) OR submodule edits (infra) — see _DISCRIMINATORS
    19: Owner.UNKNOWN,  # sync-harness (infra) OR mypy (project) — see _DISCRIMINATORS
    20: Owner.UNKNOWN,  # gitleaks timeout (infra) OR secrets found (project)
    21: Owner.PROJECT,  # untracked diagnostic scripts at the repo root
    # GHOST_DETECTED. Not the agent's "fault" in a way any tree records, but
    # the question is whose tree must change: the work was claimed and not
    # done, so the project's. The block's own remediation agrees — "re-run each
    # flagged step with genuine code changes".
    22: Owner.PROJECT,
    23: Owner.INFRA,  # connectors disabled / ANTHROPIC_* overrides
    24: Owner.INFRA,  # spawn-substrate preflight probe failed
    25: Owner.UNKNOWN,  # HARNESS_BUG OR INFRA — the class is printed, then discarded
    # REGISTRY says it outright: "project data corruption, NOT a
    # harness-methodology bug". The remedy is to restore the file.
    26: Owner.PROJECT,
    27: Owner.PROJECT,  # quality_manifest.json structurally corrupt
    28: Owner.INFRA,  # the commit landed, `git push` did not
    29: Owner.PROJECT,  # SRS.md NFR vocabulary illegal
    30: Owner.PROJECT,  # deliverable H1 anchor broken
    31: Owner.PROJECT,  # CI red — a project quality failure (Round 37)
    32: Owner.INFRA,  # CI verdict unobtainable — REGISTRY says INFRA explicitly
    33: Owner.PROJECT,  # verify-gate: one of the gate's three checks failed
    34: Owner.PROJECT,  # no gate verdict for the tree being advanced
    35: Owner.PROJECT,  # step precondition unmet (a red baseline)
    # Round 41 站3 registered this as "neither INFRA nor CODE-FIX — read
    # degradations.jsonl for the signature and fix its cause". The cause is
    # whatever failed identically N times, which this code does not name.
    36: Owner.UNKNOWN,
    37: Owner.PROJECT,  # entry obligations at the phase being entered
    38: Owner.PROJECT,  # delivered files differ from HEAD
    70: Owner.HARNESS,  # [HARNESS-BUG] — the crash boundary
    130: Owner.NONE,  # Ctrl-C
}


# ---------------------------------------------------------------------------
# Codes whose owner the message decides.
#
# The REGISTRY docstring lists four "known inconsistencies" (12/17/18/19) where
# one number means two preconditions. Measured 2026-08-12, that list is neither
# the same set nor a superset of the codes whose OWNERS conflict:
#
#   12, 17  overloaded, both sub-cases project-side  -> plain entry above
#   14, 20  NOT in the docstring's list, owners conflict
#   18, 19  in the list, owners conflict
#   25      NOT in the list, owners conflict — and it is the sharpest case:
#           cli/fr_cmds.py::_abort_dispatch_infra_or_harness_bug RECEIVES the
#           class ("HARNESS_BUG" or "INFRA"), prints it, and then returns one
#           exit code for both. The answer was computed and thrown away.
#
# A code listed here and asked without its message answers UNKNOWN. That is the
# point: the number genuinely does not carry the answer.
# ---------------------------------------------------------------------------
_DISCRIMINATORS: dict[int, tuple[tuple[re.Pattern[str], str, str], ...]] = {
    14: (
        (
            re.compile(r"pytest --cov could not be run", re.I),
            Owner.INFRA,
            "the coverage measurement never ran",
        ),
        (
            re.compile(r"coverage\s+[\d.]+%\s*<", re.I),
            Owner.PROJECT,
            "coverage measured and below the manifest threshold",
        ),
    ),
    18: (
        (
            re.compile(r"Linting \(ruff\) failure", re.I),
            Owner.PROJECT,
            "ruff findings in the project's code",
        ),
        (
            re.compile(r"submodule", re.I),
            Owner.INFRA,
            "uncommitted harness/ submodule edits — workspace state, not code",
        ),
    ),
    19: (
        (
            re.compile(r"\[sync-harness\]", re.I),
            Owner.INFRA,
            "sync-harness could not update the submodule",
        ),
        (
            re.compile(r"Type Safety \(mypy\) failure", re.I),
            Owner.PROJECT,
            "mypy findings in the project's code",
        ),
    ),
    20: (
        (
            re.compile(r"gitleaks\) timed out", re.I),
            Owner.INFRA,
            "the secrets scan never completed",
        ),
        (
            re.compile(r"Hardcoded secrets detected", re.I),
            Owner.PROJECT,
            "gitleaks found secrets in the project",
        ),
    ),
    25: (
        (
            re.compile(r"\bHARNESS_BUG\b"),
            Owner.HARNESS,
            "the sub-agent's step output carried a HARNESS_BUG class",
        ),
        (
            re.compile(r"\bINFRA(_FAIL|_BLOCKED)?\b"),
            Owner.INFRA,
            "the sub-agent's step output carried an INFRA class",
        ),
    ),
}

#: Codes whose owner the number alone cannot give. Public so the registry
#: guard can assert the two tables agree about which those are.
DISCRIMINATED_EXITS: frozenset[int] = frozenset(_DISCRIMINATORS)


# ---------------------------------------------------------------------------
# Text-only rules, for a halt that has no exit code (a workflow's own message).
#
# There are exactly two today, and that is the honest size of the evidence the
# framework currently emits. Anything else is UNKNOWN — including the 93 halt
# sites whose message is "X did not PASS in N attempts", which say nothing
# about whose defect it is. Growing this table is how classification improves;
# guessing is not.
# ---------------------------------------------------------------------------
_TEXT_RULES: tuple[tuple[re.Pattern[str], str, str], ...] = (
    (
        re.compile(r"\[HARNESS-BUG\]"),
        Owner.HARNESS,
        "the [HARNESS-BUG] banner — harness's own crash boundary",
    ),
    # Registered in core/quality_gate/block_reason.py with the remediation
    # "Persistent failure is a harness defect (crash-triage --open-cr)": the
    # harness could not complete its OWN measurement. No exception, no crash
    # bundle — the one non-crash harness signal that already existed.
    (
        re.compile(r"crg_independent_failed"),
        Owner.HARNESS,
        "crg_independent_failed — the harness's own CRG measurement failed",
    ),
)


def classify_fault(
    *, exit_code: "int | None" = None, text: "str | None" = None
) -> FaultVerdict:
    """Name the owner of a failure, or say UNKNOWN out loud.

    The exit code is the stronger signal when present: it is written by the
    framework at the site that knows, whereas *text* may have travelled. So a
    supplied code decides, and *text* is consulted only to disambiguate a code
    that genuinely carries two owners, or when there is no code at all.
    """
    haystack = text or ""

    if exit_code is not None:
        rules = _DISCRIMINATORS.get(exit_code)
        if rules is not None:
            for pattern, owner, why in rules:
                if pattern.search(haystack):
                    return FaultVerdict(owner, f"exit {exit_code}: {why}")
            return FaultVerdict(
                Owner.UNKNOWN,
                f"exit {exit_code} carries two owners and the message did not "
                f"say which — see core/fault_owner.py::_DISCRIMINATORS",
            )
        registered = OWNER_BY_EXIT.get(exit_code)
        if registered is not None:
            return FaultVerdict(registered, f"exit {exit_code}")
        return FaultVerdict(
            Owner.UNKNOWN,
            f"exit {exit_code} is not registered in cli/exit_codes.py's REGISTRY",
        )

    for pattern, owner, why in _TEXT_RULES:
        if pattern.search(haystack):
            return FaultVerdict(owner, why)

    return FaultVerdict(
        Owner.UNKNOWN,
        "no exit code, and the message matched no registered attribution rule",
    )


def routes_to_harness_repair(verdict: FaultVerdict) -> bool:
    """Only a positive HARNESS verdict starts the repair workflow.

    老闆's ruling for Round 48: 先分類，只在判定為 harness 責任時觸發。
    UNKNOWN deliberately does NOT route here. Sending a repair agent at
    harness whenever the project's fault could not be proved would give it a
    standing motive to change the judge — the one direction every round of
    this framework has been built to refuse.
    """
    return verdict.owner == Owner.HARNESS
