"""
Harness Bridge: Integration layer between the quality harness and the methodology.

Handles gate execution, results parsing, and quality manifest updates.
"""

from __future__ import annotations

# Round 80 站8: the Gate-evidence checks and the tables they read now live
# in harness/gate_checks.py. Re-exported: every caller here, in cli/ and in
# tests reaches them by these names, and a split that drops the wiring still
# imports and still passes unit tests (tests/test_god_file_split_safety.py).
from harness.gate_checks import (  # noqa: F401,E402  re-export after Round 80 站8 split
    _mutation_artifact_violations,
    DIMENSION_EXCLUSION_FILES,
    _INFRA_FAIL_EVIDENCE_SIGNATURES,
    _TOOL_CONTENT_PATTERNS,
    _TOOL_OUTPUT_MIN_BYTES,
    _TOOL_REQUIRED_PATTERNS,
    _check_infra_fail_pollution,
    _check_test_skip_ratio,
    _check_tests_failed,
    _check_tool_evidence,
    _gate_dimension_names,
    _parse_skip_counts,
    _validate_tool_content,
    _verify_system_reach_block,
    path_escapes_root,
)
# Round 81 站2: the shared gate-result writer now lives in harness/gate_io.py.
# Re-exported: eight call sites here reach it by this name. It left ahead of
# `_crg_enrich_gate_findings`, whose closure it was the only obstacle to.
from harness.gate_io import (  # noqa: F401,E402  re-export after Round 81 站2 move
    _atomic_write_gate_result,
)
# Round 81 站3: the CRG enrichment moved to harness/gate_crg.py, which 站2's
# writer move unblocked. Re-exported: finalize_gate calls it through this
# module's globals and four tests patch it by this name.
from harness.gate_crg import (  # noqa: F401,E402  re-export after Round 81 站3 move
    _crg_enrich_gate_findings,
)
import json
import re
import sys
import dataclasses
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover — annotation-only
    from collections.abc import Iterable

    from core.quality_gate.gate1_evidence import FrCoverage

from harness.crg_bridge import CRGBridge
from harness.decision_log import DecisionLogWriter, DecisionLogEntry, DecisionContext
from harness.effort_tracker import EffortTracker
from core.phase_topology import PER_FR_GATE1_PHASES
from core.quality_gate.constitution.profile import GateConfig
from core.utils.project_layout import ProjectLayout

try:
    from core.atomic_io import atomic_write_json  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover  (graceful degrade)
    atomic_write_json = None  # type: ignore[assignment]

# Round 82 站5: the records this gate produces, the block it raises and the
# four readers of them now live in harness/gate_result.py, so that 站6 can put
# the sixteen `_stage_*` methods in a mixin whose module does not import back
# into this one. Re-exported: every `from harness.harness_bridge import
# GateBlockedError` in the repo, and every use below.
# Round 82 站6: the sixteen stages `finalize_gate` runs are a mixin now, in
# harness/gate_stages.py. Imported (not re-exported) because they are reached
# as `self._stage_*` and nothing outside the class calls them by name.
from harness.gate_stages import _FinalizeStages  # noqa: E402

from harness.gate_result import (  # noqa: F401,E402  re-export after Round 82 站5 move
    SCORE_SOURCE_AGENT_UNVERIFIED,
    SCORE_SOURCE_FRAMEWORK,
    SCORE_SOURCE_FRAMEWORK_NA,
    SCORE_SOURCE_STUBBED_BOUNDARY,
    DimResult,
    GateBlockedError,
    GateResult,
    _SOURCES_NOT_FRAMEWORK_MEASURED,
    declared_dimensions,
    framework_measured,
    measurement_scope,
    s4_block_details,
)


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


def absent_declared_dimensions(
    declared: "Iterable[str]", reported: "Iterable[str]",
) -> list[str]:
    """Dimensions the gate config declares and the result never mentions.

    Round 60 站4. `_all_dims_pass` iterates the dimensions the AGENT REPORTED;
    `_cfg_dims` was read only to build `_s4_verifiable`. Nothing compared the
    two sets, so a declared dimension absent from the breakdown entirely was
    not a failing dimension — it was no dimension at all. A repository-wide
    search for the comparison found none.

    Measured over the eight corpus projects' 32 committed gate results: ten
    omissions. Five are historical (the dimension entered the YAML after that
    result was written) and three were the mutation flag, which Round 60 站2
    retired — but two are neither. taskq (2026-07-27) and taskq-plus
    (2026-08-01) each published a Gate 1 result with no
    `architecture_constraints` entry, a dimension `gate1_per_fr.yaml` has
    declared since 2026-06-22. Both predate the 2026-08-11 GATE1 prompt fix
    (Round 17's Resolution note); the six later results all carry it.

    An extra dimension the agent volunteered is not this function's business:
    it is scored on its own merits and cannot hide anything.
    """
    return sorted(set(declared) - set(reported))




def composite_over(
    dims: "list[DimResult]",
    weights: "dict[str, float]",
) -> dict:
    """The weighted composite, and the denominator it was actually taken over.

    Returns ``{score, weight, dimensions}``. `weight` is not an estimate of
    the denominator or a second derivation of it — it is the sum this
    function divided by, which is what `measurement_scope` publishes beside
    the number.

    The default weight for a dimension the gate config does not price is
    `1 / len(dims)`, unchanged from the loop this replaces. `measurement_scope`
    prices such a dimension at 0.0 instead; the two have never disagreed in
    production because every gate YAML prices every dimension it declares, and
    reconciling them is a separate decision from this one (recorded in
    docs/PROPOSAL_ADJUDICATIONS.md rather than made here).
    """
    scored = sorted((d for d in dims if framework_measured(d)),
                    key=lambda d: d.name)
    fallback = 1.0 / max(len(dims), 1)
    weighted = 0.0
    total = 0.0
    for d in scored:
        w = weights.get(d.name, fallback)
        weighted += (d.score or 0.0) * w
        total += w
    return {
        "score": weighted / max(total, 0.001),
        "weight": round(total, 10),
        "dimensions": [d.name for d in scored],
    }





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
# S4-B: Failed-tests assertion
# ---------------------------------------------------------------------------







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






def _run_harness_cross_validation(
    ctx: "GateContext", raw: dict,
    tool_runs: "dict[str, tuple[str, str, int]] | None" = None,
) -> "tuple[list[str], list[str]]":
    """S4: Run tools independently and cross-validate agent-reported scores.

    `tool_runs` is an out-parameter (same shape as Round 74 站2's
    `_parse_test_spec(spec_path, unread=None)`): when given, each dimension
    the harness actually executed is recorded as
    ``dim_name -> (tool, output, returncode)``. Nothing here reads it. It
    exists so that a later check in the same finalize can be decided from the
    run THIS function performed, instead of from the excerpt the agent pasted
    — see `_check_tests_failed` (Round 77 站1), which was deciding "are this
    FR's tests red?" by regex over `tool_evidence` while the answer sat in
    `output` forty lines above it.


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

        # No early exit on a self-reported FAIL. Round 35 站3, immediately
        # above, already named the flaw this used to have for mutmut: "the
        # early exit below is correct about fabrication and wrong about
        # attribution" — a self-reported failing score CAN be false, just in
        # the other direction (mistaken or hallucinated, not inflated), and
        # skipping verification there was never anything but a cost-saving
        # assumption. Confirmed wrong on a real taskq-verify Gate 2 round:
        # an earlier round's bandit run (committed evidence, S4-confirmed
        # passing at 96) was silently overwritten by a LATER round's
        # self-reported `security: 0` carrying a fabricated technical
        # explanation — because 0 < threshold, this function never looked
        # again, and the gate burned all 3 rounds chasing a dimension that
        # was never actually broken. Every dimension now runs through
        # `run_tool` unconditionally; `s4_score_verdict` below already
        # handles the result correctly in both directions — its
        # `fabrication` flag is `harness_score < threshold <= agent_score`,
        # which cannot fire here since `agent_score < threshold` makes that
        # false by construction. A self-reported FAIL that the tool actually
        # confirms is a PASS is corrected, not flagged.
        output, returncode = run_tool(tool, ctx.project_root)
        if tool_runs is not None:
            tool_runs[dim_name] = (tool, output, returncode)

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

    One of the two readers of TEST_SPEC.md's declaration tables — the other
    is `core.quality_gate.spec_coverage._parse_test_spec`, which this one's
    docstring used to claim it was called by. It never was. Round 73 fixed
    that reader's hardcoded `cols[1]` and Round 74 replaced its keyword-based
    header test, and both defects sat here untouched for the whole of it:
    a sibling of a fixed defect, in a function whose own comment said it was
    the fixed one (Rounds 20 and 39).

    Round 74 站3. The row layer is now shared verbatim — `_is_header_row`,
    `_header_columns`, `_row_test_fn` — so a project cannot be read one way
    by the Gate 1 per-FR spec cap and another way by D4 spec-coverage.
    `tests/test_test_spec_parser_parity.py` registers both readers and holds
    them to the same answer.

    What is NOT shared is the section layer, and deliberately: this function
    treats `### NFR-01` as a section id its caller can ask for, while
    `_parse_test_spec` slugifies every non-FR heading. Merging that would
    change which rows the Gate 1 per-FR cap counts, which is a different
    question from the one this station is answering.

    Terminates the current FR section on:
      - A new ### FR-XX / ### NFR-XX header
      - Any H2 heading (## …) — e.g. ## Cross-Cutting Integration Tests
      - A horizontal rule (---) — used as section divider in some spec styles
    Supports both old bullet-list format and the current Markdown-table format.

    Measured across the nine projects on this machine before the change: zero
    difference. Every `### FR-xx` table in all nine puts Test Function in
    column 1, and the one row whose Title prose reads as a header sits under
    an H2 that has already cleared `current_fr`. A latent sibling, not a live
    wound, and recorded as such.
    """
    import re as _re

    from core.quality_gate.spec_coverage import (
        _SEPARATOR_ROW, _header_columns, _is_header_row, _row_test_fn,
    )

    names: list[str] = []
    current_fr = ""
    in_table = False
    columns: dict = {}
    lines = spec_text.splitlines()
    for idx, line in enumerate(lines):
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
        if _is_header_row(lines, idx):
            columns = _header_columns(stripped)
            in_table = bool(columns)
            continue
        # Table separator row
        if in_table and _SEPARATOR_ROW.match(stripped):
            continue
        # Table data row
        if in_table and stripped.startswith("|") and stripped.endswith("|"):
            raw_fn = _row_test_fn(
                [c.strip() for c in stripped.split("|")[1:-1]], columns.get("fn"))
            if raw_fn:
                names.append(raw_fn)
        elif in_table and not stripped.startswith("|") and stripped:
            in_table = False
    return names


class HarnessBridge(_FinalizeStages):
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
        self._stage_shape_contract(_shape, ctx)

        # ── Round 12 站3b: INFRA_FAIL ≠ quality failure ──────────────────────
        # Reject zero scores whose evidence carries a run-gate PRECONDITION-
        # block signature BEFORE any of them can enter the manifest as fake
        # quality zeros (2026-07-16 phantom-module incident: 3 dims zeroed,
        # CODE-FIX dispatched at healthy code). The BLOCK message is the
        # navigation: fix the precondition, not the source.
        _infra_violations = _check_infra_fail_pollution(raw)
        self._stage_infra_fail_pollution(_infra_violations, ctx)

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
        self._stage_persist_cited_evidence(ctx, persist_cited_evidence, raw, result_path)

        _evidence_digests: dict = {}
        _tool_violations = _check_tool_evidence(ctx, raw, _evidence_digests)
        self._stage_tool_evidence(_evidence_digests, _tool_violations, ctx, raw, result_path)

        # ── Round 51 站2: which declared constraints nobody checks ───────────
        # The SAB's `architecture_constraints` list reaches CLAUDE.md and the
        # evaluation prompt above and nothing else, so "the agent was told" has
        # been the whole enforcement. Classify it here, at the moment the gate
        # is decided, and leave the ones with no executor in the ledger —
        # taskq-api's VERIFICATION_REPORT certified five constraints honoured
        # while two of them were being violated in the delivered tree.
        from core.quality_gate.arch_constraints import (
            contract_coverage_blocking_reason,
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
        self._stage_declared_constraints(_constraint_reason, ctx)

        # ── Round 67 站7: a contract that does not reach the delivered tree ──
        # `contract_coverage_gap` has computed this since Round 46 and its
        # docstring ends "The caller decides what a missing contract means for
        # its gate". There was one caller and it wrote a ledger row — 130 of
        # them in taskq-cc, every one naming taskq_api / __main__ / cli, while
        # lint-imports reported the contract kept for the whole run.
        #
        # Distinct from the `declared_only` case above, which Round 54 settled
        # and this does not reopen: there, nothing in the framework can decide
        # the constraint and the only way to clear a block would be to delete
        # a true declaration. Here the contract exists, the tool runs, and the
        # uncovered modules are a list the framework already has.
        _coverage_reason = contract_coverage_blocking_reason(ctx.project_root)
        self._stage_coverage_denominator(_coverage_reason, ctx)

        # ── Round 68 站1: the files the project says it must ship ────────────
        # Every other check in this framework reads one artifact against
        # another. None opened the tree. taskq-cc published Gate 4 PASS at
        # 95.28 with `.env.example` absent — SPEC §8 #26 is a grep over it —
        # and with `migrations/` and `alembic.ini` under `03-development/src/`
        # while SAD.md:45 asserts the tree "matches SPEC.md §6 exactly".
        #
        # The declaration is the project's, which is the limit of what this
        # buys and is stated in the module rather than hidden: a project that
        # declares nothing gets a ledger row, not a block. What it does buy is
        # that a declaration which IS made is decided by something that always
        # runs — unlike the `declared_only` constraints above, `Path.exists()`
        # needs no configuration and no guess at a module name.
        from core.quality_gate.required_artifacts import (
            record_required_artifacts,
            required_artifacts_blocking_reason,
        )
        _artifact_reason = required_artifacts_blocking_reason(
            record_required_artifacts(ctx.project_root)
        )
        self._stage_required_artifacts(_artifact_reason, ctx)

        # ── Round 51 站3: a number measured over a suite that removed the
        # thing it measures ──────────────────────────────────────────────────
        # Round 67 站2 keeps the findings: the verdict now refuses these
        # dimensions, and a refusal a project cannot act on is the shape
        # Round 48 named. The rows say which fixture in which file replaced
        # which module.
        _boundary_findings = _mark_stubbed_boundary_dimensions(ctx, raw)

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
        self._stage_verify_target(_vt_reason, ctx)
        # Round 77 站1: the runs S4 performs are kept so S4-B below can be
        # decided from them rather than from the agent's pasted excerpt.
        _s4_tool_runs: "dict[str, tuple[str, str, int]]" = {}
        _s4_fabrication, _s4_unverifiable = _run_harness_cross_validation(
            ctx, raw, _s4_tool_runs)
        self._stage_s4_cross_validation(_s4_fabrication, _s4_unverifiable, ctx)

        # ── Round 52 站2: the replaced boundary had to run somewhere ─────────
        # Placed after S4 because S4 is what runs `system-verification`, and
        # the reach artifact is written by that run (harness/tool_runners.py's
        # reach_instrumentation) rather than by a second execution of the
        # target. Round 51 站3 recorded which modules the suite replaced with
        # an autouse stand-in and let the number stand; the obligation is that
        # each one is executed for real by the project's own verification
        # target. Five of the six projects here owe nothing.
        _reach = _verify_system_reach_block(ctx)
        self._stage_system_reach(_reach, ctx)

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
        # S4 validates coverage % but not whether tests are red — a passing
        # coverage score with failing tests is always a fabrication signal.
        # Round 77 站1: the subject is the run S4 just made (`_s4_tool_runs`),
        # scoped to this FR by the framework's own per-test-ownership
        # predicate. Round 76 scoped it too, but by regex over the agent's
        # `tool_evidence`, where one recognisable FAILED line waived every
        # failure the regex could not see.
        if ctx.gate_num == 1:
            _s4b_run = _s4_tool_runs.get("test_coverage")

            # ── Round 77 站2: the waiver leaves a record ─────────────────────
            # Called BEFORE the block below, so the mixed case (this FR red
            # AND others red) still names the others — Round 76's `if scoped:
            # return` returned first and they left no trace at all. The
            # decision, and why it is a record rather than a block, is
            # core/quality_gate/fr_test_scope.py.
            from core.quality_gate.fr_test_scope import (
                readable_run_output,
                record_measured_tests_failed,
                record_waived_test_failures,
            )
            record_waived_test_failures(
                ctx.project_root, ctx.fr_id, ctx.phase, _s4b_run)

            # ── Round 77 站5: the declared field gets a reader ────────────────
            # `tests_failed` has been REQUIRED in the GATE1 prompt since it was
            # written, and nothing in the tree read it. The framework's own
            # count is written into `raw` here, which
            # `build_persisted_gate_result` merges into the committed
            # artifact; where it cannot measure, `_check_tests_failed` blocks
            # on the agent's own declaration instead.
            record_measured_tests_failed(
                raw, ctx.fr_id, ctx.phase, _s4b_run, ctx.project_root)

            _s4b_violations = _check_tests_failed(
                raw, fr_id=ctx.fr_id, framework_run=_s4b_run)
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
            _skip_warn = _check_test_skip_ratio(raw, framework_run=_s4b_run)
            if _skip_warn:
                print(_skip_warn)
            # Round 46 站2: the WARN above is printed and gone. Every skip gets
            # a ledger row regardless of ratio — not as a verdict (the gate
            # verdict for a requirement's own skipped guard comes from the
            # traceability dimension), but so that "how many tests did not run
            # at this gate" is answerable after the run without a person
            # having watched the console.
            #
            # Round 77 站6: both readers take the numbers from the run S4 made
            # when there was one, and `source` records which run answered —
            # the framework's is the whole suite, the agent's excerpt was its
            # per-FR scoped run, and a row that does not say which cannot be
            # compared against the row beside it.
            _skip_counts = _parse_skip_counts(raw, _s4b_run)
            if _skip_counts and _skip_counts[0] > 0:
                from core.degradation_ledger import record_degradation
                _skipped, _total = _skip_counts
                record_degradation(
                    ctx.project_root, "gate:test-skips",
                    f"{_skipped} of {_total} tests did not run",
                    why="skipped tests contribute no coverage and no evidence",
                    data={"skipped": _skipped, "total": _total,
                          "gate": ctx.gate_num, "fr_id": ctx.fr_id,
                          "source": ("harness-run"
                                     if readable_run_output(_s4b_run)
                                     else "agent-evidence")}, owner="project"
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
        self._stage_spec_coverage_cap(_spec_cap, _spec_names, dims, raw)

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
            # Dimensions the framework did not measure leave the average, and
            # their weight is redistributed across the rest — a score of None
            # (pytest-benchmark with no benchmarks: not yet applicable) and a
            # score measured over a stubbed boundary are both "not a number
            # about this tree".
            #
            # Round 67 站2: this loop used to ask `d.score is None` and nothing
            # else, so `measurement_scope` published `weight_covered: 0.88`
            # beside a composite averaged over 1.0 — one artifact, two
            # denominators. Both now read `framework_measured`, and the
            # denominator that gets published is the one this division used.
            _overall_score = composite_over(dims, _dim_weights)["score"]
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
        # Round 70 站1: both fallbacks used to read `80` and neither was ever
        # what this line compared against for Gate 1 — `GateConfig.from_dict`
        # resolved `score_gate` to 1.0 (it read `gate: 1` as a threshold), and
        # a dataclass field is always present so the getattr default was dead.
        # A ctx carrying no floor now defers to `effective_score_gate`, the
        # same function the GATE1 prompt states its bar from.
        _cfg_gt = (
            ctx.config.get("score_gate")
            if isinstance(ctx.config, dict)
            else getattr(ctx.config, "score_gate", None)
        )
        if _cfg_gt is None:
            from core.quality_gate.constitution.profile import effective_score_gate
            _cfg_gt = effective_score_gate(ctx.gate_num)
        _gt = _cfg_gt

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

        # Round 60 站4: a dimension the gate declares and the result never
        # mentions. This runs HERE, after every framework override has had its
        # say — `_override_traceability_dim_score` and
        # `_override_adversarial_review_dim_score` APPEND their dimension when
        # the agent omitted it, precisely so a framework-owned dimension does
        # not depend on agent cooperation to exist. Comparing before them would
        # report the framework's own dimensions as missing.
        _absent = absent_declared_dimensions(
            [_d.get("name", "") for _d in _cfg_dims if _d.get("name")],
            [d.name for d in dims],
        )
        self._stage_absent_dimensions(_absent, _overall_score, ctx, dims)

        # Round 67 站2: a number the framework did not measure over the
        # delivered code is not evidence that dimension passed. `_SOURCES_NOT_
        # FRAMEWORK_MEASURED` has said which numbers those are since Round 50,
        # and its own comment says it is one definition so two readers cannot
        # drift — it had two readers and neither was the verdict. Measured on
        # taskq-cc: `test_coverage` 100.0 over a suite that replaces
        # `taskq_api.service.auth` in five files, published PASS at 95.28.
        #
        # This runs before the threshold comparison rather than inside
        # `_dim_passes`, because the two answer different questions and a
        # project needs to be told which one it failed: "the number is too
        # low" is fixable by better code, "the number is not about your code"
        # is not.
        _unmeasured = sorted(
            d.name for d in dims
            if d.score is not None and not framework_measured(d)
        )
        self._stage_stubbed_boundaries(_boundary_findings, _overall_score, _unmeasured, ctx, dims)

        def _dim_passes(d: DimResult) -> bool:
            if d.score is not None:
                return d.score >= _effective_threshold(d)
            if d.name not in _s4_verifiable:
                return True  # framework cannot check it — see the note above
            return d.name in _framework_na_dims

        _all_dims_pass = all(_dim_passes(d) for d in dims) \
            if dims else False  # empty dims = no evidence = not complete
        _quality_complete = _overall_score >= _gt and _all_dims_pass

        self._stage_dimension_thresholds(_all_dims_pass, _dim_passes, _dim_weights, _effective_threshold, ctx, dims)
        # Round 73 站5: non-blocking must not mean free (Round 68 站1). The row
        # is what a later round reads to ask whether a gate's dimension list
        # and the manifest's NFR mapping were ever meant to differ; owner is
        # `harness` because the project's value is legal by SPEC's own rule
        # and the gate config is the framework's.
        _declared_absent = ctx.measurement_scope[  # type: ignore[attr-defined]
            "dimensions_declared_absent"]
        self._stage_declared_absent(_declared_absent, ctx, raw)

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

        _stage_rc = self._stage_record_verdict(_gate_passes, ctx, result, t0, time)
        if _stage_rc is not None:
            return _stage_rc

        # The contract's fall-through. Unreachable today — `_stage_record_verdict`
        # carries this method's original terminal `return result` and always
        # returns — but `-> GateResult` has to be true of the annotation, not
        # only of today's implementation.
        raise GateBlockedError(ctx.gate_num, result)

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
