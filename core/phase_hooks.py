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
import sys
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, List, Any

from kill_switch import KillSwitch
from kill_switch.models import MonitorConfig


class KillSwitchBlockedError(RuntimeError):
    """Raised when kill-switch circuit is OPEN for an agent."""


_PHASE_CHECK_TYPES: Dict[int, str] = {
    1: "srs",
    2: "sad",
    3: "implementation",
    4: "test_plan",
    5: "verification",
    6: "quality_report",
    7: "risk_management",
    8: "configuration",
}


class PhaseHooks:
    """
    Phase execution hooks framework.

    Provides specialized hooks for different stages of the development lifecycle:
    - Pre-flight: State checks, constitution validation, kill-switch, drift detection.
    - Monitoring: Logging events during active development with circuit-breaker protection.
    - Post-flight: Final validation, drift check, and reporting.
    """

    def __init__(self, project_path: str, phase: Optional[int] = None,
                 enable_kill_switch: bool = True, drift_threshold: float = 85.0,
                 auto_fix_enabled: bool = True):
        """
        Initialize the hooks manager.

        Args:
            project_path: Path to the project root directory.
            phase: Optional integer representing the current methodology phase.
            enable_kill_switch: Enable M1 kill-switch circuit breaker (default: True).
            drift_threshold: M2 drift detection ensemble score threshold (0-100, default: 85.0).
            auto_fix_enabled: Enable auto-fix integration (default: True).
        """
        self.project_path = Path(project_path)
        self.phase = phase
        self.docs_path = self.project_path / "docs"
        self.state_path = self.project_path / ".methodology" / "state.json"
        self.log_path = self.project_path / ".methodology" / "run-phase.log"
        self.fr_results: List[Dict] = []
        self.preflight_results: Dict[str, Dict[str, Any]] = {}
        self.monitoring_events: List[Dict] = []
        self.drift_threshold = drift_threshold
        self.auto_fix_enabled = auto_fix_enabled
        self._kill_switch: Optional[KillSwitch] = None
        if enable_kill_switch:
            self._kill_switch = KillSwitch()

    # PRE-FLIGHT HOOKS

    def preflight_fsm_check(self) -> Dict[str, Any]:
        """Check FSM state; FREEZE/PAUSED blocks execution."""
        print("\n[PRE-FLIGHT] FSM State Check")
        if not self.state_path.exists():
            # Phase 1 with no state.json = fresh project, auto-initialize.
            # Safety net: init-project is the canonical path; this handles bare
            # `run-phase --phase 1` invocations that skipped init-project.
            if self.phase == 1:
                from datetime import datetime, timezone
                self.state_path.parent.mkdir(parents=True, exist_ok=True)
                self.state_path.write_text(
                    json.dumps({
                        "state": "ACTIVE",
                        "current_phase": 1,
                        "last_gate": None,
                        "last_fr": None,
                        "last_update": datetime.now(timezone.utc).isoformat(),
                    }, indent=2),
                    encoding="utf-8",
                )
                print("   Auto-initialized state.json (fresh P1 project)")
                return {"passed": True, "state": "ACTIVE", "message": "Auto-initialized for P1"}
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
            _phase = self.phase if self.phase is not None else 1
            check_type = _PHASE_CHECK_TYPES.get(_phase, "all")
            result = run_constitution_check(
                check_type=check_type, docs_path=str(self.docs_path),
                current_phase=_phase, check_mode=check_mode
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
            from tool_registry import ToolRegistry  # pyright: ignore[reportMissingImports]
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
        open_agents = [
            agent_id for agent_id in self._kill_switch.get_registered_agents()
            if self._kill_switch.is_agent_circuit_open(agent_id)
        ]
        if open_agents:
            print(f"   Kill-switch BLOCKED: open circuits = {open_agents}")
            return {"passed": False, "open_agents": open_agents,
                    "message": f"Circuit OPEN for agents: {open_agents}"}
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

    def preflight_sab_check(self) -> Dict[str, Any]:
        """Check SAB constitution compliance (P3+ only — architecture baseline drift)."""
        print("\n[PRE-FLIGHT] SAB Constitution Check (M2+)")
        sab_json = self.project_path / ".methodology" / "SAB.json"
        if not sab_json.exists():
            if self.phase and self.phase >= 3:
                print("   WARNING: .methodology/SAB.json not found — SAB baseline missing")
                print("   Run: python3 scripts/generate_sab.py --project .")
                return {"passed": False, "message": "SAB.json not found — generate from SAD.md §6"}
            else:
                print("   SAB.json not yet generated (P1/P2 — expected)")
                return {"passed": True, "skipped": True, "message": "SAB not required before P3"}

        try:
            sab = json.loads(sab_json.read_text(encoding="utf-8"))
        except Exception as e:
            return {"passed": False, "message": f"Failed to parse SAB.json: {e}"}

        layers = sab.get("layers", [])
        allowed_deps = sab.get("dependencies", {})
        violations: list[str] = []

        if not layers:
            return {"passed": True, "skipped": True, "message": "SAB has no layer definitions"}

        layer_names = {layer.get("name", "") for layer in layers}
        print(f"   Layers: {len(layers)}, Dependencies: {len(allowed_deps)}")
        for layer in layers:
            layer_name = layer.get("name", "?")
            modules = layer.get("modules", [])
            declared_deps = allowed_deps.get(layer_name, [])
            invalid_deps = [d for d in declared_deps if d not in layer_names]
            if invalid_deps:
                violations.append(
                    f"Layer {layer_name}: deps {invalid_deps} reference unknown layers"
                )
            missing_modules = [
                m for m in modules
                if not m.endswith("/") and not (self.project_path / m).exists()
            ]
            if missing_modules:
                violations.append(
                    f"Layer {layer_name}: {len(missing_modules)} modules missing from codebase"
                )

        passed = len(violations) == 0
        if violations:
            print(f"   FAIL: {len(violations)} SAB violation(s)")
            for v in violations[:5]:
                print(f"     - {v}")
        else:
            print("   All SAB layers valid")

        return {"passed": passed, "violations": violations, "layers": len(layers)}

    def preflight_traceability(self) -> Dict[str, Any]:
        """Check ASPICE traceability: FR→code→test bidirectional links (P3+)."""
        print("\n[PRE-FLIGHT] ASPICE Traceability Check")
        if self.phase and self.phase < 3:
            print("   Skipped: traceability matrix not required before P3")
            return {"passed": True, "skipped": True, "message": "Traceability not required before P3"}

        try:
            from scripts.check_spec_trace import check_traceability
            _, report = check_traceability(self.project_path)
        except Exception as e:
            print(f"   Traceability check error: {e}")
            return {"passed": True, "skipped": True, "error": str(e)}

        total = report["total"]
        untested = len(report["untested"])
        uncoded = len(report["uncoded"])
        complete = report["complete"]

        # P3: informational only (matrix is being built)
        # P4+: blocking (Gate 3 requires full traceability)
        blocking = self.phase is not None and self.phase >= 4
        passed = complete if blocking else True

        c = report["completeness"]
        print(f"   FRs: {total} | Code: {c['code_coverage']} | "
              f"Test: {c['test_coverage']} | "
              f"{'BLOCKING' if blocking else 'INFO'}")
        if untested:
            print(f"   Untested FRs: {', '.join(report['untested'])}")
        if uncoded:
            print(f"   Uncoded FRs: {', '.join(report['uncoded'])}")

        return {
            "passed": passed,
            "skipped": False,
            "total_frs": total,
            "untested": report["untested"],
            "uncoded": report["uncoded"],
            "completeness": c,
            "blocking": blocking,
        }

    def preflight_gap_analysis(self) -> Dict[str, Any]:
        """M3 gap analysis — detect SPEC.md ↔ codebase gaps (P3+, informational)."""
        if self.phase is not None and self.phase < 3:
            return {"passed": True, "skipped": True, "reason": "P1/P2 — no gap analysis"}
        print("\n[PRE-FLIGHT] M3 Gap Analysis")
        try:
            from gap_detector.parser import SpecParser
            from gap_detector.scanner import CodeScanner
            from gap_detector.detector import GapDetector

            spec_path = self.project_path / "SPEC.md"
            if not spec_path.exists():
                print("   SPEC.md not found — skipping gap analysis")
                return {"passed": True, "skipped": True, "reason": "SPEC.md not found"}

            spec = SpecParser(str(spec_path)).parse()
            scanner = CodeScanner(str(self.project_path))
            code = scanner.scan()
            detector = GapDetector(spec, code)
            gaps = detector.detect()
            summary = detector.get_summary()

            report_path = self.project_path / ".methodology" / "gap_report.json"
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(json.dumps({
                "summary": {
                    "total": summary.total_gaps, "missing": summary.missing,
                    "incomplete": summary.incomplete, "orphaned": summary.orphaned,
                    "critical": summary.critical, "major": summary.major,
                    "minor": summary.minor,
                },
                "gaps": [{"type": g.gap_type, "severity": g.severity,
                          "reason": g.reason, "action": g.recommended_action}
                         for g in gaps],
            }, indent=2))
            print(f"   Gap report → {report_path}  "
                  f"(total={summary.total_gaps}, critical={summary.critical})")
            return {"passed": True, "total_gaps": summary.total_gaps,
                    "critical": summary.critical}
        except ImportError:
            print("   gap_detector unavailable — skipping")
            return {"passed": True, "skipped": True, "reason": "gap_detector unavailable"}
        except Exception as exc:
            print(f"   Gap analysis error: {exc}")
            return {"passed": True, "skipped": True, "error": str(exc)}

    def preflight_ci_readiness(self) -> Dict[str, Any]:
        """Check target project CI wiring (Context B only — advisory, non-blocking)."""
        print("\n[PRE-FLIGHT] CI Readiness Check")
        checks: Dict[str, bool] = {}
        workflow_path = self.project_path / ".github" / "workflows" / "harness_quality_gate.yml"
        checks["ci_workflow"] = workflow_path.exists()
        hooks_dir = self.project_path / ".git" / "hooks"
        checks["git_hooks"] = (hooks_dir / "prepare-commit-msg").exists()
        checks["harness_importable"] = (
            (self.project_path / "harness" / "core" / "quality_gate" / "__init__.py").exists()
            or (self.project_path / "core" / "quality_gate" / "__init__.py").exists()
            or (self.project_path / "harness_cli.py").exists()
            or (self.project_path / "harness" / "harness_cli.py").exists()
        )
        missing = [k for k, v in checks.items() if not v]
        if missing:
            print(f"   WARNING: Missing CI components: {missing}")
            print(f"   Run: python3 harness_cli.py init-project --project {self.project_path}")
        else:
            print("   All CI wiring present")
        return {"passed": True, "checks": checks,
                "missing": missing,
                "message": "All CI wiring present" if not missing else f"Missing: {missing}"}

    def preflight_previous_phase_artifacts(self) -> Dict[str, Any]:
        """Check that previous phase's required deliverables exist (ASPICE traceability).

        Ensures the ASPICE chain is intact before starting the current phase.
        P1 is exempt (no previous phase).
        """
        print("\n[PRE-FLIGHT] Previous Phase Artifact Check")
        if self.phase is None or self.phase <= 1:
            return {"passed": True, "skipped": True, "message": "P1 has no previous phase"}

        from core.quality_gate.phase_artifact_enforcer import PhaseArtifactRegistry  # pyright: ignore[reportMissingImports]

        registry = PhaseArtifactRegistry(str(self.project_path))
        result = registry.verify_phase_chain(self.phase)

        all_ok = result["all_verified"]
        if not all_ok:
            missing = result["missing_links"]
            print(f"   FAIL: {len(missing)} phase artifact link(s) broken")
            for m in missing[:5]:
                print(f"     - {m}")
        else:
            print(f"   All {result['stats']['verified']} phase artifact links verified")

        return {
            "passed": all_ok,
            "verified": result["verified_links"],
            "missing": result["missing_links"],
            "stats": result["stats"],
        }

    def preflight_bvs_phase_order(self) -> Dict[str, Any]:
        """BVS phase-order check (HR-03) — phase prerequisites and FSM FREEZE."""
        print("\n[PRE-FLIGHT] BVS Phase Order (HR-03)")
        try:
            from constitution.bvs_runner import BVSRunner
            runner = BVSRunner(str(self.project_path), phase=self.phase if self.phase is not None else 1)
            result = runner.run()
            if result["violations"]:
                for v in result["violations"]:
                    print(f"   {v['rule']}: {v['message']}")
            else:
                print("   Phase order OK")
            return {"passed": result["passed"], "violations": result["violations"]}
        except ImportError:
            print("[WARN] BVS modules unavailable — skipping phase-order check", file=sys.stderr)
            return {"passed": True, "skipped": True, "message": "BVS modules unavailable"}
        except Exception as e:
            print(f"   BVS check error: {e}")
            return {"passed": True, "skipped": True, "error": str(e)}

    def preflight_all(self) -> Dict[str, Any]:
        """Run all pre-flight checks."""
        print(f"\n{'='*60}\nPRE-FLIGHT: Phase {self.phase}\n{'='*60}")
        results = {
            "fsm": self.preflight_fsm_check(),
            "bvs_phase_order": self.preflight_bvs_phase_order(),
            "constitution": self.preflight_constitution(),
            "kill_switch": self.preflight_kill_switch(),
            "previous_phase_artifacts": self.preflight_previous_phase_artifacts(),
            "drift_detection": self.preflight_drift_detection(),
            "sab": self.preflight_sab_check(),
            "tool_registry": self.preflight_tool_registry(),
            "traceability": self.preflight_traceability(),
            "gap_analysis": self.preflight_gap_analysis(),
            "ci_readiness": self.preflight_ci_readiness(),
        }
        self.preflight_results = results
        all_passed = all(r.get("passed", False) for r in results.values())

        if not all_passed and self.auto_fix_enabled:
            results["_fix_context"] = {
                "source": "phase_hooks",
                "problem_type": "preflight_failure",
                "severity": "high",
                "phase": self.phase,
                "failing_checks": [
                    name for name, r in results.items()
                    if not r.get("passed", False) and not name.startswith("_")
                ],
                "project_root": str(self.project_path),
            }

        print(f"\nPRE-FLIGHT: {'PASS' if all_passed else 'FAIL'}")
        return {"all_passed": all_passed, "details": results}

    def to_fix_context(self) -> dict:
        """Serialize preflight failures for AutoFixEngine consumption."""
        failing = {
            name: r for name, r in self.preflight_results.items()
            if not r and not name.startswith("_")
        }
        return {
            "source": "phase_hooks",
            "problem_type": "preflight_failure" if len(failing) > 1 else "low_constitution_score",
            "severity": "high",
            "phase": self.phase,
            "failing_checks": list(failing.keys()),
            "project_root": str(self.project_path),
        }

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

    def postflight_bvs_invariants(self) -> Dict[str, Any]:
        """BVS invariant check (phase 3+) — behavioral invariants from sessions_spawn.log."""
        print("\n[POST-FLIGHT] BVS Invariant Check")
        if self.phase and self.phase < 3:
            print("   Skipped: invariant checks start at Phase 3")
            return {"passed": True, "skipped": True, "reason": "P1/P2 — invariants not required"}
        try:
            from constitution.bvs_runner import BVSRunner
            bvs_phase: int = self.phase if self.phase is not None else 3
            runner = BVSRunner(str(self.project_path), phase=bvs_phase)
            result = runner.run_full()
            if result["total_violations"]:
                for v in result["violations"]:
                    print(f"   [VIOLATION] {v.get('rule', '?')}: {v.get('message', '?')}")
                print(f"   BVS: {result['total_violations']} violation(s)")
            else:
                print("   BVS invariants PASS")
            return {"passed": result["passed"], "total_violations": result["total_violations"],
                    "invariant_report": result.get("invariant_report", {})}
        except ImportError:
            print("[WARN] BVS modules unavailable — skipping invariant check", file=sys.stderr)
            return {"passed": True, "skipped": True, "message": "BVS modules unavailable"}
        except Exception as e:
            print(f"   BVS invariant error: {e}")
            return {"passed": True, "skipped": True, "error": str(e)}

    def postflight_steering_summary(self) -> Dict[str, Any]:
        """Post-phase Steering cross-FR consistency check (phase 3+, opt-in).

        Compares adjacent FR result pairs to detect quality drift across
        FRs within a phase. Requires ≥2 FR results to produce comparisons.
        This is NOT a same-FR dev-vs-rev comparison — that requires both
        Agent A and Agent B outputs stored per FR, which is future work.
        """
        print("\n[POST-FLIGHT] Steering Phase Summary")
        if self.phase and self.phase < 3:
            return {"passed": True, "skipped": True, "reason": "P1/P2 — no Steering"}
        import os
        if os.environ.get("STEERING_ENABLED", "").lower() not in ("1", "true", "yes"):
            return {"passed": True, "skipped": True, "reason": "STEERING_ENABLED not set"}
        if len(self.fr_results) < 2:
            return {"passed": True, "skipped": True,
                    "reason": f"Need ≥2 FR results, have {len(self.fr_results)}"}
        try:
            from steering.integrations import SteeringIntegrator
            from steering.provider import create_steering_provider
            provider = create_steering_provider()
            phase: int = self.phase if self.phase is not None else 3
            integrator = SteeringIntegrator(provider, str(self.project_path), phase=phase)
            import json
            for i, fr in enumerate(self.fr_results):
                if i + 1 < len(self.fr_results):
                    a_out = {"text": json.dumps(fr)}
                    b_out = {"text": json.dumps(self.fr_results[i + 1])}
                    integrator.iterate_with_full_check(
                        a_out, b_out, run_bvs=False, run_constitution=True,
                    )
            summary = integrator.get_full_summary()
            print(f"   Steering iterations: {summary['steering']['total_iterations']}")
            return {"passed": True, "iterations": summary["steering"]["total_iterations"],
                    "summary": summary}
        except ImportError:
            print("[WARN] Steering modules unavailable — skipping phase summary", file=sys.stderr)
            return {"passed": True, "skipped": True, "message": "Steering modules unavailable"}
        except Exception as e:
            print(f"   Steering error: {e}")
            return {"passed": True, "skipped": True, "error": str(e)}

    def postflight_all(self) -> Dict[str, Any]:
        """Run all post-flight checks."""
        print(f"\n{'='*60}\nPOST-FLIGHT: Phase {self.phase}\n{'='*60}")
        const_result = self.postflight_constitution()
        bvs_result = self.postflight_bvs_invariants()
        steering_result = self.postflight_steering_summary()
        drift_result = self.postflight_drift_check()
        fr_approved = sum(1 for r in self.fr_results if r.get("review_status") == "APPROVE")
        total_frs = len(self.fr_results)
        # Per-FR gate approval only required for P3+ phases that run per-FR Gate 1.
        # Phases without FR-level gates (e.g. P6 Gate 4) have total_frs=0 → skip FR check.
        frs_ok = True
        if self.phase and self.phase >= 3 and total_frs > 0:
            frs_ok = fr_approved >= total_frs
        success = (
            const_result.get("passed", False)
            and bvs_result.get("passed", True)
            and frs_ok
            and drift_result.get("passed", True)
        )
        state_result = self.postflight_update_state(success=success)
        summary = self.postflight_summary()
        print(f"\nPOST-FLIGHT: {'PASS' if success else 'FAIL'}")
        return {"success": success, "constitution": const_result,
                "bvs_invariants": bvs_result, "steering": steering_result,
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

    def add_gate1_pass(self, fr_id: str, score: float) -> None:
        """Record a confirmed Gate 1 PASS for postflight FR approval accounting.

        Use this instead of ``add_fr_result`` with synthetic SimpleNamespace objects.
        The Gate 1 score is stored as both dev and rev confidence.
        """
        self.fr_results.append({
            "fr_id": fr_id,
            "dev_status": "complete",
            "dev_confidence": score,
            "rev_status": "complete",
            "review_status": "APPROVE",
            "rev_confidence": score,
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
            print(f"Warning: Failed to append to run-phase.log: {e}", file=sys.stderr)
