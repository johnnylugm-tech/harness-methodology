"""Round 13 站2c — agent-facing [BLOCKED] message contract.

Workflow JS is explicitly taught to treat a [BLOCKED] message as the fix
instruction itself: "advance-phase 獨立重新驗證 EVERYTHING... 其自身輸出告訴你
到底缺什麼" (phase_specs.py / js_blocks.py's own prompt text tells the agent
to read [BLOCKED] output and act on it verbatim, never guess). That contract
only holds if every [BLOCKED] message an agent can see actually carries a
remediation element — otherwise the agent has nothing to act on and either
stalls or guesses.

Scoped to the four agent-facing hot paths workflow JS dispatches into a
tight retry loop on: advance-phase, finalize-gate, run-fr-step,
push-checkpoint/push-milestone (cli/phase_cmds.py, cli/gate_cmds.py,
cli/fr_cmds.py, cli/push_cmds.py). Not the repo-wide ~110 [BLOCKED] sites —
see docs/ERROR_HANDLING.md for why this scope is deliberate.

A [BLOCKED] "message" is the print() statement containing the marker plus
whatever immediately follows it in the same block, up to the next
return/break/continue (inclusive) or 5 statements, whichever comes first —
matching how these sites are actually written (a [BLOCKED] line, 0-2
explanatory print()s, then a return). A message is compliant if that
window's combined string-literal content contains at least one remediation
element: "fix", "→", "re-run"/"rerun", or a concrete command (a backtick-
quoted `...` invocation, or a bare mention of another harness_cli.py
subcommand to run next).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

_TARGET_FILES = (
    "cli/phase_cmds.py",
    "cli/gate_cmds.py",
    "cli/fr_cmds.py",
    "cli/push_cmds.py",
)

_REMEDIATION_RE = re.compile(
    r"fix|→|re-?run|then run|run[\s:]|`[^`]*`|harness_cli\.py",
    re.IGNORECASE,
)

_WINDOW = 5  # statements scanned forward from the [BLOCKED] print, cap


def _string_content(node: ast.expr) -> str:
    """Literal string content of a Constant or JoinedStr (f-string) node —
    FormattedValue (the {dynamic} parts) contributes nothing, which is
    correct: we only care about the AUTHOR-WRITTEN literal text."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(
            v.value for v in node.values
            if isinstance(v, ast.Constant) and isinstance(v.value, str)
        )
    return ""


def _print_text(stmt: ast.stmt) -> "str | None":
    """Return the literal text of a top-level `print(...)` expression-
    statement, or None if this statement isn't a direct print call
    (nested prints inside a for/if/while are handled separately by
    _all_print_text_in — this narrow form is only used to find the
    [BLOCKED] anchor itself, which is always a direct print)."""
    if not (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call)):
        return None
    call = stmt.value
    if not (isinstance(call.func, ast.Name) and call.func.id == "print"):
        return None
    return "\n".join(_string_content(a) for a in call.args)


def _all_print_text_in(stmt: ast.stmt) -> str:
    """All print() call text anywhere inside stmt, including nested
    inside a for/if/while/try body (Round 13 站2c: a [BLOCKED] block's
    remediation line is sometimes inside a `for item in ...: print(...)`
    loop printing one line per violation — a top-level-only scan would
    false-positive on those as "no remediation found")."""
    parts = []
    for node in ast.walk(stmt):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "print"):
            parts.append("\n".join(_string_content(a) for a in node.args))
    return "\n".join(parts)


def _blocked_sites_missing_remediation(tree: ast.Module) -> list[int]:
    """Walk every statement list (function/if/for/while/try/with body) in
    the module; for each [BLOCKED] print found, scan the window starting
    there for a remediation marker."""
    hits: list[int] = []
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(body, list):
            continue
        for i, stmt in enumerate(body):
            text = _print_text(stmt)
            if text is None or "[BLOCKED]" not in text:
                continue
            window = body[i : i + _WINDOW]
            combined = ""
            for w in window:
                combined += _all_print_text_in(w) + "\n"
                if isinstance(w, (ast.Return, ast.Break, ast.Continue)):
                    break
            if not _REMEDIATION_RE.search(combined):
                hits.append(stmt.lineno)
    return sorted(set(hits))


def test_all_blocked_messages_in_hot_paths_carry_a_remediation_element():
    violations: list[str] = []
    for relpath in _TARGET_FILES:
        path = REPO / relpath
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for lineno in _blocked_sites_missing_remediation(tree):
            violations.append(f"{relpath}:{lineno}")
    assert not violations, (
        "agent-facing [BLOCKED] message with no remediation element (fix/→/"
        "re-run/a concrete command) in its immediate window — workflow JS is "
        "told to act on [BLOCKED] text verbatim, so a message with nothing "
        "to act on leaves the agent stalled or guessing:\n  "
        + "\n  ".join(violations)
    )


# ---- scanner self-tests (negative space) -----------------------------------

def _scan_source(src: str) -> list[int]:
    return _blocked_sites_missing_remediation(ast.parse(src))


def test_scanner_flags_blocked_with_no_remediation():
    src = (
        "def f():\n"
        "    print('\\n[BLOCKED] Something failed.')\n"
        "    return 1\n"
    )
    assert _scan_source(src) == [2]


def test_scanner_ignores_blocked_with_fix_hint_on_same_line():
    src = (
        "def f():\n"
        "    print('\\n[BLOCKED] Something failed. Fix: do the thing.')\n"
        "    return 1\n"
    )
    assert _scan_source(src) == []


def test_scanner_ignores_blocked_with_followup_print_containing_fix():
    src = (
        "def f():\n"
        "    print('\\n[BLOCKED] Linting failed.')\n"
        "    print('  Please fix the errors before advancing.')\n"
        "    return 18\n"
    )
    assert _scan_source(src) == []


def test_scanner_ignores_blocked_with_arrow_marker():
    src = (
        "def f():\n"
        "    print('\\n[BLOCKED] X missing.')\n"
        "    print('  → run generate-sab to fix.')\n"
        "    return 1\n"
    )
    assert _scan_source(src) == []


def test_scanner_ignores_blocked_with_command_backtick():
    src = (
        "def f():\n"
        "    print('\\n[BLOCKED] X missing.')\n"
        "    print('  Run `harness_cli.py manifest --fr-ids FR-01`')\n"
        "    return 1\n"
    )
    assert _scan_source(src) == []


def test_scanner_does_not_search_past_the_return():
    """Negative: a remediation hint AFTER the return (in unrelated later
    code) must not count — the window stops at the terminal statement."""
    src = (
        "def f():\n"
        "    print('\\n[BLOCKED] X missing.')\n"
        "    return 1\n"
        "    print('Fix: unreachable, must not count')\n"
    )
    assert _scan_source(src) == [2]


def test_scanner_finds_remediation_nested_inside_a_for_loop():
    """Regression pin: cli/phase_cmds.py's SAB-violations [BLOCKED] block
    prints its "→ Create the file OR remove its declaration" remediation
    line INSIDE a `for _item in _sab_medium:` loop, not at the top level
    of the enclosing function body — the scanner must still find it."""
    src = (
        "def f(items):\n"
        "    print('\\n[BLOCKED] X violations:')\n"
        "    for item in items:\n"
        "        print(f'  {item}')\n"
        "        print('    \\u2192 fix it')\n"
        "    return 12\n"
    )
    assert _scan_source(src) == []


def test_scanner_handles_fstring_blocked_marker():
    src = (
        "def f(x):\n"
        "    print(f'\\n[BLOCKED] {x} missing.')\n"
        "    return 1\n"
    )
    assert _scan_source(src) == [2]
