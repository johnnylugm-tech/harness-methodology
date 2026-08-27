"""`node --test`'s output format is a contract, not whatever node defaults to.

Round 80 站1. `tests/test_workflow_sim.py` reads the pass count out of node's
stdout to enforce a coverage floor — a silently shrunken sim suite is a dead
guard, which is the failure mode Round 11 站0 revived it from. It matched

    ^# pass (\\d+)$

which is node's **TAP** reporter. Node's default reporter for a non-TTY stdout
changed, and on node v26 the same 130-passing run prints

    ℹ pass 130

so the regex found nothing and the guard failed with `assert None` while every
one of the 130 node tests passed. Measured on this machine at
`dff609e6`: `scripts/self_check.sh` red, node v26.0.0. The same commit's CI run
was green — the GitHub runner ships an older node — so the guard's correctness
depended on which node the machine happened to have, not on anything the repo
states.

That is the defect this pins: reading a tool's *default human output* is
reading a proxy for the fact. `--test-reporter=tap` makes the format the
argv's own statement. Measured, both forms exit 0 on the same suite, so
pinning it changes the exit status of nothing.

WHY A SCAN AND NOT A ONE-LINE FIX

Three call sites had it (`tests/test_workflow_sim.py`,
`tests/test_workflowgen_js_units.py`, `core/harness_repair.py`) and only one
of them parsed the output, so only one broke. The other two are the same
latent defect one consumer away — the sibling-not-swept shape this repo has
recorded at Rounds 20, 39 and 74. This file is what stops a fourth appearing.

WHAT IS AND IS NOT SCANNED

Only argv **list literals** — the form every `node --test` invocation in this
repo uses. A shell-string form (`bash -c "node --test ..."`) would need its own
rule, and inventing one now would mean writing a regex against prose for a
population of zero: the mistake Round 56 counted to nine. `node --check`, which
takes no reporter, is not matched: the scan requires the exact token `--test`.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = pytest.mark.core

REPO = Path(__file__).resolve().parents[1]
_SCAN_DIRS = ("tests", "cli", "core", "harness", "scripts", "detection")

_REPORTER_PREFIX = "--test-reporter"


def _string_elements(node: ast.List) -> list[str]:
    return [
        e.value for e in node.elts
        if isinstance(e, ast.Constant) and isinstance(e.value, str)
    ]


def _argv_literal(source: str) -> ast.List:
    """Parse ``source`` as a bare list literal expression."""
    stmt = ast.parse(source).body[0]
    assert isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.List)
    return stmt.value


def _unpinned_node_test_argvs() -> list[tuple[str, int]]:
    """Every argv list literal that runs `node --test` without naming a reporter."""
    offenders: list[tuple[str, int]] = []
    for directory in _SCAN_DIRS:
        root = REPO / directory
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            if path.name == Path(__file__).name:
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except SyntaxError:  # pragma: no cover - a parse failure is ruff's job
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.List):
                    continue
                strings = _string_elements(node)
                if "node" not in strings or "--test" not in strings:
                    continue
                if any(s.startswith(_REPORTER_PREFIX) for s in strings):
                    continue
                offenders.append((str(path.relative_to(REPO)), node.lineno))
    return offenders


def test_every_node_test_invocation_pins_its_reporter():
    offenders = _unpinned_node_test_argvs()
    assert not offenders, (
        "these argv lists run `node --test` without naming a reporter, so what "
        "they produce is whatever the installed node defaults to:\n"
        + "\n".join(f"  {p}:{line}" for p, line in offenders)
        + f"\n\nAdd '{_REPORTER_PREFIX}=tap' to the argv. The pass-count line "
        f"the sim testbed reads (`# pass N`) is TAP's; node's default reporter "
        f"prints `ℹ pass N` and the guard silently stops finding it."
    )


def test_the_scan_can_see_an_unpinned_argv():
    """The scan's own witness — a guard that cannot fail is not a guard.

    Round 78 站6's rule in the other direction: this asserts the detector
    reacts to the shape it exists to find, without reading any source text.
    """
    unpinned = _argv_literal('["node", "--test", "x.mjs"]')
    pinned = _argv_literal('["node", "--test", "--test-reporter=tap", "x.mjs"]')

    assert "node" in _string_elements(unpinned)
    assert not any(
        s.startswith(_REPORTER_PREFIX) for s in _string_elements(unpinned)
    ), "the unpinned fixture must not carry a reporter, or the scan proves nothing"
    assert any(
        s.startswith(_REPORTER_PREFIX) for s in _string_elements(pinned)
    ), "the pinned fixture must carry a reporter"
