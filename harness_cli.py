#!/usr/bin/env python3
"""
harness_cli.py — Standalone CLI for harness-methodology
=========================================================

Standalone entrypoint for the harness-methodology repo.
Does NOT require the full parent system (cli.py needs 30+ external modules).

Usage:
    python harness_cli.py run-phase  --phase 3 --project .
    python harness_cli.py run-gate   --gate 2  --phase 3 --project .
    python harness_cli.py manifest   --fr-ids FR-01 FR-02 --sad SAD.md
    python harness_cli.py status     [--project .]
    python harness_cli.py effort     [--phase 3] [--project .]

Available gates:
    Gate 1  per-FR check       (P3/P5/P7/P8, trigger: per_fr_completion)
    Gate 2  P3 phase-exit      (score_gate: 75, 7 dims)
    Gate 3  P4 phase-exit      (score_gate: 80, 12 dims, full CRG)
    Gate 4  P6 full-project    (score_gate: 85, 12 dims, Hermes APPROVE required)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Ensure repo root on path so core/ and harness/ resolve
_REPO_ROOT = Path(__file__).parent
sys.path.insert(0, str(_REPO_ROOT))


# ---------------------------------------------------------------------------
# run-phase
# ---------------------------------------------------------------------------

def cmd_run_phase(args: argparse.Namespace) -> int:
    from core.phase_hooks import PhaseHooks

    project = Path(args.project).resolve()
    hooks = PhaseHooks(str(project), phase=args.phase)

    print(f"\n{'='*60}\nrun-phase: Phase {args.phase}\n{'='*60}")

    pre = hooks.preflight_all()
    if not pre["all_passed"] and not args.force:
        print(f"\nPRE-FLIGHT FAILED: {pre['details']}")
        print("Use --force to override preflight failures.")
        return 1

    print("\n[INFO] Phase execution hooks ready.")
    print("[INFO] Spawn your developer/reviewer agents and call:")
    print("       hooks.monitoring_before_dev(fr_id)")
    print("       hooks.monitoring_after_dev(fr_id, result)")
    print("       hooks.monitoring_before_rev(fr_id)")
    print("       hooks.monitoring_after_rev(fr_id, result)")
    print("       hooks.postflight_all()")
    return 0


# ---------------------------------------------------------------------------
# run-gate
# ---------------------------------------------------------------------------

def cmd_run_gate(args: argparse.Namespace) -> int:
    from harness.harness_bridge import HarnessBridge, GateBlockedError

    project = str(Path(args.project).resolve())
    bridge = HarnessBridge()

    print(f"\n{'='*60}\nrun-gate: Gate {args.gate} | Phase {args.phase}\n{'='*60}")

    fr_id = args.fr_id or None
    try:
        result = bridge.run_gate(
            gate_num=args.gate,
            project_root=project,
            phase=args.phase,
            fr_id=fr_id,
        )
        print(f"\nGATE {args.gate} PASSED")
        print(f"  score         : {result.score:.1f}")
        print(f"  quality_complete: {result.quality_complete}")
        print(f"  rounds_used   : {result.rounds_used}")
        print(f"  open_critical : {result.open_critical}")
        print(f"  open_high     : {result.open_high}")
        return 0

    except GateBlockedError as e:
        print(f"\nGATE {args.gate} BLOCKED")
        print(f"  {e}")
        for dim in e.result.dimensions:
            status = "PASS" if dim.score >= dim.threshold else "FAIL"
            print(f"  [{status}] {dim.name}: {dim.score:.1f} (threshold={dim.threshold})")
        return 1

    except NotImplementedError as e:
        print(f"\n[ERROR] {e}")
        print("  Install software_self_improvement and set PYTHONPATH to enable gate runs.")
        print("  See: docs/HARNESS_INTEGRATION.md (in software_self_improvement repo)")
        return 2

    except RuntimeError as e:
        print(f"\n[RUNTIME ERROR] {e}")
        return 2


# ---------------------------------------------------------------------------
# manifest
# ---------------------------------------------------------------------------

def cmd_manifest(args: argparse.Namespace) -> int:
    from harness.harness_bridge import HarnessBridge

    bridge = HarnessBridge()
    out = bridge.generate_quality_manifest(
        fr_ids=args.fr_ids,
        sad_path=args.sad,
    )
    print(f"quality_manifest.json written → {out}")
    manifest = json.loads(out.read_text())
    print(f"  fr_ids        : {manifest['fr_ids']}")
    print(f"  generated_at  : phase {manifest['generated_at_phase']}")
    return 0


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------

def cmd_status(args: argparse.Namespace) -> int:
    project = Path(args.project).resolve()
    manifest_path = project / ".methodology" / "quality_manifest.json"
    state_path    = project / ".methodology" / "state.json"

    print(f"\n{'='*60}\nHarness Status: {project}\n{'='*60}")

    if state_path.exists():
        state = json.loads(state_path.read_text())
        print(f"\n[FSM State]")
        print(f"  state         : {state.get('state', 'UNKNOWN')}")
        print(f"  current_phase : {state.get('current_phase', 0)}")
        print(f"  last_update   : {state.get('last_update', '-')}")
    else:
        print("\n[FSM State] .methodology/state.json not found (project not initialised)")

    if manifest_path.exists():
        m = json.loads(manifest_path.read_text())
        print(f"\n[Quality Manifest]")
        print(f"  schema_version: {m.get('schema_version')}")
        print(f"  fr_ids        : {m.get('fr_ids')}")
        gates = m.get("gate_results", {})
        for g, v in gates.items():
            if v is None:
                print(f"  {g}           : not run")
            elif isinstance(v, dict) and "score" in v:
                print(f"  {g}           : score={v['score']} complete={v['quality_complete']}")
            elif isinstance(v, dict):
                # Gate 1: per-FR dict
                for fr, r in v.items():
                    print(f"  {g}/{fr}  : score={r['score']} complete={r['quality_complete']}")
    else:
        print("\n[Quality Manifest] Not found — run `harness_cli.py manifest` first")

    return 0


# ---------------------------------------------------------------------------
# effort
# ---------------------------------------------------------------------------

def cmd_effort(args: argparse.Namespace) -> int:
    from harness.effort_tracker import EffortTracker

    tracker = EffortTracker()
    summary = tracker.summary(phase=args.phase)

    print(f"\n{'='*60}\nEffort Summary{' | Phase ' + str(args.phase) if args.phase else ''}\n{'='*60}")
    print(json.dumps(summary, indent=2))
    return 0


# ---------------------------------------------------------------------------
# CLI wiring
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="harness_cli.py",
        description="Harness-methodology standalone CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = p.add_subparsers(dest="command", metavar="command")
    sub.required = True

    # run-phase
    rp = sub.add_parser("run-phase", help="Run pre/post-flight hooks for a phase")
    rp.add_argument("--phase",   type=int, required=True, help="Phase number (1-8)")
    rp.add_argument("--project", default=".", help="Project root (default: .)")
    rp.add_argument("--force",   action="store_true", help="Ignore preflight failures")
    rp.set_defaults(func=cmd_run_phase)

    # run-gate
    rg = sub.add_parser("run-gate", help="Execute a quality gate (requires SSI installed)")
    rg.add_argument("--gate",    type=int, required=True, choices=[1, 2, 3, 4])
    rg.add_argument("--phase",   type=int, required=True, help="Current phase number")
    rg.add_argument("--project", default=".", help="Project root (default: .)")
    rg.add_argument("--fr-id",   default=None, help="FR ID (Gate 1 only)")
    rg.set_defaults(func=cmd_run_gate)

    # manifest
    mf = sub.add_parser("manifest", help="Generate quality_manifest.json at P2 exit")
    mf.add_argument("--fr-ids", nargs="+", required=True, metavar="FR_ID")
    mf.add_argument("--sad",    default="SAD.md", help="Path to SAD.md (default: SAD.md)")
    mf.set_defaults(func=cmd_manifest)

    # status
    st = sub.add_parser("status", help="Show current manifest + FSM state")
    st.add_argument("--project", default=".", help="Project root (default: .)")
    st.set_defaults(func=cmd_status)

    # effort
    ef = sub.add_parser("effort", help="Show gate effort metrics summary")
    ef.add_argument("--phase",   type=int, default=None, help="Filter by phase")
    ef.add_argument("--project", default=".", help="Project root (default: .)")
    ef.set_defaults(func=cmd_effort)

    return p


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
