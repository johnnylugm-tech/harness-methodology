"""CRG MCP enrichment: what the graph knows, appended to the gate's findings.

Round 81 站3. Moved out of harness/harness_bridge.py verbatim; the body here is
byte-identical to the one that was there, which
tests/test_god_file_split_safety.py asserts by AST source segment.

Round 80 recorded this function as not-moving because its closure pulled in
`_atomic_write_gate_result`, the writer eight call sites in harness_bridge
share, and moving THAT looked like a refactor that would leave
`gate_crg` importing `harness_bridge` and `harness_bridge` importing
`gate_crg`. The re-open condition it wrote was "共用寫入器先被移到中立模組".
Round 81 站2 did exactly that, in twelve lines, because the writer turned out
to be a leaf. This is the second half.

Measured before the move: the only names this function needs at run time are
`json`, `Path`, `dataclasses` and `_atomic_write_gate_result`. `DimResult` and
`CRGBridge` appear only inside string annotations — the rebuilds all go through
`dataclasses.replace`, which takes the instance and never names the type — so
they are imported under TYPE_CHECKING, the same treatment Round 80 站8 gave
`GateContext`. The import therefore goes one way and there is no cycle.

harness_bridge re-exports the name: its own call site inside `finalize_gate`
resolves through the module globals, and four tests patch
`harness.harness_bridge._crg_enrich_gate_findings` — both keep working through
the re-export, and tests/test_god_file_split_safety.py fails loudly if it is
ever dropped.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - annotations only
    # Both names appear in this module's annotations only. Importing them at
    # run time would point harness.gate_crg back at harness.harness_bridge,
    # which is the cycle Round 80 declined to create.
    from harness.crg_bridge import CRGBridge  # noqa: F401
    from harness.harness_bridge import DimResult  # noqa: F401

from harness.gate_io import _atomic_write_gate_result


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
