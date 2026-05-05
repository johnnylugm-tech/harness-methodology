#!/usr/bin/env python3
"""
check_spec_trace.py — FR Spec Trace Validator
===============================================
Validates that every FR-XXX ID found in SAD.md has a corresponding
test file in the target project's tests/ directory.

Called at P4 Gate 3 entry by HarnessBridge.run_gate(gate_num=3) before
invoking the SSI runner. Exit 1 raises GateBlockedError unconditionally.

Usage:
    python3 harness/scripts/check_spec_trace.py <SAD.md> <tests_dir>

Exit codes:
    0 — all FRs traced to tests/ (Gate 3 may proceed)
    1 — untested FRs found    (Gate 3 blocked)
    2 — usage error
"""

import re
import sys
from pathlib import Path
from typing import Optional, List


def extract_fr_ids(sad_path: str) -> list[str]:
    """Extract all unique FR-XXX IDs from SAD.md."""
    text = Path(sad_path).read_text(encoding="utf-8")
    return sorted(set(re.findall(r'\bFR-\d+\b', text)))


def find_tested_frs(tests_dir: str) -> set[str]:
    """
    Scan tests/test_fr_*.py files for FR-XXX references in content.
    Also matches FR IDs embedded in the file name itself.
    """
    tested: set[str] = set()
    for test_file in Path(tests_dir).glob("test_fr_*.py"):
        # FR IDs from filename (e.g. test_fr_001.py → FR-001)
        name_ids = re.findall(r'test_fr_(\d+)', test_file.name)
        for nid in name_ids:
            tested.add(f"FR-{nid.lstrip('0') or '0'}")
            tested.add(f"FR-{nid}")  # both zero-padded and plain
        # FR IDs from file content
        content_ids = re.findall(r'\bFR-\d+\b', test_file.read_text(encoding="utf-8"))
        tested.update(content_ids)
    return tested



def main(argv: Optional[List[str]] = None) -> int:
    """CLI entry point."""
    args = argv or sys.argv[1:]
    if len(args) < 2:
        print("Usage: check_spec_trace.py <SAD.md> <tests_dir>")
        return 2

    sad_path, tests_dir = args[0], args[1]

    if not Path(sad_path).exists():
        print(f"ERROR: SAD.md not found at {sad_path}")
        return 2
    if not Path(tests_dir).is_dir():
        print(f"ERROR: tests_dir not found at {tests_dir}")
        return 2

    fr_ids = extract_fr_ids(sad_path)
    if not fr_ids:
        print("WARNING: No FR-XXX IDs found in SAD.md — nothing to trace.")
        return 0

    tested = find_tested_frs(tests_dir)
    untested = [fr for fr in fr_ids if fr not in tested]

    print(f"FRs in SAD.md: {len(fr_ids)} | Tested: {len(fr_ids) - len(untested)} | Untested: {len(untested)}")

    if untested:
        print("\n❌ UNTESTED FRs (missing test files):")
        for fr in untested:
            safe_id = fr.lower().replace('-', '_')
            print(f"  {fr}  →  create tests/test_{safe_id}.py")
        print(f"\nGate 3 BLOCKED until all {len(untested)} FR(s) have corresponding test files.")
        return 1

    print("✅ All FRs traced to test files — Gate 3 spec_trace_coverage = 100%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
