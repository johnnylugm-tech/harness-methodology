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
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Dict, List, Any

from kill_switch import KillSwitch
from kill_switch.models import MonitorConfig
from core.atomic_io import atomic_write_json  # Bug #104 fix
from core.state_io import StateCorruptError, load_quality_manifest, load_state
from core.utils.timefmt import utc_now_iso


class KillSwitchBlockedError(RuntimeError):
    """Raised when kill-switch circuit is OPEN for an agent."""


# Round 14 A: cross-phase obligation model. The preflight_* methods all
# pattern-match on `self.phase` to decide whether a finding is blocking,
# so a "P(N) informational, P(N+1) blocking" gap is structurally invisible
# to advance-phase. The fix is to simulate the preflight at next_phase and
# surface the resulting blocking findings as structured obligations that
# HANDOVER.md can carry over to the next session.
@dataclass(frozen=True)
class Obligation:
    """A cross-phase carry-over obligation: P(N+1) entry blocking finding.

    `check_id` is the preflight_* key from PREFLIGHT_CHECKS (e.g.
    "property_spec", "reliability_lint"). `rule_id` identifies the specific
    FR / rule / file that triggered the obligation (e.g. "FR-03",
    "py-mkstemp-outside-try", or the check_id itself when no finer-grained
    rule is available). `message` is the human-readable description already
    printed to stdout by the preflight run.
    """

    check_id: str
    target_phase: int
    rule_id: str
    message: str
    file: str | None = None
    line: int | None = None


# Preflights that use the "P(N) informational, P(N+1) blocking" pattern.
# Preview only surfaces these — environmental / always-blocking checks
# (kill_switch, tool_registry, constitution, FSM) are not carry-over
# obligations and would only drown the operator in noise.
#
# These are PREFLIGHT_CHECKS *keys* (the first element of each pair), not
# method names. Round 43 站1: the set carried "sab_check" — the method name —
# from Round 14 A onward, so no SAB finding ever reached an obligation and the
# `elif check_id == "sab_check"` branch below was unreachable. The subset
# assertion in tests/test_preflight_registry.py now pins the two registries to
# each other; it is the fourth instance of the registry-vs-consumer
# disagreement Round 27 站4 catalogued.
_DELAYED_BLOCKING_PREFLIGHTS: frozenset[str] = frozenset({
    "drift_detection",
    "sab",
    "traceability",
    "fr_spec_consistency",
    "property_spec",
    "artifact_consistency",
    "reliability_lint",
    "config_liveness",
    "previous_phase_artifacts",
    "bvs_phase_order",
})


def _obligations_from_preflight(
    check_id: str, res: Dict[str, Any], target_phase: int,
) -> List[Obligation]:
    """Convert one preflight result dict into a list of Obligation rows.

    Each preflight reports its findings in a slightly different shape, so
    the extractor is per-check. Unknown check_ids fall back to a single
    obligation carrying the check_id / blocking message.
    """
    out: List[Obligation] = []
    if check_id == "property_spec":
        # Use `divergences` (set of rule_ids reported as errors by
        # PhaseHooks.preflight_property_spec) — one obligation per FR.
        for rid in res.get("divergences") or []:
            out.append(Obligation(
                check_id=check_id,
                target_phase=target_phase,
                rule_id=str(rid),
                message=(f"{rid} declares a property invariant but no executing "
                         "property-based test (hypothesis @given / fast-check) "
                         "covers it — add the test before entering the target phase"),
            ))
    elif check_id == "reliability_lint":
        # Use `findings` (list of {rule, file, line, severity} dicts).
        for f in res.get("findings") or []:
            out.append(Obligation(
                check_id=check_id,
                target_phase=target_phase,
                rule_id=str(f.get("rule", "?")),
                file=f.get("file"),
                line=f.get("line"),
                message=(f"{f.get('severity', '?')} {f.get('rule', '?')} "
                         f"{f.get('file', '?')}:{f.get('line', '?')} — "
                         "resolve before entering the target phase"),
            ))
    # Round 15 §3: fill in the remaining 7 delayed-blocking preflights that
    # previously fell through to the generic fallback below despite having
    # structured, actionable detail in their own return dict. Each branch
    # uses .get() defensively — a preflight's return shape drifting under
    # us must degrade to an empty obligation list here, never a crash.
    elif check_id == "drift_detection":
        # Use `details` ({drift_type: {items: [{type, location, description}]}}).
        for _drift_type, _detail in (res.get("details") or {}).items():
            _items = _detail.get("items") if isinstance(_detail, dict) else None
            for item in _items or []:
                out.append(Obligation(
                    check_id=check_id,
                    target_phase=target_phase,
                    rule_id=str(item.get("type", _drift_type)),
                    file=item.get("location"),
                    message=str(item.get("description",
                                          f"{_drift_type} drift detected")),
                ))
    elif check_id == "sab":
        # Use `violations` (list of already-formatted strings).
        for v in res.get("violations") or []:
            out.append(Obligation(
                check_id=check_id, target_phase=target_phase,
                rule_id=check_id, message=str(v),
            ))
    elif check_id == "traceability":
        # Use `untested` / `uncoded` (FR id lists) + attestation status.
        for fr in res.get("untested") or []:
            out.append(Obligation(
                check_id=check_id, target_phase=target_phase, rule_id=str(fr),
                message=f"{fr} has no test coverage — traceability requires "
                        "an FR→test link",
            ))
        for fr in res.get("uncoded") or []:
            out.append(Obligation(
                check_id=check_id, target_phase=target_phase, rule_id=str(fr),
                message=f"{fr} has no implementation — traceability requires "
                        "an FR→code link",
            ))
        _att = res.get("attestation")
        if _att and _att not in ("clean", "skipped"):
            out.append(Obligation(
                check_id=check_id, target_phase=target_phase,
                rule_id="attestation",
                message=str(res.get("attestation_message")
                            or f"trace attestation status: {_att}"),
            ))
    elif check_id == "fr_spec_consistency":
        # Use `sad_only` / `spec_only` (FR id lists — symmetric difference).
        for fr in res.get("sad_only") or []:
            out.append(Obligation(
                check_id=check_id, target_phase=target_phase, rule_id=str(fr),
                message=f"{fr} is declared in SAD.md but missing from "
                        "TEST_SPEC.md",
            ))
        for fr in res.get("spec_only") or []:
            out.append(Obligation(
                check_id=check_id, target_phase=target_phase, rule_id=str(fr),
                message=f"{fr} is declared in TEST_SPEC.md but missing from "
                        "SAD.md",
            ))
    elif check_id == "artifact_consistency":
        # Use `error_details` (Round 15 §3 additive key on
        # preflight_artifact_consistency — see that method).
        for d in res.get("error_details") or []:
            out.append(Obligation(
                check_id=check_id, target_phase=target_phase,
                rule_id=str(d.get("rule_id", check_id)),
                message=str(d.get("message", "artifact consistency violation")),
            ))
    elif check_id == "config_liveness":
        # Use `orphans` ({env_key: "file:line"}).
        for key, loc in (res.get("orphans") or {}).items():
            _file, _, _line = str(loc).rpartition(":")
            out.append(Obligation(
                check_id=check_id, target_phase=target_phase, rule_id=str(key),
                file=_file or None,
                line=int(_line) if _line.isdigit() else None,
                message=f"env key {key} read in code but not declared anywhere",
            ))
    elif check_id == "previous_phase_artifacts":
        # Use `missing` (list of already-formatted phase-link strings).
        for m in res.get("missing") or []:
            out.append(Obligation(
                check_id=check_id, target_phase=target_phase,
                rule_id=check_id, message=str(m),
            ))
    elif check_id == "bvs_phase_order":
        # Use `violations` (list of {rule, message} dicts).
        for v in res.get("violations") or []:
            if not isinstance(v, dict):
                continue
            out.append(Obligation(
                check_id=check_id, target_phase=target_phase,
                rule_id=str(v.get("rule", check_id)),
                message=str(v.get("message", "BVS phase-order violation")),
            ))
    else:
        # Generic fallback: one obligation per blocking result, carrying the
        # check's own error string. File/line not available.
        msg = (res.get("error") or res.get("message")
               or f"{check_id} would block at phase {target_phase}")
        out.append(Obligation(
            check_id=check_id,
            target_phase=target_phase,
            rule_id=check_id,
            message=str(msg),
        ))
    return out



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


# Declarative preflight pipeline: the ONLY source of checks for
# _do_preflight_all. Each entry is (result_key, method_name) — result_key is
# the key in the returned details dict; tuple order is execution/print order.
# Adding a preflight_* method to PhaseHooks without registering it here (or in
# NON_PIPELINE_PREFLIGHTS with a reason) fails the completeness meta-test in
# tests/test_preflight_registry.py — the structural fix for the recurring
# "check silently un-wired during refactor" incident class (7 wiring guards
# in tests/REGRESSION_GUARDS.yaml each pin one such incident).
PREFLIGHT_CHECKS: "tuple[tuple[str, str], ...]" = (
    ("manifest_integrity", "preflight_manifest_integrity"),
    ("fsm", "preflight_fsm_check"),
    ("bvs_phase_order", "preflight_bvs_phase_order"),
    ("kill_switch", "preflight_kill_switch"),
    ("previous_phase_artifacts", "preflight_previous_phase_artifacts"),
    ("drift_detection", "preflight_drift_detection"),
    ("sab", "preflight_sab_check"),
    ("tool_registry", "preflight_tool_registry"),
    ("traceability", "preflight_traceability"),
    ("fr_spec_consistency", "preflight_fr_spec_consistency"),
    ("spec_alignment", "preflight_spec_alignment"),
    ("property_spec", "preflight_property_spec"),
    ("artifact_consistency", "preflight_artifact_consistency"),
    ("reliability_lint", "preflight_reliability_lint"),
    ("config_liveness", "preflight_config_liveness"),
    ("submodule_pin_ci", "preflight_submodule_pin_ci"),
)

# preflight_* methods deliberately NOT in the automatic pipeline (reason required):
NON_PIPELINE_PREFLIGHTS: "dict[str, str]" = {
    "preflight_all": "aggregator, not a check",
    "preflight_constitution": (
        "on-demand only since 減法 T3 (2026-07-07) — still called by "
        "postflight_constitution and the standalone check-constitution CLI"
    ),
}

# postflight_* completeness declaration — NOT a call-order registry.
# _do_postflight_all has real data dependencies (success is computed from
# bvs/drift/artifact_links before it can be passed to postflight_update_state,
# and FR-approval accounting is inline logic, not a postflight_* method at
# all), so forcing a PREFLIGHT_CHECKS-style execution loop onto it would be
# the wrong abstraction. This only guarantees every postflight_* method is
# either called from _do_postflight_all or explicitly excluded here — see
# tests/test_postflight_registry.py.
POSTFLIGHT_CHECK_METHODS: "frozenset[str]" = frozenset({
    "postflight_bvs_invariants",
    "postflight_drift_check",
    "postflight_artifact_links",
    "postflight_update_state",
    "postflight_summary",
})
NON_PIPELINE_POSTFLIGHTS: "dict[str, str]" = {
    "postflight_all": "aggregator, not a check",
    "postflight_constitution": "on-demand only since 減法 T3 (2026-07-07)",
}

_PRAGMA_RE = re.compile(r"#\s*pragma:\s*no\s*cover")

# Single source of truth for what a `# pragma: no cover` annotation may
# exempt. Reused verbatim by cli/fr_cmds.py's COVERAGE-FIX prompt so the
# sub-agent is never told something is "allowed" that this audit then
# rejects (see harness-methodology issue: pragma-allowlist drift between
# TDD-GREEN/COVERAGE-FIX prompts and this audit).
PRAGMA_NO_COVER_ALLOWLIST: tuple[str, ...] = ("except BaseException",)
PRAGMA_NO_COVER_GUIDANCE = (
    "Only `except BaseException` (atomic-write cleanup) may be exempted "
    "with `# pragma: no cover`. For any other unreachable line: if it is "
    "genuinely dead code (a branch a library guarantee makes impossible, "
    "or a duplicate of an existing entry-point module), DELETE it. If it "
    "is reachable, write a unit test for it instead of excluding it."
)


def _audit_pragma_no_cover(targets: list[str]) -> list[dict]:
    """Scan source dirs for ``# pragma: no cover`` outside allowed patterns.

    Semgrep operates on AST and cannot match Python comments, so this runs
    as a separate grep-based check integrated into ``preflight_reliability_lint``.

    Allowlist: ``PRAGMA_NO_COVER_ALLOWLIST`` (only ``except BaseException``
    for atomic-write cleanup is automatically accepted).  All other pragma
    uses must be justified with a unit test — if the code path is reachable,
    write the test and remove the pragma; if it is genuinely unreachable,
    document why.

    Returns a list of finding dicts compatible with the semgrep findings
    format used by ``preflight_reliability_lint``.
    """
    findings: list[dict] = []
    for target in targets:
        for py_file in Path(target).rglob("*.py"):
            try:
                lines = py_file.read_text(encoding="utf-8").splitlines()
            except (OSError, UnicodeDecodeError):
                continue
            for i, line in enumerate(lines, 1):
                if not _PRAGMA_RE.search(line):
                    continue
                if any(allowed in line for allowed in PRAGMA_NO_COVER_ALLOWLIST):
                    continue
                findings.append({
                    "rule": "py-pragma-no-cover",
                    "file": str(py_file),
                    "line": i,
                    "severity": "WARNING",
                    "message": (
                        "Write a unit test instead of # pragma: no cover. "
                        "Only except BaseException atomic-write cleanup is exempt. "
                        "This code path is reachable via unit test — the pragma "
                        "produces synthetic coverage that hides untested code."
                    ),
                })
    return findings


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
            state = load_state(self.project_path)
        except StateCorruptError as e:
            return {"passed": False, "state": "CORRUPT",
                    "message": f"state.json is corrupt: {e}"}
        current_state = state.get("state", "UNKNOWN")
        current_phase = state.get("current_phase", 0)
        print(f"   State: {current_state}, Phase: {current_phase}")
        if current_state in ("FREEZE", "PAUSED"):
            return {"passed": False, "state": current_state, "message": f"FSM is {current_state}"}
        # current_phase == self.phase + 1 is allowed: this is the pre-push
        # hook (scripts/hooks/pre-push) retrospectively verifying the phase
        # advance-phase just closed, one phase behind the already-flipped
        # current_phase — not a request to re-enter or redo that phase's
        # work. Only two or more phases behind is a genuine
        # backwards-navigation mistake.
        if self.phase and current_phase > self.phase + 1:
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

    def preflight_submodule_pin_ci(self) -> Dict[str, Any]:
        """Round 67 站4: is the framework commit this project pins a green one?

        Every gate, every score and every block in this project is produced by
        the code at that SHA. Measured across the eight projects on this
        machine, two of them pin a harness commit whose own Framework
        Self-Tests were red — one of them the commit Round 66 pushed and had
        to correct an hour later.

        Never raises: an unreachable GitHub is reported as INFRA by
        `submodule_pin_verdict`, not converted into a pass and not allowed to
        take the run down.
        """
        print("\n[PRE-FLIGHT] Harness Submodule Pin CI Verdict")
        from core.quality_gate.submodule_pin import (
            pinned_submodule_sha, submodule_pin_verdict,
        )
        try:
            res = submodule_pin_verdict(
                self.project_path,
                pinned_sha=pinned_submodule_sha(self.project_path),
            )
        except Exception as exc:  # pragma: no cover — reporting must not stop a run
            return {"passed": True, "skipped": True, "infra": True,
                    "message": f"pin check unavailable: {type(exc).__name__}: {exc}"}
        print(f"   {res['message']}")
        return res

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
        """Check SAB constitution compliance (P3+ only — architecture baseline drift).

        Reports ``blocking`` like every other member of
        ``_DELAYED_BLOCKING_PREFLIGHTS``: a failure here fails
        ``_do_preflight_all``'s ``all_passed`` from P3 on, and
        ``preview_next_phase_blocking`` reads the key to decide whether a
        finding is a carry-over obligation. Round 43 站1 — before this, the
        key was simply absent, so `not res.get("blocking")` dropped every SAB
        finding a second time even once the check_id was spelled right.

        The delayed-blocking half is the module-existence scan below: at P3
        the implementation directories do not exist yet so it is skipped, and
        from P4 a SAB module with no file on disk is a violation. That is the
        exact "P(N) informational, P(N+1) blocking" shape the preview exists
        to surface.
        """
        print("\n[PRE-FLIGHT] SAB Constitution Check (M2+)")
        blocking = self.phase is None or self.phase >= 3
        sab_json = self._layout.methodology_dir / "SAB.json"
        if not sab_json.exists():
            if self.phase and self.phase >= 3:
                print("   WARNING: .methodology/SAB.json not found — SAB baseline missing")
                print("   Run: python3 scripts/generate_sab.py --project . [--overwrite]")
                return {"passed": False, "blocking": blocking,
                        "message": "SAB.json not found — generate from SAD.md §6"}
            else:
                print("   SAB.json not yet generated (P1/P2 — expected)")
                return {"passed": True, "skipped": True, "message": "SAB not required before P3"}

        try:
            sab = json.loads(sab_json.read_text(encoding="utf-8"))
        except Exception as e:
            return {"passed": False, "blocking": blocking,
                    "message": f"Failed to parse SAB.json: {e}"}

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
                from core.quality_gate.sab_amender import sab_module_candidate
                # Try to read pkg_dir the same way drift_detector does so that
                # SAB "taskq.cli" matches src/taskq/cli.py in src/-layout projects.
                pkg_dir = self._read_pkg_dir_for_sab()
                missing_modules = []
                for m in modules:
                    actual_m = sab_module_candidate(m)
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

        return {"passed": passed, "blocking": blocking,
                "violations": violations, "layers": len(layers)}

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
            print(f"   Attestation check error: {e}")
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
                atomic_to_dict, is_overlay_row_verified, load_overlay, merge_overlay,
            )
            overlay = load_overlay(self.project_path / "TRACEABILITY_MATRIX.overlay.yaml")
            if overlay:
                # merge_overlay expects an atomic dict (with "requirements" key),
                # not the check_traceability report dict. Convert rt → atomic first.
                merged = merge_overlay(atomic_to_dict(_rt), overlay)
                for fr_id, row in merged.get("requirements", {}).items():
                    if is_overlay_row_verified(row):
                        untested_set.discard(fr_id)
                        uncoded_set.discard(fr_id)
        except Exception as e:
            print(f"   [WARN] Overlay merge failed: {e}")

        untested_list = list(untested_set)
        uncoded_list = list(uncoded_set)
        # A project with zero FRs (pure library, no traceable requirements)
        # vacuously satisfies traceability — nothing is untested or uncoded.
        # `total > 0` used to be required, permanently blocking such projects
        # at P5+ even though there is nothing to trace. But zero FRs because
        # SAD.md itself is missing/unparseable is a scan FAILURE, not "no
        # requirements" — must not be indistinguishable from the vacuous case.
        from core.traceability.scanner import _find_sad
        sad_missing = total == 0 and _find_sad(self.project_path) is None
        complete = (
            len(untested_list) == 0 and len(uncoded_list) == 0 and not sad_missing
        )

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
        if sad_missing:
            print("   [BLOCKED] SAD.md not found — cannot confirm zero FRs is intentional")
        if untested_list:
            print(f"   Untested FRs: {', '.join(untested_list)}")
        if uncoded_list:
            print(f"   Uncoded FRs: {', '.join(uncoded_list)}")

        # PR 9's bounded auto-fix attempt used to run HERE, inside the check.
        # Round 43 站1 moved it to `repair_traceability_gap` below, called by
        # `cli/phase_cmds.py::cmd_run_phase` — the one command that asks for
        # the tree to be changed. The other two callers of this method
        # (`preview_next_phase_blocking`, which documents itself as mutating
        # nothing, and the postflight path) ask for an answer.
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

    def repair_traceability_gap(
        self, untested: "list[str]", uncoded: "list[str]",
    ) -> bool:
        """One bounded auto-fix attempt at a trace gap. Returns True if closed.

        PR 9's repair, relocated out of `preflight_traceability` by Round 43
        站1. The per-strategy allowlist lives inside `AutoFixEngine`: only
        `fix_missing_traceability` (problem_type='missing_traceability') is
        dispatched; the other four strategies still emit stubs and are not
        production-wired (see SAD.md §3.18 for why their wirings were removed).

        This writes: the engine fills FR annotations and test stubs into the
        project tree, and on success `attestation.json` is re-derived in place
        because the source tree it fingerprints has just changed. A caller
        that wants a measurement rather than a change must call
        `preflight_traceability` and stop there.
        """
        _fixed = _dispatch_trace_auto_fix(
            self.project_path, untested, uncoded, phase=self.phase,
        )
        if not _fixed:
            return False

        from core.traceability.scanner import check_traceability
        try:
            _rt2, report2 = check_traceability(self.project_path)  # noqa: F841
            # PR 13 fix: re-apply the overlay to the re-verify report so that
            # manually-VERIFIED FRs (whose status lives only in the overlay)
            # do not reappear as untested after auto-fix.
            try:
                from core.traceability.overlay import (
                    atomic_to_dict, is_overlay_row_verified,
                    load_overlay, merge_overlay,
                )
                overlay2 = load_overlay(
                    self.project_path / "TRACEABILITY_MATRIX.overlay.yaml"
                )
                if overlay2:
                    merged2 = merge_overlay(atomic_to_dict(_rt2), overlay2)
                    _overlay_untested: set = set(report2.get("untested", []))
                    _overlay_uncoded: set = set(report2.get("uncoded", []))
                    for fr_id, row in merged2.get("requirements", {}).items():
                        if is_overlay_row_verified(row):
                            _overlay_untested.discard(fr_id)
                            _overlay_uncoded.discard(fr_id)
                    report2 = dict(report2)
                    report2["untested"] = list(_overlay_untested)
                    report2["uncoded"] = list(_overlay_uncoded)
            except Exception as _overlay_err:
                print(f"   [WARN] re-verify overlay merge failed: {_overlay_err}")
            still_untested = report2.get("untested", [])
            still_uncoded = report2.get("uncoded", [])
            if still_untested or still_uncoded:
                print(f"   [auto-fix] re-verify: {len(still_untested)} "
                      f"untested, {len(still_uncoded)} uncoded remain")
                return False
            print("   [auto-fix] re-verify: all gaps closed")
            # F-2.5 fix: auto-fix modified the source tree, so
            # attestation.json is now stale. Re-derive and write it in-place
            # so the next verify-trace call (or CI step) doesn't fire on a
            # phantom mismatch. Developer still needs to `make attest`
            # (which stages) — this only refreshes the canonical file.
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
            return True
        except Exception as _post_err:
            print(f"   [auto-fix] post-fix re-verify error: {_post_err}",
                  file=sys.stderr)
            return False

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
        from core.quality_gate.parsers import SRS_SUBSECTION_PREFIX
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
                if blocking:
                    return {"passed": False, "skipped": False, 
                            "error": "TEST_SPEC.md is missing but SAD.md has FRs.",
                            "orphans": len(sad_frs), "missing_spec": True}
                else:
                    return {"passed": True, "skipped": False, 
                            "warning": "TEST_SPEC.md is missing but SAD.md has FRs.",
                            "orphans": len(sad_frs), "missing_spec": True}
            print("   Skipped: TEST_SPEC.md not present at 02-architecture/")
            return {"passed": True, "skipped": True,
                    "reason": "TEST_SPEC.md missing and no SAD FRs to track"}

        try:
            spec_text = spec_path.read_text(encoding="utf-8", errors="replace")
            # Match `### FR-XX` headings (per derive_test_cases.md convention).
            # SRS_SUBSECTION_PREFIX tolerates TOC-numbered subsections like
            # `### 2.1 FR-01` (see spec_alignment.py for the same bug class).
            spec_frs = {f"FR-{int(m):02d}" for m in
                        re.findall(r"^#{1,6}\s*" + SRS_SUBSECTION_PREFIX + r"FR-(\d+)\b",
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

    def preflight_spec_alignment(self) -> Dict[str, Any]:
        """Front-edge gate: canonical_spec (PRD) ↔ SRS.md FR-set coverage.

        Ingestion mode only (PROJECT_BRIEF.md declares canonical_spec). Catches
        requirements dropped from — or invented beyond — the canonical source,
        the one boundary neither `preflight_traceability` (SRS/SAD → downstream)
        nor `preflight_fr_spec_consistency` (SAD ↔ TEST_SPEC) checks. It
        mechanically enforces the ingestion prompt rule R-CANONICAL-INTERP-001
        that today only Agent A/B (LLM) uphold. Informational while P1 is still
        being authored (phase < 2); blocking from P2 entry. Elicitation mode has
        no ground truth → skipped (not a fake gate — genuinely N/A).
        """
        from core.quality_gate.spec_alignment import (
            check_spec_alignment,
            resolve_canonical_spec,
        )
        print("\n[PRE-FLIGHT] Spec Alignment (canonical_spec ↔ SRS)")
        project = self._layout.root
        if resolve_canonical_spec(project) is None:
            print("   Skipped: elicitation mode (no canonical_spec declared).")
            return {"passed": True, "skipped": True, "reason": "elicitation mode"}

        try:
            violations = check_spec_alignment(project)
        except Exception as e:  # noqa: BLE001 — fail-closed on any scan error
            print(f"   [BLOCKED] spec-alignment scan error: {e}")
            return {"passed": False, "blocking": True, "error": str(e)}

        errors = [v for v in violations if v.severity == "error"]
        reviews = [v for v in violations if v.severity == "info"]
        blocking = self.phase is not None and self.phase >= 2
        passed = (len(errors) == 0) or (not blocking)

        if errors:
            for v in errors:
                print(f"   {v.rule_id} {v.check_type}: {v.message}")
            if blocking:
                print(f"   [BLOCKED] Phase {self.phase}: {len(errors)} "
                      "canonical↔SRS divergence(s) — fix SRS.md before P2")
            else:
                print(f"   INFO: {len(errors)} divergence(s); not blocking at "
                      f"phase {self.phase}")
        elif reviews:
            print(f"   needs_review: {reviews[0].message}")
        else:
            print("   SRS.md covers canonical_spec")

        return {
            "passed": passed,
            "blocking": blocking,
            "errors": len(errors),
            "needs_review": len(reviews),
            "divergences": [v.rule_id for v in errors],
        }

    def preflight_property_spec(self) -> Dict[str, Any]:
        """Opt-in property-declaration gate (Direction B, lightweight).

        Only FRs that declare a TEST_SPEC `**Properties**` table are checked:
        declared invariants must be self-consistent (reused red_assertion
        engine), and from the FR's `fulfill_phase` (Round 14 B; default P4
        when the table omits the column) — once tests exist — actually
        executed by a property-based test (hypothesis / fast-check). Property
        *strength* is backed by the existing mutation_testing dimension, not
        re-scored here. Informational at P1; blocking from P2 (self-consistency)
        / fulfill_phase (execution). No declarations → skipped (opt-in, not a
        fake gate).
        """
        from core.quality_gate.property_check import (
            check_property_spec,
            parse_property_tables,
        )
        from core.utils.project_layout import ProjectLayout
        print("\n[PRE-FLIGHT] Property Declarations")
        # Round 14 B: dynamic fulfill_phase replaces the hard-coded P4 trigger.
        # Default 4 preserves back-compat for tables that omit the column.
        try:
            _spec_path = ProjectLayout(self.project_path).test_spec_path
            _props_by_fr: dict[str, list] = {}
            if _spec_path.exists():
                _props_by_fr = parse_property_tables(
                    _spec_path.read_text(encoding="utf-8", errors="replace"))
        except Exception as e:  # noqa: BLE001 — scan errors fall back to P4
            print(f"   [WARN] property-spec table parse failed: {e!r}; "
                  "defaulting fulfill_phase to 4")
            _props_by_fr = {}
        _fulfill_phases = [
            p.fulfill_phase
            for props in _props_by_fr.values()
            for p in props
            if p.fulfill_phase is not None
        ]
        _max_fulfill = max(_fulfill_phases) if _fulfill_phases else 4
        require_execution = (
            self.phase is not None and self.phase >= _max_fulfill)
        try:
            violations = check_property_spec(
                self._layout.root, require_execution=require_execution)
        except Exception as e:  # noqa: BLE001 — fail-closed on scan error
            print(f"   [BLOCKED] property-spec scan error: {e}")
            return {"passed": False, "blocking": True, "error": str(e)}

        if not violations:
            print("   No property declarations, or all consistent + executed.")
            return {"passed": True, "skipped": True,
                    "fulfill_phase": _max_fulfill}

        errors = [v for v in violations if v.severity == "error"]
        reviews = [v for v in violations if v.severity == "info"]
        blocking = self.phase is not None and self.phase >= 2
        passed = (len(errors) == 0) or (not blocking)
        for v in errors:
            print(f"   {v.rule_id} {v.check_type}: {v.message}")
        if errors and blocking:
            print(f"   [BLOCKED] Phase {self.phase}: {len(errors)} property issue(s)")
        elif errors:
            print(f"   INFO: {len(errors)} property issue(s); not blocking at "
                  f"phase {self.phase} (execution required from P{_max_fulfill})")
        elif reviews:
            print(f"   needs_review: {len(reviews)} invariant(s) not evaluable "
                  "against cases")
        return {"passed": passed, "blocking": blocking,
                "errors": len(errors), "needs_review": len(reviews),
                "divergences": [v.rule_id for v in errors],
                "fulfill_phase": _max_fulfill}

    def preflight_artifact_consistency(self) -> Dict[str, Any]:
        """Machine-catch P1/P2 artifact hallucinations (audit fix): an
        invented forward-reference filename (02-architecture/ARCHITECTURE.md when
        the P2 deliverable is SAD.md), a module/FR-NFR ownership drift between
        TRACEABILITY_MATRIX.md and SPEC_TRACKING.md, and an NFR dropped from
        ADR.md's traceability table. Decidable, no LLM. forward-refs and
        module-fr-coverage block from P2 (both only need P1 artifacts, already
        fixed by then); NFR→ADR coverage runs from P3 (ADR.md exists after P2).
        Informational at P1; fail-closed on scan error.

        Also runs check_security_design (Round 10): SAD.md §6's STRIDE-lite
        threat-model completeness. Unlike the checks above, its own phase
        gating (structural rules from P3, test-existence rule from P5) lives
        inside the check function itself rather than at this call site — it
        has two callers (this hook and cmd_check_artifact_consistency), and
        keeping the phase rules in one place means both callers can never
        disagree about when a rule activates.
        """
        from core.quality_gate.artifact_consistency import (
            check_ac_identifiers,
            check_ac_test_spec_coverage,
            check_forward_refs,
            check_module_fr_coverage,
            check_nfr_adr_coverage,
        )
        from core.quality_gate.security_design import check_security_design
        from core.quality_gate.srs_structure import check_srs_structure
        print("\n[PRE-FLIGHT] Artifact Consistency")
        try:
            violations = check_forward_refs(self._layout.root) + check_module_fr_coverage(self._layout.root)
            # Round 55 — the executor. Both AC checks have existed since Round
            # 51; their only consumer was `delivery_fingerprint.build_fingerprint`,
            # which counts them into a JSON field nothing blocks on. taskq-advance
            # carried 86 acceptance criteria no TEST_SPEC case cites through
            # eight phases, and taskq-super's `AC-N7.2` ("`08-config/SBOM.json`
            # exists") reached Gate 4 PASS with no case, no test and no file.
            #
            # Same phase rule as check_nfr_adr_coverage above, for the same
            # reason: the citation lives in TEST_SPEC.md, which Phase 2
            # produces, so demanding it earlier would be demanding it of an
            # artifact that does not exist yet.
            if self.phase is not None and self.phase >= 3:
                violations = violations + check_nfr_adr_coverage(self._layout.root)
                violations = violations + check_ac_identifiers(self._layout.root)
                violations = violations + check_ac_test_spec_coverage(self._layout.root)
            violations = violations + check_security_design(self._layout.root, phase=self.phase)
            # Round 42 站3: the SRS's machine-readable FR Block. It joins this
            # set rather than growing its own hook because the phase rule it
            # needs is the one already here — informational at P1, blocking
            # from P2, i.e. "the block exists by the time Phase 1 is over".
            # taskq-plus and taskq-api both entered P2 without one and were
            # read downstream as declaring no FR metadata; the only thing that
            # said so was a WARNING on stdout.
            violations = violations + check_srs_structure(self._layout.root)
        except Exception as e:  # noqa: BLE001 — fail-closed on scan error
            print(f"   [BLOCKED] artifact-consistency scan error: {e}")
            return {"passed": False, "blocking": True, "error": str(e)}

        errors = [v for v in violations if v.severity == "error"]
        reviews = [v for v in violations if v.severity == "info"]
        blocking = self.phase is not None and self.phase >= 2
        passed = (len(errors) == 0) or (not blocking)
        if errors:
            for v in errors:
                print(f"   {v.rule_id} {v.check_type}: {v.message}")
            if blocking:
                print(f"   [BLOCKED] Phase {self.phase}: {len(errors)} artifact issue(s)")
            else:
                print(f"   INFO: {len(errors)} artifact issue(s); not blocking at "
                      f"phase {self.phase}")
        elif reviews:
            print(f"   needs_review: {reviews[0].message}")
        else:
            print("   P1/P2 artifact references + module/FR-NFR ownership + "
                  "NFR→ADR coverage + security-design threat model consistent")
        return {"passed": passed, "blocking": blocking,
                "errors": len(errors), "needs_review": len(reviews),
                # Round 15 §3: additive — carries the per-violation detail
                # that was previously print()-only and discarded, so
                # preview_next_phase_blocking's obligation extractor can
                # surface actionable rule_id/message pairs instead of a
                # generic "artifact_consistency would block" placeholder.
                "error_details": [{"rule_id": v.rule_id, "message": v.message}
                                   for v in errors]}

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

        # ── Pragma no-cover audit ───────────────────────────────────────
        # Semgrep operates on AST and cannot match Python comments. Run a
        # separate grep-based check for # pragma: no cover and validate
        # each against the allowlist (only except BaseException atomic-write
        # cleanup is exempt).
        _pragma_findings = _audit_pragma_no_cover(targets)
        if _pragma_findings:
            # Normalise paths for display — semgrep reports relative paths
            # from the project root, so strip the project root prefix.
            _proj = str(self.project_path)
            for pf in _pragma_findings:
                _raw_path = pf["file"]
                if _raw_path.startswith(_proj):
                    pf["file"] = _raw_path[len(_proj):].lstrip("/")
            items.extend(_pragma_findings)

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
            mf = load_quality_manifest(self.project_path)
        except StateCorruptError as exc:
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
            # lenient: unreadable or non-dict state.json (state_io.py checks
            # both) degrades to {} — same "no evidence" outcome as before.
            _st = load_state(self.project_path, lenient=True)
            if _st.get("last_gate") or _st.get("last_fr"):
                evidence.append(
                    f"state.json last_gate={_st.get('last_gate')!r} "
                    f"last_fr={_st.get('last_fr')!r}")
            _md = self._layout.methodology_dir
            # Treat a file as evidence only when its contents actually
            # describe Gate 1 work. An empty placeholder file (post-reset,
            # mid-write) must not block phase entry — that's exactly the
            # "false-positive pre-flight stall" pattern that motivated this
            # hook in the first place. Bug #141: fr_progress.json with
            # `frs: {}` was being counted as evidence even though no FR has
            # been processed yet.
            def _has_real_gate1_content(raw: object) -> bool:
                """Return True iff `raw` describes real Gate 1 results.

                Acceptable shapes:
                  - dict with FR-XX keys (gate1_result.json / .gate1_scores.json)
                  - dict with non-empty "frs" sub-dict (fr_progress.json)
                  - list of FR entries
                """
                if isinstance(raw, dict):
                    if any(isinstance(k, str) and k.startswith("FR-")
                           for k in raw.keys()):
                        return True
                    frs = raw.get("frs")
                    if isinstance(frs, dict) and frs:
                        return True
                    return False
                if isinstance(raw, list):
                    return bool(raw)
                return False
            for _artifact in ("gate1_result.json", "fr_progress.json",
                              ".gate1_scores.json"):
                _p = _md / _artifact
                if not _p.exists():
                    continue
                try:
                    _raw = json.loads(_p.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    # Unparseable file → count it as evidence (corruption
                    # case the hook was written for).
                    evidence.append(_artifact)
                    continue
                if _has_real_gate1_content(_raw):
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
        """Run all pre-flight checks, driven by the PREFLIGHT_CHECKS registry."""
        print(f"\n{'='*60}\nPRE-FLIGHT: Phase {self.phase}\n{'='*60}")
        results = {key: getattr(self, method)() for key, method in PREFLIGHT_CHECKS}
        self.preflight_results = results
        all_passed = all(r.get("passed", False) for r in results.values())

        print(f"\nPRE-FLIGHT: {'PASS' if all_passed else 'FAIL'}")
        return {"all_passed": all_passed, "details": results}

    # Round 14 A: cross-phase obligation preview. The preflight_* methods all
    # pattern-match on `self.phase` to decide whether a finding is blocking;
    # by constructing a sibling PhaseHooks with `phase=next_phase` we can
    # simulate "what would block if I entered P(N+1) right now" without
    # mutating any state. Findings returned in this list are the cross-phase
    # carry-over obligations that the next session must resolve before they
    # actually trip the gate.
    def preview_next_phase_blocking(self, next_phase: int) -> list["Obligation"]:
        """Simulate preflight at next_phase; return obligations that would block.

        Runs `_do_preflight_all` under a fresh ``PhaseHooks(phase=next_phase)``
        instance and extracts findings from the preflights that use the
        "P(N) informational, P(N+1) blocking" pattern (the same 8 preflights
        catalogued in the P3→P4 push-block root-cause analysis). Other
        preflights (kill_switch, tool_registry, constitution, ...) are
        environmental / always-blocking; they are not "carry-over obligations"
        and are excluded from the returned list by design.

        stdout is suppressed during the simulation so the preview is
        idempotent and does not pollute the calling command's output.
        """
        from core.phase_topology import VALID_PHASES
        if next_phase not in VALID_PHASES:
            raise ValueError(
                f"next_phase must be in {list(VALID_PHASES)}; got {next_phase}")
        sibling = PhaseHooks(
            str(self.project_path), phase=next_phase,
            enable_kill_switch=False,
        )
        import contextlib
        import io
        with contextlib.redirect_stdout(io.StringIO()):
            sim = sibling._do_preflight_all()
        results = sim.get("details", {})
        obligations: list[Obligation] = []
        for check_id, res in results.items():
            if check_id not in _DELAYED_BLOCKING_PREFLIGHTS:
                continue
            if res.get("passed") or not res.get("blocking"):
                continue
            obligations.extend(_obligations_from_preflight(check_id, res, next_phase))
        return obligations

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
        self.monitoring_events.append({"timestamp": utc_now_iso(),
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
        self.monitoring_events.append({"timestamp": utc_now_iso(),
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
        self.monitoring_events.append({"timestamp": utc_now_iso(),
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
        self.monitoring_events.append({"timestamp": utc_now_iso(),
                                        "type": "after_rev", "fr_id": fr_id,
                                        "review_status": review_status,
                                        "agent_id": agent_id})
        print(f"\n[MONITORING] After Rev: {fr_id} review={review_status}")
        self._append_log(f"AFTER_REV: {fr_id} review={review_status}")
        for fr_item in reversed(self.fr_results):
            if fr_item.get("fr_id") == fr_id:
                fr_item.update({"rev_status": status, "review_status": review_status,
                                 "rev_confidence": confidence})
                break

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
        state = load_state(self.project_path)
        old_phase = state.get("current_phase", 0)
        if self.phase and self.phase > old_phase:
            state["current_phase"] = self.phase
            state["last_update"] = utc_now_iso()
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
        """Run all post-flight checks.

        Constitution keyword scoring no longer participates (減法 T3): it was
        the framework's highest-maintenance check (58 fix commits), gameable
        by keyword-sprinkling, and already reduced to a single dimension.
        Run it on demand via `check-constitution` when document quality needs
        a keyword audit.
        """
        print(f"\n{'='*60}\nPOST-FLIGHT: Phase {self.phase}\n{'='*60}")
        bvs_result = self.postflight_bvs_invariants()
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
            bvs_result.get("passed", True)
            and frs_ok
            and drift_result.get("passed", True)
            and artifact_links_result.get("passed", True)
        )
        state_result = self.postflight_update_state(success=success)
        summary = self.postflight_summary()
        print(f"\nPOST-FLIGHT: {'PASS' if success else 'FAIL'}")
        return {"success": success,
                "bvs_invariants": bvs_result,
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
            ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
            with open(self.log_path, "a") as f:
                f.write(f"[{ts}] {message}\n")
        except Exception as e:  # pragma: no cover
            import sys
            print(f"Warning: Failed to append to run-phase.log: {e}", file=sys.stderr)
