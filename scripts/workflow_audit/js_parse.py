"""Parse generated workflow JS the way the runtime does, not the way `node` does.

Round 60 站1, extracted verbatim from
``tests/test_workflow_js_conventions.py::test_node_check_syntax`` so the
generator's pre-write check and that test share one implementation.

Round 23 站2 established why the naive form is useless: a bare
``node --check <file>.js`` parses the file as CommonJS, ``export const meta``
is a syntax error on line 1, and node returns 0 anyway. Every workflow file
starts with ``export const meta``, so the check could never fail for any of
them. Re-measured 2026-08-19 on a phase3 file carrying the `f4be095`
apostrophe: bare ``node --check`` exits 0, the wrapper below exits 1 and
names the line.

The runtime evaluates the file body with top-level await and top-level
return — i.e. as a function body — which is what
``scripts/workflowgen/js_src/sim_runner.mjs`` reproduces. Wrapping the same
way before ``node --check`` makes this a real parse of real script text.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path

__all__ = ["node_available", "parse_problem"]


def node_available() -> bool:
    """Node is a dev-only dependency; callers decide what its absence means."""
    return shutil.which("node") is not None


def parse_problem(js_text: str) -> "str | None":
    """``None`` when *js_text* parses as a workflow body, else the diagnostic.

    Raises ``FileNotFoundError`` when node is absent rather than returning
    ``None`` — "could not check" must never be indistinguishable from
    "checked and clean" (Round 30). Call :func:`node_available` first.
    """
    body, count = re.subn(
        r"^export const meta", "const meta", js_text, count=1, flags=re.MULTILINE,
    )
    if not count:
        return "no `export const meta` to unwrap — not a workflow file"
    with tempfile.TemporaryDirectory() as tmp:
        # `.cjs` so the wrapper is parsed as a script, matching the runtime's
        # non-module evaluation of the body.
        wrapped = Path(tmp) / "wrapped.cjs"
        wrapped.write_text(
            "(async function (agent, phase, log, args, budget) {\n"
            + body + "\n})\n",
            encoding="utf-8",
        )
        result = subprocess.run(
            ["node", "--check", str(wrapped)],
            capture_output=True, text=True, timeout=30,
        )
    if result.returncode == 0:
        return None
    return result.stderr.strip() or f"node --check exit {result.returncode}"
