"""Ghost paper-trail detection — verify that sub-agent CLAIMED work matches ACTUAL code changes.

Companion to the AgentSpawner regression guard (_dispatch_diff_budget). The regression
guard detects destructive edits (files that LOST lines); this module detects the
opposite: agents that self-report "done" while making zero substantive code changes.

Principle: never trust agent self-report — independently verify against ground truth
(git diff). Same pattern as COVERAGE-FIX fallback (real pytest --cov, not agent claims)
and lint gate (real ruff check, not agent claims).
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Per-step exit code returned by cmd_run_fr_step when ghost paper-trail is detected.
# The workflow JS treats any non-zero exit as ERROR — this specific code is
# distinguishable in the journal for debugging but does not need special JS handling.
GHOST_DETECTED_EXIT_CODE = 22

# Directory under .sessi-work/ for ghost paper-trail records.
GHOST_PAPER_TRAIL_DIR = ".sessi-work/ghost_detected"

# Steps that are expected to produce source code changes. Steps not in this set
# (TDD-RED, GATE1, GATE1-DELTA) are skipped by ghost detection — RED only adds
# test files, GATE1 writes manifest/gate results.
_CODE_PRODUCING_STEPS = frozenset({
    "TDD-GREEN", "TDD-IMPROVE",
    "CODE-FIX", "COVERAGE-FIX", "LINT-FIX", "TEST-FIX", "INFRA-FIX",
})

# File extensions and directory prefixes excluded from the "code change" check.
# Changes limited to these files are treated as non-substantive.
_NON_CODE_SUFFIXES = (
    ".md", ".json", ".yaml", ".yml", ".toml", ".cfg", ".ini",
    ".txt", ".lock", ".gitignore",
)
_NON_CODE_DIR_PREFIXES = (".methodology/", ".sessi-work/", "docs/", "00-summary/")


def detect_ghost_changes(
    project: Path,
    pre_sha: str,
    step: str,
    fr_id: str,
    agent_output: str,
) -> dict[str, Any]:
    """Compare pre-dispatch HEAD against current HEAD; flag zero-substantive-change.

    Args:
        project: Repository root.
        pre_sha: HEAD SHA captured before the agent was dispatched.
        step: FR step name (TDD-RED, TDD-GREEN, …, GATE1-DELTA).
        fr_id: FR identifier (e.g. "FR-01").
        agent_output: Raw agent stdout/stderr for extracting claimed summary.

    Returns:
        Dict with keys ghost_detected, reason, pre_sha, post_sha,
        changed_files, total_added, total_removed, claimed_summary,
        actual_summary.
    """
    step_upper = step.upper()
    _ = fr_id  # reserved for future FR-specific ghost rules

    # ── Degraded mode: no pre-SHA available (e.g. git failure) ─────────────
    if not pre_sha:
        return {
            "ghost_detected": False,
            "reason": "no pre-step SHA available (graceful degradation)",
            "pre_sha": "",
            "post_sha": _git_rev_parse(project),
            "changed_files": 0,
            "total_added": 0,
            "total_removed": 0,
            "claimed_summary": agent_output[:200].replace("\n", " ") if agent_output else "",
            "actual_summary": "unknown (no pre-step SHA)",
        }

    # ── Step-aware skip: steps that are not expected to produce source changes ──
    if step_upper in ("TDD-RED", "GATE1", "GATE1-DELTA"):
        return {
            "ghost_detected": False,
            "reason": f"step {step_upper} is not expected to produce source changes",
            "pre_sha": pre_sha,
            "post_sha": "",
            "changed_files": 0,
            "total_added": 0,
            "total_removed": 0,
            "claimed_summary": "",
            "actual_summary": "",
        }

    current_sha = _git_rev_parse(project)
    claimed = agent_output[:200].replace("\n", " ") if agent_output else ""

    # ── No HEAD movement at all: agent made zero commits ──
    if pre_sha == current_sha:
        if step_upper in _CODE_PRODUCING_STEPS:
            return {
                "ghost_detected": True,
                "reason": "HEAD did not move — agent produced no commits",
                "pre_sha": pre_sha,
                "post_sha": current_sha or "",
                "changed_files": 0,
                "total_added": 0,
                "total_removed": 0,
                "claimed_summary": claimed,
                "actual_summary": "zero commits (HEAD unchanged)",
            }
        # Non-code-producing step with no commits — not a ghost (e.g. evaluation-only).
        return {
            "ghost_detected": False,
            "reason": f"step {step_upper} produced no commits (evaluation step)",
            "pre_sha": pre_sha,
            "post_sha": current_sha or "",
            "changed_files": 0,
            "total_added": 0,
            "total_removed": 0,
            "claimed_summary": claimed,
            "actual_summary": "zero commits",
        }

    # ── Inspect what changed between pre_sha and HEAD ──
    changed_files = _git_changed_files(project, pre_sha)
    code_files = [f for f in changed_files if _is_code_file(f)]

    if not code_files:
        return {
            "ghost_detected": True,
            "reason": (
                f"only non-code files changed ({len(changed_files)} file(s): "
                + ", ".join(changed_files[:5])
                + ("…" if len(changed_files) > 5 else "")
                + ")"
            ),
            "pre_sha": pre_sha,
            "post_sha": current_sha or "",
            "changed_files": len(changed_files),
            "total_added": 0,
            "total_removed": 0,
            "claimed_summary": claimed,
            "actual_summary": f"{len(changed_files)} non-code file(s) changed",
        }

    # ── Check for whitespace-only changes in code files ──
    added, removed = _git_diff_numstat_ignore_ws(project, pre_sha, code_files)

    if added == 0 and removed == 0:
        return {
            "ghost_detected": True,
            "reason": (
                f"whitespace-only changes in {len(code_files)} code file(s)"
            ),
            "pre_sha": pre_sha,
            "post_sha": current_sha or "",
            "changed_files": len(code_files),
            "total_added": 0,
            "total_removed": 0,
            "claimed_summary": claimed,
            "actual_summary": f"{len(code_files)} file(s), whitespace-only (+0 -0 with -w)",
        }

    # ── Substantive changes detected ──
    return {
        "ghost_detected": False,
        "reason": f"substantive changes: +{added} -{removed} in {len(code_files)} file(s)",
        "pre_sha": pre_sha,
        "post_sha": current_sha or "",
        "changed_files": len(code_files),
        "total_added": added,
        "total_removed": removed,
        "claimed_summary": claimed,
        "actual_summary": f"{len(code_files)} file(s), +{added} -{removed}",
    }


# ── Persistence ──────────────────────────────────────────────────────────────


def write_ghost_paper_trail(project: Path, ghost_result: dict[str, Any]) -> None:
    """Persist a ghost-detection record to .sessi-work/ghost_detected/.

    The file is named ``{fr_id}_{step}.json`` so subsequent detections for the
    same FR+step overwrite rather than accumulate.
    """
    fr_id = ghost_result.get("fr_id", "unknown")
    step = ghost_result.get("step", "unknown")
    trail_dir = project / GHOST_PAPER_TRAIL_DIR
    trail_dir.mkdir(parents=True, exist_ok=True)

    record = {
        **ghost_result,
        "detected_at": datetime.now(timezone.utc).isoformat(),
    }
    out_path = trail_dir / f"{fr_id}_{step}.json"
    out_path.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")


def scan_phase_ghost_trails(project: Path, phase: int) -> list[dict[str, Any]]:
    """Return all ghost paper-trail records for *phase*.

    Used by advance-phase prechecks to aggregate unresolved ghost detections
    and block advance until each flagged step is re-run with substantive changes.
    """
    trail_dir = project / GHOST_PAPER_TRAIL_DIR
    if not trail_dir.is_dir():
        return []

    results: list[dict[str, Any]] = []
    for trail_file in sorted(trail_dir.glob("*.json")):
        try:
            data = json.loads(trail_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if data.get("phase") == phase:
            results.append(data)
    return results


# ── Git helpers ──────────────────────────────────────────────────────────────


def _git_rev_parse(project: Path) -> str:
    """Return HEAD SHA or empty string on failure."""
    try:
        r = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=10,
            cwd=str(project),
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if r.returncode != 0:
        return ""
    return r.stdout.strip()


def _git_changed_files(project: Path, base: str) -> list[str]:
    """Return list of files changed between *base* and HEAD."""
    try:
        r = subprocess.run(
            ["git", "diff", "--name-only", base, "HEAD"],
            capture_output=True, text=True, timeout=15,
            cwd=str(project),
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if r.returncode != 0:
        return []
    return [line.strip() for line in r.stdout.splitlines() if line.strip()]


def _git_diff_numstat_ignore_ws(
    project: Path, base: str, files: list[str],
) -> tuple[int, int]:
    """Return (total_added, total_removed) for *files* between *base* and HEAD,
    ignoring whitespace-only changes (``-w`` flag).

    Binary files (numstat shows ``-``) are excluded from the count.
    """
    if not files:
        return 0, 0

    try:
        r = subprocess.run(
            ["git", "diff", "-w", "--numstat", base, "HEAD", "--", *files],
            capture_output=True, text=True, timeout=15,
            cwd=str(project),
        )
    except (OSError, subprocess.TimeoutExpired):
        return 0, 0
    if r.returncode != 0:
        return 0, 0

    added_total = 0
    removed_total = 0
    for line in r.stdout.splitlines():
        if not line.strip():
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        try:
            a = int(parts[0])
        except ValueError:
            a = 0  # binary file (numstat shows "-")
        try:
            r_ = int(parts[1])
        except ValueError:
            r_ = 0
        added_total += a
        removed_total += r_

    return added_total, removed_total


# ── File classification ─────────────────────────────────────────────────────


def _is_code_file(filepath: str) -> bool:
    """Return True if *filepath* looks like a source-code file (not docs/config)."""
    if filepath.endswith(_NON_CODE_SUFFIXES):
        return False
    for prefix in _NON_CODE_DIR_PREFIXES:
        if filepath.startswith(prefix):
            return False
    return True
