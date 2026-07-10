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

Exit codes:
    0   All phases complete
    1   Hard failure (investigate error)
    2   run-gap-analysis: critical gaps detected (distinct from hard error)
    5   Gate 4 prerequisites block (A2/A3/A5 schema, B2 score files)
    6   finalize-gate: gate passed but git commit did not land (manifest rolled back) — fix and re-run
    8   Missing deliverables block — required artifacts not found on disk or not git-tracked
    10  PAUSE — Claude must evaluate gate; run finalize-gate then re-run pipeline
    11  Phase Truth < 90% (HR-11); fix and re-run with --phase-from N
    16  (retired 減法 T3 — constitution keyword scoring is on-demand only)
    21  Scope violation: untracked diagnostic script(s) at repo root; move to
        .sessi-work/tmp or delete, then re-run advance-phase
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import TYPE_CHECKING

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

if TYPE_CHECKING:
    pass


# Ensure repo root on path so core/ and harness/ resolve
_REPO_ROOT = Path(__file__).parent
sys.path.insert(0, str(_REPO_ROOT))

# Script mode runs this file as __main__; register it under its module name
# too, so the cli/ family modules' `import harness_cli as _hc` binds THIS
# running module instead of re-executing the file (circular-import crash).
if __name__ == "__main__":  # pragma: no cover  (script-mode only)
    sys.modules.setdefault("harness_cli", sys.modules[__name__])

# Atomic state-file writers (CV-3 / SG-12 from robustness audit)
from core.phase_topology import (  # noqa: E402
    ADVANCE_GATE1_CHECK_PHASES as _TOPOLOGY_ADVANCE_GATE1,
    ENTRY_GATE_MAP as _TOPOLOGY_ENTRY_GATES,
    EXIT_GATE_MAP as _TOPOLOGY_EXIT_GATES,
    PER_FR_GATE1_PHASES as _TOPOLOGY_PER_FR_GATE1,
)
from core.canonical_form import canonical_form, fr_num_str  # noqa: E402, F401  # ID SSOT (fr_num_str: in-file + re-export)


# (Bug #105 compute_mutation_score import removed in S1 — cli/gate_cmds.py now
# imports it directly from core.quality_gate.mutation_enforcer.)
from core.quality_gate.legal_artifacts import PHASE_DELIVERABLES  # noqa: E402  # DRY: single source of truth shared with artifact_consistency.LEGAL_ARTIFACTS
# S2 extractions — call via module namespace (tool_checks.verify_…) so the
# only monkeypatch seam is the function's home module, never a harness_cli
# attribute that could go stale.
from core.utils.script_loader import load_harness_script  # noqa: E402, F401  (public re-export: tests + cli import it from here)
from core.utils import env_loader  # noqa: E402

# Phases where Gate 1 runs per-FR (P9 maintenance: per-CR touched FRs).
# Sourced from the topology SSOT (core/phase_topology.py) — do not re-declare.
_PER_FR_GATE1_PHASES: frozenset[int] = _TOPOLOGY_PER_FR_GATE1








# Entry gate required per phase (CONSTITUTION.md §2.3)
# Single source of truth: core/phase_topology.py
_ENTRY_GATE_MAP: dict[int, int] = _TOPOLOGY_ENTRY_GATES

# Phase → composite exit gate number (topology SSOT)
_PHASE_EXIT_GATES: dict[int, int] = _TOPOLOGY_EXIT_GATES

# Phases that require Gate 1 per-FR evaluation during advance-phase.
# Phase 6 (Quality Assurance) has no FR loop — it uses Gate 4 exclusively.
# Phase 9 (Maintenance) is deliberately absent: advance-phase --completed 9
# is always BLOCKED (terminal steady state), so its Gate 1 records are
# checked per-CR by cr-close, not here. Expressed as a derivation in
# core/phase_topology.py so it can never drift from PER_FR_GATE1_PHASES.
_PHASES_WITH_GATE1_FR_CHECK: frozenset[int] = _TOPOLOGY_ADVANCE_GATE1

# P1/P2 deliverable labels used as approval-file keys in agent_b_approvals/
# Authoritative list lives in `core.quality_gate.legal_artifacts` (single source
# of truth shared with `core.quality_gate.artifact_consistency.LEGAL_ARTIFACTS`).
_PHASE_DELIVERABLES = PHASE_DELIVERABLES  # re-export for backward compat (see legal_artifacts.py)

# ---------------------------------------------------------------------------
# plan-phase
# ---------------------------------------------------------------------------

# Moved verbatim to cli/phase_cmds.py (方案六). Re-exported so
# existing `from harness_cli import ...` imports keep working.
from cli._shared import (  # noqa: E402, F401  (S4g interim — dies with the impls in S4h)
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
# ---------------------------------------------------------------------------
# plan-all
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# run-phase
# ---------------------------------------------------------------------------



# D4 spec-coverage cluster moved verbatim to core/quality_gate/spec_coverage.py
# (方案六: core must not import the CLI layer). Re-exported here for the
# in-file callers, cli/ families (_hc), and existing monkeypatch targets.
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





# Moved verbatim to cli/check_cmds.py (方案六). Re-exported so
# existing `from harness_cli import ...` imports keep working.
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
















# ---------------------------------------------------------------------------
# run-gate  (Phase 1 of two-phase evaluation)
# ---------------------------------------------------------------------------







# Moved verbatim to cli/gate_cmds.py (方案六). Re-exported so
# existing `from harness_cli import ...` imports keep working.
from cli.gate_cmds import (  # noqa: E402, F401
    cmd_finalize_env_check,
    cmd_finalize_gate,
    cmd_gate4_tag,
    cmd_mutation_test_score,
    cmd_run_env_check,
    cmd_run_gate,
)








# ---------------------------------------------------------------------------
# run-env-check (project-aware environment readiness evaluation)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Gate 4 prerequisite checks  (A2-A5 schema, B2 score files)
# ---------------------------------------------------------------------------














# ---------------------------------------------------------------------------
# finalize-gate  (Phase 2 of two-phase evaluation)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# _cmd_finalize_gate_impl section helpers
# Return an int exit code when the gate should be blocked, None to continue.
# ---------------------------------------------------------------------------










# ---------------------------------------------------------------------------
# generate-next-plan
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# manifest
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# generate-verification-report  (P5 — produces 05-verification/VERIFICATION_REPORT.md)
# ---------------------------------------------------------------------------




# ---------------------------------------------------------------------------
# push-checkpoint  (P1/P2 human review checkpoint push + HANDOVER.md)
# ---------------------------------------------------------------------------

# Moved verbatim to cli/push_cmds.py (方案六). Re-exported so
# existing `from harness_cli import ...` imports keep working.
from cli.push_cmds import (  # noqa: E402, F401
    cmd_push_checkpoint,
    cmd_push_milestone,
)









# ---------------------------------------------------------------------------
# push-milestone  (P3+ milestone push + HANDOVER.md)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# v2.9.1 B.1: validate-handoff  (cross-deliverable dependency check)
# ---------------------------------------------------------------------------
# Closes the e2e finding where P1 orchestrator failed to produce
# TEST_INVENTORY.yaml, P2 orchestrator produced a wrong-shape TEST_SPEC.md
# (prose instead of derive_test_cases.md table), and Agent B peer review
# did not catch the cross-deliverable chain break. Workflow JS can now
# call this CLI as a pre-launch precondition before spawning the next
# phase's orchestrator.
# ---------------------------------------------------------------------------























# ---------------------------------------------------------------------------
# gate4-tag  (create annotated git tag from gate4_result.json)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------

# Moved verbatim to cli/project_cmds.py (方案六). Re-exported so
# existing `from harness_cli import ...` imports keep working.
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
# ---------------------------------------------------------------------------
# load-context
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# read-file (deterministic file read for workflow JS agents)
# ---------------------------------------------------------------------------
#
# Wraps scripts/file_loader.load_file() with CLI argument parsing so that
# workflow JS (which cannot use host APIs per playbook §4) can call this via
# the Bash tool through a SHELL WRAPPER agent. All validation (prefix, length,
# SHA-256, 8 MiB cap) happens server-side in Python — the LLM agent's only job
# is to emit the JSON stdout verbatim, eliminating LLM-interpretation failure
# modes documented in fc99e7f (v6 revert) and the 32-commit churn on
# loadFileViaBash/Python.
#
# Exit codes (machine-readable contract for workflow JS):
#   0 = OK (file exists, prefix matches, length within bounds)
#   1 = MISSING / PREFIX_MISMATCH / TOO_SHORT / TOO_LONG (recoverable)
#   2 = READ_ERROR (fatal: OSError, UnicodeDecodeError, etc.)
#
# Commonality: same flag surface as scripts/file_loader.py CLI, so callers can
# pick whichever entry point (standalone script or this CLI) without learning
# a new API.

# ---------------------------------------------------------------------------
# effort
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# advance-phase
# ---------------------------------------------------------------------------





















# Precision > recall by design (see _scope_violation_scripts docstring): this is
# a small, explicit set of tokens, matched whole (not as a substring) against
# "_"/"-"-separated segments of the filename stem — "_diag_constitution" splits
# to ["diag", "constitution"], an exact hit; "swipe" or "attempt" never match
# "wip"/"tmp" as a substring would. Whole-token matching makes it safe to extend
# this set (no accidental substring collisions to reason about), unlike a raw
# substring/regex search.








# ---------------------------------------------------------------------------
# dispatch  (spawn Agent A/B + auto-log sessions_spawn.log for HR-10)
# ---------------------------------------------------------------------------

# Moved verbatim to cli/fr_cmds.py (方案六). Re-exported so
# existing `from harness_cli import ...` imports keep working.
from cli.fr_cmds import (  # noqa: E402, F401
    cmd_dispatch,
    cmd_reload_policy,
    cmd_resume_fr_phase,
    cmd_run_fr_step,
    cmd_run_tool,
)
# ---------------------------------------------------------------------------
# run-fr-step  (Phase 3-8 sub-agent orchestration with per-step GitHub push)
# ---------------------------------------------------------------------------







# ---------------------------------------------------------------------------
# reload-policy
# ---------------------------------------------------------------------------








    # 2. No other phase storage — state.json is the single source of truth.
    #    git config quality.phase and GitHub CURRENT_PHASE variable are no longer used.

# ---------------------------------------------------------------------------
# Gate BLOCKED diagnostic helpers
# ---------------------------------------------------------------------------



# ---------------------------------------------------------------------------
# run-gap-analysis (M3)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# (run-pipeline removed in v2.5)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# verify-spec
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# migrate-trace-overlay (PR 2 of closed-loop traceability plan)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# build-trace-attestation / verify-trace (PR 3)
# ---------------------------------------------------------------------------



# ---------------------------------------------------------------------------
# check-logic
# ---------------------------------------------------------------------------









# ---------------------------------------------------------------------------
# Phase 9 Maintenance — Change Request lifecycle (cr-open / cr-update /
# cr-status / cr-close). ASPICE SUP.9 (bug) / SUP.10 (feat).
# ---------------------------------------------------------------------------

# Moved verbatim to cli/cr_cmds.py (方案六 family 1/7). Re-exported so
# existing `from harness_cli import cmd_cr_*` imports keep working.
from cli.cr_cmds import (  # noqa: E402, F401
    _cr_next_steps,
    cmd_cr_close,
    cmd_cr_open,
    cmd_cr_status,
    cmd_cr_update,
)

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

    from cli.phase_cmds import register as _register_phase_cmds
    _register_phase_cmds(sub)




    from cli.check_cmds import register as _register_check_cmds
    _register_check_cmds(sub)




    from cli.push_cmds import register as _register_push_cmds
    _register_push_cmds(sub)


    # ── Phase 9 Maintenance: Change Request lifecycle (cli/cr_cmds.py) ─────
    from cli.cr_cmds import register as _register_cr_cmds
    _register_cr_cmds(sub)


    from cli.gate_cmds import register as _register_gate_cmds
    _register_gate_cmds(sub)










    # (run-pipeline removed in v2.5 — old code consumed ~370 lines)



    from cli.project_cmds import register as _register_project_cmds
    _register_project_cmds(sub)






    from cli.fr_cmds import register as _register_fr_cmds
    _register_fr_cmds(sub)











    # check-constitution










    return p

def main() -> int:
    """Main entry point for the CLI."""
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
    return args.func(args)

if __name__ == "__main__":
    sys.exit(main())
