"""Production code that behaves differently because a test is watching.

Round 87 站6, the mirror of `boundary_realism` (Round 51 站3). That one asks
which declared boundaries the SUITE replaced before it started. Nothing had
ever asked which production modules were RESHAPED so a test would pass.

taskq-redo's `api/deps.py`, in the delivered tree, at Gate 2 composite 98.04:

    _ORIGINAL_TASK_CREATE = _tasks_service.TaskService.create
    ...
    if _tasks_service.TaskService.create is not _ORIGINAL_TASK_CREATE:
        RateRepo.reset_all()

The rate limiter turns itself off whenever another module's attribute has been
replaced. Its comment says "No-op in production — `TaskService.create` is
never replaced", which is a claim about callers, not a property of the code.
The program the four Gate 1 dimensions scored is not the program that ships.

WHY THE RULE HAS THE CLAUSE IT HAS

The first draft was "a module-level attribute snapshot, compared by identity".
Measured over twelve corpus projects: 3 hits, of which 2 are Alembic's own
`config.config_file_name is not None` — `config` IS a module-level attribute
snapshot and `is not` IS an identity test, and the file is boilerplate the
project did not write. Requiring the snapshot to be the operand COMPARED
AGAINST takes it to 1 hit in 12 projects, and that one is taskq-redo's.

A 1-of-12 result is evidence of no false positives and, equally, evidence that
the rule is narrow. It reproduces one observed shape. Asking "am I under test"
another way — `sys.modules`, `PYTEST_CURRENT_TEST`, an env var — is not caught,
and is recorded in the Round 87 ledger with the re-open condition rather than
pre-generalised here.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.quality_gate.test_seam_in_production import (
    check_test_seams,
    runtime_test_seams,
)

pytestmark = [pytest.mark.core]

CORPUS = Path("/Users/johnny/projects")


def _src(tmp_path: Path, body: str) -> Path:
    d = tmp_path / "03-development" / "src" / "pkg"
    d.mkdir(parents=True)
    (d / "mod.py").write_text(body, encoding="utf-8")
    return tmp_path


def test_the_shipped_seam_is_found(tmp_path: Path) -> None:
    """taskq-redo's exact shape."""
    project = _src(tmp_path, (
        "from pkg import service\n"
        "_ORIGINAL = service.Task.create\n\n"
        "def limit():\n"
        "    if service.Task.create is not _ORIGINAL:\n"
        "        reset_all()\n"
    ))
    found = runtime_test_seams(project)
    assert len(found) == 1, found
    assert found[0]["line"] == 5
    assert "_ORIGINAL" in found[0]["code"]


def test_alembic_boilerplate_is_not_a_seam(tmp_path: Path) -> None:
    """`config.config_file_name is not None` — the first draft reported this.

    `config` is bound at module level to an attribute of `context`, and the
    comparison is an identity test. What it is compared AGAINST is `None`, not
    the snapshot, and that is the whole difference between a seam and a
    null-check. Two corpus projects ship this file.
    """
    project = _src(tmp_path, (
        "from alembic import context\n"
        "config = context.config\n\n"
        "def run():\n"
        "    if config.config_file_name is not None:\n"
        "        fileConfig(config.config_file_name)\n"
    ))
    assert runtime_test_seams(project) == []


def test_a_comment_mentioning_monkeypatch_is_not_a_seam(tmp_path: Path) -> None:
    """Documenting that a seam exists is not branching on it.

    taskq-cc-new's production code carries several such comments ("Module-level
    alias so the FR-03 test fixture can monkeypatch …") and its implementation
    is the correct one. A scan that reads prose would charge it for being
    explicit.
    """
    project = _src(tmp_path, (
        "# Module-level alias so the test fixture can monkeypatch this.\n"
        "from pkg import repo\n"
        "rate_repo = repo\n\n"
        "def limit():\n"
        "    return rate_repo.consume()\n"
    ))
    assert runtime_test_seams(project) == []


def test_no_source_tree_is_not_a_finding(tmp_path: Path) -> None:
    """Phase 1 and 2 have no delivered source to ask the question of."""
    assert runtime_test_seams(tmp_path) == []


def test_the_finding_blocks(tmp_path: Path) -> None:
    project = _src(tmp_path, (
        "from pkg import service\n"
        "_ORIGINAL = service.Task.create\n\n"
        "def limit():\n"
        "    if service.Task.create is not _ORIGINAL:\n"
        "        reset_all()\n"
    ))
    violations = check_test_seams(project)
    assert len(violations) == 1
    assert violations[0].severity == "error"
    assert violations[0].check_type == "test_seam_in_production"


def test_the_rule_is_one_in_twelve_on_the_corpus() -> None:
    """The measurement that chose the rule's shape, kept executable.

    If a second project starts reporting here, either it grew the same defect
    or the rule drifted back toward the draft that reported Alembic. Both are
    worth stopping for.
    """
    if not (CORPUS / "taskq-cc" / "SPEC.md").exists():
        pytest.skip("corpus projects not present on this machine")
    hits = {}
    for name in ("taskq-redo", "taskq-cc", "taskq-cc-new", "taskq-new",
                 "taskq-super", "taskq-api", "taskq-advance", "taskq-renew",
                 "taskq", "taskq-plus", "taskq-mm", "taskq-verify"):
        project = CORPUS / name
        if not project.is_dir():
            continue
        found = runtime_test_seams(project)
        if found:
            hits[name] = [f"{f['file']}:{f['line']}" for f in found]
    assert list(hits) == ["taskq-redo"], (
        f"expected exactly taskq-redo to report a runtime test seam, got {hits}"
    )
