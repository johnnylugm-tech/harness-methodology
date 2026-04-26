#!/usr/bin/env python3
"""
Hybrid Workflow - Smart-Routing Workflow

Three modes:
- OFF: Single Agent
- HYBRID: Smart routing (small changes auto, large changes review)
- ON: Forced A/B review
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
        self.stats = {"auto_approved": 0, "review_required": 0, "total_tasks": 0}

    def analyze_change(self, diff: str) -> ChangeAnalysis:
        lines = diff.split('\n')
        added_lines = len([l for l in lines if l.startswith('+')])
        removed_lines = len([l for l in lines if l.startswith('-')])
        total_changes = added_lines + removed_lines
        security_keywords = ['auth', 'password', 'token', 'permission', 'security']
        is_security = any(kw in diff.lower() for kw in security_keywords)
        new_keywords = ['def new_', 'class new_', '# new']
        is_new_feature = any(kw in diff.lower() for kw in new_keywords)
        if is_security or is_new_feature:
            change_type, reason = ChangeType.LARGE, "security-related or new feature"
        elif total_changes < self.small_threshold:
            change_type, reason = ChangeType.SMALL, f"change < {self.small_threshold} lines"
        elif total_changes > self.large_threshold:
            change_type, reason = ChangeType.LARGE, f"change > {self.large_threshold} lines"
        else:
            change_type, reason = ChangeType.SMALL, "medium change, auto-pass"
        return ChangeAnalysis(
            type=change_type, lines_changed=total_changes,
            files_affected=len(set(l.split('/')[0] for l in lines if '/' in l)),
            is_security_related=is_security, is_new_feature=is_new_feature, reason=reason
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
        self.stats["auto_approved"] += 1
        return False

    def execute(self, diff: str, code_func: Callable) -> dict:
        analysis = self.analyze_change(diff)
        if self.should_review(analysis):
            return {"status": "needs_review", "analysis": analysis,
                    "message": f"Review required: {analysis.reason}"}
        result = code_func()
        return {"status": "auto_approved", "analysis": analysis, "result": result,
                "message": f"Auto-passed: {analysis.reason}"}

    def get_stats(self) -> dict:
        total = self.stats["total_tasks"]
        auto = self.stats["auto_approved"]
        review = self.stats["review_required"]
        return {**self.stats,
                "auto_approve_rate": f"{(auto/total*100):.1f}%" if total > 0 else "N/A",
                "review_rate": f"{(review/total*100):.1f}%" if total > 0 else "N/A"}
