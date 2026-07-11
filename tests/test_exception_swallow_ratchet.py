"""Exception-swallow guard — broad excepts must leave a trace when they fire.

Round 5 finding: 9 prior incidents (agent_spawner, spec_logic_checker,
check_methodology_consistency, state_monitor, gap-detector/pattern-matcher,
mutation_enforcer x2, TDD/mutmut, preflight/exit checks, advance-phase
HANDOVER) were all the same shape — a broad `except` returns a
success-looking value (``True``/``None``/``[]``/``{}``) with nothing
logged, so the failure is invisible until someone notices the *absence*
of an expected side effect. Each was fixed reactively, file by file, with
a narrow regression test pinning that one function. This guard closes the
class structurally: any broad except whose handler's last statement
returns a success-shaped value MUST also log/print/raise somewhere in the
handler body.

Unlike ``test_patch_discipline.py``'s ceiling dict or
``test_file_size_ratchet.py``'s per-file ceiling, this guard has **no
allowlist**. The fix is always available and always free: add one log
line. A handler that deliberately fails open (e.g. treating a parse error
as "assume worst case") still deserves a visible trace for when it fires.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

_SCAN_DIRS = ("cli", "core", "harness", "scripts", "detection")

_LOG_NAMES = {
    "print", "warn", "warning", "error", "exception",
    "critical", "debug", "info", "log",
}

_SUCCESS_SHAPES = (
    "True", "None", "[] (empty list)", "{} (empty dict)",
)


def _is_success_shaped(node: ast.expr) -> bool:
    if isinstance(node, ast.Constant):
        return node.value is True or node.value is None
    if isinstance(node, ast.List) and len(node.elts) == 0:
        return True
    if isinstance(node, ast.Dict) and len(node.keys) == 0:
        return True
    return False


def _has_log_or_raise(body: list[ast.stmt]) -> bool:
    probe = ast.Module(body=body, type_ignores=[])
    for node in ast.walk(probe):
        if isinstance(node, ast.Raise):
            return True
        if isinstance(node, ast.Call):
            func = node.func
            name = None
            if isinstance(func, ast.Attribute):
                name = func.attr
            elif isinstance(func, ast.Name):
                name = func.id
            if name and name.lower() in _LOG_NAMES:
                return True
    return False


def _is_broad_except(handler: ast.ExceptHandler) -> bool:
    exc_type = handler.type
    if exc_type is None:
        return True  # bare except:
    if isinstance(exc_type, ast.Name) and exc_type.id == "Exception":
        return True
    if isinstance(exc_type, ast.Tuple):
        return any(
            isinstance(elt, ast.Name) and elt.id == "Exception"
            for elt in exc_type.elts
        )
    return False


def _scan_source(source: str, filename: str = "<probe>") -> list[int]:
    """Return line numbers of broad excepts that silently fail open."""
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError:
        return []
    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        for handler in node.handlers:
            if not _is_broad_except(handler) or not handler.body:
                continue
            last = handler.body[-1]
            if not (isinstance(last, ast.Return) and last.value is not None):
                continue
            if not _is_success_shaped(last.value):
                continue
            if _has_log_or_raise(handler.body):
                continue
            hits.append(handler.lineno)
    return hits


def _scan_file(path: Path) -> list[int]:
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return _scan_source(source, filename=str(path))


def test_no_silent_fail_open():
    violations = []
    for d in _SCAN_DIRS:
        for path in sorted((REPO / d).rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            for lineno in _scan_file(path):
                rel = path.relative_to(REPO).as_posix()
                violations.append(f"{rel}:{lineno}")
    assert not violations, (
        "broad `except` returns a success-shaped value "
        f"({', '.join(_SUCCESS_SHAPES)}) with no log/print/raise in the "
        "handler — add one diagnostic line (or fix the underlying bug if "
        "the swallow is wrong, not just unlogged):\n  "
        + "\n  ".join(violations)
    )


def test_scanner_flags_unlogged_fail_open():
    """Negative: an unlogged broad except with a success-shaped return trips the scanner."""
    probe = (
        "def f():\n"
        "    try:\n"
        "        risky()\n"
        "    except Exception:\n"
        "        return True\n"
    )
    assert _scan_source(probe) == [4]


def test_scanner_ignores_logged_fail_open():
    """Negative: adding a .warning() call (the case the first heuristic missed) clears it."""
    probe = (
        "def f():\n"
        "    try:\n"
        "        risky()\n"
        "    except Exception as exc:\n"
        "        logger.warning('failed: %s', exc)\n"
        "        return True\n"
    )
    assert _scan_source(probe) == []


def test_scanner_ignores_raised_fail_open():
    probe = (
        "def f():\n"
        "    try:\n"
        "        risky()\n"
        "    except Exception:\n"
        "        raise RuntimeError('boom')\n"
    )
    assert _scan_source(probe) == []


def test_scanner_ignores_narrow_except():
    """Negative: a narrow except (not Exception/bare) is out of scope."""
    probe = (
        "def f():\n"
        "    try:\n"
        "        risky()\n"
        "    except ValueError:\n"
        "        return True\n"
    )
    assert _scan_source(probe) == []


def test_scanner_ignores_non_success_shaped_return():
    """Negative: returning a non-empty/non-True/None value isn't the fail-open shape."""
    probe = (
        "def f():\n"
        "    try:\n"
        "        risky()\n"
        "    except Exception:\n"
        "        return 0.0\n"
    )
    assert _scan_source(probe) == []
