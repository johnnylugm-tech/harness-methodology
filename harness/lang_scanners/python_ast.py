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
            # Reject constant-value assertions (assert True, assert False,
            # assert None, assert 0, assert "" …).  These are semantically
            # equivalent to pass/raise and do not verify any behaviour.
            if isinstance(sub.test, ast.Constant):
                continue
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


_SWALLOWS_ASSERTION: frozenset[str] = frozenset({
    "AssertionError", "Exception", "BaseException",
})


def _handler_swallows_assertion(handler: "ast.ExceptHandler") -> bool:
    """True when a failing assertion inside this `try` would not end the test.

    Round 53 站3. Narrower than `_handler_anti_pattern` below and asking a
    different question: that one classifies error handling in *product* code,
    where `except FileNotFoundError: pass` is deliberate and correctly exempt.
    Here the body is a test, and a handler that catches an `AssertionError` —
    by name, via `Exception`/`BaseException`, or bare — and does not re-raise
    turns the verdict into a no-op. `pytest.skip()` in the handler counts as
    swallowing too: Round 46 站1 settled that an absent witness is not a failed
    testimony, and converting the failure into a skip is how that shape hides
    inside a green suite.
    """
    names: list[str] = []
    if handler.type is None:
        names.append("BaseException")          # bare `except:`
    else:
        for node in ast.walk(handler.type):
            if isinstance(node, ast.Name):
                names.append(node.id)
            elif isinstance(node, ast.Attribute):
                names.append(node.attr)
    if not any(n in _SWALLOWS_ASSERTION for n in names):
        return False
    return not any(isinstance(n, ast.Raise) for n in ast.walk(handler))


def _assertion_nodes(fn: "ast.AST") -> "list[ast.AST]":
    """Every node `_function_has_assertion` would have accepted, not just the first."""
    found: list[ast.AST] = []
    for sub in ast.walk(fn):
        if isinstance(sub, ast.Assert):
            if not isinstance(sub.test, ast.Constant):
                found.append(sub)
        elif isinstance(sub, ast.Call):
            f = sub.func
            if isinstance(f, ast.Attribute) and (
                f.attr.startswith("assert") or f.attr in ("fail", "raises", "warns")
            ):
                found.append(sub)
            elif isinstance(f, ast.Name) and f.id in ("raises", "warns"):
                found.append(sub)
    return found


def _is_neutralised(fn: "ast.AST") -> bool:
    """True when every assertion in *fn* sits under a handler that swallows it.

    "Every", not "any": a test that checks three things and guards one of them
    still has two verdicts that can fail, and calling it neutralised would be a
    false positive on the shape where a handler adds diagnostics and re-raises.
    """
    nodes = _assertion_nodes(fn)
    if not nodes:
        return False
    spans: list[tuple[int, int]] = []
    for sub in ast.walk(fn):
        if isinstance(sub, ast.Try) and any(
            _handler_swallows_assertion(h) for h in sub.handlers
        ):
            body = sub.body
            if body:
                spans.append((
                    min(b.lineno for b in body),
                    max(getattr(b, "end_lineno", b.lineno) or b.lineno for b in body),
                ))
    if not spans:
        return False
    return all(
        any(lo <= getattr(n, "lineno", -1) <= hi for lo, hi in spans) for n in nodes
    )


def run_assertions(project_root: str) -> tuple[str, int]:
    """Scan test files and report assertion coverage of test functions.

    Returns (json_summary, 0) where summary =
    {total, asserted, zero_assert:[...], neutralised:[...]}.

    A test function with NO substantive assertion (a "pass-and-still-green"
    shell) is counted as zero_assert.  This is what test_assertion_quality
    means — pytest pass-rate cannot detect it.

    Round 53 站3 adds the third bucket. A test whose every assertion is wrapped
    in a handler that swallows `AssertionError` is not assertionless — it
    asserts, and the verdict is discarded — so it leaves `asserted` without
    becoming `zero_assert`, which would misname the defect for whoever fixes
    it. `_score_assertion_quality` is `100 × asserted / total` and needs no
    change to stop rewarding it.

    Measured across the seven projects on this machine: taskq-super 24,
    taskq-api 2, the other five zero. taskq-super's
    `test_task_repo_insert_get` is `try: insert; assert; except Exception:
    pass` verbatim, and its Gate 4 recorded test_assertion_quality = 100.0.
    Two of the four narrow-spelled ones arrived in a commit that says what it
    was doing — `54f9b93 test(p5): swallow transient assertions in
    nfr_phase6_gap gap tests` — and are the only executable checks behind
    NFR-03 and NFR-04, both still marked VERIFIED in TRACEABILITY_MATRIX.md.
    """
    import ast as _ast
    import json as _json
    from pathlib import Path as _Path

    root = _Path(project_root)
    total = 0
    asserted = 0
    zero_assert: list[str] = []
    neutralised: list[str] = []

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
                    name = f"{path.relative_to(root)}::{fn.name}"
                    if not _function_has_assertion(fn):
                        zero_assert.append(name)
                    elif _is_neutralised(fn):
                        neutralised.append(name)
                    else:
                        asserted += 1

    summary = {"total": total, "asserted": asserted,
               "zero_assert": zero_assert[:50], "neutralised": neutralised[:50]}
    return _json.dumps(summary), 0


def _handler_anti_pattern(handler: "ast.ExceptHandler") -> "str | None":
    """Classify an except handler as an anti-pattern, or None when clean.

    Patterns (one per handler, checked in severity order):
      broad_swallow          — broad type (bare / Exception / BaseException)
                               whose body is only pass/continue: errors vanish.
      except_base_exception  — catches BaseException, EVEN with re-raise: any
                               body side-effect treats CancelledError/SystemExit
                               as an application failure (the tts-new Critical
                               was exactly ``except BaseException:
                               self._on_failure(); raise`` — re-raise didn't
                               stop the breaker miscounting cancellation).
                               Error-path-only cleanup belongs in ``finally``
                               or ``except Exception`` + re-raise.
      bare_except            — bare ``except:`` without re-raising.

    The bare-raise re-raise exemption applies to bare_except/broad_swallow
    only — never to BaseException. Narrow-typed except-pass
    (e.g. ``except FileNotFoundError: pass``) is deliberate and NOT flagged.
    """
    def _is_base_exception(expr) -> bool:
        if isinstance(expr, ast.Name) and expr.id == "BaseException":
            return True
        if isinstance(expr, ast.Tuple):
            return any(_is_base_exception(e) for e in expr.elts)
        return False

    def _is_broad(expr) -> bool:
        if expr is None or _is_base_exception(expr):
            return True
        if isinstance(expr, ast.Name) and expr.id == "Exception":
            return True
        if isinstance(expr, ast.Tuple):
            return any(_is_broad(e) for e in expr.elts)
        return False

    body_only_swallows = all(
        isinstance(stmt, (ast.Pass, ast.Continue)) for stmt in handler.body
    )
    if _is_broad(handler.type) and body_only_swallows:
        return "broad_swallow"
    # BaseException is flagged unconditionally — re-raise does NOT exempt it
    # (side-effects before the re-raise still treat cancellation as failure).
    if _is_base_exception(handler.type):
        return "except_base_exception"

    reraises = any(
        isinstance(n, ast.Raise) and n.exc is None for n in ast.walk(handler)
    )
    if reraises:
        return None
    if handler.type is None:
        return "bare_except"
    return None


def run_error_handling(project_root: str) -> tuple[str, int]:
    """Scan source files and report file-level error-handling coverage.

    Returns (json_summary, 0) where summary = {total, with_handler, no_handler:[...],
    exempt:[...], anti_patterns:[...]}.  A source file counts as "handled" if it
    contains at least one try/except block with a real handler.

    anti_patterns lists handlers that exist but undermine resilience
    (see _handler_anti_pattern) as "relpath:line::pattern" entries — presence
    of a handler is no longer automatically a positive signal; the scorer
    deducts per anti-pattern.

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
    anti_patterns: list[str] = []

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
            rel_path = str(path.relative_to(root))
            handled = any(
                isinstance(n, _ast.Try) and n.handlers
                for n in _ast.walk(tree)
            )
            if handled:
                with_handler += 1
            else:
                no_handler.append(rel_path)
            for node in _ast.walk(tree):
                if not isinstance(node, _ast.ExceptHandler):
                    continue
                pattern = _handler_anti_pattern(node)
                if pattern:
                    anti_patterns.append(f"{rel_path}:{node.lineno}::{pattern}")

    summary = {
        "total": total, "with_handler": with_handler, "no_handler": no_handler[:50],
        "exempt_count": exempt_count, "exempt_files": exempt_files[:50],
        "anti_patterns": anti_patterns[:50],
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
            self.visit_FunctionDef(node)  # type: ignore[reportArgumentType]

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
