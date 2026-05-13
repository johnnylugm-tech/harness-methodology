#!/usr/bin/env python3
"""
harness_cli.py — Standalone CLI for harness-methodology.

Standalone entrypoint for the harness-methodology repo.
Does NOT require the full parent system (cli.py needs 30+ external modules).

Usage:
    python harness_cli.py plan-phase       --phase 3 [--project .] [--output plan.md]
    python harness_cli.py run-phase        --phase 3 [--project .] [--force]
    python harness_cli.py run-gate         --gate 2  --phase 3 [--project .] [--fr-id FR-01]
    python harness_cli.py finalize-gate    --gate 2  --phase 3 [--project .] [--fr-id FR-01]
    python harness_cli.py generate-next-plan [--project .] [--phase 3]
    python harness_cli.py run-pipeline     [--phase-from 1] [--phase-to 8] [--project .]
                                           [--force]
    python harness_cli.py manifest         --fr-ids FR-01 FR-02 [--sad SAD.md]
    python harness_cli.py status           [--project .]
    python harness_cli.py effort           [--phase 3] [--project .]
    python harness_cli.py reload-policy    [--policy-file enforcement/enforcement.json]
    python harness_cli.py run-gap-analysis  [--project .] [--spec SPEC.md] [--similarity 0.6]
    python harness_cli.py audit-phase       --phase 3 --repo owner/repo [--branch main]
    python harness_cli.py verify-spec       [--project .]
    python harness_cli.py check-logic       [--project .] [--srs SRS.md]
    python harness_cli.py init-project      --project /path/to/target [--phase 3] [--force]
    python harness_cli.py push-checkpoint   --phase 1|2 --project . [--fr-ids FR-01,FR-02]
    python harness_cli.py advance-phase     --completed-phase 3 [--project .]

Gate Evaluation (two-phase flow):
    1. run-gate    → prints evaluation prompt for Claude; exits 0
    2. Claude evaluates inline, writes .sessi-work/gate{N}_result.json
    3. finalize-gate → reads result, checks thresholds, commits

Available gates:
    Gate 1  per-FR check       (P3/P4/P5/P7/P8, trigger: per_fr_completion)
    Gate 2  P3 phase-exit      (score_gate: 75, 7 dims)
    Gate 3  P4 phase-exit      (score_gate: 80, 12 dims, full CRG)
    Gate 4  P6 full-project    (score_gate: 85, 12 dims, Hermes APPROVE required)

Exit codes:
    0   All phases complete
    1   Hard failure (investigate error)
    2   run-gap-analysis: critical gaps detected (distinct from hard error)
    10  PAUSE — Claude must evaluate gate; run finalize-gate then re-run pipeline
    11  Phase Truth < 90% (HR-11); fix and re-run with --phase-from N
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from harness.git_strategy import GitStrategy
    from harness.harness_bridge import GateBlockedError

# Ensure repo root on path so core/ and harness/ resolve
_REPO_ROOT = Path(__file__).parent
sys.path.insert(0, str(_REPO_ROOT))

# Phases where Gate 1 runs per-FR
_PER_FR_GATE1_PHASES: frozenset[int] = frozenset({3, 4, 5, 7, 8})

# Entry gate required per phase (CONSTITUTION.md §2.3)
_ENTRY_GATE_MAP: dict[int, int] = {4: 2, 5: 3, 6: 3, 7: 4, 8: 4}

# Phase → composite exit gate number
_PHASE_EXIT_GATES: dict[int, int] = {3: 2, 4: 3, 6: 4}


# ---------------------------------------------------------------------------
# plan-phase
# ---------------------------------------------------------------------------

def cmd_plan_phase(args: argparse.Namespace) -> int:
    """Generate phase execution plan from SRS/SAD artifacts."""
    from scripts.generate_full_plan import generate_full_plan

    repo_path = Path(args.project).resolve()
    output_path = Path(args.output) if args.output else None

    print(f"\n{'='*60}\nplan-phase: Phase {args.phase} | repo={repo_path}\n{'='*60}")

    plan = generate_full_plan(args.phase, repo_path, output_path)
    if plan is None:
        print(f"\n[ERROR] Failed to generate plan for phase {args.phase}")
        return 1

    if output_path:
        print(f"\nPlan written → {output_path}  ({len(plan)} chars)")
    else:
        print(plan)
    return 0


# ---------------------------------------------------------------------------
# run-phase
# ---------------------------------------------------------------------------

def _verify_entry_gate(project: Path, phase: int) -> dict:
    """Automatically verify entry gate conditions before phase execution.

    CONSTITUTION.md SS2.3 defines:
    - P1: None
    - P2: Human1 (P1) — git log APPROVE
    - P3: Human1 (P2) — git log APPROVE
    - P4-P8: quality_manifest.json gate PASS
    """
    if phase <= 1:
        return {"passed": True, "gate": "None", "reason": "P1 has no entry gate"}

    if phase in (2, 3):
        import subprocess as sp
        try:
            commit_marker = f"phase{phase - 1}(human-review)"
            result = sp.run(
                ["git", "-C", str(project), "log", "--oneline", "--grep", commit_marker, "-1"],
                capture_output=True, text=True, timeout=10,
            )
            if result.stdout.strip():
                return {"passed": True, "gate": f"Human1 (P{phase - 1})",
                        "reason": f"Found human APPROVE commit for P{phase - 1}"}
            return {"passed": False, "gate": f"Human1 (P{phase - 1})",
                    "reason": f"No human APPROVE commit found for P{phase - 1}"}
        except Exception as e:
            return {"passed": False, "gate": f"Human1 (P{phase - 1})",
                    "reason": f"Git log check failed: {e}"}

    manifest_path = project / ".methodology" / "quality_manifest.json"
    if not manifest_path.exists():
        return {"passed": False, "gate": f"Gate {_ENTRY_GATE_MAP.get(phase)}",
                "reason": "quality_manifest.json not found"}

    try:
        manifest = json.loads(manifest_path.read_text())
        gates = manifest.get("gates", {})
        prev_gate = _ENTRY_GATE_MAP.get(phase)
        if prev_gate:
            gate_status = gates.get(str(prev_gate), {})
            if gate_status.get("passed"):
                return {"passed": True, "gate": f"Gate {prev_gate}",
                        "reason": f"Gate {prev_gate} PASS confirmed"}
            return {"passed": False, "gate": f"Gate {prev_gate}",
                    "reason": f"Gate {prev_gate} not PASS in manifest"}
    except Exception as e:
        return {"passed": False, "reason": f"Manifest parse error: {e}"}

    return {"passed": False, "gate": "Unknown", "reason": f"No entry gate defined for phase {phase}"}


def cmd_run_phase(args: argparse.Namespace) -> int:
    """Run pre/post-flight hooks for a phase. Use --fast for commit-hook lightweight checks."""
    from core.phase_hooks import PhaseHooks

    project = Path(args.project).resolve()
    hooks = PhaseHooks(str(project), phase=args.phase)
    fast = getattr(args, "fast", False)

    print(f"\n{'='*60}\nrun-phase: Phase {args.phase}{' (fast)' if fast else ''}\n{'='*60}")

    # Entry gate check (CONSTITUTION.md SS2.3)
    entry_gate = _verify_entry_gate(project, args.phase)
    if not entry_gate["passed"] and not args.force:
        print(f"\n[ENTRY GATE FAILED] {entry_gate['gate']} — {entry_gate['reason']}")
        print("Use --force to skip entry gate verification.")
        return 10
    print(f"\n[ENTRY GATE] {entry_gate['gate']}: {entry_gate['reason']}")

    if fast:
        pre = _run_fast_preflight(hooks)
    else:
        pre = hooks.preflight_all()

    if not pre["all_passed"] and not args.force:
        print(f"\nPRE-FLIGHT FAILED: {pre['details']}")
        print("Use --force to override preflight failures.")
        return 1

    print("\n[INFO] Preflight passed. Phase execution hooks ready.")
    if fast:
        print("[INFO] Fast mode: skipped drift, traceability, gap analysis, CI readiness.")
        print("[INFO] Run without --fast for full preflight before push.")
    print("[INFO] Next steps:")
    if args.phase in _PER_FR_GATE1_PHASES:
        manifest_path = project / ".methodology" / "quality_manifest.json"
        fr_ids = []
        if manifest_path.exists():
            try:
                fr_ids = json.loads(manifest_path.read_text()).get("fr_ids", [])
            except Exception:
                pass
        if fr_ids:
            print(f"        Per-FR Gate 1 ({len(fr_ids)} FRs): {', '.join(fr_ids)}")
            for fr_id in fr_ids:
                print(f"          python harness_cli.py run-gate --gate 1 --phase {args.phase} --project {project} --fr-id {fr_id}")
        else:
            print(f"        python harness_cli.py run-gate --gate 1 --phase {args.phase} --project {project} --fr-id FR-XX")
            print(f"        (quality_manifest.json not found — run 'plan-phase' first to populate FR IDs)")
    print(f"        python harness_cli.py run-pipeline --phase-from {args.phase} --project {project}")
    if not fast:
        # POST-FLIGHT: constitution re-check, BVS invariants, drift check, FSM advance
        post = hooks.postflight_all()
        if not post["success"] and not args.force:
            print(f"\n[POST-FLIGHT FAILED]")
            return 1
        print(f"\n[POST-FLIGHT] {'PASS' if post['success'] else 'FAIL (--force)'}")
    return 0


def _run_fast_preflight(hooks) -> dict:
    """Lightweight preflight: FSM, constitution, kill-switch only. For commit hooks."""
    results = {
        "fsm": hooks.preflight_fsm_check(),
        "bvs_phase_order": hooks.preflight_bvs_phase_order(),
        "constitution": hooks.preflight_constitution(),
        "kill_switch": hooks.preflight_kill_switch(),
    }
    all_passed = all(r.get("passed", False) for r in results.values())
    return {"all_passed": all_passed, "details": results}


# ---------------------------------------------------------------------------
# run-gate  (Phase 1 of two-phase evaluation)
# ---------------------------------------------------------------------------

def cmd_run_gate(args: argparse.Namespace) -> int:
    """
    Phase 1: prepare gate context and print evaluation instructions for Claude.

    Claude must evaluate inline and write .sessi-work/gate{N}_result.json,
    then call `finalize-gate` to complete threshold checks and git operations.
    """
    from harness.harness_bridge import HarnessBridge

    project = str(Path(args.project).resolve())
    bridge = HarnessBridge()
    fr_id = getattr(args, "fr_id", None) or None

    print(f"\n{'='*60}\nrun-gate: Gate {args.gate} | Phase {args.phase}\n{'='*60}")

    ctx = bridge.prepare_gate(
        gate_num=args.gate,
        project_root=project,
        phase=args.phase,
        fr_id=fr_id,
    )

    print(ctx.evaluation_prompt())
    print("\n" + "─" * 60)
    print("NEXT STEP: Evaluate the dimensions above, then run:")
    fr_flag = f" --fr-id {fr_id}" if fr_id else ""
    print(
        f"  python harness_cli.py finalize-gate --gate {args.gate} "
        f"--phase {args.phase} --project {args.project}{fr_flag}"
    )
    print("─" * 60)
    return 0


# ---------------------------------------------------------------------------
# finalize-gate  (Phase 2 of two-phase evaluation)
# ---------------------------------------------------------------------------

def cmd_finalize_gate(args: argparse.Namespace) -> int:
    """
    Phase 2: read gate{N}_result.json, check thresholds, update manifest, git.

    Called after Claude has completed inline evaluation and written the result file.
    """
    from harness.harness_bridge import HarnessBridge, GateBlockedError

    project = str(Path(args.project).resolve())
    bridge = HarnessBridge()
    fr_id = getattr(args, "fr_id", None) or None

    print(f"\n{'='*60}\nfinalize-gate: Gate {args.gate} | Phase {args.phase}\n{'='*60}")

    # Rebuild context (loads config; skips CRG recon second time since recon file already exists)
    ctx = bridge.prepare_gate(
        gate_num=args.gate,
        project_root=project,
        phase=args.phase,
        fr_id=fr_id,
    )

    try:
        result = bridge.finalize_gate(ctx)
        print(f"\nGATE {args.gate} PASSED")
        print(f"  score           : {result.score:.1f}")
        print(f"  quality_complete: {result.quality_complete}")
        print(f"  open_critical   : {result.open_critical}")
        print(f"  open_high       : {result.open_high}")

        _update_state_checkpoint(Path(args.project).resolve(), args.gate, fr_id)

        git = _make_git(args, Path(args.project).resolve())
        git.ensure_gitignore()
        if args.gate == 1:
            git.commit_fr_gate1(fr_id or "unknown", result.score, args.phase)
        else:
            git.commit_and_push_gate(args.gate, args.phase, result.score)
        return 0

    except FileNotFoundError as e:
        print(f"\n[ERROR] {e}")
        print(
            f"  Run `python harness_cli.py run-gate --gate {args.gate} "
            f"--phase {args.phase} --project {args.project}` first,\n"
            "  then evaluate the dimensions and write the result file."
        )
        return 2

    except GateBlockedError as e:
        project_path = Path(args.project).resolve()
        print(_format_block_diagnostic(
            e, args.gate, args.phase, fr_id, 3, project_path,
        ))
        return 1


# ---------------------------------------------------------------------------
# generate-next-plan
# ---------------------------------------------------------------------------

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
        except Exception:  # pylint: disable=broad-exception-caught
            pass

    phase_names = {
        1: "Requirements Specification", 2: "Architecture Design",
        3: "Implementation",            4: "Testing",
        5: "Verification & Delivery",   6: "Quality Assurance",
        7: "Risk Management",           8: "Configuration Management",
    }
    print(f"\nPhase      : {current_phase} ({phase_names.get(current_phase, '?')})")

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

    if current_phase in _PER_FR_GATE1_PHASES:
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

    if current_phase in _PHASE_EXIT_GATES:
        gate_num = _PHASE_EXIT_GATES[current_phase]
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
        checkpoints.append(("Gate 4 — Full Project (Hermes APPROVE)", done))

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


# ---------------------------------------------------------------------------
# manifest
# ---------------------------------------------------------------------------

def _generate_sab_json(project: Path) -> bool:
    """Run scripts/generate_sab.py to produce .methodology/SAB.json. Returns True on success."""
    import subprocess  # nosec B404
    sab_script = Path(__file__).parent / "scripts" / "generate_sab.py"
    if not sab_script.exists():
        print("  [SAB] generate_sab.py not found — skipping SAB.json generation")
        return False
    try:
        result = subprocess.run(  # nosec B603 B607
            ["python3", str(sab_script), "--project", str(project)],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            sab_path = project / ".methodology" / "SAB.json"
            print(f"  [SAB] SAB.json written → {sab_path}")
            return True
        else:
            print(f"  [SAB] WARNING: generate_sab.py failed: {result.stderr[:200]}")
            return False
    except Exception as exc:
        print(f"  [SAB] WARNING: SAB generation error: {exc}")
        return False


def cmd_manifest(args: argparse.Namespace) -> int:
    """Generate quality_manifest.json at P2 exit."""
    from harness.harness_bridge import HarnessBridge

    project = Path(args.sad).resolve().parent
    bridge = HarnessBridge()
    out = bridge.generate_quality_manifest(
        fr_ids=args.fr_ids,
        sad_path=args.sad,
    )
    print(f"quality_manifest.json written → {out}")
    manifest = json.loads(out.read_text(encoding="utf-8"))
    print(f"  fr_ids        : {manifest['fr_ids']}")
    print(f"  generated_at  : phase {manifest['generated_at_phase']}")
    _generate_sab_json(project)
    git = _make_git(args, project)
    git.ensure_gitignore()
    git.commit_and_push_p2(args.fr_ids)
    return 0


# ---------------------------------------------------------------------------
# push-checkpoint  (P1/P2 human review checkpoint push + HANDOVER.md)
# ---------------------------------------------------------------------------

def cmd_push_checkpoint(args: argparse.Namespace) -> int:
    """Push P1/P2 human-review checkpoint with HANDOVER.md generation.

    Unlike raw git push, this calls GitStrategy which:
    - Writes HANDOVER.md (crash-recovery checkpoint)
    - Stages all changes
    - Commits with conventional commit message
    - Pushes to origin

    Usage:
      python harness_cli.py push-checkpoint --phase 1 --project . --fr-ids FR-01,FR-02,FR-03
      python harness_cli.py push-checkpoint --phase 2 --project . --fr-ids FR-01,FR-02
    """
    project = Path(args.project).resolve()
    fr_ids = [f.strip() for f in args.fr_ids.split(",") if f.strip()]
    # Note: if fr_ids is empty here, GitStrategy.commit_and_push_p1/p2 will
    # auto-detect from SRS.md — no need to block here.

    git = _make_git(args, project)
    git.ensure_gitignore()
    phase = args.phase
    if phase not in (1, 2):
        print(f"[ERROR] push-checkpoint only supports P1/P2 (got phase {phase}).")
        print("  P3+ use: python harness_cli.py run-pipeline --phase-from {phase}")
        return 1
    if phase == 1:
        ok = git.commit_and_push_p1(
            fr_ids=fr_ids,
            background="P1 human review APPROVED — SRS + deliverables complete.",
            notes=["Human peer review passed", "All deliverables reviewed and approved"],
        )
    else:
        ok = git.commit_and_push_p2(
            fr_ids=fr_ids,
            background="P2 human review APPROVED — SAD + ADR + quality manifest complete.",
            notes=["Human peer review passed", "SAD/ADR reviewed and approved"],
        )
    if ok:
        handover = project / "HANDOVER.md"
        if handover.exists():
            print(f"  HANDOVER.md → {handover}")
        print("  [git] pushed → remote ✓")
    return 0 if ok else 1


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------

def cmd_status(args: argparse.Namespace) -> int:
    """Show current manifest + FSM state, phase progress, and optionally test stats."""
    project = Path(args.project).resolve()
    manifest_path = project / ".methodology" / "quality_manifest.json"
    state_path    = project / ".methodology" / "state.json"
    json_out = getattr(args, "json", False)
    full = getattr(args, "full", False)

    # Gather state
    state = {}
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))

    manifest = {}
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    current_phase = state.get("current_phase", 0)
    fr_ids = manifest.get("fr_ids", [])
    gates = manifest.get("gate_results", {})

    # Phase progress table
    phase_names = {1: "Requirements", 2: "Architecture", 3: "Implementation",
                   4: "Testing", 5: "Verification", 6: "Quality", 7: "Risk", 8: "Config"}
    phase_status = {}
    for p in range(1, 9):
        if p < current_phase:
            phase_status[p] = "COMPLETE"
        elif p == current_phase:
            phase_status[p] = "IN_PROGRESS"
        else:
            phase_status[p] = "NOT_STARTED"

    # FR gate status for current phase
    fr_status = {}
    if current_phase >= 3 and gates.get("gate1"):
        for fr_id in fr_ids:
            fr_result = gates["gate1"].get(fr_id)
            if fr_result and isinstance(fr_result, dict):
                fr_status[fr_id] = {"score": fr_result.get("score", 0), "complete": fr_result.get("quality_complete", False)}
            else:
                fr_status[fr_id] = {"score": None, "complete": False}

    # Test stats (only when --full)
    test_count = None
    coverage_pct = None
    if full:
        import subprocess  # nosec B404
        try:
            r = subprocess.run(["pytest", "--collect-only", "-q", "--no-header"],
                             cwd=project, capture_output=True, text=True, timeout=30)
            m = re.search(r"(\d+) tests? collected", r.stdout + r.stderr)
            if m:
                test_count = int(m.group(1))
        except Exception:
            pass
        try:
            r = subprocess.run(["pytest", "--cov=.", "--cov-report=term", "--tb=no", "-q"],
                             cwd=project, capture_output=True, text=True, timeout=120)
            m = re.search(r"TOTAL\s+\d+\s+\d+\s+(\d+)%", r.stdout + r.stderr)
            if m:
                coverage_pct = int(m.group(1))
        except Exception:
            pass

    # Auto-fix rounds
    auto_fix_rounds_used = 0
    if full and gates:
        for gate_name in ["gate1", "gate2", "gate3", "gate4"]:
            gv = gates.get(gate_name)
            if isinstance(gv, dict) and "rounds_used" in gv:
                auto_fix_rounds_used = max(auto_fix_rounds_used, gv.get("rounds_used", 0))

    if json_out:
        result = {
            "project": str(project),
            "fsm": {"state": state.get("state", "UNKNOWN"), "current_phase": current_phase,
                    "last_update": state.get("last_update", "-")},
            "phase_progress": {str(p): phase_status[p] for p in range(1, 9)},
            "fr_ids": fr_ids,
            "gates": gates,
        }
        if full:
            result["test_count"] = test_count
            result["coverage_pct"] = coverage_pct
            result["auto_fix_rounds_used"] = auto_fix_rounds_used
        print(json.dumps(result, indent=2, default=str))
        return 0

    # Text output
    print(f"\n{'='*60}\nHarness Status: {project}\n{'='*60}")

    if state:
        print("\n[FSM State]")
        print(f"  state         : {state.get('state', 'UNKNOWN')}")
        print(f"  current_phase : {current_phase}")
        print(f"  last_update   : {state.get('last_update', '-')}")
    else:
        print("\n[FSM State] .methodology/state.json not found (project not initialised)")

    # Phase progress table
    print("\n[Phase Progress]")
    for p in range(1, 9):
        icon = {"COMPLETE": "✅", "IN_PROGRESS": "🔄", "NOT_STARTED": "⬜"}.get(phase_status[p], "⬜")
        print(f"  {icon} P{p} {phase_names.get(p, 'Unknown'):<16} {phase_status[p]}")

    if manifest:
        print("\n[Quality Manifest]")
        print(f"  schema_version: {manifest.get('schema_version')}")
        print(f"  fr_ids        : {fr_ids}")
        for g, v in gates.items():
            if v is None:
                print(f"  {g}           : not run")
            elif isinstance(v, dict) and "score" in v:
                print(f"  {g}           : score={v['score']} complete={v['quality_complete']}")
            elif isinstance(v, dict):
                for fr, r in v.items():
                    print(f"  {g}/{fr}  : score={r['score']} complete={r['quality_complete']}")
    else:
        print("\n[Quality Manifest] Not found — run `harness_cli.py manifest` first")

    # FR detail for current phase
    if fr_status:
        print(f"\n[FR Gate 1 Status — Phase {current_phase}]")
        for fr_id, fs in fr_status.items():
            if fs["score"] is not None:
                print(f"  {fr_id}: score={fs['score']} complete={fs['complete']}")
            else:
                print(f"  {fr_id}: not run")

    if full:
        print(f"\n[Test Stats]")
        print(f"  tests collected: {test_count if test_count is not None else 'N/A'}")
        print(f"  coverage       : {coverage_pct}%" if coverage_pct is not None else "  coverage       : N/A")
        print(f"\n[Auto-Fix]")
        print(f"  rounds_used    : {auto_fix_rounds_used}")

    return 0


# ---------------------------------------------------------------------------
# effort
# ---------------------------------------------------------------------------

def cmd_effort(args: argparse.Namespace) -> int:
    """Show gate effort metrics summary."""
    from harness.effort_tracker import EffortTracker

    tracker = EffortTracker()
    summary = tracker.summary(phase=args.phase)

    print(f"\n{'='*60}")
    title = f"Effort Summary{' | Phase ' + str(args.phase) if args.phase else ''}"
    print(f"{title}\n{'='*60}")
    print(json.dumps(summary, indent=2))
    return 0


# ---------------------------------------------------------------------------
# advance-phase
# ---------------------------------------------------------------------------

def cmd_advance_phase(args: argparse.Namespace) -> int:
    """Advance to next phase: sync quality.phase + GitHub CURRENT_PHASE atomically.

    Calls _advance_fsm() which:
      1. Writes .methodology/state.json (current_phase = completed + 1)
      2. Updates git config quality.phase
      3. Attempts gh variable set CURRENT_PHASE (soft-fail with manual fallback)

    Usage:
        python harness_cli.py advance-phase --completed 3   # advances to phase 4
    """
    project = Path(args.project).resolve()
    print(f"\n[advance-phase] Completed phase {args.completed_phase} → advancing to {args.completed_phase + 1}")
    _advance_fsm(project, args.completed_phase)
    print(f"[advance-phase] Done — local hooks and CI now target phase {args.completed_phase + 1}")
    return 0


# ---------------------------------------------------------------------------
# reload-policy
# ---------------------------------------------------------------------------

def cmd_reload_policy(args: argparse.Namespace) -> int:
    """Hot-reload enforcement policies from enforcement.json."""
    from enforcement.policy_engine import PolicyEngine

    json_path = args.policy_file
    if not Path(json_path).exists():
        print(f"\n[ERROR] Policy file not found: {json_path}")
        print("  Create enforcement/enforcement.json with a 'policies' array.")
        return 1

    try:
        engine = PolicyEngine()
        loaded = engine.reload_policy(json_path)
        summary = engine.get_summary()
        print(f"\n{'='*60}\nPolicy Hot-Reload\n{'='*60}")
        print(f"  file          : {json_path}")
        print(f"  loaded        : {loaded} policies from file")
        print(f"  total active  : {len(engine.policies)} policies")
        print(f"  enabled       : {summary.get('total', len(engine.policies))}")
        if loaded > 0:
            print("\n[Loaded policies]")
            for pol in engine.policies[-loaded:]:
                status = "enabled" if pol.enabled else "disabled"
                print(f"  [{pol.enforcement.value.upper()}] {pol.id} — {pol.description} ({status})")
        return 0
    except Exception as e:  # pylint: disable=broad-exception-caught
        print(f"\n[ERROR] Failed to reload policies: {e}")
        return 1


# ---------------------------------------------------------------------------
# run-pipeline helpers
# ---------------------------------------------------------------------------

def _parse_fr_ids(text: str) -> list[str]:
    """Extract sorted unique FR-XX IDs from arbitrary markdown text."""
    import re
    return sorted(set(re.findall(r"\bFR-\d+\b", text)))


def _plan_phase_silent(phase: int, repo: Path, output: Path) -> None:
    """Run plan-phase; warns on failure but never blocks the pipeline."""
    try:
        from scripts.generate_full_plan import generate_full_plan
        plan = generate_full_plan(phase, repo, output)
        if plan:
            print(f"  plan → {output}")
    except Exception as exc:  # pylint: disable=broad-exception-caught
        print(f"  [WARN] plan-phase error: {exc} — continuing without plan")


def _auto_fix_loop(hooks, pre: dict, phase: int, project: Path,
                   max_rounds: int = 3) -> dict:
    """Core auto-fix loop: fix -> verify -> repeat or escalate.

    Args:
        hooks: PhaseHooks instance.
        pre: Preflight results dict.
        phase: Current phase number.
        project: Project root path.
        max_rounds: Maximum auto-fix rounds.

    Returns:
        Updated results dict with possible "escalation" key.
    """
    from core.auto_fix import AutoFixEngine, FixContext

    engine = AutoFixEngine(project_root=project, phase=phase, max_rounds=max_rounds)
    engine.start_phase_timer(estimate_seconds=max_rounds * 180.0)  # HR-13: 3 min per fix round

    fix_ctx_data = pre.get("details", {}).get("_fix_context", {})
    if not fix_ctx_data:
        return pre

    context = FixContext(
        source=fix_ctx_data.get("source", "phase_hooks"),
        problem_type=fix_ctx_data.get("problem_type", "preflight_failure"),
        severity=fix_ctx_data.get("severity", "high"),
        phase=phase,
        project_root=project,
        details=fix_ctx_data,
    )

    for round_num in range(1, max_rounds + 1):
        context.retry_count = round_num
        result = engine.fix(context)

        if result.escalation:
            print(f"[AUTO-FIX] ESCALATED: {result.escalation.value} — human intervention required")
            return {"all_passed": False, "escalation": result.escalation.value}

        # Re-run preflight
        check_result = hooks.preflight_all()
        if check_result.get("all_passed"):
            print(f"[AUTO-FIX] SUCCESS after {round_num} round(s)")
            return check_result

        print(f"[AUTO-FIX] Round {round_num}: fix applied but preflight still failing. "
              f"Confidence: {result.confidence:.0f}%")

    print(f"[AUTO-FIX] HR-12: max rounds ({max_rounds}) exceeded → PAUSE")
    return {"all_passed": False, "escalation": "hr12_max_rounds_exceeded"}


def _preflight(phase: int, project: Path, force: bool,
               enable_kill_switch: bool = True,
               drift_threshold: float = 85.0,
               auto_fix: bool = True,
               auto_fix_rounds: int = 3) -> int:
    """Run phase pre-flight hooks. Returns 0 on pass."""
    try:
        from core.phase_hooks import PhaseHooks
        hooks = PhaseHooks(str(project), phase=phase,
                           enable_kill_switch=enable_kill_switch,
                           drift_threshold=drift_threshold,
                           auto_fix_enabled=auto_fix)
        pre = hooks.preflight_all()
        if not pre.get("all_passed"):
            if force:
                print("  [PREFLIGHT FAIL] --force: continuing despite failures")
                return 0
            if auto_fix:
                print("  [PREFLIGHT FAIL] Attempting auto-fix...")
                pre = _auto_fix_loop(hooks, pre, phase, project, auto_fix_rounds)
                if pre.get("all_passed"):
                    print("  [PREFLIGHT] Auto-fix succeeded")
                    return 0
                if pre.get("escalation"):
                    print(f"  [PREFLIGHT] Auto-fix escalated: {pre['escalation']}")
            return 1
        return 0
    except Exception as exc:
        print(f"  [WARN] Phase hooks unavailable: {exc}")
        return 0 if force else 1


def _run_gap_analysis(project: Path, similarity: float = 0.6, spec: str = "SPEC.md") -> dict:
    """Run M3 gap analysis. Returns gap report dict; warns on failure."""
    try:
        from gap_detector.parser import SpecParser
        from gap_detector.scanner import CodeScanner
        from gap_detector.detector import GapDetector

        spec_path = project / spec
        if not spec_path.exists():
            print(f"  [M3] {spec} not found — skipping gap analysis")
            return {"skipped": True, "reason": f"{spec} not found"}

        spec = SpecParser(str(spec_path)).parse()
        scanner = CodeScanner(str(project))
        code = scanner.scan()
        detector = GapDetector(spec, code, similarity_threshold=similarity)
        gaps = detector.detect()
        summary = detector.get_summary()

        report = {
            "summary": {
                "total": summary.total_gaps, "missing": summary.missing,
                "incomplete": summary.incomplete, "orphaned": summary.orphaned,
                "critical": summary.critical, "major": summary.major,
                "minor": summary.minor,
            },
            "gaps": [{"type": g.gap_type, "severity": g.severity,
                       "reason": g.reason, "action": g.recommended_action}
                      for g in gaps],
        }
        report_path = project / ".methodology" / "gap_report.json"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, indent=2))
        print(f"  [M3] Gap report → {report_path}  "
              f"(total={summary.total_gaps}, critical={summary.critical})")
        return report
    except ImportError:
        print("  [M3] gap_detector unavailable — skipping gap analysis")
        return {"skipped": True, "reason": "gap_detector unavailable"}
    except Exception as exc:  # pylint: disable=broad-exception-caught
        print(f"  [M3] Gap analysis error: {exc}")
        return {"skipped": True, "error": str(exc)}


def _make_git(args: argparse.Namespace, project: Path) -> "GitStrategy":  # noqa: F821 — lazy import
    """Instantiate GitStrategy from parsed args. Lazy-imports to keep startup fast."""
    from harness.git_strategy import GitStrategy
    no_git = getattr(args, "no_git", False)
    return GitStrategy(project=project, enabled=not no_git)


def _update_state_checkpoint(project: Path, gate_num: int, fr_id: str | None) -> None:
    """Write last_gate / last_fr to .methodology/state.json after a gate passes."""
    from datetime import datetime, timezone
    state_path = project / ".methodology" / "state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    existing: dict = {}
    if state_path.exists():
        try:
            existing = json.loads(state_path.read_text(encoding="utf-8"))
        except Exception:  # pylint: disable=broad-exception-caught
            pass
    existing["last_gate"] = gate_num
    existing["last_fr"] = fr_id
    existing["last_update"] = datetime.now(timezone.utc).isoformat()
    state_path.write_text(json.dumps(existing, indent=2), encoding="utf-8")


def _advance_fsm(project: Path, completed_phase: int) -> None:
    """Write state.json, update git config quality.phase, and sync GitHub CURRENT_PHASE."""
    import subprocess  # nosec B404
    from datetime import datetime, timezone

    next_phase = completed_phase + 1

    # 1. Write .methodology/state.json
    state_path = project / ".methodology" / "state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    existing_state = "ACTIVE"
    if state_path.exists():
        try:
            existing_state = json.loads(state_path.read_text()).\
                get("state", "ACTIVE")
        except Exception:  # pylint: disable=broad-exception-caught
            pass
    state_path.write_text(
        json.dumps({
            "state": existing_state,
            "current_phase": next_phase,
            "last_gate": None,
            "last_fr": None,
            "last_update": datetime.now(timezone.utc).isoformat(),
        }, indent=2),
        encoding="utf-8",
    )

    # 2. Update git config quality.phase (local hooks read this)
    subprocess.run(  # nosec B603 B607
        ["git", "-C", str(project), "config", "--local", "quality.phase", str(next_phase)],
        capture_output=True,
    )
    print(f"  [FSM] quality.phase → {next_phase}")

    # 3. Attempt GitHub CURRENT_PHASE sync via gh CLI (soft-fail)
    try:
        gh = subprocess.run(  # nosec B603 B607
            ["gh", "variable", "set", "CURRENT_PHASE", "--body", str(next_phase)],
            cwd=str(project),
            capture_output=True,
            text=True,
            timeout=30,
        )
        if gh.returncode == 0:
            print(f"  [FSM] GitHub CURRENT_PHASE → {next_phase} ✓")
        else:
            _warn_github_phase_sync(next_phase)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        _warn_github_phase_sync(next_phase)


def _warn_github_phase_sync(next_phase: int) -> None:
    """Print manual fallback instructions when gh CLI sync fails."""
    print(f"  [WARN] GitHub CURRENT_PHASE not auto-updated (gh CLI unavailable or not authed).")
    print(f"  Manual option A: gh variable set CURRENT_PHASE --body \"{next_phase}\"")
    print(f"  Manual option B: GitHub repo → Settings → Variables → CURRENT_PHASE = {next_phase}")


# ---------------------------------------------------------------------------
# Gate BLOCKED diagnostic helpers
# ---------------------------------------------------------------------------

_DIMENSION_HINTS: dict[str, str] = {
    "linting":            "Run `ruff check . --fix` (or flake8); resolve all remaining lint errors",
    "type_safety":        "Run `mypy .`; add missing annotations and fix all type errors",
    "test_coverage":      "Run `pytest --cov` to find uncovered lines; add unit tests for each gap",
    "security":           "Fix OWASP-category issues; validate all inputs; remove eval/exec patterns",
    "secrets_scanning":   "Remove hard-coded secrets; move to env vars / vault; run `gitleaks detect`",
    "license_compliance": "Run `pip-licenses`; replace or vendor GPL/incompatible dependencies",
    "mutation_testing":   "Run `mutmut run`; add assertions that kill every surviving mutant",
    "architecture":       "Verify imports comply with SAD.md layer boundaries; fix all violations",
    "readability":        "Add [FR-XX] docstrings with Citations:; split functions >30 lines",
    "error_handling":     "Wrap I/O and network calls in try/except with specific exception types",
    "documentation":      "All public APIs need [FR-XX] docstrings with Citations: + line numbers",
    "performance":        "Profile with cProfile; fix N+1 queries; add caching where needed",
}


def _format_block_diagnostic(
    exc: "GateBlockedError",  # noqa: F821 — lazy import
    gate_num: int,
    phase: int,
    fr_id: str | None,
    max_rounds: int,
    project: Path,
) -> str:
    """Format a structured diagnostic for a gate BLOCKED event; also writes last_block.md."""
    failing = [d for d in exc.result.dimensions if d.score < d.threshold]
    passing = [d for d in exc.result.dimensions if d.score >= d.threshold]

    lines = [
        "",
        "─" * 60,
        f"GATE {gate_num} BLOCKED"
        + (f"  fr={fr_id}" if fr_id else "")
        + f"  phase={phase}  after {max_rounds} SSI round(s)",
        f"  composite score : {exc.result.score:.1f}",
        f"  open critical   : {exc.result.open_critical}",
        f"  open high       : {exc.result.open_high}",
        "",
        f"Failing dimensions ({len(failing)}):",
    ]
    for dim in failing:
        gap = dim.threshold - dim.score
        hint = _DIMENSION_HINTS.get(dim.name, "Review dimension-specific issues in SSI output")
        lines.append(
            f"  [FAIL] {dim.name:<22} score={dim.score:>5.1f}  "
            f"need={dim.threshold:>5.1f}  gap={gap:>4.1f}"
        )
        lines.append(f"         → {hint}")

    if passing:
        lines.append("")
        lines.append(
            f"Passing ({len(passing)}): "
            + ", ".join(f"{d.name}={d.score:.1f}" for d in passing)
        )

    fr_flag = f" --fr-id {fr_id}" if fr_id else ""
    lines.extend([
        "",
        "Fix the failing dimensions above, then resume:",
        f"  python harness_cli.py run-gate --gate {gate_num} --phase {phase}"
        f"{fr_flag} --project {project} --auto-fix-rounds {max_rounds}",
        "  # or restart pipeline from this phase:",
        f"  python harness_cli.py run-pipeline --phase-from {phase}"
        f" --project {project} --auto-fix-rounds {max_rounds}",
        "─" * 60,
    ])

    # Write .methodology/last_block.md
    report_lines = [
        f"# Gate {gate_num} BLOCKED — Phase {phase}",
        "",
        f"Generated: {__import__('datetime').datetime.now().isoformat()}",
        f"fr_id: {fr_id or 'n/a'} | rounds: {exc.result.rounds_used} | "
        f"open_critical: {exc.result.open_critical} | open_high: {exc.result.open_high}",
        "",
        "## Failing Dimensions",
        "",
    ]
    for dim in failing:
        gap = dim.threshold - dim.score
        hint = _DIMENSION_HINTS.get(dim.name, "Review SSI output")
        report_lines += [
            f"### {dim.name}",
            f"- score: {dim.score:.1f} / threshold: {dim.threshold:.1f} (gap: {gap:.1f})",
            f"- fix: {hint}",
            "",
        ]
    report_lines += [
        "## Resume Commands",
        "",
        "```bash",
        f"python harness_cli.py run-gate --gate {gate_num} --phase {phase}"
        + (f" --fr-id {fr_id}" if fr_id else "")
        + f" --project {project} --auto-fix-rounds {max_rounds}",
        "# or:",
        f"python harness_cli.py run-pipeline --phase-from {phase}"
        f" --project {project} --auto-fix-rounds {max_rounds}",
        "```",
    ]
    try:
        report_path = project / ".methodology" / "last_block.md"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text("\n".join(report_lines), encoding="utf-8")
        lines.append(f"  Full report → {report_path}")
    except Exception:  # pylint: disable=broad-exception-caught
        pass

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# run-gap-analysis (M3)
# ---------------------------------------------------------------------------

def cmd_run_gap_analysis(args: argparse.Namespace) -> int:
    """Run M3 gap analysis: detect gaps between SPEC.md and codebase."""
    project = Path(args.project).resolve()
    spec = args.spec or "SPEC.md"

    print(f"\n{'='*60}\nrun-gap-analysis (M3)  project={project}\n{'='*60}")

    # Fail fast if the spec file is missing (explicit user invocation — not a pipeline skip)
    spec_path = project / spec
    if not spec_path.exists():
        print(f"[ERROR] Spec file not found: {spec_path}")
        return 1

    report = _run_gap_analysis(project, similarity=args.similarity, spec=spec)

    if report.get("skipped"):
        reason = report.get("reason") or report.get("error", "unknown")
        print(f"  Skipped: {reason}")
        return 0

    summary = report.get("summary", {})
    print(f"\n{'─'*60}")
    print("Gap Analysis Results")
    print(f"{'─'*60}")
    print(f"  Total gaps : {summary.get('total', 0)}")
    print(f"  Missing    : {summary.get('missing', 0)}")
    print(f"  Incomplete : {summary.get('incomplete', 0)}")
    print(f"  Orphaned   : {summary.get('orphaned', 0)}")
    print(f"  Critical   : {summary.get('critical', 0)}")
    print(f"  Major      : {summary.get('major', 0)}")
    print(f"  Minor      : {summary.get('minor', 0)}")

    critical = summary.get("critical", 0)
    if critical > 0:
        print(f"\n[WARN] {critical} critical gap(s) detected")
        return 2  # 2 = critical gaps (distinct from hard error = 1)
    return 0


# ---------------------------------------------------------------------------
# run-pipeline
# ---------------------------------------------------------------------------

def cmd_run_pipeline(args: argparse.Namespace) -> int:
    """
    Execute the full harness pipeline from phase_from to phase_to.

    P1/P2 pause if SRS.md / SAD.md are missing (human must provide them).
    P3+ plans are generated dynamically from SAD.md / quality_manifest.json
    so FR IDs are always available before Gate 1 runs.

    Exit codes:
        0  — all requested phases completed
        1  — hard error (SSI unavailable, missing manifest, etc.)
        10 — PAUSE: human intervention needed; re-run with --phase-from N
    """
    from harness.harness_bridge import HarnessBridge, GateBlockedError

    project = Path(args.project).resolve()
    phase_from = args.phase_from
    phase_to = args.phase_to
    enable_kill_switch = not getattr(args, "no_kill_switch", False)
    drift_threshold = getattr(args, "drift_threshold", 85.0)
    bridge = HarnessBridge()
    git = _make_git(args, project)
    git.ensure_gitignore()

    print(f"\n{'='*60}")
    print(f"run-pipeline  P{phase_from}→P{phase_to}  project={project}")
    print(f"force={args.force}  kill_switch={enable_kill_switch}  "
          f"drift_threshold={drift_threshold}")
    auto_fix = not getattr(args, "no_auto_fix", False)
    auto_fix_rounds = min(getattr(args, "auto_fix_rounds", 3), 5)
    print(f"auto_fix={auto_fix}  auto_fix_rounds={auto_fix_rounds}")
    print(f"{'='*60}")

    # Optional M1 kill-switch: check circuit state before first phase
    _ks = None
    if enable_kill_switch:
        try:
            from kill_switch import KillSwitch
            _ks = KillSwitch()
            print("[M1] Kill-switch initialized")
        except ImportError:
            print("[M1] Kill-switch unavailable — continuing without circuit breaker")

    for phase in range(phase_from, phase_to + 1):
        print(f"\n{'─'*60}\n[Phase {phase}]\n{'─'*60}")

        # M1: Check kill-switch circuit before each phase
        if _ks is not None:
            for _agent in _ks.get_registered_agents():
                if _ks.is_agent_circuit_open(_agent):
                    print(f"[M1] BLOCKED: circuit OPEN for {_agent} — "
                          f"pipeline paused at Phase {phase}")
                    return 10

        # ── P1: SRS.md must exist (human writes it); checkpoint if valid ────
        if phase == 1:
            print(f"\n[1.1] SRS check + checkpoint")
            srs = project / "01-requirements" / "SRS.md"
            if not srs.exists():
                print(f"[1.1] PAUSE: SRS.md not found at {srs}")
                print("     Create 01-requirements/SRS.md (### FR-XX: ... sections required),")
                print("     then re-run:")
                print(f"     python harness_cli.py run-pipeline --phase-from 1 "
                      f"--project {project}")
                return 10
            # Guard: at least one P1 A/B session must be logged before committing
            # the P1 checkpoint — prevents committing an unreviewed SRS shell.
            spawn_log = project / ".methodology" / "sessions_spawn.log"
            p1_reviewed = False
            if spawn_log.exists():
                for _raw in spawn_log.read_text(encoding="utf-8", errors="ignore").splitlines():
                    try:
                        _entry = json.loads(_raw)
                        if _entry.get("phase") == 1:
                            p1_reviewed = True
                            break
                    except Exception:
                        continue
            if not p1_reviewed:
                print("[1.1] PAUSE: sessions_spawn.log has no Phase 1 entry — "
                      "A/B review not started or not logged")
                print("     Complete P1 A/B review (run generate-plan --phase 1 and follow steps),")
                print("     then re-run:")
                print(f"     python harness_cli.py run-pipeline --phase-from 1 "
                      f"--project {project}")
                return 10
            print("[1.1] SRS.md + A/B review verified — committing P1 checkpoint")
            fr_ids = _parse_fr_ids(srs.read_text(encoding="utf-8", errors="ignore"))
            git.commit_and_push_p1(
                fr_ids=fr_ids,
                background="P1 human review APPROVED — SRS + deliverables complete.",
                notes=["Human peer review passed", "All deliverables reviewed and approved"],
            )
            continue

        # ── P2: SAD.md must exist; generate manifest if missing ──────────
        if phase == 2:
            print(f"\n[2.1] SAD check")
            sad = project / "02-architecture" / "SAD.md"
            manifest_path = project / ".methodology" / "quality_manifest.json"
            if not sad.exists():
                print(f"[2.1] PAUSE: SAD.md not found at {sad}")
                print("     Generate SAD.md, then re-run:")
                print(f"     python harness_cli.py run-pipeline --phase-from 2 "
                      f"--project {project}")
                return 10
            if manifest_path.exists():
                print("[2.2] quality_manifest.json exists — skipping manifest generation")
            else:
                print(f"\n[2.2] Manifest + SAB generation")
                fr_ids = _parse_fr_ids(sad.read_text(encoding="utf-8", errors="ignore"))
                if not fr_ids:
                    srs = project / "01-requirements" / "SRS.md"
                    if srs.exists():
                        fr_ids = _parse_fr_ids(srs.read_text(encoding="utf-8", errors="ignore"))
                if not fr_ids:
                    print("[2.2] ERROR: No FR-XX IDs found in SAD.md or SRS.md.")
                    print("     Add '### FR-01: ...' sections and re-run.")
                    return 1
                bridge.generate_quality_manifest(fr_ids, str(sad))
                print(f"[2.2] quality_manifest.json created  fr_ids={fr_ids}")
                _generate_sab_json(project)
                git.commit_and_push_p2(fr_ids)  # PUSH ②
            continue

        # ── P3+: SAD.md + manifest required for FR-level gate planning ────
        manifest_path = project / ".methodology" / "quality_manifest.json"
        if not manifest_path.exists():
            print("[ERROR] quality_manifest.json missing — complete Phase 2 first.")
            return 1

        fr_ids = json.loads(manifest_path.read_text(encoding="utf-8")).get("fr_ids", [])

        # ── Entry gate verification (CONSTITUTION.md §2.3) ──────────────────
        entry_gate = _verify_entry_gate(project, phase)
        if not entry_gate["passed"] and not args.force:
            print(f"\n[ENTRY GATE FAILED] {entry_gate['gate']} — {entry_gate['reason']}")
            print("  Use --force to skip entry gate verification.")
            return 10
        print(f"[ENTRY GATE] {entry_gate['gate']}: {entry_gate['reason']}")

        # ── Step 1: Dynamic plan (reads SAD.md produced in P2) ────────────
        plan_out = project / ".methodology" / f"phase{phase}_plan.md"
        print(f"\n[{phase}.1] plan-phase")
        _plan_phase_silent(phase, project, plan_out)

        # ── Step 2: Preflight ─────────────────────────────────────────────
        print(f"\n[{phase}.2] preflight")
        pf_result = _preflight(phase, project, force=args.force,
                               enable_kill_switch=enable_kill_switch,
                               drift_threshold=drift_threshold,
                               auto_fix=auto_fix,
                               auto_fix_rounds=auto_fix_rounds)
        if pf_result != 0:
            print(f"[BLOCKED] Preflight failed for Phase {phase}.")
            print(f"  Fix constitution/FSM issues, then re-run with --phase-from {phase}")
            return 10

        # ── Step 2.5: M3 Gap Analysis ─────────────────────────────────────
        if phase >= 3:
            print(f"\n[{phase}.2.5] M3 gap analysis")
            _run_gap_analysis(project)

        # ── Step 3: Per-FR Gate 1 ─────────────────────────────────────────
        fr_pass_results: list[dict] = []  # accumulate for postflight add_fr_result
        if phase in _PER_FR_GATE1_PHASES:
            if not fr_ids:
                print(f"[ERROR] No FR IDs in manifest — cannot run Gate 1 for phase {phase}.")
                return 1
            print(f"\n[{phase}.3] Gate 1 for {len(fr_ids)} FR(s): {fr_ids}")
            for fr_id in fr_ids:
                print(f"  [{fr_id}] Gate 1 …", end=" ", flush=True)
                ctx = bridge.prepare_gate(
                    gate_num=1, project_root=str(project),
                    phase=phase, fr_id=fr_id,
                )
                result_path = Path(ctx.work_dir) / "gate1_result.json"
                if not result_path.exists():
                    print("PAUSE — evaluation needed")
                    print(ctx.evaluation_prompt())
                    print("\n  After evaluating, run:")
                    print(f"  python harness_cli.py finalize-gate --gate 1 "
                          f"--phase {phase} --project {project} --fr-id {fr_id}")
                    print(f"  Then re-run: python harness_cli.py run-pipeline "
                          f"--phase-from {phase} --project {project}")
                    return 10
                try:
                    g1_result = bridge.finalize_gate(ctx)
                    print(f"PASSED  score={g1_result.score:.1f}")
                    git.commit_fr_gate1(fr_id, g1_result.score, phase)
                    fr_pass_results.append({"fr_id": fr_id, "score": g1_result.score})
                except GateBlockedError as exc:
                    print("BLOCKED")
                    print(_format_block_diagnostic(exc, 1, phase, fr_id, 3, project))
                    return 10

        # ── Step 4: Phase exit gate ───────────────────────────────────────
        if phase in _PHASE_EXIT_GATES:
            gate_num = _PHASE_EXIT_GATES[phase]
            print(f"\n[{phase}.4] Gate {gate_num} (phase exit) …", end=" ", flush=True)
            ctx = bridge.prepare_gate(
                gate_num=gate_num, project_root=str(project),
                phase=phase, fr_id=None,
            )
            result_path = Path(ctx.work_dir) / f"gate{gate_num}_result.json"
            if not result_path.exists():
                print("PAUSE — evaluation needed")
                print(ctx.evaluation_prompt())
                print("\n  After evaluating, run:")
                print(f"  python harness_cli.py finalize-gate --gate {gate_num} "
                      f"--phase {phase} --project {project}")
                print(f"  Then re-run: python harness_cli.py run-pipeline "
                      f"--phase-from {phase} --project {project}")
                return 10
            try:
                result = bridge.finalize_gate(ctx)
                print(f"PASSED  score={result.score:.1f}")
                git.commit_and_push_gate(gate_num, phase, result.score, n_frs=len(fr_ids))
            except GateBlockedError as exc:
                print("BLOCKED")
                print(_format_block_diagnostic(exc, gate_num, phase, None, 3, project))
                return 10

        # ── Phase Truth (P3–P8 — HR-11 ≥90%) ───────────────────────────────
        # Exit 11 = Phase Truth < 90% (distinct from GateBlockedError exit 10)
        if phase >= 3:
            print(f"\n[{phase}.5] Phase Truth (HR-11 ≥90%) …")
            try:
                from core.quality_gate.phase_truth_verifier import PhaseTruthVerifier
            except ImportError:
                print("  [WARN] PhaseTruthVerifier unavailable — skipping HR-11 check")
            else:
                verifier = PhaseTruthVerifier(str(project), phase)
                truth_result = verifier.verify()
                if not truth_result["passed"]:
                    print(f"\n[BLOCKED] Phase {phase} truth = {truth_result['total_score']:.0f}% < 90%")
                    print(f"  Fix issues then re-run with --phase-from {phase}")
                    return 11  # 11 = Phase Truth failure (10 = GateBlockedError)

        # ── Postflight validation (constitution re-check, drift, BVS invariants, Steering) ──
        print(f"\n[{phase}.6] Postflight validation")
        from core.phase_hooks import PhaseHooks
        post_hooks = PhaseHooks(str(project), phase=phase,
                               enable_kill_switch=enable_kill_switch,
                               drift_threshold=drift_threshold,
                               auto_fix_enabled=auto_fix)
        for fr in fr_pass_results:
            post_hooks.add_gate1_pass(fr["fr_id"], fr["score"])
        post_result = post_hooks.postflight_all()
        if not post_result["success"] and not args.force:
            print(f"[BLOCKED] Postflight failed for Phase {phase}.")
            print(f"  Fix issues then re-run with --phase-from {phase}")
            return 10
        print(f"[{phase}.6] Postflight {'PASS' if post_result['success'] else 'FAIL (--force)'}")

        # ── Advance FSM state ─────────────────────────────────────────────
        _advance_fsm(project, phase)
        print(f"\n[Phase {phase}] ✓ Complete")

    print(f"\n{'='*60}")
    print(f"PIPELINE COMPLETE  P{phase_from}→P{phase_to} ✓")
    print(f"{'='*60}")
    # PUSH ⑥ — P7/P8 final artifacts (risk register + config records)
    late_phases = [p for p in range(phase_from, phase_to + 1) if p in (7, 8)]
    if late_phases:
        git.commit_and_push_final(late_phases)
    return 0


# ---------------------------------------------------------------------------
# audit-phase
# ---------------------------------------------------------------------------

def cmd_audit_phase(args: argparse.Namespace) -> int:
    """Audit a phase against GitHub artifacts using PhaseAuditor (8-dimension check)."""
    from scripts.phase_auditor import PhaseAuditor, GitHubFetcher

    print(f"\n{'='*60}\naudit-phase: Phase {args.phase} | repo={args.repo}\n{'='*60}")

    fetcher = GitHubFetcher(repo=args.repo, branch=args.branch)
    repo_info = fetcher.get_repo_info()
    if not repo_info:
        print(f"[ERROR] Cannot access repo: {args.repo} (check gh auth status)")
        return 1

    auditor = PhaseAuditor(fetcher=fetcher, phase=args.phase)
    result = auditor.run_all_checks()

    print(f"\n{'─'*60}")
    print(f"Audit Results — Phase {args.phase}")
    print(f"{'─'*60}")
    print(f"  Score        : {result.score:.0f}%")
    print(f"  Verdict      : {result.verdict}")
    print(f"  Critical     : {len(result.criticals())}")
    print(f"  Warnings     : {len(result.warnings())}")

    if args.save:
        save_path = Path(args.save)
        if args.output == "json":
            import json as _json
            save_path.write_text(_json.dumps({
                "phase": args.phase, "score": result.score,
                "verdict": result.verdict,
                "criticals": len(result.criticals()),
                "warnings": len(result.warnings()),
                "findings": [{"severity": f.severity, "check": f.check,
                              "detail": f.detail}
                             for f in result.findings],
            }, indent=2))
        else:
            save_path.write_text(str(result))
        print(f"\nReport saved → {save_path}")

    return 0 if result.verdict != "FAIL" else 1


# ---------------------------------------------------------------------------
# verify-spec
# ---------------------------------------------------------------------------

def cmd_verify_spec(args: argparse.Namespace) -> int:
    """Verify implementation complies with spec requirements (6-dimension check)."""
    from scripts.verify_spec_compliance import SpecComplianceChecker

    project = str(Path(args.project).resolve())
    print(f"\n{'='*60}\nverify-spec  project={project}\n{'='*60}")

    checker = SpecComplianceChecker(project)
    result = checker.check_all()

    print(f"\n{'─'*60}")
    print("Spec Compliance Report")
    print(f"{'─'*60}")
    print(f"  Score : {result['score']}")

    if result["passed"]:
        print("\n  PASSED:")
        for p in result["passed"]:
            print(f"    + {p}")

    if result["issues"]:
        print("\n  ISSUES:")
        for issue in result["issues"]:
            print(f"    - {issue}")
        if getattr(args, "fix", False):
            print("\n  FIX SUGGESTIONS:")
            for hint in checker.suggest_fixes(result["issues"]):
                print(f"    → {hint}")
            print("\n  [INFO] --fix shows suggestions only. Apply fixes manually.")

    return 0 if not result["issues"] else 1


# ---------------------------------------------------------------------------
# check-logic
# ---------------------------------------------------------------------------

def cmd_check_logic(args: argparse.Namespace) -> int:
    """Check code for logic correctness issues (output/branch/lazy-init/semantic)."""
    from scripts.spec_logic_checker import SpecLogicChecker, SemanticValidator

    project = str(Path(args.project).resolve())
    print(f"\n{'='*60}\ncheck-logic  project={project}\n{'='*60}")

    checker = SpecLogicChecker(project)
    result = checker.scan_python_files()
    checker.print_report(result)

    if args.srs and Path(args.srs).exists():
        print(f"\n{'─'*60}")
        print("Semantic Validation (SRS)")
        print(f"{'─'*60}")
        validator = SemanticValidator(args.srs)
        print(f"  Requirements: {len(validator.requirements)}")
        for fr_id, req in list(validator.requirements.items())[:5]:
            print(f"  {fr_id}: {req.get('description', '?')[:60]}...")

    return 0 if result.passed else 1


# ---------------------------------------------------------------------------
# init-project
# ---------------------------------------------------------------------------

def _harness_workflow_template(phase: int) -> str:
    """Return the content of .github/workflows/harness_quality_gate.yml for a target project.

    Kept in sync with templates/harness_quality_gate.yml and INTEGRATION.md §4 Option A.
    """
    return f"""\
# Harness Quality Gate — auto-generated by harness-methodology init-project
# Kept in sync with INTEGRATION.md §4 Option A.
#
# Configure (GitHub repo → Settings):
#   CURRENT_PHASE   → Variables → Actions variables
name: Harness Quality Gate

on:
  pull_request:
    branches: [main]

jobs:
  gate-check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          submodules: true

      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'

      - name: Install harness dependencies
        run: pip install -r harness/requirements.txt || true

      - name: Run Phase Preflight (FSM / drift / constitution)
        # Structural enforcement only (FSM state, constitution, drift, traceability).
        # Gate score evaluation requires an interactive Claude session — always local.
        # Gate 4 (P6 exit) also requires human Hermes APPROVE — run locally, not in CI.
        if: vars.CURRENT_PHASE != '6'
        env:
          PHASE: ${{{{ vars.CURRENT_PHASE || '{phase}' }}}}
        run: python harness/harness_cli.py run-phase --phase $PHASE --project .

      - name: FR Traceability Check
        run: python harness/scripts/check_fr_full.py --phase ${{{{ vars.CURRENT_PHASE || '{phase}' }}}}
        continue-on-error: true
"""


def _init_phase_dirs(project: Path) -> None:
    """Create canonical 0X-name/ phase directory structure in target project."""
    dirs = [
        "01-requirements",
        "02-architecture/adr",
        "03-development/src",
        "03-development/tests",
        "04-testing",
        "05-verify",
        "06-quality",
        "07-risk",
        "08-config",
    ]
    created = 0
    skipped = 0
    for d in dirs:
        target = project / d
        if target.exists():
            skipped += 1
        else:
            target.mkdir(parents=True, exist_ok=True)
            created += 1
    if created:
        print(f"   OK — created {created} director{'y' if created == 1 else 'ies'} ({skipped} already existed)")
    else:
        print(f"   SKIP: all {skipped} directories already exist")


def _init_copy_templates(project: Path, harness_root: Path, *, force: bool = False) -> None:
    """Copy artifact templates from harness templates/ into the target project."""
    templates_dir = harness_root / "templates"
    artifact_map = [
        ("01-requirements", "SRS.md"),
        ("01-requirements", "SPEC_TRACKING.md"),
        ("01-requirements", "TRACEABILITY_MATRIX.md"),
        ("02-architecture", "SAD.md"),
        ("02-architecture/adr", "ADR.md"),
    ]
    copied = 0
    skipped = 0
    missing = 0
    for subdir, filename in artifact_map:
        src = templates_dir / filename
        dst = project / subdir / filename
        if dst.exists() and not force:
            skipped += 1
        elif src.exists():
            shutil.copy2(src, dst)
            copied += 1
        else:
            print(f"   WARNING: template not found: {src}")
            missing += 1

    # CLAUDE.md.template → project/CLAUDE.md (only if no CLAUDE.md exists)
    claude_tmpl = harness_root / "CLAUDE.md.template"
    claude_dst = project / "CLAUDE.md"
    if claude_dst.exists() and not force:
        skipped += 1
    elif claude_tmpl.exists():
        shutil.copy2(claude_tmpl, claude_dst)
        copied += 1
    else:
        missing += 1

    parts = []
    if copied:
        parts.append(f"copied {copied} template{'s' if copied != 1 else ''}")
    if skipped:
        parts.append(f"{skipped} already existed")
    if missing:
        parts.append(f"{missing} template(s) not found")
    if parts:
        print(f"   OK — {', '.join(parts)}")
    else:
        print(f"   SKIP: nothing to copy")


def cmd_init_project(args: argparse.Namespace) -> int:
    """
    Initialize harness CI wiring in a target project (Context B setup).

    Automates INTEGRATION.md §3 steps:
      1. Verify harness is importable from the target project
      2. Write .github/workflows/harness_quality_gate.yml
      3. Optionally run setup-git-hooks.sh
      4. Set git config quality.phase
      5. Print drift monitor crontab suggestion
    """
    import subprocess  # imported here (not at module level) to keep startup cost low

    project = Path(args.project).resolve()
    phase = args.phase
    harness_root = Path(__file__).parent.resolve()

    print(f"\n{'='*60}")
    print(f"init-project  target={project}  phase={phase}")
    print(f"{'='*60}")

    # 1. Verify harness is importable
    print("\n[1/6] Checking harness importability...")
    importable = (
        (project / "harness" / "core" / "quality_gate" / "__init__.py").exists()
        or (project / "core" / "quality_gate" / "__init__.py").exists()
        or (project / "harness_cli.py").exists()
        or (project / "harness" / "harness_cli.py").exists()
    )
    if importable:
        print("   OK — harness is importable")
    else:
        print("   WARNING: harness not found in target project.")
        print(f"   Run:  git submodule add {harness_root} {project}/harness")
        print(f"   Or:   export PYTHONPATH=\"{harness_root}:$PYTHONPATH\"")
        if not args.force:
            return 1

    # 2. Write CI workflow
    print("\n[2/6] Writing CI workflow...")
    workflows_dir = project / ".github" / "workflows"
    workflows_dir.mkdir(parents=True, exist_ok=True)
    workflow_path = workflows_dir / "harness_quality_gate.yml"
    if workflow_path.exists() and not args.force:
        print(f"   SKIP: {workflow_path} already exists (use --force to overwrite)")
    else:
        workflow_path.write_text(_harness_workflow_template(phase))
        print(f"   OK — wrote {workflow_path}")

    # 3. Git hooks
    print("\n[3/6] Git hooks...")
    hooks_script = harness_root / "scripts" / "setup-git-hooks.sh"
    if args.ci_only:
        print("   SKIP: --ci-only flag set (hooks not installed)")
    elif not hooks_script.exists():
        print(f"   WARNING: {hooks_script} not found — skipping hooks")
    else:
        hooks_dir = project / ".git" / "hooks"
        if (hooks_dir / "prepare-commit-msg").exists() and not args.force:
            print("   SKIP: hooks already installed (use --force to reinstall)")
        else:
            result = subprocess.run(
                ["bash", str(hooks_script)],
                cwd=str(project),
                input=f"{phase}\ny\n",  # auto-answer prompts
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                print("   OK — git hooks installed")
            else:
                print(f"   WARNING: hook install failed:\n{result.stderr[-500:]}")

    # 4. Set git config
    print("\n[4/6] Git config...")
    gc = subprocess.run(
        ["git", "-C", str(project), "config", "--local", "quality.phase", str(phase)],
        capture_output=True,
        text=True,
    )
    if gc.returncode == 0:
        print(f"   OK — quality.phase = {phase}")
    else:
        print(f"   WARNING: git config failed (rc={gc.returncode}): {gc.stderr.strip()}")

    # 5. Create canonical phase directory structure
    print("\n[5/8] Creating phase directory structure...")
    _init_phase_dirs(project)

    # 6. Copy template artifacts into phase directories
    print("\n[6/8] Copying artifact templates...")
    _init_copy_templates(project, harness_root, force=args.force)

    # 7. Initialize FSM state.json (required by run-phase preflight)
    print("\n[7/8] Initializing FSM state...")
    from datetime import datetime, timezone
    state_path = project / ".methodology" / "state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    if state_path.exists() and not args.force:
        print(f"   SKIP: {state_path} already exists (use --force to overwrite)")
    else:
        state_path.write_text(
            json.dumps({
                "state": "ACTIVE",
                "current_phase": phase,
                "last_gate": None,
                "last_fr": None,
                "last_update": datetime.now(timezone.utc).isoformat(),
            }, indent=2),
            encoding="utf-8",
        )
        print(f"   OK — state.json initialized (phase={phase})")

    # 8. Drift monitor hint
    print(f"\n[8/8] Drift Monitor hint (optional cronjob)")
    print("  Add this crontab entry (edit with: crontab -e):")
    print(f"  0 * * * * DRIFT_PROJECT_PATH={project} \\")
    print(f"    python3 {harness_root}/scripts/cron_drift_monitor.py \\")
    print(f"    >> {project}/logs/drift_monitor.log 2>&1")

    print(f"\n{'='*60}")
    print("init-project complete.")
    print(f"{'='*60}")
    print(f"  Next: Set CURRENT_PHASE = {phase} in GitHub repo → Settings → Variables")
    print(f"  Docs: {harness_root}/INTEGRATION.md")
    return 0


def cmd_audit_structure(args: argparse.Namespace) -> int:
    """Audit target project directory structure and artifact completeness.

    Checks all 8 phases:
      1. Directory existence (01-requirements/ ... 08-config/)
      2. Artifact completeness (required files per phase)
      3. Content quality (no hollow templates)
      4. ASPICE traceability chain (cross-phase references)
      5. Naming convention compliance (0X-name/ format)
    """
    import json as _json
    import re as _re

    project = Path(args.project).resolve()

    # Canonical phase directory names
    PHASE_DIRS = {
        1: "01-requirements", 2: "02-architecture",
        3: "03-development",   4: "04-testing",
        5: "05-verify",        6: "06-quality",
        7: "07-risk",          8: "08-config",
    }

    # Required artifacts per phase (aligned with phase_artifact_enforcer.py)
    PHASE_ARTIFACTS = {
        1: ["01-requirements/SRS.md", "01-requirements/SPEC_TRACKING.md",
            "01-requirements/TRACEABILITY_MATRIX.md"],
        2: ["02-architecture/SAD.md"],
        3: ["03-development/src/", "03-development/tests/",
            "03-development/COMPLIANCE_MATRIX.md"],
        4: ["04-testing/TEST_PLAN.md", "04-testing/TEST_RESULTS.md"],
        5: ["05-verify/BASELINE.md", "05-verify/VERIFICATION_REPORT.md"],
        6: ["06-quality/QUALITY_REPORT.md"],
        7: ["07-risk/RISK_ASSESSMENT.md", "07-risk/RISK_REGISTER.md"],
        8: ["08-config/CONFIG_RECORDS.md", "08-config/RELEASE_CHECKLIST.md"],
    }

    results = {
        "project": str(project),
        "dimensions": {},
    }

    # --- Dimension 1: Directory existence ---
    dir_status = {}
    for num, dname in PHASE_DIRS.items():
        dpath = project / dname
        dir_status[f"P{num}"] = {
            "dir": dname,
            "exists": dpath.is_dir(),
            "path": str(dpath),
        }
    results["dimensions"]["directory_existence"] = {
        "label": "Directory Existence (01-requirements/ ~ 08-config/)",
        "passed": all(v["exists"] for v in dir_status.values()),
        "details": dir_status,
    }

    # --- Dimension 2: Artifact completeness ---
    artifact_status = {}
    for phase_num, paths in PHASE_ARTIFACTS.items():
        phase_key = f"P{phase_num}"
        phase_files = []
        for p in paths:
            fpath = project / p
            exists = fpath.exists()
            size = fpath.stat().st_size if exists and fpath.is_file() else None
            phase_files.append({"path": p, "exists": exists, "size_bytes": size})
        artifact_status[phase_key] = {
            "dir": PHASE_DIRS[phase_num],
            "all_present": all(f["exists"] for f in phase_files),
            "files": phase_files,
        }
    results["dimensions"]["artifact_completeness"] = {
        "label": "Artifact Completeness",
        "passed": all(v["all_present"] for v in artifact_status.values()),
        "details": artifact_status,
    }

    # --- Dimension 3: Content quality ---
    # FR-reference check applies only to phases 1–4 (phases 5–8 produce
    # operational docs that legitimately contain no FR/NFR references).
    _FR_REF_PHASES = {1, 2, 3, 4}

    def _check_content_quality(fpath: Path, phase_num: int = 0) -> dict:
        if not fpath.exists() or not fpath.is_file():
            return {"quality": "missing"}
        try:
            content = fpath.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return {"quality": "unreadable"}
        issues = []
        if len(content.strip()) < 200:
            issues.append("content < 200 chars")
        if content.count("\n## ") + content.count("\n# ") < 2:
            issues.append("< 2 markdown sections")
        if phase_num in _FR_REF_PHASES and not _re.search(
            r"\[(TASK|FR|NFR)-\d+\]", content, _re.IGNORECASE
        ):
            issues.append("no [TASK/FR/NFR-XX] references")
        return {"quality": "good" if not issues else "suspicious", "issues": issues}

    quality_status = {}
    for phase_num, paths in PHASE_ARTIFACTS.items():
        phase_key = f"P{phase_num}"
        phase_quality = []
        for art_path in paths:
            q = _check_content_quality(project / art_path, phase_num)
            q["path"] = art_path
            phase_quality.append(q)
        all_ok = all(q["quality"] == "good" for q in phase_quality
                     if not q["path"].endswith("/"))
        quality_status[phase_key] = {
            "dir": PHASE_DIRS[phase_num],
            "all_quality_ok": all_ok,
            "files": phase_quality,
        }
    results["dimensions"]["content_quality"] = {
        "label": "Content Quality (non-hollow templates)",
        "passed": all(v["all_quality_ok"] for v in quality_status.values()),
        "details": quality_status,
    }

    # --- Dimension 4: ASPICE traceability chain ---
    try:
        from core.quality_gate.phase_artifact_enforcer import PhaseArtifactRegistry
        chain_result = PhaseArtifactRegistry(str(project)).verify_phase_chain(8)
        aspice_passed = chain_result["all_verified"]
        aspice_detail = {
            "all_verified": aspice_passed,
            "stats": chain_result["stats"],
            "missing_links": chain_result.get("missing_links", []),
        }
    except Exception as exc:
        aspice_passed = False
        aspice_detail = {"error": str(exc)}
    results["dimensions"]["aspice_chain"] = {
        "label": "ASPICE Traceability Chain (P1→P8)",
        "passed": aspice_passed,
        "details": aspice_detail,
    }

    # --- Dimension 5: Naming convention ---
    naming_issues = []
    expected_names = set(PHASE_DIRS.values())
    found_dirs = set()
    for child in project.iterdir():
        if not child.is_dir():
            continue
        m = _re.match(r"^\d{2}-", child.name)
        if m:
            found_dirs.add(child.name)
            if child.name not in expected_names:
                naming_issues.append(
                    f"unexpected directory '{child.name}' "
                    f"(expected one of: {', '.join(sorted(expected_names))})"
                )
    missing = expected_names - found_dirs
    if missing:
        naming_issues.append(
            f"missing directories: {', '.join(sorted(missing))}"
        )
    naming_passed = len(naming_issues) == 0
    results["dimensions"]["naming_convention"] = {
        "label": "Naming Convention (0X-name/ format)",
        "passed": naming_passed,
        "details": {"issues": naming_issues},
    }

    # --- Summary ---
    dims = results["dimensions"]
    all_passed = all(d["passed"] for d in dims.values())
    results["summary"] = {
        "all_passed": all_passed,
        "pass_count": sum(1 for d in dims.values() if d["passed"]),
        "total_dims": len(dims),
    }

    if args.json:
        print(_json.dumps(results, indent=2, ensure_ascii=False))
    else:
        _print_audit_report(results)

    return 0 if all_passed else 1


def _print_audit_report(results: dict) -> None:
    """Print human-readable audit-structure report."""
    print(f"\n{'='*60}")
    print(f"Audit-Structure Report")
    print(f"Project: {results['project']}")
    print(f"{'='*60}")

    dims = results["dimensions"]
    for key, dim in dims.items():
        icon = "PASS" if dim["passed"] else "FAIL"
        print(f"\n  [{icon}] {dim['label']}")

        if key == "directory_existence":
            for pk, dv in dim["details"].items():
                mark = "✅" if dv["exists"] else "❌"
                print(f"     {mark} {pk}  {dv['dir']}")

        elif key == "artifact_completeness":
            for pk, pv in dim["details"].items():
                mark = "✅" if pv["all_present"] else "❌"
                print(f"     {mark} {pk} ({pv['dir']})")
                if not pv["all_present"]:
                    for f in pv["files"]:
                        if not f["exists"]:
                            print(f"        ❌ MISSING: {f['path']}")

        elif key == "content_quality":
            for pk, pv in dim["details"].items():
                mark = "✅" if pv["all_quality_ok"] else "⚠️"
                print(f"     {mark} {pk} ({pv['dir']})")
                for f in pv["files"]:
                    if f["quality"] != "good" and not f["path"].endswith("/"):
                        issues = ", ".join(f.get("issues", []))
                        print(f"        ⚠️  {f['path']}: {f['quality']}"
                              + (f" ({issues})" if issues else ""))

        elif key == "aspice_chain":
            stats = dim["details"].get("stats", {})
            print(f"     Verified: {stats.get('verified', '?')}/{stats.get('total', '?')} links")
            for link in dim["details"].get("missing_links", [])[:5]:
                print(f"        ❌ {link}")

        elif key == "naming_convention":
            if dim["passed"]:
                print("     ✅ All 0X-name/ directories match expected names")
            else:
                for issue in dim["details"]["issues"]:
                    print(f"        ❌ {issue}")

    # Footer
    s = results["summary"]
    print(f"\n{'='*60}")
    if s["all_passed"]:
        print(f"RESULT: ALL PASS ({s['pass_count']}/{s['total_dims']} dimensions)")
    else:
        print(f"RESULT: FAIL — {s['total_dims'] - s['pass_count']} dimension(s) failed")
    print(f"{'='*60}")


# ---------------------------------------------------------------------------
# CLI wiring
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    """Construct the ArgumentParser for the CLI."""
    p = argparse.ArgumentParser(
        prog="harness_cli.py",
        description="Harness-methodology standalone CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = p.add_subparsers(dest="command", metavar="command")
    sub.required = True

    # plan-phase
    help_plan = "Generate phase execution plan from SRS/SAD artifacts (stdlib only)"
    pp = sub.add_parser("plan-phase", help=help_plan)
    pp.add_argument("--phase",  type=int, required=True, help="Phase number (1-8)")
    pp.add_argument("--project", default=".", help="Project root path (default: .)")
    pp.add_argument("--output", default=None, help="Output file path (default: stdout)")
    pp.set_defaults(func=cmd_plan_phase)

    # run-phase
    rp = sub.add_parser("run-phase", help="Run pre/post-flight hooks for a phase")
    rp.add_argument("--phase",   type=int, required=True, help="Phase number (1-8)")
    rp.add_argument("--project", default=".", help="Project root (default: .)")
    rp.add_argument("--force",   action="store_true", help="Ignore preflight failures")
    rp.add_argument("--fast",    action="store_true", help="Lightweight preflight (skip drift/traceability/gap/CI)")
    rp.set_defaults(func=cmd_run_phase)

    # push-checkpoint (P1/P2 human review → git push + HANDOVER.md)
    pc = sub.add_parser(
        "push-checkpoint",
        help="Push P1/P2 human-review checkpoint (writes HANDOVER.md, commits, pushes)",
    )
    pc.add_argument("--phase",   type=int, required=True, choices=[1, 2],
                    help="Phase number (1 or 2)")
    pc.add_argument("--project", default=".", help="Project root (default: .)")
    pc.add_argument("--fr-ids",  default="", dest="fr_ids",
                    help="Comma-separated FR IDs (e.g., FR-01,FR-02)")
    pc.add_argument("--no-git", action="store_true", dest="no_git",
                    help="Disable git commit/push (HANDOVER.md still written)")
    pc.set_defaults(func=cmd_push_checkpoint)

    # run-gate (Phase 1: prepare + print evaluation prompt)
    rg = sub.add_parser("run-gate", help="Prepare gate evaluation; print prompt for Claude")
    rg.add_argument("--gate",    type=int, required=True, choices=[1, 2, 3, 4])
    rg.add_argument("--phase",   type=int, required=True, help="Current phase number")
    rg.add_argument("--project", default=".", help="Project root (default: .)")
    rg.add_argument("--fr-id",   default=None, help="FR ID (Gate 1 only)", dest="fr_id")
    rg.add_argument("--skip-preflight", action="store_true", help="Skip preflight validation before gate (Item 9)")
    rg.set_defaults(func=cmd_run_gate)

    # finalize-gate (Phase 2: read result.json, check thresholds, git)
    fg = sub.add_parser(
        "finalize-gate",
        help="Finalize gate after Claude evaluation; checks thresholds and commits",
    )
    fg.add_argument("--gate",    type=int, required=True, choices=[1, 2, 3, 4])
    fg.add_argument("--phase",   type=int, required=True, help="Current phase number")
    fg.add_argument("--project", default=".", help="Project root (default: .)")
    fg.add_argument("--fr-id",   default=None, help="FR ID (Gate 1 only)", dest="fr_id")
    fg.add_argument("--no-git",  action="store_true", dest="no_git",
                    help="Disable git commit/push after gate pass")
    fg.set_defaults(func=cmd_finalize_gate)

    # generate-next-plan (checkpoint-based tactical plan generator)
    gnp = sub.add_parser(
        "generate-next-plan",
        help="Read manifest state and emit the next concrete gate evaluation plan",
    )
    gnp.add_argument("--project", default=".", help="Project root (default: .)")
    gnp.add_argument("--phase",   type=int, default=None, help="Override current phase")
    gnp.set_defaults(func=cmd_generate_next_plan)

    # run-gap-analysis (M3)
    ga = sub.add_parser(
        "run-gap-analysis",
        help="M3: Detect gaps between SPEC.md and codebase implementation",
    )
    ga.add_argument("--project",    default=".", help="Project root (default: .)")
    ga.add_argument("--spec",       default="SPEC.md", help="Path to SPEC.md")
    ga.add_argument("--similarity", type=float, default=0.6,
                    help="Similarity threshold for matching (default: 0.6)")
    ga.set_defaults(func=cmd_run_gap_analysis)

    # run-pipeline
    rpl = sub.add_parser(
        "run-pipeline",
        help="Full autonomous pipeline P{from}→P{to} with checkpoint-based gate evaluation",
    )
    rpl.add_argument("--phase-from", type=int, default=1, metavar="N", dest="phase_from",
                     help="Start phase (default: 1)")
    rpl.add_argument("--phase-to",   type=int, default=8, metavar="N", dest="phase_to",
                     help="End phase (default: 8)")
    rpl.add_argument("--project",    default=".", help="Project root (default: .)")
    rpl.add_argument("--force", action="store_true", help="Ignore preflight failures")
    rpl.add_argument("--no-git", action="store_true", dest="no_git",
                     help="Disable all git commit/push operations")
    rpl.add_argument("--no-kill-switch", action="store_true", dest="no_kill_switch",
                     help="Disable M1 kill-switch circuit breaker")
    rpl.add_argument("--drift-threshold", type=float, default=85.0, dest="drift_threshold",
                     help="M2 drift detection ensemble score threshold (default: 85.0)")
    rpl.add_argument("--auto-fix-rounds", type=int, default=3, dest="auto_fix_rounds",
                     help="Max auto-fix rounds per problem (default: 3, max: 5)")
    rpl.add_argument("--no-auto-fix", action="store_true", dest="no_auto_fix",
                     help="Disable all auto-fix; fall back to detect→block→wait_for_human")
    rpl.add_argument("--watch", action="store_true",
                     help="Enable config hot-reload: watch SKILL.md for YAML frontmatter changes (Item 2)")
    rpl.set_defaults(func=cmd_run_pipeline)

    # manifest
    mf = sub.add_parser("manifest", help="Generate quality_manifest.json at P2 exit")
    mf.add_argument("--fr-ids", nargs="+", required=True, metavar="FR_ID")
    mf.add_argument("--sad",    default="02-architecture/SAD.md", help="Path to SAD.md")
    mf.add_argument("--no-git", action="store_true", dest="no_git",
                    help="Disable git commit/push after manifest generation")
    mf.set_defaults(func=cmd_manifest)

    # status
    st = sub.add_parser("status", help="Show current manifest + FSM state")
    st.add_argument("--project", default=".", help="Project root (default: .)")
    st.add_argument("--json", action="store_true", help="Machine-readable JSON output")
    st.add_argument("--full", action="store_true", help="Include test stats and auto-fix rounds")
    st.set_defaults(func=cmd_status)

    # effort
    ef = sub.add_parser("effort", help="Show gate effort metrics summary")
    ef.add_argument("--phase",   type=int, default=None, help="Filter by phase")
    ef.add_argument("--project", default=".", help="Project root (default: .)")
    ef.set_defaults(func=cmd_effort)

    # advance-phase
    adv = sub.add_parser(
        "advance-phase",
        help="Advance to next phase: sync quality.phase + GitHub CURRENT_PHASE atomically",
    )
    adv.add_argument(
        "--completed", type=int, required=True, dest="completed_phase",
        help="Phase number that just completed (advance-phase --completed 3 → sets phase 4)",
    )
    adv.add_argument("--project", default=".", help="Project root (default: .)")
    adv.set_defaults(func=cmd_advance_phase)

    # reload-policy
    rl = sub.add_parser("reload-policy", help="Hot-reload enforcement policies from enforcement.json")
    rl.add_argument(
        "--policy-file",
        default="enforcement/enforcement.json",
        help="Path to enforcement.json (default: enforcement/enforcement.json)",
    )
    rl.set_defaults(func=cmd_reload_policy)

    # audit-phase
    ap = sub.add_parser(
        "audit-phase",
        help="Audit a phase against GitHub artifacts (8-dimension PhaseAuditor check)",
    )
    ap.add_argument("--phase",  type=int, required=True, help="Phase number to audit (1-8)")
    ap.add_argument("--repo",   required=True,
                    help="GitHub repo in owner/repo format (e.g. johnnylugm-tech/my-project)")
    ap.add_argument("--branch", default="main", help="Target branch (default: main)")
    ap.add_argument("--output", choices=["markdown", "json"], default="markdown",
                    help="Output format (default: markdown)")
    ap.add_argument("--save",   default=None, metavar="FILE",
                    help="Save report to file")
    ap.set_defaults(func=cmd_audit_phase)

    # verify-spec
    vs = sub.add_parser(
        "verify-spec",
        help="Verify implementation complies with spec requirements (6-dimension check)",
    )
    vs.add_argument("--project", default=".", help="Project root (default: .)")
    vs.add_argument("--fix", action="store_true",
                    help="Show fix suggestions for each issue (no auto-fix)")
    vs.set_defaults(func=cmd_verify_spec)

    # check-logic
    cl = sub.add_parser(
        "check-logic",
        help="Check code for logic correctness (output/branch/lazy-init/semantic)",
    )
    cl.add_argument("--project", default=".", help="Project root (default: .)")
    cl.add_argument("--srs",     default=None, help="SRS.md path for semantic validation")
    cl.set_defaults(func=cmd_check_logic)

    # init-project
    ip = sub.add_parser(
        "init-project",
        help="Initialize harness CI wiring in a target project (Context B one-shot setup)",
    )
    ip.add_argument("--project", required=True, help="Target project root path")
    ip.add_argument("--phase",   type=int, default=1, help="Current phase (default: 1)")
    ip.add_argument("--ci-only", action="store_true",
                    help="Write CI workflow only; skip git hooks")
    ip.add_argument("--force",   action="store_true",
                    help="Overwrite existing CI workflow and hooks")
    ip.set_defaults(func=cmd_init_project)

    # audit-structure
    aus = sub.add_parser(
        "audit-structure",
        help="Audit target project directory structure and artifact completeness",
    )
    aus.add_argument("--project", required=True, help="Target project root path")
    aus.add_argument("--json", action="store_true", help="Output as JSON")
    aus.set_defaults(func=cmd_audit_structure)

    return p


def main() -> int:
    """Main entry point for the CLI."""
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
