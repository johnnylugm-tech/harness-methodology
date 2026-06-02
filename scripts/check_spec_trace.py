#!/usr/bin/env python3
"""
check_spec_trace.py — FR Spec Trace Validator (Content-Level)
===============================================================
Validates bidirectional FR→code→test traceability using the
RequirementTraceability model populated from live artifacts.

Upgraded from file-existence-only check (v1) to content-level
verification (v2) using [FR-XX] annotations in source code.

PR 1 of the closed-loop traceability plan: the scan logic now lives in
`core.traceability.scanner`; this script is a thin CLI wrapper around
`scanner.check_traceability`. Tests that import `check_traceability` from
this module continue to work via re-export.

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
import sys
from pathlib import Path
from typing import List, Optional

_SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPT_DIR.parent))

# Re-export for backward compatibility with tests and external callers
# (e.g. `from scripts.check_spec_trace import check_traceability`).
from core.traceability.scanner import check_traceability  # noqa: E402,F401


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


if __name__ == "__main__":
    sys.exit(main())
