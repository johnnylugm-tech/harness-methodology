"""The records a gate produces, the block it raises, and how to read them.

Round 82 站5. This module exists so that Round 82 站6 can move
`HarnessBridge`'s sixteen `_stage_*` methods into a mixin without a cycle.

Thirteen of those stages raise `GateBlockedError` and construct `GateResult`;
three more read `DimResult`, `SCORE_SOURCE_STUBBED_BOUNDARY`,
`declared_dimensions`, `measurement_scope` or `s4_block_details`. A mixin base
has to be imported before `class HarnessBridge(...)` executes, so the module
holding those methods cannot import back into harness_bridge — and the version
that "works" because every one of these definitions happens to sit above line
1750 works by line order, and stops working the day someone moves one down.
Round 80's re-open condition for `_crg_enrich_gate_findings` named this exact
remedy: 共用寫入器先被移到中立模組.

WHAT IS HERE AND WHAT IS NOT

Here: the two result records, the exception, the score-source vocabulary, and
the four functions that read a result and answer a question about it — which
dimensions this project declared, what the composite was averaged over, whether
the framework measured a dimension itself, and what a block should say.

Not here: `GateContext` (170 lines). No stage reads it, and moving code nothing
needs is how a "neutral module" becomes a second god file.

Everything is a byte-identical move, re-exported from harness_bridge, so
`from harness.harness_bridge import GateBlockedError` and the twelve other
existing import sites are unchanged. The four functions are fingerprinted by
tests/test_god_file_split_safety.py; the classes and constants are not, because
that mechanism reads `def`s.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class DimResult:
    """Result of a single quality dimension evaluation."""
    name: str
    score: Optional[float]
    threshold: float
    issues: list[dict] = field(default_factory=list)
    # Who produced `score` — one of the SCORE_SOURCE_* constants, or None for
    # a record written before Round 50 站2. None keeps the old meaning
    # (counted as measured); absence of the field is not evidence the number
    # was unverified, and no recorded verdict is re-judged.
    score_source: Optional[str] = None


@dataclass
class GateResult:
    """Summary result of a quality gate execution."""
    gate_num: int
    score: float
    dimensions: list[DimResult] = field(default_factory=list)
    open_critical: int = 0
    open_high: int = 0
    quality_complete: bool = False
    rounds_used: int = 0


# Score provenance, written into the gate-result breakdown by S4.
#
# Round 27 站1: a `score: null` used to mean "nobody has to check this". Five
# separate layers each waved it through — S4 skipped it, the weighted average
# dropped it from the denominator (redistributing its weight onto the usually
# perfect dimensions, so the composite went UP), and _all_dims_pass treated it as
# vacuously satisfying its own floor. taskq-plus's Gate 4 evidence shows the agent
# had found the door: "dimension N/A per protocol (not free 100)" — it knew a
# claimed score gets cross-validated and picked the path that did not.
#
# None now means "the FRAMEWORK has to check". Only a None the framework itself
# reproduced (SCORE_SOURCE_FRAMEWORK_NA) is a genuine not-applicable.
SCORE_SOURCE_FRAMEWORK = "framework"
SCORE_SOURCE_FRAMEWORK_NA = "framework_na"

# Round 50 站2. The vocabulary had two words for what the framework did and
# none for what it could not do, so "the agent claimed this and nothing
# checked it" had no way to be written down — and `measurement_scope`, which
# exists to publish what a composite was averaged over, counted such a number
# as covered quality surface because the field was not None.
#
# Measured on a real Gate 4: composite 95.2776 over `weight_covered: 1.0`,
# with `performance: 100.0` an agent value the framework had tried and failed
# to reproduce (the ledger row for that failure is in the same run's
# degradations.jsonl). One sixteenth of the weight was not measured and the
# denominator said otherwise.
#
# This marks the state; it does not create it. S4 blocks on an unverifiable
# dimension, so a verdict carrying this marker is one that reached the writer
# by some path that skipped the block — which is exactly the question the
# next reader will need answered, and the answer has to survive in the
# artifact rather than in a ledger line beside it.
SCORE_SOURCE_AGENT_UNVERIFIED = "agent_unverified"

# Round 51 站3. The framework ran the tool and reproduced the number, and the
# number is still not about the delivered code: the suite it ran over replaces
# a module the project's own SAB calls high-risk, before every test in the
# file, through an `autouse` fixture no test asked for.
#
# Measured across six projects: five have zero such fixtures, taskq-api has
# seventeen across ten files including both `*_e2e.py`. Its `test_coverage`
# scored 100.0 and `integration_coverage` 80.0 over a suite in which
# `repository.session.get_session` — a body that is one `raise RuntimeError` —
# is monkeypatched away in seven test modules.
#
# Round 46 站1's witness who did not appear; this is the witness who appeared
# as somebody else.
SCORE_SOURCE_STUBBED_BOUNDARY = "stubbed_boundary"

# The sources that are not "the framework measured the delivered code". Two
# readers select on this and they must select on the same set — a second `!=`
# comparison beside the first is how one of them comes to disagree with the
# other the next time a source is added.
_SOURCES_NOT_FRAMEWORK_MEASURED: frozenset[str] = frozenset({
    SCORE_SOURCE_AGENT_UNVERIFIED,
    SCORE_SOURCE_STUBBED_BOUNDARY,
})


def framework_measured(d: "DimResult") -> bool:
    """Did the framework measure this dimension over the delivered code?

    Round 50 站2: "has a number" is not "was measured". A score the framework
    tried to reproduce and could not (SCORE_SOURCE_AGENT_UNVERIFIED) is the
    agent's claim standing alone. Round 51 站3 added the second case: a number
    measured over a suite that replaced the thing it measures
    (SCORE_SOURCE_STUBBED_BOUNDARY). A score with no recorded source predates
    the field and keeps its old meaning.

    Round 67 站2 made this a function because it had become the answer to
    three questions asked in three places, and only one of them was asking it.
    `measurement_scope` selected on it; the composite's averaging loop and the
    per-dimension pass check each had their own `if d.score is None`, which is
    a different question with the same answer most of the time. Measured on
    taskq-cc's committed Gate 4: `weight_covered: 0.88` published beside a
    composite of 95.28 that recomputes exactly over weight 1.0, and a PASS
    verdict for a `test_coverage` of 100.0 whose suite stubs
    `taskq_api.service.auth` in five files.
    """
    return (d.score is not None
            and d.score_source not in _SOURCES_NOT_FRAMEWORK_MEASURED)


def declared_dimensions(project: "str | Path") -> list[str]:
    """The dimensions this project's quality manifest pins its NFRs to.

    Round 73 站5. Empty when there is no manifest or it cannot be read —
    could-not-measure is not a finding (Rounds 32/35), and reporting every
    gate dimension as "declared absent" would be the inversion of the check
    this feeds.
    """
    from core.state_io import load_quality_manifest
    try:
        manifest = load_quality_manifest(project, lenient=True)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        print(f"[WARN] quality_manifest.json unreadable ({type(exc).__name__}: "
              f"{exc}) — dimensions declared but absent from this gate are NOT "
              f"being reported this run", file=sys.stderr)
        return []
    mapping = manifest.get("nfr_dimension_mapping") if isinstance(manifest, dict) else None
    if not isinstance(mapping, dict):
        return []
    return sorted({str(v) for v in mapping.values() if v})


def measurement_scope(
    dims: "list[DimResult]",
    weights: "dict[str, float]",
    *,
    declared: "list[str] | None" = None,
) -> dict:
    """What the composite was averaged over — the denominator, beside the number.

    Round 42 站4. `harness/ssi/scripts/score.py:431` computes
    ``overall_score = weighted_sum / weight_sum`` where ``weight_sum``
    accumulates only the dimensions that were scored, so a dimension that
    produced no number RAISES the mean.

    Round 60 站2 removed the other way a dimension could leave the
    denominator — three feature flags that dropped it from the gate's list
    before scoring ever saw it — so what remains here is the honest kind:
    a dimension that was scored, and one that was not.

    Measured on the two projects that ran the same 494-line SPEC.md:
    taskq-plus published composite 98.707 over weight 0.86 (13 dimensions,
    mutation switched off and performance N/A); taskq-renew published 93.166
    over 1.00. Recomputing plus's number from `gate4_p6_full.yaml`'s weights
    reproduces the committed value to the last digit, so the arithmetic was
    never in question — but both numbers are published in
    `gate{N}_result.json` and in QUALITY_REPORT.md's
    ``| Gate 4 composite score | >= 85 | {value} |`` row with nothing saying
    what they were averaged over. A reader comparing them, which is what
    happened, compares 0.86 of the quality surface against 1.00 of it.

    Round 39 站2 made a disabled dimension visible in the ledger,
    `gate_verify.jsonl` and the quality manifest. It did not make it visible
    beside the number it moves, and `weight_covered` existed nowhere.
    Round 37's rule — the denominator travels with the number — one level up.

    This changes no verdict. A dimension that could not be measured is still
    not scored zero (Round 35), and a project may still switch one off: a JS
    project with no mutmut is a real case, and 站0 measured that the
    `SCORE_SOURCE_FRAMEWORK_NA` path cannot speak for it — that marker is set
    only where the framework RAN the tool, and a flag-disabled dimension never
    reaches that loop.
    """
    scored = sorted(d.name for d in dims if framework_measured(d))
    unscored = sorted(d.name for d in dims if not framework_measured(d))
    # Round 73 站5: the third list, one layer above the other two. Both of
    # those are built from the dimensions THIS GATE'S CONFIG produced, so a
    # dimension the config never mentions is neither — it is invisible.
    # taskq-new's quality_manifest pins `"NFR-06": "architecture_constraints"`,
    # which is legal (SPEC's own rule is that the value must be a key
    # DIMENSION_TOOLS has, and it is); that dimension appears only in
    # gate1_per_fr.yaml, so its Gate 4 published `weight_covered: 1.0` and
    # `dimensions_unscored: []` beside composite 94.59, a number a reader
    # takes for the whole quality surface.
    #
    # Non-blocking, deliberately: which dimensions a gate runs is a framework
    # decision — `architecture_constraints` is per-FR and its absence from
    # Gate 4 has a rationale — so blocking here would stop every project on a
    # choice none of them made. NFR-06's substantive judgement is Round 73
    # 站3's, which does block through `unconfigured_blocking_reason`. What is
    # fixed here is only that a dimension left out of the average must not
    # read as though it were in it (Round 37: the denominator travels with
    # the number).
    in_gate = {d.name for d in dims}
    return {
        "weight_covered": round(sum(weights.get(n, 0.0) for n in scored), 10),
        "weight_total": round(sum(weights.values()), 10),
        "dimensions_scored": scored,
        "dimensions_unscored": unscored,
        "dimensions_declared_absent": sorted(
            set(declared or []) - in_gate),
    }


def s4_block_details(fabrication: list, unverifiable: list) -> dict:
    """Map S4's two verdicts onto the block-reason keys that explain them.

    Round 32 站4. Public and pure so it can be checked without patching five
    private seams around finalize_gate — the private-patch ratchet
    (tests/test_patch_discipline.py) rejected the version of this test that
    did, and it was right to: what needs pinning is this mapping, not the
    call graph around it.

    The two keys carry opposite instructions, which is the whole reason they
    must not be merged:

      tool_score_fabrication  the harness measured, the agent's number was
                              false -> make the claim true or withdraw it
      infra_fail              the harness could not measure -> do NOT touch
                              the score; repair the tool run. Round 13's
                              routing keeps this out of a CODE-FIX round
                              against the project.
    """
    details: dict = {}
    if fabrication:
        details["tool_score_fabrication"] = fabrication
    if unverifiable:
        details["infra_fail"] = unverifiable
    return details


class GateBlockedError(Exception):
    """Exception raised when a quality gate fails to meet its targets."""
    def __init__(self, gate_num: int, result: GateResult, details: dict | None = None):
        self.gate_num = gate_num
        self.result = result
        self.details = details or {}
        msg = (
            f"Gate {gate_num} BLOCKED — score={result.score:.1f}, "
            f"critical={result.open_critical}, high={result.open_high}"
        )
        if details:
            for key, val in details.items():
                if isinstance(val, list):
                    msg += f"\n  {key}: {', '.join(str(v) for v in val[:3])}"
        super().__init__(msg)
