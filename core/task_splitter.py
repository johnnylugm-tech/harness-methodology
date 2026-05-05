#!/usr/bin/env python3
"""
Task Splitter - Automatic Task Decomposition
"""

from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime


class TaskStatus(Enum):
    """Task lifecycle: pending→running→completed/failed/blocked."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"


class TaskPriority(Enum):
    """Task priority levels for scheduling order."""
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
    """Task decomposer"""

    def __init__(self):
        self.tasks: Dict[str, Task] = {}
        self.task_counter = 0

    def create_task(self, name: str, description: str,
                    priority: TaskPriority = TaskPriority.MEDIUM,
                    estimated_hours: float = 1.0) -> Task:
        """Create a new task and add it to the task list."""
        self.task_counter += 1
        task = Task(id=f"task-{self.task_counter:03d}", name=name,
                    description=description, priority=priority,
                    estimated_hours=estimated_hours)
        self.tasks[task.id] = task
        return task

    def add_dependency(self, task_id: str, depends_on: str) -> None:
        """Register a blocking dependency between two tasks."""
        if task_id in self.tasks and depends_on in self.tasks:
            self.tasks[task_id].dependencies.append(depends_on)

    def split_from_goal(self, goal: str) -> List[Task]:
        goal_lower = goal.lower()
        tasks = []
        phase_map = [
            (["research", "analyze", "analysis"], "Research & Analysis", "Gather info, analyze requirements", TaskPriority.HIGH, 2.0),
            (["design", "plan", "planning"], "System Design", "Design architecture, plan modules", TaskPriority.HIGH, 3.0),
            (["develop", "implement", "build", "code"], "Development", "Code, implement features", TaskPriority.CRITICAL, 8.0),
            (["test", "verify", "validation"], "Testing & Verification", "Write tests, verify features", TaskPriority.HIGH, 4.0),
            (["doc", "documentation"], "Documentation", "Write usage docs", TaskPriority.MEDIUM, 2.0),
            (["deploy", "release", "publish"], "Deployment & Release", "Deploy, release version", TaskPriority.HIGH, 1.0),
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
