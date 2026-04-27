"""
CRG Bridge: Interface for the Code Review Graph (CRG) analysis tools.

Provides methods for structural reconnaissance, context retrieval, impact analysis,
and structural drift verification using the SSI toolchain.
"""

from __future__ import annotations
import json
import os
import subprocess
from pathlib import Path


class CRGBridge:
    """Wraps software_self_improvement crg_integration.py + crg_analysis.py."""

    _available: bool | None = None

    def is_available(self) -> bool:
        """Check if the CRG MCP tools and required libraries are available."""
        if self._available is None:
            r = subprocess.run(
                ["python3", "-c", "import mcp__code_review_graph"],
                capture_output=True,
                check=False
            )
            self._available = r.returncode == 0
        return self._available

    def run_reconnaissance(self, project_root: str) -> dict:
        """
        Execute full structural reconnaissance to seed the issue registry.
        
        Args:
            project_root: Path to the target project.

        Returns:
            A dictionary containing reconnaissance data.
        """
        if not self.is_available():
            return {}
        subprocess.run(
            ["python3", "scripts/crg_integration.py", "ensure", project_root],
            capture_output=True, text=True, cwd=self._ssi_root(),
            check=False
        )
        p = Path(project_root) / ".sessi-work" / "crg_reconnaissance.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}

    def get_minimal_context(self, project_root: str, dimension: str) -> dict:
        """
        Retrieve minimal CRG context for a specific quality dimension.
        
        Args:
            project_root: Path to the target project.
            dimension: The quality dimension being evaluated.

        Returns:
            A dictionary containing structural context hints.
        """
        if not self.is_available():
            return {}
        r = subprocess.run(
            ["python3", "scripts/crg_integration.py", "context", project_root, dimension],
            capture_output=True, text=True, cwd=self._ssi_root(),
            check=False
        )
        try:
            return json.loads(r.stdout)
        except Exception:
            return {}

    def check_impact(self, project_root: str, ref: str = "HEAD", threshold: float = 0.7) -> bool:
        """
        Check if changes since 'ref' are risky based on structural impact.
        
        Args:
            project_root: Path to the target project.
            ref: Git reference to compare against.
            threshold: Risk score threshold (0.0 - 1.0).

        Returns:
            True if the impact exceeds the threshold or touches hub nodes.
        """
        if not self.is_available():
            return False
        r = subprocess.run(
            ["python3", "scripts/crg_integration.py", "risky",
             project_root, ref, str(threshold)],
            capture_output=True, cwd=self._ssi_root(),
            check=False
        )
        return r.returncode == 1   # convention: 1=risky, 0=safe

    def check_drift(self, project_root: str, threshold: float = 0.4) -> bool:
        """
        Verify structural drift after an improvement round.
        
        Args:
            project_root: Path to the target project.
            threshold: Maximum allowed structural drift.

        Returns:
            True if drift exceeds the threshold.
        """
        if not self.is_available():
            return False
        p = Path(project_root) / ".sessi-work" / "crg_metrics.json"
        if not p.exists():
            return False
        data = json.loads(p.read_text(encoding="utf-8"))
        return data.get("structural_drift", 0) > threshold

    def load_metrics(self, project_root: str) -> dict:
        """Load calculated CRG metrics from the work directory."""
        p = Path(project_root) / ".sessi-work" / "crg_metrics.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}

    def _ssi_root(self) -> str:
        """Resolve the SSI toolchain root directory."""
        return os.environ.get("SSI_ROOT", "software_self_improvement")
