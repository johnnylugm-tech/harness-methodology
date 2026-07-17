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

Round 13 站1 (老闆's "容錯了，但進入不預期的處理方式導致難以debug"
diagnosis): a repo-wide AST audit found 90 more unlogged broad excepts the
original return-success-shape check couldn't see — 53 handlers whose body
is just ``pass``, 8 ending in ``continue`` (skip this loop item, silently),
and 29 that assign some fallback value(s) and fall through to whatever
comes next with no return at all. All three are the identical Round-5
failure mode one level removed: the handler fires, nothing gets logged,
and the only trace is the eventual absence of whatever the try block was
supposed to do. Extended here (still zero allowlist, still a one-line fix).
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
    "True", "None", "[] (empty list)", "{} (empty dict)", "(True, ...) tuple",
)


def _is_success_shaped(node: ast.expr) -> bool:
    if isinstance(node, ast.Constant):
        return node.value is True or node.value is None
    if isinstance(node, ast.List) and len(node.elts) == 0:
        return True
    if isinstance(node, ast.Dict) and len(node.keys) == 0:
        return True
    if isinstance(node, ast.Tuple) and node.elts:
        # tuple[bool, ...] convention (Round 7: mutation_enforcer.py's
        # dominant return shape) — only the unambiguous "first slot is
        # literal True" case is flagged. A plain int/str tuple like
        # (0, 0) is deliberately NOT flagged: it has legitimate
        # non-error meanings elsewhere (e.g. mutation counts) and has no
        # comparable universal "nothing happened" reading the way
        # True/None/[]/{} do.
        first = node.elts[0]
        return isinstance(first, ast.Constant) and first.value is True
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


def _ends_in_return_success(body: list[ast.stmt]) -> bool:
    last = body[-1]
    return (
        isinstance(last, ast.Return)
        and last.value is not None
        and _is_success_shaped(last.value)
    )


def _ends_in_continue(body: list[ast.stmt]) -> bool:
    """Round 13 站1: a broad except ending in `continue` (inside a loop)
    silently skips the current item exactly the way an unlogged `return`
    silently skips the current call — same invisible-failure shape, one
    loop-iteration narrower."""
    return isinstance(body[-1], ast.Continue)


def _is_silent_fallthrough(body: list[ast.stmt]) -> bool:
    """Round 13 站1: every statement in the handler is a plain assignment/
    expression/pass — no return, raise, continue, or break — so control
    simply falls through to whatever comes after the try/except with no
    visible trace that the except fired. Covers both a lone `pass` and a
    multi-statement "set some default(s) and carry on" handler; the
    ratchet's docstring's Round 5 finding (9 incidents, all a broad except
    returning a success-looking value with nothing logged) is the same
    failure mode one level up — this is the version where the handler
    doesn't even return, it just quietly sets up state for what follows."""
    return all(
        isinstance(s, (ast.Assign, ast.AugAssign, ast.AnnAssign, ast.Expr, ast.Pass))
        for s in body
    )


_FAIL_OPEN_SHAPES = (_ends_in_return_success, _ends_in_continue, _is_silent_fallthrough)


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
            if _has_log_or_raise(handler.body):
                continue
            if any(shape(handler.body) for shape in _FAIL_OPEN_SHAPES):
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
        "broad `except` fails open with no log/print/raise in the handler — "
        f"either returns a success-shaped value ({', '.join(_SUCCESS_SHAPES)}), "
        "ends in `continue`, or is a silent pass/fallthrough (assigns a "
        "fallback and carries on with no return at all) — add one "
        "diagnostic line (or fix the underlying bug if the swallow is "
        "wrong, not just unlogged):\n  "
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


def test_scanner_flags_unlogged_tuple_fail_open():
    """Round 7: mutation_enforcer.py's dominant tuple[bool, ...] shape must
    trip the scanner too — this is the exact shape of the historical
    ddeb301/6805567/9a9d95c/ff98cc7 mutation_enforcer bugs, which the
    original True/None/[]/{} heuristic could not see."""
    probe = (
        "def f():\n"
        "    try:\n"
        "        risky()\n"
        "    except Exception:\n"
        "        return True, ''\n"
    )
    assert _scan_source(probe) == [4]


def test_scanner_ignores_logged_tuple_fail_open():
    """Negative: a logged (True, ...) tuple return clears it, same as bare True."""
    probe = (
        "def f():\n"
        "    try:\n"
        "        risky()\n"
        "    except Exception as exc:\n"
        "        print(f'failed: {exc}')\n"
        "        return True, ''\n"
    )
    assert _scan_source(probe) == []


def test_scanner_ignores_non_true_first_tuple_element():
    """Negative: a tuple not starting with literal True (e.g. int-pair counts
    like (0, 0)) is deliberately out of scope — it has legitimate non-error
    meanings elsewhere (see _is_success_shaped's docstring reasoning)."""
    probe = (
        "def f():\n"
        "    try:\n"
        "        risky()\n"
        "    except Exception:\n"
        "        return 0, 0\n"
    )
    assert _scan_source(probe) == []


def test_scanner_ignores_false_first_tuple_element():
    """Negative: (False, ...) is the correct fail-closed shape, not fail-open."""
    probe = (
        "def f():\n"
        "    try:\n"
        "        risky()\n"
        "    except Exception as exc:\n"
        "        return False, str(exc)\n"
    )
    assert _scan_source(probe) == []


def test_scanner_flags_unlogged_pass_only():
    """Round 13 站1: a broad except whose entire handler body is `pass`
    silently swallows the exception with no return and no trace."""
    probe = (
        "def f():\n"
        "    try:\n"
        "        risky()\n"
        "    except Exception:\n"
        "        pass\n"
    )
    assert _scan_source(probe) == [4]


def test_scanner_ignores_logged_pass_only():
    """Negative: a log call before `pass` clears it."""
    probe = (
        "def f():\n"
        "    try:\n"
        "        risky()\n"
        "    except Exception as exc:\n"
        "        logger.warning('cleanup failed: %s', exc)\n"
        "        pass\n"
    )
    assert _scan_source(probe) == []


def test_scanner_flags_unlogged_continue():
    """Round 13 站1: a broad except ending in `continue` (inside a loop)
    silently skips the current item — same invisible failure, one loop
    iteration narrower than a silent return."""
    probe = (
        "def f(items):\n"
        "    for item in items:\n"
        "        try:\n"
        "            risky(item)\n"
        "        except Exception:\n"
        "            continue\n"
    )
    assert _scan_source(probe) == [5]


def test_scanner_ignores_logged_continue():
    """Negative: a log call before `continue` clears it."""
    probe = (
        "def f(items):\n"
        "    for item in items:\n"
        "        try:\n"
        "            risky(item)\n"
        "        except Exception as exc:\n"
        "            print(f'skipping {item}: {exc}')\n"
        "            continue\n"
    )
    assert _scan_source(probe) == []


def test_scanner_flags_unlogged_silent_fallthrough():
    """Round 13 站1: a broad except that assigns a fallback value and falls
    through (no return, no raise) with nothing logged — the exact shape of
    the 29 default-assignment sites the repo-wide audit found."""
    probe = (
        "def f():\n"
        "    result = None\n"
        "    try:\n"
        "        result = risky()\n"
        "    except Exception:\n"
        "        result = DEFAULT\n"
        "    return result\n"
    )
    assert _scan_source(probe) == [5]


def test_scanner_ignores_logged_silent_fallthrough():
    """Negative: a log call before the fallback assignment clears it."""
    probe = (
        "def f():\n"
        "    result = None\n"
        "    try:\n"
        "        result = risky()\n"
        "    except Exception as exc:\n"
        "        print(f'using default: {exc}')\n"
        "        result = DEFAULT\n"
        "    return result\n"
    )
    assert _scan_source(probe) == []


def test_scanner_ignores_fallthrough_with_raise():
    """Negative: a handler that re-raises is not a silent fallthrough even
    though its other statements are plain assignments."""
    probe = (
        "def f():\n"
        "    try:\n"
        "        risky()\n"
        "    except Exception as exc:\n"
        "        note = str(exc)\n"
        "        raise RuntimeError(note) from exc\n"
    )
    assert _scan_source(probe) == []


def test_scanner_ignores_fallthrough_with_break():
    """Negative: `break` is deliberately out of scope — it exits the loop
    entirely (a visible change in the caller's control flow the same way
    an early return is), not a silently-skip-and-continue shape."""
    probe = (
        "def f(items):\n"
        "    for item in items:\n"
        "        try:\n"
        "            risky(item)\n"
        "        except Exception:\n"
        "            break\n"
    )
    assert _scan_source(probe) == []
