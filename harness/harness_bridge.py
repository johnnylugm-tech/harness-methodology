"""
Harness Bridge: Integration layer between the quality harness and the methodology.

Handles gate execution, results parsing, and quality manifest updates.
"""

from __future__ import annotations
import json
import re
import sys
import dataclasses
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover — annotation-only
    from core.quality_gate.gate1_evidence import FrCoverage

from harness.crg_bridge import CRGBridge
from harness.decision_log import DecisionLogWriter, DecisionLogEntry, DecisionContext
from harness.effort_tracker import EffortTracker, EffortRecord
from core.phase_topology import PER_FR_GATE1_PHASES
from core.quality_gate.constitution.profile import GateConfig
from core.utils.project_layout import ProjectLayout

try:
    from core.atomic_io import atomic_write_json  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover  (graceful degrade)
    atomic_write_json = None  # type: ignore[assignment]


def _first_non_null(d: dict, *keys: str, default):
    """Return the first key present in ``d`` with a non-null value.

    JSON `null` bypasses `dict.get(key, default)` — the default only
    applies when the key is absent, not when it's explicitly None — and
    an `or`-chain over multiple keys is wrong the other way (a legitimate
    `0` or `""` is falsy and would incorrectly fall through to the next
    key). This checks presence-and-not-None per key, in order.
    """
    for k in keys:
        v = d.get(k)
        if k in d and v is not None:
            return v
    return default


def path_escapes_root(candidate: Path, root: Path) -> bool:
    """True if `candidate` resolves to a location outside `root`.

    Shared containment check for agent-controlled path fields (tool_output,
    issue_registry_path, ...) so an agent writing `../../etc/passwd` (or an
    absolute path, or a symlink to outside) into a gate result JSON can't be
    silently read. May raise OSError/RuntimeError if resolution fails (e.g.
    a symlink loop) — callers catch those themselves since the message they
    surface differs per call site.
    """
    return not candidate.resolve().is_relative_to(root.resolve())


def _atomic_write_gate_result(path: Path, data: dict) -> None:
    """Atomic JSON write for gate_result.json (and any other
    pipeline-critical JSON state). Falls back to direct write if
    core.atomic_io is unavailable.
    """
    if atomic_write_json is not None:
        atomic_write_json(path, data)
    else:  # pragma: no cover
        path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )


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


# ---------------------------------------------------------------------------
# S3-A: Tool-output content patterns (Solution A)
# ---------------------------------------------------------------------------
# For each tool name, at least one pattern must match the file/inline content.
# Patterns use re.IGNORECASE | re.MULTILINE.
_TOOL_CONTENT_PATTERNS: dict[str, list[str]] = {
    "ruff": [
        r"All checks passed",          # clean run
        r"\S+\.pyi?:\d+:\d+:",         # file:line:col violation line
        r"Found \d+ error",            # summary
        r"\[[\w-]+\]",                 # rule code like [E501] or [ruff]
    ],
    "mypy": [
        r"Success: no issues found",
        r"Found \d+ error",
        r"\.pyi?:\d+: (error|note):",
    ],
    "pytest-cov": [
        r"\d+ passed",
        r"TOTAL\s+\d+",
        r"coverage:",
        r"Coverage report",
    ],
    "pytest": [
        r"\d+ passed",
        r"\d+ failed",
        r"no tests ran",
        r"={3,}",                      # pytest separator bars
    ],
    "gitleaks": [
        r"No leaks found",
        r"Secret",
        r"leaks?\s+found",
        r"gitleaks",
        r"WRN\[",                      # gitleaks warning format
        r"INF\[",                      # gitleaks info format
    ],
    "mutmut": [
        r"Killed",
        r"Survived",
        r"mutation score",
        r"mutmut",
    ],
    "scancode": [
        r"license",
        r"SPDX",
        r"copyright",
        r"scan:",
    ],
    # ── JS/TS toolchain (resolved tool ids) ─────────────────────────────────
    "eslint": [
        r'"filePath"',                 # -f json per-file result objects
        r'"errorCount"',
        r'"messages"',
    ],
    # Clean tsc compiles emit nothing — evaluate_dimension.md instructs agents
    # to append `echo "tsc exit=$?"` so clean evidence still carries a marker.
    "tsc": [
        r"error TS\d+:",               # diagnostic lines
        r"tsc exit=\d",
    ],
    "tsc-checkjs": [
        r"error TS\d+:",
        r"tsc exit=\d",
    ],
    "semgrep-js": [
        r'"results"',                  # --json envelope
        r'"check_id"',
        r"semgrep",
    ],
    "vitest-cov": [
        r'"total"',                    # coverage-summary.json artifact
        r"%\s*(Stmts|Lines)",          # text reporter table header
        r"Coverage report",
        r"\d+ passed",
    ],
    "jest-cov": [
        r'"total"',
        r"%\s*(Stmts|Lines)",
        r"Tests:\s+\d+",
    ],
    "vitest-cov-integration": [
        r'"total"',
        r"%\s*(Stmts|Lines)",
        r"\d+ passed",
    ],
    "jest-cov-integration": [
        r'"total"',
        r"%\s*(Stmts|Lines)",
        r"Tests:\s+\d+",
    ],
    "js-bench": [
        r'"benchmarks"',               # normalized run.mjs JSON
        r'"mean_ms"',
    ],
    "stryker": [
        r"mutation score",
        r'"mutationScore"',            # mutation.json report
        r"Killed",
        r"Survived",
        r"stryker",
    ],
    # ── Round 27 站1: the 15 tools that had no pattern at all ────────────────
    # Without an entry here _validate_tool_content skips check 3 entirely, so any
    # prose ≥ _TOOL_OUTPUT_MIN_BYTES that does not start with '#' passed as tool
    # evidence. taskq-plus's Gate 4 shipped "NFR-08 satisfied contractually
    # (harness surface exists)" and "dimension N/A per protocol" as the evidence
    # for two dimensions on exactly this gap. `code-review-graph` is deliberately
    # NOT listed: architecture is framework-owned (crg_independent computes it in
    # finalize_gate), so a framework sentence IS its legitimate evidence.
    "bandit": [
        r'"results"',                  # -f json envelope
        r'"issue_severity"',
        r'"metrics"',
        r"No issues identified",
    ],
    "pyright": [
        r'"generalDiagnostics"',       # --outputjson envelope
        r'"summary"',
        r'"errorCount"',
        r"\d+ errors?, \d+ warnings?",
    ],
    "pytest-benchmark": [
        r"Name\s+\(time in ",          # benchmark table header
        r"-{3,}\s*benchmark:",         # "----------- benchmark: 2 tests -----------"
        r"no tests ran",
        r"\d+ passed",
        # Two drafts were rejected here by test_prose_is_not_tool_evidence, both
        # against taskq-plus's actual N/A sentence: a bare r"benchmark" (the word
        # appears in "pytest-benchmark" and "--benchmark-only") and then
        # r"-+\s*benchmark[:\s]", which still matched the "-benchmark " inside
        # "pytest-benchmark tests". Only the real separator rule — three or more
        # dashes AND the colon — describes output the tool alone produces.
    ],
    "pytest-cov-integration": [
        r"\d+ passed",
        r"TOTAL\s+\d+",
        r"coverage:",
        r"no tests ran",
    ],
    "import-linter": [
        r"Contracts?:",                # "Contracts: 3 kept, 0 broken."
        r"\d+ (kept|broken)",
        r"lint-imports",
        r"not found in project root",  # tool_runners' required_config_file message
    ],
    "system-verification": [
        r"verify-system",              # the target's own name in make output
        r"make(\[\d+\])?:",
        r"exit=?\s*\d",
    ],
    # In-process AST/tree-sitter scanners (harness/lang_scanners/) emit a JSON
    # object per dimension — same schema across languages, hence one pattern list
    # shared by the python and js tool ids.
    "ast-assertions": [r'"total"', r'"asserted"', r'"zero_assert"'],
    "js-assertions": [r'"total"', r'"asserted"', r'"zero_assert"'],
    "ast-error-handling": [r'"with_handler"', r'"no_handler"', r'"anti_patterns"'],
    "js-error-handling": [r'"with_handler"', r'"no_handler"', r'"anti_patterns"'],
    "ast-docstrings": [r'"documented"', r'"total"', r'"missing"', r"^\{\}$"],
    "js-doc-coverage": [r'"documented"', r'"total"', r'"missing"', r"^\{\}$"],
    "readability-v2": [r'"project_score"', r'"files"', r'"lloc"'],
    "js-mi": [r'"project_score"', r'"files"', r'"lloc"'],
}

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

# Dimensions the framework scores itself inside finalize_gate (crg_independent /
# community_cohesion) rather than by re-running the `tool:` named in the gate
# YAML. S4 skips them, so they are also outside the set whose None the verdict
# layer demands the framework verified — one definition for both readers.
_CRG_OWNED_DIMENSIONS: frozenset[str] = frozenset({"architecture"})


def na_is_framework_verified(dim_data: dict) -> bool:
    """True when this dimension's absent score was reproduced by the framework.

    The single predicate behind both the weighted average and the pass/fail
    verdict, for the same reason `_effective_threshold` is single: two copies of
    "is this None allowed" is two chances to drift apart.
    """
    return dim_data.get("score_source") == SCORE_SOURCE_FRAMEWORK_NA


def _mark_framework_na(dim_entry: dict, tool: str, returncode: int) -> None:
    """Record that the framework itself reproduced this dimension's absent score."""
    dim_entry["score"] = None
    dim_entry["score_source"] = SCORE_SOURCE_FRAMEWORK_NA
    dim_entry["na_verified_by"] = f"{tool} (rc={returncode})"


def measurement_scope(
    dims: "list[DimResult]",
    weights: "dict[str, float]",
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
    # Round 50 站2: "has a number" is not "was measured". A score the
    # framework tried to reproduce and could not (SCORE_SOURCE_AGENT_UNVERIFIED)
    # is the agent's claim standing alone, and counting its weight as covered
    # is the denominator overstating itself. A score with no recorded source
    # predates this field and keeps its old meaning.
    scored = sorted(
        d.name for d in dims
        if d.score is not None
        and d.score_source not in _SOURCES_NOT_FRAMEWORK_MEASURED
    )
    unscored = sorted(
        d.name for d in dims
        if d.score is None
        or d.score_source in _SOURCES_NOT_FRAMEWORK_MEASURED
    )
    return {
        "weight_covered": round(sum(weights.get(n, 0.0) for n in scored), 10),
        "weight_total": round(sum(weights.values()), 10),
        "dimensions_scored": scored,
        "dimensions_unscored": unscored,
    }

# Minimum byte size for a tool_output file to be considered non-stub.
# Real tool output is always larger than this; pure comment lines are typically
# under 80 bytes.
#
# Round 27 站1 considered raising this and did NOT: the shortest real output a
# registered tool produces is `{}` / `[]` (2 bytes) — ast-docstrings with nothing
# to document, ruff on a clean run — so any floor high enough to reject a prose
# stub also rejects those. The check that actually rejects a stub is check 3, the
# per-tool content pattern, which this round extended from 17 tools to 31 (every
# tool except framework-owned code-review-graph).
_TOOL_OUTPUT_MIN_BYTES: int = 5


def _override_traceability_dim_score(
    dims: list,
    project_root: str,
    gate_num: int,
) -> "tuple[list[DimResult], bool]":
    """PR 4 (audit F-1.1 fix): replace the agent's `traceability` score
    with the framework-computed one. Returns (new_dims, changed) where
    changed=True means the traceability score was actually modified.
    The input is not mutated. On any error, returns (input, False).

    Why: the agent has no tool to scan SAD.md + [FR-XX] annotations +
    test references. Without this override, the agent either reports
    a wrong score (optimistic or pessimistic) or omits the dim entirely.
    The framework-computed `compute_trace_dimension` is the source of
    truth. This mirrors the architecture CRG override pattern (above).
    """
    try:
        from core.quality_gate.spec_tracking_checker import (
            compute_trace_dimension,
        )
        _trace_dim = compute_trace_dimension(project_root, gate_num)
    except Exception as _trace_err:
        print(
            f"[WARN] trace dimension override skipped: {_trace_err}",
            file=sys.stderr,
        )
        return dims, False
    if _trace_dim.get("error"):
        return dims, False
    _framework_score = _trace_dim["merged_pct"]
    _new_dims = []
    _changed = False
    for _d in dims:
        if _d.name == "traceability":
            if _d.score is not None and abs(_d.score - _framework_score) > 0.5:
                print(
                    f"[harness] trace override traceability: "
                    f"{_d.score:.1f} → {_framework_score:.1f} "
                    f"(4a={_trace_dim['4a_fr_to_test_pct']:.1f}% "
                    f"4b={_trace_dim['4b_test_spec_pct']:.1f}%)"
                )
            if _d.score != _framework_score:
                _changed = True
            # `_d.threshold` may already carry a gate_score_overrides floor-raise
            # (harness_bridge.py's "never lower a threshold, only raise it"
            # invariant, applied to dims before this override runs) — take the
            # max so a project-level override for this dim is never silently
            # discarded by the framework's own recomputed threshold_effective.
            _new_threshold = max(_trace_dim["threshold_effective"], _d.threshold)
            _new_dims.append(dataclasses.replace(
                _d,
                score=_framework_score,
                threshold=_new_threshold,
            ))
        else:
            _new_dims.append(_d)
    return _new_dims, _changed


def _override_adversarial_review_dim_score(
    dims: list,
    project_root: str,
    config_dim_list: list,
) -> "tuple[list[DimResult], bool]":
    """v2.9 C2: framework-owned adversarial_review score (Gate 3).

    Mirrors the traceability override pattern, with one strengthening: when
    the agent omits the dimension from its breakdown, the DimResult is
    APPENDED, not skipped — a framework-owned blocking dimension must not
    depend on agent cooperation to exist. Verdict comes from
    bug_hunt_verifier (report present + every confirmed critical/high
    resolved-with-evidence or refuted-with-evidence → 100; else 0).

    Only acts when the gate config declares the dimension. Returns
    (new_dims, changed); never mutates the input.
    """
    declared = any(
        (d.get("name") if isinstance(d, dict) else getattr(d, "name", ""))
        == "adversarial_review"
        for d in config_dim_list
    )
    if not declared:
        return dims, False

    try:
        from core.quality_gate.bug_hunt_verifier import verify_bug_hunt_report
        verdict = verify_bug_hunt_report(project_root)
    except Exception as _bh_err:
        print(
            f"[WARN] adversarial_review override skipped: {_bh_err}",
            file=sys.stderr,
        )
        return dims, False

    for reason in verdict.reasons[:10]:
        print(f"[harness] adversarial_review: {reason}")
    if verdict.stale:
        print(
            "[harness] adversarial_review: report git_sha differs from HEAD — "
            "content still gates, but consider re-running the hunt on large diffs"
        )

    issues = [{"severity": "high", "message": r} for r in verdict.reasons[:20]]
    _new_dims = []
    _changed = False
    _found = False
    for _d in dims:
        if _d.name == "adversarial_review":
            _found = True
            if _d.score != verdict.score:
                print(
                    f"[harness] adversarial_review override: "
                    f"{(_d.score or 0.0):.1f} → {verdict.score:.1f} "
                    f"(open_blocking={verdict.open_blocking})"
                )
                _changed = True
            _new_dims.append(
                dataclasses.replace(_d, score=verdict.score, issues=issues)
            )
        else:
            _new_dims.append(_d)
    if not _found:
        _new_dims.append(DimResult(
            name="adversarial_review", score=verdict.score,
            threshold=100.0, issues=issues,
            score_source=SCORE_SOURCE_FRAMEWORK,
        ))
        _changed = True
        print(
            f"[harness] adversarial_review appended (agent omitted it): "
            f"score={verdict.score:.1f}"
        )
    return _new_dims, _changed


# ---------------------------------------------------------------------------
def _mutation_artifact_violations(
    ctx: "GateContext", dim_name: str, agent_score: "float | None",
    threshold: float,
) -> "tuple[list[str], list[str]]":
    """S4 for mutation_testing: the score is the framework's, or the gate blocks.

    Round 31 站2. mutmut is the one tool the framework runs end-to-end itself —
    temp workdir, setup.cfg rewrite, interpreter pinning, scope from the SAB —
    and `compute_mutation_score` is where the authoritative number comes out of
    the sqlite cache. It had zero production callers. What reached a live
    Gate 2 instead was an agent-written prose file that passed content
    validation because the mutmut pattern list contains the bare word "mutmut",
    carrying a number nothing could check.

    So the framework's own artifact is the source. Returns
    ``(fabrication, unverifiable)`` — Round 35 站3 split them, because the
    outcomes carry opposite instructions and all of them used to be filed as
    `tool_score_fabrication`, whose registered remediation reads "the score,
    not the run, is what failed — do NOT re-run". For a missing artifact the
    correct action is precisely to run the command.

    * absent / unreadable / malformed → `infra_fail`, naming the command that
      writes it. Abstaining is not passing (Round 30): "we could not establish
      the score" must never resolve to "the claimed score stands".
    * present with `score: null` → `infra_fail`, carrying the reason the
      framework recorded. It ran and could not measure; nothing about the
      project's tests has been established (Round 35 站2).
    * present, and the framework's score clears the threshold → fine, whatever
      the agent wrote; the caller patches the real number into the breakdown.
    * present, framework's score BELOW threshold while the agent claimed a
      pass → `tool_score_fabrication`. That is the same rule S4 applies to
      every other tool (harness says fail, agent says pass), with the artifact
      standing in for a re-run that would cost an hour.
    """
    from core.quality_gate.mutation_enforcer import MUTATION_SCORE_ARTIFACT

    _how = (
        f"Run `python3 harness_cli.py mutation-test-score --project .` — it "
        f"runs mutmut with the framework's workdir isolation and writes "
        f"{MUTATION_SCORE_ARTIFACT}. Do not run `mutmut run` yourself."
    )
    path = Path(ctx.project_root) / MUTATION_SCORE_ARTIFACT
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        raw_score = data["score"]
        framework_score = None if raw_score is None else float(raw_score)
    except FileNotFoundError:
        return [], [
            f"{dim_name}: no framework-computed score — {MUTATION_SCORE_ARTIFACT} "
            f"is missing, so the recorded score is whatever the agent wrote. "
            f"{_how}"
        ]
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        return [], [
            f"{dim_name}: {MUTATION_SCORE_ARTIFACT} is unreadable ({exc}) — the "
            f"score it should carry cannot be established. {_how}"
        ]

    # Round 31 站4: the score is only meaningful over the scope it was taken
    # on. The generator runs once at the P2→P3 handoff, so a SAB corrected
    # mid-P3 — the normal way a missing scope_layers gets noticed, since Gate 2
    # is where the cost shows up — leaves setup.cfg saying something else.
    #
    # Round 35 站3: this used to sit below the null check, so on the one
    # occasion the scope is the likeliest cause — a run that could not measure
    # — it was never evaluated. Measured on taskq-renew, where setup.cfg
    # declares no scope while the SAB names two layers, and nothing said so.
    from core.quality_gate.mutmut_scope import scope_drift
    drift = scope_drift(ctx.project_root)
    if drift:
        return [], [f"{dim_name}: mutation scope disagrees with the SAB — {drift}"]

    if framework_score is None:
        return [], [
            f"{dim_name}: the framework ran mutmut and could not measure — "
            f"{data.get('could_not_measure') or 'no reason recorded'}. Nothing "
            f"has been established about this project's tests, so do not touch "
            f"the score: repair the run. {_how}"
        ]

    if framework_score < threshold and (
        agent_score is None or agent_score >= threshold
    ):
        _claim = "N/A (agent)" if agent_score is None else f"{agent_score:.1f}"
        return [
            f"{dim_name}: framework-computed score {framework_score:.1f} is "
            f"below threshold {threshold:.0f}, but the gate result claims "
            f"{_claim}. The framework's own run is the score for this "
            f"dimension; write {framework_score:.1f} and fix the tests that "
            f"let those mutants live."
        ], []

    print(
        f"  [S4] {dim_name}: framework-computed score {framework_score:.1f} "
        f"[scope: {data.get('paths_to_mutate', '?')}, "
        f"{data.get('mutated_files', '?')} files] ✓"
    )
    return [], []


def _validate_tool_content(
    content: str,
    tool: str | None,
    dim_name: str,
    *,
    inline: bool,
) -> list[str]:
    """S3-A: Verify that *content* looks like genuine tool output.

    Checks (in order):
      1. Minimum size (file only — inline snippets are expected to be short)
      2. Comment-header stub detection (applies to both file and inline)
      3. Tool-specific structural pattern match (applies to both)

    Returns list of violation messages (empty = OK).
    """
    violations: list[str] = []

    # 1. Minimum size (file only)
    if not inline:
        size = len(content.encode("utf-8"))
        if size < _TOOL_OUTPUT_MIN_BYTES:
            violations.append(
                f"{dim_name}: tool_output file is too small ({size} bytes) — "
                f"likely a stub; real tool output is at least {_TOOL_OUTPUT_MIN_BYTES} bytes"
            )
            return violations  # Early exit — no point checking further

    # 2. Comment-header stub detection
    first_nonblank = next((ln for ln in content.splitlines() if ln.strip()), "")
    if first_nonblank.strip().startswith("#"):
        kind = "tool_evidence" if inline else "tool_output"
        violations.append(
            f"{dim_name}: {kind} starts with '#' comment — "
            f"this is a stub marker, not genuine tool output"
        )
        return violations  # Early exit

    # 3. Tool-specific structural pattern
    if tool and tool in _TOOL_CONTENT_PATTERNS:
        patterns = _TOOL_CONTENT_PATTERNS[tool]
        if not any(
            re.search(p, content, re.IGNORECASE | re.MULTILINE)
            for p in patterns
        ):
            kind = "tool_evidence" if inline else "tool_output"
            violations.append(
                f"{dim_name}: {kind} does not match any expected output pattern for "
                f"'{tool}' — content may not be genuine {tool} output"
            )

    return violations


def _crg_enrich_gate_findings(
    crg: "CRGBridge",
    dims: list,
    project_root: str,
    work_dir: str,
    gate_num: int,
) -> "tuple[list[DimResult], bool]":
    """CRG MCP enrichment: append findings to DimResult.issues and gate_result.json.

    Returns (new_dims, score_overridden) where score_overridden=True means the
    Phase 2 hub penalty (step 9) actually changed a test_coverage score.
    Never raises. All enrichment degrades gracefully when MCP is unavailable
    (CRGBridge._check_available() returns False inside each method).
    Mostly evidence-only, with ONE score override (Phase 2 gatekeeper):
      Step 9 (query_graph tests_for): applies a score penalty to test_coverage
      when critical hub functions (fan_in≥8) have no TESTED_BY edge.
      All other steps write to DimResult.issues or gate_result.json only.

    Wired tools (9 enrichment points):
      Architecture: find_large_functions, get_hub_nodes, check_dead_code
      Review context: get_review_context, get_impact_radius, get_affected_flows
      Test coverage: get_knowledge_gaps, query_graph(tests_for) [score override]
      Error handling: list_flows (critical flow list)
    """
    result_path = Path(work_dir) / f"gate{gate_num}_result.json"

    # ── 1. find_large_functions → architecture findings ──────────────
    lf_data = crg.find_large_functions(project_root, min_lines=300, kind="Function")
    large_fns = (lf_data or {}).get("results", [])
    warn_items = [
        f"{r['name']} ({r.get('line_count', '?')} lines, {r.get('relative_path', '?')})"
        for r in large_fns
        if (r.get("line_count") or 0) >= 300
    ]
    if warn_items:
        for _i, _d in enumerate(dims):
            if _d.name == "architecture":
                # Build a NEW DimResult instead of mutating caller's object —
                # downstream overrides rely on the original list being untouched.
                new_issue = {
                    "severity": "medium",
                    "message": (
                        f"Large functions detected (≥300 lines): {len(warn_items)} function(s). "
                        "Refactor to improve cohesion."
                    ),
                    "evidence": "; ".join(warn_items[:5]),
                    "source": "crg:find_large_functions",
                }
                dims[_i] = dataclasses.replace(_d, issues=_d.issues + [new_issue])

    # ── 2. get_hub_nodes → architecture findings (re-used in step 8) ──
    hub_data = crg.get_hub_nodes(project_root, min_fan_in=8)
    hub_hubs = (hub_data or {}).get("hubs", [])
    critical_hubs = [h for h in hub_hubs if (h.get("fan_in") or 0) >= 15]
    if critical_hubs:
        for _i, _d in enumerate(dims):
            if _d.name == "architecture":
                new_issue = {
                    "severity": "high",
                    "message": (
                        f"Critical hub nodes (fan_in≥15): {len(critical_hubs)} found. "
                        "Single-point failure risk."
                    ),
                    "evidence": "; ".join(
                        f"{h.get('name')} (fan_in={h.get('fan_in')})"
                        for h in critical_hubs[:5]
                    ),
                    "source": "crg:get_hub_nodes",
                }
                dims[_i] = dataclasses.replace(_d, issues=_d.issues + [new_issue])

    # ── 3. check_dead_code → architecture findings ────────────────────
    dc_data = crg.check_dead_code(project_root, kind="Function")
    dead_items = (dc_data or {}).get("dead_code", [])
    prod_dead = [x for x in dead_items if "/tests/" not in (x.get("file") or "")]
    if len(prod_dead) > 10:
        # Severity based on absolute count — total_nodes not available here without
        # an extra MCP call. >20 is reliably > 5% of any non-trivial project,
        # matching crg_analysis.DEAD_CODE_ESCALATE_RATIO intent.
        sev = "medium" if len(prod_dead) > 20 else "low"
        for _i, _d in enumerate(dims):
            if _d.name == "architecture":
                new_issue = {
                    "severity": sev,
                    "message": (
                        f"Dead code: {len(prod_dead)} unreferenced functions/classes "
                        "detected in production code."
                    ),
                    "evidence": "; ".join(
                        x.get("name", "?") for x in prod_dead[:5]
                    ),
                    "source": "crg:refactor_tool(dead_code)",
                }
                dims[_i] = dataclasses.replace(_d, issues=_d.issues + [new_issue])

    # ── 4. get_review_context → crg_review_context in gate_result ─────
    rc = crg.get_review_context(project_root, detail_level="minimal")
    if rc and result_path.exists():
        try:
            _gr = json.loads(result_path.read_text(encoding="utf-8"))
            _gr["crg_review_context"] = rc
            _atomic_write_gate_result(result_path, _gr)
        except (OSError, json.JSONDecodeError):
            pass

    # ── 5. get_impact_radius → crg_impact_radius in gate_result ───────
    ir = crg.get_impact_radius(project_root, detail_level="minimal")
    if ir and result_path.exists():
        try:
            _gr = json.loads(result_path.read_text(encoding="utf-8"))
            _gr["crg_impact_radius"] = ir
            _atomic_write_gate_result(result_path, _gr)
        except (OSError, json.JSONDecodeError):
            pass

    # ── 6. get_affected_flows → crg_affected_flows in gate_result ─────
    af = crg.get_affected_flows(project_root)
    flows = (af or {}).get("affected_flows", [])
    if flows and result_path.exists():
        try:
            _gr = json.loads(result_path.read_text(encoding="utf-8"))
            _gr["crg_affected_flows"] = {
                "total": len(flows),
                "flows": [
                    {
                        "name": f.get("name"),
                        "criticality": f.get("criticality"),
                    }
                    for f in flows[:10]
                ],
            }
            _atomic_write_gate_result(result_path, _gr)
        except (OSError, json.JSONDecodeError):
            pass

    # ── 7. get_knowledge_gaps → test_coverage findings ─────────────────
    kg_data = crg.get_knowledge_gaps(project_root)
    kg_gaps = (kg_data or {}).get("gaps", [])
    untested_gaps = [
        g for g in kg_gaps
        if "test" in str(g.get("type", "")).lower()
        or "untested" in str(g.get("description", "")).lower()
    ][:5]
    if untested_gaps:
        for _i, _d in enumerate(dims):
            if _d.name == "test_coverage":
                new_issue = {
                    "severity": "medium",
                    "message": (
                        f"CRG knowledge gaps: {len(untested_gaps)} untested critical path(s) detected."
                    ),
                    "evidence": "; ".join(
                        g.get("name") or g.get("description", "?")
                        for g in untested_gaps
                    ),
                    "source": "crg:get_knowledge_gaps",
                }
                dims[_i] = dataclasses.replace(_d, issues=_d.issues + [new_issue])

    # ── 8. list_flows → error_handling context + crg_critical_flows ───
    flow_data = crg.list_flows(project_root, limit=10, sort_by="criticality")
    crit_flows = (flow_data or {}).get("flows", [])
    if crit_flows:
        for _i, _d in enumerate(dims):
            if _d.name == "error_handling":
                new_issue = {
                    "severity": "low",
                    "message": (
                        f"Top {len(crit_flows)} critical execution flows — "
                        "verify each has error handling coverage."
                    ),
                    "evidence": "; ".join(
                        f"{f.get('name')}(crit={f.get('criticality', 0):.2f})"
                        for f in crit_flows[:5]
                    ),
                    "source": "crg:list_flows",
                }
                dims[_i] = dataclasses.replace(_d, issues=_d.issues + [new_issue])
        if result_path.exists():
            try:
                _gr = json.loads(result_path.read_text(encoding="utf-8"))
                _gr["crg_critical_flows"] = crit_flows[:10]
                _atomic_write_gate_result(result_path, _gr)
            except (OSError, json.JSONDecodeError):
                pass

    # ── 9. query_graph(tests_for) → test_coverage score override (Phase 2 gatekeeper) ──
    # Fan_in ≥ 8 hubs with no TESTED_BY edge = confirmed structural blind spot.
    # CRG MCP is a required install (same tier as ruff/mypy), so this score
    # penalty is reliable. Penalty: 3 pts per untested critical hub, capped at 15.
    # Falls back to advisory-only when CRG MCP is unavailable (crg._check_available()=False),
    # which can only happen when harness runs as a bare subprocess outside Claude Code.
    high_hubs = [h.get("name") for h in hub_hubs if (h.get("fan_in") or 0) >= 8][:5]
    untested_hubs = []
    for fn_name in high_hubs:
        if not fn_name:
            continue
        res = crg.query_graph(project_root, "tests_for", fn_name)
        if not (res or {}).get("results"):
            untested_hubs.append(fn_name)
    _score_overridden = False
    if untested_hubs:
        _hub_penalty = min(len(untested_hubs) * 3, 15)
        # Index-based replace (same pattern as steps 1/2/3/7/8 above) rather
        # than a rebuild-into-_new_dims loop — the rebuild variant used to
        # depend on step 7's in-place `dims[_i] = ...` for test_coverage
        # having already run first, since it read from the same `dims` list
        # object. Index-based replace has no such ordering dependency.
        for _i, _d in enumerate(dims):
            if _d.name == "test_coverage":
                new_issue = {
                    "severity": "high",
                    "message": (
                        f"Hub functions with no test linkage: {len(untested_hubs)} found. "
                        f"Penalising test_coverage by {_hub_penalty} pts (Phase 2 gatekeeper)."
                    ),
                    "evidence": "; ".join(untested_hubs),
                    "source": "crg:query_graph(tests_for)",
                }
                _new_score = round(max(0.0, (_d.score or 0.0) - _hub_penalty), 1)
                print(
                    f"[harness] CRG hub penalty test_coverage: {(_d.score or 0.0):.1f} → "
                    f"{_new_score:.1f} "
                    f"(-{_hub_penalty} for {len(untested_hubs)} untested critical hub(s))"
                )
                if _new_score != _d.score:
                    _score_overridden = True
                # Combine issue-add and score-change into ONE replace to avoid two passes
                # over the original DimResult and to keep `issues` as a fresh list.
                dims[_i] = dataclasses.replace(
                    _d, score=_new_score, issues=_d.issues + [new_issue]
                )

    return dims, _score_overridden


# Round 12 站3b — infra-failure signatures inside a dimension's evidence.
# When run-gate's PRECONDITIONS block (SAB phantom/unregistered modules,
# manifest corruption), the gate evaluator agent used to follow its STOP
# Round N (2026-07): tighten the INFRA-fail signature registry. The old
# list contained `[BLOCKED] run-gate` — a generic run-gate prefix that
# appears not only on real INFRA failures (SAB phantom blocks) but also
# in any context where a sub-agent mentions or quotes gate1 output that
# contained that string. The classic false positive (taskq-plus FR-05 P3
# 2026-07): a workflow sub-agent reading its own GATE1 log and quoting
# the `[BLOCKED] run-gate` line in its report caused
# `_classify_infra_or_harness_bug` to mark the dispatch as INFRA and
# `_abort_dispatch_infra_or_harness_bug` to escalate to human — discarding
# a real Gate 1 PASS verdict (8/8 dimensions had evaluated and PASSed
# via the direct CLI). The remaining four signatures are specific to the
# Architecture Amendment Protocol pathway — they cannot appear in
# incidental context because the wording is framework-internal to
# harness-methodology. The original 2026-07-16 incident (three dimensions
# uniformly zeroed by a taskq.storage.store phantom block) is still
# caught: the dimensions' evidence carried both
# "Architecture Amendment Protocol violation" AND
# "Unregistered modules detected" — both retained signatures.
_INFRA_FAIL_EVIDENCE_SIGNATURES = (
    "Architecture Amendment Protocol violation",
    "Unregistered modules detected",
    "phantom module",
    "Phantom modules",
)


def _check_infra_fail_pollution(raw: dict) -> list[str]:
    """Round 12 站3b: INFRA_FAIL ≠ quality failure.

    A zero score whose evidence carries a run-gate PRECONDITION-block
    signature is not a measurement — the tool never ran. Writing it into
    the manifest as a quality zero poisons scoring history and dispatches
    code fixers at a non-code problem. Detect and reject the result
    outright so finalize-gate FATALs with an infra diagnosis instead.

    Round N: partial-pollution carve-out. If at least ONE evaluated dimension
    produced a real (non-zero) score with non-INFRA-pollution evidence, the
    run-gate DID execute end-to-end; the other dimensions' INFRA-block
    zeros are partial pollution (one SAB-phantom dimension aborts the run
    while the rest still score normally) and the whole verdict must NOT
    be blanket-rejected. The per-dim diagnostic message still surfaces via
    the partial-pollution diagnostics list so operators see the affected
    dimensions, but `finalize-gate` proceeds with the real PASS record for
    the cleanly-evaluated dimensions. Incident: taskq-plus FR-05 P3 (2026-07)
    — GATE1 hit `[BLOCKED] run-gate` for the SAB phantom dimension while
    7/8 other dimensions evaluated normally; blanket rejection discarded
    a real Gate 1 PASS verdict and the workflow escalated to human on
    false-positive grounds.
    """
    entries: list[tuple[str, float | None, str]] = []
    breakdown = raw.get("breakdown")
    if isinstance(breakdown, dict):
        for dim, row in breakdown.items():
            if isinstance(row, dict):
                _ev = " ".join(str(row.get(k, "")) for k in ("tool_evidence", "evidence"))
                entries.append((str(dim), row.get("score"), _ev))
    for row in raw.get("dimensions", []) or []:
        if isinstance(row, dict):
            _ev = " ".join(str(row.get(k, "")) for k in ("tool_evidence", "evidence"))
            entries.append((str(row.get("name", "?")), row.get("score"), _ev))
    # Partial-pollution carve-out: at least one dimension passed cleanly
    # (non-zero score AND its evidence contains no INFRA-fail signature).
    # When present, the gate DID run end-to-end — accept the verdict and
    # surface partial-pollution info via diagnostics rather than rejecting.
    has_real_pass = any(
        (score not in (0, 0.0, None))
        and not any(sig in (evidence or "") for sig in _INFRA_FAIL_EVIDENCE_SIGNATURES)
        for _, score, evidence in entries
    )
    violations: list[str] = []
    partial_diagnostics: list[str] = []
    for dim, score, evidence in entries:
        if not evidence:
            continue
        matched = [sig for sig in _INFRA_FAIL_EVIDENCE_SIGNATURES if sig in evidence]
        if matched and (score in (0, 0.0, None)):
            msg = (
                f"dimension {dim!r}: score={score} with run-gate PRECONDITION-block "
                f"evidence ({matched[0]!r}) — this is an INFRA failure, not a quality "
                f"measurement. Do NOT dispatch code fixes for it. Fix the precondition "
                f"run-gate reported (SAB phantom/unregistered module, manifest state), "
                f"re-run run-gate until its preconditions pass, then re-evaluate."
            )
            if has_real_pass:
                # Partial pollution — surface per-dim info but accept the whole verdict.
                partial_diagnostics.append(msg)
            else:
                # Whole-gate pollution — reject so finalize-gate FATALs with infra dx.
                violations.append(msg)
    # Attach partial diagnostics as a marker suffix so callers can still surface
    # them without treating them as blockers. The first violation (if any) carries
    # the diagnostics block; if no violations, append a synthetic diagnostic-only
    # entry prefixed with "[partial-pollution]" so it's distinguishable from the
    # whole-gate rejections (operators looking at finalize-gate output).
    if partial_diagnostics and not violations:
        violations.append(
            "[partial-pollution] " + " | ".join(partial_diagnostics)
            + " — accepted (at least one dimension PASSed cleanly); fix the "
            "SAB/manifest preconditions and re-run to clear the partial-pollution marker."
        )
    return violations


# Round 30 站6 — dimensions whose score can be moved by an exclusion file, and
# the file that moves it. `None` means "this dimension has no exclusion channel"
# and is a POSITIVE statement, not an omission: scancode (license_compliance)
# takes its exclusions on the command line, so there is no file to fingerprint,
# and recording that here is what stops the next reader assuming it was
# forgotten. A new scored dimension belongs in this table with one or the other.
DIMENSION_EXCLUSION_FILES: "dict[str, str | tuple[str, ...] | None]" = {
    "secrets_scanning": ".gitleaksignore",
    "license_compliance": None,
    # Round 31 站4: setup.cfg's [mutmut] section carries BOTH halves of the
    # mutation denominator — paths_to_mutate (what is in scope) and
    # paths_to_exclude (basenames dropped from the mutant pool, written by the
    # party being scored). Digesting the whole file covers both, which is the
    # right granularity: a verdict has to be readable back to the scope it was
    # taken on. Not a dot-file, so the entry is a project-root-relative path
    # rather than a hidden one — the registry's contract is "which file can
    # move this dimension's score", not "which dotfile".
    "mutation_testing": "setup.cfg",
    # Round 32 站5: the file that declares which tests count moves
    # test_coverage the same way an ignore-list moves secrets_scanning.
    # Measured: a project whose [tool:pytest] testpaths named nine entries
    # while the directory held sixteen test files — two of them the FR tests
    # for FR-02 and FR-07. The agent's bare `pytest` measured nine, the
    # framework's explicit path measured sixteen, and neither denominator was
    # recorded. Three files can carry the declaration, so this entry is a
    # tuple: whichever ones exist are digested.
    "test_coverage": ("pytest.ini", "pyproject.toml", "setup.cfg"),
}


def _check_tool_evidence(ctx: "GateContext", raw: dict,
                         digests: "dict | None" = None) -> list[str]:
    """S3: Verify tool execution evidence in gate result JSON.

    When *digests* is supplied, every piece of evidence that PASSES验证 is
    fingerprinted into it (Round 27 站3). The digest is taken here rather than
    later because here is the only moment the evidence is known to exist and to
    be genuine — taskq-plus's Gate 4 cites 13 tool_output paths under the
    gitignored .sessi-work/, all of them gone now, while the verdict that read
    them is committed and permanent.

    For dimensions with requires_tool_execution:true in the gate YAML config,
    the result JSON breakdown entry MUST include either:
      - tool_output: path to a file containing raw tool stdout/stderr
      - tool_evidence: inline string of tool output snippet

    Additionally (S3-A), the content of tool_output files and tool_evidence
    strings is validated for structural authenticity — stub files and comment
    placeholders are rejected.

    Returns list of violation messages (empty = all good).
    """
    import yaml as _yaml
    from pathlib import Path as _Path

    # Round 29 Station 1: use the single-source-of-truth resolver instead of
    # project_root-relative globbing.  The old path (project/harness/gate_configs)
    # was one level too high when the harness is checked out as a git submodule
    # (the actual path is project/harness/harness/gate_configs).  SSOT resolver:
    # core.quality_gate.gate_thresholds.gate_config_path() — uses __file__ so it
    # always lands on the framework's own shipped configs.
    from core.quality_gate.gate_thresholds import gate_config_path as _gcp

    # Round 30 站3: gate_num comes from GateContext, which the framework builds
    # — a value outside 1-4 is a caller-contract violation, and Round 29 caught
    # the ValueError and returned `[]`, i.e. "no evidence violations found". The
    # raise now reaches the Round 28 crash boundary, which names the caller.
    cfg_path = _gcp(ctx.gate_num)

    if not cfg_path.exists():
        # Round 29 Station 1: gate configs are framework-owned assets tracked by
        # git ls-files.  Missing → checkout is corrupt.  Return a blocking
        # violation instead of silently returning [] (which the old code did
        # and was indistinguishable from "no violations").
        return [
            f"S3 gate config not found: {cfg_path} "
            f"(gate {ctx.gate_num}). Expected framework-owned asset — "
            f"is the harness checkout intact?"
        ]

    try:
        cfg = _yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    except (_yaml.YAMLError, OSError) as exc:
        return [
            f"S3 gate config unreadable: {cfg_path} ({exc})"
        ]



    violations: list[str] = []
    breakdown = raw.get("breakdown", {})

    # Round 29 Station 6: exclusion files that alter a dimension's score
    # (e.g. .gitleaksignore for secrets_scanning) must themselves be in
    # version control.  An untracked exclusion file means the score on a
    # fresh clone would be different — the denominator is in the scorer's
    # hands, not the framework's.
    #
    # Round 30 站6: fingerprinted as well as tracked. `.gitleaksignore` is
    # committed and still the score moves when a line is added to it — the file
    # being in git says nothing about which version of it produced this verdict.
    # The digest goes into evidence_digest beside the tool outputs (Round 27
    # 站3's channel), so two verdicts scored under different exemption lists are
    # distinguishable from the artifacts alone.
    _excl_pairs: "list[tuple[str, str]]" = [
        (_dim, str(_f))
        for _dim, _spec in DIMENSION_EXCLUSION_FILES.items()
        if _spec is not None
        for _f in ((_spec,) if isinstance(_spec, str) else _spec)
    ]
    for _dim_name, _excl_file in _excl_pairs:
        _excl_path = _Path(ctx.project_root) / _excl_file
        if not _excl_path.is_file():
            continue
        if digests is not None:
            from core.quality_gate.evidence_digest import digest_of_file
            digests[f"{_dim_name}::{_excl_file}"] = digest_of_file(
                _excl_path, source=f"{_excl_file} (score-altering exclusions)"
            )
        _project_root_path = _Path(ctx.project_root)
        import subprocess as _sp  # bound before the try: the except reads it
        try:
            _tracked = _sp.run(
                ["git", "ls-files", "--error-unmatch", _excl_file],
                cwd=str(_project_root_path),
                capture_output=True, text=True, timeout=10,
            )
            if _tracked.returncode != 0:
                violations.append(
                    f"S6 {_excl_file} exists but is not tracked by git — "
                    f"the {_dim_name} score depends on an exclusion file "
                    f"that is absent on a fresh clone. "
                    f"Either commit it or remove the exclusion entries."
                )
        except (OSError, _sp.SubprocessError) as _git_exc:
            # Round 30 站3: git is a HARD dependency of this framework —
            # enforcer_sha, state.json's phase_completed[].sha and every hook
            # need it. Round 29 wrote this as `except Exception` into a
            # logging.debug nobody reads, so a check that could not run was
            # indistinguishable from a check that found nothing. It still must
            # not block a gate on a provenance-adjacent failure, so it records
            # and continues — the ledger is where "we could not check" lives.
            from core.degradation_ledger import record_degradation
            record_degradation(
                str(_project_root_path), "gate:S6-exclusion-vcs",
                f"could not verify {_excl_file} is tracked by git ({_git_exc})",
                why=f"the {_dim_name} score was accepted without its exclusion "
                    f"file being checked into version control", owner="harness"
            )

    # Evidence format patterns are keyed by the RESOLVED tool id — a TS
    # project's linting evidence is eslint JSON, not ruff output.
    from harness.toolchains import (
        get_project_language,
        get_project_test_runner,
        resolve_tool_id,
    )
    _language = get_project_language(ctx.project_root)
    _test_runner = get_project_test_runner(ctx.project_root)

    for dim in cfg.get("dimensions", []):
        dim_name = dim.get("name", "")
        requires_tool = dim.get("requires_tool_execution", False)
        if not requires_tool:
            continue

        tool = dim.get("tool")
        if _language != "python" and tool:
            tool = resolve_tool_id(
                dim_name, _language, yaml_tool=tool, test_runner=_test_runner
            ) or tool
        dim_data = breakdown.get(dim_name, {})
        tool_output = dim_data.get("tool_output")
        tool_evidence = dim_data.get("tool_evidence")

        if tool_output:
            out_path = _Path(ctx.project_root) / tool_output
            # Containment check: refuse to read any tool_output that
            # resolves outside project_root. An agent writing
            # `../../etc/passwd` (or an absolute path, or a symlink to
            # outside) into the gate result JSON must not be silently
            # read by the audit cross-check.
            try:
                if path_escapes_root(out_path, _Path(ctx.project_root)):
                    violations.append(
                        f"{dim_name}: tool_output path '{tool_output}' "
                        f"escapes project root — refusing to read"
                    )
                    continue
            except (OSError, RuntimeError) as exc:
                violations.append(
                    f"{dim_name}: tool_output path '{tool_output}' "
                    f"cannot be resolved: {exc}"
                )
                continue
            if not out_path.exists():
                violations.append(
                    f"{dim_name}: tool_output path '{tool_output}' does not exist"
                )
            else:
                try:
                    content = out_path.read_text(encoding="utf-8", errors="replace")
                except OSError as exc:
                    violations.append(f"{dim_name}: cannot read tool_output file: {exc}")
                    continue
                _content_problems = _validate_tool_content(
                    content, tool, dim_name, inline=False
                )
                violations.extend(_content_problems)
                if digests is not None and not _content_problems:
                    from core.quality_gate.evidence_digest import digest_of_file
                    digests[dim_name] = digest_of_file(out_path, source=str(tool_output))
        elif tool_evidence:
            evidence_str = str(tool_evidence).strip()
            if len(evidence_str) < 10:
                violations.append(
                    f"{dim_name}: tool_evidence too short "
                    f"({len(evidence_str)} chars) — must be real tool output snippet"
                )
            else:
                _content_problems = _validate_tool_content(
                    evidence_str, tool, dim_name, inline=True
                )
                violations.extend(_content_problems)
                if digests is not None and not _content_problems:
                    from core.quality_gate.evidence_digest import digest_of_text
                    digests[dim_name] = digest_of_text(
                        evidence_str, source="tool_evidence (inline)"
                    )
        else:
            violations.append(
                f"{dim_name}: requires tool execution but result JSON has neither "
                f"tool_output nor tool_evidence — scores must come from actual tool runs"
            )

    return violations


# ---------------------------------------------------------------------------
# S4-B: Failed-tests assertion
# ---------------------------------------------------------------------------

def _check_tests_failed(raw: dict) -> list[str]:
    """S4-B: Verify no tests failed according to test_coverage tool_evidence.

    S4 cross-validates coverage *percentage* but does not check whether any
    tests actually failed.  A gate cannot pass when tests are red — even if
    coverage stays above threshold (e.g. 432 pass + 5 fail, coverage 91%).

    Parse the pytest summary line from ``breakdown.test_coverage.tool_evidence``
    and block when *failed > 0*.

    Returns list of violation messages (empty = all clear).
    """
    breakdown = raw.get("breakdown", {})
    evidence = str(breakdown.get("test_coverage", {}).get("tool_evidence", "") or "")
    if not evidence:
        return []  # S3 already blocks on missing evidence

    m = re.search(r"(\d+)\s+failed", evidence)
    if m and int(m.group(1)) > 0:
        failed = int(m.group(1))
        return [
            f"test_coverage: {failed} test(s) FAILED in tool_evidence — "
            f"gate cannot pass with failing tests. Fix all failures before re-submitting."
        ]
    return []


def _parse_skip_counts(raw: dict) -> "tuple[int, int] | None":
    """`(skipped, total)` from the test_coverage evidence, or None.

    One parse, two readers: the ratio WARN below and the ledger row at the
    finalize call site. Round 46 站2 split them apart because they answer
    different questions — "is coverage computed from a subset?" has a ratio
    threshold, "did any test not run?" does not.
    """
    breakdown = raw.get("breakdown", {})
    evidence = str(breakdown.get("test_coverage", {}).get("tool_evidence", "") or "")
    if not evidence:
        return None
    passed_m = re.search(r"(\d+)\s+passed", evidence)
    skipped_m = re.search(r"(\d+)\s+skipped", evidence)
    if not (passed_m and skipped_m):
        return None
    passed = int(passed_m.group(1))
    skipped = int(skipped_m.group(1))
    total = passed + skipped
    return (skipped, total) if total else None


def _check_test_skip_ratio(raw: dict, threshold: float = 0.10) -> str | None:
    """W1: Warn when a high fraction of tests are skipped.

    Skipped tests contribute 0 coverage lines.  A skip ratio above *threshold*
    (default 10 %) means coverage is computed from a subset of the suite and
    may miss infrastructure code paths (e.g. DB schema, async sessions).

    This is a **WARN** (not BLOCK) — some projects legitimately skip tests
    that require real external services.

    Scope note (Round 46 站2): this is a statement about *coverage*, and about
    coverage it is honest. It is NOT the enforcer for "a requirement's own
    test did not run" — that is `compute_trace_dimension`'s absent-witness
    rule, which blocks through the traceability dimension. taskq-advance's
    17 skips are 6.25 % of its suite and never tripped this warning, while
    three of its NFRs had guards skipping themselves. Two questions, two
    mechanisms; do not make this one carry the other's weight.

    Returns a warning string, or ``None`` if the skip ratio is within threshold.
    """
    counts = _parse_skip_counts(raw)
    if counts is None:
        return None
    skipped, total = counts

    skip_ratio = skipped / total
    if skip_ratio > threshold:
        return (
            f"[WARN] {skipped} of {total} tests ({skip_ratio:.0%}) are SKIPPED — "
            f"skipped tests contribute 0 coverage lines. Coverage score reflects only "
            f"non-skipped tests. Consider mocking infrastructure to run skipped tests, "
            f"or document why the skips are architectural constraints in TODO.md."
        )
    return None


# ---------------------------------------------------------------------------
# S4: Harness cross-validation (Solution B)
# ---------------------------------------------------------------------------

def _architecture_regression_reason(
    project_root: str, gate_num: int, config, crg_metrics: dict,
) -> "str | None":
    """Return a hard-block reason if architecture regressed vs the prior baseline.

    At Gate 4 (P6 exit) the current CRG metrics are compared against
    crg_baseline_p4.json via compute_structural_drift; drift ≥
    config.crg.drift_threshold (default 0.4) is a hard regression even when the
    absolute architecture score still clears its threshold. Returns None when not
    applicable (other gates, missing baseline, or within threshold). Mirrors the
    CI crg-arch-check drift gate so local and CI agree.
    """
    if gate_num != 4:
        return None
    bl_path = Path(project_root) / ".methodology" / "crg_baseline_p4.json"
    if not bl_path.is_file():
        return None
    crg_cfg = (config.get("crg", {}) if isinstance(config, dict)
               else getattr(config, "crg", {})) or {}
    dthr = float(crg_cfg.get("drift_threshold", 0.4))
    try:
        from harness.ssi.scripts.crg_analysis import compute_structural_drift
        bl = json.loads(bl_path.read_text(encoding="utf-8"))
        drift = compute_structural_drift(bl, crg_metrics)
    except Exception as exc:
        print(f"[WARN] structural-drift check failed, skipping: {exc}")
        return None
    if drift >= dthr:
        return (f"structural drift {drift:.2f} ≥ {dthr:.2f} vs P4 baseline "
                f"({bl.get('_baseline_sha', '?')[:8]})")
    return None


# The dimensions whose number IS the test suite running against the delivered
# code. Deliberately two, not "everything test-shaped":
#   * test_assertion_quality is an AST scan of the test files, not a run;
#   * mutation_testing is self-correcting — a mutant inside a module the suite
#     patched away survives, so a stubbed boundary lowers that score instead of
#     raising it, and marking it would be describing the wrong direction.
_SUITE_MEASURED_DIMENSIONS: frozenset[str] = frozenset({
    "test_coverage", "integration_coverage",
})


def _mark_stubbed_boundary_dimensions(ctx: "GateContext", raw: dict) -> list[dict]:
    """Mark the suite-measured dimensions when the suite replaced a declared
    boundary, and leave the finding in the ledger.

    Round 51 站3. The marker demotes the dimension out of `weight_covered`
    (`measurement_scope` reads `_SOURCES_NOT_FRAMEWORK_MEASURED`); it does not
    change the score and does not block. Round 32 站4's rule stands: an
    unmeasurable dimension is the framework's debt, never a number the project
    loses. What changes is that the composite stops claiming to cover weight it
    did not measure.

    Never raises — a scan that cannot parse a test file is not a reason to stop
    a gate.
    """
    from core.degradation_ledger import record_degradation
    from core.quality_gate.boundary_realism import stubbed_boundaries

    try:
        findings = stubbed_boundaries(ctx.project_root)
    except Exception as exc:  # pragma: no cover — reporting must not stop a gate
        record_degradation(
            ctx.project_root, "gate:stubbed-boundary", "scan failed",
            f"{type(exc).__name__}: {exc}", owner="harness",
        )
        return []
    if not findings:
        return []

    modules = sorted({f["module"] for f in findings})
    breakdown = raw.get("breakdown")
    marked = []
    if isinstance(breakdown, dict):
        for dim_name in sorted(_SUITE_MEASURED_DIMENSIONS & set(breakdown)):
            entry = breakdown[dim_name]
            if isinstance(entry, dict) and entry.get("score") is not None:
                entry["score_source"] = SCORE_SOURCE_STUBBED_BOUNDARY
                marked.append(dim_name)

    record_degradation(
        ctx.project_root, "gate:stubbed-boundary",
        f"{len(findings)} autouse fixture(s) replace {len(modules)} "
        f"SAB high-risk module(s)",
        "a dimension measured over this suite is not a measurement of the "
        "delivered code; it is excluded from weight_covered",
        data={"findings": findings, "modules": modules,
              "dimensions_marked": marked},
        owner="project",
    )
    if marked:
        print(f"  [BOUNDARY] {', '.join(marked)}: measured over a suite that "
              f"replaces {', '.join(modules)} — marked "
              f"{SCORE_SOURCE_STUBBED_BOUNDARY}")
    return findings


def _record_coverage_denominator(ctx: "GateContext") -> dict:
    """Put the coverage denominator, and what left it, on the record.

    Round 51 站4. `read_coveragerc_source` has read `[run] source` since it
    was written — the denominator is not the caller's to pick — and `[run]
    omit`, which decides the same thing from the other side, was read by
    nothing. Measured on taskq-api: omit removes 63 of 839 delivered
    statements, both files at 0.0 % coverage, and 92.5 % is reported as
    100 %.

    An omit stays legal; taskq-advance's names one file with a written
    rationale. What this refuses is the silent version — the same shape as
    Round 50 站5's `cost_entries_excluded_substrate`. Never raises.
    """
    from core.degradation_ledger import record_degradation
    from core.quality_gate.cov_utils import coverage_denominator

    try:
        d = coverage_denominator(Path(ctx.project_root))
    except Exception as exc:  # pragma: no cover — reporting must not stop a gate
        record_degradation(
            ctx.project_root, "gate:coverage-denominator", "read failed",
            f"{type(exc).__name__}: {exc}", owner="harness",
        )
        return {}
    if not d.get("omitted_files"):
        return d

    delivered = d["statements_delivered"]
    share = (d["statements_omitted"] / delivered * 100) if delivered else 0.0
    if d["statements_omitted"]:
        size = (f"{d['statements_omitted']}/{delivered} statements, "
                f"{share:.1f}%")
    else:
        # coverage.json is written by a run that already applied the omit, so
        # for most projects the omitted files are simply not in it and their
        # statement count is unknowable from the report. Measured across six
        # projects: only taskq-api's report happens to contain them (63 of 839,
        # 7.5%). Saying "0 statements" here would read as "the omit costs
        # nothing", which is the opposite of what is known — so say what is
        # known, which is the file list. Counting the statements a second way
        # (an AST walk) would produce a number that looks comparable to
        # coverage.py's and is not, which is this round's own defect.
        size = "size unknown — this report was produced with the omit applied"
    record_degradation(
        ctx.project_root, "gate:coverage-denominator",
        f"{len(d['omitted_files'])} file(s) are outside the coverage "
        f"denominator ({size})",
        "the reported percentage is taken over statements_measured, not over "
        "the delivered tree; both numbers belong beside the score",
        data=d, owner="project",
    )
    return d


def _gate_dimension_names(ctx: "GateContext") -> frozenset[str]:
    """The dimension names this gate's config declares.

    Round 53 站5a. `ctx.config` is a GateConfig or a plain dict depending on
    the caller, and two places in `finalize_gate` already branch on that to
    get the full entries. This returns names only, and stays separate from
    those two on purpose: they are inline inside long functions and need the
    entries, so folding them together would trade one duplicated branch for a
    parameter that means "which shape do you want".
    """
    if isinstance(ctx.config, dict):
        entries = ctx.config.get("dimensions") or []
    else:
        entries = getattr(ctx.config, "dimensions", None) or []
    names: set[str] = set()
    for entry in entries:
        name = entry.get("name") if isinstance(entry, dict) else getattr(entry, "name", None)
        if name:
            names.add(str(name))
    return frozenset(names)


def _verify_system_reach_block(ctx: "GateContext") -> list[str]:
    """Which replaced boundaries `make verify-system` did not execute for real.

    Round 52 站2. Round 51 站3 recorded that a dimension was scored over a
    suite which replaced a SAB high-risk module before every test in the file,
    and let the number stand with a marker. The obligation this raises is the
    one thing the framework can still ask: the project's own verification
    target — the only command it runs that the test suite did not configure —
    has to execute what the suite replaced.

    Returns [] when there is nothing outstanding AND when the reach could not
    be measured; the ledger row carries the difference. A gate must not be
    blocked by a measurement that did not happen (Round 35 站2), and it must
    not read a measurement that did not happen as a pass either — which is why
    `unmet_obligations` omits the key rather than returning [], and why this
    function branches on the status instead of on the list.

    Never raises: a report about coverage instrumentation is a worse reason to
    stop a gate than the thing it was going to report.
    """
    from core.degradation_ledger import record_degradation
    from core.quality_gate.verify_system_reach import (
        STATUS_MEASURED,
        unmet_obligations,
    )

    # Round 53 站5a: only a gate that runs `execute_verification_target` can
    # have a reach artifact, so only such a gate has this question. Measured on
    # taskq-super's full P1-P8 run: 116 `gate:verify-system-reach` rows, every
    # one "no reach artifact", and correlating each row's `ts` against
    # gate_timestamps.jsonl puts ALL 116 at Gate 1 and none at Gate 2, 3 or 4 —
    # 18.5% of that project's degradation ledger, filed under owner `harness`,
    # asking a gate a question its own config says it cannot answer.
    #
    # Not "quieten the log". Round 46 站1's rule is that abstaining is not
    # passing; a question that was never in this gate's scope was never
    # abstained from, and the gate config is the single source of what a gate's
    # scope is.
    if "execute_verification_target" not in _gate_dimension_names(ctx):
        return []

    try:
        verdict = unmet_obligations(ctx.project_root)
    except Exception as exc:  # pragma: no cover — reporting must not stop a gate
        record_degradation(
            ctx.project_root, "gate:verify-system-reach",
            "reach obligation check failed", f"{type(exc).__name__}: {exc}",
            owner="harness",
        )
        return []

    if verdict["status"] != STATUS_MEASURED:
        record_degradation(
            ctx.project_root, "gate:verify-system-reach",
            "which boundaries `make verify-system` executed is unknown",
            verdict["reason"], owner="harness",
        )
        return []

    for row in verdict.get("unmeasurable") or []:
        record_degradation(
            ctx.project_root, "gate:verify-system-reach",
            f"obligation {row['module']}.{row['attr']} cannot be evaluated",
            row["why"], owner="harness",
        )

    return [
        f"{row['module']}.{row['attr']} is replaced by an autouse fixture in "
        f"the test suite and is never executed by `make verify-system`"
        for row in verdict.get("unmet") or []
    ]


def _run_harness_cross_validation(
    ctx: "GateContext", raw: dict,
) -> "tuple[list[str], list[str]]":
    """S4: Run tools independently and cross-validate agent-reported scores.

    For each Tier 1/2 dimension with requires_tool_execution:true, the harness
    executes the tool itself (via harness.tool_runners), computes a score, and
    blocks when:
      - harness_score < threshold  (harness says the code fails)
      AND
      - agent_score >= threshold   (agent claims the code passes)

    This eliminates score fabrication for tool-based dimensions: even if the
    agent writes a perfectly-structured stub, the harness independently verifies
    the actual code.

    Slow tools (mutmut, scancode) are skipped here; Solution A (content
    validation) still applies to their evidence files.

    Raw tool output is written to the directory core.evidence_retention
    names, under .methodology/gate_evidence/, for audit — a verdict may not
    cite a place advance-phase deletes (Round 50 站6).

    Returns ``(fabrication, cannot_verify)``.

    Round 32 站4: these used to be one list, filed wholesale under
    ``tool_score_fabrication`` — the block kind whose registered remediation
    reads "Do NOT re-run the gate — the score, not the run, is what failed."
    A timeout, a missing tool, a tool that cannot import the project: all of
    them arrived under that heading, telling the agent its number was a lie
    when the truth was that the framework had not measured anything. Measured
    on a live P4, `.methodology/last_block.md` carries a pyright timeout and a
    PYTHONPATH gap under exactly that kind.

    The second list is raised as ``infra_fail`` instead — a key that already
    exists in core/quality_gate/block_reason.py and already says the right
    thing ("Dimension scored zero because its tool could not run
    (infrastructure, not quality)"), and which Round 13's routing keeps out of
    a CODE-FIX round against the project.
    """
    import yaml as _yaml
    from pathlib import Path as _Path

    # Round 29 Station 1: use gate_config_path() instead of project_root-relative
    # globbing (same fix as _check_tool_evidence above).
    from core.quality_gate.gate_thresholds import gate_config_path as _gcp

    # Round 30 站3: see _check_tool_evidence — an unknown gate_num is a caller
    # bug, not "no fabrication found".
    cfg_path = _gcp(ctx.gate_num)

    if not cfg_path.exists():
        return [], [
            f"S4 gate config not found: {cfg_path} "
            f"(gate {ctx.gate_num}). Expected framework-owned asset — "
            f"is the harness checkout intact?"
        ]

    try:
        cfg = _yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    except (_yaml.YAMLError, OSError) as exc:
        return [], [
            f"S4 gate config unreadable: {cfg_path} ({exc})"
        ]


    try:
        from harness.tool_runners import run_tool, compute_tool_score
    except ImportError as exc:
        print(f"  [S4-WARN] cross-validation disabled: harness.tool_runners unavailable: {exc}")
        return [], []

    # Language-aware tool resolution: the YAML `tool:` field is the Python
    # default; non-Python projects resolve dimension → tool via the toolchain
    # registry (state.json `language`/`test_runner`, persisted at init-project).
    from harness.toolchains import (
        get_project_language,
        get_project_test_runner,
        resolve_tool_id,
    )
    _language = get_project_language(ctx.project_root)
    _test_runner = get_project_test_runner(ctx.project_root)

    # Round 50 站6: under .methodology/gate_evidence/, not .sessi-work/. The
    # block message below sends the operator to this file, and advance-phase
    # deletes .sessi-work/ at every phase transition — so the audit trail for
    # a verdict used to be gone one advance later.
    from core.evidence_retention import cited_evidence_dir
    verification_dir = cited_evidence_dir(ctx.project_root)
    verification_dir.mkdir(parents=True, exist_ok=True)
    verification_relpath = verification_dir.relative_to(ctx.project_root).as_posix()
    from core.harness_config import get_value as _get_config_value
    _audit_max_bytes = int(_get_config_value(ctx.project_root, "gate_evidence_max_bytes"))

    violations: list[str] = []
    # Round 32 站4: "the harness could not measure this" is a separate verdict
    # from "the harness measured and the agent's number was false". Filing
    # both under one key told the agent its score was a lie whenever the
    # framework's own run fell over.
    unverifiable: list[str] = []
    breakdown = raw.get("breakdown", {})

    # architecture is scored by the framework's independent CRG run (community_cohesion)
    # inside finalize_gate, not by re-running a tool here. Its tool field is
    # `code-review-graph` (no inline scorer); skip it in cross-validation.
    _crg_owned = _CRG_OWNED_DIMENSIONS

    for dim in cfg.get("dimensions", []):
        dim_name = dim.get("name", "")
        requires_tool = dim.get("requires_tool_execution", False)
        tool = dim.get("tool")
        threshold = float(dim.get("threshold", 0))

        if not requires_tool or not tool or dim_name in _crg_owned:
            continue

        if _language != "python":
            tool = resolve_tool_id(
                dim_name, _language, yaml_tool=tool, test_runner=_test_runner
            ) or tool

        # `dict.get(k, default)` only substitutes when the KEY is absent; an
        # explicit JSON null surfaces as None. Round 27 站1: this used to go
        # straight into float(), which raised TypeError out of finalize_gate —
        # the call site has no try/except, so one null crashed the whole gate.
        _dim_entry = breakdown.get(dim_name, {})
        _raw_agent = _dim_entry.get("score")
        agent_score: float | None = (
            float(_raw_agent) if _raw_agent is not None else None
        )
        _agent_label = "N/A (agent)" if agent_score is None else f"{agent_score:.1f}"

        # Round 35 站3: a dimension whose number the framework produces itself
        # is established BEFORE the agent's claim is consulted.
        #
        # The early exit below is correct about fabrication and wrong about
        # attribution. A self-reported failing score is the cheapest way to
        # stop the framework looking, and for mutation_testing it stopped the
        # only checks that can tell a harness fault from a project debt:
        # measured on taskq-renew, `.methodology/mutation_score.json` was
        # absent and `scope_drift` fired, and the gate reported neither —
        # it blocked on "scored 0.0, needs 75.0" and told the project to kill
        # mutants that had never been produced.
        #
        # mutmut is the only member today, so there is no registry for one
        # entry; the rule is stated here instead.
        if tool == "mutmut":
            _mut_fab, _mut_infra = _mutation_artifact_violations(
                ctx, dim_name, agent_score, threshold
            )
            violations.extend(_mut_fab)
            unverifiable.extend(_mut_infra)

        # Only cross-validate when the agent claims a passing score.
        # If the agent already reports FAIL, there is no fabrication concern.
        #
        # A None is NOT a claim of failure — it is a claim that the dimension does
        # not apply, and that claim is exactly what the framework has to check
        # (see na_is_framework_verified). So it falls through to run_tool below.
        if agent_score is not None and agent_score < threshold:
            continue

        output, returncode = run_tool(tool, ctx.project_root)

        # Write audit trail regardless of outcome
        audit_file = verification_dir / f"{dim_name}_harness.txt"
        # Round 50 站6: this file is now under .methodology/ and therefore
        # committed, so it is bounded by the same ceiling Round 45 站1 set for
        # cited evidence — one knob, not a second one beside it. The tail is
        # kept: a tool's verdict is at the end of its output.
        _audit_body = output
        if len(_audit_body) > _audit_max_bytes:
            _audit_body = (
                f"[truncated to the last {_audit_max_bytes} characters — "
                f"values.gate_evidence_max_bytes]\n"
                + _audit_body[-_audit_max_bytes:]
            )
        try:
            audit_file.write_text(
                f"# Harness-executed: {tool}\n"
                f"# returncode: {returncode}\n"
                f"# agent_score: {_agent_label} | threshold: {threshold}\n\n"
                f"{_audit_body}\n",
                encoding="utf-8",
            )
        except OSError:
            pass  # Audit write failure is non-fatal

        if returncode < 0 and returncode != -1:
            # Tool unavailable (rc=-2 timeout, rc=-3 not-found, rc=-4 error).
            # The agent cannot legitimately claim a passing tool-based score
            # when the harness cannot independently verify the tool runs.
            # Round 31 站6: -2 and -3 used to share one sentence, and that
            # sentence was "Install '<tool>'". A timeout means the tool IS
            # installed and did not finish, so the remediation pointed at the
            # one thing that was already true — measured on a real Gate 2,
            # where `pyright` timed out at 60s against 4917 files and the block
            # told the agent to install pyright.
            _how = {
                -2: (
                    f"'{tool}' is installed and did not finish inside its "
                    f"budget. Narrow what it scans, or raise the budget via "
                    f"harness_config values.timeouts — do NOT lower the score "
                    f"to work around a framework-side timeout."
                ),
                -3: (
                    f"Install '{tool}' so the harness can independently verify "
                    f"the score. If it is genuinely unavailable, the dimension "
                    f"score must be set below threshold ({threshold:.0f}) to "
                    f"reflect that."
                ),
            }.get(returncode, (
                f"'{tool}' failed inside the harness (rc={returncode}); this is "
                f"a framework-side fault, not an agent one — see the audit file "
                f"in {verification_relpath}/."
            ))
            _rc_labels = {-2: "timed out", -3: "not found", -4: "error"}
            _rc_label = _rc_labels.get(returncode, f"rc={returncode}")
            # Round 32 站4: this is the harness failing to measure, not the
            # agent failing to tell the truth, and it is filed as such now.
            unverifiable.append(
                f"{dim_name}: tool '{tool}' {_rc_label} — harness cannot "
                f"cross-validate agent_score={_agent_label}. {_how}"
            )
            continue

        if returncode == -1:
            # Skip-list tool (mutmut / scancode) — too slow to re-run here, but a
            # passing score must still be backed by a real, committed tool_output
            # FILE (not inline tool_evidence). Verify the file exists, is non-empty,
            # and matches the tool's output format. Missing/empty/malformed → block.
            _dim_data = breakdown.get(dim_name, {})
            _tout = _dim_data.get("tool_output")
            _problem = None
            if not _tout:
                _problem = ("no tool_output file — skip-list tools (mutmut/scancode) "
                            "require a committed output file, not inline tool_evidence")
            else:
                _tpath = _Path(ctx.project_root) / _tout
                # Containment check: refuse to read any tool_output that
                # resolves outside project_root (see _check_tool_evidence
                # for the rationale).
                try:
                    if path_escapes_root(_tpath, _Path(ctx.project_root)):
                        _problem = (
                            f"tool_output path '{_tout}' escapes project "
                            f"root — refusing to read"
                        )
                    elif not _tpath.exists() or _tpath.stat().st_size < _TOOL_OUTPUT_MIN_BYTES:
                        _problem = f"tool_output file missing or empty: {_tout}"
                    else:
                        _fmt = _validate_tool_content(
                            _tpath.read_text(encoding="utf-8", errors="replace"),
                            tool, dim_name, inline=False,
                        )
                        if _fmt:
                            _problem = "; ".join(_fmt)
                except (OSError, RuntimeError) as exc:
                    _problem = (
                        f"tool_output path '{_tout}' cannot be resolved: {exc}"
                    )
            if _problem:
                violations.append(
                    f"{dim_name}: skip-list tool '{tool}' score is unverifiable — {_problem}. "
                    f"A passing score requires genuine '{tool}' output committed to a file."
                )
            else:
                print(
                    f"  [S4] {dim_name}: '{tool}' skip-list — tool_output file verified "
                    f"(format OK); not re-run (too slow)"
                )
            # Round 31 站2: for mutmut the tool_output file above is AUDIT, not
            # the score. The number itself came from the artifact the framework
            # wrote — checked at the top of this loop since Round 35 站3, so
            # that a self-reported failing score cannot skip past it.
            continue

        if returncode == 5:
            # pytest-family exit 5 = "no tests collected". A passing agent score for a
            # dimension whose measuring suite does not exist is unverifiable — treat as a
            # fabrication risk and BLOCK (no free pass for a missing suite). The message is
            # dimension-generic: exit 5 can come from any pytest-family tool (benchmark,
            # integration-cov, or pytest-cov when a conftest import fails).
            if agent_score is None:
                # …but an agent that declared N/A and a framework run that agrees is
                # the one legitimate not-applicable: a project with no performance
                # requirement genuinely has no benchmarks. It is recorded as
                # framework-verified, so the verdict layer can tell it apart from an
                # agent-authored null nobody checked.
                _mark_framework_na(_dim_entry, tool, returncode)
                print(f"  [S4] {dim_name}: agent declared N/A — framework ran "
                      f"'{tool}' and also got no score (exit 5); recorded as "
                      f"{SCORE_SOURCE_FRAMEWORK_NA}")
                continue
            violations.append(
                f"{dim_name}: '{tool}' collected no tests (exit 5) — a passing score for "
                f"'{dim_name}' (agent={_agent_label}) is unverifiable when its measuring "
                f"suite does not exist or fails to import. Add/repair the suite, then re-finalize."
            )
            continue

        harness_score = compute_tool_score(tool, output, returncode)
        # Round 56 站6 / follow-up: `pytest-cov` scores the whole-project run,
        # which collapses the modules other FRs will fill into one denominator
        # — on taskq-cc's FR-01 that read 8.5% against 97.06% of what FR-01
        # actually owns. Re-score from the on-disk `.coverage` restricted to
        # the FR's own modules; the suite has already run, so this executes
        # nothing (Round 25 站1).
        #
        # Round 57 站1: whether to re-scope is `s4_rescopes_to_fr`'s answer,
        # read from the gate's own declared `scope:`. It used to be
        # "ctx.fr_id is truthy", which made this the only one of three
        # enforcers with no phase condition at all — and the two that had one
        # tested for phase 3, so a Phase 7 run got two verdicts.
        _fr_id = ctx.fr_id or ""  # "" is falsy to the predicate, and is a str
        if harness_score is not None and s4_rescopes_to_fr(
            gate_num=ctx.gate_num, fr_id=_fr_id, dim_name=dim_name,
        ):
            from core.quality_gate import gate1_evidence as _g1e
            _record = _g1e.fr_coverage_record(ctx.project_root, _fr_id)
            if _record is not None and _record.percent is not None:
                print(
                    f"  [S4] {dim_name}: harness={harness_score:.1f} (whole-project) | "
                    f"per-fr={_record.percent:.1f} (using per-fr scope)"
                )
                # Round 57 站3: the number now travels with the evidence for it.
                # Before this, `score` became the per-FR figure while
                # `tool_output` still cited the whole-project audit whose last
                # line reads TOTAL 62% — and the scope switch existed only on
                # stdout. The write is assignment, not setdefault: this number
                # is the framework's, so its citation is the framework's too.
                _pf_file = verification_dir / f"{dim_name}_harness_per_fr_{_fr_id}.txt"
                _pf_rel = str(_pf_file.relative_to(_Path(ctx.project_root)))
                try:
                    _pf_file.write_text(
                        per_fr_coverage_evidence(
                            fr_id=_fr_id, dim_name=dim_name, tool=tool,
                            record=_record, whole_project_score=harness_score,
                            whole_project_audit=str(
                                audit_file.relative_to(_Path(ctx.project_root))
                            ),
                        )[:_audit_max_bytes],
                        encoding="utf-8",
                    )
                    _dim_entry["tool_output"] = _pf_rel
                except OSError as exc:
                    # Evidence the verdict cannot cite must not be claimed.
                    # Leave tool_output alone and say why (Round 32 站4: the
                    # framework failing to record is the framework's debt).
                    print(f"  [S4-WARN] {dim_name}: per-FR evidence not written "
                          f"({exc}); tool_output keeps the whole-project audit",
                          file=sys.stderr)
                _dim_entry["coverage_scope"] = "per_fr"
                _dim_entry["coverage_scope_fr"] = _fr_id
                harness_score = _record.percent
        if harness_score is None:
            if agent_score is None:
                # Same reasoning as the exit-5 branch: the framework ran the tool
                # and it too produced no number, so the dimension really is not
                # applicable — and now it says so with the framework's own run
                # behind it rather than the agent's word.
                _mark_framework_na(_dim_entry, tool, returncode)
                print(f"  [S4] {dim_name}: agent declared N/A — framework ran "
                      f"'{tool}' and also got no score; recorded as "
                      f"{SCORE_SOURCE_FRAMEWORK_NA}")
                continue
            # readability (radon-mi) returns None only when there is NO analysable
            # source (radon availability is already gated by S2 tool_checks.verify_gate_tools).
            # A passing readability score with nothing to analyse is unverifiable.
            if dim_name == "readability":
                violations.append(
                    f"readability: '{tool}' produced no analysable maintainability score "
                    f"(no source files to analyse) — cannot verify agent score "
                    f"{_agent_label}. A passing readability score requires analysable "
                    f"code; add it, then re-finalize."
                )
                continue
            # Round 32 站4: every other dimension used to `continue` here in
            # silence, so the agent's number stood unchecked and the run left
            # no trace that the check had not happened. Round 30's rule: an
            # abstention is not a pass. The scorers this round taught to
            # return None instead of 0.0 all land here, so this is the branch
            # that decides whether the round traded a false accusation for a
            # silent one — it records the gap and blocks under `infra_fail`,
            # which is the framework's problem to fix, not the project's.
            # Round 50 站2: mark the dimension as well as the ledger. The
            # ledger row lives beside the verdict; this lives IN it, so a
            # reader of the gate result alone can tell an agent's unchecked
            # number from a measured one — and `measurement_scope` stops
            # counting its weight as covered.
            _dim_entry["score_source"] = SCORE_SOURCE_AGENT_UNVERIFIED
            from core.degradation_ledger import record_degradation
            record_degradation(
                ctx.project_root, f"gate:s4:{dim_name}",
                f"'{tool}' produced no score the harness could read",
                why=("the agent's number for this dimension was therefore not "
                     "cross-validated; see the audit file for the raw output"), owner="harness"
            )
            unverifiable.append(
                f"{dim_name}: '{tool}' ran but produced no score the harness "
                f"could read, so agent_score={_agent_label} could not be "
                f"cross-validated. This is a framework-side gap — read "
                f"{audit_file.relative_to(_Path(ctx.project_root))} and fix "
                f"the tool invocation or its scorer; do NOT lower the "
                f"dimension score to work around it."
            )
            continue

        if agent_score is None:
            # The agent said "not applicable"; the framework ran the tool and got a
            # number. The dimension applies after all, and the number the verdict
            # uses is the one the framework measured. finalize_gate builds its
            # DimResult list from raw["breakdown"] further down this same function,
            # so writing back here is what puts it in front of the verdict.
            #
            # Deliberately NOT reported as fabrication: the agent made no claim of
            # passing, so there is nothing it faked. If the framework's number is
            # below threshold, the ordinary per-dimension floor blocks it — routing
            # a non-claim through the fabrication path would misname the failure
            # (the Round 13 辨源 principle).
            _dim_entry["score"] = harness_score
            _dim_entry["score_source"] = SCORE_SOURCE_FRAMEWORK
            _dim_entry.setdefault(
                "tool_output",
                str(audit_file.relative_to(_Path(ctx.project_root))),
            )
            print(f"  [S4] {dim_name}: agent declared N/A — framework ran '{tool}' "
                  f"and scored {harness_score:.1f}; using the framework's score")
            continue

        # Round 54: the framework ran the tool, so the framework's number is
        # the score. Until now this branch only ever appended a violation, and
        # when it did not the agent's number survived unexamined — see
        # `s4_score_verdict` for the tree-level measurement of what that cost.
        _verdict = s4_score_verdict(
            agent_score=agent_score, harness_score=harness_score,
            threshold=threshold,
            current_source=_dim_entry.get("score_source"),
        )
        _dim_entry["score"] = _verdict["score"]
        _dim_entry["score_source"] = _verdict["score_source"]
        _dim_entry.setdefault(
            "tool_output", str(audit_file.relative_to(_Path(ctx.project_root))),
        )

        print(
            f"  [S4] {dim_name}: harness={harness_score:.1f} | "
            f"agent={_agent_label} | threshold={threshold} -> "
            f"score={_verdict['score']:.1f} ({_verdict['score_source']})"
        )

        if _verdict["fabrication"]:
            violations.append(
                f"{dim_name}: fabrication detected — "
                f"harness ran '{tool}' and scored {harness_score:.1f} "
                f"(below threshold {threshold}), but agent reported {_agent_label} "
                f"(above threshold). "
                f"See {audit_file.relative_to(_Path(ctx.project_root))}"
            )

    return violations, unverifiable


def s4_rescopes_to_fr(
    *, gate_num: int, fr_id: "str | None", dim_name: str,
) -> bool:
    """True when this dimension's number must be recomputed on one FR's modules.

    Round 57 站1. Three places used to answer "per FR or whole project", and
    they answered differently: S4 asked only whether `ctx.fr_id` was set (so
    every phase re-scoped), while `validate_fr_coverage_immediate` and
    `_check_gate1_live_coverage` each tested `phase == 3`. A Phase 7 run with
    whole-project coverage at 62% and FR-01 at 100% of its own modules got a
    pass from one enforcer and a block from the other.

    The gate declares the answer itself — `scope: single_fr` in
    gate1_per_fr.yaml — and Gate 1 carries that scope at every phase it runs,
    so consulting the declaration deletes the three phase conditions instead
    of adding a fourth.

    `test_coverage` is the only dimension re-scoped, and the reason is the
    data file rather than the dimension list. `fr_coverage_from_last_run`
    reads `.coverage`, written by the unit suite. `integration_coverage` is
    scored by `pytest-cov-integration` over a different run and appears only
    in gates 2/3/4; it was in the original condition, where it could not fire
    through any sanctioned path (no workflow JS passes `--fr-id` to those
    gates — measured, zero) and would have produced a wrong number if
    hand-invoked. One member, so the rule is stated here rather than in a
    registry of one — same reasoning as the `tool == "mutmut"` branch above.
    """
    if not fr_id or dim_name != "test_coverage":
        return False
    from core.quality_gate.gate_thresholds import is_per_fr_gate

    return is_per_fr_gate(gate_num)


def per_fr_coverage_evidence(
    *, fr_id: str, dim_name: str, tool: str,
    record: "FrCoverage", whole_project_score: float,
    whole_project_audit: str,
) -> str:
    """The audit body for a score S4 recomputed on one FR's modules.

    Round 57 站3. The per-FR re-score used to change `score` and leave
    `tool_output` pointing at the whole-project pytest-cov audit, whose last
    line reads `TOTAL … 62%` — a verdict citing a file that contradicts it,
    with the scope switch recorded only on stdout. Round 45's rule is that a
    verdict must outlive its proof; it must also agree with it.

    Both sides of the ratio and every file behind it are written out, because
    a percentage alone cannot be checked (Round 42 站4). The whole-project
    number and the path to its own audit stay in the body: they are what this
    measurement replaced, and the next reader's first question is what
    changed.
    """
    _files = "\n".join(f"#   {f}" for f in record.files) or "#   (none)"
    return (
        f"# Harness-executed: {tool} (re-scored per FR)\n"
        f"# dimension: {dim_name}\n"
        f"# fr_id: {fr_id}\n"
        f"# scope: this FR's own modules, per fr_module_traceability\n"
        f"# executed/coverable: {record.executed}/{record.coverable}\n"
        f"# per-fr coverage: {record.percent:.1f}%\n"
        f"# whole-project coverage: {whole_project_score:.1f}% "
        f"(see {whole_project_audit})\n"
        f"#\n"
        f"# files in scope:\n"
        f"{_files}\n"
    )


def s4_score_verdict(
    *, agent_score: float, harness_score: float, threshold: float,
    current_source: "str | None",
) -> dict:
    """What S4 records for one dimension once it has both numbers.

    Round 54. S4 handled three of the four cases and dropped the fourth on the
    floor: when both numbers cleared the threshold it appended nothing, so the
    dimension kept whatever the agent typed. Measured on the exact tree taskq's
    Gate 4 judged (`git archive c1af37e`, the commit whose message is
    `release(P6): Gate4 PASS score=97.2 — pipeline complete`):

        recorded score                                  100.0
        the framework's own scanner on that tree         80.0
        the agent's own evidence line
          (`total=6 source files; with_handler=4`)       66.7

    Three numbers for one dimension, and the one the verdict carried is the
    only one nobody computed. The threshold is 80, so the framework's number
    was a pass by exactly zero margin, recorded as perfect.

    The rule is Round 35 站3's, which built only its `agent_score is None`
    half: **when the framework has its own number, that number is the score.**
    Not "block when they disagree" — a threshold on the disagreement would be
    one more knob, and would leave the cause untouched.

    `fabrication` keeps its existing meaning and its existing block. "The agent
    claimed a pass the tool contradicts" is a different and worse fact than
    "the agent's number was imprecise", and Round 13's routing rule is that two
    different failures must not arrive under one heading.

    `current_source` is passed in and returned unchanged when it already says
    something this function is not entitled to overwrite. Round 51 站3 marks
    test_coverage and integration_coverage `stubbed_boundary` before S4 runs,
    and `measurement_scope` reads that marker to drop them from
    `weight_covered`; both are `requires_tool_execution`, so S4 sees them.
    "Who produced this number" and "is this number about the delivered code"
    are two questions and only the first is S4's. The number is still replaced:
    both describe the same stubbed suite, and only one of them was measured.

    Pure and public so it can be checked without patching five private seams
    around `finalize_gate` — `tests/test_patch_discipline.py` refuses that, and
    `s4_block_details` above answered the same question the same way.
    """
    keep_source = current_source in _SOURCES_NOT_FRAMEWORK_MEASURED
    return {
        "score": harness_score,
        "score_source": current_source if keep_source else SCORE_SOURCE_FRAMEWORK,
        "fabrication": harness_score < threshold <= agent_score,
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


@dataclass
class GateContext:
    """
    Context object returned by prepare_gate().

    Contains everything Claude needs to perform an inline gate evaluation:
    - configuration loaded from the gate YAML
    - SAB baseline data from quality_manifest.json (architecture_constraints, high_risk_modules)
    - paths to embedded SSI scripts, prompts, and schemas
    - a work directory for writing gate{N}_result.json

    After evaluation Claude writes gate{N}_result.json to work_dir and calls
    finalize_gate(ctx) to complete threshold checks and manifest updates.
    """
    gate_num: int
    config: GateConfig | dict
    project_root: str
    phase: int
    fr_id: str | None
    ssi_scripts_dir: str
    ssi_prompts_dir: str
    ssi_schemas_dir: str
    work_dir: str
    sab_data: dict = field(default_factory=dict)
    tier3_context: dict = field(default_factory=dict)  # CRG Point 2 — per-dim context
    crg_safety_context: dict = field(default_factory=dict)  # CRG Points 3+4 — pre-computed
    auto_fix_rounds: int = 0
    # Per-FR test spec coverage: list of required test names (one entry per
    # TEST_SPEC.md row — a parametrized case legitimately repeats its function
    # name across multiple rows, see _parse_spec_names_for_fr) + a row-based
    # count of how many of those rows have their function present. Used by
    # finalize_gate() to cap test_coverage score at spec_coverage_pct. The
    # count (not a dedupe set) keeps numerator/denominator symmetric — see
    # fix/spec-cap-list-set-mismatch.
    _spec_test_names: list[str] = field(default_factory=list)
    _existing_spec_count: int = 0

    def evaluation_prompt(self) -> str:
        """Return a human-readable evaluation instruction for Claude."""
        if isinstance(self.config, GateConfig):
            dims = [d.name for d in self.config.dimensions]
            score_gate = self.config.score_gate
            max_rounds = self.config.max_rounds
        else:
            dims = [d["name"] for d in self.config.get("dimensions", [])]
            score_gate = self.config.get("score_gate", "n/a")
            max_rounds = self.config.get("max_rounds", 3)
        result_path = str(Path(self.work_dir) / f"gate{self.gate_num}_result.json")

        sab_lines = ""
        if self.sab_data:
            constraints = self.sab_data.get("architecture_constraints", [])
            high_risk = self.sab_data.get("high_risk_modules", [])
            nfr_map = self.sab_data.get("nfr_dimension_mapping", {})
            sab_lines = "\n[SAB Baseline — from quality_manifest.json]\n"
            if constraints:
                sab_lines += f"  architecture_constraints: {constraints}\n"
            if high_risk:
                sab_lines += f"  high_risk_modules: {high_risk}\n"
            qt = self.sab_data.get("quality_targets", {})
            if qt:
                sab_lines += "  quality_targets:\n"
                for k, v in qt.items():
                    sab_lines += f"    {k}: {v}\n"
                sab_lines += (
                    "  > Treat these as project-specific NFR thresholds "
                    "when evaluating dimensions.\n"
                )
            fr_mod_trace = self.sab_data.get("fr_module_traceability", {})
            if fr_mod_trace and self.fr_id:
                mod = fr_mod_trace.get(self.fr_id)
                if mod:
                    sab_lines += f"  {self.fr_id} responsible module: {mod}\n"
                    sab_lines += (
                        "  > Focus code review on this module "
                        "when evaluating implementation.\n"
                    )
            nfr_trace = self.sab_data.get("nfr_traceability", {})
            # Only show the flat mapping when detailed traceability is absent
            # (traceability is a strict superset of nfr_dimension_mapping).
            if nfr_map and not nfr_trace:
                sab_lines += f"  nfr_dimension_mapping: {nfr_map}\n"
            if nfr_trace:
                sab_lines += "  nfr_traceability (module → quality target):\n"
                for nfr_id, v in nfr_trace.items():
                    if isinstance(v, dict):
                        sab_lines += (
                            f"    {nfr_id}: [{v.get('type', '')}] "
                            f"{v.get('module', '')} — {v.get('target', '')}\n"
                        )
                sab_lines += (
                    "  > When evaluating NFR-related dimensions, "
                    "refer to the module and target above for concrete scope.\n"
                )
            nfr_fr_map = self.sab_data.get("nfr_fr_mapping", {})
            if nfr_fr_map:
                sab_lines += "  nfr_fr_mapping (NFR → FR scope):\n"
                for nfr_id, fr_list in nfr_fr_map.items():
                    sab_lines += f"    {nfr_id}: {fr_list}\n"
                sab_lines += (
                    "  > When evaluating NFR-related dimensions, "
                    "these FRs are in scope for each NFR.\n"
                )
            sab_lines += (
                "  > When evaluating the `architecture` dimension, validate code "
                "against these constraints.\n"
                "  > high_risk_modules deserve extra scrutiny in all dimensions.\n"
            )

        # CRG Point 2: Tier 3 guidance context
        crg_lines = ""
        if self.tier3_context:
            crg_lines = "\n[CRG Tier 3 Guidance — structural context for high-cost dimensions]\n"
            for dim_name, ctx in self.tier3_context.items():
                if ctx:
                    crg_lines += f"  {dim_name}: {ctx.get('task', ctx.get('summary', 'context available'))}\n"
            crg_lines += (
                "  > Use this structural context when evaluating Tier 3 dimensions"
                " (architecture, error_handling, readability, documentation, performance).\n"
            )

        # CRG Point 3: pre-computed safety context.
        # Point 4 (post-round drift guardrail) only ran inside auto-fix rounds — no longer wired.
        if self.crg_safety_context:
            crg_lines += "\n[CRG Safety Context — pre-computed by HarnessBridge]\n"
            pre_fix = self.crg_safety_context.get("pre_fix_safety", {})
            if pre_fix:
                safe = "SAFE" if pre_fix.get("safe", True) else "UNSAFE"
                crg_lines += f"  pre_fix_safety: {safe} — {pre_fix.get('message', '')}\n"
            xp_drift = self.crg_safety_context.get("cross_phase_drift")
            if xp_drift:
                drift_val = xp_drift.get("drift", 0)
                level = "CRITICAL" if drift_val > 0.5 else ("WARNING" if drift_val > 0.3 else "STABLE")
                bl_phase = xp_drift.get("baseline_phase", "?")
                crg_lines += (
                    f"  cross_phase_drift: {level} — {drift_val:.3f} "
                    f"(baseline=P{bl_phase}, sha={xp_drift.get('baseline_sha','?')[:8]})\n"
                )
                if drift_val > 0.5:
                    crg_lines += (
                        "  > CRITICAL: significant structural degradation since last phase exit.\n"
                        "  > Increase architecture/error_handling scrutiny in this gate evaluation.\n"
                    )
                elif drift_val > 0.3:
                    crg_lines += (
                        "  > WARNING: moderate structural drift since last phase exit.\n"
                        "  > Review architecture findings against baseline changes.\n"
                    )
            crg_lines += (
                "  > Before each fix round, defer if pre_fix_safety is UNSAFE.\n"
            )

        return (
            f"Gate {self.gate_num} evaluation ready.\n"
            f"  project   : {self.project_root}\n"
            f"  phase     : {self.phase}\n"
            f"  fr_id     : {self.fr_id or 'n/a'}\n"
            f"  dimensions: {', '.join(dims) if dims else 'see gate config'}\n"
            f"  score_gate: {score_gate}\n"
            f"  max_rounds: {max_rounds}\n"
            f"{sab_lines}"
            f"{crg_lines}"
            f"\nFollow  : {self.ssi_prompts_dir}/evaluate_dimension.md\n"
            f"Scripts : {self.ssi_scripts_dir}/\n"
            f"Write result to: {result_path}\n"
            f"\nAfter writing result.json, run:\n"
            f"  python3 harness_cli.py finalize-gate {self.gate_num} "
            f"--project-root {self.project_root} --phase {self.phase}"
            + (f" --fr-id {self.fr_id}" if self.fr_id else "")
            + "\n"
        )


@dataclass
class EnvCheckContext:
    """Context object returned by prepare_env_check().

    Contains project documentation excerpts that Claude uses to determine what
    environment variables, CLI tools, and infrastructure services are required,
    then verify them against the current environment.

    After evaluation Claude writes .sessi-work/env_check_result.json and calls
    finalize_env_check() to verify completeness.
    """
    project_root: str
    phase: int
    fr_id: str | None
    ssi_schemas_dir: str
    work_dir: str
    sad_excerpt: str = ""
    srs_excerpt: str = ""
    docker_compose_excerpt: str = ""

    def evaluation_prompt(self) -> str:
        """Return the evaluation instruction for Claude."""
        result_path = str(Path(self.work_dir) / "env_check_result.json")
        schema_path = str(Path(self.ssi_schemas_dir) / "env_check_result.schema.json")

        parts: list[str] = []

        if self.sad_excerpt:
            parts.append(
                "[SAD.md — Architecture & Technology]\n"
                f"{self.sad_excerpt}"
            )
        if self.srs_excerpt:
            parts.append(
                "[SRS.md — Requirements & Verification Methods]\n"
                f"{self.srs_excerpt}"
            )
        if self.docker_compose_excerpt:
            parts.append(
                "[docker-compose.yml — Infrastructure Services]\n"
                f"{self.docker_compose_excerpt}"
            )

        fr_line = f"  FR-ID    : {self.fr_id}\n" if self.fr_id else ""

        return (
            f"{'='*60}\n"
            f"run-env-check: Phase {self.phase} | project: {self.project_root}\n"
            f"{'='*60}\n"
            f"  Phase    : {self.phase}\n"
            f"{fr_line}"
            f"\n"
            + "\n\n".join(parts) +
            f"\n\n{'─'*60}\n"
            f"[TASK — Evaluate Environment Readiness]\n\n"
            f"1. IDENTIFY all required items from the project docs above:\n"
            f"   a. Environment variables (from app.infrastructure.config / FR-21)\n"
            f"   b. CLI tools (from Technology Choices / verification methods)\n"
            f"   c. Infrastructure services (from Architecture layers / docker-compose)\n"
            f"   d. Test framework + extensions (from verification methods / constraints)\n\n"
            f"2. VERIFY each item — run ALL checks in ONE shot, never one-by-one:\n"
            f"   [CRITICAL] Burning turns on individual commands will leave no room to\n"
            f"   write the result JSON. Do this instead:\n"
            f"   a. mkdir -p .sessi-work\n"
            f"   b. Write a single verification script (e.g., `.sessi-work/verify.sh`) that\n"
            f"      chains all `which`, `echo $VAR`, and connectivity checks together.\n"
            f"   c. Run the script once to collect all results.\n"
            f"   d. Write the result JSON to {result_path} in a\n"
            f"      single Write tool call — do NOT chain writes.\n"
            f"   If the script fails, run remaining checks individually and report partial\n"
            f"   findings rather than writing nothing.\n\n"
            f"3. REPORT findings to {result_path}\n"
            f"   Schema: {schema_path}\n"
            f"   For each missing item, include the exact install/fix command.\n"
            f"   [NAMING CONTRACT] cli_tools[].name MUST be the exact name you\n"
            f"   executed or imported during verification: the executable name as\n"
            f"   invoked (e.g. `python3.11`, NOT `python311` or `Python 3.11`), or\n"
            f"   the importable module name for library/plugin requirements (e.g.\n"
            f"   `pytest_cov`). Never invent logical aliases — the framework\n"
            f"   independently re-verifies every name you claim present.\n\n"
            f"   [SCHEMA CONTRACT: env_vars — required vs optional_missing]\n"
            f"   The env_vars object has TWO fields with different trust models.\n"
            f"   Using the wrong field causes the framework to reject your result:\n"
            f"   \n"
            f"   env_vars.required[].present: true  → INDEPENDENTLY VERIFIED\n"
            f"     The framework re-checks EVERY such claim by looking up the\n"
            f"     name in os.environ. If the var is NOT actually exported, the\n"
            f"     framework FAILS the entire env-check as \"fabricated claims\".\n"
            f"     ONLY mark present:true for vars confirmed exported (echo $VAR\n"
            f"     produces a value). There are NO carve-outs for env vars.\n"
            f"   \n"
            f"   env_vars.optional_missing (array of strings)  → TRUSTED BY DESIGN\n"
            f"     For vars that have baked-in defaults in the project's config\n"
            f"     (config.py, SPEC §5.1, pyproject.toml, .env.example) but are\n"
            f"     NOT exported. These are NOT missing — the project runs\n"
            f"     correctly with its defaults. The framework cannot parse\n"
            f"     project source to verify config defaults, so this field is\n"
            f"     trusted by design (same trust model as infra_services).\n"
            f"   \n"
            f"   CLASSIFICATION RULE for each env var from project docs:\n"
            f"     - Exported in current shell?            → required.present: true\n"
            f"     - Not exported, HAS documented default?  → optional_missing (name)\n"
            f"     - Not exported, is a TEST/DEV-ONLY OPT-IN FLAG (docs describe\n"
            f"       it as gating a test-only or development-only code path,\n"
            f"       explicitly off/disabled/rejected by default in production)?\n"
            f"                                              → optional_missing (name)\n"
            f"     - Not exported, none of the above?        → required.present: false\n"
            f"   \n"
            f"   Example: the project's config docs list DATABASE_URL with\n"
            f"   default \"postgresql://localhost:5432/db\". If not exported: put\n"
            f"   \"DATABASE_URL\" in optional_missing, leave env_vars.required empty.\n"
            f"   The project IS ready with zero exported env vars when every var\n"
            f"   it needs has a documented default.\n\n"
            f"   Example: the project's docs describe FEATURE_X_DEBUG_HOOK as \"a\n"
            f"   test/development opt-in flag; the feature it gates is rejected by\n"
            f"   default in production when unset.\" If not exported: put\n"
            f"   \"FEATURE_X_DEBUG_HOOK\" in optional_missing — its absence in\n"
            f"   production is the INTENDED state, not a missing requirement. Do\n"
            f"   NOT put a test/dev-only opt-in flag into required.present:false\n"
            f"   just because it lacks a config-style default VALUE.\n\n"
            f"[FORBIDDEN]\n"
            f"- Guessing env var values — only check presence, not correctness\n"
            f"- Fabricating check results without actual tool execution\n"
            f"- Skipping a category because it seems obvious\n"
            f"- Writing result.json without running real verification commands\n"
            f"- Putting vars with baked-in defaults into required[].present:true\n"
            f"  when they are NOT exported — this WILL be flagged as fabrication\n\n"
            f"{'─'*60}\n"
            f"NEXT: After writing result.json, run:\n"
            f"  python harness_cli.py finalize-env-check "
            f"--phase {self.phase} --project {self.project_root}"
            + (f" --fr-id {self.fr_id}" if self.fr_id else "")
            + "\n" + "─"*60 + "\n"
        )


def _extract_fr_section(srs_text: str, fr_id: str) -> str:
    """Extract the ### FR-XX: section from SRS.md for a given fr_id.

    Falls back to the full text (up to 60K chars) if the section is not found.
    """
    pattern = re.compile(
        rf"(^### {re.escape(fr_id)}[:\s].*?)(?=^###\s+(?:FR|NFR)-|^##\s+|^---+|\Z)",
        re.MULTILINE | re.DOTALL,
    )
    m = pattern.search(srs_text)
    return m.group(1).strip() if m else srs_text[:60_000]


def _parse_spec_names_for_fr(spec_text: str, fr_id: str) -> list[str]:
    """Extract test function names for *fr_id* from TEST_SPEC.md text.

    Canonical parser used by both prepare_gate() and _parse_test_spec().
    Terminates the current FR section on:
      - A new ### FR-XX / ### NFR-XX header
      - Any H2 heading (## …) — e.g. ## Cross-Cutting Integration Tests
      - A horizontal rule (---) — used as section divider in some spec styles
    Supports both old bullet-list format and the current Markdown-table format.
    """
    import re as _re
    names: list[str] = []
    current_fr = ""
    in_table = False
    for line in spec_text.splitlines():
        stripped = line.strip()
        # H3 FR/NFR header → switch section
        m = _re.match(r"^###\s+([A-Z]+-\d+)(?:[:\s]|$)", stripped)
        if m:
            current_fr = m.group(1)
            in_table = False
            continue
        # H2 heading (including ## Cross-Cutting) → close current section
        if _re.match(r"^##\s+\S", stripped) and not stripped.startswith("###"):
            current_fr = ""
            in_table = False
            continue
        # Horizontal rule → close current table (but stay in same FR until next header)
        if _re.match(r"^---+$", stripped) or _re.match(r"^\*\*\*+$", stripped):
            in_table = False
            continue
        if current_fr != fr_id:
            continue
        # Old bullet-list format: - `test_foo`
        fn_m = _re.match(r"^\s*-\s*`?(test_[^`\s]+)`?", line)
        if fn_m:
            names.append(fn_m.group(1))
            continue
        # Markdown table header row
        if "|" in stripped and _re.search(r"Test Function", stripped, _re.IGNORECASE):
            in_table = True
            continue
        # Table separator row
        if in_table and _re.match(r"^\|[-| ]+\|$", stripped):
            continue
        # Table data row
        if in_table and stripped.startswith("|") and stripped.endswith("|"):
            cols = [c.strip() for c in stripped.split("|")[1:-1]]
            if len(cols) >= 2:
                raw_fn = cols[1].strip("`").strip()
                if raw_fn.startswith("test_"):
                    names.append(raw_fn)
        elif in_table and not stripped.startswith("|") and stripped:
            in_table = False
    return names


class HarnessBridge:
    """
    Gate lifecycle controller — two-phase API (prepare_gate → finalize_gate).

    Handles gate configuration loading, CRG integration, result parsing, threshold
    enforcement, and quality manifest updates. The SSI evaluation engine (prompts,
    scripts, schemas) is embedded in harness/ssi/.
    """

    def __init__(self):
        """Initialize the bridge with its dependent subsystems."""
        self.crg = CRGBridge()        # gracefully degrades if CRG unavailable
        self._log = DecisionLogWriter()
        self._effort = EffortTracker()
        self._last_gate_num: int | None = None

    def _load_manifest_sab(self, project_root: str) -> dict:
        """Read SAB-derived fields from quality_manifest.json. Returns empty dict on failure."""
        from core.state_io import StateCorruptError, load_quality_manifest
        manifest_path = Path(project_root) / ".methodology" / "quality_manifest.json"
        if not manifest_path.exists():
            return {}
        try:
            manifest = load_quality_manifest(project_root)
            return {
                "nfr_dimension_mapping":    manifest.get("nfr_dimension_mapping", {}),
                "nfr_traceability":         manifest.get("nfr_traceability", {}),
                "nfr_fr_mapping":           manifest.get("nfr_fr_mapping", {}),
                "quality_targets":          manifest.get("quality_targets", {}),
                "fr_module_traceability":   manifest.get("fr_module_traceability", {}),
                "gate_score_overrides":     manifest.get("gate_score_overrides", {}),
                "architecture_constraints": manifest.get("architecture_constraints", []),
                "high_risk_modules":        manifest.get("high_risk_modules", []),
            }
        except StateCorruptError as exc:
            # Manifest is corrupt / unreadable — surface as a
            # WARNING so a real SAB outage is visible in logs, but
            # return {} so the gate can still proceed with default
            # behaviour. Without the log entry, a truncated JSON
            # would silently turn strict SAB enforcement into
            # default behaviour with zero forensic trail.
            import logging as _logging
            _logging.getLogger(__name__).warning(
                "_load_manifest_sab: manifest parse/read failed (%s: %s); "
                "SAB-derived gate_score_overrides are DISABLED for this gate.",
                type(exc.original).__name__, exc.original,
            )
            return {}

    def prepare_env_check(
        self,
        project_root: str,
        phase: int,
        fr_id: str | None = None,
    ) -> EnvCheckContext:
        """Build an EnvCheckContext with project documentation excerpts.

        Reads SAD.md + SRS.md from the project root, extracts key sections
        (architecture layers, infrastructure config, technology choices,
        verification methods), and returns an EnvCheckContext whose
        evaluation_prompt() Claude uses for inline environment readiness
        evaluation.

        docker-compose.yml is included if present (for service health checks).
        """
        root = Path(project_root)

        sad_full = ""
        sad_path = None
        for sad_candidate in [
            root / "SAD.md",
            ProjectLayout(root).phase2_architecture_dir / "SAD.md",
            root / "architecture" / "SAD.md",
            root / "docs" / "SAD.md",
        ]:
            if sad_candidate.exists():
                sad_full = sad_candidate.read_text(encoding="utf-8")
                sad_path = sad_candidate
                break
        sad_excerpt = ""
        if sad_full:
            max_sad = 60_000
            if len(sad_full) > max_sad:
                sad_excerpt = (
                    sad_full[:max_sad]
                    + f"\n\n[... truncated at {max_sad} chars — full content at {sad_path} ...]"
                )
            else:
                sad_excerpt = sad_full

        srs_full = ""
        srs_path = None
        for srs_candidate in [
            root / "SRS.md",
            ProjectLayout(root).srs_path,
            root / "requirements" / "SRS.md",
            root / "docs" / "SRS.md",
        ]:
            if srs_candidate.exists():
                srs_full = srs_candidate.read_text(encoding="utf-8")
                srs_path = srs_candidate
                break
        srs_excerpt = ""
        if srs_full:
            max_srs = 60_000
            if fr_id:
                srs_excerpt = _extract_fr_section(srs_full, fr_id)
            elif len(srs_full) > max_srs:
                srs_excerpt = (
                    srs_full[:max_srs]
                    + f"\n\n[... truncated at {max_srs} chars — full content at {srs_path} ...]"
                )
            else:
                srs_excerpt = srs_full

        dc_excerpt = ""
        dc = root / "docker-compose.yml"
        if dc.exists():
            dc_excerpt = dc.read_text(encoding="utf-8")[:2000]

        ssi_dir = Path(__file__).parent / "ssi"
        work_dir = root / ".sessi-work"
        # Note: callers that write to work_dir (cmd_run_env_check) are
        # responsible for mkdir. prepare_env_check is read-only.

        return EnvCheckContext(
            project_root=project_root,
            phase=phase,
            fr_id=fr_id,
            ssi_schemas_dir=str(ssi_dir / "schemas"),
            work_dir=str(work_dir),
            sad_excerpt=sad_excerpt,
            srs_excerpt=srs_excerpt,
            docker_compose_excerpt=dc_excerpt,
        )

    def finalize_env_check(self, ctx: EnvCheckContext) -> tuple[bool, str]:
        """Read env_check_result.json and verify it passes schema + readiness.

        Returns (ready, summary_message).
        """
        import json as _json
        result_path = Path(ctx.work_dir) / "env_check_result.json"
        if not result_path.exists():
            return False, f"Result file not found: {result_path} — run run-env-check first"
        try:
            data = _json.loads(result_path.read_text(encoding="utf-8"))
        except Exception:
            return False, f"Result file is malformed JSON: {result_path}"

        ready = data.get("ready", False)
        summary = data.get("summary", "No summary provided.")
        checked_at = data.get("checked_at")
        env_vars = data.get("env_vars", {})
        cli_tools = data.get("cli_tools", {})
        infra = data.get("infra_services", {})

        # Minimal anti-fabrication: required fields must be present
        if checked_at is None:
            return False, "Result missing required field: checked_at"
        if not isinstance(env_vars.get("required"), list):
            return False, "Result missing required field: env_vars.required"
        if not isinstance(cli_tools.get("required"), list):
            return False, "Result missing required field: cli_tools.required"
        if not isinstance(infra.get("required"), list):
            return False, "Result missing required field: infra_services.required"

        if ready:
            return True, f"Environment ready.\n{summary}"
        else:
            return False, f"Environment NOT ready.\n{summary}"

    def prepare_gate(
        self,
        gate_num: int,
        project_root: str,
        phase: int,
        fr_id: str | None = None,
        auto_fix_rounds: int = 0,
    ) -> GateContext:
        """
        Phase 1 of the two-phase gate evaluation API.

        Loads gate configuration, optionally triggers CRG reconnaissance,
        reads SAB baseline from quality_manifest.json, and returns a GateContext
        that Claude uses to perform inline evaluation.

        The caller (Claude) should:
        1. Read ctx.evaluation_prompt() for instructions.
        2. Evaluate all dimensions, writing ctx.work_dir/gate{N}_result.json.
        3. Call finalize_gate(ctx) to complete threshold checks + manifest update.

        Args:
            gate_num: Gate ID (1–4).
            project_root: Absolute path to the target project.
            phase: Current methodology phase.
            fr_id: Functional requirement ID (Gate 1 per-FR only).

        Returns:
            GateContext with all paths and config Claude needs.
        """
        self._last_gate_num = gate_num
        config = self._load_config(gate_num)
        # Round 60 站2: the dimension list the YAML declares is the list the
        # gate judges. `22e2471` inserted a feature-flag filter here so
        # `evaluation_prompt()` would stop advertising a dimension the
        # orchestrator was not going to score; the flags themselves are now
        # retired (core.harness_config.RETIRED_FEATURES), so there is nothing
        # left to filter and nothing between the declaration and the prompt.
        if auto_fix_rounds:
            config = GateConfig(
                gate_num=config.gate_num, score_gate=config.score_gate,
                dimensions=config.dimensions, per_dim_min=config.per_dim_min,
                max_rounds=auto_fix_rounds, blocking=config.blocking,
                trigger=config.trigger, scope=config.scope, crg=config.crg,
            )

        # CRG Point 1: structural reconnaissance for gates that require it (Gate 3/4)
        if config.crg.get("reconnaissance"):
            self.crg.run_reconnaissance(project_root)

        # CRG Gate 2: lightweight graph refresh for impact check (no full recon).
        # Gate 2 declares impact_check but not reconnaissance — ensure graph exists
        # so pre-fix blast radius checks have structural data to work with.
        # CRG is mandatory; refresh failure is a blocking error, same as Gate 3/4.
        if config.crg.get("impact_check") and not config.crg.get("reconnaissance"):
            self.crg.refresh_graph(project_root)

        # CRG Point 2: Tier 3 guidance — get minimal context for each Tier 3 dimension
        tier3_context: dict[str, dict] = {}
        if config.crg.get("tier3_guidance"):
            for dim in config.dimensions:
                if dim.tier == 3:
                    tier3_context[dim.name] = self.crg.get_minimal_context(
                        project_root, dim.name
                    )

        # CRG Point 2b: knowledge gaps enrichment for test_coverage context
        # get_knowledge_gaps surfaces untested hotspots (test_coverage is Tier 1
        # so it's not in the tier3 loop; inject separately).
        if config.crg.get("tier3_guidance") or config.crg.get("reconnaissance"):
            _kg = self.crg.get_knowledge_gaps(project_root)
            if _kg:
                tier3_context.setdefault("test_coverage", {})
                tier3_context["test_coverage"]["knowledge_gaps"] = _kg

        # CRG cross-phase drift: compare current structure against previous exit gate baseline.
        # Only meaningful for Gate 3 (P4, baseline=P3) and Gate 4 (P6, baseline=P4).
        # Gate 2 may lack metrics (no full recon), so baseline may be absent.
        # The architecture dimension first appears at Gate 3 (P4), so the earliest
        # architecture baseline is crg_baseline_p4; there is no p3 baseline (Gate 2
        # has no architecture dim). Drift is therefore only valid at Gate 4 (P6 vs P4) —
        # the old {4: 3} entry pointed at a baseline that is never generated.
        _cross_phase_drift = None
        _baseline_phase_map = {6: 4}  # gate phase → previous exit gate phase (P6 vs P4)
        _prev_phase = _baseline_phase_map.get(phase)
        if _prev_phase is not None:
            _baseline_path = (
                Path(project_root) / ".methodology"
                / f"crg_baseline_p{_prev_phase}.json"
            )
            if _baseline_path.is_file():
                try:
                    import json as _json
                    _baseline = _json.loads(_baseline_path.read_text(encoding="utf-8"))
                    _current_metrics_path = (
                        Path(project_root) / ".sessi-work" / "crg_metrics.json"
                    )
                    if _current_metrics_path.is_file():
                        _current = _json.loads(_current_metrics_path.read_text(encoding="utf-8"))
                        from harness.ssi.scripts.crg_analysis import compute_structural_drift
                        _drift = compute_structural_drift(_baseline, _current)
                        _drift_threshold = config.crg.get("drift_threshold", 0.4)
                        _regressed = _drift >= _drift_threshold
                        _cross_phase_drift = {
                            "drift": _drift,
                            "baseline_phase": _prev_phase,
                            "baseline_sha": _baseline.get("_baseline_sha", "unknown"),
                            "drift_threshold": _drift_threshold,
                            "regression": _regressed,
                        }
                        if _regressed:
                            # Soft block: surface the regression loudly (was silently
                            # advisory). Agents/reports see it via crg_safety_context.
                            print(
                                f"[CRG] ⚠ architecture regression vs P{_prev_phase} "
                                f"baseline: drift={_drift:.2f} ≥ threshold "
                                f"{_drift_threshold:.2f} "
                                f"(baseline_sha={_baseline.get('_baseline_sha', '?')[:8]})",
                                flush=True,
                            )
                except Exception as _xp_exc:
                    print(
                        f"[CRG] WARN: cross-phase drift skipped — {_xp_exc}",
                        flush=True,
                    )

        # CRG Point 3: pre-compute pre-fix safety context (not just text hints).
        # Point 4 (post-round drift check) only ran inside auto-fix rounds — no longer wired.
        crg_safety_context: dict[str, dict] = {}
        if _cross_phase_drift is not None:
            crg_safety_context["cross_phase_drift"] = _cross_phase_drift
        if config.crg.get("impact_check") or config.crg.get("enabled"):
            crg_safety_context["pre_fix_safety"] = self.check_pre_fix_safety(project_root)

        ssi_dir = Path(__file__).parent / "ssi"
        work_dir = Path(project_root) / ".sessi-work"
        work_dir.mkdir(parents=True, exist_ok=True)

        sab_data = self._load_manifest_sab(project_root)

        # ── Per-FR test spec coverage ────────────────────────────────────
        # Used by finalize_gate() to cap test_coverage score at spec coverage %,
        # so incomplete test suites don't get a falsely high score when existing
        # tests all pass at 100% coverage.
        _spec_names: list[str] = []
        _existing_spec_count: int = 0
        if fr_id and gate_num == 1 and phase in PER_FR_GATE1_PHASES:
            _layout = ProjectLayout(project_root)
            _test_dir = _layout.active_test_dir
            _spec_path = _layout.test_spec_path
            if _spec_path.exists():
                try:
                    _spec_text = _spec_path.read_text(encoding="utf-8")
                    _spec_names = _parse_spec_names_for_fr(_spec_text, fr_id)
                    # Validate: warn if FR section exists but has no table header
                    # (missing header means 0 spec names even though rows exist)
                    _fr_section_exists = bool(
                        re.search(r"^###\s+" + re.escape(fr_id) + r"(?:[:\s]|$)", _spec_text, re.MULTILINE)
                    )
                    if _fr_section_exists and not _spec_names:
                        print(
                            f"  [WARN] TEST_SPEC.md: {fr_id} section found but no test functions "
                            f"parsed. Check that the section contains a valid table header row:\n"
                            f"    | # | Test Function | Type | Derivation |\n"
                            f"    |---|---|---|---|\n"
                            f"  If the header row is missing, insert it above the data rows."
                        )
                except OSError:
                    pass
            if _spec_names and _test_dir.is_dir():
                try:
                    # Language-aware scan (matches core.quality_gate.spec_coverage's
                    # _run_spec_coverage_check, which this cap is meant to mirror) —
                    # a hardcoded *.py glob here would silently zero-cap test_coverage
                    # for js/ts projects, since no test function would ever match.
                    from core.quality_gate.spec_coverage import (
                        _get_test_directories,
                        _scan_test_functions,
                    )
                    from core.utils.lang_patterns import project_language
                    _lang = project_language(project_root)
                    _actual_fns: set[str] = set()
                    for _tdir in _get_test_directories(Path(project_root)):
                        _actual_fns |= _scan_test_functions(_tdir, _lang)
                    # Row-based count, NOT a dedupe set: _spec_names has one entry
                    # per TEST_SPEC.md row, and a parametrized case legitimately
                    # repeats its function name across multiple rows. Deduping the
                    # numerator while the denominator stays row-based mathematically
                    # caps the score below 100% even when every required test exists
                    # (see fix/spec-cap-list-set-mismatch).
                    for fn in _spec_names:
                        raw_fn = fn.strip("`").strip()
                        raw_fn = re.sub(r"\[.*\]$", "", raw_fn)
                        raw_fn = re.sub(r"\(\)$", "", raw_fn)
                        if raw_fn in _actual_fns:
                            _existing_spec_count += 1
                except OSError:
                    pass

        return GateContext(
            gate_num=gate_num,
            config=config,
            project_root=project_root,
            phase=phase,
            fr_id=fr_id,
            ssi_scripts_dir=str(ssi_dir / "scripts"),
            ssi_prompts_dir=str(ssi_dir / "prompts"),
            ssi_schemas_dir=str(ssi_dir / "schemas"),
            work_dir=str(work_dir),
            sab_data=sab_data,
            tier3_context=tier3_context,
            crg_safety_context=crg_safety_context,
            auto_fix_rounds=auto_fix_rounds,
            _spec_test_names=_spec_names,
            _existing_spec_count=_existing_spec_count,
        )

    def finalize_gate(self, ctx: GateContext) -> GateResult:
        """
        Phase 2 of the two-phase gate evaluation API.

        Reads gate{N}_result.json written by Claude's inline evaluation,
        checks thresholds, updates the quality manifest, and records decisions.

        Round 38 removed the `da_waivers` parameter. A threshold is no longer
        waivable by anything, so there is no argument that could bypass one —
        which is the point: while the parameter existed, a caller could
        reintroduce threshold-zeroing without touching da_waiver.py.

        Args:
            ctx: The GateContext returned by prepare_gate().

        Returns:
            GateResult if gate passes all thresholds.

        Raises:
            FileNotFoundError: If Claude did not write gate{N}_result.json.
            GateBlockedError: If the gate fails its quality targets.
        """
        import time

        result_path = Path(ctx.work_dir) / f"gate{ctx.gate_num}_result.json"
        if not result_path.exists():
            raise FileNotFoundError(
                f"gate{ctx.gate_num}_result.json not found in {ctx.work_dir}. "
                f"Claude must evaluate and write results before calling finalize_gate()."
            )

        t0 = time.time()
        # A truncated/corrupt gate_result.json would otherwise raise
        # an uncaught JSONDecodeError that bypasses the GateBlockedError
        # contract documented at the top of this method. Convert it
        # to a GateBlockedError with a clear message.
        try:
            raw = json.loads(result_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise GateBlockedError(
                ctx.gate_num,
                GateResult(
                    gate_num=ctx.gate_num,
                    score=0.0,
                    dimensions=[],
                    open_critical=1,
                    open_high=0,
                    quality_complete=False,
                    rounds_used=0,
                ),
                details={
                    "malformed_gate_result": (
                        f"gate{ctx.gate_num}_result.json is not valid JSON: {exc}. "
                        "Re-run the gate so the agent writes a well-formed result."
                    ),
                },
            ) from exc

        # ── Round 21 站2: the shape contract, enforced ───────────────────────
        # harness/ssi/schemas/harness_gate_result.schema.json existed from the
        # start and was loaded by nothing, so it drifted into describing a file
        # no run produces while every consumer guessed at field names — which is
        # how the DA-waiver safeguard came to read `tool_score`, then `target`,
        # neither of which any writer emits. Validate once, here, at the read
        # that actually drives scoring.
        from core.quality_gate.gate_result_schema import validate_gate_result
        _shape = validate_gate_result(raw)
        if not _shape.valid:
            _shape_msg = _shape.as_block_message(ctx.gate_num)
            print(f"\n[BLOCKED] {_shape_msg}", file=sys.stderr)
            raise GateBlockedError(
                ctx.gate_num,
                GateResult(
                    gate_num=ctx.gate_num, score=0.0, dimensions=[],
                    open_critical=1, open_high=0,
                    quality_complete=False, rounds_used=0,
                ),
                details={"malformed_gate_result": list(_shape.violations)},
            )

        # ── Round 12 站3b: INFRA_FAIL ≠ quality failure ──────────────────────
        # Reject zero scores whose evidence carries a run-gate PRECONDITION-
        # block signature BEFORE any of them can enter the manifest as fake
        # quality zeros (2026-07-16 phantom-module incident: 3 dims zeroed,
        # CODE-FIX dispatched at healthy code). The BLOCK message is the
        # navigation: fix the precondition, not the source.
        _infra_violations = _check_infra_fail_pollution(raw)
        if _infra_violations:
            print("\n[INFRA_FAIL] gate result rejected — infrastructure failure "
                  "recorded as quality zero(s); NOT a code-quality verdict:")
            for _v in _infra_violations:
                print(f"  - {_v}")
            raise GateBlockedError(
                ctx.gate_num,
                GateResult(
                    gate_num=ctx.gate_num,
                    score=0.0,
                    dimensions=[],
                    open_critical=len(_infra_violations),
                    open_high=0,
                    quality_complete=False,
                    rounds_used=0,
                ),
                details={"infra_fail": _infra_violations},
            )

        # ── S3: Tool execution evidence enforcement ──────────────────────────
        # For dimensions with requires_tool_execution:true in the gate YAML,
        # the result JSON must include tool_output (path to raw tool output)
        # or tool_evidence (inline snippet). Prevents LLM score fabrication
        # when tools are installed but never actually run.
        # S3-A (Solution A): content of those files/strings is also validated —
        # stub comments, files that are too small, and content that does not match
        # the expected tool output structure are all rejected.
        # Round 27 站3: fingerprint each piece of evidence as S3 clears it, and
        # carry the fingerprints into the result below.
        #
        # Round 45 站1: that sentence used to end "…and cannot be separated by
        # a cleanup of the gitignored work directory", and it was half true.
        # The fingerprints were kept; the files they fingerprint were not.
        # Measured across five projects' committed gate results, 149 of 162
        # cited tool_output paths no longer resolve, because every one of them
        # points under `.sessi-work/` — the first line the harness writes into
        # each project's own .gitignore. A sha256 of a file nobody has is a
        # claim that cannot be checked.
        #
        # So the citations are re-pointed at copies under `.methodology/`
        # BEFORE S3 reads them: every check below then runs on the file a
        # later reader can actually open, and the digest's `source` names it.
        # This adds no judgement — S3's existence, containment and content
        # checks are untouched.
        from core.quality_gate.gate_evidence_store import persist_cited_evidence
        if persist_cited_evidence(Path(ctx.project_root), ctx.gate_num, raw):
            # The re-pointing has to reach the file, not just this dict: the
            # digest block below re-reads `result_path` from disk, and
            # cli/gate_cmds.py copies that same file to
            # .methodology/gate{N}_result.json. Written only when something
            # actually moved, so a run that changes nothing leaves the agent's
            # bytes alone.
            _atomic_write_gate_result(result_path, raw)

        _evidence_digests: dict = {}
        _tool_violations = _check_tool_evidence(ctx, raw, _evidence_digests)
        if _evidence_digests:
            # Persist immediately, in the same shape the CRG enrichment below
            # already uses (read → add field → atomic write): the digest is only
            # true of the files as they are RIGHT NOW, and a later BLOCK on some
            # other check must not throw away the record of what this run read.
            try:
                _gr = json.loads(result_path.read_text(encoding="utf-8"))
                if isinstance(_gr, dict):
                    _gr["evidence_digest"] = _evidence_digests
                    _atomic_write_gate_result(result_path, _gr)
                    raw["evidence_digest"] = _evidence_digests
            except (OSError, json.JSONDecodeError) as _exc:
                print(f"[WARN] could not record evidence digests: {_exc}",
                      file=sys.stderr)
        if _tool_violations:
            raise GateBlockedError(
                ctx.gate_num,
                GateResult(
                    gate_num=ctx.gate_num,
                    score=0.0,
                    dimensions=[],
                    open_critical=len(_tool_violations),
                    open_high=0,
                    quality_complete=False,
                    rounds_used=0,
                ),
                details={"tool_evidence_missing": _tool_violations},
            )

        # ── Round 51 站2: which declared constraints nobody checks ───────────
        # The SAB's `architecture_constraints` list reaches CLAUDE.md and the
        # evaluation prompt above and nothing else, so "the agent was told" has
        # been the whole enforcement. Classify it here, at the moment the gate
        # is decided, and leave the ones with no executor in the ledger —
        # taskq-api's VERIFICATION_REPORT certified five constraints honoured
        # while two of them were being violated in the delivered tree.
        from core.quality_gate.arch_constraints import (
            record_constraint_status,
            unconfigured_blocking_reason,
        )
        _constraint_rows = record_constraint_status(ctx.project_root, ctx.sab_data)

        # ── Round 54: the state the project can actually fix ────────────────
        # Round 51 站2 recorded all of these and blocked on none, which was
        # right while the two available states were "checked" and "nothing can
        # check it". Station 1 split off the third: a tool the framework
        # already runs decides this constraint, and this project has not
        # configured it. Measured across the seven projects here, 8 of the 23
        # constraints are in that state — taskq-super declares
        # `no_circular_dependencies` and `sqlalchemy_only_in_repository` and
        # ships an import-linter config carrying neither a `layers` nor a
        # `forbidden` contract.
        #
        # `declared_only` is still never blocked, and the reason is in
        # `unconfigured_blocking_reason`: the only way to satisfy a block on a
        # constraint nothing can decide is to delete the declaration, which
        # makes the SAB less true rather than the code better.
        _constraint_reason = unconfigured_blocking_reason(_constraint_rows)
        if _constraint_reason:
            raise GateBlockedError(
                ctx.gate_num,
                GateResult(
                    gate_num=ctx.gate_num, score=0.0, dimensions=[],
                    open_critical=1, open_high=0,
                    quality_complete=False, rounds_used=0,
                ),
                details={"arch_constraint_unconfigured": [_constraint_reason]},
            )

        # ── Round 51 站3: a number measured over a suite that removed the
        # thing it measures ──────────────────────────────────────────────────
        _mark_stubbed_boundary_dimensions(ctx, raw)

        # ── Round 51 站4: which files left the coverage denominator ─────────
        _record_coverage_denominator(ctx)

        # ── Round 52 站1: whether the verification target verifies anything ──
        # `execute_verification_target` is the only dimension that executes the
        # delivered system, and what it executes is a recipe the judged project
        # writes. Round 46 站5 made it run at every exit; nothing ever read it.
        # Measured on the six projects here: two re-run tools the gate already
        # scored and never name the delivered package, and one invokes it
        # behind `|| true`. The ledger row goes in either way; the block is
        # only for those two shapes (see verify_target.blocking_reason).
        from core.quality_gate.verify_target import (
            blocking_reason as _verify_target_block,
            record_verify_target_status,
        )
        record_verify_target_status(ctx.project_root)
        _vt_reason = _verify_target_block(ctx.project_root)
        if _vt_reason:
            raise GateBlockedError(
                ctx.gate_num,
                GateResult(
                    gate_num=ctx.gate_num, score=0.0, dimensions=[],
                    open_critical=1, open_high=0,
                    quality_complete=False, rounds_used=0,
                ),
                details={"verify_target": [_vt_reason]},
            )

        # ── S4: Harness cross-validation (Solution B) ────────────────────────
        # For each Tier 1/2 dimension where the agent claims a passing score,
        # the harness independently runs the tool and computes its own score.
        # If harness_score < threshold but agent_score ≥ threshold, the gate is
        # blocked with a fabrication violation.
        # Slow tools (mutmut, scancode) are skipped here; S3-A covers them.
        print("\n[S4] Running harness cross-validation...")
        _s4_fabrication, _s4_unverifiable = _run_harness_cross_validation(ctx, raw)
        if _s4_fabrication or _s4_unverifiable:
            _s4_details = s4_block_details(_s4_fabrication, _s4_unverifiable)
            raise GateBlockedError(
                ctx.gate_num,
                GateResult(
                    gate_num=ctx.gate_num,
                    score=0.0,
                    dimensions=[],
                    open_critical=len(_s4_fabrication) + len(_s4_unverifiable),
                    open_high=0,
                    quality_complete=False,
                    rounds_used=0,
                ),
                details=_s4_details,
            )

        # ── Round 52 站2: the replaced boundary had to run somewhere ─────────
        # Placed after S4 because S4 is what runs `system-verification`, and
        # the reach artifact is written by that run (harness/tool_runners.py's
        # reach_instrumentation) rather than by a second execution of the
        # target. Round 51 站3 recorded which modules the suite replaced with
        # an autouse stand-in and let the number stand; the obligation is that
        # each one is executed for real by the project's own verification
        # target. Five of the six projects here owe nothing.
        _reach = _verify_system_reach_block(ctx)
        if _reach:
            raise GateBlockedError(
                ctx.gate_num,
                GateResult(
                    gate_num=ctx.gate_num, score=0.0, dimensions=[],
                    open_critical=len(_reach), open_high=0,
                    quality_complete=False, rounds_used=0,
                ),
                details={"stubbed_boundary_never_run": _reach},
            )

        # ── Round 52 站3: the product-side facts, on the record ──────────────
        # Rendered from the producers above, judged by nothing here. The
        # framework's guards are unary predicates and cannot express
        # "regressed"; this is the half of that which can be built today —
        # a cross-project corpus has nowhere to live (the harness is a
        # submodule of each project). Never raises.
        try:
            from core.quality_gate.delivery_fingerprint import write_fingerprint
            write_fingerprint(ctx.project_root, phase=ctx.phase,
                              gate=ctx.gate_num)
        except Exception as _fp_exc:  # pragma: no cover — a record, not a gate
            from core.degradation_ledger import record_degradation
            record_degradation(
                ctx.project_root, "gate:delivery-fingerprint",
                "delivery fingerprint not written",
                f"{type(_fp_exc).__name__}: {_fp_exc}", owner="harness",
            )

        # ── S4-B: Failed-tests assertion (Gate 1 only) ───────────────────────
        # S4 validates coverage % but not whether tests are red.  Parse
        # tool_evidence for "N failed" and block immediately — a passing
        # coverage score with failing tests is always a fabrication signal.
        if ctx.gate_num == 1:
            _s4b_violations = _check_tests_failed(raw)
            if _s4b_violations:
                raise GateBlockedError(
                    ctx.gate_num,
                    GateResult(
                        gate_num=ctx.gate_num,
                        score=0.0,
                        dimensions=[],
                        open_critical=len(_s4b_violations),
                        open_high=0,
                        quality_complete=False,
                        rounds_used=0,
                    ),
                    details={"tool_score_fabrication": _s4b_violations},
                )

            # ── W1: High skip-ratio warning (non-blocking) ───────────────────
            # Skipped tests contribute 0 coverage lines.  High skip ratio means
            # coverage is measured on a subset of the suite — flag for review.
            _skip_warn = _check_test_skip_ratio(raw)
            if _skip_warn:
                print(_skip_warn)
            # Round 46 站2: the WARN above is printed and gone. Every skip gets
            # a ledger row regardless of ratio — not as a verdict (the gate
            # verdict for a requirement's own skipped guard comes from the
            # traceability dimension), but so that "how many tests did not run
            # at this gate" is answerable after the run without a person
            # having watched the console.
            _skip_counts = _parse_skip_counts(raw)
            if _skip_counts and _skip_counts[0] > 0:
                from core.degradation_ledger import record_degradation
                _skipped, _total = _skip_counts
                record_degradation(
                    ctx.project_root, "gate:test-skips",
                    f"{_skipped} of {_total} tests did not run",
                    why="skipped tests contribute no coverage and no evidence",
                    data={"skipped": _skipped, "total": _total,
                          "gate": ctx.gate_num, "fr_id": ctx.fr_id}, owner="project"
                )

            # ── W2: Sub-100% coverage advisory (non-blocking) ─────────────────
            # advance-phase (P3+) runs --cov-fail-under=100 on 03-development/src.
            # Warn here so agents know to add # pragma: no cover before reaching
            # advance-phase — avoids a surprise blocker at phase transition.
            try:
                _cov_pct = float(
                    (raw.get("breakdown") or {})
                    .get("test_coverage", {})
                    .get("score", 100)
                )
            except (TypeError, ValueError):
                _cov_pct = 100.0
            if _cov_pct < 100.0:
                print(
                    f"[W2] test_coverage {_cov_pct:.1f}% < 100 — "
                    "advance-phase requires 100% on 03-development/src. "
                    "Lines not exercisable in tests: add # pragma: no cover."
                )

        # Build per-dimension results from breakdown if provided.
        # Gate config dimension metadata (for fallback when agent omits top-level fields).
        _dim_weights: dict[str, float] = {}
        _dim_thresholds: dict[str, float] = {}
        # ctx.config is either a GateConfig object or a plain dict — handle both.
        if isinstance(ctx.config, dict):
            _config_dim_list = ctx.config.get('dimensions', [])
        else:
            _config_dim_list = getattr(ctx.config, 'dimensions', [])

        for _d in _config_dim_list:
            _dname = _d.get('name') if isinstance(_d, dict) else getattr(_d, 'name', '')
            _dweight = _d.get('weight') if isinstance(_d, dict) else getattr(_d, 'weight', 0.0)
            _dt = _d.get('threshold') if isinstance(_d, dict) else getattr(_d, 'threshold', 0.0)
            if _dname:
                if _dweight is not None:
                    _dim_weights[_dname] = float(_dweight)
                if _dt is not None:
                    _dim_thresholds[_dname] = float(_dt)

        # Compute test_coverage cap from spec test coverage.
        # When required tests are partially missing, coverage % can be 100% even
        # when most tests don't exist yet. Cap at spec_coverage_pct.
        _spec_names: list = getattr(ctx, '_spec_test_names', [])
        _existing_count: int = getattr(ctx, '_existing_spec_count', 0)
        _spec_cap: float = 100.0
        if _spec_names and _existing_count < len(_spec_names):
            _spec_cap = _existing_count / max(len(_spec_names), 1) * 100.0

        dims: list[DimResult] = []
        for dim_name, dim_data in raw.get("breakdown", {}).items():
            # `dict.get(k, default)` only substitutes when the key is absent;
            # an explicit JSON `null` still surfaces as None. DimResult.score is
            # Optional[float] and every downstream reader in this function already
            # guards `is not None` — preserve None here instead of coercing to 0.0,
            # which used to make a legitimately-inapplicable dimension (e.g.
            # pytest-benchmark with no benchmarks) look like a real 0-score failure.
            raw_score = dim_data.get("score")
            score = float(raw_score) if raw_score is not None else None
            raw_thresh = dim_data.get("threshold")
            threshold = float(raw_thresh) if raw_thresh is not None else 0.0
            if dim_name == "test_coverage" and _spec_names and score is not None:
                score = min(score, _spec_cap)
            dims.append(DimResult(
                name=dim_name,
                score=score,
                threshold=threshold,
                issues=dim_data.get("issues", []),
                # Written by S4 above (_mark_framework_na, the framework-score
                # write-back, the unverifiable branch). Reading it here is what
                # carries the provenance into the verdict and its denominator
                # instead of leaving it in the breakdown dict nobody consults.
                score_source=dim_data.get("score_source"),
            ))

        # Apply gate_score_overrides from quality_manifest as threshold floor.
        # Never lower a threshold below what the gate YAML / Claude set — only raise it.
        # sab_data is already loaded in prepare_gate() — no need to re-read the manifest.
        _overrides: dict[str, float] = ctx.sab_data.get("gate_score_overrides", {})
        if _overrides:
            dims = [
                dataclasses.replace(d, threshold=max(d.threshold, float(_overrides[d.name])))
                if d.name in _overrides else d
                for d in dims
            ]
            # _effective_threshold() below checks _dim_thresholds (sourced from the
            # gate YAML) BEFORE d.threshold — every real gate config declares a
            # static threshold per dimension, so without this the floor-raise above
            # never reaches the actual pass/fail decision. Mirror it into
            # _dim_thresholds so the raised floor is what gets enforced.
            for _dname, _floor in _overrides.items():
                _dim_thresholds[_dname] = max(_dim_thresholds.get(_dname, 0.0), float(_floor))

        # CRG-ONLY dimension override: the *architecture* dimension is scored by the
        # framework's OWN independent CRG run (community_cohesion), never the agent.
        # error_handling was moved to a tool-scored dimension (ast-error-handling) —
        # the CRG flow `has_error_handler` field does not exist in the package.
        #
        # Round 38 站1: the trigger is what the *gate config* declares, not what
        # the agent's breakdown happens to contain. Until now this block ran
        # `if any(d.name in _CRG_ONLY_DIMS for d in dims)` — dims is built from
        # raw["breakdown"], so a gate result that simply omitted the row skipped
        # the framework's own CRG run entirely and the dimension was never
        # scored, never checked against its threshold, and never blocked.
        # Gates 3 and 4 hid this because their agents always wrote the row
        # (as JSON null); gate 2 declaring architecture would have inherited a
        # decorative entry. Same rule, and the same wording, as
        # `_override_adversarial_review_dim`: a framework-owned blocking
        # dimension must not depend on agent cooperation to exist.
        _crg_overrides_applied = False
        _crg_metrics_path = Path(ctx.work_dir) / "crg_metrics.json"
        _CRG_ONLY_DIMS = {"architecture"}
        _crg_declared = any(
            (_d.get("name") if isinstance(_d, dict) else getattr(_d, "name", ""))
            in _CRG_ONLY_DIMS
            for _d in _config_dim_list
        )
        if _crg_declared or any(d.name in _CRG_ONLY_DIMS for d in dims):
            # Regenerate crg_metrics.json from an independent CRG run, overwriting any
            # agent-written file. CRG is a hard dependency (verified at preflight); a
            # failure is a real error → BLOCK, never a fallback to agent scores.
            from harness.crg_independent import run_independent_crg, CrgIndependentError
            try:
                run_independent_crg(ctx.project_root, ctx.work_dir)
                _crg_m = json.loads(_crg_metrics_path.read_text(encoding="utf-8"))
                # Phase 1 gatekeeper: use architecture_score (cohesion − large_fn_penalty)
                # if available; fall back to raw cohesion for backward compatibility with
                # older crg_metrics.json that predate the large-function penalty field.
                _arch_score = _crg_m.get("architecture_score")
                if _arch_score is None:
                    _arch_score = (_crg_m.get("community_cohesion") or {}).get("score")
                _lf_penalty = _crg_m.get("large_functions_penalty", 0)
            except (CrgIndependentError, json.JSONDecodeError, OSError) as _crg_err:
                raise GateBlockedError(
                    ctx.gate_num,
                    GateResult(
                        gate_num=ctx.gate_num, score=0.0, dimensions=[],
                        open_critical=1, open_high=0,
                        quality_complete=False, rounds_used=0,
                    ),
                    details={"crg_independent_failed": [str(_crg_err)]},
                ) from _crg_err

            # Round 44 站3: and the score must be a score of THIS tree. A graph
            # that covers fewer files than the project delivers produces a
            # number about a subset; Round 37 站2 measured what that costs
            # (taskq-renew: 77.8 over 11 of 47 files, against CI's 57.1 over
            # all of them) and forced one full rebuild, then recorded whatever
            # survived it and moved on. Round 42 站4c carried the denominator
            # into the result and nothing read it either.
            #
            # `infra_fail`, not a low score: the project cannot fix CRG's
            # parser, and Round 32 站4's rule is that a dimension the
            # framework could not measure is the framework's problem — never
            # a number the project may lower to work around.
            from harness.crg_independent import graph_coverage_gap
            _unparsed = graph_coverage_gap(_crg_m)
            if _unparsed:
                raise GateBlockedError(
                    ctx.gate_num,
                    GateResult(
                        gate_num=ctx.gate_num, score=0.0, dimensions=[],
                        open_critical=1, open_high=0,
                        quality_complete=False, rounds_used=0,
                    ),
                    details={"crg_graph_incomplete": [
                        f"architecture cannot be scored: the code-review-graph "
                        f"covers {_crg_m.get('_graph_files')} file(s) and this "
                        f"project delivers {_crg_m.get('_source_files')}. A "
                        f"score computed over a subset is a score of that "
                        f"subset.",
                        *(f"  unparsed: {p}" for p in _unparsed),
                        "Fix: make each file above parseable by "
                        "code-review-graph, or take it out of the delivered "
                        "set (delete it, or .gitignore it if it is generated). "
                        "Do NOT lower the architecture score to work around "
                        "this — the framework, not the project, owes the "
                        "measurement.",
                    ]},
                )

            # Hard regression block (interactive gate — mirrors CI crg-arch-check):
            # architecture must not regress vs the prior exit baseline even if its
            # absolute score still clears the threshold.
            _arch_reg = _architecture_regression_reason(
                ctx.project_root, ctx.gate_num, ctx.config, _crg_m
            )
            if _arch_reg:
                raise GateBlockedError(
                    ctx.gate_num,
                    GateResult(
                        gate_num=ctx.gate_num, score=0.0, dimensions=[],
                        open_critical=1, open_high=0,
                        quality_complete=False, rounds_used=0,
                    ),
                    details={"architecture_regression": [_arch_reg]},
                )

            if _arch_score is not None:
                _new_dims = []
                _arch_found = False
                for _d in dims:
                    if _d.name == "architecture":
                        _arch_found = True
                        _old = _d.score if _d.score is not None else 0.0
                        _new = float(_arch_score)
                        if abs(_old - _new) > 1.5:
                            _cohesion_raw = (_crg_m.get("community_cohesion") or {}).get("score", _new)
                            _detail = f"community_cohesion={_cohesion_raw:.1f}"
                            if _lf_penalty:
                                _detail += f" − large_fn_penalty={_lf_penalty}"
                            print(
                                f"[harness] CRG override architecture: {_old:.1f} → "
                                f"{_new:.1f} ({_detail})"
                            )
                            _crg_overrides_applied = True
                        _new_dims.append(dataclasses.replace(_d, score=_new))
                        # Print unhealthy communities so the agent knows what to fix
                        _coh_data = _crg_m.get("community_cohesion", {})
                        _unhealthy = _coh_data.get("unhealthy", [])
                        _excluded = _coh_data.get("excluded_test_communities", 0)
                        if _unhealthy and _new < (_dim_thresholds.get("architecture") or 80):
                            print(f"\n[harness] CRG community diagnostics — {len(_unhealthy)} unhealthy community(ies):")
                            print(f"  Threshold: cohesion ≥ {_coh_data.get('_cohesion_threshold', '?')}, size ≤ {_coh_data.get('_community_oversized', '?')}")
                            if _excluded:
                                print(f"  Test-only communities excluded: {_excluded}")
                            for _u in _unhealthy[:8]:  # cap at 8 to avoid flooding
                                _issues = ", ".join(_u.get("issues", []))
                                print(f"  ❌ {_u['name']:<35} cohesion={_u['cohesion']:.3f}  size={_u['size']}  [{_issues}]")
                                # Round 42 站5: when the community is one
                                # file's internals, say so and say where. The
                                # three code-level remedies below all assume a
                                # community is a set of modules; none of them
                                # can act on this shape, which is how
                                # calibration became the only lever left.
                                if _u.get("dominant_file"):
                                    print(f"       ↳ this community is mostly {_u['dominant_file']} — "
                                          "its internals, not a module boundary. Split that file "
                                          "along the clusters, or connect them.")
                            if len(_unhealthy) > 8:
                                print(f"  ... +{len(_unhealthy) - 8} more (see .sessi-work/crg_metrics.json)")
                            print()
                            print("  Fix: unhealthy communities have low intra-community connectivity.")
                            print("  - Add cross-module imports/tests between files in the same community")
                            print("  - Merge small isolated communities into larger coherent modules")
                            print("  - Split oversized communities (>50) into focused subdirectories")
                            print("  - If CRG genuinely misreads an intentional layout, calibrate")
                            print("    crg_excludes / crg_cohesion_healthy in harness_config.json —")
                            print("    committed, so CI applies it too. Waivers were removed in R38.")
                    else:
                        _new_dims.append(_d)
                if not _arch_found and _crg_declared:
                    _new_dims.append(DimResult(
                        name="architecture",
                        score=float(_arch_score),
                        threshold=float(_dim_thresholds.get("architecture", 0.0)),
                        issues=[],
                        score_source=SCORE_SOURCE_FRAMEWORK,
                    ))
                    _crg_overrides_applied = True
                    print(
                        "[harness] architecture appended (agent omitted it): "
                        f"score={float(_arch_score):.1f}"
                    )
                dims = _new_dims
            # Round 42 站4c: the ruler travels with the number. The
            # calibration and the graph's size are computed already —
            # `crg_independent.py` puts `_cohesion_threshold` inside
            # `community_cohesion` and `_graph_files` / `_source_files`
            # alongside it (Round 37 站2) — but they stop at
            # `.sessi-work/crg_metrics.json`, which is gitignored and which
            # advance-phase deletes, while the score they qualify is
            # committed. taskq-plus scored architecture 100.0 at
            # `crg_cohesion_healthy: 0.2` and taskq-renew 77.8 at 0.25, and
            # neither gate result says which ruler it used. Written by the
            # framework, not narrated by the agent: plus's evidence string
            # mentioned its 0.2 in prose the agent wrote, renew's mentioned
            # nothing.
            _coh = (_crg_m or {}).get("community_cohesion") or {}
            ctx.architecture_calibration = {  # type: ignore[attr-defined]
                "cohesion_healthy": _coh.get("_cohesion_threshold"),
                "community_oversized": _coh.get("_community_oversized"),
                "graph_files": (_crg_m or {}).get("_graph_files"),
                "source_files": (_crg_m or {}).get("_source_files"),
            }

        # ── PR 4 (audit F-1.1 fix): framework trace score override ─────
        # The agent cannot compute the trace dimension (no tool to scan
        # SAD.md + [FR-XX] annotations + test references). The framework
        # runs `compute_trace_dimension` and overrides whatever the agent
        # wrote. Mirrors the CRG override pattern above: replace the
        # score in-place; log the change; never silently lose it.
        dims, _trace_overridden = _override_traceability_dim_score(
            dims, ctx.project_root, ctx.gate_num
        )
        # The gate YAML (gate{N}_p3_exit.yaml) declares a static threshold=100
        # for traceability — correct for its 4a (FR→code→test) sub-metric, but
        # traceability's persisted score is merged_pct = min(4a, 4b, 4c), and
        # 4b/4c intentionally use a lower 60/80/90% ladder. `_dim_thresholds`
        # (sourced from that YAML) takes precedence over `d.threshold` below,
        # so it must be dropped here — `d.threshold` already carries the
        # correct per-round `threshold_effective` from the override above.
        if any(d.name == "traceability" for d in dims):
            _dim_thresholds.pop("traceability", None)
        if _trace_overridden:
            _crg_overrides_applied = True

        # ── v2.9 C2: adversarial_review (Gate 3) — framework-owned ─────
        # Verdict from bug_hunt_verifier over bug_hunt_report.json; appended
        # when the agent omits the dimension. Same recompute semantics as the
        # CRG/trace overrides above.
        dims, _ar_overridden = _override_adversarial_review_dim_score(
            dims, ctx.project_root, _config_dim_list
        )
        if _ar_overridden:
            _crg_overrides_applied = True

        # ── CRG findings enrichment (MCP path, graceful degrade) ──────────
        # Runs after CRG independent score override so score is already final.
        # All enrichment writes to DimResult.issues (evidence only) or appends
        # auxiliary fields to gate_result.json. The Phase 2 hub penalty (step 9)
        # is the one exception: it changes test_coverage score and must therefore
        # set _crg_overrides_applied so overall_score is recomputed from corrected dims.
        if ctx.gate_num >= 2:
            try:
                dims, _enrich_overridden = _crg_enrich_gate_findings(
                    self.crg, dims, ctx.project_root, ctx.work_dir, ctx.gate_num
                )
                if _enrich_overridden:
                    _crg_overrides_applied = True
            except Exception as _enrich_err:
                # Real CRG failure (MCP unavailable, schema drift,
                # import error, etc.) — log via the module logger so
                # a real bug is in the decision / forensic trail,
                # and stderr for live operator visibility. The
                # test_coverage hub-penalty fabrication signal
                # (step 9) is silently dropped if this fails
                # without a log entry.
                import logging as _logging
                _logging.getLogger(__name__).warning(
                    "CRG enrichment failed; test_coverage hub-penalty "
                    "fabrication signal may be dropped. Error: %s: %s",
                    type(_enrich_err).__name__, _enrich_err,
                )

        # SG-2 (robustness audit): per-dimension variance sanity check.
        # If ≥3 dimensions all share the SAME score, that's suspiciously uniform
        # — Claude's per-dim evaluation should produce naturally varied scores.
        # We don't BLOCK here (Claude may legitimately rate dims identically on
        # very small projects), but we LOG to decision_log for forensic review.
        # A future enhancement (deferred audit recommendation) is to compare
        # these scores against a per-dimension evidence trail in .sessi-work/.
        self._variance_check_log(ctx, dims)

        # ── Fallback: derive overall_score from breakdown if agent omitted it ──
        # When CRG overrides changed dim scores, skip the agent-reported overall_score
        # (it was computed before the override) and recompute from corrected dims.
        _raw_overall = raw.get("overall_score", raw.get("score"))
        if _raw_overall is not None and not _crg_overrides_applied:
            _overall_score = float(_raw_overall)
        elif dims and _dim_weights:
            # Compute weighted average from breakdown using gate config weights.
            # Skip dimensions whose score is None (e.g. pytest-benchmark with no
            # benchmark tests — dimension not yet applicable, returns None). Their
            # weight is redistributed across the remaining dimensions.
            _weighted = 0.0
            _total_weight = 0.0
            for d in dims:
                if d.score is None:
                    continue
                w = _dim_weights.get(d.name, 1.0 / max(len(dims), 1))
                _weighted += d.score * w
                _total_weight += w
            _overall_score = _weighted / max(_total_weight, 0.001)
        elif dims:
            # Same None-skip as the weighted branch above — a dim with no
            # applicable score (e.g. pytest-benchmark with no benchmarks)
            # must not crash the simple average.
            _scored = [d.score for d in dims if d.score is not None]
            _overall_score = sum(_scored) / max(len(_scored), 1)
        else:
            _overall_score = 0.0

        # ── Quality gate verdict: composite ≥ score_gate AND every dim ≥ threshold ──
        # Hard rule (HR-18): both conditions must be met independently. The agent's
        # self-reported `quality_complete` is advisory only — never trusted as the
        # sole pass condition. CRG overrides (architecture) also force recompute
        # so the verdict reflects the corrected scores.
        # Dimensions with score=None (e.g. pytest-benchmark with no benchmark tests)
        # are excluded — the dimension is not yet applicable.
        _gt = ctx.config.get("score_gate", 80) if isinstance(ctx.config, dict) else getattr(ctx.config, 'score_gate', 80)

        def _effective_threshold(d: DimResult) -> float:
            """The bar this dimension is actually judged against.

            Gate-config value wins over the agent's self-reported breakdown
            threshold, which wins over the gate-wide floor. Single definition
            because the pass/fail verdict, the failing-dimension report, and the
            DA-waiver adjudication must all compare against the same number —
            three copies of this expression is three chances to drift apart.
            """
            return float(_dim_thresholds.get(d.name) or d.threshold or _gt)

        # A None-scored dim is "not applicable" and so cannot be REQUIRED to prove
        # it passed — but Round 27 站1: WHOSE None?
        #
        # This used to be a bare `d.score is None or ...`, which let the party being
        # scored decide it was exempt. S4 above now resolves every agent-authored
        # null first (running the tool itself and either writing back a real score
        # or marking it framework_na), so the only Nones that should reach here are
        # ones the framework reproduced, plus the framework-owned dimensions that
        # never go through S4 at all (requires_tool_execution: false — traceability,
        # adversarial_review — whose scores the framework patches in itself).
        #
        # Reading score_source rather than trusting that ordering keeps the check
        # honest if S4 is ever skipped: an unverified null then fails its floor
        # instead of silently satisfying it.
        #
        # Scope is deliberately narrow — ONLY the dimensions S4 can actually
        # verify (this gate's config declares them AND they name a tool AND they
        # require tool execution). A None for anything else stays a vacuous pass,
        # because the framework has no way to check it and being strict where it
        # cannot check is how a Gate 3 agent was once pushed into fabricating a
        # performance score (test_finalize_gate_null_breakdown_score_does_not_block).
        # Too lax lets a declared N/A through unchecked; too strict manufactures
        # the fabrication it is trying to prevent. The verifiable set is the line
        # between them.
        # Round 27 站5 investigated settling every d.threshold to
        # _effective_threshold here, so that core/quality_gate/block_reason.py —
        # which renders "X scored 98.9, needs {d.threshold}" — would quote the
        # number that actually judged. The diagnosis holds: a project whose SAB
        # set quality_targets.min_coverage to 100 produced 23 gate-block lessons
        # all reading "needs 100.0" while this gate's YAML threshold for
        # test_coverage was 80, and the two agreeing by coincidence is what kept
        # it invisible.
        #
        # The change was reverted. test_finalize_gate_override_is_floor_not_ceiling
        # caught it: _effective_threshold prefers _dim_thresholds (which the
        # gate_score_overrides mirror above writes into) over d.threshold, so
        # overwriting d.threshold with it LOWERS an agent-set 90 to an
        # override-set 80 — the floor becoming a ceiling, the exact thing that
        # test exists to prevent. Making it right means _effective_threshold
        # taking a max across the three sources instead of first-truthy-wins,
        # which changes how strictly every Gate 1 judges. That is a bigger,
        # separate decision than this station. Recorded in
        # docs/PROPOSAL_ADJUDICATIONS.md as R27-DEFER-1.
        _framework_na_dims = {
            _name for _name, _entry in raw.get("breakdown", {}).items()
            if isinstance(_entry, dict) and na_is_framework_verified(_entry)
        }
        _cfg_dims = (
            ctx.config.get("dimensions", []) if isinstance(ctx.config, dict)
            else [dataclasses.asdict(x) for x in ctx.config.dimensions]
        )
        _s4_verifiable = {
            _d.get("name") for _d in _cfg_dims
            if _d.get("requires_tool_execution", False) and _d.get("tool")
            and _d.get("name") not in _CRG_OWNED_DIMENSIONS
        }

        def _dim_passes(d: DimResult) -> bool:
            if d.score is not None:
                return d.score >= _effective_threshold(d)
            if d.name not in _s4_verifiable:
                return True  # framework cannot check it — see the note above
            return d.name in _framework_na_dims

        _all_dims_pass = all(_dim_passes(d) for d in dims) \
            if dims else False  # empty dims = no evidence = not complete
        _quality_complete = _overall_score >= _gt and _all_dims_pass

        if not _all_dims_pass and dims:
            _failing = [f"{d.name}={d.score:.1f}" for d in dims
                        if d.score is not None and d.score < _effective_threshold(d)]
            _unverified_na = [d.name for d in dims if not _dim_passes(d) and d.score is None]
            if _unverified_na:
                _failing += [f"{n}=N/A (unverified)" for n in _unverified_na]
            print(f"\n[harness] {len(_failing)} dimension(s) below individual threshold: {', '.join(_failing)}")

        # Round 42 站4b: what the composite was averaged over, computed where
        # the weights already are. Stashed on the context rather than
        # recomputed at the write site, for the reason `spec_coverage_report`
        # takes its rows as an argument: a second derivation of the
        # denominator is a second denominator.
        ctx.measurement_scope = measurement_scope(  # type: ignore[attr-defined]
            dims, _dim_weights,
        )

        result = GateResult(
            gate_num=ctx.gate_num,
            score=_overall_score,
            dimensions=dims,
            # JSON `null` bypasses `dict.get(k, default)` and surfaces as None —
            # coerce defensively so a None never reaches `open_critical == 0` checks.
            open_critical=int(_first_non_null(raw, "open_critical_count", "open_critical", default=0)),
            open_high=int(_first_non_null(raw, "open_high_count", "open_high", default=0)),
            quality_complete=_quality_complete,
            rounds_used=_first_non_null(raw, "rounds_used", default=1),
        )

        # Round 38: no threshold can be waived. Round 21 moved the adjudication
        # of a waiver's *necessity* to this point, after the framework's own CRG
        # run had produced a number; Round 38 removes the question. A waiver was
        # only ever visible to this one enforcer — `cmd_crg_arch_check`, which CI
        # runs on every push from phase 3 and which the workflow JS ANDs into
        # `gate{N}Pass`, has no waiver logic — so a granted waiver produced a
        # local PASS and a red build, and the gate loop then burned its three
        # rounds on a remedy that could not satisfy the check. The remedy that
        # does work is calibration (`crg_excludes` / `crg_cohesion_healthy` in
        # .methodology/harness_config.json): committed, and therefore read by
        # every enforcer. A request is refused at collection time
        # (cli/gate_cmds.py::_collect_da_waivers), never here.
        #
        # taskq-renew is the worked example: its Gate 4 waiver named
        # `storage-load-sub1` / `sub2`, communities that exist only in the
        # truncated 11-of-47-file graph Round 37 diagnosed. The premise was
        # manufactured by the measurement defect the waiver excused.

        # Determine final pass/fail state BEFORE writing
        # manifest/log so that manifest and decision log reflect the actual gate outcome.
        _gate_passes: bool
        if ctx.gate_num == 1:
            # Gate 1 must check BOTH per-dim thresholds AND the gate's
            # overall score_gate floor. Without the score check, an FR
            # with all dims passing individually but overall_score
            # below the gate floor would silently flip to PASS via
            # the quality_complete=True line below.
            _gate1_score_gate = 0.0
            if isinstance(ctx.config, GateConfig):
                _gate1_score_gate = ctx.config.score_gate
            else:
                _gate1_score_gate = ctx.config.get("score_gate", 0)
            _gate_passes = (
                not any(d.score is not None and d.score < d.threshold
                        for d in result.dimensions)
                and result.score >= _gate1_score_gate
            )
        else:
            if isinstance(ctx.config, GateConfig):
                score_gate = ctx.config.score_gate
            else:
                score_gate = ctx.config.get("score_gate", 0)
            _gate_passes = result.score >= score_gate and result.quality_complete

        # A CRG override recompute can change the pass state; update
        # result.quality_complete so manifest + log reflect the real outcome.
        if _gate_passes and not result.quality_complete:
            result = dataclasses.replace(result, quality_complete=True)

        self._update_quality_manifest(ctx.gate_num, ctx.fr_id, result, project_root=ctx.project_root)

        self._effort.record(EffortRecord(
            phase=ctx.phase, gate_num=ctx.gate_num, agent_id="GATE",
            operation="gate_finalize", duration_s=time.time() - t0,
        ))
        self._log.write(DecisionLogEntry(
            ctx=DecisionContext(agent_id="GATE", phase=ctx.phase, fr_id=ctx.fr_id),
            decision="GATE_PASS" if result.quality_complete else "GATE_BLOCK",
            reasoning=(
                f"Gate {ctx.gate_num}: score={result.score:.1f}, "
                f"critical={result.open_critical}, high={result.open_high}"
            ),
            scores={"gate_score": result.score},
        ))

        if not _gate_passes:
            self._trigger_hooks(ctx, "on_gate_fail")
            raise GateBlockedError(ctx.gate_num, result)

        self._trigger_hooks(ctx, "after_gate_pass")

        return result

    def check_pre_fix_safety(self, project_root: str, ref: str = "HEAD") -> dict:
        """
        CRG Point 3: Pre-fix safety gate — check if pending changes are safe to modify.

        Call before each improvement round. Defers fix if CRG impact check reports risky.
        """
        threshold = 0.7
        if self._last_gate_num is not None:
            config = self._load_config(self._last_gate_num)
            threshold = config.crg.get("impact_threshold", 0.7)
        risky = self.crg.check_impact(project_root, ref=ref, threshold=threshold)
        return {
            "safe": not risky,
            "threshold": threshold,
            "message": "Safe to modify" if not risky else
                       f"DEFER: risk score >= {threshold} — structural impact too high",
        }

    def check_post_round_drift(self, project_root: str) -> dict:
        """
        CRG Point 4: Post-round drift check — verify no structural drift introduced.

        NOT WIRED: only ever called inside an auto-fix fix round, which was removed
        (see the core/auto_fix NOT-WIRED note). Kept as infra for a future redesign.
        Designed to run after each improvement round; triggers revert protocol if
        drift detected.
        """
        threshold = 0.4
        if self._last_gate_num is not None:
            config = self._load_config(self._last_gate_num)
            threshold = config.crg.get("drift_threshold", 0.4)
        drifted = self.crg.check_drift(project_root, threshold=threshold)
        metrics = self.crg.load_metrics(project_root)
        structural_drift = metrics.get("structural_drift", 0.0)
        return {
            "drifted": drifted,
            "structural_drift": structural_drift,
            "threshold": threshold,
            "message": "No structural drift" if not drifted else
                       f"DRIFT DETECTED: structural_drift={structural_drift} > {threshold}",
        }

    @staticmethod
    def _nfr_type_to_dim(nfr_type: str) -> str:
        """Map an NFR type keyword to a harness quality dimension name."""
        t = nfr_type.lower()
        if any(k in t for k in ("performance", "latency", "throughput", "response")):
            return "performance"
        if any(k in t for k in ("security", "auth", "access control", "encryption")):
            return "security"
        if any(k in t for k in ("reliability", "availability", "uptime", "recovery")):
            return "reliability"
        if any(k in t for k in ("deploy", "deployability", "docker", "container", "rollout")):
            return "deployability"
        if any(k in t for k in ("maintainability", "modularity", "extensibility")):
            return "maintainability"
        if any(k in t for k in ("test", "coverage", "quality")):
            return "test_coverage"
        if any(k in t for k in ("traceability", "tracking", "audit")):
            return "traceability"
        if any(k in t for k in ("clarity", "documentation", "readability")):
            return "clarity"
        return "correctness"

    def _parse_nfr_from_srs(self, project_root: Path) -> dict[str, str]:
        """Extract NFR→dimension mapping from SRS.md NFR sections as fallback.

        Supports two SRS formats:
        - H3-heading:  ### NFR-01: Performance
        - Pipe-table:  | NFR-01 | Performance | description |
        """
        srs_path = ProjectLayout(project_root).srs_path
        if not srs_path.exists():
            return {}
        try:
            text = srs_path.read_text(encoding="utf-8")
            nfr_map: dict[str, str] = {}
            # Format 1: ### NFR-XX: Title sections
            for m in re.finditer(r'^###\s+(NFR-\d+)\s*:\s*(.+)$', text, re.MULTILINE):
                nfr_id = m.group(1)
                nfr_map[nfr_id] = self._nfr_type_to_dim(m.group(2).strip())
            # Format 2 (fallback): pipe-table | NFR-01 | Type | ... |
            if not nfr_map:
                for m in re.finditer(
                    r'^\|\s*(NFR-\d+)\s*\|\s*([^|]+?)\s*\|', text, re.MULTILINE
                ):
                    nfr_id = m.group(1)
                    if nfr_id not in nfr_map:
                        nfr_map[nfr_id] = self._nfr_type_to_dim(m.group(2).strip())
            return nfr_map
        except Exception as exc:
            print(f"[WARN] NFR-dimension map parse failed: {exc}")
            return {}

    def _parse_nfr_fr_xref(self, project_root: Path) -> dict[str, list[str]]:
        """Extract NFR→[FR, ...] mapping from the §2 FR Cross-Reference table in SRS.md.

        Looks for a pipe-table whose header contains 'NFR Association'.
        Returns {nfr_id: [fr_id, ...]} reverse mapping.
        """
        srs_path = ProjectLayout(project_root).srs_path
        if not srs_path.exists():
            return {}
        try:
            text = srs_path.read_text(encoding="utf-8")
            # Find table header with 'NFR Association' column
            header_re = re.compile(
                r'^(?:\|[^|\n]*)+\|\s*NFR\s*Association\s*\|', re.IGNORECASE | re.MULTILINE
            )
            header_match = header_re.search(text)
            if not header_match:
                return {}
            cols = [c.strip() for c in header_match.group(0).split('|') if c.strip()]
            nfr_col = next(
                (i for i, c in enumerate(cols) if 'nfr' in c.lower() and 'assoc' in c.lower()),
                -1,
            )
            if nfr_col == -1:
                return {}
            # Build FR→[NFR] map from table rows, then reverse it
            fr_nfr: dict[str, list[str]] = {}
            for line in text[header_match.end():].splitlines():
                line = line.strip()
                if not line.startswith('|'):
                    if line:
                        break
                    continue
                if re.match(r'^\|[\s\-|]+\|$', line):
                    continue
                cells = [c.strip() for c in line.split('|') if c.strip()]
                if not cells:
                    continue
                fr_match = re.match(r'^(FR-\d+)$', cells[0])
                if not fr_match:
                    continue
                fr_id = f"FR-{fr_match.group(1).split('-')[1].zfill(2)}"
                if nfr_col < len(cells):
                    nfr_ids = [f"NFR-{n.zfill(2)}" for n in re.findall(r'NFR-(\d+)', cells[nfr_col])]
                    if nfr_ids:
                        fr_nfr[fr_id] = nfr_ids
            # Reverse: NFR → [FR, ...]
            nfr_fr: dict[str, list[str]] = {}
            for fr_id, nfr_ids in fr_nfr.items():
                for nfr_id in nfr_ids:
                    nfr_fr.setdefault(nfr_id, []).append(fr_id)
            return nfr_fr
        except Exception as exc:
            print(f"[WARN] NFR→FR cross-reference parse failed: {exc}")
            return {}

    def _reconcile_with_sab_json(
        self, parsed: dict, sab_json: dict, source_label: str = "SAD §5"
    ) -> dict:
        """Reconcile SAD.md §5 parse result with .methodology/SAB.json content.

        Bug H-A fix: SAD.md §5 is the *declarative* source of architecture
        state, but in practice the §5 YAML may still carry template placeholder
        values (e.g. ``FR-01: "app.api.webhooks"``) for projects that never
        project-tailored §5 after using the canonical template. Meanwhile
        ``.methodology/SAB.json`` is the *runtime canonical* source — every
        downstream hook (drift_detector, phase_hooks.preflight_sab_check,
        spec_coverage) reads SAB.json, not the §5 YAML. When §5 and SAB.json
        disagree, the manifest currently writes §5's templated values and
        silently drifts from what the rest of the harness trusts.

        Root-cause fix: SAB.json wins for architecture-derived fields. §5
        parse only fills fields absent from SAB.json. When both sides have
        values and they disagree, a ``[WARN]`` is emitted naming both sides
        verbatim — the operator can fix §5 (the canonical contract) and
        regenerate SAB.json to make the disagreement disappear.

        Args:
            parsed: result of ``scripts.generate_sab.parse_sad(sad_path)``.
                Keys use sab_parser short aliases (``fr_module_traceability``,
                ``high_risk``, ``constraints``, ``nfr_dim_map``, etc.).
            sab_json: parsed ``.methodology/SAB.json`` content. May be empty
                if the file does not exist or failed to parse.
            source_label: human-readable source name for the WARN line,
                e.g. ``"SAD §5"`` or ``"SAD.md"``.

        Returns:
            dict with the same shape as ``parsed``, but with architecture
            fields overwritten from SAB.json when SAB.json has non-empty
            values.
        """
        # (parsed_key, sab_key) pairs. SAB.json is canonical for all of these.
        # nfr_fr_mapping is intentionally excluded — it is SAD-derived prose
        # data (parsed from §2 cross-reference), never written to SAB.json.
        field_pairs = [
            ("fr_module_traceability", "fr_module_traceability"),
            ("high_risk", "high_risk_modules"),
            ("constraints", "architecture_constraints"),
            ("quality_targets", "quality_targets"),
            ("nfr_dim_map", "nfr_dimension_mapping"),
            ("nfr_traceability", "nfr_traceability"),
            ("gate_score_overrides", "gate_score_overrides"),
        ]
        merged = dict(parsed)

        for parsed_key, sab_key in field_pairs:
            sab_val = sab_json.get(sab_key)
            # Treat empty dict / empty list as "absent" — pure-template
            # manifests must still benefit from the SAB.json fallback.
            sab_present = bool(sab_val) and sab_val not in ({}, [], "", None)
            if not sab_present:
                continue

            parsed_val = merged.get(parsed_key, ({} if isinstance(sab_val, dict) else []))
            if parsed_val and self._values_disagree(parsed_val, sab_val):
                print(
                    f"  [WARN] {source_label}.{parsed_key} disagrees with "
                    f".methodology/SAB.json.{sab_key}; using SAB.json "
                    f"(canonical — drift_detector and all SAB-aware hooks read "
                    f"SAB.json). Re-run `python3 scripts/generate_sab.py "
                    f"--project . --overwrite` after editing {source_label}.",
                    file=sys.stderr,
                )
            merged[parsed_key] = sab_val

        return merged

    @staticmethod
    def _values_disagree(parsed_val: object, sab_val: object) -> bool:
        """True when parsed (§5) and SAB.json values differ semantically.

        Dict ordering is normalised (JSON serialise → reparse) so that
        ``{"a": 1, "b": 2}`` vs ``{"b": 2, "a": 1}`` does NOT count as a
        disagreement — only structural / value differences count.
        """
        try:
            return json.loads(json.dumps(parsed_val, sort_keys=True)) != \
                json.loads(json.dumps(sab_val, sort_keys=True))
        except (TypeError, ValueError):
            # Non-JSON values fall back to literal compare; this branch only
            # fires for pathological inputs the dataclass contract already
            # prevents.
            return parsed_val != sab_val

    def generate_quality_manifest(
        self,
        fr_ids: list[str],
        sad_path: str,
        project_root: str | None = None,
        force: bool = False,
    ) -> Path | None:
        """Called at P2 exit. Parses SAD.md -> constraints + high_risk_modules.

        ``force=False`` (default) refuses to overwrite an existing manifest and
        returns ``None``; the caller decides what to do (preserve is the
        safer default because the manifest holds accumulated Gate scores
        that ``plan-all`` does not own). ``force=True`` overwrites and
        returns the path.

        ``project_root`` is the project root the manifest is written under.
        Required: a CWD-relative path would silently corrupt harness state
        when the CLI is invoked with ``--project-root <path>`` from a
        different working directory (HR-09: silent CWD-rel manifest path).
        If omitted, falls back to CWD for backward compatibility — the
        existing ``test_generate_quality_manifest_creates_file`` harness
        uses the old signature and would otherwise break.
        """
        # Defensive de-dup (preserve order): callers may pass a CSV/list that
        # accumulated duplicates upstream (e.g. a workflow re-passing the FR set
        # across phases). fr_ids is a registry, not a multiset — duplicates inflate
        # the "N FRs" count in load-context / run-phase output and re-list the same
        # FR in the per-FR Gate 1 loop.
        fr_ids = list(dict.fromkeys(fr_ids))

        # Resolve project_root with safe default. If the caller didn't
        # pass one, we use os.getcwd() — but emit a WARNING so the
        # CWD-rel hazard is visible in the logs (helps diagnose
        # already-broken CI invocations that didn't migrate yet).
        try:
            from scripts.generate_sab import parse_sad
            sab = parse_sad(sad_path)
        except Exception as exc:
            from core.degradation_ledger import record_degradation
            record_degradation(
                Path(project_root) if project_root else Path.cwd(),
                "harness_bridge.generate_quality_manifest",
                f"SAD.md §5 parse failed ({sad_path}) — SAB baseline from SAD.md "
                "starts empty; downstream reconciliation against SAB.json is the "
                "only source of architecture-constraint data this round",
                why=str(exc), owner="harness"
            )
            sab = {}

        # Bug H-A fix: reconcile with .methodology/SAB.json as canonical
        # for architecture-derived fields. SAD.md §5 may still carry
        # template placeholder values; SAB.json is what drift_detector and
        # every SAB-aware hook actually reads at runtime. Without this step,
        # the manifest silently drifts from runtime arch state. See
        # ``_reconcile_with_sab_json`` for the rationale. Backward-
        # compatible: if SAB.json is absent, parse_sad's result is used
        # unchanged.
        #
        # IMPORTANT: resolve SAB.json path from `project_root` when available
        # (the caller already validated it — hr-09 / hr-11), falling back
        # to a derivation from `sad_path` only if project_root is unknown.
        # Using `Path(sad_path).parent.parent` alone is CWD-relative for
        # relative `sad_path` arguments and produces a path the caller's
        # ``Path`` mock (when patching for tests) cannot redirect — that's
        # why HR-09 insists on explicit project_root.
        if project_root:
            _project_root_for_sab_json = Path(project_root).resolve()
        else:
            _project_root_for_sab_json = Path(sad_path).parent.resolve().parent
        _sab_json_path = _project_root_for_sab_json / ".methodology" / "SAB.json"
        _sab_json: dict = {}
        if _sab_json_path.exists():
            try:
                _sab_json = json.loads(
                    _sab_json_path.read_text(encoding="utf-8")
                )
            except Exception as _sab_exc:  # pylint: disable=broad-exception-caught
                print(
                    f"  [WARN] Could not parse {_sab_json_path} for "
                    f"reconciliation: {_sab_exc}; falling back to §5 parse.",
                    file=sys.stderr,
                )
        sab = self._reconcile_with_sab_json(sab, _sab_json)

        # Reuse the already-resolved, project_root-aware root computed above
        # for SAB.json — re-deriving `Path(sad_path).parent.parent` here would
        # reintroduce the same CWD-relative hazard HR-09 fixed for SAB.json,
        # just for NFR parsing instead.
        _project_root = _project_root_for_sab_json
        nfr_map = sab.get("nfr_dim_map", {})
        # Fallback: if SAD.md nfr_dim_map is empty, parse from SRS.md
        if not nfr_map:
            srs_nfr = self._parse_nfr_from_srs(_project_root)
            nfr_map = srs_nfr or nfr_map

        # NFR→[FR] reverse mapping from §2 cross-reference table
        nfr_fr_map = self._parse_nfr_fr_xref(_project_root)

        qt = sab.get("quality_targets", {})
        # (7) Start from NFR-backed dimension floors (sab_parser.derive_gate_score_overrides):
        # an NFR mapped to a gate dimension forces that dimension to clear its standard
        # threshold. quality_targets below merge on top (floor only ever rises).
        gate_score_overrides: dict[str, float] = {
            k: float(v) for k, v in sab.get("gate_score_overrides", {}).items()
        }

        # Only quality_targets whose VALUE is itself a 0-100 dimension score may seed a
        # threshold floor. max_complexity→complexity / min_reliability→reliability were
        # dead (no such gate dimension). p95_latency_ms is milliseconds — NOT a 0-100
        # score — feeding it here set performance's floor to 3000, which no score can
        # clear; performance's floor comes from NFR derive_gate_score_overrides.
        #
        # Round 46 站3 struck the rest of that sentence, which said p95 "is
        # enforced inside the performance dimension's benchmark scorer". It is
        # not. `_score_pytest_benchmark` applies a fixed 1000ms/3000ms penalty
        # its own docstring calls "cross-validation heuristics, not NFR
        # targets"; no line of it reads a p95, a project's budget, or an NFR.
        # A project's latency budget is enforced by the project's own
        # `# NFR-01`-annotated tests, and since Round 46 站1 those have to
        # actually pass for the requirement to count as covered. Naming an
        # enforcer that does not exist is worse than naming none: it is why
        # taskq-advance's FINAL_SIGN_OFF could record "NFR-01 … Conditional
        # PASS … dimension scoring uses framework override path" next to a
        # performance score of 100.0.
        _qt_map = {
            "min_coverage": "test_coverage",
            "min_security_score": "security",
        }
        for qt_key, dim_name in _qt_map.items():
            if qt_key in qt:
                try:
                    gate_score_overrides[dim_name] = max(
                        gate_score_overrides.get(dim_name, 0.0), float(qt[qt_key]))
                except (ValueError, TypeError):
                    pass

        manifest: dict[str, Any] = {
            "schema_version": "1.0",
            "generated_at_phase": 2,
            "fr_ids": fr_ids,
            "nfr_dimension_mapping": nfr_map,
            "nfr_fr_mapping": nfr_fr_map,
            "nfr_traceability": sab.get("nfr_traceability", {}),
            "quality_targets": qt,
            "fr_module_traceability": sab.get("fr_module_traceability", {}),
            "architecture_constraints": sab.get("constraints", []),
            "high_risk_modules": sab.get("high_risk", []),
            "gate_score_overrides": gate_score_overrides,
            "gate_results": {"gate1": {}, "gate2": None, "gate3": None, "gate4": None},
        }
        if project_root is None:
            import os as _os
            project_root = _os.getcwd()
            print(
                f"  [WARN] generate_quality_manifest called without "
                f"project_root; falling back to CWD ({project_root}). "
                f"Pass project_root explicitly to avoid CWD-rel hazards.",
                file=sys.stderr,
            )
        out = Path(project_root) / ".methodology" / "quality_manifest.json"
        if out.exists() and not force:
            return None
        out.parent.mkdir(parents=True, exist_ok=True)
        # Atomic write so a SIGKILL mid-flush doesn't corrupt the
        # harness state manifest.
        if atomic_write_json is not None:
            atomic_write_json(out, manifest)
        else:  # pragma: no cover
            out.write_text(
                json.dumps(manifest, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        return out

    def _trigger_hooks(self, ctx: GateContext, event_name: str) -> None:
        """Trigger lifecycle hooks for gate events (non-fatal)."""
        import logging as _logging
        try:
            from core.lifecycle_hooks import HookRunner, HookEvent
            event = HookEvent(event_name)
            runner = HookRunner(Path(ctx.project_root))
            results = runner.run_hooks(event, {"gate_num": str(ctx.gate_num), "phase": str(ctx.phase)})
            for r in results:
                if not r.success and r.hook.required:
                    _logging.getLogger(__name__).warning(
                        "Required hook '%s' failed: %s", r.hook.name, r.output
                    )
        except Exception as exc:
            _logging.getLogger(__name__).warning(
                "_trigger_hooks(%s) failed: %s", event_name, exc
            )

    def _variance_check_log(
        self, ctx: "GateContext", dims: list["DimResult"],
    ) -> None:
        """Per-dimension variance sanity check (SG-2 forensic flag).

        If ≥3 dimensions share suspiciously uniform scores, log a
        ``GATE_VARIANCE_LOW`` decision-log entry for human review.
        Never blocks finalize (variance is advisory), but the check
        itself must be resilient — narrow the catch to the
        exceptions we expect from the check (statistics, IO, decision-
        log writer), and log a WARNING for anything unexpected so
        real bugs in the check are visible.
        """
        import statistics as _stats
        try:
            dim_scores = [d.score for d in dims if d.score is not None]  # B3: include zero-scored dims
            if len(dim_scores) < 3:
                return
            _stdev = _stats.pstdev(dim_scores)
            if _stdev >= 0.5:
                return
            self._log.write(DecisionLogEntry(
                ctx=DecisionContext(
                    agent_id="GATE", phase=ctx.phase, fr_id=ctx.fr_id,
                ),
                decision="GATE_VARIANCE_LOW",
                reasoning=(
                    f"Per-dimension scores cluster tightly "
                    f"(n={len(dim_scores)}, stddev={_stdev:.3f}, "
                    f"scores={dim_scores}). Forensic flag — manually "
                    f"verify evidence trail."
                ),
                scores={"dim_stddev": _stdev},
            ))
        except Exception as exc:  # variance check is advisory
            # Never block finalize (the check is advisory), but log
            # a WARNING so a real bug in the check itself is visible
            # in forensic review instead of being silently dropped
            # (the original `pass` masked ImportError, TypeError,
            # statistics errors, decision-log writer IO, etc.).
            import logging as _logging
            _logging.getLogger(__name__).warning(
                "variance_check_log suppressed: %s: %s",
                type(exc).__name__, exc,
            )

    def _load_config(self, gate_num: int) -> GateConfig:
        """Load the YAML configuration for a specific gate."""
        import yaml  # type: ignore[import-untyped]
        # Round 29 Station 1: use the SSOT resolver + name registry instead of
        # a local copy of the names dict.  gate_config_path() validates gate_num
        # with the same ValueError shape the old inline check had.
        from core.quality_gate.gate_thresholds import gate_config_path
        config_path = gate_config_path(gate_num)
        with open(config_path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)
        return GateConfig.from_dict(raw, gate_num)

    def _update_quality_manifest(
        self, gate_num: int, fr_id: str | None, result: GateResult,
        project_root: str | None = None,
    ) -> None:
        """Update the persistent manifest with latest gate results.

        ``project_root`` is the project root the manifest lives
        under. Required to avoid the CWD-rel hazard: a CLI
        invocation with ``--project-root <path>`` from a different
        cwd would otherwise write quality-gate results into the
        wrong tree (HR-09 / same pattern as generate_quality_manifest).
        CWD fallback stays for backward compat with a WARNING so
        the hazard is visible in logs.
        """
        if project_root is None:
            import os as _os
            project_root = _os.getcwd()
            import logging as _logging
            _logging.getLogger(__name__).warning(
                "_update_quality_manifest called without project_root; "
                f"falling back to CWD ({project_root}). Pass project_root "
                "explicitly to avoid CWD-rel hazards."
            )
        p = Path(project_root) / ".methodology" / "quality_manifest.json"
        if not p.exists():
            return
        # Round 14 站2d: was an uncaught json.loads — a corrupt manifest raised
        # JSONDecodeError straight through this gate-result write, which the
        # crash boundary then misclassified as [HARNESS-BUG]. StateCorruptError
        # is deliberately left uncaught here too (this write can't proceed
        # without knowing the manifest's true prior content) but now surfaces
        # correctly as [FATAL] exit 26.
        from core.state_io import load_quality_manifest
        manifest = load_quality_manifest(project_root)
        key = f"gate{gate_num}"
        payload: dict[str, Any] = {
            "score": result.score, "quality_complete": result.quality_complete,
            "rounds_used": result.rounds_used, "open_critical": result.open_critical,
            "open_high": result.open_high,
        }
        # Round 38: `da_waiver_applied` / `da_waiver_needs_human_review` are
        # gone from this payload. They recorded that a threshold had been
        # zeroed; no threshold can be zeroed now, so a field that could only
        # ever be absent is one more thing for a reader to misinterpret.
        #
        if fr_id:
            if not isinstance(manifest["gate_results"][key], dict):
                manifest["gate_results"][key] = {}
            manifest["gate_results"][key][fr_id] = payload
        else:
            manifest["gate_results"][key] = payload
        _atomic_write_gate_result(p, manifest)
