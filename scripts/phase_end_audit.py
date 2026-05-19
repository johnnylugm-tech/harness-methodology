#!/usr/bin/env python3
"""
Phase End Audit — independent deliverable verification for Phase 3-8.

Called automatically by advance-phase and push-milestone (replaces
A/B collaboration checks that have been removed for Phase 3+).

Runs no LLM; pure filesystem/git/JSON checks that the main agent cannot
fabricate — the results reflect actual on-disk and git-tracked state.

Exit codes:
  0 — all clear (no CRITICAL gaps)
  1 — CRITICAL gaps found (fix before advancing)
  2 — error running audit
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Tuple


# ── Per-phase artifact lists (mirrors PHASE_ARTIFACTS in harness_cli.py) ─────
_DELIVERABLES: dict[int, list[str]] = {
    3: ["03-development/src/", "03-development/tests/"],
    4: ["04-testing/TEST_PLAN.md", "04-testing/TEST_RESULTS.md"],
    5: ["05-verification/BASELINE.md", "05-verification/VERIFICATION_REPORT.md"],
    6: ["06-quality/QUALITY_REPORT.md"],
    7: ["07-risk/RISK_ASSESSMENT.md", "07-risk/RISK_REGISTER.md"],
    8: ["08-config/CONFIG_RECORDS.md", "08-config/RELEASE_CHECKLIST.md"],
}

_EXIT_GATES: dict[int, int] = {3: 2, 4: 3, 6: 4}

_MILESTONES: dict[int, list[str]] = {
    3: ["p3-mid", "p3-pre-gate2"],
    4: ["p4-mid", "p4-pre-gate3"],
    5: ["p5-baseline"],
    7: ["p7"],
    8: ["p8"],
}


# ── Audit functions ──────────────────────────────────────────────────────────

def audit_plan_completion(project: Path, phase: int) -> Tuple[list[str], list[str]]:
    """Check phase plan for unchecked items that indicate incomplete work.

    Returns (critical_gaps, warning_gaps).
    """
    plan = project / ".methodology" / f"phase{phase}_plan.md"
    if not plan.exists():
        return ([f"Phase plan `.methodology/phase{phase}_plan.md` not found"], [])

    text = plan.read_text(encoding="utf-8", errors="replace")

    # Find unchecked items. Exclude patterns that are meta-items:
    # [INFO], [PHASE-AUDIT], Gate * score, Phase * complete
    skip_patterns = re.compile(
        r"^\s*\*\s*\[INFO\]|\[PHASE-AUDIT\]|Gate \d+.*score|Phase \d+.*complete",
        re.IGNORECASE,
    )
    unchecked: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("- [ ]"):
            if skip_patterns.search(stripped):
                continue
            unchecked.append(stripped)

    if unchecked:
        msg = f"Plan has {len(unchecked)} unchecked item(s)"
        return ([msg], unchecked[:5])
    return ([], [])


def audit_deliverables(project: Path, phase: int) -> Tuple[list[str], list[str]]:
    """Check declared deliverables exist on disk and are git-tracked.

    Returns (critical_gaps, warning_gaps).
    """
    criticals: list[str] = []
    warnings: list[str] = []

    paths = _DELIVERABLES.get(phase, [])
    for rel_path in paths:
        full = project / rel_path
        exists = full.exists()
        tracked = _is_git_tracked(project, rel_path)

        if not exists:
            criticals.append(f"Deliverable `{rel_path}` not found on disk")
        elif not tracked:
            warnings.append(f"Deliverable `{rel_path}` exists but is not git-tracked")
        elif full.is_file() and full.stat().st_size < 200:
            warnings.append(f"Deliverable `{rel_path}` exists but has <200 bytes of content")

    return (criticals, warnings)


def audit_gate_results(
    project: Path, phase: int,
) -> Tuple[list[str], list[str]]:
    """Check quality_manifest.json for gate completion.

    Returns (critical_gaps, warning_gaps).
    """
    criticals: list[str] = []
    manifest_path = project / ".methodology" / "quality_manifest.json"
    if not manifest_path.exists():
        return (["quality_manifest.json not found — gate results cannot be verified"], [])

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return ([f"Cannot parse quality_manifest.json: {exc}"], [])

    gate_results = manifest.get("gate_results", {})
    fr_ids = manifest.get("fr_ids", [])

    # Check Gate 1 per-FR
    gate1 = gate_results.get("gate1", {})
    if isinstance(gate1, dict) and fr_ids:
        missing = [fr for fr in fr_ids
                   if not isinstance(gate1.get(fr), dict)
                   or not gate1[fr].get("quality_complete")]
        if missing:
            criticals.append(f"Gate 1 not complete for FR(s): {', '.join(missing)}")
    elif fr_ids:
        criticals.append("Gate 1 results missing from quality_manifest.json")

    # Check exit gate if applicable
    exit_gate = _EXIT_GATES.get(phase)
    if exit_gate:
        gk = f"gate{exit_gate}"
        gv = gate_results.get(gk, {})
        if not isinstance(gv, dict) or not gv.get("quality_complete"):
            criticals.append(
                f"Exit Gate {exit_gate} not marked quality_complete "
                f"in quality_manifest.json"
            )

    return (criticals, [])


def audit_git_log(project: Path, phase: int) -> Tuple[list[str], list[str]]:
    """Check git log for milestone commits and per-FR activity.

    Returns (critical_gaps, warning_gaps).
    """
    warnings: list[str] = []

    # Check milestone commits
    milestones = _MILESTONES.get(phase, [])
    if milestones:
        try:
            result = subprocess.run(
                ["git", "-C", str(project), "log", "--oneline", "-30"],
                capture_output=True, text=True, timeout=30,
            )
            log_text = result.stdout
            for ms in milestones:
                if ms not in log_text:
                    warnings.append(
                        f"Milestone commit `{ms}` not found in recent git history"
                    )
        except (subprocess.TimeoutExpired, OSError) as exc:
            warnings.append(f"Could not check git log for milestones: {exc}")

    return ([], warnings)


def audit_development_log(project: Path, phase: int) -> Tuple[list[str], list[str]]:
    """Check DEVELOPMENT_LOG.md contains phase entries.

    Returns (critical_gaps, warning_gaps).
    """
    warnings: list[str] = []
    dev_log = project / "DEVELOPMENT_LOG.md"
    if not dev_log.exists():
        warnings.append("DEVELOPMENT_LOG.md not found")
        return ([], warnings)

    text = dev_log.read_text(encoding="utf-8", errors="replace")
    if f"Phase {phase}" not in text:
        warnings.append(f"DEVELOPMENT_LOG.md has no entries for Phase {phase}")
    if not re.search(r"session[_-]?id", text, re.IGNORECASE):
        warnings.append("DEVELOPMENT_LOG.md has no session_id references")

    return ([], warnings)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _is_git_tracked(project: Path, rel_path: str) -> bool:
    """Check if a path is tracked by git."""
    try:
        result = subprocess.run(
            ["git", "-C", str(project), "ls-files", "--error-unmatch", rel_path],
            capture_output=True, text=True, timeout=15,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, OSError):
        return False


def _git_shortlog(project: Path) -> str:
    """Get recent git log summary for the report."""
    try:
        result = subprocess.run(
            ["git", "-C", str(project), "log", "--oneline", "--graph", "-15"],
            capture_output=True, text=True, timeout=30,
        )
        return result.stdout
    except (subprocess.TimeoutExpired, OSError):
        return "(unable to retrieve git log)"


# ── Report writer ─────────────────────────────────────────────────────────────

def write_report(
    project: Path, phase: int,
    criticals: list[str],
    warnings: list[str],
    verified: list[str],
) -> Path:
    """Write .methodology/audit_gaps_{phase}.md. Returns the output path."""
    out_dir = project / ".methodology"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"audit_gaps_{phase}.md"

    lines = [
        f"# Phase {phase} End Audit",
        "",
        f"**Audited**: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        f"**Result**: {'GAPS FOUND' if criticals else 'PASSED'}",
        "",
    ]

    if criticals:
        lines.append("## CRITICAL Gaps (must fix before advancing)")
        lines.append("")
        for g in criticals:
            lines.append(f"- [ ] {g}")
        lines.append("")

    if warnings:
        lines.append("## WARNING Gaps")
        lines.append("")
        for w in warnings:
            lines.append(f"- ⚠ {w}")
        lines.append("")

    if verified:
        lines.append("## Verified")
        lines.append("")
        for v in verified:
            lines.append(f"- ✓ {v}")
        lines.append("")

    lines.append("## Git Log")
    lines.append("")
    lines.append("```")
    lines.append(_git_shortlog(project))
    lines.append("```")
    lines.append("")

    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out_path


# ── Main entry point ─────────────────────────────────────────────────────────

def run_audit(project: Path, phase: int) -> int:
    """Run all audit checks and write report. Returns exit code."""
    criticals: list[str] = []
    warnings: list[str] = []
    verified: list[str] = []

    # 1. Plan completion
    c, w = audit_plan_completion(project, phase)
    criticals.extend(c)
    warnings.extend(w)
    if not c:
        verified.append("Phase plan checklist reviewed")

    # 2. Deliverable existence
    c, w = audit_deliverables(project, phase)
    criticals.extend(c)
    warnings.extend(w)
    if not c:
        verified.append("All declared deliverables present on disk")

    # 3. Gate results
    c, w = audit_gate_results(project, phase)
    criticals.extend(c)
    warnings.extend(w)
    if not c:
        verified.append("Gate results verified in quality_manifest.json")

    # 4. Git log
    c, w = audit_git_log(project, phase)
    criticals.extend(c)
    warnings.extend(w)
    verified.append("Git history checked")

    # 5. Development log
    c, w = audit_development_log(project, phase)
    criticals.extend(c)
    warnings.extend(w)
    verified.append("DEVELOPMENT_LOG.md checked")

    out_path = write_report(project, phase, criticals, warnings, verified)
    print(f"\n[phase-end-audit] Report: {out_path}")

    if criticals:
        print(f"[phase-end-audit] ❌ {len(criticals)} CRITICAL gap(s) found")
        for g in criticals:
            print(f"  - {g}")
    else:
        print("[phase-end-audit] ✅ No critical gaps found")

    if warnings:
        print(f"[phase-end-audit] ⚠ {len(warnings)} warning(s)")
        for warning in warnings:
            print(f"  - {warning}")

    return 1 if criticals else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Phase End Audit — independent Phase 3-8 deliverable verification."
    )
    parser.add_argument(
        "--phase", type=int, required=True, choices=range(3, 9),
        help="Phase number to audit (3-8)",
    )
    parser.add_argument(
        "--project", type=str, default=".",
        help="Project root path (default: current directory)",
    )
    args = parser.parse_args(argv)

    project = Path(args.project).resolve()
    if not project.is_dir():
        print(f"[phase-end-audit] ERROR: project path not found: {project}", file=sys.stderr)
        return 2

    return run_audit(project, args.phase)


if __name__ == "__main__":
    sys.exit(main())
