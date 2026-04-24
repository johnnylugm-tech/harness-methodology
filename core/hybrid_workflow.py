# TODO: rewrite sessions_spawn → Task tool
#!/usr/bin/env python3
"""
Hybrid Workflow - 智慧分流工作流

三種模式：
- OFF: 單一 Agent
- HYBRID: 智慧分流（小改自動，大改審查）
- ON: 強制 A/B 審查
"""

from enum import Enum
from dataclasses import dataclass
from typing import Optional, Callable

class WorkflowMode(Enum):
    OFF = "off"
    HYBRID = "hybrid"
    ON = "on"

class ChangeType(Enum):
    SMALL = "small"
    LARGE = "large"

@dataclass
class ChangeAnalysis:
    type: ChangeType
    lines_changed: int
    files_affected: int
    is_security_related: bool
    is_new_feature: bool
    reason: str

class HybridWorkflow:
    def __init__(
        self,
        mode: WorkflowMode = WorkflowMode.HYBRID,
        small_change_threshold: int = 10,
        large_change_threshold: int = 30
    ):
        self.mode = mode
        self.small_threshold = small_change_threshold
        self.large_threshold = large_change_threshold
        self.stats = {
            "auto_approved": 0,
            "review_required": 0,
            "total_tasks": 0
        }
    
    def analyze_change(self, diff: str) -> ChangeAnalysis:
        lines = diff.split('\n')
        added_lines = len([l for l in lines if l.startswith('+')])
        removed_lines = len([l for l in lines if l.startswith('-')])
        total_changes = added_lines + removed_lines
        
        security_keywords = ['auth', 'password', 'token', 'permission', 'security']
        is_security = any(kw in diff.lower() for kw in security_keywords)
        
        new_keywords = ['def new_', 'class new_', '# 新增', '# new']
        is_new_feature = any(kw in diff.lower() for kw in new_keywords)
        
        if is_security or is_new_feature:
            change_type = ChangeType.LARGE
            reason = "安全相關或新功能"
        elif total_changes < self.small_threshold:
            change_type = ChangeType.SMALL
            reason = f"改動 < {self.small_threshold} 行"
        elif total_changes > self.large_threshold:
            change_type = ChangeType.LARGE
            reason = f"改動 > {self.large_threshold} 行"
        else:
            change_type = ChangeType.SMALL
            reason = "中等改動，預設通過"
        
        return ChangeAnalysis(
            type=change_type,
            lines_changed=total_changes,
            files_affected=len(set(l.split('/')[0] for l in lines if '/' in l)),
            is_security_related=is_security,
            is_new_feature=is_new_feature,
            reason=reason
        )
    
    def should_review(self, analysis: ChangeAnalysis) -> bool:
        self.stats["total_tasks"] += 1
        
        if self.mode == WorkflowMode.OFF:
            self.stats["auto_approved"] += 1
            return False
        
        if self.mode == WorkflowMode.ON:
            self.stats["review_required"] += 1
            return True
        
        if analysis.type == ChangeType.LARGE:
            self.stats["review_required"] += 1
            return True
        else:
            self.stats["auto_approved"] += 1
            return False
    
    def execute(self, diff: str, code_func: Callable) -> dict:
        analysis = self.analyze_change(diff)
        
        if self.should_review(analysis):
            return {
                "status": "needs_review",
                "analysis": analysis,
                "message": f"需要審查：{analysis.reason}"
            }
        else:
            result = code_func()
            return {
                "status": "auto_approved",
                "analysis": analysis,
                "result": result,
                "message": f"自動通過：{analysis.reason}"
            }
    
    def get_stats(self) -> dict:
        total = self.stats["total_tasks"]
        auto = self.stats["auto_approved"]
        review = self.stats["review_required"]
        return {
            **self.stats,
            "auto_approve_rate": f"{(auto/total*100):.1f}%" if total > 0 else "N/A",
            "review_rate": f"{(review/total*100):.1f}%" if total > 0 else "N/A"
        }
