"""Self-heal `state.json.phase_completed[N].sha` when it is no longer
ancestor of HEAD (read-time repair of an emergent invariant that
push-checkpoint's pre-push HEAD write can break between state.json
write and the commit that follows it — e.g. an orchestrator running
`git reset HEAD~N` mid-orchestration).

Scope: P1/P2 entry. `_verify_entry_gate` runs the SHA contract only
for phases 2 and 3, so the writer we are repairing (push-checkpoint,
P1/P2 only) is the only one whose invariants this helper can
re-establish. P3+ entries are written by `cmd_advance_phase` AFTER
its own commit, so the race window there is closed by construction;
generalising this helper to P3+ would require a shared resolver
also used by `_fr_step_lineage_boundary` (deferred).

Repair protocol (matches the Plan-agent verdict):

1. Capture explicit HEAD SHA (so a concurrent reset does not move
   the reference we search from).
2. Search ONLY that captured HEAD for the canonical
   `phase{prev}(review-complete)` (and the legacy
   `phase{prev}(human-review)`) marker, returning full SHA via
   `%H`. No `--all`, no reflog walk: a truly reset-away lineage
   cannot reach a non-HEAD ancestor and must NOT be returned.
3. Verify the candidate is an ancestor of the captured HEAD
   (technically redundant after a correctly scoped `git log <ref>`,
   but defence-in-depth against future command drift).
4. Acquire `file_lock(state_lock_path)`, reload state.json INSIDE
   the lock, compare the current entry.sha to the originally
   observed bad SHA — if another process already installed a valid
   SHA, return without a duplicate recovery event; if it changed
   to a different invalid value, retry resolution; if it is still
   the same bad SHA, merge the repair into the freshly loaded
   object and atomic_write_json.
5. Append an event to top-level `phase_completed_recovery_log`
   (independent of `phase_completed[N]`) so the audit survives
   cmd_advance_phase's full-entry replacement at
   phase_cmds.py:869-883. Preserve the original entry fields
   (timestamp, enforcer_sha, enforcer_surface) — recovery has its
   own `recovered_at` timestamp and does not mutate the
   completion-time provenance.
6. The verifier does NOT stage or commit. The next commit
   naturally carries the corrected state.json.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from core.atomic_io import atomic_write_json, file_lock, state_lock_path
from core.harness_provenance import enforcer_sha
from core.state_io import load_state
from core.utils.timefmt import utc_now_iso


_LOG_KEY = "phase_completed_recovery_log"

_MARKERS = (
    "phase{prev}(review-complete)",
    "phase{prev}(human-review)",
)


def _capture_head(project: Path) -> str:
    r = subprocess.run(
        ["git", "-C", str(project), "rev-parse", "HEAD"],
        capture_output=True, text=True, timeout=10,
    )
    if r.returncode != 0 or not r.stdout.strip():
        return ""
    return r.stdout.strip()


def _search_marker(project: Path, captured_head: str, marker: str) -> str:
    """Return full SHA of the most recent commit reachable from
    `captured_head` whose SUBJECT contains `marker`. Empty string on
    miss.

    `git log --grep` matches the entire commit message (subject AND
    body) — to enforce subject-only we first narrow candidates with
    `--grep` (no `--all`, restricting to `captured_head` reachability
    so a reset-away lineage cannot match), then verify the marker
    appears in `%s` (subject). The two-step filter is defence-in-depth
    against future `--grep` flag drift (e.g. `--regexp-ignore-case`,
    `-E`) introducing body matches."""
    r = subprocess.run(
        [
            "git", "-C", str(project), "log",
            captured_head,
            "--grep", marker,
            "--pretty=format:%H",
        ],
        capture_output=True, text=True, timeout=10,
    )
    if r.returncode != 0:
        return ""
    candidates = [s.strip() for s in r.stdout.splitlines() if s.strip()]
    for sha in candidates:
        sub_r = subprocess.run(
            ["git", "-C", str(project), "log", "-1", sha,
             "--pretty=format:%s"],
            capture_output=True, text=True, timeout=10,
        )
        if sub_r.returncode == 0 and marker in sub_r.stdout:
            return sha
    return ""


def _is_ancestor(project: Path, candidate: str, head: str) -> bool:
    r = subprocess.run(
        ["git", "-C", str(project), "merge-base", "--is-ancestor",
         candidate, head],
        capture_output=True, text=True, timeout=10,
    )
    return r.returncode == 0


def _attempt_resolve(project: Path, prev: int, captured_head: str) -> str:
    """Pick the first marker that yields a valid, ancestor-of-captured-HEAD
    candidate. Returns empty string on miss."""
    for marker_template in _MARKERS:
        marker = marker_template.format(prev=prev)
        candidate = _search_marker(project, captured_head, marker)
        if candidate and _is_ancestor(project, candidate, captured_head):
            return candidate
    return ""


def _merge_repair(
    state: Dict[str, Any],
    prev: int,
    observed_bad_sha: str,
    healed_sha: str,
    observed_head: str,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Build the updated phase_completed[prev] entry and the recovery
    event. Mutates `state` (the freshly-loaded dict inside the lock)
    in place and returns the new entry dict and the appended event."""
    phase_completed = state.setdefault("phase_completed", {})
    entry = phase_completed.get(str(prev))
    if not isinstance(entry, dict):
        entry = {}
    # Preserve original completion-time fields; record only the
    # recovered-from pointer and the recovery timestamp.
    new_entry = dict(entry)
    new_entry["sha"] = healed_sha
    if "timestamp" not in new_entry:
        new_entry["timestamp"] = utc_now_iso()
    new_entry["recovered_from_sha"] = observed_bad_sha
    new_entry["recovered_at"] = utc_now_iso()
    new_entry["recovery_marker"] = f"phase{prev}(review-complete)"
    new_entry["enforcer_sha"] = enforcer_sha()
    phase_completed[str(prev)] = new_entry

    event = {
        "phase": prev,
        "from_sha": observed_bad_sha,
        "to_sha": healed_sha,
        "at": utc_now_iso(),
        "observed_head": observed_head,
        "marker": f"phase{prev}(review-complete)",
        "enforcer_sha": enforcer_sha(),
    }
    log = state.setdefault(_LOG_KEY, [])
    if not isinstance(log, list):
        log = []
        state[_LOG_KEY] = log
    log.append(event)
    state[_LOG_KEY] = log

    return new_entry, event


def try_recover_dangling_phase_completed(
    project: Path, prev: int, observed_bad_sha: str,
) -> Optional[Dict[str, Any]]:
    """Attempt to repair `state.json.phase_completed[prev].sha` when
    the recorded SHA is not an ancestor of HEAD.

    Returns a dict with the new sha + audit info on success, None on
    no-match. Never raises: a repair failure must not block the
    caller's gate fallback chain."""
    captured_head = _capture_head(project)
    if not captured_head:
        return None

    healed_sha = _attempt_resolve(project, prev, captured_head)
    if not healed_sha:
        return None

    state_path = project / ".methodology" / "state.json"
    try:
        with file_lock(state_lock_path(project)):
            sd = load_state(project, lenient=True)
            pc = sd.get("phase_completed", {})
            current_entry = pc.get(str(prev)) if isinstance(pc, dict) else None
            current_sha = (
                current_entry.get("sha")
                if isinstance(current_entry, dict) else None
            )
            if current_sha == healed_sha:
                # Another process already installed the repair.
                return {
                    "phase": prev,
                    "from_sha": observed_bad_sha,
                    "to_sha": healed_sha,
                    "at": utc_now_iso(),
                    "observed_head": captured_head,
                    "marker": f"phase{prev}(review-complete)",
                    "already_healed": True,
                }
            # If the entry has changed to a different invalid value
            # since we observed it, return without overwriting; the
            # caller's gate will hard-fail and surface the issue.
            if current_sha and current_sha != observed_bad_sha:
                return None
            _merge_repair(
                sd, prev, observed_bad_sha, healed_sha, captured_head,
            )
            atomic_write_json(state_path, sd)
    except Exception as _exc:  # pylint: disable=broad-exception-caught
        # Repair is best-effort; do not block the caller's gate.
        # Surface the failure to stderr so the exception-swallow ratchet
        # can distinguish "silent pass" from "best-effort and logged".
        print(f"[WARN] phase_completed_recovery: repair failed: "
              f"{type(_exc).__name__}: {_exc}", file=sys.stderr)
        return None

    return {
        "phase": prev,
        "from_sha": observed_bad_sha,
        "to_sha": healed_sha,
        "at": utc_now_iso(),
        "observed_head": captured_head,
        "marker": f"phase{prev}(review-complete)",
        "already_healed": False,
    }
