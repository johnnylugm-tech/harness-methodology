"""wiring.py — which repair strategies are live, and which are refused (Round 48 站6).

`core/auto_fix/` is 2076 lines and registers 13 fix strategies behind 31
detector outcomes. Measured 2026-08-12, exactly ONE has a production caller:
`fix_missing_traceability`, reached from `core/phase_hooks.py:853` at P5+. The
other twelve are unreachable from any production path.

Round 30's pattern would say "wire them". Reading them says the opposite, and
that is the finding this module records:

    fix_keyword_density         appends a dimension's keywords to a markdown
                                file to raise that dimension's score
    fix_constitution_dimension  the same, keyed on the failing dimension
    fix_section_headers         appends "## <missing section>\\n\\nTBD"
    fix_hollow_content          appends TBD blocks to files under 200 bytes,
                                to satisfy the hollow-content checker
    fix_missing_artifact        writes a TBD stub for a missing deliverable
    fix_missing_aspice_docs     the same, for ASPICE docs
    fix_gap_critical            writes "<gap>_stub.md" with TBD criteria
    fix_drift                   appends an "AUTO-FIX: drift reconciliation
                                stub" comment and nothing else
    fix_low_coverage            writes `assert True` test stubs
    fix_pytest_failures         runs pytest, then rewrites failing assertions
                                to whatever value was observed

Every one of those makes a checker quiet without making its subject true. They
are the artifacts Rounds 27, 32, 42 and 46 exist to refuse — written by the
framework, at the framework's own initiative. Wiring them would be worse than
leaving them unreachable.

Two are retired for a different reason: `fix_missing_spec_tracking` duplicates
`core/traceability/spec_tracking_render.py` (Round 25 站2's renderer, already
wired into advance-phase) in a different output format, and
`fix_over_interpretation_gap` is honest about not auto-applying anything — its
own docstring explains that the choice between DERIVED / NFR-99 / verbatim is
semantic and belongs to the next A round — but it still has no caller.

RETIREMENT IS ENFORCED, NOT LABELLED

`AutoFixEngine.fix()` refuses a retired problem_type at the dispatch — after the
escalation ladder, so a retired strategy cannot shadow a kill-switch or
integrity-freeze escalation with its own refusal. A label that left the code
reachable would be the very shape this round is about: a statement with no
executor behind it.

ONE CONSEQUENCE, RECORDED RATHER THAN PAPERED OVER

`EscalationCondition.LOW_CONFIDENCE` fires when a strategy reports below the
confidence threshold. The one LIVE strategy always reports 90, and every
strategy that reported below 70 is retired — so after this round LOW_CONFIDENCE
is not reachable through `fix()` at all. Its unit tests now drive
`check_escalation` directly, which is the honest shape for a test of the ladder
anyway. Keeping a fabricating strategy wired up so one escalation stays
reachable would be the tail wagging the dog; see docs/PROPOSAL_ADJUDICATIONS.md.

WHAT THIS ROUND DELIBERATELY DID NOT DO

Delete the twelve functions and their 30 CLASSIFICATION_TABLE entries. Measured
blast radius: `tests/test_auto_fix.py`, `tests/test_no_hardcoded_paths.py` (whose
Round 20 站2 guard has `fix_low_coverage` as its SUBJECT — that guard is
registered in tests/REGRESSION_GUARDS.yaml and would be left describing a
function that no longer exists). Removing dead code whose absence unregisters a
guard is a subtraction round of its own; doing it as station 6 of eight would be
the runaway-refactor failure mode. Re-open condition: a dedicated pass that
retires the guard entry and the tests in the same commit as the code.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

__all__ = ["StrategySpec", "LIVE_STRATEGIES", "RETIRED_STRATEGIES", "is_retired"]


@dataclass(frozen=True)
class StrategySpec:
    """A strategy that is actually reachable, and what makes it trustworthy.

    `reverify` is not optional. Round 47's `env_repair` shape — detect, repair,
    **re-detect**, block with the true cause — is what separates a repair from
    a claim that one happened, and Round 24 named the alternative: a field
    existing is not the field being true.
    """

    caller: str
    reverify: "Callable | None"
    why: str


def _recheck_traceability():
    from core.traceability.scanner import check_traceability

    return check_traceability


LIVE_STRATEGIES: dict[str, StrategySpec] = {
    "missing_traceability": StrategySpec(
        caller="core/phase_hooks.py::repair_traceability_gap",
        reverify=_recheck_traceability,
        why=(
            "proposes a diff, applies it with `git apply --3way`, RE-RUNS "
            "check_traceability, and rolls every partial apply back when the "
            "bounded loop is exhausted. The only one of the thirteen that "
            "verifies its own repair rather than reporting that it wrote "
            "something"
        ),
    ),
}

RETIRED_STRATEGIES: dict[str, str] = {
    "low_keyword_density": (
        "appends a dimension's keywords to markdown so the constitution scorer "
        "counts them. That is score gaming performed by the framework itself; "
        "the score would then measure the repair, not the document"
    ),
    "low_constitution_score": (
        "same mechanism as low_keyword_density, keyed on whichever dimension "
        "failed. A dimension that rises because the framework pasted its "
        "keywords in has not improved"
    ),
    "missing_section_headers": (
        "appends '## <missing section>' followed by TBD. The checker asks "
        "whether the section exists; this makes the answer yes and the "
        "document no better"
    ),
    "hollow_content": (
        "appends TBD blocks to files under 200 bytes — directly against the "
        "purpose of the hollow-content check it is answering. Round 42 is the "
        "round that made 'declared but not delivered' say so per item"
    ),
    "missing_artifact": (
        "writes a TBD stub for a missing deliverable, which converts 'the "
        "artifact is absent' into 'the artifact is empty'. Round 42 站3 made a "
        "missing required deliverable a block rather than a warning; this "
        "would clear that block without delivering anything"
    ),
    "missing_aspice_docs": (
        "the same TBD-stub mechanism, for ASPICE documents"
    ),
    "gap_critical": (
        "writes '<gap>_stub.md' with TBD acceptance criteria. A critical gap "
        "closed by a file naming the gap is the gap, renamed"
    ),
    "drift_detected": (
        "appends '<!-- AUTO-FIX: drift reconciliation stub -->' to the spec "
        "and changes nothing else. Zero semantic content; it exists only to "
        "make the drift check stop reporting"
    ),
    "low_coverage": (
        "writes test stubs whose bodies are `assert True`. Coverage would rise "
        "and nothing would be tested — the exact artifact Round 46 站3 ruled a "
        "zero-row test suite is not a full score"
    ),
    "pytest_failures": (
        "runs pytest, parses the failures, and rewrites the failing assertion "
        "to the value that was actually observed. This is the single most "
        "dangerous of the thirteen: it makes a red suite green by changing "
        "what the tests claim"
    ),
    "missing_spec_tracking": (
        "duplicates core/traceability/spec_tracking_render.py (Round 25 站2), "
        "which is already wired into advance-phase, in a different output "
        "format. Two renderers for one artifact is how the two start "
        "disagreeing"
    ),
    "over_interpretation_gap": (
        "honest about not auto-applying anything — its own docstring explains "
        "the DERIVED / NFR-99 / verbatim choice is semantic and belongs to the "
        "next A round — but it has no production caller, so the proposal file "
        "it writes has no reader either"
    ),
    "hardcoded_secrets": (
        "classified HUMAN_REQUIRED and has no strategy function at all; listed "
        "here so the CLASSIFICATION_TABLE completeness check has an answer for "
        "it rather than a gap"
    ),
    "hard_rule_violation": (
        "classified HUMAN_REQUIRED (bypass commands, kill-switch, "
        "constitution-as-code). A hard rule that an automated fixer may clear "
        "is not a hard rule"
    ),
}


def is_retired(problem_type: str) -> bool:
    """True when this problem_type must NOT be dispatched to a fix strategy."""
    return problem_type in RETIRED_STRATEGIES
