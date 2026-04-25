#!/usr/bin/env python3
"""
Task Splitter - 任務自動分解
"""

from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime


class TaskStatus(Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"


class TaskPriority(Enum):
    LOW = 1
    MEDIUM = 2
    HIGH = 3
    CRITICAL = 4


@dataclass
class Task:
    id: str
    name: str
    description: str
    status: TaskStatus = TaskStatus.PENDING
    priority: TaskPriority = TaskPriority.MEDIUM
    dependencies: List[str] = field(default_factory=list)
    assignee: Optional[str] = None
    estimated_hours: float = 1.0
    actual_hours: float = 0.0
    output: Any = None
    error: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    completed_at: Optional[str] = None


class TaskSplitter:
    """任務分解器"""

    def __init__(self):
        self.tasks: Dict[str, Task] = {}
        self.task_counter = 0

    def create_task(self, name: str, description: str,
                    priority: TaskPriority = TaskPriority.MEDIUM,
                    estimated_hours: float = 1.0) -> Task:
        self.task_counter += 1
        task = Task(id=f"task-{self.task_counter:03d}", name=name,
                    description=description, priority=priority,
                    estimated_hours=estimated_hours)
        self.tasks[task.id] = task
        return task

    def add_dependency(self, task_id: str, depends_on: str):
        if task_id in self.tasks and depends_on in self.tasks:
            self.tasks[task_id].dependencies.append(depends_on)

    def split_from_goal(self, goal: str) -> List[Task]:
        goal_lower = goal.lower()
        tasks = []
        phase_map = [
            (["研究", "research", "分析", "analyze"], "調研與分析", "收集資訊、分析需求", TaskPriority.HIGH, 2.0),
            (["設計", "design", "規劃", "plan"], "系統設計", "設計架構、規劃模組", TaskPriority.HIGH, 3.0),
            (["開發", "develop", "實現", "implement", "寫", "build"], "開發實現", "編碼、實現功能", TaskPriority.CRITICAL, 8.0),
            (["測試", "test", "驗證", "verify"], "測試驗證", "編寫測試、驗證功能", TaskPriority.HIGH, 4.0),
            (["文檔", "doc", "說明"], "文檔撰寫", "撰寫使用文檔", TaskPriority.MEDIUM, 2.0),
            (["部署", "deploy", "發布", "release"], "部署發布", "部署上線、發布版本", TaskPriority.HIGH, 1.0),
        ]
        for keywords, name, desc, priority, hours in phase_map:
            if any(k in goal_lower for k in keywords):
                tasks.append(self.create_task(name, desc, priority, hours))
        for i in range(1, len(tasks)):
            self.add_dependency(tasks[i].id, tasks[i-1].id)
        return tasks

    def get_ready_tasks(self) -> List[Task]:
        return [
            t for t in self.tasks.values()
            if t.status == TaskStatus.PENDING and all(
                self.tasks.get(dep_id, Task("x", "x", "")).status == TaskStatus.COMPLETED
                for dep_id in t.dependencies
            )
        ]

    def get_execution_order(self) -> List[Task]:
        order, remaining = [], set(self.tasks.keys())
        while remaining:
            for task_id in list(remaining):
                task = self.tasks[task_id]
                if all(dep_id not in remaining for dep_id in task.dependencies):
                    order.append(task)
                    remaining.remove(task_id)
        return order

    def get_dag(self) -> Dict:
        return {
            "nodes": [{"id": t.id, "label": t.name, "status": t.status.value,
                       "priority": t.priority.value} for t in self.tasks.values()],
            "edges": [{"from": dep, "to": tid}
                      for tid, task in self.tasks.items() for dep in task.dependencies]
        }

    def get_summary(self) -> Dict:
        return {
            "total_tasks": len(self.tasks),
            "pending": sum(1 for t in self.tasks.values() if t.status == TaskStatus.PENDING),
            "running": sum(1 for t in self.tasks.values() if t.status == TaskStatus.RUNNING),
            "completed": sum(1 for t in self.tasks.values() if t.status == TaskStatus.COMPLETED),
            "failed": sum(1 for t in self.tasks.values() if t.status == TaskStatus.FAILED),
            "total_estimated_hours": sum(t.estimated_hours for t in self.tasks.values())
        }
