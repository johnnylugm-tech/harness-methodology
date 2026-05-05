"""
Feedback-aware AutoQualityGate hook.
"""

from __future__ import annotations
import sys
from pathlib import Path
from . import AutoQualityGate

__all__ = ["AutoQualityGateWithFeedback"]


class AutoQualityGateWithFeedback(AutoQualityGate):
    """Enhanced AutoQualityGate that submits feedback automatically."""

    def __init__(self, *args, feedback_store=None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._feedback_store = feedback_store
        self._adapter = None

    def _get_adapter(self):
        if self._adapter is None and self._feedback_store is not None:
            core_dir = Path(__file__).parent.parent
            if str(core_dir) not in sys.path:
                sys.path.insert(0, str(core_dir))
            from feedback.quality_gate_adapter import QualityGateFeedbackAdapter
            self._adapter = QualityGateFeedbackAdapter(self._feedback_store)
        return self._adapter

    def check(self, *args, **kwargs) -> dict:
        """Run gate feedback check against current phase context."""
        result = super().check(*args, **kwargs)
        adapter = self._get_adapter()
        if adapter and result.get("phase") is not None:
            adapter.on_quality_gate_complete(
                gate_result=result, phase=result["phase"],
                artifacts=kwargs.get("artifacts", {}),
            )
        return result

    def run(self, *args, **kwargs) -> dict:
        """Execute feedback hook and return structured results."""
        return self.check(*args, **kwargs)
