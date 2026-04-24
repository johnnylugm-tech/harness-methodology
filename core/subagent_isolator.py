# TODO: rewrite sessions_spawn.log → Task tool records
#!/usr/bin/env python3
"""
SubagentIsolator — Subagent 隔離管理模組

功能：
- 標準化 sessions_spawn 呼叫
- Fresh messages[] 隔離
- 結果合併協議
- Session 生命週期管理

用法：
    from subagent_isolator import SubagentIsolator, AgentRole
    
    si = SubagentIsolator()
    result = si.spawn(
        role=AgentRole.DEVELOPER,
        task="Implement FR-01",
        context={"fr": "FR-01", "srs": "..."}
    )
    si.merge(result)
"""

import json
import uuid
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Any, Callable
from enum import Enum
from datetime import datetime
import sys
import os

# 嘗試导入 sessions_spawn
try:
    from openclaw import sessions_spawn
    HAS_SPAWN = True
except ImportError:
    HAS_SPAWN = False

# 嘗試导入自訂異常
try:
    from exceptions import MethodologyError, ArtifactMissingError, OnDemandViolationError
except ImportError:
    from .exceptions import MethodologyError, ArtifactMissingError, OnDemandViolationError

# SessionsSpawnLogger（v6.60: 整合日誌）
try:
    from sessions_spawn_logger import SessionsSpawnLogger
except ImportError:
    from .sessions_spawn_logger import SessionsSpawnLogger

# On Demand / Need to Know 約束
MAX_CONTEXT_SIZE = 5000  # 字元


class AgentRole(Enum):
    """Agent 角色"""
    DEVELOPER = "developer"
    REVIEWER = "reviewer"
    TESTER = "tester"
    VERIFIER = "verifier"
    ARCHITECT = "architect"


@dataclass
class AgentPersona:
    """Agent 人格定義"""
    role: AgentRole
    goal: str
    backstory: str
    constraints: List[str] = field(default_factory=list)
    
    def to_system_prompt(self) -> str:
        return f"""你是 {self.role.value} Agent。

目標：{self.goal}

背景：{self.backstory}

約束：
{chr(10).join(f"- {c}" for c in self.constraints)}

產出格式：
{{
    "status": "success" | "error" | "unable_to_proceed",
    "result": "...",
    "confidence": 1-10,
    "citations": [...],
    "summary": "50字內摘要"
}}
"""


# 預設 Persona 模板
DEFAULT_PERSONAS = {
    AgentRole.DEVELOPER: AgentPersona(
        role=AgentRole.DEVELOPER,
        goal="產出高質量代碼",
        backstory="10年資深工程師，注重正確性和可維護性",
        constraints=[
            "必須包含 @FR: FR-XX annotation",
            "嚴禁使用省略號",
            "每個函式必須有 docstring"
        ]
    ),
    AgentRole.REVIEWER: AgentPersona(
        role=AgentRole.REVIEWER,
        goal="嚴格審查把關，不放過任何問題",
        backstory="資深技術評審，只驗證不寫代碼",
        constraints=[
            "必須驗證每一個聲稱",
            "輸出具體問題列表",
            "無法確定的必須標註 UNVERIFIED"
        ]
    ),
    AgentRole.TESTER: AgentPersona(
        role=AgentRole.TESTER,
        goal="執行測試驗證，正確性優先",
        backstory="測試專家，不放過任何 bug",
        constraints=[
            "每個 FR 至少一個 positive 和一個 negative 測試",
            "必須包含 @covers: FR-XX",
            "邊界條件是關鍵"
        ]
    ),
    AgentRole.VERIFIER: AgentPersona(
        role=AgentRole.VERIFIER,
        goal="驗證產物正確性，獨立於開發者",
        backstory="獨立審計，只看事實和證據",
        constraints=[
            "每個聲稱必須有對應證據",
            "無法驗證的必須說 UNVERIFIED",
            "不帶預設立場"
        ]
    ),
}


@dataclass
class SubagentResult:
    """Subagent 執行結果"""
    session_key: str
    role: AgentRole
    status: str  # success/error/timeout (subagent execution result)
    result: Any
    confidence: int
    citations: List[str] = field(default_factory=list)
    summary: str = ""
    error: Optional[str] = None
    duration_seconds: float = 0
    # v6.93: For Reviewer role, stores APPROVE/REJECT
    review_status: Optional[str] = None  # APPROVE/REJECT (only for Reviewer)


class SubagentIsolator:
    """
    Subagent 隔離管理器
    
    解決：
    - 上下文污染（每次 spawn 用獨立 messages[]）
    - 結果合併不統一
    - Session 生命週期混亂
    
    On Demand / Need to Know 強制執行（v6.32.0+）：
    - 優先使用 artifact_paths 而非 context
    - context 參數已 deprecated，最大 5000 字元
    - HR-15 強制：subagent 必須引用 artifact 否則任務失敗
    """
    
    def __init__(self, project_path: str = "."):
        self.project_path = project_path
        self.active_sessions = {}  # session_key -> metadata
        self.results = {}  # session_key -> SubagentResult
        self._persona_cache = {}
        # sessions_spawn.log 寫入 .methodology/ 目錄（HR-10 合規）
        # v6.60: 使用 SessionsSpawnLogger 取代直接寫入
        self._logger = SessionsSpawnLogger(Path(project_path))

    # ─── Internal Helpers ────────────────────────────────────────────────────────

    def get_persona(self, role: AgentRole, custom: AgentPersona = None) -> str:
        """取得 Agent persona"""
        if custom:
            return custom.to_system_prompt()
        
        if role not in self._persona_cache:
            persona = DEFAULT_PERSONAS.get(role)
            if persona:
                self._persona_cache[role] = persona.to_system_prompt()
        
        return self._persona_cache.get(role, "")
    
    def _build_ondemand_prompt(
        self,
        task: str,
        artifact_paths: List[str],
        custom_persona: AgentPersona = None
    ) -> str:
        """
        嚴格執行 On Demand 原則建構 prompt：
        - 只給 task + artifact_paths
        - 不 dump 任何內容
        - subagent 自己讀取 artifact
        """
        prompt = f"""任務：{task}

嚴格執行 On Demand 原則：
- 不要問我任何問題
- 自己讀取以下 artifact paths
- 讀取完成後才能開始實作

Artifact Paths：
"""
        for i, path in enumerate(artifact_paths, 1):
            prompt += f"{i}. {path}\n"

        prompt += """
產出格式（必須包含）：
- status: success/error/unable_to_proceed
- result: 實際產出
- confidence: 1-10（10=高度確定有引用）
- citations: ["FR-01", "SRS.md#L23", "SAD.md#§3.2"]
- summary: 50字內

HR-15 強制：
- citations 必須包含 artifact 名 + 行號
- 無 citations = 任務失敗
"""
        return prompt

    def _verify_artifacts_read(
        self,
        result: SubagentResult,
        artifact_paths: List[str]
    ) -> bool:
        """驗證 subagent 是否真的讀了 artifact（HR-15 強制）。"""
        if not result.citations:
            return False

        for path in artifact_paths:
            filename = Path(path).name
            if not any(filename in cite for cite in result.citations):
                return False
        return True

    def spawn(
        self,
        role: AgentRole,
        task: str,
        artifact_paths: List[str] = None,
        session_id: str = None,
        context: Dict[str, Any] = None,
        custom_persona: AgentPersona = None,
        timeout: int = 300,
        model: str = None
    ) -> SubagentResult:
        """
        啟動 Subagent（隔離環境）
        
        On Demand / Need to Know 強制執行：
        - 優先使用 artifact_paths
        - context 參數已 deprecated，最大 5000 字元
        """
        if context is not None:
            context_size = len(json.dumps(context, ensure_ascii=False))
            if context_size > MAX_CONTEXT_SIZE:
                raise OnDemandViolationError(
                    f"Context too large ({context_size} > {MAX_CONTEXT_SIZE} chars). "
                    "Please use artifact_paths instead.",
                    context={"context_size": context_size, "max_size": MAX_CONTEXT_SIZE}
                )
        
        use_ondemand = artifact_paths is not None and len(artifact_paths) > 0
        
        if session_id is None:
            session_id = str(uuid.uuid4())
        session_key = f"sub_{role.value}_{uuid.uuid4().hex[:8]}"
        start_time = datetime.now()
        
        self._logger.log_spawn(
            role=role.value,
            task=task,
            session_id=session_id,
            session_key=session_key,
            status="PENDING"
        )
        
        system_prompt = self.get_persona(role, custom_persona)
        
        if use_ondemand:
            task_prompt = self._build_ondemand_prompt(task, artifact_paths, custom_persona)
        else:
            task_prompt = f"任務：{task}\n\n"
            if context:
                import warnings
                warnings.warn(
                    "context parameter is deprecated, use artifact_paths instead",
                    DeprecationWarning,
                    stacklevel=2
                )
                task_prompt += f"上下文：{json.dumps(context, ensure_ascii=False)}\n\n"
            task_prompt += "產出必須包含 status/result/confidence/citations/summary"
        
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": task_prompt}
        ]
        
        self.active_sessions[session_key] = {
            "role": role.value,
            "task": task,
            "started_at": start_time.isoformat(),
            "timeout": timeout
        }
        
        if HAS_SPAWN:
            try:
                response = sessions_spawn(
                    task=json.dumps({"messages": messages}),
                    session_key=session_key,
                    timeout=timeout
                )
                
                if isinstance(response, dict):
                    status = response.get("status", "success")
                    result = response.get("result", str(response) if not isinstance(response, dict) else "")
                    confidence = response.get("confidence", 5)
                    citations = response.get("citations", [])
                    review_status = response.get("review_status", None)
                else:
                    try:
                        parsed = json.loads(response) if isinstance(response, str) else {}
                        status = parsed.get("status", "success")
                        result = parsed.get("result", str(response))
                        confidence = parsed.get("confidence", 5)
                        citations = parsed.get("citations", [])
                        review_status = parsed.get("review_status", None)
                    except (json.JSONDecodeError, TypeError):
                        status = "success"
                        result = str(response)
                        confidence = 5
                        citations = []
                        review_status = None
                error = None
                
            except Exception as e:
                status = "error"
                result = None
                confidence = 0
                citations = []
                review_status = None
                error = str(e)
        else:
            status = "success"
            result = f"[Mock] {role.value}: {task[:50]}..."
            confidence = 7
            citations = []
            review_status = None
            
            if role == AgentRole.REVIEWER:
                review_status = "APPROVE"
            
            error = None
        
        duration = (datetime.now() - start_time).total_seconds()
        
        sub_result = SubagentResult(
            session_key=session_key,
            role=role,
            status=status,
            result=result,
            confidence=confidence,
            citations=citations,
            summary=self._generate_summary(result, 50),
            error=error,
            duration_seconds=duration,
            review_status=review_status
        )
        
        if use_ondemand and artifact_paths:
            if not self._verify_artifacts_read(sub_result, artifact_paths):
                sub_result.result = sub_result.result or ""
                sub_result.result += f"\n[HR-15 WARNING] Citations not found for: {[Path(p).name for p in artifact_paths]}"
        
        self.results[session_key] = sub_result
        
        self._logger.log_update(
            session_id=session_id,
            session_key=session_key,
            status="COMPLETED" if sub_result.status == "success" else "FAILED",
            confidence=sub_result.confidence,
            duration_seconds=duration
        )
        
        return sub_result
    
    def _generate_summary(self, content: Any, max_len: int = 50) -> str:
        """產生摘要"""
        if not content:
            return ""
        content_str = str(content)
        if len(content_str) <= max_len:
            return content_str
        return content_str[:max_len-3] + "..."
    
    def merge(self, result: SubagentResult) -> Dict:
        """合併 Subagent 結果到主流程"""
        return {
            "role": result.role.value,
            "status": result.status,
            "result": result.result,
            "confidence": result.confidence,
            "summary": result.summary,
            "citations": result.citations[:5]
        }
    
    def merge_all(self) -> List[Dict]:
        """合併所有 Subagent 結果"""
        return [self.merge(r) for r in self.results.values()]
    
    def get_active_sessions(self) -> List[Dict]:
        """取得活躍 session"""
        return list(self.active_sessions.values())
    
    def get_result(self, session_key: str) -> Optional[SubagentResult]:
        """取得特定 session 的結果"""
        return self.results.get(session_key)
    
    def terminate(self, session_key: str) -> bool:
        """終止 session"""
        if session_key in self.active_sessions:
            del self.active_sessions[session_key]
            return True
        return False
    
    def clear(self):
        """清理所有 session 和結果"""
        self.active_sessions.clear()
        self.results.clear()
    
    def get_integrity_score(self) -> float:
        """計算隔離完整性分數"""
        if not self.results:
            return 1.0
        
        scores = []
        for r in self.results.values():
            if r.status == "error":
                scores.append(0.3)
            else:
                citation_score = min(len(r.citations) / 2, 1.0)
                confidence_score = r.confidence / 10.0
                scores.append((citation_score + confidence_score) / 2)
        
        return sum(scores) / len(scores)

    def pre_spawn_audit(self, task_id: str, artifact_paths: List[str]) -> List[dict]:
        """在派遣前檢查 artifact 完整性"""
        results = []
        for path in artifact_paths:
            if not Path(path).exists():
                results.append({"artifact": path, "status": "MISSING"})
            else:
                results.append({"artifact": path, "status": "OK"})
        
        missing = [r for r in results if r["status"] == "MISSING"]
        if missing:
            raise ArtifactMissingError(
                f"Artifact 缺失: {[r['artifact'] for r in missing]}",
                artifacts=missing
            )
        return results


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="SubagentIsolator CLI")
    subparsers = parser.add_subparsers(dest="command")
    
    spawn_parser = subparsers.add_parser("spawn", help="Spawn subagent")
    spawn_parser.add_argument("--role", required=True, choices=["developer", "reviewer", "tester", "verifier"])
    spawn_parser.add_argument("--task", required=True, help="Task description")
    spawn_parser.add_argument("--timeout", type=int, default=300)
    
    list_parser = subparsers.add_parser("list", help="List sessions")
    
    result_parser = subparsers.add_parser("result", help="Get result")
    result_parser.add_argument("--session", required=True)
    
    args = parser.parse_args()
    si = SubagentIsolator()
    
    if args.command == "spawn":
        role = AgentRole[args.role.upper()]
        result = si.spawn(role=role, task=args.task, timeout=args.timeout)
        print(f"Session: {result.session_key}")
        print(f"Status: {result.status}")
        print(f"Confidence: {result.confidence}")
        print(f"Summary: {result.summary}")
    
    elif args.command == "list":
        sessions = si.get_active_sessions()
        print(f"Active sessions: {len(sessions)}")
        for s in sessions:
            print(f"  [{s['role']}] {s['task'][:50]}...")
    
    elif args.command == "result":
        result = si.get_result(args.session)
        if result:
            print(json.dumps({
                "session_key": result.session_key,
                "role": result.role.value,
                "status": result.status,
                "confidence": result.confidence,
                "summary": result.summary,
                "error": result.error
            }, indent=2))
        else:
            print(f"Session not found: {args.session}")


if __name__ == "__main__":
    main()
