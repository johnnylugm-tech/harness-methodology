"""Round 113 站F — tests that launch the suite (or verification target) they belong to.

A test that runs `pytest` over a directory containing itself, or runs
`make verify-system` (whose own steps run the suite), re-enters the run it is
part of. The harness runs both itself — its suite run feeds the zero-skip
check in phase_truth_verifier, and the toolchain's `system-verification` tool
runs `make verify-system` at Gates 2-4 — so such a test is never needed, and
every workaround the corpus invented (skip when PYTEST_CURRENT_TEST is set,
skip under --cov) turns the test into a skip.

Read from the source, not run: Python AST over the project's test files. A
call is judged only by its literal argv; anything computed is unknown and not
accused. `--collect-only` / `--co` executes nothing and is not flagged.
Non-Python test trees are out of reach of this reading and are not judged.
"""

from __future__ import annotations

import ast
from pathlib import Path

__all__ = ["self_invoking_tests"]

_SUBPROCESS_CALLS = frozenset({
    "run", "call", "check_call", "check_output", "Popen",
    "create_subprocess_exec", "create_subprocess_shell", "system",
})
_NO_EXECUTION = frozenset({"--collect-only", "--co"})
_VERIFY_TARGET = "verify-system"  # harness/toolchains/registry.py `system-verification`


def _argv(call: ast.Call) -> "list[str] | None":
    """The call's argv as literal strings ('' for a computed element)."""
    if not call.args:
        return None
    first = call.args[0]
    if isinstance(first, (ast.List, ast.Tuple)):
        return [e.value if isinstance(e, ast.Constant) and isinstance(e.value, str) else ""
                for e in first.elts]
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        return first.value.split()
    if isinstance(call.func, ast.Attribute) and call.func.attr == "create_subprocess_exec":
        return [a.value if isinstance(a, ast.Constant) and isinstance(a.value, str) else ""
                for a in call.args]
    return None


def _reenters(argv: "list[str]", test_file: Path, project: Path) -> "str | None":
    if "make" in argv and _VERIFY_TARGET in argv:
        return f"`make {_VERIFY_TARGET}`, which the harness itself runs at Gates 2-4"
    names = [Path(a).name for a in argv]
    if "pytest" not in names or _NO_EXECUTION & set(argv):
        return None
    after = argv[names.index("pytest") + 1:]
    paths = [a for a in after if a and not a.startswith("-")]
    if any(not a for a in after):
        return None  # a computed argument may be the path; not accused
    if not paths:
        return "pytest with no path, which collects the whole suite including this test"
    here = test_file.resolve()
    for p in paths:
        target = (project / p).resolve()
        if here == target or target in here.parents:
            return f"pytest over {p}, which contains this test"
    return None


def self_invoking_tests(project: "str | Path") -> "list[str]":
    """One row per test that re-enters the suite or verification target."""
    from core.quality_gate.spec_coverage import _get_test_directories

    project = Path(project)
    rows: list[str] = []
    seen: set = set()
    for test_dir in _get_test_directories(project) or []:
        for path in sorted(test_dir.rglob("*.py")):
            if path in seen or "__pycache__" in path.parts:
                continue
            seen.add(path)
            try:
                tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
            except SyntaxError:
                continue
            for fn in ast.walk(tree):
                if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                for call in ast.walk(fn):
                    if not (isinstance(call, ast.Call)
                            and isinstance(call.func, (ast.Attribute, ast.Name))):
                        continue
                    name = call.func.attr if isinstance(call.func, ast.Attribute) else call.func.id
                    if name not in _SUBPROCESS_CALLS:
                        continue
                    argv = _argv(call)
                    why = _reenters(argv, path, project) if argv else None
                    if why:
                        rel = path.relative_to(project).as_posix()
                        rows.append(f"{rel}:{call.lineno} {fn.name} runs {why}")
    return rows
