"""Round 24 站1 — the single source of truth for WHY a gate blocked.

`harness_bridge.finalize_gate` raises `GateBlockedError` from ten distinct
sites. Nine of them attach a `details` dict whose key names the real cause:
`tool_score_fabrication` (an agent-claimed score the harness could not
reproduce by running the tool itself), `tool_evidence_missing`,
`infra_fail`, `malformed_gate_result`, `crg_independent_failed`,
`architecture_regression`, `da_waiver`. The tenth (the generic
`not _gate_passes` path) carries no details and blocks on the *result*
itself — a failing dimension, a non-zero `open_critical`, or
`quality_complete=False`.

Before this module, both consumers of a block event —
`cli/gate_cmds.py::_format_block_diagnostic` (which writes the agent-facing
diagnostic and `.methodology/last_block.md`) and
`core/lessons.py::record_gate_block` (cross-run failure memory) — each
carried its own copy of one filter:

    [d for d in result.dimensions if d.score is not None and d.score < d.threshold]

so both modelled exactly one cause: "a dimension is below threshold".
`details` was never read by either. Observed live in the run-all-by-workflow
P1-P8 validation run, `.methodology/last_block.md`:

    fr_id: FR-05 | rounds: 0 | open_critical: 1 | open_high: 0
    ## Failing Dimensions          <- empty
    ## Resume Commands             <- "re-run run-gate", nothing else

Five seconds later the same FR scored 100.0. The harness's own
anti-fabrication detection is its most important check, and its verdict was
invisible to the agent it was meant to constrain; the only action the agent
was told to take was to run the gate again.

`derive_block_reasons` is now that model, shared by both consumers. Adding a
new `raise GateBlockedError(..., details={"new_key": ...})` without adding
`new_key` here fails `tests/test_block_reason_registry.py` — the registry
completeness meta-test scans harness_bridge.py for detail keys passed either
by keyword OR positionally (the `da_waiver` site passes its dict as the third
positional argument; an AST scan that only reads `keywords` misses it, which
is how the 7th key was nearly lost while writing this module).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Remediation for a dimension that scored below its threshold. Moved here
# verbatim from cli/gate_cmds.py — a dimension hint IS a block remediation,
# and core must not import from cli.
DIMENSION_HINTS: dict[str, str] = {
    "linting":            "Run `ruff check . --fix` (or flake8); resolve all remaining lint errors",
    "type_safety":        "Run `mypy .`; add missing annotations and fix all type errors",
    "test_coverage":      "Run `pytest --cov` to find uncovered lines; add unit tests for each gap",
    "security":           "Fix OWASP-category issues; validate all inputs; remove eval/exec patterns",
    "secrets_scanning":   "Remove hard-coded secrets; move to env vars / vault; run `gitleaks detect`",
    "license_compliance": "Run `pip-licenses`; replace or vendor GPL/incompatible dependencies",
    "mutation_testing":   "Run `mutmut run`; add assertions that kill every surviving mutant",
    "architecture":       (
        "Two distinct failure modes — check tool_evidence to identify which applies: "
        "(1) CRG community issues: if god-module (size>50) or low cohesion (all communities <0.3), "
        "reduce cross-package coupling so CRG detects sub-communities, or split the oversized "
        "community. There is no waiver: Round 38 removed it because a waiver was read only by "
        "finalize-gate, while crg-arch-check (CI, every push from phase 3) never saw it — the "
        "waiver bought a local PASS and a red build. For a genuine CRG false positive (workflow "
        "tooling counted as product code, small-package Leiden over-fragmentation) calibrate "
        "crg_excludes / crg_cohesion_healthy in .methodology/harness_config.json, which is "
        "committed and therefore applies to CI as well; "
        "(2) Import boundary violations: verify imports comply with SAD.md layer boundaries and fix violations."
    ),
    "readability":        "Add [FR-XX] docstrings with Citations:; split functions >30 lines",
    "error_handling":     (
        "CRG flow_coverage score: percent of call-chain flows with at least one specific error handler. "
        "Use `get_affected_flows` CRG tool to identify which flows lack handlers. "
        "Fix: add try/except with specific exception types (not bare `except:`) to I/O, "
        "network, and external service calls. Bare `except:` does NOT improve CRG score."
    ),
    "documentation":      "All public APIs need [FR-XX] docstrings with Citations: + line numbers",
    "performance":        "Profile with cProfile; fix N+1 queries; add caching where needed",
    # Round 50 站4d. The six below were added by later rounds with a scorer, a
    # threshold and a weight each, and no remediation — so every block on them
    # fell through to _DEFAULT_DIMENSION_HINT, and `core/lessons.py` copied
    # that sentence into the lesson file as its `Fix:` line. Measured on a
    # full P1-P8 run: 42 lesson files, 42 identical Fix lines, none naming
    # anything about the failure it was written for. The cross-run memory
    # Direction C built recorded forty-two times that something should be
    # reviewed. tests/test_block_reason_registry.py now holds this table
    # against every dimension the gate configs declare.
    "integration_coverage": (
        "Line coverage of the source tree measured by the INTEGRATION suite alone "
        "(03-development/tests/integration), not the unit suite. A low score means "
        "the integration tests exercise a narrow slice: add tests that drive real "
        "collaborations end to end — API in, storage out — rather than more unit "
        "tests, which do not count toward this dimension."
    ),
    "test_assertion_quality": (
        "Percent of test functions containing a substantive assertion. Find them "
        "with the `zero_assert` list in the ast-assertions output: a test whose "
        "only assertion is `assert True` (or which has none) verifies nothing and "
        "is counted as hollow. Give each one a real assertion or delete it."
    ),
    "execute_verification_target": (
        "The project's own verification entry point (`make verify-system` or the "
        "equivalent declared in the SRS) must exit 0. This is pass/fail, not a "
        "percentage: read its output, fix what it reports. A missing target is "
        "the same failure as a failing one — the project declared it."
    ),
    "traceability": (
        "Framework-computed from the trace attestation: every FR reaches code and "
        "a test that RAN. A shortfall names specific FRs — a requirement whose "
        "witness was skipped is not verified (Round 46 站1). Regenerate the "
        "matrix with `harness_cli.py sync-trace` after adding the missing link, "
        "and check the FR id appears in a test title (`test_frNN_*`)."
    ),
    "architecture_constraints": (
        "The SAB's declared layer boundaries versus the imports actually present. "
        "Run import-linter to see which contract broke; fix the import, or amend "
        "the SAB if the new dependency is intended — a constraint the code "
        "outgrew is amended in SAD.md first, never silently."
    ),
    "adversarial_review": (
        "Framework-owned (Gate 3): the bug-hunt findings the project has not "
        "resolved. Each finding names a file and a line; close them by fixing the "
        "defect and recording the fix_commit in bug_hunt_report.json. Re-running "
        "the gate without changing code re-rolls the same judgement."
    ),
}

_DEFAULT_DIMENSION_HINT = "Review dimension-specific issues in SSI output"


@dataclass(frozen=True)
class BlockReason:
    """One reason a gate blocked, with the action that resolves it.

    `items` carries the concrete offenders (dimension names, violation
    strings) so the agent sees WHAT, not just WHICH CATEGORY.
    """

    kind: str
    headline: str
    remediation: str
    items: list[str] = field(default_factory=list)


# ── details-key registry ────────────────────────────────────────────────
# key -> (headline, remediation). Every `details` key raised anywhere in
# harness_bridge.py must appear here; tests/test_block_reason_registry.py
# enforces it in both directions.
#
# The remediation for `tool_score_fabrication` is the reason this whole
# module exists: the fix is NOT to re-run the gate. Re-running re-rolls the
# same agent judgement against the same code. The claimed score has to be
# made true, or the claim withdrawn.
_DETAIL_REGISTRY: dict[str, tuple[str, str]] = {
    "tool_score_fabrication": (
        "Claimed dimension score could not be reproduced by running the tool",
        "Do NOT re-run the gate — the score, not the run, is what failed. For each "
        "dimension listed: run the tool yourself, read its real output, and either "
        "fix the code until the tool agrees with the claimed score, or write the "
        "score the tool actually produced into the gate result file.",
    ),
    "tool_evidence_missing": (
        "Dimension claims a passing score with no tool evidence attached",
        "Add `tool_evidence` (inline tool output) or `evidence_file` (path to the "
        "captured run) to each dimension listed in the gate result file, then re-run "
        "finalize-gate. A score with no evidence is not a measurement.",
    ),
    "infra_fail": (
        "Dimension scored zero because its tool could not run (infrastructure, not quality)",
        "This is an environment failure, not a code defect — do NOT send it into a "
        "code-fix round. Install/repair the tool named in each entry, verify it runs "
        "standalone, then re-run run-gate so the dimension gets a real measurement.",
    ),
    "malformed_gate_result": (
        "The gate result file is structurally invalid",
        "The gate result file is truncated or violates its schema — re-run run-gate "
        "to regenerate it. Do not hand-edit it into shape; a hand-repaired file hides "
        "whichever step produced the broken one.",
    ),
    "crg_graph_incomplete": (
        "The architecture graph covers fewer files than the project delivers",
        "The score would be a score of a subset. Make each named file parseable by "
        "code-review-graph, or take it out of the delivered set (delete it, or "
        ".gitignore it if it is generated). Do NOT lower the architecture score to "
        "work around it — the framework owes the measurement, not the project. "
        "Full coverage is the normal outcome of a correct build, measured at "
        "exact parity on every project this rule was validated against.",
    ),
    "crg_independent_failed": (
        "The harness's independent CRG measurement failed to run",
        "The framework could not compute its own architecture number, so the agent's "
        "claim cannot be checked. Fix the CRG invocation error shown, then re-run "
        "finalize-gate. Treat a persistent failure as a harness defect "
        "(crash-triage --open-cr), not a project defect.",
    ),
    "architecture_regression": (
        "Architecture score regressed against the previous gate's baseline",
        "The code got structurally worse since the last exit gate. Compare against "
        "`.methodology/crg_baseline_p*.json`, undo the coupling that caused the drop, "
        "then re-run finalize-gate. Raising a waiver does not clear a regression.",
    ),
    # Round 38 removed the `da_waiver` entry along with the raise site that
    # produced it. A waiver is now refused at collection time
    # (cli/gate_cmds.py::_collect_da_waivers) and never reaches finalize_gate,
    # so an entry here would be a remediation with no cause — the dead-registry
    # shape Round 36 spent a round unwinding.
}


def _unknown_detail_reason(key: str, value: Any) -> BlockReason:
    """A details key with no registry entry still has to reach the agent.

    Deliberately not an exception: this runs on the BLOCKED path, and turning
    a gate block into a harness crash strictly reduces what the agent can act
    on. The registry gap is caught at test time by
    tests/test_block_reason_registry.py instead.
    """
    return BlockReason(
        kind=key,
        headline=f"Gate blocked by `{key}` (no remediation registered for this cause)",
        remediation=(
            "This is a harness defect: harness_bridge raised a block reason that "
            "core/quality_gate/block_reason.py does not know how to explain. The raw "
            "detail is printed above — act on it directly, and file the gap with "
            "`harness_cli.py crash-triage --open-cr`."
        ),
        items=_as_items(value),
    )


def _as_items(value: Any) -> list[str]:
    if isinstance(value, (list, tuple, set)):
        return [str(v) for v in value]
    if isinstance(value, dict):
        return [f"{k}: {v}" for k, v in value.items()]
    if value is None:
        return []
    return [str(value)]


def derive_block_reasons(
    gate_num: int,
    result: Any,
    details: dict | None = None,
) -> list[BlockReason]:
    """Every reason this gate blocked, most specific first.

    `result` is duck-typed (a harness_bridge.GateResult) so core does not
    import the bridge. Order: `details` causes, then failing dimensions, then
    the result-level fallback (`open_critical` / `open_high` /
    `quality_complete`) — the last of which fires for the one raise site that
    carries no details at all and can block with every dimension passing.
    """
    reasons: list[BlockReason] = []

    for key, value in (details or {}).items():
        entry = _DETAIL_REGISTRY.get(key)
        if entry is None:
            reasons.append(_unknown_detail_reason(key, value))
            continue
        headline, remediation = entry
        reasons.append(BlockReason(
            kind=key, headline=headline, remediation=remediation, items=_as_items(value),
        ))

    failing = [
        d for d in (getattr(result, "dimensions", None) or [])
        if getattr(d, "score", None) is not None
        and d.score < getattr(d, "threshold", 0)
    ]
    for dim in failing:
        gap = dim.threshold - dim.score
        reasons.append(BlockReason(
            kind="dimension_below_threshold",
            headline=f"{dim.name} scored {dim.score:.1f}, needs {dim.threshold:.1f} (gap {gap:.1f})",
            remediation=DIMENSION_HINTS.get(dim.name, _DEFAULT_DIMENSION_HINT),
            items=[dim.name],
        ))

    if not reasons:
        reasons.extend(_result_level_reasons(gate_num, result))

    return reasons


def _result_level_reasons(gate_num: int, result: Any) -> list[BlockReason]:
    """Fallback for the detail-less raise site: what in the result blocked it.

    Without this, a gate that blocks on `open_critical > 0` with every
    dimension passing renders an empty failure list — the exact shape observed
    in run-all-by-workflow's last_block.md.
    """
    reasons: list[BlockReason] = []
    open_critical = int(getattr(result, "open_critical", 0) or 0)
    open_high = int(getattr(result, "open_high", 0) or 0)

    if open_critical:
        reasons.append(BlockReason(
            kind="open_critical_findings",
            headline=f"{open_critical} unresolved CRITICAL finding(s)",
            remediation=(
                "Every dimension passed its threshold — what blocks the gate is the "
                "open CRITICAL findings recorded in the gate result file "
                "(`.sessi-work/gate{n}_result.json`, `findings` / `issues`). Resolve each "
                "one and mark it resolved there, then re-run finalize-gate. There is "
                "nothing to fix in the dimension scores.".replace("{n}", str(gate_num))
            ),
            items=[f"open_critical={open_critical}"],
        ))
    if open_high:
        reasons.append(BlockReason(
            kind="open_high_findings",
            headline=f"{open_high} unresolved HIGH finding(s)",
            remediation=(
                "Resolve the open HIGH findings in the gate result file "
                "(`.sessi-work/gate{n}_result.json`) and mark them resolved, then re-run "
                "finalize-gate.".replace("{n}", str(gate_num))
            ),
            items=[f"open_high={open_high}"],
        ))

    if not reasons:
        score = getattr(result, "score", None)
        score_txt = f"{score:.1f}" if isinstance(score, (int, float)) else "unknown"
        reasons.append(BlockReason(
            kind="composite_below_gate",
            headline=f"Composite score {score_txt} is below this gate's score gate",
            remediation=(
                "No single dimension failed and there are no open findings — the "
                "weighted composite itself is short. Raise the lowest-weighted passing "
                "dimensions (see the passing list in the diagnostic) and re-run "
                "finalize-gate."
            ),
            items=[f"composite={score_txt}"],
        ))
    return reasons
