#!/usr/bin/env python3
"""
Path Consistency Verifier
=========================
Auto-verify Phase Plan doc paths match Framework Tool paths.

Usage:
    python3 scripts/verify_path_consistency.py

Exit codes:
    0 = All consistent
    1 = Inconsistencies found
    2 = Error
"""

import re
import sys
from pathlib import Path
from typing import Dict, Set

TOOL_FILES = [
    "core/quality_gate/phase_paths.py",
    "core/quality_gate/phase_artifact_enforcer.py",
    "core/quality_gate/phase_aware_constitution.py",
    "core/quality_gate/constitution/phase_prerequisite_checker.py",
    "core/quality_gate/constitution/__init__.py",
]

PHASE_WHERE_PATTERNS = {
    5: "05-verify",
    6: "06-quality",
    7: "07-risk",
    8: "08-config",
}

PHASE_ARTIFACTS = {
    5: ["05-verify/BASELINE.md", "05-verify/VERIFICATION_REPORT.md"],
    6: ["06-quality/QUALITY_REPORT.md", "06-quality/MONITORING_PLAN.md"],
    7: ["07-risk/RISK_ASSESSMENT.md", "07-risk/RISK_REGISTER.md"],
    8: ["08-config/CONFIG_RECORDS.md", "08-config/requirements.lock"],
}


def extract_paths_from_tool(filepath: str) -> Dict[int, Set[str]]:
    """Parse a tool output file for phase-specific path references."""
    paths: dict[int, set[str]] = {5: set(), 6: set(), 7: set(), 8: set()}
    try:
        content = Path(filepath).read_text(encoding="utf-8")
        for phase, pattern in PHASE_WHERE_PATTERNS.items():
            for m in re.findall(rf'"{pattern}/[^"]*"', content):
                paths[phase].add(m.strip('"'))
            for m in re.findall(rf'output_dir["\s:]+["\']?{pattern}["\']?', content):
                paths[phase].add(pattern)
    except Exception as e:
        print(f"WARNING: Error reading {filepath}: {e}")
    return paths


def extract_paths_from_plan(phase: int) -> Set[str]:
    """Extract WHERE field path from a phase plan document."""
    plan_file = Path(f"docs/Phase{phase}_Plan_5W1H_AB.md")
    if not plan_file.exists():
        return set()
    content = plan_file.read_text(encoding="utf-8")
    match = re.search(r'\*\*WHERE\*\*\s*\|\s*`([^`]+)`', content)
    return {match.group(1).strip().rstrip("/")} if match else set()


def main():
    """CLI entry point: verify path consistency between plans and tool config."""
    print("=" * 60)
    print("PATH CONSISTENCY VERIFIER")
    print("=" * 60)

    inconsistencies = []
    for phase in [5, 6, 7, 8]:
        print(f"\nPhase {phase}")
        print("-" * 40)
        plan_paths = extract_paths_from_plan(phase)
        plan_path_str = list(plan_paths)[0] if plan_paths else "NOT FOUND"
        print(f"  Plan WHERE: {plan_path_str}")

        all_tool_paths: Dict[int, Set[str]] = {5: set(), 6: set(), 7: set(), 8: set()}
        for tool_file in TOOL_FILES:
            if not Path(tool_file).exists():
                continue
            for p in [5, 6, 7, 8]:
                all_tool_paths[p].update(extract_paths_from_tool(tool_file)[p])

        tool_phase_paths = all_tool_paths.get(phase, set())
        if tool_phase_paths:
            print(f"  Tool paths: {tool_phase_paths}")

        plan_dir = plan_path_str.rstrip("/")
        if tool_phase_paths and plan_dir not in [p.rstrip("/") for p in tool_phase_paths]:
            inconsistencies.append({"phase": phase, "plan_path": plan_path_str, "tool_paths": tool_phase_paths})
            print("  STATUS: INCONSISTENT")
        else:
            print("  STATUS: CONSISTENT")

    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    if not inconsistencies:
        print("\nALL PATHS CONSISTENT")
        return 0
    print(f"\n{len(inconsistencies)} INCONSISTENCIES FOUND:")
    for inc in inconsistencies:
        print(f"  Phase {inc['phase']}: Plan={inc['plan_path']}  Tools={inc['tool_paths']}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
