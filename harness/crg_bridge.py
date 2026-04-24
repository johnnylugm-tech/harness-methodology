# harness/crg_bridge.py
# §6.5: 4-point CRG integration wrapper.
# Graceful degradation: all methods are no-ops if CRG not installed.
from __future__ import annotations
import json
import os
import subprocess
from pathlib import Path


class CRGBridge:
    """Wraps software_self_improvement crg_integration.py + crg_analysis.py."""

    _available: bool | None = None

    def is_available(self) -> bool:
        if self._available is None:
            r = subprocess.run(
                ["python3", "-c", "import mcp__code_review_graph"],
                capture_output=True,
            )
            self._available = r.returncode == 0
        return self._available

    # Point 1: Structural Reconnaissance (Gate 3/4 phase entry)
    def run_reconnaissance(self, project_root: str) -> dict:
        """9 CRG queries → seed issue_registry. ~3,900 tokens, once per session."""
        if not self.is_available():
            return {}
        subprocess.run(
            ["python3", "scripts/crg_integration.py", "ensure", project_root],
            capture_output=True, text=True, cwd=self._ssi_root(),
        )
        p = Path(project_root) / ".sessi-work" / "crg_reconnaissance.json"
        return json.loads(p.read_text()) if p.exists() else {}

    # Point 2: Tier 3 dimension guidance (get_minimal_context before each eval)
    def get_minimal_context(self, project_root: str, dimension: str) -> dict:
        """Returns minimal CRG context. Reduces Tier 3 eval tokens 30–50%."""
        if not self.is_available():
            return {}
        r = subprocess.run(
            ["python3", "scripts/crg_integration.py", "context", project_root, dimension],
            capture_output=True, text=True, cwd=self._ssi_root(),
        )
        try:
            return json.loads(r.stdout)
        except Exception:
            return {}

    # Point 3: Pre-fix safety gate (before each improvement round)
    def check_impact(self, project_root: str, ref: str = "HEAD", threshold: float = 0.7) -> bool:
        """True if risky (risk_score >= threshold OR hub/bridge touched) → defer fix."""
        if not self.is_available():
            return False
        r = subprocess.run(
            ["python3", "scripts/crg_integration.py", "risky",
             project_root, ref, str(threshold)],
            capture_output=True, cwd=self._ssi_root(),
        )
        return r.returncode == 1   # convention: 1=risky, 0=safe

    # Point 4: Post-round structural drift verification
    def check_drift(self, project_root: str, threshold: float = 0.4) -> bool:
        """True if structural drift > threshold → trigger revert protocol."""
        if not self.is_available():
            return False
        p = Path(project_root) / ".sessi-work" / "crg_metrics.json"
        if not p.exists():
            return False
        return json.loads(p.read_text()).get("structural_drift", 0) > threshold

    def load_metrics(self, project_root: str) -> dict:
        """Load 6 formula-driven CRG signals from crg_metrics.json."""
        p = Path(project_root) / ".sessi-work" / "crg_metrics.json"
        return json.loads(p.read_text()) if p.exists() else {}

    def _ssi_root(self) -> str:
        return os.environ.get("SSI_ROOT", "software_self_improvement")
