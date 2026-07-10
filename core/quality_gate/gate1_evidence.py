"""Gate 1 evidence persistence + anti-fabrication interval checks.

Moved verbatim from harness_cli.py (絞殺者續章 S3). Three co-equal Gate 1
evidence channels (sentinel flags, finalized marks, gate_timestamps.jsonl)
plus the batch-commit fraud detector and the per-FR score tracker feeding
the inter-FR variance check.
"""

from __future__ import annotations

import json
from pathlib import Path

import re
import subprocess
import sys
import warnings
from typing import Optional

from core.atomic_io import atomic_write_json
from core.quality_gate.spec_coverage import _git_test_patterns
from core.utils.project_layout import ProjectLayout

__all__ = [
    "fr_gate1_commit_sha",
    "fr_code_changed_since_last_gate1",
    "validate_fr_coverage_immediate",
    "GATE_TIMESTAMPS_FILE",
    "GATE_TIMESTAMPS_MAX_ENTRIES",
    "GATE1_SCORES_FILE",
    "record_gate_timestamp",
    "gate1_evidence_exists",
    "check_commit_intervals",
    "record_gate1_score",
    "_sentinel_path",
    "_finalize_sentinel_path",
    "SENTINEL_FLAG_TEMPLATE",
    "SENTINEL_FINALIZED_TEMPLATE",
]

# Sentinel filename SSOT (Round 3 Station I): every consumer of the on-disk
# sentinel format — the two path builders below, the evidence probe, and the
# prose generate_full_plan.py renders into phase plans — formats these
# templates instead of hand-writing the pattern. bea1bb1 fixed exactly that
# hand-written-copy drift by hand; tests/test_sentinel_template_ssot.py makes
# the next one fail at birth.
SENTINEL_FLAG_TEMPLATE = "g{gate}_p{phase}_{key}.flag"
SENTINEL_FINALIZED_TEMPLATE = "g{gate}_p{phase}_{key}.finalized"


def _sentinel_path(project: Path, gate: int, fr_id: str | None, phase: int | None = None) -> Path:
    """Return the sentinel file path that run-gate writes and finalize-gate verifies.

    v2.13 sentinel scope fix: include phase in the path so that Gate 1 written
    by Phase 1 (spec coverage) does NOT satisfy Gate 1 required by Phase 3
    (code coverage). Without phase, the same `g1_fr01.flag` path is reused
    across phases and stale Phase 1 sentinels leak into Phase 3 pre-checks.

    Path format:
      FR-specific:  g{gate}_p{phase}_{fr}.flag    e.g. g1_p3_fr01.flag
      Phase-level:  g{gate}_p{phase}_phase.flag  e.g. g2_p3_phase.flag (fr_id=None)

    Moved from cli/_shared.py (harness bug: GATE1 idempotency phase-scoping
    fix) so core/quality_gate/gate1_evidence.py can reuse it without a
    core -> cli circular import.
    """
    key = (fr_id or "phase").replace("-", "").lower()
    d = project / ".sessi-work" / "sentinels"
    if phase is None:
        warnings.warn(
            f"_sentinel_path(gate={gate}, fr_id={fr_id!r}) called without phase= "
            "(Bug #121 regression risk): cross-phase sentinel collision possible. "
            "Pass phase= explicitly.",
            DeprecationWarning,
            stacklevel=2,
        )
        return d / f"g{gate}_{key}.flag"
    return d / SENTINEL_FLAG_TEMPLATE.format(gate=gate, phase=phase, key=key)


def _finalize_sentinel_path(project: Path, gate: int, fr_id: str | None, phase: int | None = None) -> Path:
    """Return the sentinel that finalize-gate writes. advance-phase verifies it.

    See _sentinel_path for the v2.13 phase-scoping rationale. Moved from
    cli/_shared.py alongside _sentinel_path (see its docstring).
    """
    key = (fr_id or "phase").replace("-", "").lower()
    d = project / ".sessi-work" / "sentinels"

    if phase is not None:
        return d / SENTINEL_FINALIZED_TEMPLATE.format(gate=gate, phase=phase, key=key)

    # Legacy fallback (no phase provided): prefer the new-style .finalized;
    # fall back to legacy .flag with hyphen-stripped fr id (Bug #120 compat).
    std_path = d / f"g{gate}_{key}.finalized"
    if fr_id:
        legacy_path = d / f"g{gate}_{fr_id}.flag"
        if not std_path.exists() and legacy_path.exists():
            return legacy_path

    return std_path

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
    if (sentinels_dir / SENTINEL_FLAG_TEMPLATE.format(gate=1, phase=phase, key=fr_key)).exists():
        return True
    if (sentinels_dir / SENTINEL_FINALIZED_TEMPLATE.format(gate=1, phase=phase, key=fr_key)).exists():
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


# --- Gate-1 change detection + live coverage (moved from harness_cli, S4e) ---

def fr_gate1_commit_sha(fr_id: str, project: Path, phase: int | None = None) -> str | None:
    """Return the SHA of the most recent Gate 1 PASS commit for the given FR.

    phase-scoped lookup (when phase is given): bounds the git-log search to
    commits at/after the phase-scoped finalize-gate sentinel's own timestamp
    (_finalize_sentinel_path is only ever written right after a genuine
    bridge.finalize_gate() PASS for that exact phase — see gate_cmds.py
    cmd_finalize_gate). This closes a real gap: without --since, --grep
    matches ANY commit reachable from HEAD regardless of phase, and the
    unscoped "Gate1 PASS" fallback below can bind to a DIFFERENT FR's batch
    commit. If the sentinel doesn't exist, there is provably no Gate 1 PASS
    for this FR in this phase yet, so no SHA lookup / fallback is attempted.
    """
    import subprocess as _sp
    pattern = f"feat({fr_id}): Gate1 PASS"

    if phase is not None:
        sentinel = _finalize_sentinel_path(project, 1, fr_id, phase=phase)
        if not sentinel.exists():
            return None
        since = sentinel.read_text(encoding="utf-8").strip()
        r = _sp.run(
            ["git", "log", "--oneline", "--grep", pattern, "--since", since, "-1", "--format=%H"],
            capture_output=True, text=True, cwd=str(project),
        )
        sha = r.stdout.strip()
        return sha if sha else None

    r = _sp.run(
        ["git", "log", "--oneline", "--grep", pattern, "-1", "--format=%H"],
        capture_output=True, text=True, cwd=str(project),
    )
    sha = r.stdout.strip()
    if sha:
        return sha
    # Fallback: P3 batch commit e.g. "feat(P3-mid): 8/8 FR(s) Gate1 PASS"
    # Only reached for legacy (no phase) callers — phase-scoped callers above
    # return None instead of falling back to a possibly-different FR's commit.
    r2 = _sp.run(
        ["git", "log", "--oneline", "--grep", "Gate1 PASS", "-1", "--format=%H"],
        capture_output=True, text=True, cwd=str(project),
    )
    sha2 = r2.stdout.strip()
    return sha2 if sha2 else None


def fr_code_changed_since_last_gate1(fr_id: str, project: Path, phase: int | None = None) -> bool:
    """Check whether FR source/test files have changed since last Gate 1 PASS.

    Returns True if code has changed (re-evaluation needed), False otherwise.
    Uses AST parsing to accurately determine if changed lines overlap with FR functions.
    """
    import subprocess as _sp
    import ast
    sha = fr_gate1_commit_sha(fr_id, project, phase=phase)
    if sha is None:
        return True  # No prior Gate 1 PASS (this phase) → treat as changed

    # 1. Check test files directly
    fr_files: list[str] = []
    num_match = re.match(r"FR-(\d+)", fr_id)
    num_str = num_match.group(1).zfill(2) if num_match else ""
    if num_str:
        for p in _git_test_patterns(project, num_str, str(int(num_str))):
            fr_files.append(p)
            
    r_test = _sp.run(
        ["git", "diff", "--name-only", sha, "HEAD", "--"] + fr_files,
        capture_output=True, text=True, cwd=str(project),
    )
    if r_test.stdout.strip():
        return True

    # 2. Check source files via AST diff overlap
    r_src = _sp.run(
        ["git", "diff", "--name-only", sha, "HEAD", "--", "03-development/src"],
        capture_output=True, text=True, cwd=str(project),
    )
    changed_src = [f for f in r_src.stdout.splitlines() if f.endswith(".py")]
    
    for py_file in changed_src:
        curr_path = project / py_file

        if not curr_path.exists():
            continue

        try:
            content = curr_path.read_text(encoding="utf-8")
            if f"[{fr_id}]" not in content:
                continue

            tree = ast.parse(content)
            fr_ranges = []
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    doc = ast.get_docstring(node)
                    if doc and f"[{fr_id}]" in doc:
                        fr_ranges.append((node.lineno, getattr(node, "end_lineno", node.lineno)))

            if not fr_ranges:
                # string is in file but not in a docstring, default to changed
                return True

            # Single -U0 diff for both removed-tag check and hunk line parsing
            r_u0 = _sp.run(
                ["git", "diff", "-U0", sha, "HEAD", "--", py_file],
                capture_output=True, text=True, cwd=str(project),
            )
            # Fallback: tag was removed in the diff
            if f"[{fr_id}]" in r_u0.stdout:
                return True
            for line in r_u0.stdout.splitlines():
                if line.startswith("@@ "):
                    # @@ -old,n +new,n @@
                    try:
                        parts = line.split(" ")[2].split(",")
                        start_line = int(parts[0].lstrip("+"))
                        count = int(parts[1]) if len(parts) > 1 else 1
                        end_line = start_line + count - 1

                        for (fr_start, fr_end) in fr_ranges:
                            # Overlap check
                            if start_line <= fr_end and end_line >= fr_start:
                                return True
                    except Exception:
                        pass
        except Exception:
            # On parse error, fail safe
            return True

    return False

def validate_fr_coverage_immediate(
    project: Path, timeout: int = 120
) -> Optional[float]:
    """Run ``pytest --cov`` for the whole project right now and return line coverage %.

    Returns:
        ``None``      — pytest not installed, no tests found, or subprocess error.
        ``float``     — coverage percentage (0.0 - 100.0), or 0.0 if tests failed.

    Single whole-project pytest run (1-2s in practice) is used rather than
    per-FR scoped runs. Rationale: per-FR coverage is structurally misleading
    in multi-FR projects — each FR's test only covers its own source files,
    not the other 7 FRs' files, so a per-FR scope would always report
    ~1/N of project coverage. Whole-project coverage is the only signal
    that proves "all source is exercised by tests" (the actual TDD goal).
    Mirrors the TDD-PRECHECK check at line ~4220; advance-phase re-runs the
    same measurement so the manifest's recorded score is verified live.

    """
    layout = ProjectLayout(project)
    src_dir = layout.active_src_dir
    tests_dir = layout.active_test_dir
    if not src_dir.is_dir():
        return None
    if not tests_dir.is_dir():
        return None
    cov_target = layout.get_relative_str(src_dir)
    cmd = [
        sys.executable, "-m", "pytest",
        f"--cov={cov_target}", "--cov-report=term",
        "--tb=no", "-q",
    ]
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True,
            cwd=str(project), timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return None
    except Exception:
        return None
    m = re.search(r"TOTAL\s+\d+\s+\d+\s+(\d+)%", r.stdout)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return None
    return 0.0 if r.returncode == 0 else None
