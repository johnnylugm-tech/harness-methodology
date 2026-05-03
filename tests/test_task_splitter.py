from core.task_splitter import TaskSplitter, TaskStatus, TaskPriority

def test_create_task():
    splitter = TaskSplitter()
    task = splitter.create_task("Test Task", "Desc", priority=TaskPriority.HIGH)
    assert task.name == "Test Task"
    assert task.priority == TaskPriority.HIGH
    assert task.status == TaskStatus.PENDING

def test_dependencies():
    splitter = TaskSplitter()
    t1 = splitter.create_task("Task 1", "Desc")
    t2 = splitter.create_task("Task 2", "Desc")
    splitter.add_dependency(t2.id, t1.id)
    assert t1.id in t2.dependencies

def test_execution_order():
    splitter = TaskSplitter()
    t1 = splitter.create_task("T1", "D1")
    t2 = splitter.create_task("T2", "D2")
    splitter.add_dependency(t2.id, t1.id)
    order = splitter.get_execution_order()
    assert order[0].id == t1.id
    assert order[1].id == t2.id
