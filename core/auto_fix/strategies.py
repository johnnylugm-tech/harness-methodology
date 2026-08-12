#!/usr/bin/env python3
"""
Fix strategies for each problem type.

Each strategy function:
- Takes FixContext + project_root
- Returns (success: bool, action_taken: str, confidence: float)
"""

from __future__ import annotations


from pathlib import Path
from typing import Callable, Dict, List, Tuple

# ── AUTO_FIX strategies ──────────────────────────────────────────────────────


def _filter_overlay_gaps(
    project_root: Path, rt, report: dict,
) -> Tuple[List[str], List[str]]:
    """Discard uncoded/untested FRs that TRACEABILITY_MATRIX.overlay.yaml marks
    VERIFIED or Manual. Mirrors the PR 13 fix in core/phase_hooks.py so this
    retry loop doesn't treat manually-verified FRs as gaps forever.
    """
    uncoded_set = set(report.get("uncoded", []))
    untested_set = set(report.get("untested", []))
    try:
        from core.traceability.overlay import (
            atomic_to_dict, is_overlay_row_verified, load_overlay, merge_overlay,
        )
        overlay = load_overlay(project_root / "TRACEABILITY_MATRIX.overlay.yaml")
        if overlay:
            merged = merge_overlay(atomic_to_dict(rt), overlay)
            for fr_id, row in merged.get("requirements", {}).items():
                if is_overlay_row_verified(row):
                    uncoded_set.discard(fr_id)
                    untested_set.discard(fr_id)
    except Exception as e:
        print(f"   [WARN] Overlay merge failed: {e}")
    return list(uncoded_set), list(untested_set)


def fix_missing_traceability(context, project_root: Path) -> Tuple[bool, str, float]:
    """PR 5 auto-fix: re-verify loop with bounded retries; escalate on max_rounds.

    Behavior:
      1. propose_fixes() emits a unified diff of candidate [FR-XX] annotations
         and test stubs.
      2. Apply diff to source tree via `git apply --3way`.
      3. Re-run `check_traceability` to verify.
      4. If passed → return success (True, "Auto-fixed: N changes", 90.0).
      5. If failed → context.retry_count += 1; if < max_rounds → loop.
      6. If max_rounds exhausted → write final diff to
         `.methodology/trace/proposed_fix.diff` and return
         (False, "HUMAN_REQUIRED: apply git apply ...", 0.0) — the
         AutoFixEngine treats `False` as escalation and surfaces the
         message to the user.

    The legacy stub-matrix path is removed (it produced an artifact that
    could never pass `check_spec_trace`).
    """
    from core.traceability.auto_fix_propose import (
        apply_diff, propose_fixes, rollback, write_proposed_diff,
    )
    from core.traceability.scanner import check_traceability

    # Bound the loop by the strategy's max_rounds (or a hard ceiling of 5).
    try:
        max_rounds = int(context.details.get("max_rounds", 5))
    except (AttributeError, TypeError, ValueError):
        max_rounds = 5

    applied_diffs: List[str] = []
    last_diff = ""
    last_msg = ""
    for round_idx in range(max_rounds):
        # 1. Re-derive the current gaps
        _rt, report = check_traceability(project_root)
        uncoded, untested = _filter_overlay_gaps(project_root, _rt, report)
        if not uncoded and not untested:
            return (True, "All FRs already fully traced", 90.0)

        # 2. Propose a diff
        diff_text = propose_fixes(_rt, report, project_root)
        if not diff_text.strip():
            return (True, "No additional fixes proposed", 90.0)
        last_diff = diff_text

        # 3. Apply
        ok, apply_msg = apply_diff(project_root, diff_text)
        if not ok:
            rollback(project_root, applied_diffs)
            last_msg = f"round {round_idx+1}: apply failed ({apply_msg})"
            continue

        # 4. Re-verify
        _rt2, report2 = check_traceability(project_root)
        still_uncoded, still_untested = _filter_overlay_gaps(project_root, _rt2, report2)
        if not still_uncoded and not still_untested:
            n = len(uncoded) + len(untested)
            return (True, f"Auto-fixed: {n} gap(s) closed in {round_idx+1} round(s)", 90.0)
        last_msg = (f"round {round_idx+1}: applied but {len(still_uncoded)} "
                    f"uncoded / {len(still_untested)} untested remain")
        applied_diffs.append(diff_text)

    # 5. Exhausted: rollback all partial applies, re-derive a clean cumulative
    #    diff so proposed_fix.diff captures the full gap (not just last round's
    #    incremental delta), and leave the source tree clean on escalation.
    rollback(project_root, applied_diffs)
    _rt_clean, report_clean = check_traceability(project_root)
    cumulative_diff = propose_fixes(_rt_clean, report_clean, project_root)
    out_path = write_proposed_diff(project_root, cumulative_diff or last_diff)
    return (
        False,
        f"Auto-fix exhausted {max_rounds} rounds ({last_msg}). "
        f"Human review required. Apply: git apply {out_path}",
        0.0,
    )


# ── AUTO_FIX_WITH_VERIFICATION strategies ────────────────────────────────────


def _insert_import(content: str, import_line: str) -> str:
    """Insert an import line after the last existing import in the file."""
    if import_line in content:
        return content
    lines = content.split("\n")
    last_import_idx = -1
    for i, line in enumerate(lines):
        if line.startswith("import ") or line.startswith("from "):
            last_import_idx = i
    if last_import_idx >= 0:
        lines.insert(last_import_idx + 1, import_line)
    else:
        # No existing imports — insert at top of file (before docstring)
        lines.insert(0, import_line)
    return "\n".join(lines)


def _module_exists_in_project(module: str, project_root: Path) -> bool:
    """Check if a module/package exists in the project."""
    parts = module.split(".")
    # Try as directory package
    pkg_path = project_root.joinpath(*parts)
    if pkg_path.is_dir() and (pkg_path / "__init__.py").exists():
        return True
    # Try as single file
    file_path = project_root / f"{'/'.join(parts)}.py"
    if file_path.exists():
        return True
    return False


def _is_simple_value(val: str) -> bool:
    """Check if a string value is a simple literal (int, float, str, bool, None)."""
    val = val.strip()
    if val in ("True", "False", "None"):
        return True
    try:
        int(val)
        return True
    except ValueError:
        pass
    try:
        float(val)
        return True
    except ValueError:
        pass
    if (val.startswith("'") and val.endswith("'")) or (val.startswith('"') and val.endswith('"')):
        return True
    return False


# ── Strategy registry ────────────────────────────────────────────────────────

STRATEGY_REGISTRY: Dict[str, Callable] = {
    # One entry. Round 48 站6 retired the other twelve — every one of them
    # made a checker quiet without making its subject true — and R49-C
    # deleted their code. core/auto_fix/wiring.py::RETIRED_STRATEGIES keeps
    # the reason for each, and AutoFixEngine.fix() still refuses those
    # problem_types by name, so a detector that emits one gets a refusal
    # rather than a KeyError.
    "missing_traceability": fix_missing_traceability,
}

# ── Internal helpers ─────────────────────────────────────────────────────────


