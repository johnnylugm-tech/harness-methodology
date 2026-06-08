"""harness/crg_api.py — universal subprocess backend for CRG analysis tools.

The CRG MCP tools (review_context, impact_radius, affected_flows, minimal_context,
knowledge_gaps, semantic_search, …) are normally reached via the `mcp_tools`
Python module that Claude Code injects into an interactive session. Outside that
session (harness_cli.py as a Bash subprocess, CI, plain Python) `mcp_tools` does
not exist, so every CRG feature routed through it silently no-ops.

This module provides the same capability without `mcp_tools`, by driving CRG's
own `code_review_graph.tools` Python API through a subprocess under CRG's
interpreter — the exact pattern crg_independent.py already uses for the
architecture score. It reuses crg_independent's binary/interpreter/runner
helpers so there is a single source of truth for "how to talk to CRG".

`make_tool(name)` returns a callable shaped like the mcp tool it replaces
(``f(repo_root=..., **kwargs) -> dict``), degrading gracefully (empty dict +
one-line stderr note) when CRG is not installed or the call fails — matching the
no-op contract crg_bridge already relies on.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Callable

from harness.crg_independent import (
    CrgIndependentError,
    _crg_interpreter,
    _run,
    crg_binary,
)

_RUNNER = Path(__file__).parent / "ssi" / "scripts" / "crg_tool_runner.py"
_TOOL_TIMEOUT = 180  # seconds; read-only analysis on an already-built graph


def call_crg_tool(repo_root: str, func_name: str, **kwargs: Any) -> dict:
    """Run ``code_review_graph.tools.<func_name>(repo_root=…, **kwargs)`` out-of-process.

    Returns the tool's result dict. Raises CrgIndependentError if CRG is not on
    PATH or the subprocess fails (callers that want graceful behaviour should use
    make_tool() instead).
    """
    binary = crg_binary()  # raises CrgIndependentError if absent
    interp = _crg_interpreter(binary)
    root = str(Path(repo_root).resolve())
    payload = json.dumps({"func": func_name, "repo_root": root, "kwargs": kwargs})
    stdout = _run(
        [interp, str(_RUNNER), payload],
        cwd=root, timeout=_TOOL_TIMEOUT, label=f"crg_tool_runner {func_name}",
    )
    try:
        return json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise CrgIndependentError(
            f"crg_tool_runner {func_name} produced invalid JSON: {exc}"
        ) from exc


def make_tool(func_name: str) -> Callable[..., dict]:
    """Return a graceful mcp-tool-shaped callable backed by the CRG subprocess.

    The returned callable accepts ``repo_root=...`` plus arbitrary kwargs (the
    same shape as the mcp tool it stands in for) and never raises: on any failure
    it prints a one-line note to stderr and returns ``{}``.
    """
    def _tool(repo_root: str | None = None, **kwargs: Any) -> dict:
        try:
            return call_crg_tool(repo_root or ".", func_name, **kwargs)
        except (CrgIndependentError, OSError) as exc:
            print(f"[CRG] {func_name} unavailable via subprocess: {exc}", file=sys.stderr)
            return {}
    return _tool
