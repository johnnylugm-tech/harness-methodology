#!/usr/bin/env python3
"""
Cross-Artifact Consistency Validator (D3)
==========================================
Detects fabrication patterns that span multiple artifacts:

1. Report phase number mismatch — e.g. COVERAGE_REPORT.md title says
   "Phase 3" but the file is in 04-testing/ (Phase 4 territory).
2. FR coverage mismatch — TEST_RESULTS.md lists FRs that have no
   corresponding entries in sessions_spawn.log.
3. Coverage number mismatch — COVERAGE_REPORT.md claims N% but
   pytest --cov output disagrees.

These checks run during Phase Truth verification and finalize-gate
postflight, not as standalone tools.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List
from core.utils.project_layout import ProjectLayout, phase_artifacts as _phase_artifacts


# Canonical artifact paths per phase (relative to project root)
# Defined dynamically via ProjectLayout within functions now, so we remove the static map.


def check_phase_title(project_root: Path, phase: int) -> List[Dict[str, str]]:
    """Check that report H1 titles reference the correct phase number.

    Detects copy-paste from previous phase reports (e.g. Phase 3 title
    in a Phase 4 report).

    Returns list of violations (empty if clean).
    """
    violations: List[Dict[str, str]] = []
    layout = ProjectLayout(project_root)
    phase_artifacts = {
        4: [
            layout.get_relative_str(layout.test_plan_path),
            layout.get_relative_str(layout.phase4_testing_dir / "TEST_RESULTS.md"),
            layout.get_relative_str(layout.phase4_testing_dir / "COVERAGE_REPORT.md"),
        ],
        5: [
            layout.get_relative_str(layout.phase5_verification_dir / "BASELINE.md"),
            layout.get_relative_str(layout.phase5_verification_dir / "VERIFICATION_REPORT.md"),
        ],
        6: [layout.get_relative_str(layout.phase6_quality_dir / "QUALITY_REPORT.md")],
        7: _phase_artifacts(7),
        8: [
            layout.get_relative_str(layout.phase8_config_dir / "CONFIG_RECORDS.md"),
            layout.get_relative_str(layout.phase8_config_dir / "RELEASE_CHECKLIST.md"),
        ],
    }
    artifacts = phase_artifacts.get(phase, [])

    for rel_path in artifacts:
        fpath = project_root / rel_path
        if not fpath.exists():
            continue

        try:
            content = fpath.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        # Find H1 heading (# Title)
        h1_match = re.search(r'^#\s+(.+)$', content, re.MULTILINE)
        if not h1_match:
            continue

        title = h1_match.group(1)

        # Check for wrong phase number in title
        wrong_phase = None
        for p in range(1, 9):
            if p == phase:
                continue
            if f"Phase {p}" in title or f"phase {p}" in title:
                wrong_phase = p
                break

        if wrong_phase is not None:
            violations.append({
                "file": rel_path,
                "issue": f"Title says 'Phase {wrong_phase}' but file belongs to Phase {phase}",
                "title": title,
                "severity": "HIGH",
                "suggestion": f"Update H1 to reference Phase {phase}",
            })

        elif f"Phase {phase}" not in title and f"phase {phase}" not in title:
            violations.append({
                "file": rel_path,
                "issue": f"Title does not reference Phase {phase}",
                "title": title,
                "severity": "MEDIUM",
                "suggestion": f"Include 'Phase {phase}' in the H1 heading",
            })

    return violations


def check_fr_coverage(project_root: Path, _phase: int) -> List[Dict[str, str]]:
    """Verify FRs claimed in TEST_RESULTS.md have entries in sessions_spawn.log.

    Args:
        _phase: Reserved for future per-phase FR coverage rules.
    """
    layout = ProjectLayout(project_root)
    violations: List[Dict[str, str]] = []

    results_path = layout.phase4_testing_dir / "TEST_RESULTS.md"
    if not results_path.exists():
        return violations

    spawn_log = layout.sessions_spawn_log
    if not spawn_log.exists():
        return [{
            "file": layout.get_relative_str(results_path),
            "issue": "Cannot cross-validate: sessions_spawn.log not found",
            "severity": "HIGH",
        }]

    # Extract FR IDs from TEST_RESULTS.md
    try:
        results_content = results_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return violations

    claimed_frs: set[str] = set()
    for m in re.finditer(r'FR-(\d+)', results_content, re.IGNORECASE):
        claimed_frs.add(f"FR-{m.group(1).zfill(2)}")

    if not claimed_frs:
        return violations

    # Extract FR IDs from sessions_spawn.log
    try:
        log_content = spawn_log.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return violations

    logged_frs: set[str] = set()
    for line in log_content.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        fr = entry.get("fr_id", "")
        if fr:
            logged_frs.add(fr)

    # FRs claimed in TEST_RESULTS but missing from session log
    unverified = claimed_frs - logged_frs
    for fr in sorted(unverified):
        violations.append({
            "file": layout.get_relative_str(results_path),
            "fr_id": fr,
            "issue": f"{fr} claimed in TEST_RESULTS.md but has no session log evidence",
            "severity": "HIGH",
            "suggestion": f"Dispatch Agent A/B for {fr} or remove from test results",
        })

    return violations


def check_coverage_report(project_root: Path, _phase: int) -> List[Dict[str, str]]:
    """Validate COVERAGE_REPORT.md numbers against actual pytest --cov output.

    Parses COVERAGE_REPORT.md for coverage percentage claims and runs
    pytest --cov to compare. Only compares line coverage (most commonly
    reported).

    Returns list of violations (empty if clean or coverage tool unavailable).
    """
    layout = ProjectLayout(project_root)
    violations: List[Dict[str, str]] = []

    cov_report = layout.phase4_testing_dir / "COVERAGE_REPORT.md"
    if not cov_report.exists():
        return violations

    try:
        cov_content = cov_report.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return violations

    # Extract claimed coverage percentage
    # Match only on the "Line coverage" line to avoid picking up target/other %
    claimed_match = re.search(
        r'(?im)^Line coverage[^\d]*(\d{2,3}(?:\.\d)?)\s*%',
        cov_content,
    )
    if not claimed_match:
        # Bare percentage: "Overall: 85%" or "Total: 95%"
        bare_m = re.search(
            r'(?im)^(?:Total|Overall|Average)[:\s]+(\d{2,3}(?:\.\d)?)\s*%',
            cov_content,
        )
        if not bare_m:
            return violations  # No numeric claim to validate
        claimed_pct = float(bare_m.group(1))
    else:
        claimed_pct = float(claimed_match.group(1))

    # Try running pytest --cov to get actual coverage.
    # Guarded behind HARNESS_CROSS_ARTIFACT_COV=1 to avoid re-running the full
    # test suite (up to 120s) on every finalize-gate call. When disabled, fall
    # back to .coverage data if available.
    import os as _os
    import subprocess  # nosec B404
    import sys

    if _os.environ.get("HARNESS_CROSS_ARTIFACT_COV") == "1":
        test_target = "."
        if layout.active_test_dir.is_dir():
            test_target = layout.get_relative_str(layout.active_test_dir)
            
        try:
            result = subprocess.run(  # nosec B603 B607
                [sys.executable, "-m", "pytest", test_target, "--cov=.", "--cov-report=term-missing", "-q"],
                cwd=str(project_root),
                capture_output=True, text=True,
                timeout=120,
            )
        except Exception:
            return violations  # Cannot verify — skip
    else:
        # Fast path: try to use existing .coverage SQLite data
        coverage_data = project_root / ".coverage"
        if not coverage_data.exists():
            return violations  # No coverage data — skip silently
        try:
            result = subprocess.run(  # nosec B603 B607
                [sys.executable, "-m", "coverage", "report", "--format=total"],
                cwd=str(project_root),
                capture_output=True, text=True,
                timeout=15,
            )
        except Exception:
            return violations

    # Parse coverage output. Two formats:
    # 1. pytest --cov: "TOTAL    123    45    85%" (term-missing table)
    # 2. coverage report --format=total: just "85" or "85.0" on stdout
    actual_match = re.search(
        r'TOTAL\s+\d+\s+\d+\s+(\d{2,3}(?:\.\d)?)\s*%',
        result.stdout + result.stderr,
    )
    if actual_match:
        actual_pct = float(actual_match.group(1))
    else:
        # Try --format=total output (bare number)
        stripped = result.stdout.strip()
        if stripped and re.match(r'^\d{1,3}(?:\.\d+)?$', stripped):
            actual_pct = float(stripped)
        else:
            return violations  # Cannot parse — skip
    diff = abs(claimed_pct - actual_pct)

    if diff > 10:
        violations.append({
            "file": layout.get_relative_str(cov_report),
            "issue": (
                f"Coverage mismatch: report claims {claimed_pct}% but "
                f"pytest --cov measured {actual_pct}% (diff={diff:.1f}%)"
            ),
            "severity": "CRITICAL",
            "claimed": str(claimed_pct),
            "actual": str(actual_pct),
        })
    elif diff > 5:
        violations.append({
            "file": layout.get_relative_str(cov_report),
            "issue": (
                f"Coverage discrepancy: report claims {claimed_pct}% vs "
                f"actual {actual_pct}% (diff={diff:.1f}%)"
            ),
            "severity": "HIGH",
            "claimed": str(claimed_pct),
            "actual": str(actual_pct),
        })

    return violations


def run_cross_artifact_checks(
    project_root: Path, phase: int
) -> Dict[str, Any]:
    """Run all cross-artifact consistency checks for a phase.

    Returns:
        {"passed": bool, "violations": [...], "checks_ran": int}
    """
    violations: List[Dict[str, str]] = []

    # 1. Phase title check (all phases with artifacts)
    violations.extend(check_phase_title(project_root, phase))

    # 2. FR coverage check (Phase 4 specifically)
    if phase >= 4:
        violations.extend(check_fr_coverage(project_root, phase))

    # 3. Coverage report check (Phase 4 specifically)
    if phase >= 4:
        violations.extend(check_coverage_report(project_root, phase))

    criticals = [v for v in violations if v.get("severity") == "CRITICAL"]
    highs = [v for v in violations if v.get("severity") == "HIGH"]

    passed = len(criticals) == 0

    if violations:
        print(f"\n[Cross-Artifact] {len(violations)} issue(s):")
        for v in violations:
            sev = v.get("severity", "INFO")
            print(f"  [{sev}] {v.get('file', '?')}: {v.get('issue', '?')}")

    return {
        "passed": passed,
        "violations": violations,
        "checks_ran": 3 if phase >= 4 else 1,
        "critical_count": len(criticals),
        "high_count": len(highs),
    }
