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
    """Find the highest-numbered gate result file in .sessi-work/ or .methodology/."""
    search_dirs = [project / ".sessi-work", project / ".methodology"]
    
    for gate_num in (4, 3, 2, 1):
        for d in search_dirs:
            if not d.is_dir():
                continue
            result_path = d / f"gate{gate_num}_result.json"
            data = _load_json(result_path)
            if data:
                return gate_num, data
    return 0, {}


def _build_dimension_table(gate_result: dict[str, Any]) -> list[str]:
    """Build the 12-dimension score table markdown."""
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

    lines = [
        "| Dimension | Score | Status | Detail |",
        "|-----------|-------|--------|--------|",
    ]

    for kid, label in items:
        entry = dims.get(kid, {})
        score = entry.get("score", 0) if isinstance(entry, dict) else 0
        detail = entry.get("detail", "") if isinstance(entry, dict) else ""
        if not detail and isinstance(entry, dict):
            detail = entry.get("evidence", "")
        passed = score >= 70  # default threshold
        status = "✓ PASS" if passed else "✗ FAIL"
        lines.append(f"| {label} | {score}/100 | {status} | {detail} |")
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
        score = result.get("score", "N/A") if isinstance(result, dict) else "N/A"
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


def generate_quality_report(project_root: str,
                            output_path: str | None = None) -> str:
    """Generate the QUALITY_REPORT.md content and write to disk."""
    project = Path(project_root).resolve()
    manifest = _load_json(project / ".methodology" / "quality_manifest.json")
    gate_num, gate_result = _find_latest_gate_result(project)

    overall_score = gate_result.get("composite_score", 0) or gate_result.get("score", 0) or gate_result.get("total_score", 0)

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
    lines.extend(_build_dimension_table(gate_result))

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
    out = Path(output_path) if output_path else project / "06-quality" / "QUALITY_REPORT.md"
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
