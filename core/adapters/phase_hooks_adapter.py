#!/usr/bin/env python3
"""
PhaseHooks Adapter
==================
Adapts PhaseHooks for integration with external systems that cannot
directly import from core/ (CLI runners, MCP hooks, remote triggers).

Provides a dict-in / dict-out interface so callers need zero knowledge
of PhaseHooks internals.

Usage:
    from core.adapters.phase_hooks_adapter import PhaseHooksAdapter

    adapter = PhaseHooksAdapter("/path/to/project", phase=3)

    # Run preflight
    result = adapter.preflight()
    if not result["all_passed"]:
        sys.exit(1)

    # Wrap FR execution
    adapter.before_dev("FR-01")
    # ... agent execution ...
    adapter.after_dev("FR-01", {"status": "success", "confidence": 8})
    adapter.before_rev("FR-01")
    # ... reviewer ...
    adapter.after_rev("FR-01", {"status": "success", "review_status": "APPROVE"})

    # Check HR-12
    if not adapter.hr12_check("FR-01", iteration=3):
        print("PAUSE triggered")

    # Run postflight
    result = adapter.postflight()
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional


class PhaseHooksAdapter:
    """
    Thin adapter wrapping PhaseHooks with a plain dict interface.

    Lazy-imports PhaseHooks to avoid circular dependencies when
    called from external CLI or remote trigger contexts.
    """

    def __init__(self, project_path: str, phase: Optional[int] = None):
        self.project_path = str(project_path)
        self.phase = phase
        self._hooks = None  # lazy init

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _get_hooks(self):
        """Lazy-initialize PhaseHooks."""
        if self._hooks is None:
            from core.harness_config import get_value
            from core.phase_hooks import PhaseHooks
            self._hooks = PhaseHooks(
                self.project_path, phase=self.phase,
                drift_threshold=get_value(self.project_path, "drift_threshold"))
        return self._hooks

    # ------------------------------------------------------------------
    # Pre-flight
    # ------------------------------------------------------------------

    def preflight(self) -> Dict[str, Any]:
        """
        Run all pre-flight checks.

        Returns:
            dict with 'all_passed' (bool) and 'details' (per-check results)
        """
        return self._get_hooks().preflight_all()

    def preflight_fsm(self) -> Dict[str, Any]:
        """Run FSM state check only."""
        return self._get_hooks().preflight_fsm_check()

    def preflight_constitution(self) -> Dict[str, Any]:
        """Run constitution check only."""
        return self._get_hooks().preflight_constitution()

    # ------------------------------------------------------------------
    # Monitoring (per-FR)
    # ------------------------------------------------------------------

    def before_dev(self, fr_id: str) -> None:
        """Signal start of developer execution for fr_id."""
        self._get_hooks().monitoring_before_dev(fr_id)

    def after_dev(self, fr_id: str, result: Optional[Dict[str, Any]] = None) -> None:
        """
        Signal end of developer execution.

        Args:
            fr_id:  FR identifier (e.g. 'FR-01')
            result: agent output dict with at least 'status' and 'confidence'
        """
        class _Wrapper:
            def __init__(self, d):
                d = d or {}
                self.status = d.get("status", "unknown")
                self.confidence = d.get("confidence", 0)
        self._get_hooks().monitoring_after_dev(fr_id, _Wrapper(result))

    def before_rev(self, fr_id: str) -> None:
        """Signal start of reviewer execution for fr_id."""
        self._get_hooks().monitoring_before_rev(fr_id)

    def after_rev(self, fr_id: str, result: Optional[Dict[str, Any]] = None) -> None:
        """
        Signal end of reviewer execution.

        Args:
            fr_id:  FR identifier
            result: reviewer output dict with 'status', 'review_status', 'confidence'
        """
        class _Wrapper:
            def __init__(self, d):
                d = d or {}
                self.status = d.get("status", "unknown")
                self.review_status = d.get("review_status", None)
                self.confidence = d.get("confidence", 0)
        self._get_hooks().monitoring_after_rev(fr_id, _Wrapper(result))

    def hr12_check(self, fr_id: str, iteration: int, max_iterations: int = 5) -> bool:
        """
        HR-12 iteration check.

        Returns:
            True if execution may continue, False if PAUSE should be triggered.
        """
        return self._get_hooks().monitoring_hr12_check(fr_id, iteration, max_iterations)

    # ------------------------------------------------------------------
    # Post-flight
    # ------------------------------------------------------------------

    def postflight(self) -> Dict[str, Any]:
        """
        Run all post-flight checks.

        Returns:
            dict with 'success' (bool), 'constitution', 'state_update', 'summary'
        """
        return self._get_hooks().postflight_all()

    def postflight_summary(self) -> Dict[str, Any]:
        """Return execution summary without running state update."""
        return self._get_hooks().postflight_summary()

    # ------------------------------------------------------------------
    # Convenience: run full phase lifecycle
    # ------------------------------------------------------------------

    def run_phase_lifecycle(
        self,
        fr_results: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        Run a complete phase lifecycle from pre- to post-flight.

        Intended for CLI runners that want a single call interface.

        Args:
            fr_results: list of dicts, each with keys:
                fr_id, dev_result (dict), rev_result (dict)

        Returns:
            dict with 'preflight', 'fr_outcomes', 'postflight'
        """
        preflight_result = self.preflight()
        if not preflight_result.get("all_passed"):
            return {
                "preflight": preflight_result,
                "fr_outcomes": [],
                "postflight": {"success": False, "reason": "preflight_failed"},
            }

        fr_outcomes = []
        # hooks = self._get_hooks()  # Removed per linting
        for entry in fr_results:
            fr_id = entry["fr_id"]
            self.before_dev(fr_id)
            self.after_dev(fr_id, entry.get("dev_result", {}))
            self.before_rev(fr_id)
            self.after_rev(fr_id, entry.get("rev_result", {}))
            fr_outcomes.append({
                "fr_id": fr_id,
                "review_status": entry.get("rev_result", {}).get("review_status"),
            })

        postflight_result = self.postflight()

        return {
            "preflight": preflight_result,
            "fr_outcomes": fr_outcomes,
            "postflight": postflight_result,
        }

    # ------------------------------------------------------------------
    # State helpers
    # ------------------------------------------------------------------

    def get_current_phase(self) -> Optional[int]:
        """Read current phase from state.json."""
        from core.utils.project_layout import ProjectLayout
        state_path = ProjectLayout(self.project_path).state_json_path
        if not state_path.exists():
            return None
        try:
            state = json.loads(state_path.read_text())
            return state.get("current_phase")
        except Exception as exc:
            print(f"[WARN] get_current_phase: could not read {state_path}: {exc}")
            return None

    def get_monitoring_events(self) -> List[Dict[str, Any]]:
        """Return all monitoring events recorded by PhaseHooks."""
        return self._get_hooks().monitoring_events

    def get_fr_results(self) -> List[Dict[str, Any]]:
        """Return all FR results recorded by PhaseHooks."""
        return self._get_hooks().fr_results
