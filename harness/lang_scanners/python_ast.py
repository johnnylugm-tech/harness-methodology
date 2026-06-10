"""Python in-process scanners — assertions / error-handling / docstrings.

Moved verbatim from harness/tool_runners.py (PR-3): same scan logic, same
output JSON schema, same pragma semantics. tool_runners dispatches here via
harness.lang_scanners.RUNNERS.

Output contracts (consumed by the ast-* scorers in tool_runners):
  run_assertions      → {"total", "asserted", "zero_assert": [...]}
  run_error_handling  → {"total", "with_handler", "no_handler": [...],
                         "exempt_count", "exempt_files": [...]}
  run_docstrings      → {"total", "with_doc", "missing": [...]} or {} (no code)
"""

from __future__ import annotations

import ast

# Source directories scanned for file-level error-handling coverage.
_SRC_DIRS: tuple[str, ...] = ("03-development/src", "src")

# Test directories scanned for assertion-quality analysis (first existing wins
# is NOT used — all are scanned and merged).
_TEST_DIRS: tuple[str, ...] = ("tests", "03-development/tests")


def _function_has_assertion(node: ast.AST) -> bool:
    """True if a (async)FunctionDef body contains a substantive assertion.

    Recognises: bare `assert`, unittest `self.assertXxx(...)` / `self.fail()`,
    `pytest.raises`/`pytest.warns` (call or `with` context manager), numpy
    `np.testing.assert_*`, and bare `raises(...)`/`warns(...)` imports.
    """
    for sub in ast.walk(node):
        if isinstance(sub, ast.Assert):
            return True
        if isinstance(sub, ast.Call):
            fn = sub.func
            if isinstance(fn, ast.Attribute):
                name = fn.attr
                if name.startswith("assert") or name in ("fail", "raises", "warns"):
                    return True
            elif isinstance(fn, ast.Name):
                if fn.id in ("raises", "warns"):
                    return True
    return False


def run_assertions(project_root: str) -> tuple[str, int]:
    """Scan test files and report assertion coverage of test functions.

    Returns (json_summary, 0) where summary = {total, asserted, zero_assert:[...]}.
    A test function with NO substantive assertion (a "pass-and-still-green" shell)
    is counted as zero_assert.  This is what test_assertion_quality means —
    pytest pass-rate cannot detect it.
    """
    import ast as _ast
    import json as _json
    from pathlib import Path as _Path

    root = _Path(project_root)
    total = 0
    asserted = 0
    zero_assert: list[str] = []

    seen: set[str] = set()
    for rel in _TEST_DIRS:
        base = root / rel
        if not base.is_dir():
            continue
        for path in base.rglob("test_*.py"):
            key = str(path.resolve())
            if key in seen:
                continue
            seen.add(key)
            try:
                tree = _ast.parse(path.read_text(encoding="utf-8", errors="replace"))
            except (SyntaxError, ValueError, OSError):
                continue
            for fn in _ast.walk(tree):
                # pytest convention: `test_*` (or bare `test`). Avoid matching helpers
                # like `testing_*` / `tests_*` that are not test cases.
                if isinstance(fn, (_ast.FunctionDef, _ast.AsyncFunctionDef)) and (
                    fn.name.startswith("test_") or fn.name == "test"
                ):
                    total += 1
                    if _function_has_assertion(fn):
                        asserted += 1
                    else:
                        zero_assert.append(f"{path.relative_to(root)}::{fn.name}")

    summary = {"total": total, "asserted": asserted, "zero_assert": zero_assert[:50]}
    return _json.dumps(summary), 0


def run_error_handling(project_root: str) -> tuple[str, int]:
    """Scan source files and report file-level error-handling coverage.

    Returns (json_summary, 0) where summary = {total, with_handler, no_handler:[...],
    exempt:[...]}.  A source file counts as "handled" if it contains at least one
    try/except block with a real handler.

    Files containing ``# pragma: no error-handling`` are EXEMPT — they are excluded
    from the denominator entirely. Use this for Pydantic models, data-only classes,
    and pure pass-through files that legitimately have no I/O or external calls to
    handle. Justification: error-handling coverage measures resilience, not
    compliance; penalising files that cannot fail is a false positive.

    This is a framework-owned, independently reproducible measure — it replaces
    the CRG flow path whose has_error_handler field does not exist.
    """
    import ast as _ast
    import json as _json
    from pathlib import Path as _Path

    _PRAGMA_EXEMPT = "# pragma: no error-handling"

    root = _Path(project_root)
    total = 0
    with_handler = 0
    exempt_count = 0
    no_handler: list[str] = []
    exempt_files: list[str] = []

    seen: set[str] = set()
    for rel in _SRC_DIRS:
        base = root / rel
        if not base.is_dir():
            continue
        for path in base.rglob("*.py"):
            key = str(path.resolve())
            if key in seen:
                continue
            seen.add(key)
            try:
                source = path.read_text(encoding="utf-8", errors="replace")
                tree = _ast.parse(source)
            except (SyntaxError, ValueError, OSError):
                continue
            # Skip files with no functions/classes (e.g. empty __init__.py) — they
            # have nothing to handle and would unfairly dilute the ratio.
            has_code = any(
                isinstance(n, (_ast.FunctionDef, _ast.AsyncFunctionDef, _ast.ClassDef))
                for n in _ast.walk(tree)
            )
            if not has_code:
                continue
            # Exemption: pragma comment in the file → legitimately no I/O to handle
            if _PRAGMA_EXEMPT in source:
                exempt_count += 1
                exempt_files.append(str(path.relative_to(root)))
                continue
            total += 1
            handled = any(
                isinstance(n, _ast.Try) and n.handlers
                for n in _ast.walk(tree)
            )
            if handled:
                with_handler += 1
            else:
                no_handler.append(str(path.relative_to(root)))

    summary = {
        "total": total, "with_handler": with_handler, "no_handler": no_handler[:50],
        "exempt_count": exempt_count, "exempt_files": exempt_files[:50],
    }
    return _json.dumps(summary), 0


def run_docstrings(project_root: str) -> tuple[str, int]:
    """Scan source files and report public-API docstring coverage.

    Returns (json_summary, 0) where summary = {total, with_doc, missing:[...]}.
    A "public" def/class is one whose name does not start with '_' (excludes
    private members and dunders like __init__). This is a framework-owned,
    independently reproducible documentation measure — it replaces pydocstyle's
    style-only check with actual docstring presence on the public surface.
    """
    import ast as _ast
    import json as _json
    from pathlib import Path as _Path

    root = _Path(project_root)

    class PublicAPIVisitor(_ast.NodeVisitor):
        def __init__(self, filepath):
            self.filepath = filepath
            self.total = 0
            self.with_doc = 0
            self.missing = []

        def visit_ClassDef(self, node):
            if not node.name.startswith("_"):
                self.total += 1
                if _ast.get_docstring(node):
                    self.with_doc += 1
                else:
                    self.missing.append(f"{self.filepath}::{node.name}")
                # Visit methods, but do not generic_visit to avoid nested classes
                for child in node.body:
                    if isinstance(child, (_ast.FunctionDef, _ast.AsyncFunctionDef)):
                        if not child.name.startswith("_"):
                            self.total += 1
                            if _ast.get_docstring(child):
                                self.with_doc += 1
                            else:
                                self.missing.append(f"{self.filepath}::{node.name}.{child.name}")

        def visit_FunctionDef(self, node):
            if not node.name.startswith("_"):
                self.total += 1
                if _ast.get_docstring(node):
                    self.with_doc += 1
                else:
                    self.missing.append(f"{self.filepath}::{node.name}")

        def visit_AsyncFunctionDef(self, node):
            self.visit_FunctionDef(node)

    total = 0
    with_doc = 0
    missing: list[str] = []
    has_code = False

    seen: set[str] = set()
    for rel in _SRC_DIRS:
        base = root / rel
        if not base.is_dir():
            continue
        for path in base.rglob("*.py"):
            key = str(path.resolve())
            if key in seen:
                continue
            seen.add(key)
            try:
                tree = _ast.parse(path.read_text(encoding="utf-8", errors="replace"))
            except (SyntaxError, ValueError, OSError):
                continue

            # Skip files with no functions/classes
            file_has_code = any(
                isinstance(n, (_ast.FunctionDef, _ast.AsyncFunctionDef, _ast.ClassDef))
                for n in _ast.walk(tree)
            )
            if not file_has_code:
                continue

            has_code = True

            visitor = PublicAPIVisitor(path.relative_to(root))
            visitor.visit(tree)
            total += visitor.total
            with_doc += visitor.with_doc
            missing.extend(visitor.missing)

    if not has_code:
        return _json.dumps({}), 0

    summary = {"total": total, "with_doc": with_doc, "missing": missing[:50]}
    return _json.dumps(summary), 0
