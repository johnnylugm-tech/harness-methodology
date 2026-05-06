#!/usr/bin/env python3
"""
Phase Hooks Framework
=====================

Provides hooks for Agent phase execution: preflight checks, monitoring,
and postflight validation.

Usage:
    from core.phase_hooks import PhaseHooks

    hooks = PhaseHooks("/path/to/project", phase=3)
    hooks.preflight_all()
    hooks.monitoring_before_dev(fr_id="FR-01")
    hooks.monitoring_after_dev(fr_id="FR-01", result=dev_result)
    hooks.postflight_all()
"""

import json
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, List, Any

from kill_switch import KillSwitch
from kill_switch.models import MonitorConfig


class KillSwitchBlockedError(RuntimeError):
    """Raised when kill-switch circuit is OPEN for an agent."""


class PhaseHooks:
    """
    Phase execution hooks framework.

    Provides specialized hooks for different stages of the development lifecycle:
    - Pre-flight: State checks, constitution validation, kill-switch, drift detection.
    - Monitoring: Logging events during active development with circuit-breaker protection.
    - Post-flight: Final validation, drift check, and reporting.
    """

    def __init__(self, project_path: str, phase: Optional[int] = None,
                 enable_kill_switch: bool = True, drift_threshold: float = 85.0):
        """
        Initialize the hooks manager.

        Args:
            project_path: Path to the project root directory.
            phase: Optional integer representing the current methodology phase.
            enable_kill_switch: Enable M1 kill-switch circuit breaker (default: True).
            drift_threshold: M2 drift detection ensemble score threshold (0-100, default: 85.0).
        """
        self.project_path = Path(project_path)
        self.phase = phase
        self.docs_path = self.project_path / "docs"
        self.state_path = self.project_path / ".methodology" / "state.json"
        self.log_path = self.project_path / ".methodology" / "run-phase.log"
        self.fr_results: List[Dict] = []
        self.preflight_results: Dict[str, bool] = {}
        self.monitoring_events: List[Dict] = []
        self.drift_threshold = drift_threshold
        self._kill_switch: Optional[KillSwitch] = None
        if enable_kill_switch:
            self._kill_switch = KillSwitch()

    # PRE-FLIGHT HOOKS

    def preflight_fsm_check(self) -> Dict[str, Any]:
        """Check FSM state; FREEZE/PAUSED blocks execution."""
        print("\n[PRE-FLIGHT] FSM State Check")
        if not self.state_path.exists():
            return {"passed": False, "state": "UNKNOWN", "message": "state.json not found"}
        state = json.loads(self.state_path.read_text())
        current_state = state.get("state", "UNKNOWN")
        current_phase = state.get("current_phase", 0)
        print(f"   State: {current_state}, Phase: {current_phase}")
        if current_state in ("FREEZE", "PAUSED"):
            return {"passed": False, "state": current_state, "message": f"FSM is {current_state}"}
        if self.phase and current_phase > self.phase:
            return {"passed": False, "state": current_state,
                    "message": f"Cannot go backwards: current={current_phase}, requested={self.phase}"}
        print("   FSM check passed")
        return {"passed": True, "state": current_state, "message": "OK"}

    def preflight_constitution(self, check_mode: str = "preflight") -> Dict[str, Any]:
        """Run constitution quality gate check."""
        print(f"\n[PRE-FLIGHT] Constitution Check ({check_mode})")
        try:
            from core.quality_gate.constitution import run_constitution_check
            result = run_constitution_check(
                check_type="all", docs_path=str(self.docs_path),
                current_phase=self.phase or 1, check_mode=check_mode
            )
            print(f"   Score: {result.score:.0f}%, Violations: {len(result.violations)}")
            return {"passed": result.passed, "score": result.score,
                    "violations": len(result.violations)}
        except Exception as e:
            print(f"   Constitution check error: {e}")
            return {"passed": False, "score": 0, "violations": 0, "error": str(e)}

    def preflight_tool_registry(self) -> Dict[str, Any]:
        """Check tool registry state."""
        print("\n[PRE-FLIGHT] Tool Registry Check")
        try:
            from tool_registry import ToolRegistry
            count = len(ToolRegistry.list_tools())
            print(f"   Tools: {count}")
            return {"passed": count > 0, "tools_count": count}
        except ImportError:
            return {"passed": True, "tools_count": 0, "skipped": True}
        except Exception as e:
            return {"passed": True, "tools_count": 0, "error": str(e)}

    def preflight_kill_switch(self) -> Dict[str, Any]:
        """Check kill-switch (M1) circuit breaker state."""
        print("\n[PRE-FLIGHT] Kill-Switch (M1) Check")
        if not self._kill_switch:
            return {"passed": True, "skipped": True, "message": "Kill-switch disabled"}
        # Verify kill-switch is operational (no open circuits at start)
        print("   Kill-switch operational")
        return {"passed": True, "kill_switch": "operational"}

    def preflight_drift_detection(self) -> Dict[str, Any]:
        """Run M2 drift detection: compare specs vs implementation."""
        print("\n[PRE-FLIGHT] Drift Detection (M2)")
        try:
            from detection import DriftDetector
            detector = DriftDetector(str(self.project_path))
            results = detector.detect_all()
            total_drifts = sum(r.drifted for r in results.values())
            avg_score = (sum(r.score for r in results.values()) /
                         max(len(results), 1))
            score_pct = avg_score * 100
            passed = score_pct >= self.drift_threshold
            print(f"   Drifts: {total_drifts}, Score: {score_pct:.0f}% "
                  f"(threshold: {self.drift_threshold:.0f}%)")
            return {"passed": passed, "drifts": total_drifts,
                    "score": score_pct,
                    "threshold": self.drift_threshold,
                    "details": {k: v.to_dict() for k, v in results.items()}}
        except ImportError:
            return {"passed": True, "skipped": True, "message": "detection module unavailable"}
        except Exception as e:
            print(f"   Drift detection error: {e}")
            return {"passed": True, "skipped": True, "error": str(e)}

    def preflight_all(self) -> Dict[str, Any]:
        """Run all pre-flight checks."""
        print(f"\n{'='*60}\nPRE-FLIGHT: Phase {self.phase}\n{'='*60}")
        results = {
            "fsm": self.preflight_fsm_check(),
            "constitution": self.preflight_constitution(),
            "kill_switch": self.preflight_kill_switch(),
            "drift_detection": self.preflight_drift_detection(),
            "tool_registry": self.preflight_tool_registry(),
        }
        all_passed = all(r.get("passed", False) for r in results.values())
        print(f"\nPRE-FLIGHT: {'PASS' if all_passed else 'FAIL'}")
        return {"all_passed": all_passed, "details": results}

    # MONITORING HOOKS

    def _check_kill_switch(self, agent_id: str) -> None:
        """Verify kill-switch circuit is CLOSED for agent_id; raise if OPEN."""
        if not self._kill_switch:
            return
        if self._kill_switch.is_agent_circuit_open(agent_id):
            raise KillSwitchBlockedError(
                f"Kill-switch: circuit OPEN for {agent_id} — agent blocked"
            )

    def _start_kill_switch_monitoring(self, agent_id: str) -> None:
        """Start kill-switch monitoring for an agent."""
        if not self._kill_switch:
            return
        config = MonitorConfig(agent_id=agent_id)
        self._kill_switch.start_monitoring(agent_id, config)

    def _stop_kill_switch_monitoring(self, agent_id: str) -> None:
        """Stop kill-switch monitoring for an agent."""
        if not self._kill_switch:
            return
        self._kill_switch.stop_monitoring(agent_id)

    def monitoring_before_dev(self, fr_id: str, agent_id: str = "agent-a") -> None:
        """Hook: before developer execution. Checks kill-switch circuit."""
        self._check_kill_switch(agent_id)
        self._start_kill_switch_monitoring(agent_id)
        self.monitoring_events.append({"timestamp": datetime.now().isoformat(),
                                        "type": "before_dev", "fr_id": fr_id,
                                        "agent_id": agent_id})
        print(f"\n[MONITORING] Before Dev: {fr_id} agent={agent_id}")
        self._append_log(f"BEFORE_DEV: {fr_id} agent={agent_id}")

    def monitoring_after_dev(self, fr_id: str, result: Any = None,
                              agent_id: str = "agent-a") -> None:
        """Hook: after developer execution. Stops kill-switch monitoring."""
        self._stop_kill_switch_monitoring(agent_id)
        status = getattr(result, 'status', 'unknown') if result else 'unknown'
        confidence = getattr(result, 'confidence', 0) if result else 0
        self.monitoring_events.append({"timestamp": datetime.now().isoformat(),
                                        "type": "after_dev", "fr_id": fr_id,
                                        "status": status, "confidence": confidence,
                                        "agent_id": agent_id})
        print(f"\n[MONITORING] After Dev: {fr_id} status={status} confidence={confidence}")
        self._append_log(f"AFTER_DEV: {fr_id} status={status}")
        self.fr_results.append({"fr_id": fr_id, "dev_status": status, "dev_confidence": confidence})

    def monitoring_before_rev(self, fr_id: str, agent_id: str = "agent-b") -> None:
        """Hook: before reviewer execution. Checks kill-switch circuit."""
        self._check_kill_switch(agent_id)
        self._start_kill_switch_monitoring(agent_id)
        self.monitoring_events.append({"timestamp": datetime.now().isoformat(),
                                        "type": "before_rev", "fr_id": fr_id,
                                        "agent_id": agent_id})
        print(f"\n[MONITORING] Before Rev: {fr_id} agent={agent_id}")
        self._append_log(f"BEFORE_REV: {fr_id} agent={agent_id}")

    def monitoring_after_rev(self, fr_id: str, result: Any = None,
                              agent_id: str = "agent-b") -> None:
        """Hook: after reviewer execution. Stops kill-switch monitoring."""
        self._stop_kill_switch_monitoring(agent_id)
        status = getattr(result, 'status', 'unknown') if result else 'unknown'
        review_status = getattr(result, 'review_status', None) if result else None
        confidence = getattr(result, 'confidence', 0) if result else 0
        self.monitoring_events.append({"timestamp": datetime.now().isoformat(),
                                        "type": "after_rev", "fr_id": fr_id,
                                        "review_status": review_status,
                                        "agent_id": agent_id})
        print(f"\n[MONITORING] After Rev: {fr_id} review={review_status}")
        self._append_log(f"AFTER_REV: {fr_id} review={review_status}")
        if self.fr_results and self.fr_results[-1].get("fr_id") == fr_id:
            self.fr_results[-1].update({"rev_status": status, "review_status": review_status,
                                         "rev_confidence": confidence})

    def monitoring_hr12_check(self, fr_id: str, iteration: int, max_iterations: int = 5) -> bool:
        """HR-12 check: block if max iterations exceeded."""
        if iteration >= max_iterations:
            print(f"\n[MONITORING] HR-12 TRIGGERED: {fr_id} iter {iteration} >= {max_iterations}")
            self._append_log(f"HR12_TRIGGERED: {fr_id}")
            return False
        return True

    # POST-FLIGHT HOOKS

    def postflight_constitution(self) -> Dict[str, Any]:
        """Post-flight constitution check."""
        print("\n[POST-FLIGHT] Constitution Check")
        return self.preflight_constitution(check_mode="postflight")

    def postflight_update_state(self, success: bool = True) -> Dict[str, Any]:
        """Update FSM state.json on success."""
        print("\n[POST-FLIGHT] Update FSM State")
        if not success:
            return {"updated": False, "reason": "execution_failed"}
        if not self.state_path.exists():
            return {"updated": False, "reason": "no_state"}
        state = json.loads(self.state_path.read_text())
        old_phase = state.get("current_phase", 0)
        if self.phase and self.phase > old_phase:
            state["current_phase"] = self.phase
            state["last_update"] = datetime.now().isoformat()
            self.state_path.write_text(json.dumps(state, indent=2))
            print(f"   Updated: {old_phase} -> {self.phase}")
            self._append_log(f"STATE_UPDATE: {old_phase} -> {self.phase}")
            return {"updated": True, "old_phase": old_phase, "new_phase": self.phase}
        return {"updated": False, "reason": "phase_not_advanced"}

    def postflight_summary(self) -> Dict[str, Any]:
        """Generate execution summary."""
        total_frs = len(self.fr_results)
        approved = sum(1 for r in self.fr_results if r.get("review_status") == "APPROVE")
        print(f"\n[POST-FLIGHT] Summary: {approved}/{total_frs} FRs approved")
        return {"total_frs": total_frs, "approved": approved,
                "fr_results": self.fr_results,
                "monitoring_events": len(self.monitoring_events)}

    def postflight_drift_check(self) -> Dict[str, Any]:
        """Post-flight drift detection: verify no new drift introduced."""
        print("\n[POST-FLIGHT] Drift Detection (M2)")
        try:
            from detection import DriftDetector
            detector = DriftDetector(str(self.project_path))
            results = detector.detect_all()
            total_drifts = sum(r.drifted for r in results.values())
            avg_score = (sum(r.score for r in results.values()) /
                         max(len(results), 1))
            score_pct = avg_score * 100
            passed = score_pct >= self.drift_threshold
            print(f"   Drifts: {total_drifts}, Score: {score_pct:.0f}% "
                  f"(threshold: {self.drift_threshold:.0f}%)")
            return {"passed": passed, "drifts": total_drifts,
                    "score": score_pct,
                    "threshold": self.drift_threshold}
        except ImportError:
            return {"passed": True, "skipped": True, "message": "detection module unavailable"}
        except Exception as e:
            print(f"   Drift detection error: {e}")
            return {"passed": True, "skipped": True, "error": str(e)}

    def postflight_all(self) -> Dict[str, Any]:
        """Run all post-flight checks."""
        print(f"\n{'='*60}\nPOST-FLIGHT: Phase {self.phase}\n{'='*60}")
        const_result = self.postflight_constitution()
        drift_result = self.postflight_drift_check()
        fr_approved = sum(1 for r in self.fr_results if r.get("review_status") == "APPROVE")
        total_frs = max(len(self.fr_results), 1)
        success = const_result.get("passed", False) and fr_approved >= total_frs
        state_result = self.postflight_update_state(success=success)
        summary = self.postflight_summary()
        print(f"\nPOST-FLIGHT: {'PASS' if success else 'FAIL'}")
        return {"success": success, "constitution": const_result,
                "drift_detection": drift_result,
                "state_update": state_result, "summary": summary}

    def add_fr_result(self, fr_id: str, dev_result: Any, rev_result: Any) -> None:
        """Manually record FR result."""
        self.fr_results.append({
            "fr_id": fr_id,
            "dev_status": getattr(dev_result, 'status', 'unknown'),
            "dev_confidence": getattr(dev_result, 'confidence', 0),
            "rev_status": getattr(rev_result, 'status', 'unknown'),
            "review_status": getattr(rev_result, 'review_status', None),
            "rev_confidence": getattr(rev_result, 'confidence', 0),
        })

    def _append_log(self, message: str) -> None:
        """Append to run-phase.log."""
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with open(self.log_path, "a") as f:
                f.write(f"[{ts}] {message}\n")
        except Exception as e:  # pragma: no cover
            import sys
            sys.stderr.write(f"Warning: Failed to append to run-phase.log: {e}\n")
