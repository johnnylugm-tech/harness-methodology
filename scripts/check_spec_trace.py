#!/usr/bin/env python3
"""
check_spec_trace.py — FR Spec Trace Validator (Content-Level)
===============================================================
Validates bidirectional FR→code→test traceability using the
RequirementTraceability model populated from live artifacts.

Upgraded from file-existence-only check (v1) to content-level
verification (v2) using [FR-XX] annotations in source code.

Usage:
    python3 scripts/check_spec_trace.py --project . [--sad SAD.md]
    python3 scripts/check_spec_trace.py --project . --block  # exit 1 on gaps

Exit codes:
    0 — all FRs fully traced (code + test)
    1 — untraced FRs found
    2 — usage error
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

_SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPT_DIR.parent))

from core.requirement_traceability import (  # noqa: E402
    RequirementTraceability,
    TraceStatus,
)


FR_TAG_PATTERN = re.compile(r'\[FR-(\d+)\]', re.IGNORECASE)
FR_SAD_PATTERN = re.compile(r'\bFR-(\d+)\b', re.IGNORECASE)


def _norm_fr(num_str: str) -> str:
    return f"FR-{int(num_str):02d}"


# ---------------------------------------------------------------------------
# Core check logic
# ---------------------------------------------------------------------------

def check_traceability(
    project: Path,
    sad_path: Optional[Path] = None,
) -> Tuple[RequirementTraceability, Dict]:
    """
    Run content-level traceability check.

    Returns (model, report) where report keys:
      - fr_ids: all FRs from SAD.md
      - tested: FRs with test coverage (content-verified)
      - coded: FRs with code annotations
      - untested: FRs without test coverage
      - uncoded: FRs without code annotations
      - complete: whether all FRs are fully traced
    """
    if sad_path is None:
        sad_path = _find_sad(project)

    # Extract FRs from SAD.md
    sad_frs: List[str] = []
    if sad_path and sad_path.exists():
        text = sad_path.read_text(encoding="utf-8", errors="replace")
        sad_frs = sorted({_norm_fr(m) for m in FR_SAD_PATTERN.findall(text)})

    # Scan all Python source for [FR-XX] annotations
    coded: Set[str] = set()
    fr_to_code: Dict[str, List[str]] = {}
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
            coded.add(fr_id)
            fr_to_code.setdefault(fr_id, []).append(rel)

    # Scan test files for FR coverage (content-level)
    tests_dir = project / "tests"
    tested: Set[str] = set()
    fr_to_tests: Dict[str, List[str]] = {}
    if tests_dir.is_dir():
        for test_file in tests_dir.rglob("test_*.py"):
            name_match = re.match(r'test_fr_(\d+)', test_file.name)
            if name_match:
                fr_id = _norm_fr(name_match.group(1))
                tested.add(fr_id)
                rel = str(test_file.relative_to(project))
                fr_to_tests.setdefault(fr_id, []).append(rel)
            try:
                text = test_file.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            for m in FR_TAG_PATTERN.finditer(text):
                fr_id = _norm_fr(m.group(1))
                tested.add(fr_id)
                rel = str(test_file.relative_to(project))
                if rel not in fr_to_tests.get(fr_id, []):
                    fr_to_tests.setdefault(fr_id, []).append(rel)

    all_frs = sorted(set(sad_frs) | coded | tested)
    ghost_frs = sorted((coded | tested) - set(sad_frs))
    # Only SAD-defined FRs are authoritative for traceability gaps
    untested = [fr for fr in sad_frs if fr not in tested]
    uncoded = [fr for fr in sad_frs if fr not in coded]

    # Populate the traceability model
    rt = RequirementTraceability(project_id=project.resolve().name)
    for fr_id in all_frs:
        has_code = fr_id in coded
        has_test = fr_id in tested
        if has_code and has_test:
            status = TraceStatus.VERIFIED
        elif has_code:
            status = TraceStatus.IN_PROGRESS
        elif fr_id in sad_frs:
            status = TraceStatus.PENDING
        else:
            status = TraceStatus.NOT_IMPLEMENTED

        srs_section = "SAD.md" if fr_id in sad_frs else None
        rt.add_requirement(
            req_id=fr_id,
            title=f"Requirement {fr_id}",
            srs_section=srs_section,
            description="",
            priority="HIGH",
            metadata={
                "code_files": fr_to_code.get(fr_id, []),
                "test_files": fr_to_tests.get(fr_id, []),
            },
        )
        rt.requirements[fr_id].status = status

        for fp in fr_to_code.get(fr_id, []):
            rt.add_code_component(file_path=fp, fr_id=fr_id)
        for tf in fr_to_tests.get(fr_id, []):
            rt.add_test_coverage(test_file=tf, fr_id=fr_id)

    report = {
        "total": len(all_frs),
        "sad_frs": len(sad_frs),
        "coded": len(coded),
        "tested": len(tested),
        "untested": untested,
        "uncoded": uncoded,
        "ghost_frs": ghost_frs,
        "complete": len(untested) == 0 and len(uncoded) == 0,
        "completeness": rt.verify_completeness(),
    }
    return rt, report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="FR Spec Trace Validator (content-level)"
    )
    parser.add_argument("--project", required=True, help="Project root path")
    parser.add_argument("--sad", default=None, help="Path to SAD.md")
    parser.add_argument("--block", action="store_true",
                        help="Exit 1 if untraced FRs found (for CI/gate use)")
    parser.add_argument("--json", action="store_true",
                        help="Output report as JSON")
    parser.add_argument("--export", default=None,
                        help="Export full traceability report JSON")
    args = parser.parse_args(argv)

    project = Path(args.project).resolve()
    if not project.is_dir():
        print(f"ERROR: not a directory: {project}", file=sys.stderr)
        return 2

    sad_path = Path(args.sad) if args.sad else None
    rt, report = check_traceability(project, sad_path=sad_path)

    if args.export:
        rt.save(args.export)
        print(f"Report saved to {args.export}", file=sys.stderr)

    if args.json:
        import json
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0 if not args.block or report["complete"] else 1

    # Human-readable output
    print(f"FRs in SAD.md: {report['sad_frs']} | "
          f"Coded: {report['coded']} | Tested: {report['tested']} | "
          f"Total: {report['total']}")
    print(f"Completeness: {report['completeness']['code_coverage']} code, "
          f"{report['completeness']['test_coverage']} test")

    if report["untested"]:
        print("\nUNTESTED FRs (no test file or content reference):")
        for fr in report["untested"]:
            safe_id = fr.lower().replace('-', '_')
            print(f"  {fr}  ->  create tests/test_{safe_id}.py")
    if report["uncoded"]:
        print("\nUNCODED FRs (no [FR-XX] annotation in source):")
        for fr in report["uncoded"]:
            print(f"  {fr}  ->  add [FR-XX] docstring annotation to implementation file")
    if report["ghost_frs"]:
        print("\nGHOST FRs (referenced in code/tests but not defined in SAD.md):")
        for fr in report["ghost_frs"]:
            print(f"  {fr}  ->  remove [FR-XX] annotation or add to SAD.md (non-blocking)")

    if report["complete"]:
        msg = "\nAll SAD FRs fully traced (code + test) — Gate 3 spec_trace_coverage = 100%"
        if report["ghost_frs"]:
            msg += f"\n  ({len(report['ghost_frs'])} ghost FR(s) present — cleanup recommended, not blocking)"
        print(msg)
        return 0
    else:
        outstanding = len(report["untested"]) + len(report["uncoded"])
        print(f"\nGate 3 BLOCKED until {outstanding} gap(s) resolved.")
        return 1 if args.block else 0


def _find_sad(project: Path) -> Optional[Path]:
    for c in ["02-architecture/SAD.md"]:
        p = project / c
        if p.exists():
            return p
    return None


def _skip_path(p: Path) -> bool:
    skip_tokens = {"venv", "__pycache__", ".sessi-work", ".methodology",
                   ".git", "node_modules", ".mypy_cache", ".pytest_cache",
                   ".ruff_cache", "dist", "build"}
    parts = set(p.parts)
    if parts & skip_tokens:
        return True
    return any(part.endswith(".egg-info") for part in p.parts)


if __name__ == "__main__":
    sys.exit(main())
