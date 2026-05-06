#!/usr/bin/env python3
"""
build_traceability.py — ASPICE Full Traceability Matrix Builder
================================================================
Populates RequirementTraceability model from live artifacts (SAD.md,
Python [FR-XX] annotations, test files) and auto-generates
TRACEABILITY_MATRIX.md with ASPICE SWE.3 compliance reporting.

Usage:
    python3 scripts/build_traceability.py --project . [--sad SAD.md]
    python3 scripts/build_traceability.py --project . --format aspice --export report.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Set

# Ensure harness-methodology root is on path for core imports
_SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPT_DIR.parent))

from core.requirement_traceability import (  # noqa: E402
    RequirementTraceability,
    TraceStatus,
)


FR_TAG_PATTERN = re.compile(r'\[FR-(\d+)\]', re.IGNORECASE)
FR_SAD_PATTERN = re.compile(r'\bFR-(\d+)\b', re.IGNORECASE)


def _norm_fr(num_str: str) -> str:
    """Normalize FR number to 2-digit zero-padded format."""
    return f"FR-{int(num_str):02d}"


# ---------------------------------------------------------------------------
# Scanners
# ---------------------------------------------------------------------------

def extract_fr_ids_from_sad(sad_path: Path) -> List[str]:
    """Extract all unique FR-XX IDs from SAD.md, zero-padded to 2 digits."""
    if not sad_path.exists():
        return []
    text = sad_path.read_text(encoding="utf-8", errors="replace")
    ids = {_norm_fr(m) for m in FR_SAD_PATTERN.findall(text)}
    return sorted(ids)


def scan_python_fr_annotations(project: Path) -> Dict[str, List[str]]:
    """Scan all .py files for [FR-XX] annotations. Returns {FR-XX: [file_path]}."""
    fr_to_files: Dict[str, List[str]] = {}
    for py_file in project.rglob("*.py"):
        if _skip_path(py_file):
            continue
        try:
            text = py_file.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        found = {_norm_fr(m) for m in FR_TAG_PATTERN.findall(text)}
        rel = str(py_file.relative_to(project))
        for fr_id in found:
            fr_to_files.setdefault(fr_id, []).append(rel)
    return fr_to_files


def scan_test_fr_coverage(tests_dir: Path) -> Dict[str, List[str]]:
    """Scan test files for FR references. Returns {FR-XX: [test_file]}."""
    fr_to_tests: Dict[str, List[str]] = {}
    if not tests_dir.is_dir():
        return fr_to_tests
    for test_file in tests_dir.rglob("test_*.py"):
        # FR from filename: test_fr_01.py → FR-01
        name_match = re.match(r'test_fr_(\d+)', test_file.name)
        if name_match:
            fr_id = _norm_fr(name_match.group(1))
            rel = str(test_file.relative_to(tests_dir.parent))
            fr_to_tests.setdefault(fr_id, []).append(rel)
        # FR from content
        try:
            text = test_file.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for m in FR_TAG_PATTERN.finditer(text):
            fr_id = _norm_fr(m.group(1))
            rel = str(test_file.relative_to(tests_dir.parent))
            if rel not in fr_to_tests.get(fr_id, []):
                fr_to_tests.setdefault(fr_id, []).append(rel)
    return fr_to_tests


def scan_sad_fr_modules(sad_path: Path) -> Dict[str, List[str]]:
    """Extract FR→module mappings from SAD.md component table rows.

    Matches patterns like:
      | `module.py` | FR-01 | ...
      FR-01 → `module.py`
    """
    fr_to_modules: Dict[str, List[str]] = {}
    if not sad_path.exists():
        return fr_to_modules
    text = sad_path.read_text(encoding="utf-8", errors="replace")
    # Pattern: FR-XX followed by backtick-quoted .py file on same line
    for m in re.finditer(r'FR-(\d+)[^\n]*?`([^`]+\.py)`', text):
        fr_id = _norm_fr(m.group(1))
        module = m.group(2)
        if module not in fr_to_modules.get(fr_id, []):
            fr_to_modules.setdefault(fr_id, []).append(module)
    return fr_to_modules


def _skip_path(p: Path) -> bool:
    """Exclude virtualenvs, caches, and harness internals."""
    skip_tokens = {"venv", "__pycache__", ".sessi-work", ".methodology",
                   ".git", "node_modules", ".mypy_cache", ".pytest_cache",
                   ".ruff_cache", "dist", "build", "*.egg-info"}
    parts = set(p.parts)
    return bool(parts & skip_tokens)


# ---------------------------------------------------------------------------
# Matrix builder
# ---------------------------------------------------------------------------

def build_traceability(
    project: Path,
    sad_path: Optional[Path] = None,
) -> RequirementTraceability:
    """Populate a RequirementTraceability model from live project artifacts."""
    project_id = project.resolve().name
    rt = RequirementTraceability(project_id=project_id)

    if sad_path is None:
        sad_path = project / "SAD.md"
        if not sad_path.exists():
            sad_path = project / "02-architecture" / "SAD.md"

    # 1. Extract FRs from SAD.md (source of truth)
    sad_frs = extract_fr_ids_from_sad(sad_path)
    sad_modules = scan_sad_fr_modules(sad_path)

    # 2. Scan code for [FR-XX] annotations
    code_fr_map = scan_python_fr_annotations(project)

    # 3. Scan tests for FR coverage
    tests_dir = project / "tests"
    test_fr_map = scan_test_fr_coverage(tests_dir)

    # 4. Merge all FR IDs
    all_frs: Set[str] = set(sad_frs)
    all_frs.update(code_fr_map.keys())
    all_frs.update(test_fr_map.keys())
    all_frs.update(sad_modules.keys())

    # 5. Populate model
    for fr_id in sorted(all_frs):
        srs_section = "SAD.md" if fr_id in sad_frs else None

        # Determine status from coverage completeness
        has_code = fr_id in code_fr_map
        has_test = fr_id in test_fr_map
        has_module = fr_id in sad_modules
        if has_code and has_test:
            status = TraceStatus.VERIFIED
        elif has_code or has_module:
            status = TraceStatus.IN_PROGRESS
        elif fr_id in sad_frs:
            status = TraceStatus.PENDING
        else:
            status = TraceStatus.NOT_IMPLEMENTED

        rt.add_requirement(
            req_id=fr_id,
            title=f"Requirement {fr_id}",
            srs_section=srs_section,
            description="",
            priority="HIGH",
            metadata={
                "sad_mapped": fr_id in sad_frs,
                "code_files": code_fr_map.get(fr_id, []),
                "test_files": test_fr_map.get(fr_id, []),
                "sad_modules": sad_modules.get(fr_id, []),
            },
        )
        rt.requirements[fr_id].status = status

        # Add code components
        for file_path in code_fr_map.get(fr_id, []):
            rt.add_code_component(file_path=file_path, fr_id=fr_id)

        # Add test coverage
        for test_file in test_fr_map.get(fr_id, []):
            rt.add_test_coverage(test_file=test_file, fr_id=fr_id)

    return rt


# ---------------------------------------------------------------------------
# Report generators
# ---------------------------------------------------------------------------

def generate_markdown_matrix(rt: RequirementTraceability, output_path: Path) -> None:
    """Generate TRACEABILITY_MATRIX.md from the traceability model."""
    completeness = rt.verify_completeness()
    lines = [
        "# Traceability Matrix",
        "",
        "> Auto-generated by `scripts/build_traceability.py`",
        f"> Project: `{rt.project_id}`",
        "",
        "## ASPICE Compliance Summary",
        "",
        "| Metric | Value | Target |",
        "|--------|-------|--------|",
        f"| Total Requirements | {completeness['total_requirements']} | — |",
        f"| SRS Coverage | {completeness['srs_coverage']} | 100% |",
        f"| Code Coverage | {completeness['code_coverage']} | 100% |",
        f"| Test Coverage | {completeness['test_coverage']} | 100% |",
        f"| Verification Rate | {completeness['verification_rate']} | 100% |",
        f"| Total Links | {completeness['total_links']} | — |",
        "",
    ]

    # ASPICE SWE.3 compliance
    aspice = completeness.get("missing_mappings", {})
    frs_srs = set(rt.requirements.keys()) - set(aspice.get("fr_without_srs", []))
    frs_code = set(rt.requirements.keys()) - set(aspice.get("fr_without_code", []))
    frs_test = set(rt.requirements.keys()) - set(aspice.get("fr_without_test", []))
    total = max(len(rt.requirements), 1)
    lines.extend([
        "### ASPICE SWE.3 Compliance",
        "",
        "| Practice | Status | Coverage |",
        "|----------|--------|----------|",
        f"| SWE.3 BP1: FR→SRS | {'PASS' if len(frs_srs) == total else 'FAIL'} | {len(frs_srs)}/{total} |",
        f"| SWE.3 BP2: SRS→Code | {'PASS' if len(frs_code) == total else 'FAIL'} | {len(frs_code)}/{total} |",
        f"| SWE.3 BP3: Code→Test | {'PASS' if len(frs_test) == total else 'FAIL'} | {len(frs_test)}/{total} |",
        "",
    ])

    # Detailed matrix
    lines.extend([
        "## Detailed Traceability Matrix",
        "",
        "| Requirement | Status | Code Files | Test Files | SAD Module |",
        "|-------------|--------|------------|------------|------------|",
    ])

    for rid, req in sorted(rt.requirements.items()):
        meta = req.metadata
        code_files = meta.get("code_files", [])
        test_files = meta.get("test_files", [])
        sad_mods = meta.get("sad_modules", [])
        code_str = ", ".join(code_files[:3]) or "—"
        test_str = ", ".join(test_files[:3]) or "—"
        sad_str = ", ".join(sad_mods[:3]) or "—"
        if len(code_files) > 3:
            code_str += f" (+{len(code_files)-3})"
        if len(test_files) > 3:
            test_str += f" (+{len(test_files)-3})"
        lines.append(
            f"| {rid} | {req.status.value} | {code_str} | {test_str} | {sad_str} |"
        )

    # Missing mappings
    missing = completeness.get("missing_mappings", {})
    if any(missing.values()):
        lines.extend([
            "",
            "## Gaps",
            "",
        ])
        for label, ids in [
            ("FR without SRS mapping", missing.get("fr_without_srs", [])),
            ("FR without Code", missing.get("fr_without_code", [])),
            ("FR without Test", missing.get("fr_without_test", [])),
        ]:
            if ids:
                lines.append(f"- **{label}**: {', '.join(sorted(ids))}")

    lines.append("")
    output_path.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="ASPICE Full Traceability Matrix Builder"
    )
    parser.add_argument("--project", required=True, help="Project root path")
    parser.add_argument("--sad", default=None, help="Path to SAD.md (default: <project>/SAD.md)")
    parser.add_argument("--output-matrix", default=None,
                        help="Output path for TRACEABILITY_MATRIX.md")
    parser.add_argument("--export", default=None, help="Export JSON report to file")
    parser.add_argument("--format", default="standard",
                        choices=["standard", "aspice"],
                        help="Export format (default: standard)")
    parser.add_argument("--json", action="store_true",
                        help="Print completeness report as JSON to stdout")
    args = parser.parse_args(argv)

    project = Path(args.project).resolve()
    if not project.is_dir():
        print(f"ERROR: project directory not found: {project}", file=sys.stderr)
        return 2

    sad_path = Path(args.sad) if args.sad else None
    rt = build_traceability(project, sad_path=sad_path)

    # JSON stdout
    if args.json:
        report = rt.export_report(format=args.format)
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0

    # Export JSON
    if args.export:
        rt.save(args.export)
        print(f"Report saved to {args.export}")

    # Generate TRACEABILITY_MATRIX.md
    matrix_path = Path(args.output_matrix) if args.output_matrix else project / "TRACEABILITY_MATRIX.md"
    generate_markdown_matrix(rt, matrix_path)
    print(f"Traceability matrix written to {matrix_path}")

    # Print summary
    c = rt.verify_completeness()
    print("\nASPICE Compliance Summary:")
    print(f"  Requirements: {c['total_requirements']}")
    print(f"  SRS Coverage:  {c['srs_coverage']}")
    print(f"  Code Coverage: {c['code_coverage']}")
    print(f"  Test Coverage: {c['test_coverage']}")
    print(f"  Total Links:   {c['total_links']}")

    missing = c.get("missing_mappings", {})
    if missing.get("fr_without_code"):
        print(f"  FRs without code: {missing['fr_without_code']}")
    if missing.get("fr_without_test"):
        print(f"  FRs without test: {missing['fr_without_test']}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
