#!/usr/bin/env python3
"""
harness_cli.py — Standalone CLI for harness-methodology.

Standalone entrypoint for the harness-methodology repo.
Does NOT require the full parent system (cli.py needs 30+ external modules).

Usage:
    python harness_cli.py plan-phase       --phase 3 [--project .] [--output plan.md]
    python harness_cli.py run-phase        --phase 3 [--project .]
    python harness_cli.py run-gate         --gate 2  --phase 3 [--project .] [--fr-id FR-01]
    python harness_cli.py finalize-gate    --gate 2  --phase 3 [--project .] [--fr-id FR-01]
    python harness_cli.py generate-next-plan [--project .] [--phase 3]
    python harness_cli.py manifest         --fr-ids FR-01 FR-02 [--sad SAD.md]
    python harness_cli.py status           [--project .]
    python harness_cli.py effort           [--phase 3] [--project .]
    python harness_cli.py reload-policy    [--policy-file enforcement/enforcement.json]
    python harness_cli.py run-gap-analysis  [--project .] [--spec SPEC.md] [--similarity 0.6]
    python harness_cli.py audit-phase       --phase 3 --repo owner/repo [--branch main]
    python harness_cli.py verify-spec       [--project .]
    python harness_cli.py check-logic       [--project .] [--srs SRS.md]
    python harness_cli.py init-project      --project /path/to/target [--phase 3] [--overwrite]
    python harness_cli.py push-checkpoint   --phase 1|2 --project . [--fr-ids FR-01,FR-02]
    python harness_cli.py push-milestone    --type p3-mid|p3-pre-gate2|p3-post-gate2|p4-mid|p4-pre-gate3|p5-baseline|p7|p8 --project .
    python harness_cli.py advance-phase     --completed-phase 3 [--project .]
    python harness_cli.py dispatch          --role developer|reviewer --fr-id FR-01 --prompt "..." --phase 3

Gate Evaluation (two-phase flow):
    1. run-gate    → prints evaluation prompt for Claude; exits 0
    2. Claude evaluates inline, writes .sessi-work/gate{N}_result.json
    3. finalize-gate → reads result, checks thresholds, commits

Available gates:
    Gate 1  per-FR check       (P3/P4/P5/P7/P8, trigger: per_fr_completion)
    Gate 2  P3 phase-exit      (score_gate: 75, 9 dims)
    Gate 3  P4 phase-exit      (score_gate: 80, 14 dims, full CRG)
    Gate 4  P6 full-project    (score_gate: 85, 14 dims)

Exit codes (single source of truth: cli/exit_codes.py's REGISTRY — this
section must match it exactly, enforced by tests/test_exit_code_registry.py):
    0   All phases complete
    1   Hard failure (investigate error)
    2   run-gap-analysis: critical gaps detected (distinct from hard error)
    5   Gate 4 prerequisites block (A2/A3/A5 schema, B2 score files)
    6   finalize-gate: gate passed but git commit did not land (manifest rolled back) — fix and re-run
    8   Missing deliverables block — required artifacts not found on disk or not git-tracked
    9   advance-phase: 100% coverage required on 03-development/src not met
    10  PAUSE — Claude must evaluate gate; run finalize-gate then re-run pipeline
    11  Phase Truth < 90% (HR-11); fix and re-run with --phase-from N
    12  advance-phase precondition block — phase_truth_passed missing OR SAB
        architecture violation (see printed message)
    13  advance-phase: Agent B approvals incomplete for this phase
    14  advance-phase: live pytest --cov could not run, or coverage below
        the manifest's recorded threshold
    15  advance-phase: next phase's plan file not found — run generate-next-plan first
    16  (retired 減法 T3 — constitution keyword scoring is on-demand only)
    17  advance-phase precondition block — finalize-gate not called for a
        required gate OR unresolved deferred_fixes.md items
    18  advance-phase precondition block — ruff linting failure OR
        submodule safety violation
    19  sync-harness: SubmoduleSyncError, OR advance-phase: mypy
        type-safety failure
    20  advance-phase: gitleaks secrets scan failed or timed out
    21  Scope violation: untracked diagnostic script(s) at repo root; move to
        .sessi-work/tmp or delete, then re-run advance-phase
    22  GHOST_DETECTED — agent claimed work but made no substantive code
        change (see .sessi-work/ghost_detected/)
    23  Sub-agent dispatch is structurally broken (e.g. claude.ai connectors
        disabled) — not a retryable failure
    24  run-phase: spawn-substrate preflight probe FAILED
    25  run-fr-step: INFRA_FAIL signature found in sub-agent output —
        aborted before dispatching a fix agent; repair project state
        (amend-sab) and re-run. A [HARNESS-BUG] banner in the same output
        is code 70, not this one: the remedies are opposite.
    26  [FATAL] .methodology/state.json or quality_manifest.json exists but
        is not readable/parseable JSON — project data corruption, NOT a
        harness-methodology bug (see core/state_io.py's StateCorruptError)
    27  advance-phase: quality_manifest.json parses but its structure is
        corrupt (truncated fr_ids / cleared traceability / wiped gate1) —
        refusing to commit it; restore from HEAD and re-run
    28  advance-phase --push: the handover commit landed locally but `git
        push` failed — NOT rolled back; fix connectivity/remote and re-run
        the push command printed in the [BLOCKED] message
    29  advance-phase (P1 exit): SRS.md's machine-readable NFR block uses a
        `type:` outside ALL_NFR_TYPES or a `dimension:` that names no scored
        dimension — fix the value in SRS.md; it is refused here rather than
        in Phase 2, where it would already be locked into an approved
        deliverable
    30  advance-phase: a deliverable's first line no longer starts with the H1
        anchor its path declares in DELIVERABLE_ANCHORS — the Phase 1/2
        orchestrator reloads it with that anchor and would abort after 3
        attempts; fix the H1 in the named file
    31  verify-ci: GitHub Actions reports at least one failing run for the
        pushed commit — the push landed, the build did not; fix the named
        job(s) and re-push before advancing
    32  verify-ci: the CI verdict could not be obtained (no gh, no network, no
        origin remote, or no run has appeared yet) — INFRA, not a pass;
        re-run once CI has reported
    33  verify-gate: at least one of the gate's three checks (last_gate,
        spec-coverage, crg-arch) failed — the verdict is recorded as FAIL in
        .methodology/gate_verify.jsonl; fix the named check and re-run
    34  advance-phase: the exit gate has no PASS verdict recorded for the tree
        being advanced — run verify-gate against this tree; a verdict measured
        on a different tree is not a verdict for this one
    35  run-fr-step: the step correctly did nothing because its precondition
        was not met (a refactor step cannot run on a red baseline) — not an
        agent-logic error; repair the named baseline failure, or revert the
        step that produced it, then re-run
    36  run-fr-step: this (FR, step) pair has already failed with an identical
        signature as many times as the in-process retry allows — refusing to
        spend another dispatch on a failure that has not changed; read
        .methodology/degradations.jsonl for the signature
    37  advance-phase: the preflight simulated at the phase being entered
        reports findings that would block entry there — the [BLOCKED] table
        names each one by check, rule and file:line. state.json was NOT
        advanced; resolve the listed findings and re-run
    38  advance-phase: delivered files differ from HEAD, so the commit about to
        record this phase does not contain the tree the phase's checks were
        measured on — the [BLOCKED] list names each file. Commit the listed
        work (or gitignore it, if it is generated at runtime) and re-run
    39  run-gate: .methodology/harness_config.json still switches a dimension
        off (features.<key>: false). No dimension can be excluded from a gate
        any more — remove the named key; a tool that genuinely cannot run here
        is an INFRA block with a repair route, not a scoring exemption
    70  [HARNESS-BUG] — a defect in harness-methodology's own code: an
        uncaught exception at the crash boundary (core/errors.py), or the
        same banner surfacing through a sub-agent's GATE1 output
        (run-fr-step). Not a project quality failure; no re-run clears it.
    130 Interrupted (Ctrl-C)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Fail fast with a clear message when an unsupported Python is used.
# Common mistake: agents or shells use /usr/bin/python3 (macOS system 3.9)
# instead of the project's .venv/bin/python. The error otherwise propagates
# as a cryptic ImportError deep inside the call stack.
if sys.version_info < (3, 10):  # type: ignore[reportUnreachable]
    print(
        f"ERROR: harness-methodology requires Python 3.10+. "
        f"Got {sys.version.split()[0]} at {sys.executable}\n"
        "  Fix: run with .venv/bin/python or python3.10+ "
        "instead of /usr/bin/python3 (macOS system Python 3.9)"
    )
    sys.exit(1)

# Ensure repo root on path so core/ and harness/ resolve
# Ensure repo root on path so core/ and harness/ resolve
_REPO_ROOT = Path(__file__).parent
sys.path.insert(0, str(_REPO_ROOT))

# --- core imports (after the sys.path bootstrap → E402 by design) ---
from core.phase_topology import (  # noqa: E402
    ADVANCE_GATE1_CHECK_PHASES as _TOPOLOGY_ADVANCE_GATE1,
    ENTRY_GATE_MAP as _TOPOLOGY_ENTRY_GATES,
    EXIT_GATE_MAP as _TOPOLOGY_EXIT_GATES,
    PER_FR_GATE1_PHASES as _TOPOLOGY_PER_FR_GATE1,
)
from core.canonical_form import canonical_form, fr_num_str  # noqa: E402, F401  # ID SSOT (fr_num_str: in-file + re-export)
from core.quality_gate.legal_artifacts import PHASE_DELIVERABLES  # noqa: E402  # DRY: single source of truth shared with artifact_consistency.LEGAL_ARTIFACTS
from core.utils.script_loader import load_harness_script  # noqa: E402, F401  (public re-export: tests + cli import it from here)
from core.utils import env_loader  # noqa: E402
from core.quality_gate.spec_coverage import (  # noqa: E402, F401
    _collect_shared_test_files,
    _flatten_test_names,
    _get_test_directories,
    _git_test_patterns,
    _parse_inventory_fallback,
    _parse_test_spec,
    _run_spec_coverage_check,
    _scan_test_functions,
)

# --- topology mirrors kept for the anchor tests + back-compat imports ---
_PER_FR_GATE1_PHASES: frozenset[int] = _TOPOLOGY_PER_FR_GATE1
_ENTRY_GATE_MAP: dict[int, int] = _TOPOLOGY_ENTRY_GATES
_PHASE_EXIT_GATES: dict[int, int] = _TOPOLOGY_EXIT_GATES
_PHASES_WITH_GATE1_FR_CHECK: frozenset[int] = _TOPOLOGY_ADVANCE_GATE1
_PHASE_DELIVERABLES = PHASE_DELIVERABLES  # re-export for backward compat (see legal_artifacts.py)

# --- re-exports: every cmd_* and legacy helper name moved out by 方案六/絞殺者
# 續章 lives in cli/ or core/; kept importable from harness_cli for tests and
# downstream callers. Legal at top level since S5: cli/ no longer imports
# harness_cli, so the old mid-file circular-import dance is gone.
from cli._shared import (  # noqa: E402, F401
    _finalize_sentinel_path,
    _generate_stage_pass,
    _run_phase_auditor,
    _sentinel_path,
    _write_finalize_sentinels_for_tests,
)
from cli.phase_cmds import (  # noqa: E402, F401
    cmd_advance_phase,
    cmd_generate_next_plan,
    cmd_plan_all,
    cmd_plan_phase,
    cmd_pre_commit_check,
    cmd_run_phase,
    cmd_sync_harness,
    cmd_validate_handoff,
)
from cli.check_cmds import (  # noqa: E402, F401
    cmd_bug_hunt_targets,
    cmd_build_trace_attestation,
    cmd_check_constitution,
    cmd_check_logic,
    cmd_check_test_mirrors_spec,
    cmd_check_test_spec_consistency,
    cmd_crg_arch_check,
    cmd_generate_verification_report,
    cmd_manifest,
    cmd_migrate_trace_overlay,
    cmd_run_gap_analysis,
    cmd_spec_coverage_check,
    cmd_verify_agent_b_approvals,
    cmd_verify_file,
    cmd_verify_spec,
    cmd_verify_trace,
    cmd_write_approval,
)
from cli.gate_cmds import (  # noqa: E402, F401
    cmd_finalize_env_check,
    cmd_finalize_gate,
    cmd_gate4_tag,
    cmd_mutation_test_score,
    cmd_run_env_check,
    cmd_run_gate,
)
from cli.push_cmds import (  # noqa: E402, F401
    cmd_push_checkpoint,
    cmd_push_milestone,
)
from cli.project_cmds import (  # noqa: E402, F401
    cmd_amend_sab,
    cmd_audit_phase,
    cmd_audit_structure,
    cmd_doctor,
    cmd_effort,
    cmd_init_project,
    cmd_kill_switch,
    cmd_load_context,
    cmd_read_file,
    cmd_status,
)
from cli.fr_cmds import (  # noqa: E402, F401
    cmd_dispatch,
    cmd_reload_policy,
    cmd_resume_fr_phase,
    cmd_run_fr_step,
    cmd_run_tool,
)
from cli.cr_cmds import (  # noqa: E402, F401
    _cr_next_steps,
    cmd_cr_close,
    cmd_cr_open,
    cmd_cr_status,
    cmd_cr_update,
    cmd_crash_triage,
)
from cli.report_cmds import cmd_run_report  # noqa: E402, F401


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

    from cli.phase_cmds import register as _register_phase_cmds
    _register_phase_cmds(sub)




    from cli.check_cmds import register as _register_check_cmds
    _register_check_cmds(sub)




    from cli.push_cmds import register as _register_push_cmds
    _register_push_cmds(sub)


    # ── Phase 9 Maintenance: Change Request lifecycle (cli/cr_cmds.py) ─────
    from cli.cr_cmds import register as _register_cr_cmds
    _register_cr_cmds(sub)

    # ── Round 14 站1: run-report (cli/report_cmds.py) ───────────────────────
    from cli.report_cmds import register as _register_report_cmds
    _register_report_cmds(sub)

    # ── Round 48 站3: repair-harness (cli/repair_cmds.py) ───────────────────
    from cli.repair_cmds import register as _register_repair_cmds
    _register_repair_cmds(sub)


    from cli.gate_cmds import register as _register_gate_cmds
    _register_gate_cmds(sub)










    # (run-pipeline removed in v2.5 — old code consumed ~370 lines)



    from cli.project_cmds import register as _register_project_cmds
    _register_project_cmds(sub)






    from cli.fr_cmds import register as _register_fr_cmds
    _register_fr_cmds(sub)











    # check-constitution










    return p


def _dispatch(args: argparse.Namespace, argv: list[str]) -> int:
    """Call the parsed subcommand's handler, converting any exception that
    escapes it into the appropriate top-level exit code (Round 13 站0 crash
    boundary — see docs/ERROR_HANDLING.md). Split out from main() so tests
    can drive it without going through sys.argv/argparse.

    Before this, an unhandled exception anywhere under args.func(args) hit
    Python's default handler: a raw traceback + exit 1 — indistinguishable
    from a normal "hard failure" (also exit 1). A sub-agent piping this
    command's output would see neither a recognizable signal nor guidance,
    and (per this round's diagnosis) has in the past mis-treated the
    surrounding failure as a project quality problem to "fix" rather than
    a harness bug to report.
    """
    import traceback

    from core.errors import format_harness_bug_banner, write_crash_bundle
    from core.state_io import StateCorruptError
    from cli.exit_codes import EX_HARNESS_BUG, EX_KEYBOARD_INTERRUPT, EX_FAIL, EX_STATE_CORRUPT

    try:
        return args.func(args)
    except KeyboardInterrupt as exc:
        # Round 66: core.errors.HarnessTerminated is a KeyboardInterrupt
        # subclass, so SIGTERM lands here too and carries a message; Ctrl-C
        # carries none. One handler, one exit code, two nameable causes.
        print(f"\n[INTERRUPTED] {exc}".rstrip(), file=sys.stderr)
        return EX_KEYBOARD_INTERRUPT
    except _leaked_control_flow_exceptions() as exc:
        # These are meant to be caught close to their raise site (e.g.
        # cli/gate_cmds.py catches GateBlockedError around finalize-gate).
        # Reaching here means some call path forgot to catch one — surface
        # it visibly instead of a bare traceback, but keep exit 1 (it IS a
        # real block, just one that leaked past its intended catch site).
        print(
            f"\n[WARN] {type(exc).__name__} leaked to top-level (a catch site "
            f"is missing) — treating as a hard failure:\n{exc}",
            file=sys.stderr,
        )
        return EX_FAIL
    except StateCorruptError as exc:
        # Round 14 站2: project data corruption (state.json / quality_
        # manifest.json unreadable) is NOT harness's own bug — it must
        # never fall into the generic Exception branch below, which prints
        # [HARNESS-BUG] and tells the reader "not your fault, don't touch
        # project code". Corrupt PROJECT data is exactly a project problem;
        # FATAL on stdout (this round's own taxonomy) is the correct level.
        print(f"\n[FATAL] {exc}")
        return EX_STATE_CORRUPT
    except Exception as exc:  # noqa: BLE001 -- this IS the crash boundary
        bundle_path = write_crash_bundle(exc, argv)
        print("\n" + format_harness_bug_banner(exc, bundle_path), file=sys.stderr)
        print(
            "\n" + "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
            file=sys.stderr,
        )
        return EX_HARNESS_BUG
    finally:
        # Round 24 站5a: liveness trail. This is the single funnel every CLI
        # subcommand passes through, so a new subcommand cannot forget to
        # participate. In `finally` because a command that failed still proves
        # the harness was running — a stalled run and a failing run need to be
        # distinguishable. record_heartbeat never raises (see its docstring):
        # nothing here may change the exit code being returned above.
        from core.heartbeat import record_heartbeat
        record_heartbeat(getattr(args, "project", ".") or ".",
                         getattr(args, "command", None) or "unknown")


def _leaked_control_flow_exceptions() -> tuple[type[BaseException], ...]:
    """Lazy import to avoid adding these modules to harness_cli.py's
    already-large top-level import block for a path that (by design) is
    rarely hit."""
    from harness.harness_bridge import GateBlockedError
    from core.phase_hooks import KillSwitchBlockedError
    return (GateBlockedError, KillSwitchBlockedError)


def main() -> int:
    """Main entry point for the CLI."""
    # Round 66: before anything else, so that a run killed during startup
    # still unwinds through _dispatch rather than dying where it stands.
    from core.errors import install_termination_handler
    install_termination_handler()

    # Load .env from CWD first (covers `cd project && python harness_cli.py`).
    env_loader.load_env_file(Path.cwd() / ".env")
    # Also load from --project path if it differs from CWD.
    for i, arg in enumerate(sys.argv[1:], 1):
        if arg in ("--project", "-p") and i < len(sys.argv):
            proj_env = Path(sys.argv[i]) / ".env"
            if proj_env.resolve() != (Path.cwd() / ".env").resolve():
                env_loader.load_env_file(proj_env)
            break
        if arg.startswith("--project="):
            proj_env = Path(arg.split("=", 1)[1]) / ".env"
            if proj_env.resolve() != (Path.cwd() / ".env").resolve():
                env_loader.load_env_file(proj_env)
            break

    parser = build_parser()
    args = parser.parse_args()
    return _dispatch(args, sys.argv[1:])


if __name__ == "__main__":
    sys.exit(main())
