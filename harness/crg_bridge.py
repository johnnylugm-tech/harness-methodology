"""
CRG Bridge: Interface for the Code Review Graph (CRG) analysis tools.

Provides methods for structural reconnaissance, context retrieval, impact analysis,
and structural drift verification using CRG MCP tools directly.
"""

from __future__ import annotations

import json
from pathlib import Path

# CRG MCP tools are injected by Claude Code runtime — only available inside CC
try:
    from mcp_tools import (  # type: ignore[import-untyped]
        mcp__code_review_graph__build_or_update_graph_tool as _crg_build,
        mcp__code_review_graph__get_minimal_context_tool as _crg_minimal_context,
        mcp__code_review_graph__detect_changes_tool as _crg_detect_changes,
    )

    _CRG_MCP_AVAILABLE = True
except ImportError:
    _CRG_MCP_AVAILABLE = False


class CRGBridge:
    """Wraps CRG MCP tools for structural analysis of the target project."""

    def is_available(self) -> bool:
        """Check if CRG MCP tools are available in this runtime."""
        return _CRG_MCP_AVAILABLE

    def run_reconnaissance(self, project_root: str) -> dict:
        """
        Execute full structural reconnaissance to seed the issue registry.

        Returns reconnaissance data dict, or empty dict if CRG unavailable.
        """
        if not _CRG_MCP_AVAILABLE:
            return {}
        try:
            _crg_build(repo_root=project_root, full_rebuild=True)
        except Exception:
            return {}
        p = Path(project_root) / ".sessi-work" / "crg_reconnaissance.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}

    def get_minimal_context(self, project_root: str, dimension: str) -> dict:
        """
        Retrieve minimal CRG context for a specific quality dimension.
        """
        if not _CRG_MCP_AVAILABLE:
            return {}
        try:
            return _crg_minimal_context(task=dimension, repo_root=project_root)
        except Exception:
            return {}

    def check_impact(self, project_root: str, ref: str = "HEAD", threshold: float = 0.7) -> bool:
        """
        Check if changes since 'ref' are risky based on structural impact.

        Returns True if risk_score >= threshold.
        """
        if not _CRG_MCP_AVAILABLE:
            return False
        try:
            data = _crg_detect_changes(
                base=ref, repo_root=project_root, detail_level="standard"
            )
            rs = data.get("risk_score", 0)
            return float(rs) >= threshold if rs is not None else False
        except Exception:
            return False

    def check_drift(self, project_root: str, threshold: float = 0.4) -> bool:
        """Verify structural drift after an improvement round."""
        p = Path(project_root) / ".sessi-work" / "crg_metrics.json"
        if not p.exists():
            return False
        data = json.loads(p.read_text(encoding="utf-8"))
        return data.get("structural_drift", 0) > threshold

    def load_metrics(self, project_root: str) -> dict:
        """Load calculated CRG metrics from the work directory."""
        p = Path(project_root) / ".sessi-work" / "crg_metrics.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
