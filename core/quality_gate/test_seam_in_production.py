"""Production code that behaves differently because a test is watching.

Round 87 站6. `boundary_realism` (Round 51 站3) asks which declared boundaries
the test suite replaced before it started. This is the mirror nobody had
asked: which production modules were RESHAPED so a test would pass.

taskq-redo's `api/deps.py`, in the delivered tree, at Gate 2 score 98.04:

    _ORIGINAL_TASK_CREATE = _tasks_service.TaskService.create
    ...
    # [FR-10 / AC-10.5] Test seam: when `TaskService.create` has been
    # monkey-patched (the test_fr10 500-trigger contract test),
    # reset the rate bucket so the trigger can reach the handler.
    if _tasks_service.TaskService.create is not _ORIGINAL_TASK_CREATE:
        RateRepo.reset_all()

The rate limiter is disabled at runtime whenever another module's attribute
has been replaced. The comment says it is a no-op in production; that is a
claim about who calls it, not a property of the code. Two siblings in the same
project were shaped the same way and are NOT reported here, because they are
not decidable: `_FR06QueuePool` exists because "the FR-06 acceptance test
calls `engine.pool.size()`", and `_mirror_pool_pre_ping` publishes a flag
"onto the introspection seams the FR-06 test probes". Both are honest code
that happens to have been designed by a test. Only the runtime branch is a
different program under test than in production.

THE RULE, AND THE CONDITION THAT MAKES IT ONE

    a module-level snapshot of an attribute (`_X = mod.attr`), compared by
    IDENTITY at runtime, where the comparison's other operand IS that snapshot

Measured over twelve corpus projects. Without the last clause: 3 hits, and 2
are Alembic's own `config.config_file_name is not None` boilerplate — `config`
is a module-level attribute snapshot and `is not` is an identity test, but the
operand is `None`, not the snapshot. With it: 1 hit, taskq-redo's `deps.py:179`,
and nothing else.

That 1/12 is both the evidence of zero false positives and the reason this
rule is narrow: it reproduces one observed shape and does not generalise ahead
of a second. A different way of asking "am I under test" (reading
`sys.modules`, an env var, `PYTEST_CURRENT_TEST`) is not caught here. Recorded
in the Round 87 ledger with the re-open condition — a second instance of a
different shape folds both in rather than pre-generalising now.
"""
from __future__ import annotations

import ast
from pathlib import Path

from core.quality_gate import Violation
from core.utils.project_layout import ProjectLayout

__all__ = ["check_test_seams", "runtime_test_seams"]


def _module_attribute_snapshots(tree: ast.Module) -> set[str]:
    """Names bound at MODULE level to an attribute of something else.

    Module level only: a snapshot taken inside a function is a local read, not
    a before-image kept for later comparison.
    """
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Attribute):
            names.update(
                t.id for t in node.targets if isinstance(t, ast.Name))
    return names


def runtime_test_seams(project: "str | Path") -> list[dict]:
    """`[{file, line, code}]` — production sites that branch on a test seam.

    Never raises: a source tree that will not parse is another check's finding,
    and refusing to answer is better than answering wrong.
    """
    # Resolved, because `ProjectLayout` resolves too and `rglob` therefore
    # yields resolved paths. Without this, a caller passing an unresolved path
    # — macOS `/tmp` -> `/private/tmp`, or any project reached through a
    # symlink — made `relative_to` below raise ValueError, and
    # `preflight_artifact_consistency` turns any exception into
    # `[BLOCKED] artifact-consistency scan error`. "Never raises" has to be
    # true, not documented.
    root = Path(project).resolve()
    src_dir = ProjectLayout(root).phase3_development_dir / "src"
    if not src_dir.is_dir():
        return []
    findings: list[dict] = []
    for path in sorted(src_dir.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except (SyntaxError, OSError):
            continue
        snapshots = _module_attribute_snapshots(tree)
        if not snapshots:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Compare):
                continue
            if not any(isinstance(op, (ast.Is, ast.IsNot)) for op in node.ops):
                continue
            # The snapshot must be one OPERAND of the identity test — either
            # side. `is not` is symmetric and the first version of this rule
            # was not: reading only `node.comparators` meant
            # `if _ORIGINAL is not service.Task.create:` hid the same branch
            # entirely.
            #
            # What this still excludes is what it always excluded, and reading
            # both sides does not weaken it: Alembic's
            # `config.config_file_name is not None` has an Attribute on the
            # left and a Constant on the right, so neither operand is the
            # snapshot. That boilerplate ships in two corpus projects and is
            # why the operand rule exists at all.
            operands = [node.left, *node.comparators]
            if not any(isinstance(o, ast.Name) and o.id in snapshots
                       for o in operands):
                continue
            findings.append({
                "file": str(path.relative_to(root)),
                "line": node.lineno,
                "code": ast.unparse(node),
            })
    return findings


def check_test_seams(project: "str | Path") -> list[Violation]:
    """The findings above as blocking Violations, for the artifact preflight.

    `error`, and from phase 3 like its neighbours there — before Phase 3 there
    is no delivered source for the question to be about. Kept in this module
    rather than in `artifact_consistency` so the check and the AST rule it
    depends on stay one call apart, and so the file that already carries every
    AC check does not grow a subject that is not an AC.
    """
    return [
        Violation(
            check_type="test_seam_in_production", rule_id="TS",
            severity="error", file=f["file"],
            message=(
                f"{f['file']}:{f['line']} branches at runtime on whether "
                f"another module's attribute still holds its import-time value "
                f"(`{f['code']}`). That is a test seam: the delivered system "
                f"behaves one way under a suite that monkey-patches and another "
                f"way in production, and only one of the two is ever gated. "
                f"Make the behaviour a parameter the caller passes, or let the "
                f"test construct the state it needs."))
        for f in runtime_test_seams(project)
    ]
