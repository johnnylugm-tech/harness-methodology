"""Phase lifecycle commands (plan-phase, plan-all, run-phase, pre-commit-check, advance-phase, generate-next-plan, validate-handoff, sync-harness).

Extracted verbatim from harness_cli.py (方案六); helpers moved home in
絞殺者續章 S4 — this module no longer imports harness_cli (all
dependencies are direct stdlib/core/harness imports). harness_cli still
re-exports the cmd_* names, so `from harness_cli import cmd_x` works.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from cli import _shared
from core import claude_md
from core.atomic_io import (
    FileSnapshot,
    StateTransaction,
    atomic_write_json,
    file_lock,
    state_lock_path,
)
from core.canonical_form import canonical_form
from core.quality_gate import agent_b_approvals, gate1_evidence
from core.quality_gate.ghost_detector import scan_phase_ghost_trails
from core.quality_gate.legal_artifacts import PHASE_DELIVERABLES
from core.quality_gate import spec_coverage
from core.quality_gate.spec_coverage import _parse_inventory_fallback, _parse_test_spec
from harness import tool_checks
from core.harness_config import get_timeout, get_value
from core.phase_topology import (
    ADVANCE_GATE1_CHECK_PHASES,
    ENTRY_GATE_MAP,
    EXIT_GATE_MAP,
    PER_FR_GATE1_PHASES,
    VALID_PHASES,
    phase_name,
)
from core.utils.project_layout import ProjectLayout
from core.utils.script_loader import load_harness_script
from harness.handover_generator import HandoverGenerator


def cmd_plan_phase(args: argparse.Namespace) -> int:
    """Generate phase execution plan from SRS/SAD artifacts.

    Round 5 建議2站2: replace cwd-relative `from scripts.generate_full_plan
    import …` with `load_harness_script()` — same P6-2026-07-07 bug class
    (never swept by the original P6/A1 fixes, which only covered
    phase_auditor/generate_quality_report/generate_release_notes). Behavior
    is bit-equivalent; user-facing CLI is allowed to hard-fail if the
    install is corrupted (an ImportError means scripts/ is missing, which
    is a real problem worth surfacing).
    """
    generate_full_plan = load_harness_script("generate_full_plan.py").generate_full_plan

    repo_path = Path(args.project).resolve()
    output_path = Path(args.output) if args.output else None

    print(f"\n{'='*60}\nplan-phase: Phase {args.phase} | repo={repo_path}\n{'='*60}")

    plan = generate_full_plan(args.phase, repo_path, output_path,
                              force=getattr(args, "force", False))
    if plan is None:
        print(f"\n[ERROR] Failed to generate plan for phase {args.phase}")
        return 1

    if output_path:
        print(f"\nPlan written → {output_path}  ({len(plan)} chars)")
    else:
        print(plan)
    return 0


def cmd_plan_all(args: argparse.Namespace) -> int:
    """Generate all 8 phase plans in dynamic mode at project start.

    Round 5 建議2站2: see cmd_plan_phase's docstring for the
    load_harness_script migration rationale (same call, same bug class).
    """
    generate_full_plan = load_harness_script("generate_full_plan.py").generate_full_plan

    project = Path(args.project).resolve()
    out_dir = Path(args.output_dir) if args.output_dir else project / ".methodology"

    if not (project / ".methodology").is_dir():
        print("[ERROR] .methodology/ not found. Run init-project first.")
        return 1

    _force = getattr(args, "force", False)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Guard quality_manifest.json from accidental shrink. `plan-all` only
    # regenerates phaseN_plan.md + plan_status.md; it never writes
    # quality_manifest.json. If a manifest already exists *and* is a
    # readable, valid JSON file we leave it alone — because the manifest
    # holds accumulated Gate scores and shrinking it resets pipeline
    # progress. An empty file, a directory, a broken symlink, or
    # non-JSON content all bypass this guard so plan-all can proceed
    # (the manifest is effectively absent in those cases).
    _manifest = out_dir / "quality_manifest.json"
    if _manifest.is_file():
        try:
            json.loads(_manifest.read_text(encoding="utf-8"))
            _manifest_usable = True
        except (OSError, json.JSONDecodeError):
            _manifest_usable = False
    else:
        _manifest_usable = False
    if _manifest_usable:
        print(
            f"[PRESERVE] {_manifest.name} already exists; "
            "plan-all does not touch it. Use 'harness_cli manifest --force "
            "--fr-ids ... --sad ...' to regenerate."
        )
    results = []
    for phase_num in VALID_PHASES:
        out_path = out_dir / f"phase{phase_num}_plan.md"
        plan = generate_full_plan(phase_num, project, out_path, dynamic=True, force=_force)
        status = "OK" if plan else "FAIL"
        results.append((phase_num, status, str(out_path)))
        print(f"  Phase {phase_num}: {status} → {out_path}")

    # Write plan_status.md
    status_path = out_dir / "plan_status.md"
    status_lines = [
        "# Plan Generation Status",
        "",
        f"Generated: {__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "Mode: Dynamic",
        "",
        "| Phase | Status | File |",
        "|-------|--------|------|",
    ]
    for phase_num, status, path in results:
        status_lines.append(f"| {phase_num} | {status} | {Path(path).name} |")
    status_lines.append("")
    status_path.write_text("\n".join(status_lines), encoding="utf-8")
    print(f"\nplan_status.md → {status_path}")

    failed = [p for p, s, _ in results if s == "FAIL"]
    if failed:
        print(f"[ERROR] Failed phases: {failed}")
        return 1
    return 0


def cmd_run_phase(args: argparse.Namespace) -> int:
    """OTEL span wrapper for run-phase. Business logic in _cmd_run_phase_impl."""
    try:
        from core.observability import init_tracer
        _tracer = init_tracer(Path(args.project).resolve())
    except Exception as exc:
        print(f"[WARN] run-phase: OTEL tracer init failed, proceeding without tracing: {exc}", file=sys.stderr)
        _tracer = None
    if _tracer is None:
        return _cmd_run_phase_impl(args)
    with _tracer.start_as_current_span("run_phase") as _span:
        _span.set_attribute("harness.phase", args.phase)
        _exit = _cmd_run_phase_impl(args)
        _span.set_attribute("harness.exit_code", _exit)
        _span.set_attribute("harness.blocked", _exit != 0)
        return _exit


def cmd_pre_commit_check(args: argparse.Namespace) -> int:
    """Lightweight pre-commit hook check (FSM + kill-switch only).

    Intended exclusively for git commit hooks where speed matters.
    Skips constitution (verified at advance-phase postflight), drift,
    traceability, and gap analysis — those are
    enforced by run-phase / finalize-gate.

    Do NOT use this command in pipelines or as a substitute for run-phase.
    """
    from core.phase_hooks import PhaseHooks

    project = Path(args.project).resolve()
    hooks = PhaseHooks(str(project), phase=args.phase,
                       drift_threshold=get_value(project, "drift_threshold"))

    print(f"\n{'='*60}\npre-commit-check: Phase {args.phase}\n{'='*60}")

    entry_gate = _verify_entry_gate(project, args.phase)
    if not entry_gate["passed"]:
        print(f"\n[ENTRY GATE FAILED] {entry_gate['gate']} — {entry_gate['reason']}")
        return 10
    print(f"\n[ENTRY GATE] {entry_gate['gate']}: {entry_gate['reason']}")

    pre = _run_fast_preflight(hooks)
    if not pre["all_passed"]:
        print(f"\nPRE-FLIGHT FAILED: {pre['details']}")
        return 1

    print("\n[INFO] Fast preflight passed (FSM + BVS phase order + kill-switch + trace freshness).")
    print("[INFO] Full enforcement (drift, traceability) runs at run-phase / finalize-gate.")

    print("[INFO] Skipped here: drift, traceability (run at run-phase / finalize-gate).")
    print("[INFO] Next steps:")
    return 0


def cmd_advance_phase(args: argparse.Namespace) -> int:
    """Advance to next phase: update state.json atomically.

    Calls _advance_fsm() which:
      1. Writes .methodology/state.json (current_phase = completed + 1) — the
         single source of truth read by hooks and CI.

    After FSM advance, regenerates HANDOVER.md so crash-recovery always
    reflects the current phase, then commits locally (no push — next
    milestone push will publish to origin).

    Usage:
        python harness_cli.py advance-phase --completed 3   # advances to phase 4
    """
    # Preserve CWD — if any Python code in this process changes directory
    # (e.g. os.chdir in a hook or library), restore it before returning.
    # Subprocess calls (git -C, claude -p) do NOT change the parent CWD.
    _saved_cwd = os.getcwd()
    project = Path(args.project).resolve()

    # Phase 9 (Maintenance) is a terminal steady state: work happens as
    # re-entrant CR tickets (cr-open/cr-close), never as a phase exit.
    if args.completed_phase >= 9:
        print(
            "\n[BLOCKED] advance-phase: Phase 9 (Maintenance) is a terminal "
            "steady state — there is no Phase 10.\n"
            "  Maintenance work is ticket-driven and re-entrant:\n"
            "    python3 harness_cli.py cr-open --type bug|feat --title ... --project .\n"
            "    python3 harness_cli.py cr-close --cr CR-NN --project .",
            file=sys.stderr,
        )
        return 2

    # CV-2: Validate args.completed_phase against state.json::current_phase.
    #
    # Three cases:
    #   1. current == completed  → normal advance (run prechecks, advance FSM)
    #   2. current >  completed  → re-verify (run prechecks only, no FSM change)
    #   3. current <  completed  → skip attempt — BLOCKED (prevent phase skips)
    state_path = project / ".methodology" / "state.json"
    if state_path.exists():
        try:
            # B4 (CV-2): hold the state lock for the read so a concurrent
            # advance-phase process cannot write between our read and the check.
            with file_lock(state_lock_path(project)):
                _state = json.loads(state_path.read_text(encoding="utf-8"))
            _current = int(_state.get("current_phase", 0))

            if _current and _current > args.completed_phase:
                # Re-verify mode: Phase N was already advanced past. Re-run
                # exit checks so the user can fix document quality at the
                # correct phase boundary without hacking state.json.
                # Does NOT change current_phase or write state.
                print(
                    f"\n[RE-VERIFY] Phase {args.completed_phase} already advanced "
                    f"(current_phase={_current}). Re-running exit checks…"
                )
                rc = _advance_prechecks(project, args.completed_phase)
                if rc != 0:
                    print(
                        f"\n[BLOCKED] Phase {args.completed_phase} exit checks "
                        f"failed (code={rc}). Fix issues above, then re-run:\n"
                        f"    python3 harness_cli.py advance-phase "
                        f"--completed {args.completed_phase} --project {project}"
                    )
                    return rc
                print(
                    f"\n[RE-VERIFY] Phase {args.completed_phase} exit checks "
                    f"re-verified ✓ (already at Phase {_current})"
                )
                return 0

            if _current and _current < args.completed_phase:
                # Skip attempt: agent tried to jump ahead
                print(
                    f"\n[BLOCKED] advance-phase: --completed={args.completed_phase} "
                    f"is ahead of state.json::current_phase={_current}.\n"
                    f"  This prevents accidental phase skips. To advance, use:\n"
                    f"    python3 harness_cli.py advance-phase --completed {_current} --project {project}",
                    file=sys.stderr,
                )
                return 2
            # Check phase_truth_passed for phases with exit gates
            if args.completed_phase in EXIT_GATE_MAP:
                _req_gate = EXIT_GATE_MAP[args.completed_phase]
                _passed = _state.get("phase_truth_passed")
                _last_gate = _state.get("last_gate")
                # P5-BUG-02 defense: Ensure both phase_truth_passed and the last_gate match the exit gate
                if not _passed or _last_gate != _req_gate:
                    print(
                        f"\n[BLOCKED] advance-phase: phase_truth_passed not recorded "
                        f"in state.json for Phase {args.completed_phase}.\n"
                        f"  Run: python harness_cli.py finalize-gate "
                        f"--gate {EXIT_GATE_MAP[args.completed_phase]} "
                        f"--phase {args.completed_phase} --project {project}\n"
                        f"  and ensure Phase Truth ≥ 90% before advancing.",
                        file=sys.stderr,
                    )
                    # Exit 12 = phase_truth_passed missing in state.json.
                    # Distinct from exit 11 (Phase Truth score < 90%) so pipeline
                    # automation and humans can apply the correct remediation:
                    #   11 → re-run Phase Truth until score ≥ 90%
                    #   12 → run finalize-gate for the exit gate of this phase
                    return 12
        except (ValueError, OSError, json.JSONDecodeError) as exc:
            print(
                f"  [WARN] Could not read state.json::current_phase for validation: {exc} — proceeding.",
                file=sys.stderr,
            )

    next_phase = args.completed_phase + 1

    # Look up gate/FR state from quality_manifest.json for accurate state.json
    manifest_path = project / ".methodology" / "quality_manifest.json"
    manifest = {}
    last_gate_num = None
    last_fr_id = None
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception as exc:  # pylint: disable=broad-exception-caught
            print(
                f"  [WARN] Could not read quality_manifest.json for state.json "
                f"gate/FR tracking: {exc} — proceeding with empty manifest.",
                file=sys.stderr,
            )

    gate_results = manifest.get("gate_results", {})
    for gn in (4, 3, 2, 1):
        gv = gate_results.get(f"gate{gn}")
        if isinstance(gv, dict) and gv.get("quality_complete"):
            last_gate_num = gn
            break

    gate1 = gate_results.get("gate1", {})
    if isinstance(gate1, dict):
        for fr_id in manifest.get("fr_ids", []):
            if isinstance(gate1.get(fr_id), dict) and gate1[fr_id].get("quality_complete"):
                last_fr_id = fr_id

    gate_score_str = ""
    if last_gate_num and isinstance(gate_results.get(f"gate{last_gate_num}"), dict):
        _gscore = gate_results[f"gate{last_gate_num}"].get("score", "")
        if _gscore:
            gate_score_str = f" (score={_gscore})"

    fr_done = len([f for f in manifest.get("fr_ids", [])
                   if isinstance(gate1, dict)
                   and isinstance(gate1.get(f), dict)
                   and gate1[f].get("quality_complete")])
    fr_total = len(manifest.get("fr_ids", []))

    task_bg = (f"Phase transition from Phase {args.completed_phase} to Phase {next_phase}."
               if not fr_total else
               f"Phase {args.completed_phase} complete ({fr_done}/{fr_total} FRs Gate 1 PASS). "
               f"Gate {last_gate_num}{gate_score_str}. Advancing to Phase {next_phase}.")

    status = (f"Phase {args.completed_phase} completed. Ready to begin Phase {next_phase}."
              if not fr_total else
              f"Phase {args.completed_phase}: {fr_done}/{fr_total} FRs Gate 1 PASS. "
              f"Gate {last_gate_num}{gate_score_str} — quality_complete. "
              f"Ready to begin Phase {next_phase}.")

    # ── Pre-advance checks ────────────────────────────────────────────
    rc = _advance_prechecks(project, args.completed_phase)
    if rc != 0:
        return rc

    print(f"\n[advance-phase] Completed phase {args.completed_phase} → advancing to {next_phase}")
    # B1 (split-brain fix): capture the advance write-set BEFORE anything is
    # written, so a failed handover commit can restore the pre-advance state
    # instead of leaving state.json claiming a phase git never recorded
    # (ghost state — hooks and CI read state.json::current_phase).
    # Superset of _advance_commit_targets; absent files are restored to
    # absent. .sessi-work cleanup is deliberately NOT restored: it is
    # idempotent hygiene, not phase state, and re-runs on the next attempt.
    _layout = ProjectLayout(project)
    _advance_snap = FileSnapshot([
        project / ".methodology" / "state.json",
        project / ".methodology" / "fr_progress.json",
        project / ".methodology" / "gate_timestamps.jsonl",
        project / ".methodology" / "quality_manifest.json",
        project / ".methodology" / f"phase{args.completed_phase}_plan.md",
        project / "HANDOVER.md",
        project / "CLAUDE.md",
        project / "00-summary" / f"Phase{args.completed_phase}_STAGE_PASS.md",
        _layout.config_records_path,
        _layout.release_checklist_path,
    ])
    _advance_fsm(project, args.completed_phase,
                 last_gate=last_gate_num, last_fr=last_fr_id)
    claude_md.update_claude_md(project)               # phase number just changed → refresh CLAUDE.md
    claude_md.llm_clean_stale_claude_md(project)      # remove stale manual harness status text

    # Generate CRG wiki on P3+ advance (architecture docs for agents, incremental).
    # Driven via the code-review-graph CLI so it works in any environment — the old
    # mcp_tools import only existed inside interactive Claude Code and silently no-op'd,
    # so .code-review-graph/wiki/ was never produced.
    if args.completed_phase >= 2:
        _crg_bin = shutil.which("code-review-graph")
        if _crg_bin:
            try:
                subprocess.run(
                    [_crg_bin, "wiki", "--repo", str(project)],
                    check=True, capture_output=True, text=True, timeout=get_timeout("subprocess", project),
                )
                print("  [CRG] Wiki updated → .code-review-graph/wiki/")
            except Exception as _w:  # non-blocking, but surface the reason (no silent pass)
                print(f"  [CRG] Wiki skipped: {_w}")

    # CV-13: Stale .sessi-work/ artifacts can cause the next phase's gate
    # evaluation to skip re-computation (agent sees old result JSONs and
    # assumes they are current). Clean aggressively at every phase transition.
    # Bug #H fix: preserve .sessi-work/sentinels/ — those files are the
    # gate-completion evidence consumed by the next phase's
    # validate-handoff (g1_fr01.flag etc.). Wiping them caused every
    # advance-phase to drop Gate 1 evidence, so the precondition check
    # on the next phase always reported "sentinel missing" even after
    # a successful Gate 1 finalize.
    sessi_work = project / ".sessi-work"
    sentinels_dir = sessi_work / "sentinels"
    _sentinels_backup: Optional[Path] = None
    # Bug H1 fix: wrap backup→rm→restore in try/finally so the temp dir is
    # cleaned up even if shutil.rmtree / copytree raises a non-OSError
    # (KeyboardInterrupt, RuntimeError, etc.) that ignore_errors won't swallow.
    try:
        if sentinels_dir.is_dir():
            _sentinels_backup = Path(tempfile.mkdtemp(prefix="harness-sentinels-"))
            shutil.copytree(sentinels_dir, _sentinels_backup / "sentinels")
        if sessi_work.is_dir():
            shutil.rmtree(sessi_work, ignore_errors=True)
            print(f"  [advance-phase] Cleared stale {sessi_work}")
        if _sentinels_backup is not None:
            sentinels_dir.mkdir(parents=True, exist_ok=True)
            shutil.copytree(_sentinels_backup / "sentinels", sentinels_dir, dirs_exist_ok=True)
            _n = sum(1 for _ in sentinels_dir.iterdir() if _.is_file())
            print(f"  [advance-phase] Preserved {_n} sentinel(s) under {sentinels_dir}")
    finally:
        if _sentinels_backup is not None:
            shutil.rmtree(_sentinels_backup, ignore_errors=True)

    # Fix Finding #3: auto-regenerate quality_manifest.json at P2 exit.
    #
    # P2 plan delegates to scripts/generate_sab.py (writes SAB.json only) but
    # never re-invokes `harness_cli.py manifest` to update quality_manifest.json
    # with the fresh SAD-derived data (nfr_dim_map, high_risk_modules,
    # gate_score_overrides). P3 entry checks "manifest exists" and may use the
    # stale P1 manifest, causing downstream gate checks to score against the
    # wrong dimension floors. Re-run the manifest generator here using the
    # fresh SAD.md so P3/P4/P5 phases see current data.
    #
    # Best-effort: skip with WARNING if SAD.md is missing (caller can re-run
    # `harness_cli.py manifest` manually). Surface the reason rather than
    # silent-skip — we have been bitten by silent skips before.
    _manifest_regenerated = False
    if args.completed_phase == 2:
        sad_path = ProjectLayout(project).sad_path
        if sad_path.exists():
            try:
                from harness.harness_bridge import HarnessBridge
                # Reuse fr_ids from current manifest, fall back to SRS.md scan
                _mf_path = project / ".methodology" / "quality_manifest.json"
                _fr_ids: list[str] = []
                if _mf_path.exists():
                    try:
                        _fr_ids = json.loads(
                            _mf_path.read_text(encoding="utf-8")
                        ).get("fr_ids", [])
                    except Exception as exc:  # pylint: disable=broad-exception-caught
                        print(f"  [P2→P3] quality_manifest.json unreadable, "
                              f"falling back to SRS.md FR scan: {exc}", file=sys.stderr)
                if not _fr_ids:
                    # Fallback: scan SRS.md for FR markers. Match "### FR-XX" headers
                    # (separator can be `:`, `—`, `-`, `|`, or whitespace after the
                    # number) and table rows "| FR-XX | ...". Previous regex required
                    # `\s*:|\s*|` after the digits, which silently dropped SRS files
                    # using em-dash (`### FR-01 — ...`) — leaving fr_ids empty and
                    # tripping the manifest-integrity pre-flight (Bug #140).
                    # SRS_SUBSECTION_PREFIX tolerates TOC-numbered subsections like
                    # "### 3.1 FR-01" — same bug class as spec_alignment.py /
                    # phase_hooks.py / spec_coverage.py / artifact_parsers.py; this
                    # call site was missed in that round (2026-07-14).
                    import re as _re_fr
                    from core.quality_gate.parsers import SRS_SUBSECTION_PREFIX
                    _srs = ProjectLayout(project).srs_path
                    if _srs.exists():
                        _fr_ids = [
                            f"FR-{n}" for n in _re_fr.findall(
                                r"^(?:###\s+" + SRS_SUBSECTION_PREFIX + r"FR-|\|\s*FR-)(\d+)\b",
                                _srs.read_text(encoding="utf-8"),
                                _re_fr.MULTILINE,
                            )
                        ]
                # Fail-fast guard: if both seed manifest and SRS regex
                # produce zero FRs, do NOT silently call
                # generate_quality_manifest with an empty list. An empty
                # manifest passes the regeneration print, then trips
                # preflight Pattern A in P3 — the failure surfaces far
                # from its cause. Refuse the advance locally instead so
                # the user fixes the SRS format / fr_ids injection at
                # the point of failure. (Bug #140 hardened the regex;
                # this guards the malformed-SRS case the regex now
                # correctly reports as zero matches.)
                if not _fr_ids:
                    print(
                        f"  [P2→P3] manifest regeneration REFUSED: "
                        f"fr_ids is empty (no seed in quality_manifest.json "
                        f"and no FR markers matched in SRS.md).\n"
                        f"    Fix one of:\n"
                        f"      - inject fr_ids into quality_manifest.json\n"
                        f"    (recommended: pre-populate via `harness_cli.py "
                        f"manifest --fr-ids FR-XX ... --sad {sad_path}`)\n"
                        f"      - repair SRS.md so FR headers are detectable "
                        f"by `^(?:###\\s+(?:\\d+(?:\\.\\d+)*\\.?\\s+)?FR-|"
                        f"\\|\\s*FR-)(\\d+)\\b` (subsection-numbered headings "
                        f"like `### 3.1 FR-01` are accepted)",
                        file=sys.stderr,
                    )
                    return 2
                _bridge = HarnessBridge()
                _out = _bridge.generate_quality_manifest(
                    fr_ids=_fr_ids,
                    sad_path=str(sad_path),
                    project_root=str(project),
                    force=True,
                )
                print(
                    f"  [P2→P3] quality_manifest.json regenerated → {_out} "
                    f"({len(_fr_ids)} FRs, generated_at_phase=2)"
                )
                _manifest_regenerated = True
            except Exception as _m:  # pylint: disable=broad-exception-caught
                print(
                    f"  [P2→P3] manifest regeneration FAILED: {_m}\n"
                    f"    P3 entry will use stale P1 manifest. Fix and run:\n"
                    f"    python3 harness_cli.py manifest "
                    f"--fr-ids {' '.join(_fr_ids)} --sad {sad_path}",  # type: ignore[reportPossiblyUnboundVariable]
                    file=sys.stderr,
                )
        else:
            print(
                f"  [P2→P3] {sad_path} not found — manifest regeneration skipped.\n"
                f"    P3 entry will use the existing manifest. Create SAD.md and run:\n"
                f"    python3 harness_cli.py manifest --fr-ids FR-XX [...] --sad {sad_path}",
                file=sys.stderr,
            )

    # P7→P8: deterministic baseline for CONFIG_RECORDS.md / RELEASE_CHECKLIST.md.
    # LLM agents had been authoring these from scratch and stalling in P8 (4 stalls
    # in the workflow record before this change). The deterministic generator
    # builds both files from state.json + quality_manifest.json + git state;
    # the LLM agent that runs P8 can then review and append human-only context
    # instead of re-deriving the whole structure.
    if next_phase == 8:
        try:
            from scripts.phase8_doc_gen import generate as _p8_generate
            _p8_result = _p8_generate(project)
            print(
                f"  [P7→P8] CONFIG_RECORDS.md + RELEASE_CHECKLIST.md generated → "
                f"{_p8_result['config_path'].parent}"
            )
        except Exception as _p8e:  # pylint: disable=broad-exception-caught
            print(
                f"  [P7→P8] phase8_doc_gen failed: {_p8e}\n"
                f"    P8 entry will rely on LLM generation. Investigate:\n"
                f"    python3 scripts/phase8_doc_gen.py --project {project}",
                file=sys.stderr,
            )

    gen = HandoverGenerator(project)
    gen.write(
        checkpoint_id=f"P{next_phase}-entry-{datetime.now(timezone.utc).strftime('%Y%m%d')}",
        phase=next_phase,
        task_background=task_bg,
        current_status=status,
        next_steps=[
            f"Follow SKILL.md §0.1 Phase {next_phase} entry checklist",
            f"Read the Phase {next_phase} plan and execute",
        ],
        resume_phase=next_phase,
    )

    # Commit locally (no push — next milestone push publishes to origin)
    if os.environ.get("HARNESS_NO_GIT"):
        print("[advance-phase] HARNESS_NO_GIT=1 — skipping git commit")
    else:
        # Shared pre-push attestation refresh (refresh_attestation's docstring
        # carries the "every push path is symmetric" invariant and its
        # history). Gated to completed_phase >= 3 — no code exists yet for
        # the scan before that (same threshold as _regen_traceability_views
        # below). The try only guards the import — the helper never raises.
        if args.completed_phase >= 3:
            try:
                from scripts.build_trace_attestation import refresh_attestation
                refresh_attestation(project)
            except Exception as _att_err:  # pylint: disable=broad-exception-caught
                print(f"  [WARN] attestation pre-refresh failed: {_att_err}")

        # Fix Finding #3: include regenerated quality_manifest.json in commit when
        # P2→P3 just regenerated it, so the advance commit captures the fresh data
        # atomically (state.json + manifest). Without this, the regenerated file
        # would only land in the next push, leaving a window where CI sees stale
        # manifest.
        _add_targets = _advance_commit_targets(
            args.completed_phase, next_phase, _manifest_regenerated,
            (project / ".methodology" / "fr_progress.json").exists(),
            (project / ".methodology" / "gate_timestamps.jsonl").exists(),
            (project / "00-summary" / f"Phase{args.completed_phase}_STAGE_PASS.md").exists(),
            (project / ".methodology" / f"phase{args.completed_phase}_plan.md").exists(),
            attestation_exists=(project / ".methodology" / "trace" / "attestation.json").exists(),
        )
        _commit_failure: Optional[str] = None
        add_result = subprocess.run(
            ["git", "-C", str(project), "add", *_add_targets],
            capture_output=True, text=True,
        )
        if add_result.returncode != 0:
            _commit_failure = f"git add failed — {add_result.stderr.strip()}"
        else:
            commit_result = subprocess.run(
                ["git", "-C", str(project), "commit", "-m",
                 f"handover: advance to Phase {next_phase}"],
                capture_output=True, text=True,
            )
            if commit_result.returncode == 0:
                print("[advance-phase] Committed HANDOVER.md + state.json locally.")
            elif "nothing to commit" in (commit_result.stdout + commit_result.stderr):
                print("[advance-phase] Nothing to commit (already clean).")
            else:
                _commit_failure = (
                    "git commit failed — "
                    f"{(commit_result.stdout + commit_result.stderr).strip()}"
                )
        if _commit_failure:
            # B1 (split-brain fix): the advance did NOT land in git — restore
            # the pre-advance write-set so state.json never claims a phase
            # git history doesn't record. WARN-and-continue here was the
            # ghost-state bug: hooks/CI immediately targeted the phantom
            # phase (see tests/test_advance_commit_rollback.py).
            #
            # Round 2 Station F: restore() writes with a bare os.replace (no
            # lock) — hold state_lock here so a concurrent process legitimately
            # writing state.json (e.g. push-milestone) cannot interleave with
            # the rollback. This does NOT wrap the whole advance-phase flow:
            # _advance_fsm acquires this same lock internally, and fcntl.flock
            # is not reentrant within one process across separate os.open()
            # calls — nesting here would deadlock. By this point that inner
            # lock has long been released, so this is safe.
            with file_lock(state_lock_path(project)):
                _advance_snap.restore()
                # Un-stage what our `git add` staged so the index matches the
                # restored worktree (best-effort: fails only on an unborn HEAD,
                # which methodology projects never have past init).
                reset_result = subprocess.run(
                    ["git", "-C", str(project), "reset", "-q", "--", *_add_targets],
                    capture_output=True, text=True,
                )
            if reset_result.returncode != 0:
                print(
                    f"  [WARN] git reset after rollback failed — the index may "
                    f"still show staged entries (worktree content is already "
                    f"restored): {reset_result.stderr.strip()}\n"
                    f"  Run `git status` and `git reset -- <file>` manually if "
                    f"needed.",
                    file=sys.stderr,
                )
            print(
                f"\n[BLOCKED] advance-phase: {_commit_failure}\n"
                f"  The advance was rolled back — state.json still says "
                f"Phase {args.completed_phase}.\n"
                f"  Fix the reported error (often a commit-hook rejection), "
                f"then re-run:\n"
                f"    python harness_cli.py advance-phase "
                f"--completed {args.completed_phase} --project {project}",
                file=sys.stderr,
            )
            try:
                if os.getcwd() != _saved_cwd:
                    os.chdir(_saved_cwd)
            except OSError:
                pass
            return 6  # same commit-failed exit code as run-fr-step / finalize-gate

    print(f"[advance-phase] Done — local hooks and CI now target phase {next_phase}")
    # Restore CWD if any internal Python code (hook, library) changed it.
    # Subprocess calls do NOT change the parent process CWD.
    try:
        if os.getcwd() != _saved_cwd:
            os.chdir(_saved_cwd)
            print(f"[advance-phase] CWD restored to {_saved_cwd}")
    except OSError:
        pass
    return 0


def cmd_generate_next_plan(args: argparse.Namespace) -> int:
    """
    Recovery / position reporter.

    Reports WHERE the main agent currently is in the phase plan so it can
    resume execution without re-reading the full SKILL.md.

    Output (always):
      Phase      : N (Name)
      Plan file  : path/to/phase{N}_plan.md   ← open and follow this
      Last ckpt  : CHECKPOINT-K (Gate X / FR-YY) PASS  (or "none")
      Next ckpt  : CHECKPOINT-K+1 (Gate X / ...)
      Action     : exact single command to run next

    If no plan file exists for the current phase, instructs the agent to
    generate it first.  If all checkpoints in the current phase are done,
    reports the next phase to start.
    """
    project = Path(getattr(args, "project", ".")).resolve()
    phase_hint = getattr(args, "phase", None)
    manifest_path = project / ".methodology" / "quality_manifest.json"

    W = 62
    print(f"\n{'='*W}")
    print("POSITION REPORT  (generate-next-plan)")
    print(f"{'='*W}")

    # ── Read state.json ──────────────────────────────────────────────────────
    state_path = project / ".methodology" / "state.json"
    current_phase: int = phase_hint or 3
    last_gate: int | None = None
    last_fr: str | None = None
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            current_phase = phase_hint or int(state.get("current_phase", 3))
            last_gate = state.get("last_gate")
            last_fr = state.get("last_fr")
        except Exception as exc:  # pylint: disable=broad-exception-caught
            print(f"  [WARN] generate-next-plan: state.json unreadable, "
                  f"using phase_hint/defaults: {exc}", file=sys.stderr)

    print(f"\nPhase      : {current_phase} ({phase_name(current_phase, default='?')})")

    # ── Resolve plan file ────────────────────────────────────────────────────
    plan_file = project / ".methodology" / f"phase{current_phase}_plan.md"
    if plan_file.exists():
        print(f"Plan file  : {plan_file}")
        print("             → Open this file and follow from the next checkpoint")
    else:
        print(f"Plan file  : *** NOT FOUND ***  ({plan_file})")
        print("\n[ACTION] Generate the phase plan first:")
        print(f"  python harness_cli.py plan-phase --phase {current_phase} "
              f"--project {project}")
        print(f"  python scripts/generate_full_plan.py --phase {current_phase} "
              f"--repo {project} --output {plan_file}")
        print(f"\n{'='*W}")
        return 0

    # ── Read manifest ────────────────────────────────────────────────────────
    if not manifest_path.exists():
        print("\n[WARN] quality_manifest.json not found — cannot determine checkpoints.")
        print("  Run: python harness_cli.py manifest --fr-ids FR-01 ... --sad SAD.md")
        print(f"\n{'='*W}")
        return 0

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    fr_ids: list[str] = manifest.get("fr_ids", [])
    gate_results: dict = manifest.get("gate_results", {})
    gate1_results: dict = gate_results.get("gate1", {})

    # ── Build ordered checkpoint list for current phase ──────────────────────
    # Each entry: (label, is_complete_fn)
    checkpoints: list[tuple[str, bool]] = []

    if current_phase in PER_FR_GATE1_PHASES:
        for fr_id in fr_ids:
            # Prefer state.json's last_gate/last_fr for completion signal;
            # fall back to manifest gate_results scan.
            if last_gate is not None:
                # A per-FR gate is complete if we've passed it (last_gate > 1)
                # or if it matches last_gate=1, last_fr
                done = (last_gate > 1
                        or (last_gate == 1 and last_fr is not None
                            and last_fr in fr_ids
                            and fr_ids.index(fr_id) <= fr_ids.index(last_fr)))
            else:
                fr_res = gate1_results.get(fr_id) if isinstance(gate1_results, dict) else None
                done = bool(fr_res and fr_res.get("quality_complete"))
            checkpoints.append((f"Gate 1 / {fr_id}", done))

    if current_phase in EXIT_GATE_MAP:
        gate_num = EXIT_GATE_MAP[current_phase]
        if last_gate is not None:
            done = last_gate >= gate_num
        else:
            g_res = gate_results.get(f"gate{gate_num}")
            done = bool(g_res and g_res.get("quality_complete"))
        checkpoints.append((f"Gate {gate_num} — Phase {current_phase} Exit", done))
    elif current_phase == 6:
        if last_gate is not None:
            done = last_gate >= 4
        else:
            g_res = gate_results.get("gate4")
            done = bool(g_res and g_res.get("quality_complete"))
        checkpoints.append(("Gate 4 — Full Project", done))

    # ── Find last complete and first incomplete ──────────────────────────────
    last_done_idx = -1
    for i, (_, done) in enumerate(checkpoints):
        if done:
            last_done_idx = i

    next_idx = last_done_idx + 1

    if last_done_idx < 0:
        print("Last ckpt  : (none — starting from the beginning)")
    else:
        label, _ = checkpoints[last_done_idx]
        print(f"Last ckpt  : CHECKPOINT-{last_done_idx + 1} ({label}) ✓ PASS")

    if next_idx >= len(checkpoints):
        # All done in current phase
        next_phase = current_phase + 1
        print("Next ckpt  : (all checkpoints complete in this phase)")
        if current_phase >= 1:
            print(f"\n  Phase Truth ≥ 90% (HR-11): verify before advancing to Phase {next_phase}:")
            print("    (Exits 0 on PASS, 11 if Phase Truth < 90%)")
        print(f"\n✓ Phase {current_phase} complete — start Phase {next_phase}:")
        print(f"  python harness_cli.py run-phase --phase {next_phase} "
              f"--project {project}")
        print(f"  python scripts/generate_full_plan.py --phase {next_phase} "
              f"--repo {project} --output "
              f"{project}/.methodology/phase{next_phase}_plan.md")
        print(f"\n{'='*W}")
        return 0

    next_label, _ = checkpoints[next_idx]
    print(f"Next ckpt  : CHECKPOINT-{next_idx + 1} ({next_label})")

    # ── Emit single action command ───────────────────────────────────────────
    print(f"\n[ACTION] Open plan and execute from CHECKPOINT-{next_idx + 1}:")
    print(f"  Plan: {plan_file}")

    # Also emit the run-gate command as a quick-start shortcut
    if "Gate 1 /" in next_label:
        fr_id_next = next_label.split("Gate 1 / ")[-1].strip()
        print(f"\n  Quick-start Gate 1 for {fr_id_next}:")
        print(f"  python harness_cli.py run-gate --gate 1 --phase {current_phase} "
              f"--project {project} --fr-id {fr_id_next}")
    elif "Gate" in next_label:
        m = re.search(r"Gate (\d+)", next_label)
        if m:
            g = m.group(1)
            print(f"\n  Quick-start Gate {g}:")
            print(f"  python harness_cli.py run-gate --gate {g} "
                  f"--phase {current_phase} --project {project}")

    print(f"\n{'='*W}")
    return 0


def cmd_validate_handoff(args: argparse.Namespace) -> int:
    """v2.9.1 B.1: Cross-deliverable dependency check for phase handoffs.

    Validates that the upstream phase's deliverables are present and
    well-formed before the downstream phase is launched. Used by
    workflow JS as a pre-launch precondition and by Agent B peer
    review as a structural cross-deliverable assertion.

    Usage:
        python harness_cli.py validate-handoff --from-phase 1 --project .
        python harness_cli.py validate-handoff --from-phase 2 --project .
        python harness_cli.py validate-handoff --from-phase 3 --project .

    Exit 0 = handoff OK. Exit 1 = handoff blocked (error list printed).
    """
    project = Path(args.project).resolve()
    from_phase = args.from_phase
    errors = _validate_handoff(project, from_phase)
    if not errors:
        print(f"[validate-handoff] P{from_phase} → P{from_phase + 1}: OK")
        return 0
    print(f"[validate-handoff] P{from_phase} → P{from_phase + 1}: BLOCKED")
    for e in errors:
        print(f"  • {e}")
    return 1


def cmd_sync_harness(args: argparse.Namespace) -> int:
    """J: `harness sync` — pull + commit + push harness submodule.

    One-shot replacement for the 4-step manual process:
      1. cd harness && git pull --ff-only
      2. cd .. && git add harness
      3. git commit -m "chore(harness): bump to v <sha>"
      4. git push

    Pre-condition: working tree must be clean (asserted).
    """
    from core.submodule_sync import (
        SubmoduleSyncError,
        sync_submodule,
    )
    project = Path(getattr(args, "project", "."))
    submodule = project / (args.submodule or "harness")
    push = not getattr(args, "no_push", False)

    try:
        result = sync_submodule(
            submodule,
            push=push,
            remote=getattr(args, "remote", "origin"),
            branch=getattr(args, "branch", "main"),
        )
    except SubmoduleSyncError as e:
        print(f"[sync-harness] FAILED: {e}", file=sys.stderr)
        return 19

    n = result["behind_count"]
    sha = result["short_sha"]
    if n == 0:
        print(f"[sync-harness] OK — already up-to-date ({sha})")
        return 0

    print(f"[sync-harness] OK — pulled {n} commit(s); new SHA: {sha}")
    import subprocess
    commit_msg = result["message"]
    subprocess.run(["git", "commit", "-m", commit_msg, "--", "harness"], cwd=project, check=True)
    if push:
        subprocess.run(["git", "push", "origin", "HEAD"], cwd=project, check=True)
        print(f"[sync-harness] Pushed: {commit_msg}")
    else:
        print(f"[sync-harness] (--no-push) Committed locally: {commit_msg}")
    return 0




# --- helpers moved verbatim from harness_cli.py (絞殺者續章 S4d) ---

def _trace_dirty_state(project_path: Path) -> Dict[str, Any]:
    """PR 6: mtime-based trace staleness probe — <50ms, no rglob.

    Compares `attestation.json` mtime against `SAD.md` mtime and the
    newest `tests/test_fr*.py` mtime. Returns the *first* staleness
    cause found, in this order: missing attestation, SAD newer,
    tests newer. Catches the common case where a developer edited
    code or spec but forgot to re-derive `attestation.json`. False
    negatives (edits to `core/foo.py` without FR tag changes) are
    caught by the full preflight at `run-phase` time.
    """
    trace_dir = project_path / ".methodology" / "trace"
    att_path = trace_dir / "attestation.json"

    _FIX_HINT = (
        "Fix: python3 harness_cli.py build-trace-attestation --project . --write"
    )
    if not att_path.exists():
        return {
            "passed": False,
            "reason": f"attestation.json missing — {_FIX_HINT}",
            "staler": None,
            "newer": None,
        }

    try:
        att_mtime = att_path.stat().st_mtime
    except OSError as e:
        return {"passed": False, "reason": f"attestation.json stat failed: {e}",
                "staler": None, "newer": None}

    # Phase-aware: in implementation phases (>=3), test files are expected
    # to be newer than attestation.json during TDD cycles. Attestation is
    # regenerated in ORCH-POST after GATE1, not after each TDD step.
    # Blocking on stale attestation during TDD would reject every GREEN
    # and GATE1 commit (see P3 health-check 2026-07-10: FR-02/03 commits
    # blocked by prepare-commit-msg hook). Full preflight (run-phase) still
    # enforces attestation at push time.
    current_phase = 1
    state_path = project_path / ".methodology" / "state.json"
    if state_path.exists():
        try:
            current_phase = json.loads(
                state_path.read_text(encoding="utf-8")
            ).get("current_phase", 1)
        except Exception as exc:
            print(f"[WARN] pre-commit-check: state.json unreadable, defaulting to "
                  f"phase 1 (strict traceability enforcement): {exc}", file=sys.stderr)
    strict_trace = current_phase < 3  # P1/P2: hard-block; P3+: warn-only

    # SAD.md (canonical locations)
    for sad_candidate in ("02-architecture/SAD.md", "SAD.md"):
        sad_path = project_path / sad_candidate
        if sad_path.exists():
            try:
                if sad_path.stat().st_mtime > att_mtime:
                    return {"passed": False,
                            "reason": (
                                f"{sad_candidate} newer than attestation.json — "
                                f"{_FIX_HINT}"
                            ),
                            "staler": str(sad_path.relative_to(project_path)),
                            "newer": "attestation.json"}
            except OSError:
                pass
            break

    # Newest test file (language-aware glob; test_*.py or *.test.ts etc.)
    from core.utils.lang_patterns import iter_test_files, project_language
    tests_dir = ProjectLayout(project_path).active_test_dir
    if tests_dir.is_dir():
        try:
            candidates = list(
                iter_test_files(tests_dir, project_language(project_path))
            )
        except OSError:
            candidates = []
        if candidates:
            try:
                newest_test = max(candidates,
                                  key=lambda p: p.stat().st_mtime)
                if newest_test.stat().st_mtime > att_mtime:
                    rel = str(newest_test.relative_to(project_path))
                    if strict_trace:
                        return {"passed": False,
                                "reason": (
                                    f"{rel} newer than attestation.json — "
                                    f"{_FIX_HINT}"
                                ),
                                "staler": rel, "newer": "attestation.json"}
                    else:
                        # P3+: warn but don't block commit. Test files are
                        # naturally newer than attestation during TDD cycles;
                        # attestation regenerates in ORCH-POST after GATE1.
                        # Full preflight (run-phase) still enforces at push.
                        print(
                            f"[INFO] {rel} newer than attestation.json — "
                            f"expected during TDD cycles in Phase {current_phase}, "
                            f"not blocking commit. Full preflight at push time "
                            f"will still enforce attestation.",
                            file=sys.stderr,
                        )
            except OSError:
                pass

    return {"passed": True, "reason": "trace attestation is current",
            "staler": None, "newer": None}


def _run_fast_preflight(hooks) -> dict:
    """Lightweight preflight: FSM, BVS phase order, kill-switch, trace mtime.

    Used exclusively by cmd_pre_commit_check (git commit hook path).
    Not exposed via run-phase to prevent agents from bypassing full enforcement.

    PR 6: adds `_trace_dirty_state` mtime probe (cheaper than the full
    `preflight_traceability` re-derive). Catches the common case of
    "I edited [FR-XX] but forgot to re-attest" before commit.
    """
    results = {
        "fsm": hooks.preflight_fsm_check(),
        "bvs_phase_order": hooks.preflight_bvs_phase_order(),
        "kill_switch": hooks.preflight_kill_switch(),
        "trace_dirt": _trace_dirty_state(hooks.project_path),
    }
    all_passed = all(r.get("passed", False) for r in results.values())
    return {"all_passed": all_passed, "details": results}

def _advance_commit_targets(
    completed_phase: int,
    next_phase: int,
    manifest_regenerated: bool,
    fr_progress_exists: bool,
    gate_timestamps_exists: bool = False,
    stage_pass_exists: bool = False,
    plan_exists: bool = True,
    attestation_exists: bool = False,
) -> list[str]:
    """Files the advance-phase local commit must stage.

    Uses an explicit list (not `git add -A`) so unrelated working-tree noise is
    not swept in. fr_progress.json is rewritten by _advance_fsm during this same
    advance, so it must be staged — but only when present: pre-Gate-1 advances
    (P1->P2, P2->P3) have no fr_progress.json yet, and an explicit `git add` of a
    missing pathspec fails the whole commit.

    gate_timestamps.jsonl is functional FR-gate state (read back to verify per-FR
    gate events) that the DELTA fast-path appends within a phase; the advance
    commit sweeps its tail so it does not linger unstaged after every phase bump.
    Conditional-exists for the same missing-pathspec reason as fr_progress.json.

    00-summary/Phase{N}_STAGE_PASS.md is machine-generated by _generate_stage_pass
    on every advance-phase run (always-regenerate). It is staged here too so a
    single `git add` in the advance commit covers it — even if the earlier
    conditional git-add at line ~6372 was skipped because content matched the
    already-committed bytes.

    .methodology/trace/attestation.json mirrors the refresh-before-push pattern
    push_cmds.py already applies to push-checkpoint/push-milestone ("every push
    path is symmetric") — advance-phase was the one caller that skipped it,
    landing a handover commit with a stale attestation SHA that only surfaces
    as a blocking failure at the next P5+ pre-push.
    """
    targets = [
        ".methodology/state.json", "HANDOVER.md",
        "CLAUDE.md",
    ]
    if plan_exists:
        # Same missing-pathspec hazard as fr_progress.json below: a project
        # without the pre-generated plan file made the whole `git add` fail,
        # so the advance commit NEVER landed (caught by
        # tests/test_advance_commit_rollback.py).
        targets.append(f".methodology/phase{completed_phase}_plan.md")
    if fr_progress_exists:
        targets.append(".methodology/fr_progress.json")
    if gate_timestamps_exists:
        targets.append(".methodology/gate_timestamps.jsonl")
    if manifest_regenerated:
        targets.append(".methodology/quality_manifest.json")
    if stage_pass_exists:
        targets.append(f"00-summary/Phase{completed_phase}_STAGE_PASS.md")
    if attestation_exists:
        targets.append(".methodology/trace/attestation.json")
    if next_phase == 8:
        targets += ["08-config/CONFIG_RECORDS.md", "08-config/RELEASE_CHECKLIST.md"]
    return targets

def _advance_fsm(project: Path, completed_phase: int,
                 last_gate: int | None = None,
                 last_fr: str | None = None) -> None:
    """Write state.json — the single source of truth for phase state.

    Local hooks, CI, and all harness commands read .methodology/state.json::current_phase.
    No other phase storage mechanisms exist.
    """
    from datetime import datetime, timezone
    from core.fsm.fsm import validate_fsm_state, FSMError

    next_phase = completed_phase + 1

    # 1. Prepare the full write set BEFORE anything becomes visible, then
    # publish HANDOVER.md + state.json in one StateTransaction (state.json
    # LAST — it is the authoritative file, so a partial commit can never
    # claim more progress than the artifacts on disk support). This is the
    # fix for the half-state class: the old order wrote state.json first
    # and only WARNed when HANDOVER regeneration failed afterwards, leaving
    # state advanced with a stale crash-recovery document (the P8→9 crash).
    # Cross-process locked (SG-12) so a parallel _update_state_checkpoint
    # or push-milestone state-write cannot corrupt the file.
    state_path = project / ".methodology" / "state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    with file_lock(state_lock_path(project)):
        existing_state = "INIT"
        state_data: dict = {}
        if state_path.exists():
            try:
                state_data = json.loads(state_path.read_text())
            except Exception as exc:  # pylint: disable=broad-exception-caught
                from core.degradation_ledger import record_degradation
                record_degradation(
                    project, "phase_cmds._advance_fsm",
                    "state.json unreadable — treating FSM state as fresh (INIT) "
                    "and overwriting the file's other fields",
                    why=str(exc),
                )
                state_data = {}
            else:
                try:
                    existing_state = validate_fsm_state(state_data.get("state", "INIT"))
                except FSMError as e:
                    print(f"\n  [FSM ERROR] {e}")
                    print("  Fix state.json manually or run `advance-phase` with a clean state.")
                    sys.exit(11)
        # Merge into the existing dict rather than replacing it — state.json also
        # carries fields this function doesn't own (last_push_checkpoint,
        # phase_completed, ci_readiness_ack, language, test_runner, ...); a bare
        # replacement here silently discarded them on every advance-phase call.
        state_data.update({
            "state": existing_state,
            "current_phase": next_phase,
            "last_gate": last_gate,
            "last_fr": last_fr,
            "last_update": datetime.now(timezone.utc).isoformat(),
            # P5-BUG-02: User expects phase_truth_passed to be True after advance-phase runs verify_phase_truth
            "phase_truth_passed": True,
            "last_milestone_command": f"advance-phase --completed-phase {completed_phase}",
        })

        # Render HANDOVER.md before any write — a render failure aborts the
        # advance with NOTHING published (previously it warned after state
        # was already advanced).
        gen = HandoverGenerator(project)
        handover_content = gen.render(
            checkpoint_id=f"P{next_phase}-entry-{datetime.now(timezone.utc).strftime('%Y%m%d')}",
            phase=next_phase,
            task_background=(
                f"Phase {completed_phase} completed. Advancing FSM to Phase {next_phase}."
            ),
            current_status=f"FSM advanced from Phase {completed_phase} to Phase {next_phase}.",
            next_steps=[
                f"Follow SKILL.md §0.1 Phase {next_phase} entry checklist",
                f"Read the Phase {next_phase} plan and execute",
            ],
            resume_phase=next_phase,
        )

        with StateTransaction(project) as txn:
            txn.stage_text(gen.handover_path, handover_content)
            txn.stage_json(state_path, state_data)   # authoritative file last
            txn.commit()

        # B5: Advance fr_progress.json inside the same lock so state.json and
        # fr_progress.json are always updated atomically from any reader's
        # perspective. Moving it outside created a window where another process
        # could see next_phase in state.json but the old phase in fr_progress.json.
        # SG-9: do not silently swallow exceptions — log to stderr so the
        # operator knows if state.json and fr_progress.json fall out of sync.
        # FileNotFoundError is expected for P1/P2 (no fr_progress.json yet).
        try:
            from harness.fr_progress_tracker import FRProgressTracker
            FRProgressTracker(project, phase=next_phase).advance_phase(next_phase)
        except FileNotFoundError:
            pass  # P1/P2 projects: fr_progress.json doesn't exist yet — expected.
        except Exception as exc:  # pylint: disable=broad-exception-caught
            print(
                f"  [WARN] FRProgressTracker.advance_phase failed: {type(exc).__name__}: {exc}\n"
                f"  state.json advanced to phase {next_phase}, but fr_progress.json may now\n"
                f"  be out of sync. Inspect .methodology/fr_progress.json and repair if needed.",
                file=sys.stderr,
            )
    print(f"  [FSM] state.json current_phase → {next_phase}")
    print(f"  [FSM] HANDOVER.md regenerated for Phase {next_phase}")




# --- advance/handoff cluster (moved verbatim from harness_cli.py, S4g) ---

_SUBSTRATE_PROBE_CACHE = ".sessi-work/substrate_probe_ok.json"
_SUBSTRATE_PROBE_TTL_SECONDS = 6 * 3600  # one workflow run calls run-phase 2×


def _run_substrate_probe(project: Path, phase: int) -> int:
    """Spawn-substrate preflight (Round 12 站0b). 0 = OK / cached-OK.

    On failure prints the three-surface diagnosis (which probe command was
    blocked, the effective permission_mode/setting_sources, and the agent
    output tail) and returns non-zero so run-phase FATALs before any
    per-FR dispatch loop starts. A success is cached for
    _SUBSTRATE_PROBE_TTL_SECONDS so the workflow's second run-phase call
    in the same run does not pay for a second probe.
    """
    import time as _time

    from cli.fr_cmds import _resolve_phase3_context
    from core.agent_spawner import AgentSpawner

    cache_path = project / _SUBSTRATE_PROBE_CACHE
    try:
        cached = json.loads(cache_path.read_text())
        if (cached.get("ok") is True
                and _time.time() - float(cached.get("ts", 0)) < _SUBSTRATE_PROBE_TTL_SECONDS):
            print("\n[SUBSTRATE PROBE] cached OK "
                  f"({int(_time.time() - float(cached['ts']))}s ago) — skipping")
            return 0
    except (OSError, ValueError, TypeError):
        pass

    phase_ctx = _resolve_phase3_context(project)
    pmode = get_value(project, "permission_mode")
    print("\n[SUBSTRATE PROBE] verifying spawned sub-agents can execute "
          "python3/pytest + git (≤90s) ...")
    spawner = AgentSpawner(project_path=project)
    probe = spawner.preflight_substrate(
        phase=phase,
        mcp_config=phase_ctx["mcp_config"],
        setting_sources=phase_ctx["setting_sources"] or "",
        permission_mode=pmode,
    )
    if probe["ok"]:
        print("[SUBSTRATE PROBE] OK — pytest/git/canary all executed")
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(json.dumps({"ok": True, "ts": _time.time()}))
        except OSError:
            pass
        return 0
    print(
        "\n[FATAL] run-phase: spawn-substrate probe FAILED — sub-agents "
        "dispatched in this environment cannot do pipeline work. NOT "
        "entering the per-FR loop (this exact condition wasted 140 "
        "dispatches / ~2.5h on 2026-07-16).\n"
        f"  probe spawn status : {probe['status']}\n"
        f"  python3 -m pytest  : {'OK' if probe['pytest_ok'] else 'BLOCKED/missing'}\n"
        f"  git commit --dry-run: {'OK' if probe['git_ok'] else 'BLOCKED/missing'}\n"
        f"  canary echo        : {'OK' if probe['canary_ok'] else 'BLOCKED/missing'}\n"
        f"  permission_mode    : {probe['permission_mode']}\n"
        f"  setting_sources    : {probe['setting_sources']!r}\n"
        "  Common causes: Bash tool permission wall in the spawned session "
        "(check .claude/settings.local.json allowlist — `python3 *` and "
        "`git commit -m ' *` entries cover the pipeline's command forms), "
        "an OS sandbox wrapping the nested claude CLI, or a stale/broken "
        "claude installation.\n"
        "  Agent output tail:\n    "
        + probe["detail"][-600:].replace("\n", "\n    ")
        + "\n  Re-run after fixing, or bypass once with --skip-substrate-probe.",
        file=sys.stderr,
    )
    return 24


def _cmd_run_phase_impl(args: argparse.Namespace) -> int:
    """Run preflight checks for a phase.

    Preflight scans the most recently completed phase's artifacts (via
    state.json.phase_completed) to ensure the project is ready to enter the
    target phase.  No postflight is executed here.

    Postflight coverage by command:
        - finalize-gate (gate >= 2, standalone): runs only postflight_artifact_links()
      + postflight_drift_check().  Constitution and BVS invariants are NOT
      checked on this path.
    - finalize-gate (gate 1): no postflight; constitution/BVS covered by the
    """
    from core.phase_hooks import PhaseHooks

    project = Path(args.project).resolve()
    hooks = PhaseHooks(str(project), phase=args.phase,
                       drift_threshold=get_value(project, "drift_threshold"))

    print(f"\n{'='*60}\nrun-phase: Phase {args.phase}\n{'='*60}")

    # Entry gate check (CONSTITUTION.md SS2.3)
    entry_gate = _verify_entry_gate(project, args.phase)
    if not entry_gate["passed"]:
        print(f"\n[ENTRY GATE FAILED] {entry_gate['gate']} — {entry_gate['reason']}")
        return 10
    print(f"\n[ENTRY GATE] {entry_gate['gate']}: {entry_gate['reason']}")

    pre = hooks.preflight_all()
    if not pre["all_passed"]:
        # PR 9: most preflight failures are substantive gaps that need real
        # development work or a human. The exception is the trace gap
        # (problem_type="missing_traceability"): PhaseHooks.preflight_traceability
        # dispatches _dispatch_trace_auto_fix for one bounded attempt
        # (per-strategy allowlist inside AutoFixEngine — only
        # fix_missing_traceability is wired). Other strategies (coverage,
        # drift, artifact chain) still emit stubs and are not production-wired.
        # If we reach this point, all preflights are still failing — block.
        print(f"\nPRE-FLIGHT FAILED: {pre['details']}")
        return 1

    # Required-component check (hard dependencies — incl. code-review-graph, which
    # scores the architecture dimension). Verified at every phase entry so a missing
    # component surfaces at setup, not deep inside Gate 3/4. No graceful degradation.
    _tools_ok, _missing_components = tool_checks.verify_all_gate_tools(str(project))
    if not _tools_ok:
        print(
            "\n[BLOCKED] run-phase: required components not installed:\n"
            + "\n".join(f"  - {m}" for m in _missing_components)
            + "\n  These are hard dependencies (no degradation). Install them, then re-run.\n"
            "  See SKILL.md / harness/ssi/prompts/evaluate_dimension.md for install commands."
        )
        return 1

    # ── Round 12 站0b: spawn-substrate preflight probe ───────────────────
    # Governance preflight above proves the ARTIFACTS are ready; it says
    # nothing about whether a spawned `claude -p` sub-agent can actually
    # execute pytest / git commit in this environment. The 2026-07-16 P3
    # run burned ~2.5h and 140 dispatches on FR-01 discovering it could
    # not (agents stalled on permission walls → 600s timeouts → empty
    # commits). One 90s probe here surfaces that before the per-FR loop.
    # Probe parameters mirror run-fr-step's real dispatch parameters
    # (same _resolve_phase3_context + values.permission_mode chain) so it
    # measures the substrate the pipeline will actually use.
    if args.phase in PER_FR_GATE1_PHASES and not getattr(args, "skip_substrate_probe", False):
        _probe_rc = _run_substrate_probe(project, args.phase)
        if _probe_rc != 0:
            return _probe_rc

    # Phase 3+: point to LLM-driven env check (project-aware, reads SAD.md + SRS.md).
    # preflight_all() validates governance artifacts but does not check runtime
    # dependencies (env vars, CLI tools, DB/cache connectivity, docker services)
    # that sub-agents need. Those are project-specific — Claude evaluates them
    # inline via run-env-check.
    if args.phase in PER_FR_GATE1_PHASES:
        print(f"\n[INFO] Phase {args.phase} requires environment validation. Run:")
        print(f"  python harness_cli.py run-env-check --phase {args.phase} --project {project}")
        print("  # then evaluate inline and run finalize-env-check")
        print("  # or run run-fr-step directly — _fr_step_preflight also guards each step")

    print("\n[INFO] Preflight passed. Phase execution hooks ready.")

    print("[INFO] Next steps:")
    if args.phase in PER_FR_GATE1_PHASES:
        manifest_path = project / ".methodology" / "quality_manifest.json"
        fr_ids = []
        if manifest_path.exists():
            try:
                fr_ids = json.loads(manifest_path.read_text()).get("fr_ids", [])
            except Exception as exc:
                print(f"[WARN] run-phase next-steps: quality_manifest.json unreadable, "
                      f"cannot list per-FR Gate 1 commands: {exc}", file=sys.stderr)
        if fr_ids:
            print(f"        Per-FR Gate 1 ({len(fr_ids)} FRs): {', '.join(fr_ids)}")
            for fr_id in fr_ids:
                print(f"          python harness_cli.py run-gate --gate 1 --phase {args.phase} --project {project} --fr-id {fr_id}")
        else:
            print(f"        python harness_cli.py run-gate --gate 1 --phase {args.phase} --project {project} --fr-id FR-XX")
            print("        (quality_manifest.json not found — run 'plan-phase' first to populate FR IDs)")
    return 0

def _verify_entry_gate(project: Path, phase: int) -> dict:
    """Automatically verify entry gate conditions before phase execution.

    CONSTITUTION.md SS2.3 defines:
    - P1: None
    - P2: Agent B¹ (P1) — git log APPROVE
    - P3: Agent B¹ (P2) — git log APPROVE
    - P4-P8: quality_manifest.json gate PASS
    """
    # SG-6: reject out-of-range phase early. Previously `phase <= 1` accepted
    # phase=0 and phase=-1, which is meaningless (only 1..9 exist).
    if phase not in VALID_PHASES:
        return {
            "passed": False,
            "gate": "InvalidPhase",
            "reason": f"phase={phase} is out of range 1..9",
        }
    if phase == 1:
        return {"passed": True, "gate": "None", "reason": "P1 has no entry gate"}

    if phase in (2, 3):
        prev = phase - 1
        state_path = project / ".methodology" / "state.json"
        import subprocess as sp

        # Primary: state.json phase_completed[N].sha + git merge-base --is-ancestor.
        # When state.json records a SHA, it IS the authority: a mismatched ancestry
        # means the recorded commit is no longer reachable from HEAD (branch reset,
        # force-push, etc.) and must hard-fail. We do NOT fall through to grep —
        # that would risk a false positive matching a commit message text alone.
        if state_path.exists():
            try:
                state = json.loads(state_path.read_text())
            except Exception as exc:  # pylint: disable=broad-exception-caught
                return {"passed": False, "gate": f"Human1 (P{prev})",
                        "reason": f"state.json unreadable: {exc}"}
            entry = state.get("phase_completed", {}).get(str(prev))
            if entry and entry.get("sha"):
                try:
                    r = sp.run(
                        ["git", "-C", str(project), "merge-base", "--is-ancestor",
                         entry["sha"], "HEAD"],
                        capture_output=True, text=True, timeout=10,
                    )
                except Exception as exc:  # pylint: disable=broad-exception-caught
                    return {"passed": False, "gate": f"Human1 (P{prev})",
                            "reason": f"git merge-base check failed: {exc}"}
                if r.returncode == 0:
                    return {"passed": True, "gate": f"Human1 (P{prev})",
                            "reason": f"Found human APPROVE commit for P{prev} "
                                      f"(sha={entry['sha'][:8]})"}
                # merge-base failed — check whether this is a shallow clone before
                # concluding branch reset. Shallow clones legitimately can't reach
                # older commits even when the ancestry is correct.
                try:
                    shallow = sp.run(
                        ["git", "-C", str(project), "rev-parse", "--is-shallow-repository"],
                        capture_output=True, text=True, timeout=5,
                    )
                    if shallow.returncode == 0 and shallow.stdout.strip() == "true":
                        deliverables = PHASE_DELIVERABLES.get(prev, [])
                        if deliverables:
                            passed_ab, _ = agent_b_approvals.verify_agent_b_approvals_core(
                                project, prev, deliverables
                            )
                            if passed_ab:
                                return {"passed": True, "gate": f"Human1 (P{prev})",
                                        "reason": (
                                            f"Shallow clone — git ancestry unverifiable; "
                                            f"P{prev} phase-level approvals verified via "
                                            "agent_b_approvals"
                                        )}
                            return {"passed": False, "gate": f"Human1 (P{prev})",
                                    "reason": (
                                        f"Shallow clone — git ancestry unverifiable and "
                                        f"agent_b_approvals check failed for P{prev} "
                                        "deliverables (run push-checkpoint)"
                                    )}
                except Exception as exc:  # pylint: disable=broad-exception-caught
                    print(f"[WARN] Human1 (P{prev}) gate: shallow-clone fallback check "
                          f"failed: {exc}", file=sys.stderr)
                return {"passed": False, "gate": f"Human1 (P{prev})",
                        "reason": f"phase_completed[{prev}].sha={entry['sha'][:8]} "
                                  "is not an ancestor of HEAD — branch may have been "
                                  "reset or force-pushed; re-run push-checkpoint."}

        # Fallback: git log --grep — only reached when state.json has no
        # phase_completed entry (legacy projects). Accept both old marker
        # (human-review) and new marker (review-complete) for backward compat.
        try:
            for commit_marker in (f"phase{prev}(review-complete)", f"phase{prev}(human-review)"):
                result = sp.run(
                    ["git", "-C", str(project), "log", "--oneline", "--grep", commit_marker, "-1"],
                    capture_output=True, text=True, timeout=10,
                )
                if result.stdout.strip():
                    return {"passed": True, "gate": f"Human1 (P{prev})",
                            "reason": f"Found human APPROVE commit for P{prev} (legacy grep)"}
            return {"passed": False, "gate": f"Human1 (P{prev})",
                    "reason": f"No human APPROVE commit found for P{prev}"}
        except Exception as e:
            return {"passed": False, "gate": f"Human1 (P{prev})",
                    "reason": f"Git log check failed: {e}"}

    manifest_path = project / ".methodology" / "quality_manifest.json"
    if not manifest_path.exists():
        return {"passed": False, "gate": f"Gate {ENTRY_GATE_MAP.get(phase)}",
                "reason": "quality_manifest.json not found"}

    try:
        manifest = json.loads(manifest_path.read_text())
        gates = manifest.get("gate_results", {})
        prev_gate = ENTRY_GATE_MAP.get(phase)
        if prev_gate:
            # A freshly generated manifest seeds gate2/3/4 as None (not yet run).
            # `gates.get(key, {})` returns that None, and None.get(...) raised
            # AttributeError → caught below → a return that OMITTED "gate" → the
            # caller's entry_gate['gate'] then KeyError-crashed. `or {}` makes a
            # not-yet-run gate read as a clean "not PASS".
            gate_status = gates.get(f"gate{prev_gate}") or {}
            if gate_status.get("quality_complete"):
                return {"passed": True, "gate": f"Gate {prev_gate}",
                        "reason": f"Gate {prev_gate} PASS confirmed"}
            return {"passed": False, "gate": f"Gate {prev_gate}",
                    "reason": f"Gate {prev_gate} not PASS in manifest"}
    except Exception as e:
        return {"passed": False, "gate": "Unknown", "reason": f"Manifest parse error: {e}"}

    return {"passed": False, "gate": "Unknown", "reason": f"No entry gate defined for phase {phase}"}


def _check_ghost_paper_trail(project: Path, completed_phase: int) -> int:
    """Block advance if any FR in this phase has an unresolved ghost detection.

    Ghost paper-trail records are written by ``cmd_run_fr_step`` when an agent
    self-reports completion but made zero substantive code changes (only
    whitespace, comments, or non-code files).

    Returns 0 if clear, 22 (GHOST_DETECTED) if unresolved ghosts remain.
    """
    ghost_trails = scan_phase_ghost_trails(project, completed_phase)
    if not ghost_trails:
        return 0

    # Group by FR for clear reporting.
    by_fr: dict[str, list[dict]] = {}
    for gt in ghost_trails:
        fr = gt.get("fr_id", "unknown")
        by_fr.setdefault(fr, []).append(gt)

    print(
        f"\n[BLOCKED] Ghost paper-trail detected in Phase {completed_phase}:\n"
        f"  {len(ghost_trails)} step(s) across {len(by_fr)} FR(s) reported completion\n"
        f"  but made no substantive code changes."
    )
    for fr, trails in by_fr.items():
        for t in trails:
            print(f"    {fr} / {t.get('step', '?')}: {t.get('reason', 'unknown')}")
    print(
        "\n  Re-run each flagged step with genuine code changes, then re-run advance-phase.\n"
        "  Ghost paper-trail records: .sessi-work/ghost_detected/"
    )
    return 22


def _advance_prechecks(project: Path, completed_phase: int) -> int:
    """Run pre-advance checks: Agent B approvals, gate variance, Phase Truth,
    PhaseAuditor C1-C12, TDD.

    Returns 0 if all checks pass, non-zero exit code on first failure:
      8  = C1 CRITICAL (deliverables missing / untracked)
      9  = pytest / coverage failure (P3+)
      10 = spec-coverage below phase threshold (P3+) [unified D4]
      11 = Phase Truth < 90% (P3+) or Mutation Testing failure (P3+)
      13 = Agent B approvals missing / rejected (P1/P2)
      14 = Gate 1 per-FR coverage incomplete (P3+)
      15 = Phase{N+1}_plan.md not found (generate-next-plan not run)
      16 = Constitution postflight below phase threshold (all phases)
      17 = Unresolved deferred fixes in deferred_fixes.md (P3+)
      18 = Submodule guard: harness/ has uncommitted edits that would be clobbered
      22 = Ghost paper-trail detected (agent claimed progress but made no code changes) (P3+)
    """
    # ── P1 checksum: TEST_INVENTORY.yaml baseline ────────────────────
    if completed_phase == 1:
        inventory_path = project / "TEST_INVENTORY.yaml"
        if inventory_path.exists():
            import hashlib
            _cksum = hashlib.sha256(inventory_path.read_bytes()).hexdigest()
            _state_path = project / ".methodology" / "state.json"
            try:
                with file_lock(state_lock_path(_state_path.parent.parent)):
                    _state: dict = {}
                    if _state_path.exists():
                        try:
                            _state = json.loads(_state_path.read_text(encoding="utf-8"))
                        except (json.JSONDecodeError, OSError):
                            pass
                    _state["test_inventory_checksum"] = _cksum
                    atomic_write_json(_state_path, _state)
                    print(f"  [D4] TEST_INVENTORY.yaml checksum: {_cksum[:12]}...")
            except OSError as _e:
                print(f"  [WARN] Could not write test_inventory_checksum: {_e}")

    # ── Gate score variance check ─────────────────────────────────────
    if completed_phase >= 3:
        _rc = _check_gate_score_variance(project, completed_phase)
        if _rc != 0:
            return _rc

    # ── Deferred-fix closure (P3+) — close the quality loop ────────────
    if completed_phase >= 3:
        _rc = _check_deferred_fixes_resolved(project)
        if _rc != 0:
            return _rc

    # ── Ghost paper-trail check (P3+) — detect agent self-reports with   ──
    # zero substantive code changes. Aggregate per-FR ghost detections from
    # this phase; block advance until each flagged step is re-run.
    if completed_phase >= 3:
        _rc = _check_ghost_paper_trail(project, completed_phase)
        if _rc != 0:
            return _rc

    # ── Finalize-gate sentinel check ───────────────────────────────────
    # Verify finalize-gate was actually called — prevents the agent from
    # fabricating gate{N}_result.json + quality_manifest.json directly
    # without the harness running S3/S4 cross-validation.
    _missing_finalize: list[str] = []
    # Exit gate check (phase-level): Gate 2 for P3, Gate 3 for P4, Gate 4 for P6
    if completed_phase in EXIT_GATE_MAP:
        _exit_gate = EXIT_GATE_MAP[completed_phase]
        # v2.13: pass completed_phase so the path matches what finalize-gate
        # wrote (Bug #121 — no cross-phase sentinel reuse).
        _fs = _shared._finalize_sentinel_path(project, _exit_gate, None, phase=completed_phase)
        if not _fs.exists():
            _missing_finalize.append(
                f"Gate {_exit_gate} (phase-exit) — expected {_fs.name}"
            )
    # Gate 1 per-FR check: every FR must have a finalized Gate 1 sentinel
    manifest_path = project / ".methodology" / "quality_manifest.json"
    _fr_ids_for_finalize: list[str] = []
    if manifest_path.exists():
        try:
            _fr_ids_for_finalize = json.loads(
                manifest_path.read_text(encoding="utf-8")
            ).get("fr_ids", [])
        except (json.JSONDecodeError, OSError):
            pass
    if completed_phase >= 3 and _fr_ids_for_finalize:
        _missing_fr_finalize: list[str] = []
        for _frid in _fr_ids_for_finalize:
            # v2.13: pass completed_phase so the path matches finalize-gate's
            # per-phase write (Bug #121).
            _fs = _shared._finalize_sentinel_path(project, 1, _frid, phase=completed_phase)
            if not _fs.exists():
                # DELTA auto-skip exemption: if no code changed since last Gate 1,
                # the per-FR finalize step was never called (correctly). Skip check
                # for FRs where code hasn't changed — same logic as _check_gate1_live_coverage.
                try:
                    # No phase= here on purpose: this exemption checks whether code
                    # changed since the LAST Gate 1 PASS from ANY earlier phase (DELTA
                    # carry-forward semantics) — completed_phase's own sentinel was
                    # just proven absent above, so scoping to completed_phase would
                    # always find nothing and defeat the exemption.
                    if not gate1_evidence.fr_code_changed_since_last_gate1(_frid, project):
                        continue
                except Exception as exc:
                    print(f"[WARN] advance-phase: DELTA auto-skip check for {_frid} "
                          f"failed, treating as changed (finalize still required): {exc}",
                          file=sys.stderr)
                _missing_fr_finalize.append(_frid)
                _ = None  # appease pyright
        if _missing_fr_finalize:
            _missing_finalize.append(
                f"Gate 1 per-FR ({len(_missing_fr_finalize)} FRs): "
                + ", ".join(_missing_fr_finalize[:5])
                + (f" +{len(_missing_fr_finalize)-5} more" if len(_missing_fr_finalize) > 5 else "")
            )
    if _missing_finalize:
        print(
            "\n[BLOCKED] finalize-gate not called for required gate(s):\n"
            + "".join(f"  ✗ {m}\n" for m in _missing_finalize)
            + "\n  The agent must call finalize-gate (with S3/S4 cross-validation)\n"
            + "  before advance-phase. Fabricating gate{N}_result.json or\n"
            + "  quality_manifest.json without finalize-gate is not permitted.\n"
            + "  Run: python3 harness_cli.py finalize-gate --gate <N> --phase <P> --project ."
        )
        return 17

    # ── Gate 1 per-FR coverage check (FR-loop phases only) ───────────
    if completed_phase in ADVANCE_GATE1_CHECK_PHASES:
        _rc = _check_gate1_live_coverage(project, completed_phase)
        if _rc != 0:
            return _rc

    # ── Phase Truth check (HR-11 ≥90%) ────────────────────────────────
    if completed_phase >= 3:
        try:
            from core.quality_gate.phase_truth_verifier import PhaseTruthVerifier
            verifier = PhaseTruthVerifier(str(project), completed_phase)
            truth_result = verifier.verify()
            if not truth_result["passed"]:
                score = truth_result.get("total_score", 0)
                print(f"\n[BLOCKED] Phase {completed_phase} truth = {score:.0f}% < 90% (HR-11)")
                print("  Fix gaps first, then re-run advance-phase.")
                return 11
            else:
                score = truth_result.get("total_score", 0)
                print(f"  [HR-11] Phase Truth = {score:.0f}% ≥ 90% ✓")
        except ImportError:
            print("  [WARN] PhaseTruthVerifier not available — skipping HR-11 check")
        except Exception as e:
            print(f"\n  [BLOCKED] Phase Truth check failed with unexpected error: {e}")
            print(
                "  Fix: investigate the exception above, then re-run:\n"
                f"    python harness_cli.py advance-phase --completed-phase "
                f"{completed_phase} --project {project}"
            )
            return 11


    # ── Ensure Phase{N}_STAGE_PASS.md exists before the internal Phase
    # Auditor call below — its own C2 check CRITICAL-fails when the file is
    # entirely missing (first-ever advance for this phase). This early pass
    # may write a stale quality_complete value (state.json.phase_truth_passed
    # hasn't been finalized yet) — that's fine, it only needs to exist here.
    # The authoritative content is written by the final regeneration pass
    # near the end of this function (see truth_override=True below), after
    # every blocking check (including this same Phase Auditor call) passes.
    _early_sp_path = project / "00-summary" / f"Phase{completed_phase}_STAGE_PASS.md"
    if not _early_sp_path.exists():
        _shared._generate_stage_pass(project, 4 if completed_phase >= 6 else 1, completed_phase)
        if _early_sp_path.exists():
            subprocess.run(["git", "add", str(_early_sp_path)], cwd=str(project), capture_output=True)

    # ── Always-regenerate traceability views from SSOT ───────────────
    # TRACEABILITY_MATRIX.md (and SPEC_TRACKING.md) are render-only views of the
    # live build_traceability scan — never a gate input. Refresh them here so a
    # phase advance can't leave a stale/hand-mocked matrix; staged only if
    # changed (same no-op guard as STAGE_PASS).
    #
    # Gated to completed_phase >= 3: at P1/P2, 01-requirements/TRACEABILITY_MATRIX.md
    # is the legal_artifacts.py SSOT's peer-reviewed P1 deliverable (phase1_plan.md
    # Sub-Task 3/4), not yet a "render-only view" — no code exists yet for
    # build_traceability to scan, so the regen silently replaced the approved
    # deliverable with an all-zero empty scaffold (Total Requirements: 0, every
    # SWE.3 practice FAIL) on every single P1->P2 advance. The "stale/hand-mocked
    # matrix" drift this regen guards against is a post-implementation concern
    # (matches the completed_phase >= 3 threshold already used above for
    # PhaseTruthVerifier, the first point real code exists to scan).
    if completed_phase >= 3:
        _regen_traceability_views(project)

    # ── Next-phase plan: must exist before advancing (Phase 3–7) ────
    # Prevents "advance first, plan later" ordering bugs. generate-next-plan
    # must be run BEFORE advance-phase so the agent has a plan to follow.
    # Phase 1-2 use HANDOVER.md entry flow; plan generation starts at Phase 3.
    # P8→P9 is exempt: Phase 9 (Maintenance) is ticket-driven — its plan is
    # a static playbook (phase9_plan.md, generated by plan-all), and the real
    # work plan materializes per-CR via cr-open, so no pre-advance plan gate.
    if 3 <= completed_phase < 8:
        _next_phase = completed_phase + 1
        _next_plan = project / ".methodology" / f"phase{_next_phase}_plan.md"
        if not _next_plan.exists():
            print(
                f"\n[BLOCKED] Phase{_next_phase}_plan.md not found.\n"
                f"  Run generate-next-plan BEFORE advance-phase:\n"
                f"    python3 harness_cli.py generate-next-plan --phase {_next_phase}"
                f" --project .\n"
                f"  Then re-run: python3 harness_cli.py advance-phase"
                f" --completed-phase {completed_phase} --project ."
            )
            return 15

    # ── Phase Auditor: full C1-C12 for all phases ────────────────────
    audit_rc = _shared._run_phase_auditor(project, completed_phase)
    if audit_rc != 0:
        return audit_rc

    # ── WRITE_SCOPE guard: no orphan diagnostic scripts at the repo root ──
    # Mechanism (not agent self-discipline) that keeps debug artifacts out of the
    # source tree. A workflow advance agent once stranded _diag_constitution.py here
    # while diagnosing a constitution BLOCK; BLOCK the advance until it is cleaned.
    _orphans = _scope_violation_scripts(project)
    if _orphans:
        print(
            f"\n[BLOCKED] Scope violation: {len(_orphans)} untracked diagnostic "
            f"script(s) at the repo root:"
        )
        for _o in _orphans:
            print(f"  - {_o}")
        print(
            "  Debug/diagnostic artifacts must live under .sessi-work/tmp/ "
            "(gitignored). Move or delete them, then re-run advance-phase."
        )
        return 21

    # ── Constitution keyword scoring: demoted to on-demand (減法 T3) ──────
    # The keyword-based document scorer no longer auto-gates advance-phase
    # (previously exit 16). Evidence for the demotion: 58 fix commits of
    # false-positive tuning (the highest maintenance tax of any check), it is
    # trivially gamed by keyword-sprinkling, and every phase had already been
    # reduced to the single "correctness" dimension. Document quality is
    # carried by A/B peer review + the tool-backed Gate 2/3/4 dimensions.
    # On-demand: python harness_cli.py check-constitution --phase N --project .

    # ── Agent B approvals (P1/P2/P6) — after C1 so deliverables confirmed ──
    if completed_phase in (1, 2, 6):
        deliverable_ids = PHASE_DELIVERABLES.get(completed_phase, [])
        if deliverable_ids:
            passed_ab, report_ab = agent_b_approvals.verify_agent_b_approvals_core(
                project, completed_phase, deliverable_ids
            )
            if not passed_ab:
                print(f"\n[BLOCKED] Agent B approvals incomplete for Phase {completed_phase}:")
                print(report_ab)
                print(
                    "\n  Fix: each deliverable needs "
                    ".methodology/agent_b_approvals/<id>.json "
                    "with review_status=APPROVE and "
                    "docs_embedded containing the required source documents, "
                    "then re-run advance-phase."
                )
                return 13
            print(f"  [Agent B] Phase {completed_phase} approvals verified ✓")

    # ── TDD checks: pytest + coverage, spec-coverage (P3+) ──────
    # Return code map for this block (pre-existing codes occupy 1-17):
    #   17 → finalize-gate sentinel missing (see check above)
    #   18 → ruff: lint errors in src
    #   19 → mypy: type errors in src
    #   20 → gitleaks: hardcoded secrets detected
    if completed_phase >= 3:
        # 0.1 Secrets Scanning (gitleaks)
        # Runs outside src_dir.is_dir() intentionally: gitleaks scans the whole
        # repo (docs, configs, history), not just the source tree.
        if shutil.which("gitleaks"):
            try:
                _gl_r = subprocess.run(
                    ["gitleaks", "detect", "--source", "."],
                    cwd=str(project),
                    capture_output=True,
                    text=True,
                    timeout=get_timeout("gitleaks", project),
                )
            except subprocess.TimeoutExpired:
                print("\n[BLOCKED] Secrets Scanning (gitleaks) timed out.")
                print("  Fix: re-run `gitleaks detect --source .` manually to "
                      "see where it hangs, then re-run advance-phase.")
                return 20
            if _gl_r.returncode != 0:
                print("\n[BLOCKED] Secrets Scanning (gitleaks) failure.")
                print("  Hardcoded secrets detected in the codebase/docs.")
                print("  Fix: remove the secret(s) `gitleaks detect --source .` "
                      "flagged (or add a documented allowlist entry), then re-run.")
                return 20
        else:
            print("  [WARN] gitleaks not installed. Skipping secrets scanning.")
        # Phase-based spec-coverage thresholds (unified v2.6)
        if completed_phase >= 6:
            sc_thresh = 90.0
        elif completed_phase >= 4:
            sc_thresh = 80.0
        else:
            sc_thresh = 60.0

        # 1. pytest + 100% coverage on TDD-governed source
        src_dir = ProjectLayout(project).active_src_dir
        if src_dir.is_dir():
            # 0.2 Linting (ruff)
            if shutil.which("ruff"):
                _rf_r = subprocess.run(["ruff", "check", ".", "--extend-ignore", "RUF001,RUF002,RUF003"], cwd=str(project))
                if _rf_r.returncode != 0:
                    print("\n[BLOCKED] Linting (ruff) failure.")
                    print("  Please fix the linting errors before advancing.")
                    return 18
            else:
                print("  [WARN] ruff not installed. Skipping linting.")

            # 0.3 Type Safety (mypy)
            if shutil.which("mypy"):
                _mp_r = subprocess.run([sys.executable, "-m", "mypy", ".", "--ignore-missing-imports"], cwd=str(project))
                if _mp_r.returncode != 0:
                    print("\n[BLOCKED] Type Safety (mypy) failure.")
                    print("  Please fix the type errors before advancing.")
                    return 19
            else:
                print("  [WARN] mypy not installed. Skipping type safety.")

            r = subprocess.run(
                [sys.executable, "-m", "pytest", "--tb=short", "-q",
                 "--cov=03-development/src", "--cov-fail-under=100"],
                cwd=str(project),
            )
            if r.returncode != 0:
                print("\n[BLOCKED] TDD test/coverage failure.")
                print("  Fix: 100% coverage on 03-development/src required.")
                print("  For genuinely untestable lines add: # pragma: no cover")
                # P3-A: Python < 3.11 async coverage hint
                if sys.version_info < (3, 11):  # type: ignore[reportUnreachable]
                    print(
                        f"  [Python {sys.version_info.major}.{sys.version_info.minor} note] "
                        "async function bodies called via asyncio.run() may not be tracked."
                    )
                    print("  Add '# pragma: no cover' to the 'async def' line to exclude it.")
                return 9

        # 2. D4 traceability: TEST_SPEC.md → tests/ (spec-coverage — unified)
        #    TEST_SPEC.md is the single source of truth (v2.6).
        sc_rc, sc_pct = spec_coverage._run_spec_coverage_check(project, sc_thresh, verbose=True)
        if sc_rc != 0:
            print(f"\n[BLOCKED] spec-coverage {sc_pct:.1f}% < threshold {sc_thresh:.0f}%.")
            print("  Fix: implement missing test cases from TEST_SPEC.md in tests/, then re-run.")
            return 10

    # ── P2-A: SAB consistency pre-check (MEDIUM violations block advance) ────
    # Catches "architecture declared file X but not in codebase" before git push
    # fails.  Gives an actionable message + the specific missing files.
    if completed_phase >= 3:
        try:
            from detection.drift_detector import DriftDetector
            _dd = DriftDetector(str(project))
            _sab_result = _dd.detect_sab_drift()
            _sab_medium = [
                _item for _item in _sab_result.drift_items
                if _item.severity.value in ("MEDIUM", "HIGH", "CRITICAL")
                and _item.actual == "not found"
            ]
            if _sab_medium:
                print(
                    f"\n[BLOCKED] SAB architecture violations — "
                    f"{len(_sab_medium)} declared file(s) missing from codebase:"
                )
                for _item in _sab_medium:
                    print(f"  [{_item.location}] expected: {_item.expected}")
                    print("    → Create the file OR remove its declaration from SAD.md")
                return 12
        except ImportError:
            print("  [WARN] DriftDetector not available — skipping SAB pre-advance check")
        except Exception as _sab_err:  # pylint: disable=broad-exception-caught
            print(f"  [WARN] SAB pre-advance check error: {_sab_err}")

    # ── P3-B: Phase 4+ integration package advisory (non-blocking) ───────────
    if completed_phase >= 3:
        _missing_pkgs = []
        for _pkg in ("fastapi", "httpx"):
            try:
                __import__(_pkg)
            except ImportError:
                _missing_pkgs.append(_pkg)
        if _missing_pkgs:
            print(
                f"\n[WARN] Phase {completed_phase + 1} integration packages not installed: "
                f"{', '.join(_missing_pkgs)}"
            )
            print(f"  Install: pip install {' '.join(_missing_pkgs)}")
            print("  (Non-blocking — integration tests will fail without these)")

    # ── Submodule guard (improvement E2) ───────────────────────────────
    # Detect uncommitted edits in harness/ submodule before `git submodule
    # update --remote` would silently clobber them. Hard-fail (exit 18) on
    # unsafe state. Silent skip when path is not a submodule (project-side
    # harness CLI uses pre_flight.check_submodule_safety directly).
    from core.pre_flight import check_submodule_safety
    _sub_safe, _sub_diag = check_submodule_safety(project / "harness")
    if not _sub_safe:
        print(f"\n[BLOCKED] {_sub_diag}")
        print("  Fix: commit or stash the uncommitted harness/ submodule changes "
              "above (do NOT run `git submodule update --remote` while they're "
              "pending — it would silently clobber them), then re-run advance-phase.")
        return 18

    # ── Submodule drift advisory (non-blocking) ──────────────────────
    _check_submodule_drift(project)

    # ── Always-regenerate Phase{N}_STAGE_PASS.md ─────────────────────
    # The file is machine-generated from quality_manifest.json + state.json (no LLM).
    # Always regenerate (not just when missing) so a previously-committed stale
    # artifact (e.g. pre-d8fccea "always FAIL" content from older _generate_stage_pass
    # logic) gets refreshed on every advance-phase run. Stage the file only if
    # its content actually changed — avoids empty no-op commits when the logic
    # already produced the right bytes.
    #
    # Placement (B-2026-07-13 fix): this block runs LAST in _advance_prechecks,
    # after every blocking check (Agent B approvals, TDD/coverage, SAB drift,
    # WRITE_SCOPE, submodule safety, ...) has already passed. Reaching this
    # point means _advance_prechecks is about to return 0 (success), so for
    # phases with no gate_data yet (P1-P2's empty-gate-data fallback in
    # _generate_stage_pass) we can pass truth_override=True instead of reading
    # state.json.phase_truth_passed — which _advance_fsm() does not set to
    # True until AFTER this function returns, so reading it here would always
    # see the stale pre-advance value. Previously this block ran immediately
    # after the HR-11 Phase Truth check (before Agent B approvals and other
    # blocking checks even ran), permanently baking quality_complete=False
    # into every first-ever Phase 1/2 STAGE_PASS.md.
    _stage_pass_path = project / "00-summary" / f"Phase{completed_phase}_STAGE_PASS.md"
    _sp_gate = 4 if completed_phase >= 6 else 1
    _existing_bytes_hash: int | None = None
    if _stage_pass_path.exists():
        try:
            _existing_bytes_hash = hash(_stage_pass_path.read_bytes())
        except OSError:
            pass
    print(
        f"  [advance-phase] Regenerating Phase{completed_phase}_STAGE_PASS.md "
        f"from quality_manifest (gate {_sp_gate})"
    )
    _shared._generate_stage_pass(project, _sp_gate, completed_phase, truth_override=True)
    # Stage only if content changed — avoids touching git index when nothing
    # actually differs from what is already committed.
    if _stage_pass_path.exists():
        try:
            _new_bytes_hash = hash(_stage_pass_path.read_bytes())
        except OSError:
            _new_bytes_hash = None
        if _new_bytes_hash != _existing_bytes_hash:
            subprocess.run(
                ["git", "add", str(_stage_pass_path)],
                cwd=str(project), capture_output=True,
            )
            print(
                f"  [STAGE_PASS] content changed → staged {completed_phase} advance commit"
            )

    return 0


def _validate_handoff_p1_to_p2(project: Path) -> list[str]:
    """P1→P2: TEST_INVENTORY.yaml must exist, be non-empty, and cover all FRs."""
    errors: list[str] = []
    # NOTE: TEST_INVENTORY.yaml lives at project root per harness design
    # (D4 spec-coverage fallback, init-project template). This B.1 check originally
    # looked at 01-requirements/ — inconsistent with the rest of harness,
    # and silently blocked every fresh project's P2 entry (Bug
    # discovered 2026-06-17, integration-test E2E).
    inv_path = project / "TEST_INVENTORY.yaml"
    if not inv_path.exists():
        return [
            "TEST_INVENTORY.yaml missing at project root. "
            "P1 Sub-Task 4/4 in the plan template produces this file. "
            "Re-run the Phase 1 orchestrator or invoke the inventory skill manually."
        ]
    text = inv_path.read_text(encoding="utf-8", errors="replace").strip()
    if not text:
        return [
            "TEST_INVENTORY.yaml is empty. P1 orchestrator produced a stub. "
            "Re-run Phase 1 with explicit --fr-tests populated."
        ]

    # Parse and check coverage
    try:
        import yaml
        inventory = yaml.safe_load(text)
    except ImportError:
        inventory = _parse_inventory_fallback(text)
    except Exception as e:  # pylint: disable=broad-exception-caught
        return [f"TEST_INVENTORY.yaml is unparseable: {e}"]

    if not inventory.get("fr_tests") and not inventory.get("cross_cutting"):
        errors.append(
            "TEST_INVENTORY.yaml has neither `fr_tests:` nor `cross_cutting:` "
            "sections. At minimum the P1 naming authority must declare per-FR test names."
        )

    # Check that every FR in SRS has at least one test name in inventory
    srs_path = ProjectLayout(project).srs_path
    if srs_path.exists():
        srs_text = srs_path.read_text(encoding="utf-8", errors="replace")
        declared_frs = set(re.findall(r"\bFR-\d+\b", srs_text))
        covered_frs: set[str] = set()
        fr_tests = inventory.get("fr_tests") or {}
        for fr_id, names in fr_tests.items():
            if names:  # non-empty list of test names
                # I: use canonical_form() — handles all variants
                try:
                    norm = canonical_form(fr_id)
                except ValueError:
                    continue
                if norm in declared_frs:
                    covered_frs.add(norm)
        missing_frs = declared_frs - covered_frs
        if missing_frs:
            errors.append(
                f"TEST_INVENTORY.yaml missing test names for {len(missing_frs)} "
                f"FR(s) declared in SRS.md: {', '.join(sorted(missing_frs))}. "
                f"P1 deliverable must name at least one test per FR."
            )

    return errors

def _validate_handoff_p2_to_p3(project: Path) -> list[str]:
    """P2→P3: TEST_SPEC.md must contain parseable named test cases (table format)."""
    errors: list[str] = []
    spec_path = ProjectLayout(project).test_spec_path
    if not spec_path.exists():
        return [
            "TEST_SPEC.md missing at 02-architecture/TEST_SPEC.md. "
            "P2 Sub-Task 3/3 produces this file via the derive_test_cases.md skill. "
            "Re-run Phase 2 orchestrator with explicit skill invocation."
        ]
    items = _parse_test_spec(spec_path)
    if not items:
        # 0 cases may be legitimate (genuinely empty) or wrong-shape. Distinguish.
        _code, _ = spec_coverage._run_spec_coverage_check(
            project, threshold=60.0, fr_id=None, verbose=False
        )
        if _code == 1:
            errors.append(
                "TEST_SPEC.md has 0 parseable test cases but FRs are defined. "
                "The file is likely the wrong shape (prose strategy doc instead "
                "of the derive_test_cases.md table). Re-run the skill in Phase 2."
            )
        # else: spec-coverage returned 0 (vacuous OK because no FRs); pass.
    return errors

def _validate_handoff_p3_to_p4(project: Path) -> list[str]:
    """P3→P4: every FR must have a per-FR Gate 1 sentinel.

    Same precondition as push-milestone --type p3-post-gate2, but fr_ids
    is auto-resolved from the manifest if not provided.
    """
    fr_ids = _resolve_fr_ids_from_manifest(project)
    if not fr_ids:
        return [
            "Could not resolve FR IDs from .methodology/quality_manifest.json "
            "or --fr-ids. Cannot verify per-FR Gate 1 sentinels."
        ]
    return _shared._validate_p3_post_gate2_precondition(project, fr_ids)

def _validate_handoff_p4_to_p5(project: Path) -> list[str]:
    """P4→P5: TEST_RESULTS.md (P4's deliverable) must exist with non-trivial content,
    AND Gate 3 must be PASS in quality_manifest.json.

    Bug fix (harness-methodology handoff-loop): the previous implementation
    required `05-verification/VERIFICATION_REPORT.md` here, but that file is
    *produced by Phase 5* — checking it on P4→P5 handoff is a chicken-and-egg
    that blocks every fresh Phase 5 entry. Aligned with the other handoff
    validators (P1→P2 checks P1's SRS, P2→P3 checks P2's TEST_SPEC, etc.):
    verify the *upstream* phase's deliverable, not the downstream one.

    VERIFICATION_REPORT.md existence is still asserted by `_validate_handoff_p5_to_p6`
    below, which is the correct handoff boundary for that file.
    """
    errors: list[str] = []
    results_path = ProjectLayout(project).test_results_path
    if not results_path.exists():
        return [
            "TEST_RESULTS.md missing at 04-testing/TEST_RESULTS.md. "
            "Phase 4 produces this file. Re-run Phase 4 orchestrator."
        ]
    text = results_path.read_text(encoding="utf-8", errors="replace").strip()
    if len(text) < 200:
        errors.append(
            f"TEST_RESULTS.md is suspiciously short ({len(text)} chars). "
            f"Real test results are ≥ 1KB. Possible stub."
        )
    # Gate 3 PASS precondition: verified via quality_manifest.json (written by P4
    # workflow). Mirrors the entry-gate check at _verify_entry_gate
    # (harness_cli.py:1553): the manifest's top-level key is `gate_results`
    # (not `gates`), and the field that signals completion is
    # `quality_complete` (not `status`).
    manifest_path = project / ".methodology" / "quality_manifest.json"
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            gate_results = manifest.get("gate_results") or {}
            gate3 = gate_results.get("gate3") or {}
            if not gate3.get("quality_complete"):
                errors.append(
                    "Gate 3 not PASS in .methodology/quality_manifest.json "
                    "(gate_results.gate3.quality_complete is not True). "
                    "Re-run Phase 4 Gate 3 evaluation."
                )
        except (json.JSONDecodeError, OSError):
            pass  # unparseable manifest is a separate concern; don't double-fail here
    return errors

def _validate_handoff_p5_to_p6(project: Path) -> list[str]:
    """P5→P6: VERIFICATION_REPORT.md must exist (aligned with plan text)."""
    errors: list[str] = []
    report = ProjectLayout(project).verification_report_path
    if not report.exists() and not (project / "VERIFICATION_REPORT.md").exists():
        return [
            "VERIFICATION_REPORT.md missing at 05-verification/VERIFICATION_REPORT.md (or VERIFICATION_REPORT.md). "
            "Phase 5 produces this file via the verify methodology."
        ]
    return errors

def _validate_handoff_p6_to_p7(project: Path) -> list[str]:
    """P6→P7: QUALITY_REPORT.md, RELEASE_NOTES.md, FINAL_SIGN_OFF.md must exist
    (same artifacts P6 dispatch review covers; also gate4 quality_complete must be True)."""
    errors: list[str] = []
    q6 = ProjectLayout(project).phase6_quality_dir
    for name in ("QUALITY_REPORT.md", "RELEASE_NOTES.md", "FINAL_SIGN_OFF.md"):
        if not (q6 / name).exists() and not (project / name).exists():
            errors.append(f"{name} missing at 06-quality/{name} (or root). Phase 6 produces this file.")
            
    manifest_path = project / ".methodology" / "quality_manifest.json"
    if manifest_path.exists():
        try:
            import json as _json
            manifest = _json.loads(manifest_path.read_text(encoding="utf-8"))
            gate_results = manifest.get("gate_results") or {}
            gate4 = gate_results.get("gate4") or {}
            if not gate4.get("quality_complete"):
                errors.append(
                    "Gate 4 not PASS in .methodology/quality_manifest.json "
                    "(gate_results.gate4.quality_complete is not True). "
                    "Re-run Phase 6 Gate 4 evaluation."
                )
        except Exception as exc:  # pylint: disable=broad-exception-caught
            print(f"[WARN] P6→P7 handoff: quality_manifest.json malformed "
                  f"(not blocking handoff — separate concern): {exc}", file=sys.stderr)
    else:
        errors.append("quality_manifest.json missing; run `finalize-gate --gate 4 --phase 6` first.")
    return errors

def _validate_handoff_p7_to_p8(project: Path) -> list[str]:
    """P7→P8: risk register deliverables must exist (07-risk/RISK_REGISTER.md,
    RISK_MITIGATION_PLANS.md, RISK_STATUS_REPORT.md)."""
    errors: list[str] = []
    q7 = ProjectLayout(project).phase7_risk_dir
    for name in ("RISK_REGISTER.md", "RISK_MITIGATION_PLANS.md", "RISK_STATUS_REPORT.md"):
        if not (q7 / name).exists():
            errors.append(f"{name} missing at 07-risk/{name}. Phase 7 produces this file.")
    return errors

def _validate_handoff_p8_to_p9(project: Path) -> list[str]:
    """P8→P9: config records + release checklist must exist, and the
    .methodology-archive/ release snapshot must be populated (P8 milestone
    prerequisite) before entering maintenance."""
    errors: list[str] = []
    q8 = ProjectLayout(project).phase8_config_dir
    for name in ("CONFIG_RECORDS.md", "RELEASE_CHECKLIST.md"):
        if not (q8 / name).exists():
            errors.append(f"{name} missing at 08-config/{name}. Phase 8 produces this file.")
    archive_dir = project / ".methodology-archive"
    if not archive_dir.is_dir() or not any(archive_dir.iterdir()):
        errors.append(
            ".methodology-archive/ missing or empty — the P8 release snapshot "
            "must exist before entering maintenance (push-milestone --type p8 validates it)."
        )
    return errors


_HANDOFF_VALIDATORS = {
    1: _validate_handoff_p1_to_p2,
    2: _validate_handoff_p2_to_p3,
    3: _validate_handoff_p3_to_p4,
    4: _validate_handoff_p4_to_p5,
    5: _validate_handoff_p5_to_p6,
    6: _validate_handoff_p6_to_p7,
    7: _validate_handoff_p7_to_p8,
    8: _validate_handoff_p8_to_p9,
}


def _validate_handoff(project: Path, from_phase: int) -> list[str]:
    """Dispatch to the right per-transition validator.

    Args:
        project:    project root
        from_phase: phase number that just completed (1..8). P8→P9 checks
                    the release snapshot before entering maintenance; P9
                    itself never hands off (terminal steady state).

    Returns:
        list of error strings (empty = handoff OK).
    """
    if from_phase not in _HANDOFF_VALIDATORS:
        return [
            f"No handoff validator for from-phase={from_phase}. "
            f"Supported: {sorted(_HANDOFF_VALIDATORS.keys())}."
        ]
    return _HANDOFF_VALIDATORS[from_phase](project)

def _resolve_fr_ids_from_manifest(project: Path) -> list[str]:
    """Resolve FR IDs from .methodology/quality_manifest.json (fr_ids field)."""
    manifest_path = project / ".methodology" / "quality_manifest.json"
    if not manifest_path.exists():
        return []
    try:
        _mf = json.loads(manifest_path.read_text(encoding="utf-8"))
        return list(_mf.get("fr_ids") or [])
    except (json.JSONDecodeError, OSError):
        return []

def _check_deferred_fixes_resolved(project: Path) -> int:
    """Hard-block advance if deferred_fixes.md has unresolved items (Stage 5).

    Deferred fixes are escape-hatch debt from the CASE PLATEAU protocol — they
    close the quality loop only if they are actually resolved before leaving the
    phase (the audit found they were created but never enforced). Items are
    marked '- [ ]' (open) / '- [x]' (resolved); any open item blocks advance.
    Legacy free-text files with no checkboxes are treated as resolved
    (backward-compatible).

    Returns 0 if clear, 17 if unresolved deferred items remain.
    """
    dpath = project / ".methodology" / "deferred_fixes.md"
    if not dpath.exists():
        return 0
    try:
        content = dpath.read_text(encoding="utf-8")
    except OSError:
        return 0
    open_items = re.findall(r"^\s*-\s*\[ \]\s*(.+)$", content, re.MULTILINE)
    if open_items:
        print(f"\n[BLOCKED] {len(open_items)} unresolved deferred fix(es) in "
              ".methodology/deferred_fixes.md:")
        for _it in open_items[:10]:
            print(f"    - [ ] {_it.strip()}")
        print("  Resolve each item, then mark it '- [x]' (with evidence) before advancing.")
        return 17
    return 0

def _check_gate1_live_coverage(project: Path, completed_phase: int) -> int:
    """Verify Gate 1 coverage by running pytest --cov right now.

    Replaces the old gate_timestamps.jsonl-only check: a sentinel existing
    in the jsonl does NOT prove the code actually passes coverage today
    (the file is append-only and the manifest's ``gate_results.gate1[fr]``
    record is agent-writable). This function runs pytest per FR, scoped to
    the FR's own test + tagged source files, and verifies the live coverage
    meets ``min_coverage`` from the manifest.

    Returns:
        0  — all FRs pass live coverage (or manifest absent → non-FR project)
        14 — one or more FRs missing, failing, or below min_coverage
    """
    manifest_path = project / ".methodology" / "quality_manifest.json"
    manifest: dict = {}
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    fr_ids_manifest: list[str] = manifest.get("fr_ids", [])
    if not fr_ids_manifest:
        return 0  # Non-FR project or unreadable manifest — skip

    # Read min_coverage from manifest, default 80.0 (matches _check_fr_test_step).
    _min_cov = float(
        manifest.get("quality_targets", {}).get("min_coverage", 80.0)
    )

    # DELTA-phase auto-skip: P4/P5/P7/P8 re-run Gate 1 as a delta check. When
    # NO FR's code has changed since its last Gate 1 PASS, the per-FR DELTA
    # loop is a no-op (every run-fr-step would `already done → skip`). In
    # that case trust the prior finalize-gate record — re-running pytest
    # 8 times per advance would be wasted work. Code changes (test additions
    # included) force a fresh live check.
    if completed_phase in (4, 5, 7, 8):
        try:
            _all_unchanged = all(
                not gate1_evidence.fr_code_changed_since_last_gate1(fr, project)
                for fr in fr_ids_manifest
            )
        except Exception as exc:  # pylint: disable=broad-exception-caught
            print(f"[WARN] advance-phase Gate 1 coverage: DELTA-unchanged check failed, "
                  f"forcing a full live coverage run: {exc}", file=sys.stderr)
            _all_unchanged = False
        if _all_unchanged:
            print(
                f"  [Gate 1 coverage] Phase {completed_phase}: all {len(fr_ids_manifest)}"
                f" FR(s) unchanged since last gate — DELTA auto-satisfied (live pytest skipped)."
            )
            return 0

    # Live verification: one whole-project pytest --cov run proves the
    # manifest's recorded per-FR coverage is achievable against current code.
    cov = gate1_evidence.validate_fr_coverage_immediate(project)
    if cov is None:
        print(
            f"\n[BLOCKED] Phase {completed_phase} Gate 1 live coverage check failed:\n"
            f"  pytest --cov could not be run (pytest missing, no tests/, or timeout).\n"
            f"  Re-run: python3 harness_cli.py finalize-gate --gate 1"
            f" --phase {completed_phase} --fr-id <FR-ID> --project {project}"
        )
        return 14
    if cov < _min_cov:
        print(
            f"\n[BLOCKED] Phase {completed_phase} Gate 1 live coverage check failed:\n"
            f"  whole-project coverage {cov:.1f}% < {_min_cov:.1f}% (from manifest)\n"
            f"  Add tests or use '# pragma: no cover' for unreachable paths, then re-run."
        )
        return 14
    print(
        f"  [Gate 1 coverage] Phase {completed_phase}: live pytest --cov"
        f" = {cov:.1f}% ≥ {_min_cov:.1f}% ✓ ({len(fr_ids_manifest)} FRs covered)"
    )
    return 0

def _check_submodule_drift(project: Path) -> None:
    """Phase 6 improvement #3: detect when harness/ submodule HEAD is behind
    origin/main (e.g. CI auto-fix landed). Prints actionable warning.
    Non-blocking — silent skip when offline / no origin access.

    J improvement: prefer `harness sync` (one-shot) over manual 4-step process.
    Delegates to core.submodule_sync.behind_count() for the count.
    """
    from core.submodule_sync import behind_count as _behind_count
    _sub = project / "harness"
    _behind = _behind_count(_sub)
    if _behind <= 0:
        return  # offline (-1) or already up to date (0) → silent
    print(
        f"\n[WARN] harness/ submodule is {_behind} commit(s) behind "
        f"origin/main. CI may have applied test-fix commits."
    )
    print("  Quick fix — one-shot sync:")
    print("    python3 -m harness.cli sync-harness")
    print("  Or manually:")
    print(f"    git -C {project}/harness pull --ff-only origin main")
    print(f"    git -C {project} add harness && git commit -m "
          f"'chore(harness): bump submodule to latest'")
    print("  (Non-blocking — local checkout is still functional.)")

def _check_gate_score_variance(project: Path, phase: int) -> int:
    """Check that gate scores within a phase vary across FRs.

    Returns 0 on pass, 1 on fabrication detected, or 0 on skip
    (not enough files, missing yaml, etc.).
    """
    try:
        import glob as _glob
        import yaml as _yaml
    except ImportError:
        print("[advance-phase] ⚠ yaml unavailable — skipping gate score variance check")
        return 0

    try:
        _decision_dir = project / ".methodology" / "decision_logs"
        _score_files = _glob.glob(
            str(_decision_dir / "**" / f"GATE_{phase}_*.yaml"),
            recursive=True,
        )
        _scores: list[float] = []
        for _sf in _score_files:
            try:
                _d = _yaml.safe_load(open(_sf, encoding="utf-8"))
                # Skip aggregate entries (Gate2/Gate4 have fr_id=null); only check per-FR scores.
                if (_d or {}).get("ctx", {}).get("fr_id") is None:
                    continue
                _s = (_d or {}).get("scores", {}).get("gate_score")
                if _s is not None:
                    _scores.append(float(_s))
            except Exception as exc:
                print(f"[WARN] SG-1 fabrication check: {_sf} unparseable, "
                      f"excluded from stddev sample: {exc}", file=sys.stderr)

        # SG-1: stricter fabrication detection. The previous check fired only
        # when ALL scores were identical (one decimal of variation defeated it,
        # e.g. 85.0 + 85.0 + 85.1). Now we compute stddev — if N≥3 scores have
        # stddev < 0.5, they're suspiciously uniform.
        # Saturated exception: when every FR is at-or-near the ceiling
        # (mean >= 99.5), per-FR variance is bounded by the distance to the
        # ceiling, so low stddev is a legitimate outcome of a clean codebase
        # rather than fabrication. Same threshold as the gate-3
        # dimension-variance `_saturated` exemption below.
        if len(_scores) >= 3:
            import statistics as _stats
            _stdev = _stats.pstdev(_scores)
            _mean = _stats.fmean(_scores)
            _saturated = _mean >= 99.5
            if _stdev < 0.5 and not _saturated:
                print(
                    f"\n[BLOCKED] Gate score variance check failed for Phase {phase}:\n"
                    f"  {len(_scores)} per-FR scores cluster around {_mean:.2f} "
                    f"(stddev={_stdev:.3f} < 0.5).\n"
                    f"  Scores: {_scores}\n"
                    f"  This indicates scores were copied/fabricated rather than\n"
                    f"  evaluated per FR. Re-run run-gate + evaluate dimensions\n"
                    f"  inline + finalize-gate for each FR with genuine evidence."
                )
                return 1
        if _scores:
            print(f"[advance-phase] Gate score variance OK "
                  f"({len(_scores)} per-FR scores: {sorted(set(_scores))})")
        return 0
    except Exception as _exc:
        print(f"[advance-phase] ⚠ Gate score variance check error ({_exc}) — skipping")
        return 0

def _regen_traceability_views(project: Path) -> None:
    """Always-regenerate the human-readable traceability views from the live
    build_traceability scan, so a phase advance can never leave a stale or
    hand-mocked matrix behind. The authoritative FR status is that scan (code /
    test coverage) and quality_manifest.json — these Markdown files are
    render-only views (AUTO-GEN sentinel block); their content is never a gate
    input. Regenerated at phase granularity (advance-phase), matching their role
    as phase-level ASPICE tracking views.
    """
    try:
        from scripts.build_traceability import (
            build_traceability,
            generate_markdown_matrix,
        )
    except Exception as e:  # noqa: BLE001
        print(f"  [advance-phase] traceability views skipped (import): {e}")
        return
    try:
        rt = build_traceability(project)
    except Exception as e:  # noqa: BLE001
        print(f"  [advance-phase] traceability views skipped (scan failed): {e}")
        return
    layout = ProjectLayout(project)
    _regen_and_stage_view(
        project, layout.traceability_matrix_path,
        lambda p: generate_markdown_matrix(rt, p),
    )
    try:
        from core.traceability.spec_tracking_render import write_spec_tracking
        _regen_and_stage_view(
            project, layout.spec_tracking_path,
            lambda p: write_spec_tracking(project, rt, out_path=p),
        )
    except Exception as e:  # noqa: BLE001
        print(f"  [advance-phase] SPEC_TRACKING view skipped: {e}")

def _regen_and_stage_view(project: Path, path: Path, render) -> None:
    """Render a human-readable view file from SSOT and `git add` it only if its
    bytes actually changed (same no-op-commit guard as the STAGE_PASS regen).

    Best-effort: a render error is warned, never fatal — these are render-only
    views, not the authoritative source (that is build_traceability /
    quality_manifest.json).
    """
    old_hash = None
    if path.exists():
        try:
            old_hash = hash(path.read_bytes())
        except OSError:
            pass
    try:
        render(path)
    except Exception as e:  # noqa: BLE001
        print(f"  [advance-phase] {path.name} view regen skipped: {e}")
        return
    if not path.exists():
        return
    try:
        new_hash = hash(path.read_bytes())
    except OSError:
        new_hash = None
    if new_hash != old_hash:
        subprocess.run(["git", "add", str(path)], cwd=str(project), capture_output=True)
        print(f"  [advance-phase] {path.name} refreshed from SSOT → staged")

def _scope_violation_scripts(project: Path) -> list[str]:
    """Untracked diagnostic/debug scripts stranded at the repo root.

    WRITE_SCOPE convention: agent-generated debug artifacts belong under
    .sessi-work/tmp/ (gitignored), never the source tree. A workflow advance agent
    once left _diag_constitution.py at the repo root while diagnosing a constitution
    BLOCK. This is the mechanism that catches such orphans (the per-phase self-clean
    prompt rule only reduces their frequency; it relies on the agent complying).

    Narrow, high-precision pattern to avoid false positives that would halt the
    pipeline: untracked (git ??) AND top-level (no path separator — recursing would
    flag legitimate new module files not yet committed mid-phase) AND a script
    extension AND a name signalling a diagnostic. .sessi-work/ is gitignored, so its
    contents never surface as untracked and are never flagged.

    Uses `-z` (NUL-terminated, unquoted paths): without it, `git status --porcelain`
    quotes any path containing a space or non-ASCII character (core.quotePath), so
    e.g. "diag tool.py" comes back as the literal 13-char string `"diag tool.py"`
    (quotes included) — Path(...).suffix is then '.py"', which never matches
    _SCOPE_SCRIPT_EXTS and the file silently evades detection.

    `--untracked-files=normal` (git's default) rather than `=all`: an untracked
    directory is reported once (`?? dirname/`) instead of git recursing into and
    listing every file inside it — those entries would all be discarded by the
    top-level-only filter below anyway, so `=all` only adds wasted work on a large
    untracked tree (e.g. a not-yet-gitignored build/venv dir) with no behavior
    difference for the loose top-level files this check actually targets.
    """
    result = subprocess.run(
        ["git", "-C", str(project), "status", "--porcelain=v1", "-z",
         "--untracked-files=normal"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return []
    offenders: list[str] = []
    for entry in result.stdout.split("\0"):
        if not entry.startswith("??"):
            continue
        path = entry[3:]
        if "/" in path:  # top-level only
            continue
        p = Path(path)
        if p.suffix.lower() in _SCOPE_SCRIPT_EXTS and _scope_debug_name_match(p.stem):
            offenders.append(path)
    return offenders

def _scope_debug_name_match(stem: str) -> bool:
    tokens = re.split(r"[_\-\s]+", stem.lower())
    return any(t in _SCOPE_DEBUG_NAME_TOKENS for t in tokens)

_SCOPE_SCRIPT_EXTS: frozenset[str] = frozenset({".py", ".js", ".ts", ".sh"})

_SCOPE_DEBUG_NAME_TOKENS: frozenset[str] = frozenset({
    "diag", "debug", "scratch", "explore", "probe", "tmp",
    "sandbox", "throwaway", "adhoc", "wip", "poc",
})


def register(sub) -> None:
    """Wire this family's parsers onto the main subparser action."""
    # plan-phase
    help_plan = "Generate phase execution plan from SRS/SAD artifacts (stdlib only)"
    pp = sub.add_parser("plan-phase", help=help_plan)
    pp.add_argument("--phase",  type=int, required=True, help="Phase number (1-8)")
    pp.add_argument("--project", default=".", help="Project root path (default: .)")
    pp.add_argument("--output", default=None, help="Output file path (default: stdout)")
    pp.add_argument("--force", action="store_true",
                    help="Overwrite an existing plan even if it has progress marks ([x])")
    pp.set_defaults(func=cmd_plan_phase)

    # plan-all
    pa = sub.add_parser("plan-all",
                        help="Generate all 8 phase plans (dynamic mode) at project start")
    pa.add_argument("--project", default=".", help="Project root path (default: .)")
    pa.add_argument("--output-dir", default=None, dest="output_dir",
                    help="Output directory (default: <project>/.methodology/)")
    pa.add_argument("--force", action="store_true",
                    help="Regenerate all plans even those with progress marks ([x])")
    pa.set_defaults(func=cmd_plan_all)

    # run-phase
    rp = sub.add_parser("run-phase", help="Run preflight checks before entering a phase")
    rp.add_argument("--phase",   type=int, required=True, help="Phase number (1-8)")
    rp.add_argument("--project", default=".", help="Project root (default: .)")
    rp.add_argument("--skip-substrate-probe", action="store_true",
                    dest="skip_substrate_probe",
                    help="Skip the spawn-substrate preflight probe (Round 12 站0b). "
                         "Escape hatch for a broken/false-positive probe — the "
                         "per-FR loop then runs unprotected against permission walls.")
    rp.set_defaults(func=cmd_run_phase)

    # pre-commit-check (git commit hook only — FSM + BVS order + kill-switch + trace freshness)
    pcc = sub.add_parser(
        "pre-commit-check",
        help="Lightweight check for git commit hooks (FSM/constitution/kill-switch only; no drift/traceability)",
    )
    pcc.add_argument("--phase",   type=int, required=True, help="Phase number (1-8)")
    pcc.add_argument("--project", default=".", help="Project root (default: .)")
    pcc.set_defaults(func=cmd_pre_commit_check)

    # advance-phase
    adv = sub.add_parser(
        "advance-phase",
        help="Advance to next phase: update state.json (single source of truth)",
    )
    adv.add_argument(
        "--completed", type=int, required=True, dest="completed_phase",
        help="Phase number that just completed (advance-phase --completed 3 → sets phase 4)",
    )
    adv.add_argument("--project", default=".", help="Project root (default: .)")
    adv.set_defaults(func=cmd_advance_phase)

    # generate-next-plan (checkpoint-based tactical plan generator)
    gnp = sub.add_parser(
        "generate-next-plan",
        help="Read manifest state and emit the next concrete gate evaluation plan",
    )
    gnp.add_argument("--project", default=".", help="Project root (default: .)")
    gnp.add_argument("--phase",   type=int, default=None, help="Override current phase")
    gnp.set_defaults(func=cmd_generate_next_plan)

    # v2.9.1 B.1: validate-handoff
    vh = sub.add_parser(
        "validate-handoff",
        help="Cross-deliverable dependency check for phase handoffs (P{N} → P{N+1})",
    )
    vh.add_argument(
        "--from-phase", type=int, required=True, dest="from_phase",
        choices=[1, 2, 3, 4, 5, 6, 7, 8],
        help="Phase number that just completed; validator checks deliverables needed by P{N+1}",
    )
    vh.add_argument("--project", default=".", help="Project root (default: .)")
    vh.set_defaults(func=cmd_validate_handoff)

    # J: sync-harness — pull + commit + push harness submodule in one shot
    sh = sub.add_parser(
        "sync-harness",
        help="Pull + commit + push harness submodule (J improvement)",
    )
    sh.add_argument("--project", default=".", help="Project root (default: .)")
    sh.add_argument("--submodule", default="harness", help="Submodule path (default: harness)")
    sh.add_argument("--remote", default="origin", help="Remote name (default: origin)")
    sh.add_argument("--branch", default="main", help="Branch name (default: main)")
    sh.add_argument("--no-push", action="store_true",
                    help="Skip push; just pull + show commit message")
    sh.set_defaults(func=cmd_sync_harness)
