"""Gate 1 evidence persistence + anti-fabrication interval checks.

Moved verbatim from harness_cli.py (絞殺者續章 S3). Three co-equal Gate 1
evidence channels (sentinel flags, finalized marks, gate_timestamps.jsonl)
plus the batch-commit fraud detector and the per-FR score tracker feeding
the inter-FR variance check.
"""

from __future__ import annotations

import json
from pathlib import Path

from core.atomic_io import atomic_write_json

__all__ = [
    "GATE_TIMESTAMPS_FILE",
    "GATE_TIMESTAMPS_MAX_ENTRIES",
    "GATE1_SCORES_FILE",
    "record_gate_timestamp",
    "gate1_evidence_exists",
    "check_commit_intervals",
    "record_gate1_score",
]

# Non-dotfile (consistent with other .methodology/ files like state.json, sessions_spawn.log).
# Replaces the old ".gate_timestamps.jsonl" hidden file name used before 2026-05-18.
GATE_TIMESTAMPS_FILE = "gate_timestamps.jsonl"
GATE_TIMESTAMPS_MAX_ENTRIES = 200
# Sizing: 22 FRs × max_fix_rounds(3) × 3 phases ≈ 200; increase if FR count > 22.

GATE1_SCORES_FILE = ".gate1_scores.json"


def record_gate_timestamp(project: Path, phase: int, gate_num: int, fr_id: str | None) -> None:
    """Append gate commit timestamp to .methodology/gate_timestamps.jsonl (P1 persistence).

    Called only on SUCCESSFUL gate finalization — not on failed checks — so the file
    represents genuine completed gates, not attempts.  Trims to the last
    GATE_TIMESTAMPS_MAX_ENTRIES entries to bound file growth.
    """
    import time as _time
    ts_dir = project / ".methodology"
    ts_dir.mkdir(parents=True, exist_ok=True)
    ts_file = ts_dir / GATE_TIMESTAMPS_FILE

    # One-time migration: rename old hidden-file to the new visible name
    _old = ts_dir / ".gate_timestamps.jsonl"
    if _old.exists() and not ts_file.exists():
        try:
            _old.rename(ts_file)
        except OSError:
            pass

    entry = {"phase": phase, "gate": gate_num, "fr_id": fr_id or "phase", "ts": _time.time()}
    try:
        # Append
        with open(str(ts_file), "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
        # Trim to last GATE_TIMESTAMPS_MAX_ENTRIES lines
        raw = ts_file.read_text(encoding="utf-8")
        lines = [line for line in raw.splitlines() if line.strip()]
        if len(lines) > GATE_TIMESTAMPS_MAX_ENTRIES:
            ts_file.write_text(
                "\n".join(lines[-GATE_TIMESTAMPS_MAX_ENTRIES:]) + "\n",
                encoding="utf-8",
            )
    except OSError:
        pass  # Non-blocking


def gate1_evidence_exists(project: Path, fr_id: str, phase: int = 3) -> bool:
    """Multi-source Gate 1 evidence check (O2, 2026-07-07).

    Accepts any of three co-equal Gate 1 evidence channels — eliminates the
    single-source-of-evidence design defect where a clean restart wiping
    `.sessi-work/sentinels/` would block P3→P4 handoff even though Gate 1
    was genuinely complete (fr_progress.json + gate_timestamps.jsonl both
    persist this fact).

    Try in order:
      1. `.sessi-work/sentinels/g1_p{phase}_{fr}.flag`  — run-gate's mark
      2. `.sessi-work/sentinels/g1_p{phase}_{fr}.finalized` — finalize-gate's mark
         (finalize-gate implies run-gate ran, so this is sufficient)
      3. `.methodology/gate_timestamps.jsonl` row matching phase/gate/fr_id
         (P1-persistent; survives clean restart)

    `fr_id` normalization (`replace("-", "").lower()`) matches `_sentinel_path`
    and `_finalize_sentinel_path` in harness_cli.py.
    """
    fr_key = fr_id.replace("-", "").lower()
    sentinels_dir = project / ".sessi-work" / "sentinels"
    if (sentinels_dir / f"g1_p{phase}_{fr_key}.flag").exists():
        return True
    if (sentinels_dir / f"g1_p{phase}_{fr_key}.finalized").exists():
        return True
    ts_file = project / ".methodology" / GATE_TIMESTAMPS_FILE
    if ts_file.exists():
        try:
            for line in ts_file.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                _e = json.loads(line)
                if (
                    _e.get("phase") == phase
                    and _e.get("gate") == 1
                    and str(_e.get("fr_id", "")).replace("-", "").lower() == fr_key
                ):
                    return True
        except (json.JSONDecodeError, OSError):
            pass
    return False


def check_commit_intervals(
    project: str, phase: int, gate_num: int, fr_id: str | None = None
) -> tuple[bool, str]:
    """Check if current gate attempt would exceed the batch-commit threshold (P1).

    Pure read — does NOT write timestamps.  The caller (cmd_finalize_gate) must call
    record_gate_timestamp() only on successful finalization, so failed attempts don't
    accumulate in the file and trigger false positives on retry.

    Blocks if ≥2 prior successful finalizations exist within a 2-second window for the
    same (phase, gate, fr_id) bucket (3 total = statistically implausible for genuine
    per-FR work). fr_id is optional: when None the check is phase-level only (legacy
    behaviour for callers that don't track per-FR); when provided, distinct FRs do
    not collide into the same bucket, so 5 FRs completing in the same 2s window is
    no longer flagged as fraud.
    Returns (ok, diagnostic).
    """
    import time as _time
    project_path = Path(project)
    ts_dir = project_path / ".methodology"
    ts_file = ts_dir / GATE_TIMESTAMPS_FILE

    # One-time migration: honour renamed dotfile for legacy projects
    _old = ts_dir / ".gate_timestamps.jsonl"
    if _old.exists() and not ts_file.exists():
        try:
            _old.rename(ts_file)
        except OSError:
            pass

    now = _time.time()
    recent: list[dict] = []

    if ts_file.exists():
        try:
            for line in ts_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if (entry.get("phase") == phase
                        and entry.get("gate") == gate_num
                        and now - entry.get("ts", 0) <= 2.0):
                    # Per-FR bucket isolation: when the caller provides an fr_id,
                    # only count entries with the SAME fr_id as "same bucket".
                    # Distinct FRs finalizing within 2s (the natural per-FR
                    # sequential finalize-gate pattern) must NOT collide here —
                    # doing so was producing false-positive fraud blocks during
                    # Phase 3 Gate 1 finalization runs.
                    if fr_id is not None and entry.get("fr_id") != fr_id:
                        continue
                    recent.append(entry)
        except OSError:
            pass

    if len(recent) >= 2:  # 2 prior successful + 1 current attempt = 3 total in window
        return False, (
            f"{len(recent) + 1} gate commits within 2 seconds — "
            "scores must be evaluated per-FR with genuine evidence, not batch-copied"
        )
    return True, ""


def record_gate1_score(project: Path, phase: int, fr_id: str, score: float) -> None:
    """Track Gate 1 composite score per FR for inter-FR variance check.

    Prunes phases older than (phase - 1) to bound file growth; the previous phase
    data is kept so finalize-gate can still reference it, but anything further back
    is stale and safe to drop.
    """
    scores_file = project / ".methodology" / GATE1_SCORES_FILE
    scores: dict = {}
    if scores_file.exists():
        try:
            scores = json.loads(scores_file.read_text(encoding="utf-8"))
        except Exception:  # pylint: disable=broad-exception-caught
            pass
    scores.setdefault(str(phase), {})[fr_id] = score
    # Prune stale phases — keep current and previous only
    stale = [k for k in list(scores.keys()) if int(k) < phase - 1]
    for k in stale:
        del scores[k]
    try:
        atomic_write_json(scores_file, scores)
    except Exception:  # pylint: disable=broad-exception-caught
        pass
