# Sessions Spawn Logger - Auto-record sub-agent dispatch
# v6.60: Added log_update() supporting two-phase write (PENDING -> COMPLETED/FAILED)
# v6.61: Atomic full-rewrite + cross-process file lock (CV-3 / SG-3 / SG-12).

import json
from pathlib import Path
from core.utils.timefmt import utc_now_iso
from typing import Optional, Dict, Any, List

try:
    from core.atomic_io import atomic_write_text, file_lock  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover  (graceful degrade if utility missing)
    atomic_write_text = None  # type: ignore[assignment]
    file_lock = None  # type: ignore[assignment]


class SessionsSpawnLogger:
    """
    Auto-record sessions_spawn dispatch events
    """

    LOG_FILENAME = ".methodology/sessions_spawn.log"

    def __init__(self, repo_path: Path):
        self.repo_path = Path(repo_path)
        self.log_path = self.repo_path / self.LOG_FILENAME
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    def _lock_path(self) -> Path:
        return self.log_path.with_suffix(self.log_path.suffix + ".lock")

    def _ensure_initialized(self):
        if not self.log_path.exists():
            if (atomic_write_text is not None):
                atomic_write_text(self.log_path, "")
            else:
                self.log_path.write_text("")

    def _read_entries(self) -> List[Dict[str, Any]]:
        if not self.log_path.exists():
            return []
        content = self.log_path.read_text().strip()
        if not content:
            return []
        entries = []
        for line in content.split("\n"):
            line = line.strip().lstrip(",")
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                pass
        return entries

    def _write_entries(self, entries: List[Dict[str, Any]]):
        """Atomic full-rewrite (CV-3): tempfile + os.replace so a crash
        mid-write cannot truncate the log."""
        lines = [json.dumps(e, ensure_ascii=False) for e in entries]
        content = "\n".join(lines) + "\n"
        if (atomic_write_text is not None):
            atomic_write_text(self.log_path, content)
        else:
            self.log_path.write_text(content)

    def log_spawn(self, role: str, task: str, session_id: str,
                  confidence: Optional[int] = None, status: str = "SPAWNED",
                  **kwargs) -> Dict[str, Any]:
        """Record a new agent spawn event with role and task attribution.

        Standard kwargs written to each entry:
            phase: int — methodology phase (1-8)
            fr_id: str | None — functional requirement id (e.g. "FR-01")
            regression_flags: dict — sub-agent regression guard output
            error_output: str — truncated stderr/stdout from AgentSpawner when
                status is ERROR / TIMEOUT / REGRESSION_GUARD (capped ~500 chars).
                Without this, ERROR sessions only showed status="ERROR" +
                session_id="" with no clue why spawn failed.
            exit_code: int | None — subprocess returncode on non-zero exit.
                None for complete / SPAWNED / PENDING.
            total_cost_usd, num_turns, duration_api_ms: float/int | None —
                lifted directly from the claude -p --output-format json
                envelope (Round 14 站0). Only present when that envelope was
                actually produced — absent on TIMEOUT / non-zero-exit /
                non-JSON-stdout entries, and absent entirely on log lines
                written before this station.
            usage: dict | None — envelope's token counts (input_tokens,
                output_tokens, cache_read_input_tokens,
                cache_creation_input_tokens), same presence rule as above.

        Guarded by cross-process file lock (SG-3): parallel log_spawn +
        log_update calls cannot interleave and lose entries.
        """
        self._ensure_initialized()
        entry: dict[str, Any] = {"timestamp": utc_now_iso(), "role": role,
                                "task": task, "session_id": session_id, "status": status}
        if confidence is not None:
            entry["confidence"] = confidence
        entry.update(kwargs)
        if atomic_write_text is not None and file_lock is not None:
            with file_lock(self._lock_path()):
                with open(self.log_path, "a") as f:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        else:
            with open(self.log_path, "a") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return entry

    def log_update(self, session_id: str, **updates) -> Optional[Dict[str, Any]]:
        """Update an entry by session_id. Read-modify-write is serialized via
        the same lock as log_spawn so parallel callers cannot lose entries.
        """
        if atomic_write_text is not None and file_lock is not None:
            with file_lock(self._lock_path()):
                return self._log_update_unlocked(session_id, updates)
        return self._log_update_unlocked(session_id, updates)

    def _log_update_unlocked(self, session_id: str, updates: dict) -> Optional[Dict[str, Any]]:
        entries = self._read_entries()
        for i, entry in enumerate(entries):
            if entry.get("session_id") == session_id:
                entry.update(updates)
                entry["_updated_at"] = utc_now_iso()
                entries[i] = entry
                self._write_entries(entries)
                return entry
        return None

    def validate(self) -> Dict[str, Any]:
        """Validate all session records for consistency (ID uniqueness, task gaps)."""
        if not self.log_path.exists():
            return {"valid": True, "count": 0, "errors": []}
        content = self.log_path.read_text().strip()
        if not content:
            return {"valid": True, "count": 0, "errors": []}
        lines = [line for line in content.split("\n") if line.strip()]
        errors, count = [], 0
        for i, line in enumerate(lines):
            try:
                entry = json.loads(line.lstrip(","))
                for field in ["role", "session_id"]:
                    if field not in entry:
                        errors.append(f"Line {i+1}: missing {field}")
                count += 1
            except json.JSONDecodeError as e:
                errors.append(f"Line {i+1}: {e}")
        return {"valid": len(errors) == 0, "count": count, "errors": errors}

    def get_summary(self) -> Dict[str, Any]:
        """Return aggregate session stats: counts, FR tasks, status distribution."""
        result = self.validate()
        role_counts: dict[str, int] = {}
        fr_tasks: set[str] = set()
        status_counts = {"PENDING": 0, "SPAWNED": 0, "COMPLETED": 0, "FAILED": 0}
        for entry in self._read_entries():
            role = entry.get("role", "unknown")
            role_counts[role] = role_counts.get(role, 0) + 1
            task = entry.get("task", "")
            if task:
                fr_tasks.add(task.split()[0] if " " in task else task)
            status = entry.get("status", "")
            if status in status_counts:
                status_counts[status] += 1
        return {"total_entries": result["count"], "role_counts": role_counts,
                "fr_tasks": sorted(fr_tasks), "status_counts": status_counts,
                "valid": result["valid"]}


    def log_turn(self, fr_id: str, turn_number: int, session_id: str,
                 status: str = "COMPLETED", **kwargs) -> Dict[str, Any]:
        """Record a turn-based execution entry (Item 7)."""
        return self.log_spawn(
            role="developer",
            task=f"{fr_id} turn {turn_number}",
            session_id=session_id,
            status=status,
            fr_id=fr_id,
            turn_number=turn_number,
            **kwargs,
        )


def log_spawn_event(repo_path: Path, role: str, task: str,
                    session_id: str, **kwargs) -> Dict[str, Any]:
    """Convenience function: directly record one dispatch"""
    logger = SessionsSpawnLogger(repo_path)
    return logger.log_spawn(role=role, task=task, session_id=session_id, **kwargs)
