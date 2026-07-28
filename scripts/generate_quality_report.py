#!/usr/bin/env python3
"""
Generate QUALITY_REPORT.md from quality_manifest.json and the latest gate result.

Usage:
    python3 scripts/generate_quality_report.py --project /path/to/project
    python3 scripts/generate_quality_report.py --project . --output QUALITY_REPORT.md

Output:
    Creates/updates 06-quality/QUALITY_REPORT.md with:
    - 12-dimension score table (from latest gate result)
    - Per-FR Gate 1 score summary
    - Defect statistics
    - ASPICE traceability references
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

_SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPT_DIR.parent))

from core.utils.project_layout import ProjectLayout  # noqa: E402

# ── 12 Dimension labels (Gate 3/4) ─────────────────────────────────────
DIMENSIONS_12: list[dict[str, str]] = [
    {"id": "completeness",    "label": "Completeness"},
    {"id": "correctness",     "label": "Correctness"},
    {"id": "consistency",     "label": "Consistency"},
    {"id": "clarity",         "label": "Clarity"},
    {"id": "test_coverage",   "label": "Test Coverage"},
    {"id": "maintainability", "label": "Maintainability"},
    {"id": "reliability",     "label": "Reliability"},
    {"id": "performance",     "label": "Performance"},
    {"id": "security",        "label": "Security"},
    {"id": "traceability",    "label": "Traceability"},
    {"id": "integrity",       "label": "Integrity"},
    {"id": "phase_truth",     "label": "Phase Truth"},
]


def _load_json(path: Path) -> dict[str, Any]:
    """Load a JSON file, returning {} on failure."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _find_latest_gate_result(project: Path) -> tuple[int, dict[str, Any]]:
    """Find the highest-numbered gate result file in .methodology/ or .sessi-work/.

    Round 24 站2b: the search order lives in
    core.quality_gate.quality_report_verify.find_latest_gate_result, because the
    verifier that checks this report has to read the SAME authority the renderer
    read — two copies of "which gate result wins" would make the guard argue with
    the renderer instead of with the report. Kept as a thin alias so this module's
    existing callers and tests are unaffected.
    """
    from core.quality_gate.quality_report_verify import find_latest_gate_result

    return find_latest_gate_result(project)


def _build_dimension_table(gate_result: dict[str, Any],
                           validated_waivers: set[str] | None = None) -> list[str]:
    """Build the 12-dimension score table markdown.

    validated_waivers: dimension ids with a harness-verified DA waiver, from
    quality_manifest.json's gate_results.gate{N}.da_waiver_applied — the field
    _update_quality_manifest() writes only after an actual DA-challenge (see
    harness/harness_bridge.py). gate_result["da_waiver"] is NOT used for this:
    it is the agent's own unpatched self-assessment in gate{N}_result.json (only
    composite_score/quality_complete/verdict/passed get harness-recomputed on
    finalize-gate — da_waiver passes through verbatim), so trusting it here would
    let an agent write da_waiver: {"security": true} into its own gate result and
    have a real 0-score failure render as PASS.
    """
    dims = {}
    items = []

    if "breakdown" in gate_result:
        dims = gate_result["breakdown"]
        items = [(k, k.replace("_", " ").title()) for k in dims.keys()]
    elif "dimensions" in gate_result:
        dims = gate_result["dimensions"]
        items = [(d["id"], d["label"]) for d in DIMENSIONS_12]
    else:
        # Try alternate schema: flat score key
        scores = gate_result.get("scores", {})
        dims = {d["id"]: {"score": scores.get(d["id"], 0), "detail": ""}
                for d in DIMENSIONS_12}
        items = [(d["id"], d["label"]) for d in DIMENSIONS_12]

    waivers = validated_waivers or set()

    lines = [
        "| Dimension | Score | Status | Detail |",
        "|-----------|-------|--------|--------|",
    ]

    for kid, label in items:
        entry = dims.get(kid, {})
        # entry.get("score", 0) would NOT catch a present-but-null score — .get's
        # default only applies when the key is absent. Per
        # harness/ssi/schemas/harness_gate_result.schema.json, score: null is a
        # documented state (framework-owned dimension, e.g. architecture pre-patch;
        # or no measurement applies, e.g. pytest-benchmark with no benchmarks; or
        # excluded via harness_config.json feature flag). Readers MUST handle it
        # explicitly instead of comparing None >= 70 (TypeError) or defaulting to 0
        # (renders a disabled/inapplicable dimension as a false FAIL).
        score = entry.get("score") if isinstance(entry, dict) else None
        detail = entry.get("detail", "") if isinstance(entry, dict) else ""
        if not detail and isinstance(entry, dict):
            detail = entry.get("evidence", "")
        # A Devil's-Advocate waiver is the authoritative verdict for a dimension
        # whose raw tool score is below threshold (e.g. CRG community-cohesion for
        # an intentional star-topology). Show the raw score honestly but mark it
        # PASS (DA-waiver) rather than a bare FAIL.
        if kid in waivers:
            status = "✓ PASS (DA-waiver)"
            score_display = f"{score}/100" if score is not None else "N/A"
        elif score is None:
            excluded = isinstance(entry, dict) and entry.get("excluded_by_feature_flag")
            status = "⊘ EXCLUDED" if excluded else "⊘ FRAMEWORK-OWNED"
            score_display = "N/A"
        else:
            status = "✓ PASS" if score >= 70 else "✗ FAIL"
            score_display = f"{score}/100"
        lines.append(f"| {label} | {score_display} | {status} | {detail} |")
    return lines


def _build_fr_summary(quality_manifest: dict[str, Any]) -> list[str]:
    """Build per-FR Gate 1 score summary."""
    gate1 = quality_manifest.get("gate_results", {}).get("gate1", {})
    if not gate1 or not isinstance(gate1, dict):
        return ["(No per-FR Gate 1 scores available)"]

    lines = [
        "| FR ID | Score | Status |",
        "|-------|-------|--------|",
    ]
    for fr_id, result in gate1.items():
        # Same null-vs-absent distinction as _build_dimension_table: .get(key, "N/A")
        # only substitutes when "score" is absent, not when present as None.
        raw_score = result.get("score") if isinstance(result, dict) else None
        score = raw_score if raw_score is not None else "N/A"
        passed = result.get("quality_complete", False) if isinstance(result, dict) else False
        status = "✓ PASS" if passed else "—"
        lines.append(f"| {fr_id} | {score} | {status} |")
    return lines


def _build_defect_summary(quality_manifest: dict[str, Any],
                          gate_result: dict[str, Any]) -> list[str]:
    """Extract defect/issue counts from manifest and gate result."""
    issues = gate_result.get("issues", [])
    critical = sum(1 for i in issues if isinstance(i, dict) and i.get("severity") == "critical")
    high = sum(1 for i in issues if isinstance(i, dict) and i.get("severity") == "high")
    medium = sum(1 for i in issues if isinstance(i, dict) and i.get("severity") == "medium")
    low = sum(1 for i in issues if isinstance(i, dict) and i.get("severity") == "low")
    return [
        f"- **Critical**: {critical}",
        f"- **High**: {high}",
        f"- **Medium**: {medium}",
        f"- **Low**: {low}",
    ]


def _crg_call(project: Path, func: str, **kwargs) -> dict:
    """Call a CRG tools function via the subprocess backend; {} on any failure.

    generate_quality_report runs both standalone and imported by harness_cli, so
    ensure the repo root (which contains the `harness` package) is importable.
    """
    try:
        import sys as _sys
        _repo_root = str(Path(__file__).resolve().parent.parent)
        if _repo_root not in _sys.path:
            _sys.path.insert(0, _repo_root)
        from harness.crg_api import call_crg_tool
        return call_crg_tool(str(project), func, **kwargs) or {}
    except Exception as exc:  # CRG absent / failed → informational, never blocking
        print(f"[WARN] CRG call {func} failed: {exc}")
        return {}


def _build_architecture_section(project: Path) -> list[str]:
    """CRG architecture overview — communities + cross-community coupling warnings.

    Explains *why* the architecture dimension scored as it did instead of just a
    bare number. Uses the crg_api subprocess backend (works outside Claude Code).
    """
    overview = _crg_call(project, "get_architecture_overview_func")
    if not overview or overview.get("status") != "ok":
        return ["_CRG architecture overview unavailable._", ""]

    out: list[str] = [overview.get("summary", ""), ""]
    communities = overview.get("communities", [])
    if communities:
        out.append("| Community | Size | Cohesion |")
        out.append("|---|---|---|")
        for c in sorted(communities, key=lambda x: x.get("size", 0), reverse=True)[:10]:
            out.append(
                f"| {c.get('name', '?')} | {c.get('size', '?')} "
                f"| {float(c.get('cohesion', 0) or 0):.2f} |"
            )
        out.append("")
    warnings = overview.get("warnings", [])
    if warnings:
        out.append("**Coupling / cohesion warnings:**")
        out.extend(f"- ⚠ {w}" for w in warnings)
        out.append("")
    return out


def _build_dead_code_section(project: Path) -> list[str]:
    """CRG dead-code candidates — advisory (framework callbacks may be false positives)."""
    result = _crg_call(project, "refactor_func", mode="dead_code")
    if not result or result.get("status") != "ok":
        return ["_Dead-code analysis unavailable._", ""]
    dead = result.get("dead_code", [])
    if not dead:
        return ["_No dead-code candidates found._", ""]
    out = [
        f"{result.get('summary', '')} "
        "_(advisory — verify before removing; framework callbacks / entrypoints "
        "can be false positives)_",
        "",
        "| Symbol | Kind | File |",
        "|---|---|---|",
    ]
    for d in dead[:20]:
        rel = d.get("relative_path") or d.get("file_path") or d.get("file", "?")
        out.append(f"| {d.get('name', '?')} | {d.get('kind', '?')} | `{rel}` |")
    out.append("")
    return out


def generate_quality_report(project_root: str,
                            output_path: str | None = None) -> str:
    """Generate the QUALITY_REPORT.md content and write to disk."""
    project = Path(project_root).resolve()
    manifest = _load_json(project / ".methodology" / "quality_manifest.json")
    gate_num, gate_result = _find_latest_gate_result(project)

    overall_score = gate_result.get("composite_score", 0) or gate_result.get("score", 0) or gate_result.get("total_score", 0)

    # Validated DA waivers (not the agent-self-written gate_result["da_waiver"] —
    # see _build_dimension_table docstring).
    _manifest_waivers = manifest.get("gate_results", {}).get(f"gate{gate_num}", {}).get("da_waiver_applied", [])
    validated_waivers = set(_manifest_waivers) if isinstance(_manifest_waivers, list) else set()

    lines: list[str] = [
        "# Quality Report",
        "",
        f"> **Generated**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"> **Gate**: {gate_num or 'N/A'}",
        f"> **Overall Score**: {overall_score}/100",
        "",
        "---",
        "",
        "## Assessment Dimensions",
        "",
    ]
    lines.extend(_build_dimension_table(gate_result, validated_waivers))

    lines.extend([
        "",
        "---",
        "",
        "## Per-FR Gate 1 Summary",
        "",
    ])
    lines.extend(_build_fr_summary(manifest))

    lines.extend([
        "",
        "---",
        "",
        "## Defect / Issue Summary",
        "",
    ])
    lines.extend(_build_defect_summary(manifest, gate_result))

    lines.extend([
        "",
        "---",
        "",
        "## Architecture (CRG)",
        "",
    ])
    lines.extend(_build_architecture_section(project))

    lines.extend([
        "",
        "### Dead Code Candidates (CRG)",
        "",
    ])
    lines.extend(_build_dead_code_section(project))

    lines.extend([
        "",
        "---",
        "",
        "## ASPICE Traceability",
        "",
        "- **BASELINE.md**: See `05-verification/BASELINE.md` for performance baseline",
        "- **VERIFICATION_REPORT.md**: See `05-verification/VERIFICATION_REPORT.md` for verification results",
        "",
        "---",
        "",
        "_Report auto-generated by harness-methodology/scripts/generate_quality_report.py_",
        "",
    ])

    content = "\n".join(lines)

    # Determine output path
    out = Path(output_path) if output_path else ProjectLayout(project).quality_report_path
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(content, encoding="utf-8")
    print(f"[QREPORT] Written → {out}  ({len(lines)} lines)")
    return str(out)


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate QUALITY_REPORT.md")
    parser.add_argument("--project", default=".", help="Project root (default: .)")
    parser.add_argument("--output", default=None, help="Output path (default: 06-quality/QUALITY_REPORT.md)")
    args = parser.parse_args()
    generate_quality_report(args.project, args.output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
