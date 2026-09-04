#!/usr/bin/env python3
"""
Generate VERIFICATION_REPORT.md from quality_manifest.json + Gate 1/3 results.

Usage:
    python3 scripts/generate_verification_report.py --project /path/to/project
    python3 scripts/generate_verification_report.py --project . --output VERIFICATION_REPORT.md

Output:
    Creates/updates 05-verification/VERIFICATION_REPORT.md with:
    - Per-FR verification status (PASS/FAIL), evidence, acceptance criteria result
    - Test coverage %, mutation score, deferred issues from Gate 3
    - Certification that all Gate 3 open issues are addressed or deferred with justification

This script was created to fix Finding #16: the P5 plan said "Generate
VERIFICATION_REPORT.md" but no tool produced it. The Phase 4→5 handoff
validator checks for this file and would block with no remediation path.
"""
from __future__ import annotations

import argparse
import json
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Ensure harness root (parent of scripts/) is on sys.path so core.quality_gate is importable
_HARNESS_ROOT = Path(__file__).parent.parent
if str(_HARNESS_ROOT) not in sys.path:
    sys.path.insert(0, str(_HARNESS_ROOT))

from core.utils.project_layout import ProjectLayout  # noqa: E402


def _load_json(path: Path) -> dict[str, Any]:
    """Load a JSON file, returning {} on failure."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _load_text(path: Path) -> str | None:
    """Load a text file, returning None on failure."""
    try:
        return path.read_text(encoding="utf-8")
    except (FileNotFoundError, OSError):
        return None


def _extract_acceptance_criteria(project: Path) -> dict[str, list[str]]:
    """Per-requirement acceptance criteria — the framework's own answer.

    Round 97. This used to carry its own regex — a dashed identifier form on
    a plain line — and it had never matched anything. Measured across the
    eleven corpus
    projects: `artifact_consistency.srs_acceptance_criteria` finds 1,004
    criteria (28-134 per project) and this function found ZERO on every one of
    them — so all eleven shipped a VERIFICATION_REPORT.md whose every per-FR
    section reads "_No acceptance criteria extracted from SRS.md — verify
    manually._" for the whole of Phase 5.

    Every SRS in the corpus writes `#### AC-1.1` headings, which is what the
    Phase 1 prompt produces and what the canonical parser was written for. It
    also reads the bullet spelling, so nothing is traded away by deferring to
    it.

    Not a better regex — one place where the framework decides what an
    acceptance criterion is. Round 17 / Round 33: one contract, one statement.
    """
    from core.quality_gate.artifact_consistency import srs_acceptance_criteria

    return srs_acceptance_criteria(project)


def _get_fr_gate1_status(
    fr_id: str, gate_results: dict[str, Any]
) -> tuple[str, str]:
    """Return (status, score_str) for an FR from gate1 results."""
    g1 = gate_results.get("gate1", {})
    if not isinstance(g1, dict):
        return ("UNKNOWN", "n/a")
    entry = g1.get(fr_id)
    if isinstance(entry, dict):
        if entry.get("quality_complete"):
            score = entry.get("score", "n/a")
            return ("PASS", str(score))
        return ("FAIL", str(entry.get("score", "n/a")))
    return ("NOT-RUN", "n/a")


def _collect_deferred_issues(gate_results: dict[str, Any]) -> list[str]:
    """Return deferred-issue strings from Gate 3 result (if present)."""
    g3 = gate_results.get("gate3")
    if not isinstance(g3, dict):
        return []
    issues = g3.get("deferred_issues") or g3.get("deferred") or []
    if not isinstance(issues, list):
        return []
    return [str(i) for i in issues]


def _format_fr_section(
    fr_id: str,
    ac_list: list[str],
    status: str,
    score_str: str,
) -> str:
    """Format a per-FR verification block."""
    lines = [f"### {fr_id}", ""]
    if ac_list:
        lines.append("**Acceptance Criteria** (from SRS.md):")
        lines.append("")
        for ac in ac_list:
            lines.append(f"- {ac}")
        lines.append("")
    else:
        lines.append(
            "_No acceptance criteria extracted from SRS.md — verify manually._"
        )
        lines.append("")
    lines.append(f"**Status**: {status}  ")
    lines.append(f"**Score**: {score_str}")
    lines.append("")
    return "\n".join(lines)


def generate_verification_report(project_root: str | Path) -> Path:
    """Generate 05-verification/VERIFICATION_REPORT.md. Returns the output path."""
    project = Path(project_root).resolve()
    manifest_path = project / ".methodology" / "quality_manifest.json"
    srs_path = ProjectLayout(project).srs_path

    manifest = _load_json(manifest_path)
    fr_ids: list[str] = manifest.get("fr_ids", []) or []
    gate_results: dict[str, Any] = manifest.get("gate_results", {}) or {}
    ac_map = _extract_acceptance_criteria(project)

    if not fr_ids:
        # Fallback: derive from SRS.md headers
        if srs_path.exists():
            import re
            text = srs_path.read_text(encoding="utf-8")
            fr_ids = [
                f"FR-{n}" for n in re.findall(
                    r"^###\s+FR-(\d+)\s*:", text, re.MULTILINE
                )
            ]
    fr_ids = sorted(set(fr_ids))

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    # Aggregate Gate 3 metrics (best-effort)
    g3_raw: Any = gate_results.get("gate3")
    g3: dict[str, Any] = g3_raw if isinstance(g3_raw, dict) else {}
    coverage_str = str(g3.get("coverage_pct", "n/a"))
    mutation_str = str(g3.get("mutation_score", "n/a"))
    deferred = _collect_deferred_issues(gate_results)

    # Per-FR sections
    fr_sections: list[str] = []
    pass_count = 0
    for fr_id in fr_ids:
        status, score = _get_fr_gate1_status(fr_id, gate_results)
        if status == "PASS":
            pass_count += 1
        fr_sections.append(
            _format_fr_section(fr_id, ac_map.get(fr_id, []), status, score)
        )

    total = len(fr_ids)
    pass_pct = (100.0 * pass_count / total) if total else 0.0

    # Certification block
    # Bug H4 fix: previous logic checked `deferred` first, so a project with
    # any deferred issues AND any Gate 1 FAIL was reported as Conditional PASS.
    # Certification must be ordered by gate priority: empty manifest → unknown,
    # any Gate 1 FAIL → fail, otherwise deferred decides PASS vs Conditional PASS.
    if total == 0:
        cert = "**UNKNOWN** — No FRs declared in manifest. Verify project scope."
    elif pass_count < total:
        cert = (
            f"**FAIL** — {pass_count}/{total} FRs PASS at Gate 1. "
            "Resolve failing FRs before P5 exit."
        )
    elif deferred:
        cert = (
            "**Conditional PASS** — Gate 1 complete for all FRs; "
            f"{len(deferred)} Gate 3 issue(s) deferred with justification:\n\n"
            + "\n".join(f"- {d}" for d in deferred)
        )
    else:
        cert = "**PASS** — All FRs verified PASS at Gate 1. No Gate 3 deferred issues."

    body = f"""# VERIFICATION_REPORT — {project.name}

> Generated by `harness/scripts/generate_verification_report.py` on {now}
> Source: `.methodology/quality_manifest.json` (gate1/gate3) + `01-requirements/SRS.md` (AC)
> This report certifies the verification status of each Functional Requirement
> against its acceptance criteria, with Gate 3 deferred issues noted.

## Summary

| Metric | Value |
|--------|-------|
| Total FRs | {total} |
| FRs Gate 1 PASS | {pass_count} |
| FRs Gate 1 FAIL | {total - pass_count} |
| Pass rate | {pass_pct:.1f}% |
| Test coverage (Gate 3) | {coverage_str} |
| Mutation score (Gate 3) | {mutation_str} |
| Gate 3 deferred issues | {len(deferred)} |

## Certification

{cert}

## Per-FR Verification

{chr(10).join(fr_sections) if fr_sections else "_No FRs found in manifest or SRS.md._"}

---

## Provenance

- Manifest: `.methodology/quality_manifest.json`
- SRS: `01-requirements/SRS.md`
- Generator: `harness/scripts/generate_verification_report.py`
- Generated: {now}
- Generator commit: see `git log -1 --format='%H' -- harness/scripts/generate_verification_report.py`
"""
    out = ProjectLayout(project).verification_report_path
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(body, encoding="utf-8")
    return out


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Generate 05-verification/VERIFICATION_REPORT.md from "
            ".methodology/quality_manifest.json (gate1/gate3) and "
            "01-requirements/SRS.md (acceptance criteria, read through\n"
            "core.quality_gate.artifact_consistency.srs_acceptance_criteria)."
        ),
    )
    parser.add_argument("--project", default=".", help="Project path")
    parser.add_argument(
        "--output", default="05-verification/VERIFICATION_REPORT.md",
        help="Output path (relative to --project, or absolute).",
    )
    args = parser.parse_args()

    project = Path(args.project)
    if not project.is_absolute():
        project = project.resolve()

    try:
        out = generate_verification_report(project)
    except Exception as exc:  # surface error rather than silent fail
        # Bug M12 fix: print full traceback so the operator sees the
        # actual failing call. Comment claimed this already surfaced
        # errors but str(exc) alone hides the cause.
        print(f"[FAIL] {exc}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        return 1

    # If --output is custom, copy the default output there too
    if args.output and args.output != "05-verification/VERIFICATION_REPORT.md":
        target = Path(args.output)
        if not target.is_absolute():
            target = project / target
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(out.read_text(encoding="utf-8"), encoding="utf-8")
        out = target

    print(f"VERIFICATION_REPORT.md written → {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
