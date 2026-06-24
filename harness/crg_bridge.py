"""
CRG Bridge: Programmatic API for HarnessBridge and AutoFixEngine.

Provides methods for structural reconnaissance, context retrieval, impact analysis,
and structural drift verification using CRG MCP tools directly.

CRG is mandatory for Gate 3/4 structural dimensions (same tier as ruff/mypy/pytest).
When the CRG MCP server is not available (standalone Python, non-Claude Code sessions),
all methods degrade gracefully to no-ops returning empty dicts or False.

For standalone CLI usage (bash commands in prompts), see
harness/ssi/scripts/crg_integration.py.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any

__log = logging.getLogger(__name__)

# Core CRG tools — conditional import with graceful degradation.
# In Claude Code sessions the mcp_tools module is injected by the MCP runtime.
# In standalone Python (e.g. `python3 harness_cli.py manifest`) it does not exist.
try:
    from mcp_tools import (  # type: ignore[import-untyped]
        mcp__code_review_graph__build_or_update_graph_tool as _crg_build,
        mcp__code_review_graph__get_minimal_context_tool as _crg_minimal_context,
        mcp__code_review_graph__detect_changes_tool as _crg_detect_changes,
    )
    _CRG_AVAILABLE = True
except ImportError:
    # Outside Claude Code (Bash subprocess, CI, plain Python): fall back to the
    # crg_api subprocess backend so read-only analysis tools still work. Graph
    # lifecycle (`build`) stays None — the graph is (re)built by crg_independent
    # during finalize_gate, so crg_bridge never needs to build it here.
    from harness import crg_api as _crg_api
    _crg_build = None  # type: ignore[assignment]
    _crg_minimal_context = _crg_api.make_tool("get_minimal_context")  # type: ignore[assignment]
    _crg_detect_changes = _crg_api.make_tool("detect_changes_func")  # type: ignore[assignment]
    _CRG_AVAILABLE = False

# Extended CRG tools — imported individually; None if unavailable in this runtime
try:
    from mcp_tools import (  # type: ignore[import-untyped]
        mcp__code_review_graph__get_hub_nodes_tool as _crg_hub_nodes,
    )
except ImportError:  # pragma: no cover
    from harness.crg_api import make_tool as _make_tool
    _crg_hub_nodes = _make_tool("get_hub_nodes_func")

try:
    from mcp_tools import (  # type: ignore[import-untyped]
        mcp__code_review_graph__list_communities_tool as _crg_list_communities,
    )
except ImportError:  # pragma: no cover
    from harness.crg_api import make_tool as _make_tool
    _crg_list_communities = _make_tool("list_communities_func")

try:
    from mcp_tools import (  # type: ignore[import-untyped]
        mcp__code_review_graph__get_knowledge_gaps_tool as _crg_knowledge_gaps,
    )
except ImportError:  # pragma: no cover
    from harness.crg_api import make_tool as _make_tool
    _crg_knowledge_gaps = _make_tool("get_knowledge_gaps_func")

try:
    from mcp_tools import (  # type: ignore[import-untyped]
        mcp__code_review_graph__semantic_search_nodes_tool as _crg_semantic_search,
    )
except ImportError:  # pragma: no cover
    from harness.crg_api import make_tool as _make_tool
    _crg_semantic_search = _make_tool("semantic_search_nodes")

try:
    from mcp_tools import (  # type: ignore[import-untyped]
        mcp__code_review_graph__query_graph_tool as _crg_query_graph,
    )
except ImportError:  # pragma: no cover
    from harness.crg_api import make_tool as _make_tool
    _crg_query_graph = _make_tool("query_graph")

try:
    from mcp_tools import (  # type: ignore[import-untyped]
        mcp__code_review_graph__find_large_functions_tool as _crg_large_funcs,
    )
except ImportError:  # pragma: no cover
    from harness.crg_api import make_tool as _make_tool
    _crg_large_funcs = _make_tool("find_large_functions")

try:
    from mcp_tools import (  # type: ignore[import-untyped]
        mcp__code_review_graph__list_flows_tool as _crg_list_flows,
    )
except ImportError:  # pragma: no cover
    from harness.crg_api import make_tool as _make_tool
    _crg_list_flows = _make_tool("list_flows")

try:
    from mcp_tools import (  # type: ignore[import-untyped]
        mcp__code_review_graph__refactor_tool as _crg_refactor,
    )
except ImportError:  # pragma: no cover
    _crg_refactor = None


class CRGBridge:
    """Wraps CRG MCP tools for structural analysis of the target project.

    When the CRG MCP server is not available (standalone Python, non-Claude Code
    sessions), all methods return empty dicts or False — graceful degradation.
    """

    def __init__(self):
        self._warned = False

    def _check_available(self) -> bool:
        """Return True if the in-process mcp_tools backend is importable.

        Gates only the graph-lifecycle methods (refresh_graph / run_reconnaissance),
        whose build side-effects belong to crg_independent outside Claude Code. The
        read-only analysis tools no longer depend on this — they fall back to the
        crg_api subprocess backend (see the import block above).
        """
        if not _CRG_AVAILABLE and not self._warned:
            print(
                "[CRG] INFO: mcp_tools not injected (running outside Claude Code).\n"
                "  Graph build/reconnaissance via crg_bridge is skipped here — the graph "
                "is built by crg_independent during finalize_gate.\n"
                "  Read-only analysis tools (context/impact/flows/review) run via the "
                "crg_api subprocess backend instead.",
                file=sys.stderr,
            )
            self._warned = True
        return _CRG_AVAILABLE

    # ── Graph lifecycle ────────────────────────────────────────────────────

    def refresh_graph(self, project_root: str) -> None:
        if not self._check_available():
            return
        _crg_build(repo_root=project_root, full_rebuild=False)  # type: ignore[misc]

    def run_reconnaissance(self, project_root: str) -> dict:
        if not self._check_available():
            return {}
        _crg_build(repo_root=project_root, full_rebuild=True)  # type: ignore[misc]
        p = Path(project_root) / ".sessi-work" / "crg_reconnaissance.json"
        if not p.exists():
            raise FileNotFoundError(
                f"CRG reconnaissance data not found at {p}. "
                "Run the CRG reconnaissance protocol first."
            )
        return json.loads(p.read_text(encoding="utf-8"))

    # ── Context & impact ───────────────────────────────────────────────────

    def get_minimal_context(self, project_root: str, dimension: str) -> dict:
        if _crg_minimal_context is None:
            return {}
        return _crg_minimal_context(task=dimension, repo_root=project_root)  # type: ignore[misc]

    def check_impact(self, project_root: str, ref: str = "HEAD", threshold: float = 0.7) -> bool:
        if _crg_detect_changes is None:
            return False
        data = _crg_detect_changes(  # type: ignore[misc]
            base=ref, repo_root=project_root, detail_level="standard"
        )
        rs = data.get("risk_score", 0)
        return float(rs) >= threshold if rs is not None else False

    # ── Drift & metrics ────────────────────────────────────────────────────

    def check_drift(
        self, project_root: str, threshold: float = 0.4, base: str = "HEAD~1",
    ) -> bool:
        if _crg_detect_changes is None:
            return False
        data = _crg_detect_changes(  # type: ignore[misc]
            base=base, repo_root=project_root, detail_level="minimal"
        )
        rs = data.get("risk_score", 0)
        return float(rs) >= threshold if rs is not None else False

    def load_metrics(self, project_root: str) -> dict:
        if not self._check_available():
            return {}
        p = Path(project_root) / ".sessi-work" / "crg_metrics.json"
        if not p.exists():
            raise FileNotFoundError(
                f"CRG metrics not found at {p}. "
                "Run crg_analysis.py metrics to compute them."
            )
        return json.loads(p.read_text(encoding="utf-8"))

    # ── Structural analysis (extended tools) ────────────────────────────────

    def _call_crg(self, fn, kwargs: dict[str, Any]) -> dict[str, Any]:
        """Call a CRG tool, filtering kwargs against its signature (Bug #29 fix).

        Some in-process CRG tools (e.g. mcp__code_review_graph__*_tool) do
        not accept every kwarg the harness wants to pass. Calling with
        unknown kwargs raises TypeError; the TypeError is silently caught
        by the fallback path and the entire CRG enrichment is lost. To
        prevent this, introspect the bound function's signature and drop
        unsupported kwargs before invocation.
        """
        if fn is None:
            return {}
        import inspect
        try:
            sig = inspect.signature(fn)
            if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()):
                accepted = set(kwargs.keys())
            else:
                accepted = set(sig.parameters.keys())
        except (TypeError, ValueError):
            accepted = set(kwargs.keys())
        filtered = {k: v for k, v in kwargs.items() if k in accepted}
        return fn(**filtered)

    def get_hub_nodes(self, project_root: str, min_fan_in: int = 5) -> dict[str, Any]:
        """Return high fan-in nodes (structural chokepoints)."""
        return self._call_crg(_crg_hub_nodes, {
            "repo_root": project_root,
        })

    def list_communities(
        self, project_root: str, min_size: int = 2, sort_by: str = "size"
    ) -> dict[str, Any]:
        """List detected code communities with cohesion scores."""
        return self._call_crg(_crg_list_communities, {
            "repo_root": project_root, "min_size": min_size, "sort_by": sort_by,
        })

    def get_knowledge_gaps(self, project_root: str) -> dict[str, Any]:
        """Find untested critical paths (cross-ref with TEST_INVENTORY.yaml)."""
        return self._call_crg(_crg_knowledge_gaps, {"repo_root": project_root})

    def semantic_search(
        self, project_root: str, query: str, kind: str | None = None, limit: int = 20
    ) -> dict[str, Any]:
        """Search for code entities by name, keyword, or semantic similarity."""
        kwargs: dict[str, Any] = {"query": query, "repo_root": project_root, "limit": limit}
        if kind:
            kwargs["kind"] = kind
        return self._call_crg(_crg_semantic_search, kwargs)

    def query_graph(
        self, project_root: str, pattern: str, target: str
    ) -> dict[str, Any]:
        """
        Run a predefined graph query.

        Patterns: callers_of, callees_of, imports_of, importers_of,
        children_of, tests_for, inheritors_of, file_summary.
        """
        return self._call_crg(_crg_query_graph, {
            "pattern": pattern, "target": target, "repo_root": project_root
        })

    def find_large_functions(
        self, project_root: str, min_lines: int = 50, kind: str | None = None, limit: int = 50
    ) -> dict[str, Any]:
        """Find functions/classes/files exceeding line-count threshold."""
        kwargs: dict[str, Any] = {
            "min_lines": min_lines, "repo_root": project_root, "limit": limit,
        }
        if kind:
            kwargs["kind"] = kind
        return self._call_crg(_crg_large_funcs, kwargs)

    def list_flows(
        self, project_root: str, limit: int = 20, sort_by: str = "criticality"
    ) -> dict[str, Any]:
        """List execution flows sorted by criticality."""
        return self._call_crg(_crg_list_flows, {
            "repo_root": project_root, "limit": limit, "sort_by": sort_by,
        })

    def check_dead_code(
        self, project_root: str, kind: str | None = None
    ) -> dict[str, Any]:
        """Find unreferenced functions/classes."""
        kwargs: dict[str, Any] = {"mode": "dead_code", "repo_root": project_root}
        if kind:
            kwargs["kind"] = kind
        return self._call_crg(_crg_refactor, kwargs)

    def get_review_context(
        self,
        project_root: str,
        changed_files: list[str] | None = None,
        max_depth: int = 2,
        detail_level: str = "minimal",
    ) -> dict[str, Any]:
        """Return focused review context combining impact analysis and source snippets."""
        kwargs: dict[str, Any] = {
            "repo_root": project_root,
            "max_depth": max_depth,
            "detail_level": detail_level,
        }
        if changed_files is not None:
            kwargs["changed_files"] = changed_files
        try:
            from mcp_tools import (  # type: ignore[import-untyped]
                mcp__code_review_graph__get_review_context_tool as _f,
            )
            return _f(**kwargs)
        except ImportError:  # outside Claude Code → subprocess backend
            from harness.crg_api import make_tool
            return make_tool("get_review_context")(**kwargs)
        except Exception as exc:  # pragma: no cover
            __log.warning(
                "CRG get_review_context failed (%s: %s); returning {}",
                type(exc).__name__, exc,
            )
            return {}

    def get_impact_radius(
        self,
        project_root: str,
        changed_files: list[str] | None = None,
        max_depth: int = 2,
        base: str = "HEAD~1",
        detail_level: str = "minimal",
    ) -> dict[str, Any]:
        """Return blast radius of recent changes."""
        kwargs: dict[str, Any] = {
            "repo_root": project_root,
            "max_depth": max_depth,
            "base": base,
            "detail_level": detail_level,
        }
        if changed_files is not None:
            kwargs["changed_files"] = changed_files
        try:
            from mcp_tools import (  # type: ignore[import-untyped]
                mcp__code_review_graph__get_impact_radius_tool as _f,
            )
            return _f(**kwargs)
        except ImportError:  # outside Claude Code → subprocess backend
            from harness.crg_api import make_tool
            return make_tool("get_impact_radius")(**kwargs)
        except Exception as exc:  # pragma: no cover
            __log.warning(
                "CRG get_impact_radius failed (%s: %s); returning {}",
                type(exc).__name__, exc,
            )
            return {}

    def get_affected_flows(
        self,
        project_root: str,
        changed_files: list[str] | None = None,
        base: str = "HEAD~1",
    ) -> dict[str, Any]:
        """Return execution flows affected by recent changes."""
        kwargs: dict[str, Any] = {"repo_root": project_root, "base": base}
        if changed_files is not None:
            kwargs["changed_files"] = changed_files
        try:
            from mcp_tools import (  # type: ignore[import-untyped]
                mcp__code_review_graph__get_affected_flows_tool as _f,
            )
            return _f(**kwargs)
        except ImportError:  # outside Claude Code → subprocess backend
            from harness.crg_api import make_tool
            return make_tool("get_affected_flows_func")(**kwargs)
        except Exception as exc:  # pragma: no cover
            __log.warning(
                "CRG get_affected_flows failed (%s: %s); returning {}",
                type(exc).__name__, exc,
            )
            return {}
