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
from typing import Any

from harness.crg_bridge import CRGBridge
from harness.decision_log import DecisionLogWriter, DecisionLogEntry, DecisionContext
from harness.effort_tracker import EffortTracker, EffortRecord
from core.quality_gate.constitution.profile import GateConfig


@dataclass
class DimResult:
    """Result of a single quality dimension evaluation."""
    name: str
    score: float
    threshold: float
    issues: list[dict] = field(default_factory=list)


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
}

# Minimum byte size for a tool_output file to be considered non-stub.
# Real tool output is always larger than this; pure comment lines are typically
# under 80 bytes.
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
            _new_dims.append(dataclasses.replace(_d, score=_framework_score))
        else:
            _new_dims.append(_d)
    return _new_dims, _changed


def _extract_mutmut_kill_rate(content: str) -> "float | None":
    """Parse a mutmut tool_output file and return the computed kill rate (0-100).

    Supports both v2 and v3 output formats:
      - "Killed XX Survived YY"  (plain text or table)
      - "mutation score: NN%"    (summary line)

    Returns None when no parseable statistics are found (e.g. empty output or
    an unrecognised format — treated as a non-blocking parse failure).
    """
    import re as _re

    # Format A: explicit "Killed N" and "Survived N" counts
    killed_m = _re.search(r'\bKilled\s+(\d+)', content, _re.IGNORECASE)
    survived_m = _re.search(r'\bSurvived\s+(\d+)', content, _re.IGNORECASE)
    if killed_m and survived_m:
        killed = int(killed_m.group(1))
        survived = int(survived_m.group(1))
        total = killed + survived
        if total > 0:
            return killed / total * 100.0

    # Format B: explicit "mutation score: NN%" / "mutation score NN%"
    score_m = _re.search(
        r'mutation\s+score[:\s]+(\d+(?:\.\d+)?)\s*%', content, _re.IGNORECASE
    )
    if score_m:
        return float(score_m.group(1))

    return None


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
        for _d in dims:
            if _d.name == "architecture":
                _d.issues.append({
                    "severity": "medium",
                    "message": (
                        f"Large functions detected (≥300 lines): {len(warn_items)} function(s). "
                        "Refactor to improve cohesion."
                    ),
                    "evidence": "; ".join(warn_items[:5]),
                    "source": "crg:find_large_functions",
                })

    # ── 2. get_hub_nodes → architecture findings (re-used in step 8) ──
    hub_data = crg.get_hub_nodes(project_root, min_fan_in=8)
    hub_hubs = (hub_data or {}).get("hubs", [])
    critical_hubs = [h for h in hub_hubs if (h.get("fan_in") or 0) >= 15]
    if critical_hubs:
        for _d in dims:
            if _d.name == "architecture":
                _d.issues.append({
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
                })

    # ── 3. check_dead_code → architecture findings ────────────────────
    dc_data = crg.check_dead_code(project_root, kind="Function")
    dead_items = (dc_data or {}).get("dead_code", [])
    prod_dead = [x for x in dead_items if "/tests/" not in (x.get("file") or "")]
    if len(prod_dead) > 10:
        # Severity based on absolute count — total_nodes not available here without
        # an extra MCP call. >20 is reliably > 5% of any non-trivial project,
        # matching crg_analysis.DEAD_CODE_ESCALATE_RATIO intent.
        sev = "medium" if len(prod_dead) > 20 else "low"
        for _d in dims:
            if _d.name == "architecture":
                _d.issues.append({
                    "severity": sev,
                    "message": (
                        f"Dead code: {len(prod_dead)} unreferenced functions/classes "
                        "detected in production code."
                    ),
                    "evidence": "; ".join(
                        x.get("name", "?") for x in prod_dead[:5]
                    ),
                    "source": "crg:refactor_tool(dead_code)",
                })

    # ── 4. get_review_context → crg_review_context in gate_result ─────
    rc = crg.get_review_context(project_root, detail_level="minimal")
    if rc and result_path.exists():
        try:
            _gr = json.loads(result_path.read_text(encoding="utf-8"))
            _gr["crg_review_context"] = rc
            result_path.write_text(
                json.dumps(_gr, indent=2, ensure_ascii=False), encoding="utf-8"
            )
        except (OSError, json.JSONDecodeError):
            pass

    # ── 5. get_impact_radius → crg_impact_radius in gate_result ───────
    ir = crg.get_impact_radius(project_root, detail_level="minimal")
    if ir and result_path.exists():
        try:
            _gr = json.loads(result_path.read_text(encoding="utf-8"))
            _gr["crg_impact_radius"] = ir
            result_path.write_text(
                json.dumps(_gr, indent=2, ensure_ascii=False), encoding="utf-8"
            )
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
            result_path.write_text(
                json.dumps(_gr, indent=2, ensure_ascii=False), encoding="utf-8"
            )
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
        for _d in dims:
            if _d.name == "test_coverage":
                _d.issues.append({
                    "severity": "medium",
                    "message": (
                        f"CRG knowledge gaps: {len(untested_gaps)} untested critical path(s) detected."
                    ),
                    "evidence": "; ".join(
                        g.get("name") or g.get("description", "?")
                        for g in untested_gaps
                    ),
                    "source": "crg:get_knowledge_gaps",
                })

    # ── 8. list_flows → error_handling context + crg_critical_flows ───
    flow_data = crg.list_flows(project_root, limit=10, sort_by="criticality")
    crit_flows = (flow_data or {}).get("flows", [])
    if crit_flows:
        for _d in dims:
            if _d.name == "error_handling":
                _d.issues.append({
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
                })
        if result_path.exists():
            try:
                _gr = json.loads(result_path.read_text(encoding="utf-8"))
                _gr["crg_critical_flows"] = crit_flows[:10]
                result_path.write_text(
                    json.dumps(_gr, indent=2, ensure_ascii=False), encoding="utf-8"
                )
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
        _new_dims = []
        for _d in dims:
            if _d.name == "test_coverage":
                _d.issues.append({
                    "severity": "high",
                    "message": (
                        f"Hub functions with no test linkage: {len(untested_hubs)} found. "
                        f"Penalising test_coverage by {_hub_penalty} pts (Phase 2 gatekeeper)."
                    ),
                    "evidence": "; ".join(untested_hubs),
                    "source": "crg:query_graph(tests_for)",
                })
                _new_score = round(max(0.0, (_d.score or 0.0) - _hub_penalty), 1)
                print(
                    f"[harness] CRG hub penalty test_coverage: {_d.score:.1f} → "
                    f"{_new_score:.1f} "
                    f"(-{_hub_penalty} for {len(untested_hubs)} untested critical hub(s))"
                )
                if _new_score != _d.score:
                    _score_overridden = True
                _new_dims.append(dataclasses.replace(_d, score=_new_score))
            else:
                _new_dims.append(_d)
        dims = _new_dims

    return dims, _score_overridden


def _check_tool_evidence(ctx: "GateContext", raw: dict) -> list[str]:
    """S3: Verify tool execution evidence in gate result JSON.

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

    # Load gate config to find requires_tool_execution dimensions
    cfg_path = None
    for pattern in [
        f"gate{ctx.gate_num}_p*.yaml",
        f"gate{ctx.gate_num}_*.yaml",
    ]:
        import glob as _glob
        candidates = _glob.glob(
            str(_Path(ctx.project_root) / "harness" / "gate_configs" / pattern)
        )
        if candidates:
            cfg_path = _Path(candidates[0])
            break

    if not cfg_path or not cfg_path.exists():
        return []  # No config — cannot enforce

    try:
        cfg = _yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    except Exception:
        return []

    violations: list[str] = []
    breakdown = raw.get("breakdown", {})

    for dim in cfg.get("dimensions", []):
        dim_name = dim.get("name", "")
        requires_tool = dim.get("requires_tool_execution", False)
        if not requires_tool:
            continue

        tool = dim.get("tool")
        dim_data = breakdown.get(dim_name, {})
        tool_output = dim_data.get("tool_output")
        tool_evidence = dim_data.get("tool_evidence")

        if tool_output:
            out_path = _Path(ctx.project_root) / tool_output
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
                violations.extend(
                    _validate_tool_content(content, tool, dim_name, inline=False)
                )
        elif tool_evidence:
            evidence_str = str(tool_evidence).strip()
            if len(evidence_str) < 10:
                violations.append(
                    f"{dim_name}: tool_evidence too short "
                    f"({len(evidence_str)} chars) — must be real tool output snippet"
                )
            else:
                violations.extend(
                    _validate_tool_content(evidence_str, tool, dim_name, inline=True)
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


def _check_test_skip_ratio(raw: dict, threshold: float = 0.10) -> str | None:
    """W1: Warn when a high fraction of tests are skipped.

    Skipped tests contribute 0 coverage lines.  A skip ratio above *threshold*
    (default 10 %) means coverage is computed from a subset of the suite and
    may miss infrastructure code paths (e.g. DB schema, async sessions).

    This is a **WARN** (not BLOCK) — some projects legitimately skip tests
    that require real external services.

    Returns a warning string, or ``None`` if the skip ratio is within threshold.
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
    if total == 0:
        return None

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
    except Exception:
        return None
    if drift >= dthr:
        return (f"structural drift {drift:.2f} ≥ {dthr:.2f} vs P4 baseline "
                f"({bl.get('_baseline_sha', '?')[:8]})")
    return None


def _run_harness_cross_validation(ctx: "GateContext", raw: dict) -> list[str]:
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

    Raw tool output is written to .sessi-work/harness_verification/ for audit.

    Returns list of fabrication violation messages (empty = all clear).
    """
    import yaml as _yaml
    from pathlib import Path as _Path

    cfg_path = None
    for pattern in [f"gate{ctx.gate_num}_p*.yaml", f"gate{ctx.gate_num}_*.yaml"]:
        import glob as _glob
        candidates = _glob.glob(
            str(_Path(ctx.project_root) / "harness" / "gate_configs" / pattern)
        )
        if candidates:
            cfg_path = _Path(sorted(candidates)[0])
            break

    if not cfg_path or not cfg_path.exists():
        return []

    try:
        cfg = _yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"  [S4-WARN] cross-validation disabled: failed to parse {cfg_path.name}: {exc}")
        return []

    try:
        from harness.tool_runners import run_tool, compute_tool_score
    except ImportError as exc:
        print(f"  [S4-WARN] cross-validation disabled: harness.tool_runners unavailable: {exc}")
        return []

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

    verification_dir = _Path(ctx.project_root) / ".sessi-work" / "harness_verification"
    verification_dir.mkdir(parents=True, exist_ok=True)

    violations: list[str] = []
    breakdown = raw.get("breakdown", {})

    # architecture is scored by the framework's independent CRG run (community_cohesion)
    # inside finalize_gate, not by re-running a tool here. Its tool field is
    # `code-review-graph` (no inline scorer); skip it in cross-validation.
    _crg_owned = {"architecture"}

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

        agent_score = float(breakdown.get(dim_name, {}).get("score", 0))

        # Only cross-validate when the agent claims a passing score.
        # If the agent already reports FAIL, there is no fabrication concern.
        if agent_score < threshold:
            continue

        output, returncode = run_tool(tool, ctx.project_root)

        # Write audit trail regardless of outcome
        audit_file = verification_dir / f"{dim_name}_harness.txt"
        try:
            audit_file.write_text(
                f"# Harness-executed: {tool}\n"
                f"# returncode: {returncode}\n"
                f"# agent_score: {agent_score} | threshold: {threshold}\n\n"
                f"{output}\n",
                encoding="utf-8",
            )
        except OSError:
            pass  # Audit write failure is non-fatal

        if returncode < 0 and returncode != -1:
            # Tool unavailable (rc=-2 timeout, rc=-3 not-found, rc=-4 error).
            # The agent cannot legitimately claim a passing tool-based score
            # when the harness cannot independently verify the tool runs.
            _rc_labels = {-2: "timed out", -3: "not found", -4: "error"}
            _rc_label = _rc_labels.get(returncode, f"rc={returncode}")
            violations.append(
                f"{dim_name}: tool '{tool}' {_rc_label} — harness cannot "
                f"cross-validate agent_score={agent_score:.1f}. "
                f"Install '{tool}' so the harness can independently verify the score, "
                f"or if the tool is genuinely unavailable, the dimension score must be "
                f"set below threshold ({threshold:.0f}) to reflect that."
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
                if not _tpath.exists() or _tpath.stat().st_size < _TOOL_OUTPUT_MIN_BYTES:
                    _problem = f"tool_output file missing or empty: {_tout}"
                else:
                    _fmt = _validate_tool_content(
                        _tpath.read_text(encoding="utf-8", errors="replace"),
                        tool, dim_name, inline=False,
                    )
                    if _fmt:
                        _problem = "; ".join(_fmt)
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
                # mutmut: cross-check computed kill_rate vs agent_score (±5% tolerance).
                # mutmut is too slow to re-run, but we CAN verify the kill_rate that is
                # derivable from its tool_output and compare it to the agent's claimed score.
                # _tpath is guaranteed to be assigned here: we are in the else-branch
                # of `if _problem`, which is only reached when _tout was non-empty AND
                # _tpath.exists() — the pyright possibly-unbound warning is a false positive.
                if tool == "mutmut" and _tout:
                    _kill_rate = _extract_mutmut_kill_rate(
                        (_Path(ctx.project_root) / _tout).read_text(
                            encoding="utf-8", errors="replace"
                        )
                    )
                    if _kill_rate is not None and abs(_kill_rate - agent_score) > 5.0:
                        violations.append(
                            f"{dim_name}: tool_output kill_rate={_kill_rate:.1f}% "
                            f"≠ agent_score={agent_score:.1f} (diff > 5 pp) — "
                            f"suspected fabrication; re-run mutmut and report the actual score."
                        )
                    elif _kill_rate is not None:
                        print(
                            f"  [S4] {dim_name}: mutmut kill_rate={_kill_rate:.1f}% "
                            f"≈ agent_score={agent_score:.1f} ✓"
                        )
            continue
        if returncode == -2:
            # Timed out — a passing score MUST be independently reproducible. A tool
            # that cannot finish within budget cannot confirm the agent's claim, and
            # a slow run is an exploitable fabrication path → block.
            violations.append(
                f"{dim_name}: '{tool}' timed out during harness cross-validation — "
                f"cannot verify agent score {agent_score:.1f}. A passing score must be "
                f"independently reproducible; reduce tool runtime or fix the tool, then re-finalize."
            )
            continue
        if returncode in (-3, -4):
            # -3 not-found (already guarded by S2 _verify_gate_tools before finalize) /
            # -4 tool error (framework-side, not agent-controllable) — warn only.
            print(
                f"  [S4-WARN] {dim_name}: '{tool}' cross-validation skipped "
                f"(returncode={returncode}) — verify manually"
            )
            continue

        if returncode == 5:
            # pytest-family exit 5 = "no tests collected". A passing agent score for a
            # dimension whose measuring suite does not exist is unverifiable — treat as a
            # fabrication risk and BLOCK (no free pass for a missing suite). The message is
            # dimension-generic: exit 5 can come from any pytest-family tool (benchmark,
            # integration-cov, or pytest-cov when a conftest import fails).
            violations.append(
                f"{dim_name}: '{tool}' collected no tests (exit 5) — a passing score for "
                f"'{dim_name}' (agent={agent_score:.1f}) is unverifiable when its measuring "
                f"suite does not exist or fails to import. Add/repair the suite, then re-finalize."
            )
            continue

        harness_score = compute_tool_score(tool, output, returncode)
        if harness_score is None:
            # readability (radon-mi) returns None only when there is NO analysable
            # source (radon availability is already gated by S2 _verify_gate_tools).
            # A passing readability score with nothing to analyse is unverifiable.
            if dim_name == "readability":
                violations.append(
                    f"readability: '{tool}' produced no analysable maintainability score "
                    f"(no source files to analyse) — cannot verify agent score "
                    f"{agent_score:.1f}. A passing readability score requires analysable "
                    f"code; add it, then re-finalize."
                )
            # Other dimensions returning None mean the harness could not re-score
            # (e.g. a tool parse issue) — a framework-side gap, not proof of agent
            # fabrication. Skip rather than falsely blocking the gate (origin behaviour).
            continue

        print(
            f"  [S4] {dim_name}: harness={harness_score:.1f} | "
            f"agent={agent_score:.1f} | threshold={threshold}"
        )

        if harness_score < threshold:
            violations.append(
                f"{dim_name}: fabrication detected — "
                f"harness ran '{tool}' and scored {harness_score:.1f} "
                f"(below threshold {threshold}), but agent reported {agent_score:.1f} "
                f"(above threshold). "
                f"See {audit_file.relative_to(_Path(ctx.project_root))}"
            )

    return violations


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


class PreflightBlockedError(Exception):
    """Raised when preflight validation fails before gate evaluation (Item 9)."""

    def __init__(self, preflight_result: dict):
        self.preflight_result = preflight_result
        failing = [
            k for k, v in preflight_result.get("details", {}).items()
            if isinstance(v, dict) and not v.get("passed", False)
        ]
        super().__init__(f"Preflight BLOCKED — failing checks: {failing}")


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
    # Per-FR test spec coverage: list of required test names + set that exist.
    # Used by finalize_gate() to cap test_coverage score at spec_coverage_pct.
    _spec_test_names: list[str] = field(default_factory=list)
    _existing_spec_tests: set[str] = field(default_factory=set)

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
            f"   For each missing item, include the exact install/fix command.\n\n"
            f"[FORBIDDEN]\n"
            f"- Guessing env var values — only check presence, not correctness\n"
            f"- Fabricating check results without actual tool execution\n"
            f"- Skipping a category because it seems obvious\n"
            f"- Writing result.json without running real verification commands\n\n"
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
        manifest_path = Path(project_root) / ".methodology" / "quality_manifest.json"
        if not manifest_path.exists():
            return {}
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
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
        except Exception:
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
            root / "02-architecture" / "SAD.md",
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
            root / "01-requirements" / "SRS.md",
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
        _existing_spec: set[str] = set()
        if fr_id and gate_num == 1 and phase in {3, 4, 5, 7, 8}:
            _test_dir = Path(project_root) / "03-development" / "tests"
            if not _test_dir.is_dir():
                _test_dir = Path(project_root) / "tests"
            _spec_path = Path(project_root) / "02-architecture" / "TEST_SPEC.md"
            if _spec_path.exists():
                try:
                    _spec_text = _spec_path.read_text(encoding="utf-8")
                    _spec_names = _parse_spec_names_for_fr(_spec_text, fr_id)
                    # Validate: warn if FR section exists but has no table header
                    # (missing header means 0 spec names even though rows exist)
                    _fr_section_exists = bool(
                        re.search(r"^###\s+" + fr_id + r"(?:[:\s]|$)", _spec_text, re.MULTILINE)
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
                    _actual_fns = set()
                    for _f in _test_dir.rglob("*.py"):
                        try:
                            _content = _f.read_text(encoding="utf-8", errors="replace")
                            for line in _content.splitlines():
                                m2 = re.match(r"^\s*(?:async\s+)?def\s+(test_\w+)\s*\(", line)
                                if m2:
                                    _actual_fns.add(m2.group(1))
                        except OSError:
                            continue
                    _existing_spec = set()
                    for fn in _spec_names:
                        raw_fn = fn.strip("`").strip()
                        raw_fn = re.sub(r"\[.*\]$", "", raw_fn)
                        raw_fn = re.sub(r"\(\)$", "", raw_fn)
                        if raw_fn in _actual_fns:
                            _existing_spec.add(fn)
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
            _existing_spec_tests=_existing_spec,
        )

    def finalize_gate(
        self,
        ctx: GateContext,
        da_waivers: "set[str] | None" = None,
    ) -> GateResult:
        """
        Phase 2 of the two-phase gate evaluation API.

        Reads gate{N}_result.json written by Claude's inline evaluation,
        checks thresholds, updates the quality manifest, and records decisions.

        Args:
            ctx: The GateContext returned by prepare_gate().
            da_waivers: Optional set of dimension names whose score threshold is
                bypassed because a Devil's Advocate challenge confirmed intentional
                design (e.g. Orchestrator/hub-and-spoke architecture).

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
        raw = json.loads(result_path.read_text(encoding="utf-8"))

        # ── S3: Tool execution evidence enforcement ──────────────────────────
        # For dimensions with requires_tool_execution:true in the gate YAML,
        # the result JSON must include tool_output (path to raw tool output)
        # or tool_evidence (inline snippet). Prevents LLM score fabrication
        # when tools are installed but never actually run.
        # S3-A (Solution A): content of those files/strings is also validated —
        # stub comments, files that are too small, and content that does not match
        # the expected tool output structure are all rejected.
        _tool_violations = _check_tool_evidence(ctx, raw)
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

        # ── S4: Harness cross-validation (Solution B) ────────────────────────
        # For each Tier 1/2 dimension where the agent claims a passing score,
        # the harness independently runs the tool and computes its own score.
        # If harness_score < threshold but agent_score ≥ threshold, the gate is
        # blocked with a fabrication violation.
        # Slow tools (mutmut, scancode) are skipped here; S3-A covers them.
        print("\n[S4] Running harness cross-validation...")
        _s4_violations = _run_harness_cross_validation(ctx, raw)
        if _s4_violations:
            raise GateBlockedError(
                ctx.gate_num,
                GateResult(
                    gate_num=ctx.gate_num,
                    score=0.0,
                    dimensions=[],
                    open_critical=len(_s4_violations),
                    open_high=0,
                    quality_complete=False,
                    rounds_used=0,
                ),
                details={"tool_score_fabrication": _s4_violations},
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
        _spec_existing: set = getattr(ctx, '_existing_spec_tests', set())
        _spec_cap: float = 100.0
        if _spec_names and len(_spec_existing) < len(_spec_names):
            _spec_cap = len(_spec_existing) / max(len(_spec_names), 1) * 100.0

        dims: list[DimResult] = []
        for dim_name, dim_data in raw.get("breakdown", {}).items():
            score = dim_data.get("score", 0.0)
            if dim_name == "test_coverage" and _spec_names:
                score = min(score, _spec_cap)
            dims.append(DimResult(
                name=dim_name,
                score=score,
                threshold=dim_data.get("threshold", 0.0),
                issues=dim_data.get("issues", []),
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

        # CRG-ONLY dimension override: the *architecture* dimension is scored by the
        # framework's OWN independent CRG run (community_cohesion), never the agent.
        # error_handling was moved to a tool-scored dimension (ast-error-handling) —
        # the CRG flow `has_error_handler` field does not exist in the package.
        _crg_overrides_applied = False
        _crg_metrics_path = Path(ctx.work_dir) / "crg_metrics.json"
        _CRG_ONLY_DIMS = {"architecture"}
        if any(d.name in _CRG_ONLY_DIMS for d in dims):
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
                for _d in dims:
                    if _d.name == "architecture":
                        _old = _d.score
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
                            if len(_unhealthy) > 8:
                                print(f"  ... +{len(_unhealthy) - 8} more (see .sessi-work/crg_metrics.json)")
                            print()
                            print("  Fix: unhealthy communities have low intra-community connectivity.")
                            print("  - Add cross-module imports/tests between files in the same community")
                            print("  - Merge small isolated communities into larger coherent modules")
                            print("  - Split oversized communities (>50) into focused subdirectories")
                            print("  - Or: if hub-and-spoke is intentional (Orchestrator), file a DA waiver")
                    else:
                        _new_dims.append(_d)
                dims = _new_dims

        # ── PR 4 (audit F-1.1 fix): framework trace score override ─────
        # The agent cannot compute the trace dimension (no tool to scan
        # SAD.md + [FR-XX] annotations + test references). The framework
        # runs `compute_trace_dimension` and overrides whatever the agent
        # wrote. Mirrors the CRG override pattern above: replace the
        # score in-place; log the change; never silently lose it.
        dims, _trace_overridden = _override_traceability_dim_score(
            dims, ctx.project_root, ctx.gate_num
        )
        if _trace_overridden:
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
            except Exception as _enrich_err:  # pragma: no cover
                print(
                    f"[WARN] CRG enrichment skipped: {_enrich_err}",
                    file=sys.stderr,
                )

        # SG-2 (robustness audit): per-dimension variance sanity check.
        # If ≥3 dimensions all share the SAME score, that's suspiciously uniform
        # — Claude's per-dim evaluation should produce naturally varied scores.
        # We don't BLOCK here (Claude may legitimately rate dims identically on
        # very small projects), but we LOG to decision_log for forensic review.
        # A future enhancement (deferred audit recommendation) is to compare
        # these scores against a per-dimension evidence trail in .sessi-work/.
        try:
            import statistics as _stats
            dim_scores = [d.score for d in dims]  # B3: include zero-scored dims
            if len(dim_scores) >= 3:
                _stdev = _stats.pstdev(dim_scores)
                if _stdev < 0.5:
                    self._log.write(DecisionLogEntry(
                        ctx=DecisionContext(agent_id="GATE", phase=ctx.phase, fr_id=ctx.fr_id),
                        decision="GATE_VARIANCE_LOW",
                        reasoning=(
                            f"Per-dimension scores cluster tightly "
                            f"(n={len(dim_scores)}, stddev={_stdev:.3f}, scores={dim_scores}). "
                            f"Forensic flag — manually verify evidence trail."
                        ),
                        scores={"dim_stddev": _stdev},
                    ))
        except Exception:  # pylint: disable=broad-exception-caught
            pass  # variance check is advisory — never block finalize

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
            _overall_score = sum(d.score for d in dims) / max(len(dims), 1)
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
        _all_dims_pass = all(
            d.score is not None and d.score >= (_dim_thresholds.get(d.name) or d.threshold or _gt)
            for d in dims
        ) if dims else False  # empty dims = no evidence = not complete
        _quality_complete = _overall_score >= _gt and _all_dims_pass

        if not _all_dims_pass and dims:
            _failing = [f"{d.name}={d.score:.1f}" for d in dims
                        if d.score is not None and d.score < (_dim_thresholds.get(d.name) or d.threshold or _gt)]
            print(f"\n[harness] {len(_failing)} dimension(s) below individual threshold: {', '.join(_failing)}")

        result = GateResult(
            gate_num=ctx.gate_num,
            score=_overall_score,
            dimensions=dims,
            open_critical=raw.get("open_critical_count", raw.get("open_critical", 0)),
            open_high=raw.get("open_high_count", raw.get("open_high", 0)),
            quality_complete=_quality_complete,
            rounds_used=raw.get("rounds_used", 1),
        )

        # DA waivers: zero out threshold for dimensions whose design was justified by a
        # Devil's Advocate challenge (e.g. Orchestrator pattern → architecture score 0 is OK).
        _effective_dims = result.dimensions
        if da_waivers:
            _effective_dims = [
                dataclasses.replace(d, threshold=0.0) if d.name in da_waivers else d
                for d in result.dimensions
            ]

        # Determine final pass/fail state (using effective thresholds) BEFORE writing
        # manifest/log so that manifest and decision log reflect the actual gate outcome.
        _gate_passes: bool
        if ctx.gate_num == 1:
            _gate_passes = not any(d.score is not None and d.score < d.threshold for d in _effective_dims)
        else:
            if isinstance(ctx.config, GateConfig):
                score_gate = ctx.config.score_gate
            else:
                score_gate = ctx.config.get("score_gate", 0)
            # Recompute quality_complete against effective (waived) thresholds.
            # Must also preserve the open_critical == 0 requirement so that a DA waiver
            # cannot allow a gate to pass with unresolved critical issues.
            _eff_qc = result.quality_complete
            if da_waivers and result.dimensions:
                _eff_qc = (
                    result.score >= score_gate
                    and result.open_critical == 0
                    and all(d.score is not None and d.score >= d.threshold for d in _effective_dims)
                )
            _gate_passes = result.score >= score_gate and _eff_qc

        # If DA waivers (or CRG override recompute) changed the pass state,
        # update result.quality_complete so manifest + log reflect the real outcome.
        if _gate_passes and not result.quality_complete:
            result = dataclasses.replace(result, quality_complete=True)

        self._update_quality_manifest(ctx.gate_num, ctx.fr_id, result, da_waivers=da_waivers)

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
        srs_path = project_root / "01-requirements" / "SRS.md"
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
        except Exception:
            return {}

    def _parse_nfr_fr_xref(self, project_root: Path) -> dict[str, list[str]]:
        """Extract NFR→[FR, ...] mapping from the §2 FR Cross-Reference table in SRS.md.

        Looks for a pipe-table whose header contains 'NFR Association'.
        Returns {nfr_id: [fr_id, ...]} reverse mapping.
        """
        srs_path = project_root / "01-requirements" / "SRS.md"
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
        except Exception:
            return {}

    def generate_quality_manifest(self, fr_ids: list[str], sad_path: str) -> Path:
        """Called at P2 exit. Parses SAD.md -> constraints + high_risk_modules."""
        try:
            from scripts.generate_sab import parse_sad
            sab = parse_sad(sad_path)
        except Exception:
            sab = {}

        _project_root = Path(sad_path).parent.parent
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
        # clear; performance's floor comes from NFR derive_gate_score_overrides, and p95
        # is enforced inside the performance dimension's benchmark scorer.
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
        out = Path(".methodology/quality_manifest.json")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
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
        except Exception:
            pass  # hooks are non-fatal

    def _load_config(self, gate_num: int) -> GateConfig:
        """Load the YAML configuration for a specific gate."""
        import yaml  # type: ignore[import-untyped]
        names = {1: "gate1_per_fr.yaml", 2: "gate2_p3_exit.yaml",
                 3: "gate3_p4_exit.yaml", 4: "gate4_p6_full.yaml"}
        config_path = Path(__file__).parent / "gate_configs" / names[gate_num]
        with open(config_path, "r", encoding="utf-8") as f:
            raw = yaml.safe_load(f)
        return GateConfig.from_dict(raw, gate_num)

    def _update_quality_manifest(
        self, gate_num: int, fr_id: str | None, result: GateResult,
        da_waivers: "set[str] | None" = None,
    ) -> None:
        """Update the persistent manifest with latest gate results."""
        p = Path(".methodology/quality_manifest.json")
        if not p.exists():
            return
        manifest = json.loads(p.read_text(encoding="utf-8"))
        key = f"gate{gate_num}"
        payload: dict[str, Any] = {
            "score": result.score, "quality_complete": result.quality_complete,
            "rounds_used": result.rounds_used, "open_critical": result.open_critical,
            "open_high": result.open_high,
        }
        if da_waivers:
            # A DA waiver bypassed a dimension's score threshold (e.g. architecture=0
            # for an intentional Orchestrator pattern). Record it for human review —
            # it overrides a framework-owned score, so it must not pass silently.
            payload["da_waiver_applied"] = sorted(da_waivers)
            payload["da_waiver_needs_human_review"] = True
        if fr_id:
            if not isinstance(manifest["gate_results"][key], dict):
                manifest["gate_results"][key] = {}
            manifest["gate_results"][key][fr_id] = payload
        else:
            manifest["gate_results"][key] = payload
        p.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
