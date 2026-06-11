"""
harness/fr_progress_tracker.py — Persistent FR Gate-1 progress within P3.

Writes ``<project>/.methodology/fr_progress.json`` after every Gate-1 event so
that a new session can resume P3 without re-running completed FRs.

Schema
------
.. code-block:: json

    {
        "phase": 3,
        "updated_at": "2026-05-04T10:30:00Z",
        "frs": {
            "FR-001": {"status": "gate1_pass", "score": 82.5, "phase": 3,
                       "timestamp": "2026-05-04T10:00:00Z"},
            "FR-002": {"status": "gate1_fail", "score": 61.0, "phase": 3,
                       "timestamp": "2026-05-04T10:05:00Z",
                       "reason": "coverage below threshold"}
        }
    }

Usage::

    tracker = FRProgressTracker(project_root)
    tracker.record_gate1_pass("FR-001", score=82.5, phase=3)
    tracker.record_gate1_fail("FR-002", score=61.0, phase=3, reason="low coverage")

    print(tracker.summary())         # "2/5 FRs Gate1 PASS"
    print(tracker.pending(all_frs))  # ["FR-003", "FR-004", "FR-005"]
"""
from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_log = logging.getLogger(__name__)

_STATUS_PASS = "gate1_pass"  # nosec B105
_STATUS_FAIL = "gate1_fail"
_STATUS_PENDING = "pending"

_METHODOLOGY_DIR = ".methodology"
_PROGRESS_FILE = "fr_progress.json"


class FRProgressTracker:
    """
    Persist and query FR Gate-1 progress inside a project's ``.methodology/`` dir.

    Thread-safety: each call to :meth:`record_*` performs an atomic
    read-modify-write on the JSON file.  Concurrent access from multiple
    processes is not protected; that case is not expected in the single-agent
    harness pipeline.

    Parameters
    ----------
    project:
        Absolute path to the project root.
    phase:
        Pipeline phase number (default 3).  Stored in the progress file for
        informational purposes only.
    """

    def __init__(self, project: Path, phase: int = 3) -> None:
        self.project = project
        self.phase = phase
        self._path = project / _METHODOLOGY_DIR / _PROGRESS_FILE

    # ------------------------------------------------------------------
    # Public write API
    # ------------------------------------------------------------------

    def record_gate1_pass(self, fr_id: str, score: float, phase: int | None = None) -> None:
        """Record a Gate-1 PASS for *fr_id*."""
        self._update_fr(
            fr_id,
            status=_STATUS_PASS,
            score=score,
            phase=phase or self.phase,
        )

    def record_gate1_fail(
        self,
        fr_id: str,
        score: float,
        phase: int | None = None,
        reason: str = "",
    ) -> None:
        """Record a Gate-1 FAIL for *fr_id*."""
        self._update_fr(
            fr_id,
            status=_STATUS_FAIL,
            score=score,
            phase=phase or self.phase,
            reason=reason,
        )

    def reset(self) -> None:
        """Delete the progress file (start fresh)."""
        if self._path.exists():
            self._path.unlink()

    def advance_phase(self, phase: int) -> None:
        """Update the top-level ``phase`` field to *phase* in-place."""
        data = self.load()
        data["phase"] = phase
        data["updated_at"] = datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        self._write(data)

    # ------------------------------------------------------------------
    # Public read API
    # ------------------------------------------------------------------

    def load(self) -> dict:
        """Return the raw progress dict (empty scaffold if file missing)."""
        if not self._path.exists():
            return {"phase": self.phase, "updated_at": "", "frs": {}}
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            # Corruption guard: rename the corrupt file aside before the next
            # _update_fr() clobbers it, then return the empty scaffold so the
            # pipeline can continue. An operator can recover the original
            # bytes from the .corrupt.<ts> backup.
            ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            corrupt_path = self._path.with_name(
                f"{self._path.name}.corrupt.{ts}"
            )
            try:
                self._path.rename(corrupt_path)
            except OSError as rename_exc:
                _log.warning(
                    "fr_progress.json corrupt (%s) and rename-to-backup "
                    "failed (%s); next write will overwrite the file.",
                    exc, rename_exc,
                )
            else:
                _log.warning(
                    "fr_progress.json corrupt (%s); preserved at %s. "
                    "load() returns empty scaffold until manual recovery.",
                    exc, corrupt_path,
                )
            return {"phase": self.phase, "updated_at": "", "frs": {}}
        except OSError:
            # Permission denied, IsADirectoryError, disk read error, etc.
            # Distinguish "cannot read progress" from "no progress yet" by
            # re-raising — the caller decides how to handle the failure.
            raise

    def passed_fr_ids(self) -> list[str]:
        """Return sorted list of FR IDs with Gate-1 PASS."""
        data = self.load()
        return sorted(
            fr_id
            for fr_id, entry in data.get("frs", {}).items()
            if entry.get("status") == _STATUS_PASS
        )

    def failed_fr_ids(self) -> list[str]:
        """Return sorted list of FR IDs with Gate-1 FAIL (last attempt)."""
        data = self.load()
        return sorted(
            fr_id
            for fr_id, entry in data.get("frs", {}).items()
            if entry.get("status") == _STATUS_FAIL
        )

    def pending(self, all_fr_ids: list[str]) -> list[str]:
        """
        Return FRs from *all_fr_ids* that have NOT yet achieved Gate-1 PASS.
        Preserves original order.
        """
        passed = set(self.passed_fr_ids())
        return [fr for fr in all_fr_ids if fr not in passed]

    def completion_ratio(self, total: int) -> float:
        """Return fraction of *total* FRs that have Gate-1 PASS (0.0–1.0)."""
        if total <= 0:
            return 0.0
        return len(self.passed_fr_ids()) / total

    def summary(self, total: Optional[int] = None) -> str:
        """
        Human-readable summary string, e.g. ``"3/6 FRs Gate1 PASS"``.

        Parameters
        ----------
        total:
            Expected total FR count.  If ``None``, uses count of all recorded FRs.
        """
        passed = self.passed_fr_ids()
        all_recorded = self.load().get("frs", {})
        denom = total if total is not None else len(all_recorded)
        passed_ids = ",".join(passed[:5])
        if len(passed) > 5:
            passed_ids += f",…+{len(passed) - 5}"
        tail = f" [{passed_ids}]" if passed_ids else ""
        return f"{len(passed)}/{denom} FRs Gate1 PASS{tail}"

    def to_status_string(self, total: Optional[int] = None) -> str:
        """
        Multi-line status string suitable for HANDOVER.md ``current_status``.
        """
        data = self.load()
        frs = data.get("frs", {})
        passed = [fid for fid, e in frs.items() if e.get("status") == _STATUS_PASS]
        failed = [fid for fid, e in frs.items() if e.get("status") == _STATUS_FAIL]
        denom = total if total is not None else len(frs)
        lines = [
            f"{len(passed)}/{denom} FRs Gate1 PASS.",
        ]
        if passed:
            lines.append(f"Passed: {', '.join(sorted(passed))}")
        if failed:
            lines.append(f"Failed (need retry): {', '.join(sorted(failed))}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _update_fr(
        self,
        fr_id: str,
        status: str,
        score: float,
        phase: int,
        reason: str = "",
    ) -> None:
        """Read → modify → write the progress file."""
        # Score validation: NaN/inf would be emitted as the literal
        # `NaN`/`Infinity` tokens by json.dumps, which is invalid per
        # RFC 8259 — the next load() would fall back to the empty
        # scaffold and lose ALL prior progress. Reject early with a
        # clear contract.
        try:
            _score_val = float(score)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"score for {fr_id} must be a real number; got "
                f"{type(score).__name__}: {score!r}"
            ) from exc
        if not math.isfinite(_score_val):
            raise ValueError(
                f"score for {fr_id} must be a finite number; got {_score_val}"
            )

        data = self.load()
        entry: dict = {
            "status": status,
            "score": round(_score_val, 2),
            "phase": phase,
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        if reason:
            entry["reason"] = reason
        data.setdefault("frs", {})[fr_id] = entry
        # Top-level `phase` is owned by advance_phase() — do NOT
        # clobber it here. For a fresh tracker, load() returns
        # {"phase": self.phase, ...} so the file already has the
        # right initial value. Regressing this would undo
        # advance_phase(4) on the next record_gate1_pass call.
        data["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        self._write(data)

    def _write(self, data: dict) -> None:
        # Atomic write (CV-3): tempfile + os.replace so a mid-write crash
        # cannot truncate fr_progress.json. Falls back to direct write if
        # core.atomic_io is unavailable (e.g. partial install) or if
        # atomic_write itself raises a runtime error (TypeError for
        # non-serialisable data, ValueError for bad values, OSError for
        # disk full / file lock). Without the broader catch, those
        # runtime failures would propagate and crash the caller, leaving
        # the file unmodified.
        self._path.parent.mkdir(parents=True, exist_ok=True)
        try:
            from core.atomic_io import atomic_write_json  # type: ignore[import-not-found]
            atomic_write_json(self._path, data, ensure_ascii=False)
        except ImportError:  # pragma: no cover  (graceful degrade)
            self._path.write_text(
                json.dumps(data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except (TypeError, ValueError, OSError) as exc:
            _log.warning(
                "atomic_write_json failed (%s: %s); falling back to "
                "non-atomic write per CV-3 contract.",
                type(exc).__name__, exc,
            )
            self._path.write_text(
                json.dumps(data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
