# TODO: rewrite for Claude Code session tracking
# Sessions Spawn Logger - 自動記錄 sub-agent 派遣
# 解決方案：SubagentIsolator.spawn() hook 自動寫入 log
# v6.60: 加入 log_update() 支援兩階段寫入（PENDING → COMPLETED/FAILED）

import json
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, Any, List


class SessionsSpawnLogger:
    """
    自動記錄 sessions_spawn 派遣事件
    
    使用方式：
        from sessions_spawn_logger import SessionsSpawnLogger
        
        logger = SessionsSpawnLogger(repo_path)
        
        # Phase 1: 記錄 PENDING
        entry = logger.log_spawn(
            role="developer",
            task="FR-01",
            session_id="xxx",
            session_key="sub_developer_abc123",
            status="PENDING"
        )
        
        # Phase 2: 更新為 COMPLETED
        logger.log_update(
            session_id="xxx",
            session_key="sub_developer_abc123",
            status="COMPLETED",
            confidence=9,
            duration_seconds=125.5
        )
    """
    
    LOG_FILENAME = ".methodology/sessions_spawn.log"
    
    def __init__(self, repo_path: Path):
        self.repo_path = Path(repo_path)
        self.log_path = self.repo_path / self.LOG_FILENAME
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
    
    def _ensure_initialized(self):
        """確保 log 檔案存在"""
        if not self.log_path.exists():
            self.log_path.write_text("")
    
    def _read_entries(self) -> List[Dict[str, Any]]:
        """讀取所有 log entries"""
        if not self.log_path.exists():
            return []
        content = self.log_path.read_text().strip()
        if not content:
            return []
        entries = []
        for line in content.split("\n"):
            line = line.strip()
            if not line:
                continue
            if line.startswith(","):
                line = line[1:]
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                pass
        return entries
    
    def _write_entries(self, entries: List[Dict[str, Any]]):
        """寫回所有 entries（瓦片式覆寫）"""
        lines = [json.dumps(e, ensure_ascii=False) for e in entries]
        self.log_path.write_text("\n".join(lines) + "\n")
    
    def log_spawn(
        self,
        role: str,
        task: str,
        session_id: str,
        confidence: Optional[int] = None,
        status: str = "SPAWNED",
        **kwargs
    ) -> Dict[str, Any]:
        """
        記錄一次 sub-agent 派遣（Phase 1：PENDING）
        """
        self._ensure_initialized()
        
        entry = {
            "timestamp": datetime.now().isoformat(),
            "role": role,
            "task": task,
            "session_id": session_id,
            "status": status,
        }
        
        if confidence is not None:
            entry["confidence"] = confidence
        
        entry.update(kwargs)
        
        with open(self.log_path, "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        
        return entry
    
    def log_update(
        self,
        session_id: str,
        **updates
    ) -> Optional[Dict[str, Any]]:
        """
        更新已存在的 entry（Phase 2：COMPLETED/FAILED）
        """
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
        """驗證 log 格式"""
        if not self.log_path.exists():
            return {"valid": True, "count": 0, "errors": []}
        
        content = self.log_path.read_text().strip()
        if not content:
            return {"valid": True, "count": 0, "errors": []}
        
        lines = [l for l in content.split("\n") if l.strip()]
        errors = []
        count = 0
        
        for i, line in enumerate(lines):
            try:
                if line.startswith(","):
                    line = line[1:]
                entry = json.loads(line)
                
                for field in ["role", "session_id"]:
                    if field not in entry:
                        errors.append(f"Line {i+1}: missing {field}")
                
                count += 1
            except json.JSONDecodeError as e:
                errors.append(f"Line {i+1}: {e}")
        
        return {
            "valid": len(errors) == 0,
            "count": count,
            "errors": errors
        }
    
    def get_summary(self) -> Dict[str, Any]:
        """取得 log 摘要"""
        result = self.validate()
        
        role_counts = {}
        fr_tasks = set()
        status_counts = {"PENDING": 0, "SPAWNED": 0, "COMPLETED": 0, "FAILED": 0}
        
        entries = self._read_entries()
        for entry in entries:
            role = entry.get("role", "unknown")
            role_counts[role] = role_counts.get(role, 0) + 1
            
            task = entry.get("task", "")
            if task:
                fr_tasks.add(task.split()[0] if " " in task else task)
            
            status = entry.get("status", "")
            if status in status_counts:
                status_counts[status] += 1
        
        return {
            "total_entries": result["count"],
            "role_counts": role_counts,
            "fr_tasks": sorted(fr_tasks),
            "status_counts": status_counts,
            "valid": result["valid"]
        }


def log_spawn_event(
    repo_path: Path,
    role: str,
    task: str,
    session_id: str,
    **kwargs
) -> Dict[str, Any]:
    """便利函數：直接記錄一次派遣"""
    logger = SessionsSpawnLogger(repo_path)
    return logger.log_spawn(role=role, task=task, session_id=session_id, **kwargs)
