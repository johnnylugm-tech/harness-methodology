# Sessions Spawn Logger - Auto-record sub-agent dispatch
# v6.60: Added log_update() supporting two-phase write (PENDING -> COMPLETED/FAILED)

import json
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any, List


class SessionsSpawnLogger:
    """
    Auto-record sessions_spawn dispatch events
    """

    LOG_FILENAME = ".methodology/sessions_spawn.log"

    def __init__(self, repo_path: Path):
        self.repo_path = Path(repo_path)
        self.log_path = self.repo_path / self.LOG_FILENAME
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    def _ensure_initialized(self):
        if not self.log_path.exists():
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
        lines = [json.dumps(e, ensure_ascii=False) for e in entries]
        self.log_path.write_text("\n".join(lines) + "\n")

    def log_spawn(self, role: str, task: str, session_id: str,
                  confidence: Optional[int] = None, status: str = "SPAWNED",
                  **kwargs) -> Dict[str, Any]:
        self._ensure_initialized()
        entry = {"timestamp": datetime.now().isoformat(), "role": role,
                 "task": task, "session_id": session_id, "status": status}
        if confidence is not None:
            entry["confidence"] = confidence
        entry.update(kwargs)
        with open(self.log_path, "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        return entry

    def log_update(self, session_id: str, **updates) -> Optional[Dict[str, Any]]:
        entries = self._read_entries()
        for i, entry in enumerate(entries):
            if entry.get("session_id") == session_id:
                entry.update(updates)
                entry["_updated_at"] = datetime.now().isoformat()
                entries[i] = entry
                self._write_entries(entries)
                return entry
        return None

    def validate(self) -> Dict[str, Any]:
        if not self.log_path.exists():
            return {"valid": True, "count": 0, "errors": []}
        content = self.log_path.read_text().strip()
        if not content:
            return {"valid": True, "count": 0, "errors": []}
        lines = [l for l in content.split("\n") if l.strip()]
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
        result = self.validate()
        role_counts, fr_tasks = {}, set()
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


def log_spawn_event(repo_path: Path, role: str, task: str,
                    session_id: str, **kwargs) -> Dict[str, Any]:
    """Convenience function: directly record one dispatch"""
    logger = SessionsSpawnLogger(repo_path)
    return logger.log_spawn(role=role, task=task, session_id=session_id, **kwargs)
