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
import re
import sys
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, List, Any

from kill_switch import KillSwitch
from kill_switch.models import MonitorConfig
from core.atomic_io import atomic_write_json  # Bug #104 fix


class KillSwitchBlockedError(RuntimeError):
    """Raised when kill-switch circuit is OPEN for an agent."""


# Phase → check_type mapping is owned by the constitution package
# (core.quality_gate.constitution.PHASE_CHECK_TYPES). Import lazily inside
# preflight_constitution() to avoid a heavy import at module load time.


def _dispatch_trace_auto_fix(project_path, untested, uncoded, phase=None) -> bool:
    """PR 9: dispatch one bounded auto-fix attempt for missing traceability.

    Returns True if the fix closed the gap (caller should re-verify);
    False if the fix failed or escalated (caller should keep the gate
    blocked). Never raises — auto-fix failures are reported via stdout.

    The per-strategy allowlist lives inside `AutoFixEngine`:
    `fix_missing_traceability` (problem_type='missing_traceability') is
    the only strategy that actually re-derives and re-verifies. Other
    strategies still emit stubs and are not invoked by this path.

    F-2.6 fix: `phase` defaults to None (caller passes the real phase
    when available). Currently `fix_missing_traceability` doesn't
    consume the phase, but new strategies may.
    """
    try:
        from core.auto_fix import AutoFixEngine, FixContext
    except ImportError as e:
        print(f"   [auto-fix] engine import failed: {e}", file=sys.stderr)
        return False

    actual_phase = phase if phase is not None else 1
    try:
        engine = AutoFixEngine(
            project_root=project_path,
            phase=actual_phase,
            max_rounds=1,
        )
        ctx = FixContext(
            source="phase_hooks/preflight_traceability",
            problem_type="missing_traceability",
            severity="high",
            phase=actual_phase,
            project_root=project_path,
            details={
                "max_rounds": 1,
                "untested": list(untested),
                "uncoded": list(uncoded),
            },
        )
        result = engine.fix(ctx)
        # CRITICAL FIX (audit F-4.1): engine.fix returns FixResult (a
        # dataclass). `bool(FixResult(success=False, ...))` is True
        # because dataclasses are truthy by default. The previous
        # `isinstance(result, tuple)` branch was dead for FixResult
        # returns and unblocked the gate even on failure/escalation.
        # Use the structured `success` field instead.
        if isinstance(result, tuple):  # pragma: no cover (legacy path)
            ok = bool(result and result[0])
            msg = result[1] if len(result) > 1 else ""
        else:
            ok = bool(getattr(result, "success", False))
            msg = (getattr(result, "action_taken", "")
                   or getattr(result, "error", ""))
        print(f"   [auto-fix] round 1: {msg or ('passed' if ok else 'failed')}")
        return ok
    except Exception as e:
        print(f"   [auto-fix] engine.fix raised: {e}", file=sys.stderr)
        return False


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
        from core.utils.project_layout import ProjectLayout
        self._layout = ProjectLayout(self.project_path)
        self.phase = phase
        self.docs_path = self.project_path / "docs"
        self.state_path = self._layout.state_json_path
        self.log_path = self._layout.methodology_dir / "run-phase.log"
        self.fr_results: List[Dict] = []
        self.preflight_results: Dict[str, Dict[str, Any]] = {}
        self.monitoring_events: List[Dict] = []
        self.drift_threshold = drift_threshold
        self._kill_switch: Optional[KillSwitch] = None
        self._pkg_dir_cache: Optional[str] = None  # Bug #119: cached setup.cfg package_dir

        if enable_kill_switch:
            self._kill_switch = KillSwitch()

        self.tracer: Any = None
        try:
            from core.observability import init_tracer
            self.tracer = init_tracer(self.project_path)
        except ImportError:
            pass

    def _read_pkg_dir_for_sab(self) -> Optional[str]:
        """Return the package source dir (e.g. 'src') for src/-layout projects.

        Bug #119: SAB `modules` may be in dotted form ("taskq.cli") which needs
        to be expanded against the actual filesystem layout. Reads setup.cfg
        via the shared helper in detection.drift_detector so this matches
        what DriftDetector.detect_sab_drift uses.
        """
        if self._pkg_dir_cache is None:
            from detection.drift_detector import read_package_dir
            self._pkg_dir_cache = read_package_dir(Path(self.project_path))
        return self._pkg_dir_cache

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
                # Bug #104 fix: atomic_write_json (tempfile + os.replace) so
                # a mid-write crash (Ctrl-C, OOM kill, disk-full) cannot
                # leave state.json truncated. Without this, a crash during
                # the bare `run-phase --phase 1` auto-init path would
                # leave an empty state.json that every subsequent
                # preflight then fails on (entire pipeline blocked).
                atomic_write_json(self.state_path, {
                    "state": "RUNNING",
                    "current_phase": 1,
                    "last_gate": None,
                    "last_fr": None,
                    "last_update": datetime.now(timezone.utc).isoformat(),
                })
                print("   Auto-initialized state.json (fresh P1 project)")
                return {"passed": True, "state": "RUNNING", "message": "Auto-initialized for P1"}
            return {"passed": False, "state": "UNKNOWN", "message": "state.json not found"}
        try:
            state = json.loads(self.state_path.read_text())
        except (json.JSONDecodeError, OSError) as e:
            return {"passed": False, "state": "CORRUPT",
                    "message": f"state.json is corrupt: {e}"}
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
            from core.quality_gate.constitution import (
                run_constitution_check, PHASE_CHECK_TYPES,
            )
            _phase = self.phase if self.phase is not None else 1
            check_type = PHASE_CHECK_TYPES.get(_phase, "all")
            result = run_constitution_check(
                check_type=check_type, docs_path=str(self.docs_path),
                current_phase=_phase, check_mode=check_mode
            )
            print(f"   Score: {result.score:.0f}%, Violations: {len(result.violations)}")
            if not result.passed:
                # Gap-6 fix: surface a concrete re-dispatch command so the executing
                # agent triggers an A/B fix loop instead of manually patching keywords.
                print(
                    f"\n   [CONSTITUTION FAIL] Score {result.score:.0f}% below threshold.\n"
                    "   Do NOT manually edit keywords. Re-dispatch Agent A to fix the document:\n"
                    f"     python harness_cli.py dispatch --role developer "
                    f"--phase {_phase} --project . \\\n"
                    f'       --prompt "Constitution check failed (score {result.score:.0f}%): '
                    "improve document quality to meet keyword coverage thresholds. "
                    "Refer to the failing dimensions in the check output and enrich the "
                    'document sections accordingly."'
                )
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
            # P3: informational only — no implementation exists yet at entry.
            # P4+: blocking — drift must be within threshold.
            # `phase is None` is treated as P4+ so a broken detector can
            # never silently pass (caller violated the constructor contract).
            blocking = self.phase is None or self.phase >= 4
            passed = score_pct >= self.drift_threshold if blocking else True
            print(f"   Drifts: {total_drifts}, Score: {score_pct:.0f}% "
                  f"(threshold: {self.drift_threshold:.0f}%)"
                  f"{' [INFO]' if not blocking else ''}")
            return {"passed": passed, "drifts": total_drifts,
                    "score": score_pct,
                    "threshold": self.drift_threshold,
                    "details": {k: v.to_dict() for k, v in results.items()}}
        except ImportError:
            return {"passed": True, "skipped": True, "message": "detection module unavailable"}
        except Exception as e:
            print(f"   Drift detection error: {e}")
            # P4+ is blocking — module errors must NOT silently pass
            blocking = self.phase is None or self.phase >= 4
            return {"passed": not blocking, "skipped": True,
                    "error": str(e), "blocking": blocking}

    def preflight_sab_check(self) -> Dict[str, Any]:
        """Check SAB constitution compliance (P3+ only — architecture baseline drift)."""
        print("\n[PRE-FLIGHT] SAB Constitution Check (M2+)")
        sab_json = self._layout.methodology_dir / "SAB.json"
        if not sab_json.exists():
            if self.phase and self.phase >= 3:
                print("   WARNING: .methodology/SAB.json not found — SAB baseline missing")
                print("   Run: python3 scripts/generate_sab.py --project . [--overwrite]")
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
            # P3 or phase-agnostic: skip module-existence check (implementation
            # dirs not created yet). Structural violations (invalid deps) still fail.
            # P4+: enforce that all SAB-layer modules exist on disk.
            # Modules listed as FR IDs (FR-XX) are not file paths — skip file check.
            # Bug #119: also expand dotted notation ("taskq.cli") into path
            # candidates so this check agrees with DriftDetector.detect_sab_drift.
            if self.phase is not None and self.phase >= 4:
                from detection.drift_detector import sab_module_to_path_variants
                # Try to read pkg_dir the same way drift_detector does so that
                # SAB "taskq.cli" matches src/taskq/cli.py in src/-layout projects.
                pkg_dir = self._read_pkg_dir_for_sab()
                missing_modules = []
                for m in modules:
                    actual_m = m.get("implemented_in", m.get("name", "")) if isinstance(m, dict) else m
                    if not isinstance(actual_m, str):
                        continue
                    if actual_m.endswith("/") or re.match(r'^FR-\d+$', actual_m):
                        continue
                    if any(
                        (self.project_path / cand).exists()
                        or (self._layout.active_src_dir / cand).exists()
                        for cand in sab_module_to_path_variants(actual_m, pkg_dir)
                    ):
                        continue
                    missing_modules.append(m.get("name", str(m)) if isinstance(m, dict) else m)
                if missing_modules:
                    violations.append(
                        f"Layer {layer_name}: {len(missing_modules)} modules missing from codebase"
                    )

        # P3 or phase-agnostic: module-existence skipped (impl dirs not created
        # yet); structural violations (invalid deps) still fail.
        # P4+: module existence enforced; all violations are blocking.
        passed = len(violations) == 0
        if violations:
            print(f"   FAIL: {len(violations)} SAB violation(s)")
            for v in violations[:5]:
                print(f"     - {v}")
        else:
            print("   All SAB layers valid")

        return {"passed": passed, "violations": violations, "layers": len(layers)}

    def preflight_traceability(self) -> Dict[str, Any]:
        """Check ASPICE traceability: FR→code→test bidirectional links (P3+).

        PR 3: also verifies the git-anchored attestation exists at P3+
        (informational at P3/P4, blocking at P5+). Mismatched / missing
        attestations must NOT silently pass at P5+.
        """
        print("\n[PRE-FLIGHT] ASPICE Traceability Check")
        if self.phase and self.phase < 3:
            print("   Skipped: traceability matrix not required before P3")
            return {"passed": True, "skipped": True, "message": "Traceability not required before P3"}

        try:
            from core.traceability.scanner import check_traceability
            _rt, report = check_traceability(self.project_path)
        except Exception as e:
            print(f"   Traceability check error: {e}")
            # P5+ is blocking — module errors must NOT silently pass
            blocking = self.phase is not None and self.phase >= 5
            return {"passed": not blocking, "skipped": True,
                    "error": str(e), "blocking": blocking}

        # PR 3: also verify the attestation (P3+ informational, P5+ blocking)
        att_status = "skipped"
        att_msg = ""
        try:
            from scripts.verify_trace_attestation import verify_attestation
            att_code, att_msg = verify_attestation(self.project_path)
            att_status = {0: "clean", 1: "mismatch", 2: "missing",
                          3: "schema-error"}.get(att_code, "unknown")
        except Exception as e:
            att_status = "error"
            att_msg = str(e)

        total = report["total"]

        # PR 13: Re-apply overlay to filter out manually VERIFIED FRs.
        # This fixes a gap where `check_traceability` (which is atomic and pure-regex)
        # sees intentionally manual FRs as 'untested' and triggers auto-fix stubs.
        untested_set = set(report["untested"])
        uncoded_set = set(report["uncoded"])
        try:
            from core.traceability.overlay import (
                atomic_to_dict, load_overlay, merge_overlay,
            )
            overlay = load_overlay(self.project_path / "TRACEABILITY_MATRIX.overlay.yaml")
            if overlay:
                # merge_overlay expects an atomic dict (with "requirements" key),
                # not the check_traceability report dict. Convert rt → atomic first.
                merged = merge_overlay(atomic_to_dict(_rt), overlay)
                for fr_id, row in merged.get("requirements", {}).items():
                    if row.get("status") == "VERIFIED" or "Manual" in str(row.get("test_files", [])):
                        untested_set.discard(fr_id)
                        uncoded_set.discard(fr_id)
        except Exception as e:
            print(f"   [WARN] Overlay merge failed: {e}")

        untested_list = list(untested_set)
        uncoded_list = list(uncoded_set)
        # A project with zero FRs (pure library, no traceable requirements)
        # vacuously satisfies traceability — nothing is untested or uncoded.
        # `total > 0` used to be required, permanently blocking such projects
        # at P5+ even though there is nothing to trace.
        complete = len(untested_list) == 0 and len(uncoded_list) == 0

        # P3: informational only (code being built)
        # P4: informational only (tests being built)
        # P5+: blocking (full FR→code→test traceability required)
        blocking = self.phase is not None and self.phase >= 5
        passed = complete if blocking else True
        if blocking and att_status in ("mismatch", "missing", "schema-error", "error"):
            passed = False

        c = report["completeness"]
        # Recalculate coverages if overlay mitigated them? Keep display atomic, but block state uses merged.
        print(f"   FRs: {total} | Code: {c['code_coverage']} | "
              f"Test: {c['test_coverage']} | "
              f"{'BLOCKING' if blocking else 'INFO'}")
        print(f"   Attestation: {att_status}  {att_msg}")
        if untested_list:
            print(f"   Untested FRs: {', '.join(untested_list)}")
        if uncoded_list:
            print(f"   Uncoded FRs: {', '.join(uncoded_list)}")

        # PR 9: dispatch one bounded auto-fix attempt at P5+ when blocked.
        # Per-strategy allowlist lives inside AutoFixEngine — only
        # `fix_missing_traceability` (problem_type='missing_traceability')
        # is dispatched. Other strategies still emit stubs.
        if blocking and not passed and (untested_list or uncoded_list):
            _fixed = _dispatch_trace_auto_fix(
                self.project_path, untested_list, uncoded_list,
                phase=self.phase,
            )
            if _fixed:
                try:
                    _rt2, report2 = check_traceability(self.project_path)  # noqa: F841
                    # PR 13 fix: re-apply the overlay to the re-verify report so that
                    # manually-VERIFIED FRs (whose status lives only in the overlay)
                    # do not reappear as untested after auto-fix.
                    try:
                        from core.traceability.overlay import (
                            atomic_to_dict, load_overlay, merge_overlay,
                        )
                        overlay2 = load_overlay(
                            self.project_path / "TRACEABILITY_MATRIX.overlay.yaml"
                        )
                        if overlay2:
                            merged2 = merge_overlay(atomic_to_dict(_rt2), overlay2)
                            _overlay_untested: set = set(report2.get("untested", []))
                            _overlay_uncoded: set = set(report2.get("uncoded", []))
                            for fr_id, row in merged2.get("requirements", {}).items():
                                if (row.get("status") == "VERIFIED"
                                        or "Manual" in str(row.get("test_files", []))):
                                    _overlay_untested.discard(fr_id)
                                    _overlay_uncoded.discard(fr_id)
                            report2 = dict(report2)
                            report2["untested"] = list(_overlay_untested)
                            report2["uncoded"] = list(_overlay_uncoded)
                    except Exception as _overlay_err:
                        print(f"   [WARN] re-verify overlay merge failed: {_overlay_err}")
                    still_untested = report2.get("untested", [])
                    still_uncoded = report2.get("uncoded", [])
                    if not still_untested and not still_uncoded:
                        passed = True
                        print("   [auto-fix] re-verify: all gaps closed")
                        # F-2.5 fix: auto-fix modified the source tree, so
                        # attestation.json is now stale. Re-derive and
                        # write it in-place so the next verify-trace
                        # call (or CI step) doesn't fire on a phantom
                        # mismatch. Developer still needs to `make attest`
                        # (which stages) — this only refreshes the
                        # canonical file.
                        try:
                            from scripts.build_trace_attestation import (
                                build_attestation, write_attestation,
                            )
                            _att = build_attestation(self.project_path)
                            write_attestation(self.project_path, _att)
                            print("   [auto-fix] attestation.json refreshed")
                        except Exception as _att_err:
                            print(f"   [auto-fix] attestation refresh failed: {_att_err}",
                                  file=sys.stderr)
                    else:
                        print(f"   [auto-fix] re-verify: {len(still_untested)} "
                              f"untested, {len(still_uncoded)} uncoded remain")
                except Exception as _post_err:
                    print(f"   [auto-fix] post-fix re-verify error: {_post_err}",
                          file=sys.stderr)
        ghost = report.get("ghost_frs", [])
        if ghost:
            print(f"   Ghost FRs (non-blocking): {', '.join(ghost)} — in code/tests but not in SAD.md")

        return {
            "passed": passed,
            "skipped": False,
            "total_frs": total,
            "untested": untested_list,
            "uncoded": uncoded_list,
            "completeness": c,
            "attestation": att_status,
            "attestation_message": att_msg,
        
            "blocking": blocking,
        }

    def preflight_fr_spec_consistency(self) -> Dict[str, Any]:
        """PR 7: SAD ↔ TEST_SPEC.md symmetric-difference check.

        Catches FRs declared in one source but not the other (a class
        of mismatch that 4a and 4b miss independently). P3/P4
        informational; P5+ blocking when orphans are found.

        If `02-architecture/TEST_SPEC.md` is missing, skip the check
        (4b will surface missing spec separately via
        `_run_spec_coverage_check`).
        """
        import re
        print("\n[PRE-FLIGHT] FR Spec Consistency")
        sad_path = self._layout.sad_path
        spec_path = self._layout.test_spec_path

        sad_frs: set = set()
        if sad_path.exists():
            try:
                sad_text = sad_path.read_text(encoding="utf-8", errors="replace")
                sad_frs = {f"FR-{int(m):02d}" for m in
                           re.findall(r"\bFR-(\d+)\b", sad_text)}
            except OSError as e:
                print(f"   SAD read error: {e}", file=sys.stderr)

        spec_frs: set = set()
        if not spec_path.exists():
            # PR 13: If SAD has FRs, TEST_SPEC.md MUST exist.
            # D4 also silently skipped missing TEST_SPEC.md, creating a major loophole.
            if sad_frs:
                blocking = self.phase is not None and self.phase >= 5
                print(f"   TEST_SPEC.md is missing but SAD.md contains {len(sad_frs)} FRs.")
                return {"passed": not blocking, "skipped": False, 
                        "error": "TEST_SPEC.md is missing but SAD.md has FRs.",
                        "orphans": len(sad_frs), "missing_spec": True}
            print("   Skipped: TEST_SPEC.md not present at 02-architecture/")
            return {"passed": True, "skipped": True,
                    "reason": "TEST_SPEC.md missing and no SAD FRs to track"}

        try:
            spec_text = spec_path.read_text(encoding="utf-8", errors="replace")
            # Match `### FR-XX` headings (per derive_test_cases.md convention)
            spec_frs = {f"FR-{int(m):02d}" for m in
                        re.findall(r"^#{1,6}\s*FR-(\d+)\b",
                                   spec_text, re.MULTILINE)}
        except OSError as e:
            print(f"   TEST_SPEC read error: {e}", file=sys.stderr)
            return {"passed": True, "skipped": True, "error": str(e)}

        sad_only = sorted(sad_frs - spec_frs)
        spec_only = sorted(spec_frs - sad_frs)
        orphans = len(sad_only) + len(spec_only)
        blocking = self.phase is not None and self.phase >= 5
        passed = (orphans == 0) or (not blocking)

        if orphans:
            if sad_only:
                print(f"   sad_only (in SAD but not in TEST_SPEC): {sad_only}")
            if spec_only:
                print(f"   spec_only (in TEST_SPEC but not in SAD): {spec_only}")
            if blocking:
                print(f"   [BLOCKED] Phase {self.phase} requires SAD↔SPEC parity")
            else:
                print(f"   INFO: {orphans} orphan(s); not blocking at phase {self.phase}")
        else:
            print("   SAD and TEST_SPEC agree on FR set")

        return {
            "passed": passed,
            "blocking": blocking,
            "sad_only": sad_only,
            "spec_only": spec_only,
            "orphan_count": orphans,
        }

    # Source dirs scanned by the reliability/config-liveness preflights —
    # same layout convention as the in-process scanners (lang_scanners).
    _SCAN_SRC_DIRS = ("03-development/src", "src")

    def preflight_reliability_lint(self) -> Dict[str, Any]:
        """v2.9 A2: vendored semgrep reliability rules over the source tree.

        High-confidence resource/concurrency patterns (subprocess without
        timeout, mkstemp outside try, TOCTOU, time.sleep in async, …) distilled
        from the tts-new post-Gate-4 bug hunt. P3 informational; P4+ blocking
        on any finding. Python ruleset only for now — js/ts projects skip with
        a note (js_reliability.yaml is tracked in ADDING_LANGUAGE_SUPPORT_SOP).
        """
        import shutil
        import subprocess as _sp

        print("\n[PRE-FLIGHT] Reliability Lint (semgrep, vendored rules)")
        from core.utils.lang_patterns import project_language
        language = project_language(self.project_path)
        if language != "python":
            print(f"   Skipped: no reliability ruleset for '{language}' yet")
            return {"passed": True, "skipped": True,
                    "reason": f"no ruleset for {language}"}

        targets = [str(self.project_path / d) for d in self._SCAN_SRC_DIRS
                   if (self.project_path / d).is_dir()]
        if not targets:
            print("   Skipped: no source directories (src/, 03-development/src)")
            return {"passed": True, "skipped": True, "reason": "no src dirs"}

        blocking = self.phase is None or self.phase >= 4
        if not shutil.which("semgrep"):
            # Pinned in requirements.txt — absence is an environment defect,
            # not a reason to silently skip a blocking check.
            print("   semgrep not found (pinned in requirements.txt)")
            return {"passed": not blocking, "skipped": False, "blocking": blocking,
                    "error": "semgrep not installed — pip install -r requirements.txt"}

        rules = (Path(__file__).parent.parent / "harness" / "toolchains"
                 / "semgrep_rules" / "py_reliability.yaml")
        try:
            proc = _sp.run(
                ["semgrep", "scan", "--config", str(rules), "--json",
                 "--metrics=off", "--quiet", *targets],
                capture_output=True, text=True, timeout=180,
            )
            data = json.loads(proc.stdout)
            findings = data.get("results", [])
        except (_sp.TimeoutExpired, _sp.SubprocessError, json.JSONDecodeError, OSError) as e:
            print(f"   semgrep run failed: {e}")
            return {"passed": not blocking, "skipped": False, "blocking": blocking,
                    "error": f"semgrep run failed: {e}"}

        items = [
            {"rule": r.get("check_id", "?").split(".")[-1],
             "file": r.get("path", "?"),
             "line": r.get("start", {}).get("line"),
             "severity": r.get("extra", {}).get("severity", "?")}
            for r in findings
        ]
        passed = (not items) or (not blocking)
        if items:
            for it in items[:10]:
                print(f"   {it['severity']:7} {it['rule']} {it['file']}:{it['line']}")
            if len(items) > 10:
                print(f"   ... and {len(items) - 10} more")
            print(f"   [{'BLOCKED' if blocking else 'INFO'}] "
                  f"{len(items)} reliability finding(s) at phase {self.phase}")
        else:
            print("   No reliability findings")
        return {"passed": passed, "blocking": blocking,
                "finding_count": len(items), "findings": items[:50]}

    # Runtime/system env vars that are not project configuration.
    _SYSTEM_ENV_VARS = frozenset({
        "PATH", "HOME", "USER", "SHELL", "TERM", "LANG", "LC_ALL", "TZ",
        "PWD", "TMPDIR", "TMP", "TEMP", "HOSTNAME", "CI",
        "PYTHONPATH", "VIRTUAL_ENV", "NODE_ENV", "PYTEST_CURRENT_TEST",
    })

    # Files where project env vars are legitimately declared/documented.
    _ENV_DECLARATION_GLOBS = (
        ".env.example", ".env.sample", ".env.template", ".env",
        "docker-compose*.yml", "docker-compose*.yaml",
        "deployment/**/*.yml", "deployment/**/*.yaml",
        "k8s/**/*.yml", "k8s/**/*.yaml",
        "README.md",
    )

    def preflight_config_liveness(self) -> Dict[str, Any]:
        """v2.9 A3: env keys read in code must be declared somewhere.

        Catches the tts-new config bug class: a typo'd env var name means the
        code path silently always uses the default — tests stay green, prod
        config is dead. Cross-checks os.getenv/os.environ (py) and
        process.env.X (js/ts) against declaration sources (.env.example,
        docker-compose, deployment manifests, README). No declaration source
        in the project → skipped (small projects without deploy config).
        P3 informational; P4+ blocking on orphan keys.
        """
        print("\n[PRE-FLIGHT] Config Liveness (env keys read vs declared)")
        from core.utils.lang_patterns import (
            iter_source_files, project_language,
        )
        language = project_language(self.project_path)

        decl_text = ""
        decl_files = []
        for pattern in self._ENV_DECLARATION_GLOBS:
            for f in sorted(self.project_path.glob(pattern)):
                if f.is_file():
                    decl_files.append(str(f.relative_to(self.project_path)))
                    try:
                        decl_text += f.read_text(encoding="utf-8", errors="replace")
                    except OSError:
                        pass

        # Parse declared env keys exactly — substring matching caused a bug where
        # KOKORO_BACKEND_URL passed as "declared" when only LEGACY_KOKORO_BACKEND_URL_V1
        # was actually in .env.example. .env files use KEY=value; YAML uses KEY: value.
        declared_keys: set[str] = set()
        for line in decl_text.splitlines():
            stripped = line.strip()
            # .env  KEY=value  (stop at = or : to get the bare key)
            eq_pos = stripped.find("=")
            colon_pos = stripped.find(":")
            delim = min(eq_pos, colon_pos) if min(eq_pos, colon_pos) != -1 else max(eq_pos, colon_pos)
            if delim > 0:
                key = stripped[:delim].strip()
                if key and key.isidentifier() and key.isupper():
                    declared_keys.add(key)
        if not decl_files:
            print("   Skipped: no env declaration sources "
                  "(.env.example / docker-compose / deployment / README)")
            return {"passed": True, "skipped": True, "reason": "no declaration sources"}

        if language == "python":
            key_re = re.compile(
                # Require the call/subscript to actually close — otherwise
                # syntactically broken code (e.g. a missing `)`) still
                # matches and is misreported as a legitimate declaration.
                r"os\.(?:getenv|environ\.get)\(\s*['\"]([A-Z][A-Z0-9_]+)['\"]\s*[,)]"
                r"|os\.environ\[\s*['\"]([A-Z][A-Z0-9_]+)['\"]\s*\]"
            )
        else:
            key_re = re.compile(
                r"process\.env\.([A-Z][A-Z0-9_]+)"
                r"|process\.env\[\s*['\"]([A-Z][A-Z0-9_]+)['\"]\s*\]"
            )

        used: dict[str, str] = {}  # key → first "file:line"
        for rel_dir in self._SCAN_SRC_DIRS:
            base = self.project_path / rel_dir
            if not base.is_dir():
                continue
            for src in iter_source_files(base, language):
                try:
                    text = src.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                # Scan the whole file (not line-by-line): a multi-line env read
                # — os.environ.get(\n  "KEY", ...) — is exactly the case the
                # typo'd KOKORO_BACKEND_URL bug lived in, and \s* spans the
                # newline. Derive the line number from the match offset.
                for m in key_re.finditer(text):
                    key = m.group(1) or m.group(2)
                    if key and key not in self._SYSTEM_ENV_VARS:
                        lineno = text.count("\n", 0, m.start()) + 1
                        used.setdefault(
                            key,
                            f"{src.relative_to(self.project_path)}:{lineno}",
                        )

        orphans = {k: loc for k, loc in sorted(used.items())
                   if k not in declared_keys}
        blocking = self.phase is None or self.phase >= 4
        passed = (not orphans) or (not blocking)
        if orphans:
            for k, loc in list(orphans.items())[:10]:
                print(f"   ORPHAN {k}  (read at {loc}, declared nowhere)")
            print(f"   [{'BLOCKED' if blocking else 'INFO'}] "
                  f"{len(orphans)} env key(s) read in code but not declared in "
                  f"{len(decl_files)} source(s)")
        else:
            print(f"   {len(used)} env key(s) all declared "
                  f"({len(decl_files)} declaration source(s))")
        return {"passed": passed, "blocking": blocking,
                "orphans": orphans, "used_count": len(used),
                "declaration_files": decl_files}

    def preflight_gap_analysis(self) -> Dict[str, Any]:
        """M3 gap analysis — detect SPEC.md ↔ codebase gaps (P3+, informational)."""
        if self.phase is not None and self.phase < 3:
            return {"passed": True, "skipped": True, "reason": "P1/P2 — no gap analysis"}
        print("\n[PRE-FLIGHT] M3 Gap Analysis")
        try:
            from gap_detector.parser import SpecParser
            from gap_detector.scanner import CodeScanner
            from gap_detector.detector import GapDetector

            spec_path = self._layout.spec_path
            if not spec_path.exists():
                print("   SPEC.md not found — skipping gap analysis")
                return {"passed": True, "skipped": True, "reason": "SPEC.md not found"}

            spec = SpecParser(str(spec_path)).parse()
            scanner = CodeScanner(str(self.project_path))
            code = scanner.scan()
            detector = GapDetector(spec, code)
            gaps = detector.detect()
            summary = detector.get_summary()

            report_path = self._layout.methodology_dir / "gap_report.json"
            report_path.parent.mkdir(parents=True, exist_ok=True)
            # Bug #104 fix: atomic write so a crash mid-write does not
            # leave a truncated gap_report.json. The next preflight would
            # otherwise refuse to load the corrupt JSON and surface a
            # confusing parse error instead of the real gap analysis
            # result.
            atomic_write_json(report_path, {
                "summary": {
                    "total": summary.total_gaps, "missing": summary.missing,
                    "incomplete": summary.incomplete, "orphaned": summary.orphaned,
                    "critical": summary.critical, "major": summary.major,
                    "minor": summary.minor,
                },
                "gaps": [{"type": g.gap_type, "severity": g.severity,
                          "reason": g.reason, "action": g.recommended_action}
                         for g in gaps],
            })
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

    CI_READINESS_COMPONENTS = (
        "ci_workflow", "git_hooks", "harness_importable",
        "ecc_hooks", "branch_protection",
    )

    def preflight_manifest_integrity(self) -> Dict[str, Any]:
        """Fix IV — validate quality_manifest.json structure before any phase preflight.

        Corrupted manifests (truncated fr_ids, empty gate1 dict, missing
        fr_module_traceability) cause workflows to stall in infinite retry
        loops because gate1-precheck sees no completed FRs and re-dispatches
        TDD agents that also cannot complete.  This hook detects the three
        known corruption patterns and blocks phase entry with a clear
        recovery command.
        """
        print("\n[PRE-FLIGHT] Manifest Integrity Check")
        manifest_path = self.project_path / ".methodology" / "quality_manifest.json"
        if not manifest_path.exists():
            return {"passed": True, "skipped": True,
                    "reason": "quality_manifest.json not yet created"}

        try:
            mf = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"  [BLOCKED] quality_manifest.json is unreadable: {exc}")
            print("  Recovery: git checkout HEAD -- .methodology/quality_manifest.json")
            return {"passed": False, "blocked": True,
                    "reason": f"Manifest unreadable: {exc}",
                    "recovery": "git checkout HEAD -- .methodology/quality_manifest.json"}

        fr_ids = mf.get("fr_ids") or []
        fr_trace = mf.get("fr_module_traceability") or {}
        gate_results = mf.get("gate_results") or {}
        gate1 = gate_results.get("gate1") or {}

        issues: list[str] = []

        # Pattern A: fr_ids truncated (e.g. 3→2 after sub-agent edit)
        if len(fr_ids) < len(fr_trace):
            issues.append(
                f"fr_ids has {len(fr_ids)} entries but fr_module_traceability "
                f"has {len(fr_trace)} — manifest was likely truncated by a "
                f"sub-agent. Expected FRs: {sorted(fr_trace.keys())}")
        # Pattern B: gate1 emptied while independent evidence says Gate 1 has
        # run — that is corruption. A fresh Phase 3 entry (post-reset, or right
        # after P2 manifest generation) legitimately has an empty gate1: no FR
        # has been finalized yet. Distinguish via the FSM (state.json
        # last_gate/last_fr are set only by finalize-gate) and residual per-FR
        # artifacts, instead of blocking on emptiness alone.
        if self.phase is not None and self.phase >= 3 and not gate1:
            evidence: list[str] = []
            try:
                _st = json.loads(self.state_path.read_text(encoding="utf-8"))
                if _st.get("last_gate") or _st.get("last_fr"):
                    evidence.append(
                        f"state.json last_gate={_st.get('last_gate')!r} "
                        f"last_fr={_st.get('last_fr')!r}")
            except (OSError, json.JSONDecodeError, AttributeError):
                # AttributeError: state.json parsed but isn't a dict (e.g. a
                # list) — treat like unreadable/unparseable, no evidence.
                pass
            _md = self._layout.methodology_dir
            for _artifact in ("gate1_result.json", "fr_progress.json",
                              ".gate1_scores.json"):
                if (_md / _artifact).exists():
                    evidence.append(_artifact)
            if evidence:
                issues.append(
                    "gate_results.gate1 is empty but Gate 1 has run "
                    f"(evidence: {', '.join(evidence)}) — manifest per-FR "
                    "results were likely wiped")
        # Pattern C: fr_ids missing but traceability present (shouldn't happen
        # after Phase 2 generation)
        if not fr_ids and fr_trace:
            issues.append(
                "fr_ids is empty but fr_module_traceability has entries — "
                "manifest likely truncated")

        if issues:
            for issue in issues:
                print(f"  [BLOCKED] {issue}")
            print("  Recovery: git checkout HEAD -- .methodology/quality_manifest.json")
            return {"passed": False, "blocked": True,
                    "reason": "; ".join(issues),
                    "recovery": "git checkout HEAD -- .methodology/quality_manifest.json"}

        print(f"  OK: {len(fr_ids)} FRs, "
              f"{len(fr_trace)} traceability entries, "
              f"{len(gate1)} gate1 entries")
        return {"passed": True, "fr_count": len(fr_ids)}

    def preflight_ci_readiness(self) -> Dict[str, Any]:
        """Check target project CI wiring + ECC hooks + branch protection (advisory, non-blocking)."""
        print("\n[PRE-FLIGHT] CI Readiness Check")
        checks: Dict[str, bool] = {}
        # Accept either harness_quality_gate.yml (target-project contract, see
        # init-project) or harness_ci.yml (this framework repo's own CI; both
        # are referenced in CONTRIBUTING/README/INTEGRATION and are equivalent
        # in scope — lint + test + manifest/trace validation).
        workflows_dir = self.project_path / ".github" / "workflows"
        checks["ci_workflow"] = (
            (workflows_dir / "harness_quality_gate.yml").exists()
            or (workflows_dir / "harness_ci.yml").exists()
        )
        hooks_dir = self.project_path / ".git" / "hooks"
        checks["git_hooks"] = (hooks_dir / "prepare-commit-msg").exists()
        checks["harness_importable"] = (
            (self.project_path / "harness" / "core" / "quality_gate" / "__init__.py").exists()
            or (self.project_path / "core" / "quality_gate" / "__init__.py").exists()
            or (self.project_path / "harness_cli.py").exists()
            or (self.project_path / "harness" / "harness_cli.py").exists()
        )
        # ECC hooks — session-level git --no-verify blocker
        ecc_file = Path.home() / ".claude" / "hooks" / "hooks.json"
        if ecc_file.exists():
            try:
                import json as _json
                ecc_data = _json.loads(ecc_file.read_text(encoding="utf-8"))
                checks["ecc_hooks"] = "pre:bash:dispatcher" in ecc_data
            except Exception:
                checks["ecc_hooks"] = False
        else:
            checks["ecc_hooks"] = False
        # Branch protection — best-effort via gh CLI
        checks["branch_protection"] = self._check_branch_protection()

        missing = [k for k, v in checks.items() if not v]
        # ci-ack: projects that deliberately cannot/will not resolve a component
        # (e.g. an agent forbidden from touching branch protection rules) can
        # acknowledge it once via `harness_cli.py ci-ack --component <name>` so
        # this advisory warning doesn't repeat on every phase's preflight.
        ack: Dict[str, bool] = {}
        state_path = self.project_path / ".methodology" / "state.json"
        if state_path.exists():
            try:
                ack_data = json.loads(state_path.read_text(encoding="utf-8")).get("ci_readiness_ack")
                ack = ack_data if isinstance(ack_data, dict) else {}
            except Exception:
                pass
        missing = [k for k in missing if not ack.get(k)]
        if missing:
            print(f"   WARNING: Missing CI/enforcement components: {missing}")
            if "ecc_hooks" in missing:
                print("   → ECC hooks: bash scripts/setup-ecc-hooks.sh")
            if "branch_protection" in missing:
                print("   → Branch protection: python3 harness_cli.py init-project --setup-branch-protection")
            if not checks["ci_workflow"]:
                print(f"   → CI: python3 harness_cli.py init-project --project {self.project_path}")
        else:
            print("   All CI wiring + bypass protections present")
        return {"passed": True, "checks": checks,
                "missing": missing,
                "message": "All CI wiring present" if not missing else f"Missing: {missing}"}

    def _check_branch_protection(self) -> bool:
        """Best-effort check for GitHub branch protection on main (requires gh CLI).

        Returns True when: (a) protection is positively confirmed active, or
        (b) we cannot verify (auth/network errors, missing gh) — assumes OK to
        avoid false alarms.  Returns False only when we positively confirm
        protection is absent (API 404) or no GitHub remote exists.
        """
        try:
            import subprocess
            remote = subprocess.run(
                ["git", "-C", str(self.project_path), "remote", "get-url", "origin"],
                capture_output=True, text=True, timeout=10,
            )
            if remote.returncode != 0:
                return False  # No git remote — not a GitHub project
            remote_url = remote.stdout.strip()
            if "github.com" not in remote_url:
                return False  # Not a GitHub remote — skip

            # Parse owner/repo
            url = remote_url.rstrip(".git")
            parts = url.split("github.com")[-1].strip("/:").split("/")
            if len(parts) < 2:
                return False
            owner, repo = parts[-2], parts[-1]

            r = subprocess.run(
                ["gh", "api", f"repos/{owner}/{repo}/branches/main/protection",
                 "--jq", ".allow_force_pushes.enabled,.allow_deletions.enabled"],
                capture_output=True, text=True, timeout=15,
            )
            if r.returncode != 0:
                # 404 = "Branch not protected" → genuinely unconfigured
                if "404" in (r.stderr or "") or "Not Found" in (r.stderr or ""):
                    return False
                # Other errors (auth, network) → can't verify; don't alert
                return True  # Assume OK if we can't reach the API
            # Parse two boolean lines: allow_force_pushes, allow_deletions
            lines = [ln.strip() for ln in r.stdout.strip().splitlines() if ln.strip()]
            if len(lines) >= 2:
                force_ok = lines[0].lower() == "false"
                delete_ok = lines[1].lower() == "false"
                return force_ok and delete_ok
            return False
        except Exception:
            return True  # Can't verify — assume OK (don't cry wolf)

    def preflight_previous_phase_artifacts(self) -> Dict[str, Any]:
        """Check that previous phase's required deliverables exist (ASPICE traceability).

        Ensures the ASPICE chain is intact before starting the current phase.
        P1 is exempt (no previous phase).
        """
        print("\n[PRE-FLIGHT] Previous Phase Artifact Check")
        if self.phase is None or self.phase <= 1:
            return {"passed": True, "skipped": True, "message": "P1 has no previous phase"}

        try:
            from core.quality_gate.phase_artifact_enforcer import PhaseArtifactRegistry  # pyright: ignore[reportMissingImports]
            registry = PhaseArtifactRegistry(str(self.project_path))
            result = registry.verify_phase_chain(self.phase)
        except ImportError:
            print("   ERROR: PhaseArtifactRegistry not importable — cannot verify artifact chain")
            return {"passed": False, "skipped": True,
                    "error": "PhaseArtifactRegistry import failed"}
        except Exception as exc:
            print(f"   ERROR: Artifact chain verification crashed: {exc}")
            return {"passed": False, "skipped": True,
                    "error": f"verify_phase_chain({self.phase}) raised {type(exc).__name__}: {exc}"}

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
        """Run all pre-flight checks wrapped in an OpenTelemetry span."""
        if getattr(self, "tracer", None):
            with self.tracer.start_as_current_span(f"phase_{self.phase}_preflight") as span:
                span.set_attribute("harness.phase", self.phase if self.phase is not None else -1)
                result = self._do_preflight_all()
                span.set_attribute("harness.all_passed", result.get("all_passed", False))
                return result
        return self._do_preflight_all()

    def _do_preflight_all(self) -> Dict[str, Any]:
        """Run all pre-flight checks."""
        print(f"\n{'='*60}\nPRE-FLIGHT: Phase {self.phase}\n{'='*60}")
        results = {
            "manifest_integrity": self.preflight_manifest_integrity(),
            "fsm": self.preflight_fsm_check(),
            "bvs_phase_order": self.preflight_bvs_phase_order(),
            "kill_switch": self.preflight_kill_switch(),
            "previous_phase_artifacts": self.preflight_previous_phase_artifacts(),
            "drift_detection": self.preflight_drift_detection(),
            "sab": self.preflight_sab_check(),
            "tool_registry": self.preflight_tool_registry(),
            "traceability": self.preflight_traceability(),
            "fr_spec_consistency": self.preflight_fr_spec_consistency(),
            "reliability_lint": self.preflight_reliability_lint(),
            "config_liveness": self.preflight_config_liveness(),
            "gap_analysis": self.preflight_gap_analysis(),
            "ci_readiness": self.preflight_ci_readiness(),
        }
        self.preflight_results = results
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
            # Bug #104 fix: atomic write so the phase advance is durable
            # even if the process is killed mid-write. A truncated
            # state.json here would desync the recorded phase from the
            # actual project state, and the next hook run would either
            # skip preflight checks (state corrupted to FREEZE-by-mistake)
            # or refuse to advance.
            atomic_write_json(self.state_path, state)
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
        # `phase is None` is treated as P4+ so a broken detector can never
        # silently pass (caller violated the constructor contract).
        blocking = self.phase is None or self.phase >= 4
        try:
            from detection import DriftDetector
            detector = DriftDetector(str(self.project_path))
            results = detector.detect_all()
            total_drifts = sum(r.drifted for r in results.values())
            avg_score = (sum(r.score for r in results.values()) /
                         max(len(results), 1))
            score_pct = avg_score * 100
            passed = score_pct >= self.drift_threshold if blocking else True
            print(f"   Drifts: {total_drifts}, Score: {score_pct:.0f}% "
                  f"(threshold: {self.drift_threshold:.0f}%)")
            return {"passed": passed, "drifts": total_drifts,
                    "score": score_pct,
                    "threshold": self.drift_threshold}
        except ImportError:
            return {"passed": True, "skipped": True, "message": "detection module unavailable"}
        except Exception as e:
            print(f"   Drift detection error: {e}")
            # P4+: errors are blocking — a broken detector must not silently pass
            return {"passed": not blocking, "skipped": True, "error": str(e)}

    def postflight_artifact_links(self) -> Dict[str, Any]:
        """Post-flight ASPICE traceability: verify current phase artifacts cite predecessor outputs.

        Calls verify_phase_link(skip_to_side=False) for every dependency of the
        current phase.  Preflight skips this check (artifacts don't exist yet at
        phase entry); postflight is the only point where the check can be enforced
        against the artifacts the agent just wrote.  Blocking for P4+.
        """
        print("\n[POST-FLIGHT] Artifact Cross-Reference Check (ASPICE)")
        if self.phase is None or self.phase <= 1:
            print("   Skipped: P1 has no predecessor artifacts")
            return {"passed": True, "skipped": True, "reason": "P1 has no predecessor artifacts"}
        blocking = self.phase >= 4
        try:
            from core.quality_gate.phase_artifact_enforcer import (  # pyright: ignore
                Phase, PhaseArtifactRegistry,
            )
            try:
                current_enum = Phase.from_int(self.phase)
            except KeyError:
                return {"passed": True, "skipped": True,
                        "reason": f"No phase enum for phase {self.phase}"}
            registry = PhaseArtifactRegistry(str(self.project_path))
            deps: List = registry.PHASE_ARTIFACTS.get(
                current_enum, {}).get("depends_on", [])
            if not deps:
                print("   No predecessor dependencies — skipped")
                return {"passed": True, "skipped": True, "reason": "No predecessor dependencies"}
            issues: List[str] = []
            for dep_enum in deps:
                link = registry.verify_phase_link(dep_enum, current_enum, skip_to_side=False)
                if not link.passed:
                    print(f"   FAIL: {link.reason}")
                    issues.append(link.reason)
                else:
                    print(f"   PASS: {link.reason}")
            passed = len(issues) == 0
            return {"passed": passed, "issues": issues, "blocking": blocking}
        except ImportError:
            return {"passed": True, "skipped": True,
                    "message": "PhaseArtifactRegistry unavailable"}
        except Exception as e:
            print(f"   Artifact link check error: {e}")
            return {"passed": not blocking, "skipped": True,
                    "error": str(e), "blocking": blocking}

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
        """Run all post-flight checks wrapped in an OpenTelemetry span."""
        if getattr(self, "tracer", None):
            with self.tracer.start_as_current_span(f"phase_{self.phase}_postflight") as span:
                span.set_attribute("harness.phase", self.phase if self.phase is not None else -1)
                result = self._do_postflight_all()
                span.set_attribute("harness.success", result.get("success", False))
                return result
        return self._do_postflight_all()

    def _do_postflight_all(self) -> Dict[str, Any]:
        """Run all post-flight checks."""
        print(f"\n{'='*60}\nPOST-FLIGHT: Phase {self.phase}\n{'='*60}")
        const_result = self.postflight_constitution()
        bvs_result = self.postflight_bvs_invariants()
        steering_result = self.postflight_steering_summary()
        drift_result = self.postflight_drift_check()
        artifact_links_result = self.postflight_artifact_links()
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
            and artifact_links_result.get("passed", True)
        )
        state_result = self.postflight_update_state(success=success)
        summary = self.postflight_summary()
        print(f"\nPOST-FLIGHT: {'PASS' if success else 'FAIL'}")
        return {"success": success, "constitution": const_result,
                "bvs_invariants": bvs_result, "steering": steering_result,
                "drift_detection": drift_result, "artifact_links": artifact_links_result,
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
