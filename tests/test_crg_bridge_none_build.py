"""Regression tests for the ``_crg_build == None`` runtime bug.

In Claude Code sessions where the MCP tools for code-review-graph are
declared-deferred (ToolSearch not invoked yet), the ``mcp_tools`` module
exists but ``mcp__code_review_graph__build_or_update_graph_tool`` resolves
to ``None``. The legacy ``_CRG_AVAILABLE`` import-check returns ``True``
because the import succeeded, so ``refresh_graph`` falls into
``_crg_build(...)`` → ``TypeError: 'NoneType' object is not callable``.

Fix contract: ``refresh_graph`` MUST treat ``_crg_build`` as unavailable
when it is ``None`` (or otherwise non-callable), and degrade gracefully
(print a one-line warning, return). Same applies to ``run_reconnaissance``
which also calls ``_crg_build`` directly.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture()
def fresh_crg_bridge_module():
    """Force re-import of harness.crg_bridge with a custom mcp_tools mock.

    The module's import-time binding ``_crg_build = ...`` is captured the
    first time ``harness.crg_bridge`` is imported. To install a mock that
    simulates the deferred-tool runtime (where ``mcp_tools`` exists but
    ``_crg_build`` is None), we have to install the mock BEFORE importing
    ``harness.crg_bridge``.
    """
    # Wipe any prior cached import so the fixture re-runs the top-level
    # try/except with our mock in place.
    for mod in list(sys.modules):
        if mod == "harness.crg_bridge" or mod.startswith("harness.crg_bridge"):
            del sys.modules[mod]

    class _MockMcpTools:
        """mcp_tools exists. ``_crg_build`` resolves to None (deferred tool).
        Other tools are normal MagicMock callables so the rest of the
        bridge contract remains exercisable."""

        def __getattr__(self, name):
            if name == "mcp__code_review_graph__build_or_update_graph_tool":
                return None  # the deferred-tool runtime bug under test
            from unittest.mock import MagicMock
            return MagicMock()

    saved_mcp_tools = sys.modules.get("mcp_tools")
    sys.modules["mcp_tools"] = _MockMcpTools()
    mod = importlib.import_module("harness.crg_bridge")
    try:
        yield mod
    finally:
        # Cleanup: restore the previous mcp_tools (conftest's MagicMock) so
        # other tests in the suite still see their own contract.
        if saved_mcp_tools is not None:
            sys.modules["mcp_tools"] = saved_mcp_tools
        else:
            sys.modules.pop("mcp_tools", None)
        for modname in list(sys.modules):
            if modname.startswith("harness.crg_bridge"):
                del sys.modules[modname]


def test_refresh_graph_tolerates_none_build_tool(fresh_crg_bridge_module, capsys):
    """refresh_graph() must not raise when _crg_build is None. It should
    gracefully skip (one-line stderr message) instead of letting
    TypeError crash the caller (run-gate → prepare_gate → refresh_graph)."""
    from harness.crg_bridge import CRGBridge

    bridge = CRGBridge()
    # Must not raise.
    bridge.refresh_graph("/tmp/anywhere")

    captured = capsys.readouterr()
    combined = captured.out + captured.err
    # Graceful skip emits some user-visible evidence, but MUST NOT raise
    # TypeError. The legacy module-level False-branch warned from
    # _check_available; the new None-tool branch warns from refresh_graph
    # itself. Accept either.
    assert "TypeError" not in combined, (
        f"refresh_graph must not raise TypeError when _crg_build is None; "
        f"got stderr={captured.err!r}"
    )


def test_run_reconnaissance_tolerates_none_build_tool(
    fresh_crg_bridge_module, tmp_path
):
    """run_reconnaissance() also calls _crg_build; same fix applies.
    When the build tool is None, it should not raise TypeError — the
    method may return {} (graceful) when CRG unavailable."""
    from harness.crg_bridge import CRGBridge

    bridge = CRGBridge()

    try:
        result = bridge.run_reconnaissance(str(tmp_path))
        graceful = True
    except FileNotFoundError:
        graceful = False
    except TypeError as exc:
        pytest.fail(
            f"run_reconnaissance raised TypeError when _crg_build is None: {exc}"
        )

    if graceful:
        assert result == {}, (
            f"when CRG unavailable, run_reconnaissance should return {{}} "
            f"or raise FileNotFoundError, got {result!r}"
        )
