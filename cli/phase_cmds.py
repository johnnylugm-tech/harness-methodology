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
import sys
from pathlib import Path
from typing import Any, Dict

from cli import _shared
from core.atomic_io import (
    FileSnapshot,
    file_lock,
    state_lock_path,
)
from core.evidence_retention import (
    evidence_in_cleared_dirs,
)
from core.quality_gate import agent_b_approvals, gate1_evidence
from core.quality_gate.ghost_detector import scan_phase_ghost_trails
from core.quality_gate.legal_artifacts import PHASE_DELIVERABLES
from core.quality_gate.phase_completed_recovery import (
    try_recover_dangling_phase_completed,
)
from core.state_io import StateCorruptError, load_quality_manifest, load_state
from harness import tool_checks
from core.harness_config import get_value
from core.phase_topology import (
    ENTRY_GATE_MAP,
    EXIT_GATE_MAP,
    PER_FR_GATE1_PHASES,
    VALID_PHASES,
    gates_for_phase,
    phase_name,
)
from core.degradation_ledger import record_degradation
from core.harness_provenance import (
    phase_record_defects,
)
from core.utils.delivery_scope import (
    backup_artifacts,
)
from core.utils.project_layout import ProjectLayout

# Round 80 站7: the six functions around the handover commit advance-phase
# makes now live in cli/advance_commit.py. Re-exported for the call sites
# that remain here and for the tests that reach them by these names.
from cli.advance_commit import (  # noqa: F401  re-export after Round 80 站7 split
    _advance_commit_targets,
    _advance_fsm,
    _enforcer_moved_note,
    _git_head_short,
    _porcelain_paths,
    _uncommitted_deliverables,
)

# Round 80 站7: the nine tree-reading checks and repairs advance-phase
# runs now live in cli/advance_checks.py. Re-exported: every caller here
# and every test reaches them by these names.
from cli.advance_checks import (  # noqa: F401  re-export after Round 80 站7 split
    _broken_deliverable_anchors,
    _check_gate1_live_coverage,
    _check_gate_score_variance,
    _gate1_per_fr_coverage_verdict,
    _regen_and_stage_view,
    _regen_traceability_views,
    _scope_debug_name_match,
    _scope_violation_scripts,
    _warn_if_view_lost_its_anchor,
)

# Round 80 站7: the nine P(N)->P(N+1) handoff validators, their dispatch
# table and _resolve_fr_ids_from_manifest now live in cli/handoff_validators.py.
# Re-exported rather than referenced through the module, because every
# existing caller and test reaches them by these names — a split that drops
# the wiring still imports and still passes unit tests
# (tests/test_god_file_split_safety.py).
from cli.handoff_validators import (  # noqa: F401  re-export after Round 80 站7 split
    _HANDOFF_VALIDATORS,
    _resolve_fr_ids_from_manifest,
    _validate_handoff,
    _validate_handoff_p1_to_p2,
    _validate_handoff_p2_to_p3,
    _validate_handoff_p3_to_p4,
    _validate_handoff_p4_to_p5,
    _validate_handoff_p5_to_p6,
    _validate_handoff_p6_to_p7,
    _validate_handoff_p7_to_p8,
    _validate_handoff_p8_to_p9,
)

# Round 82 站2: the nine checks advance-phase runs before it will move a phase
# now live in cli/advance_prechecks.py, with `_MYPY_EXCLUDE_ARGS` — the only
# name here they read. Re-exported: every call site in `_advance_prechecks`,
# every test that patches them through this module, and
# tests/test_mypy_excludes_harness_submodule.py reach them by these names.
from cli.advance_prechecks import (  # noqa: F401  re-export after Round 82 站2 split
    _MYPY_EXCLUDE_ARGS,
    _precheck_backup_artifacts,
    _precheck_cleared_dir_evidence,
    _precheck_deliverable_anchors,
    _precheck_early_stage_pass,
    _precheck_manifest_and_p1_baselines,
    _precheck_p3_criteria_review,
    _precheck_p3_security_and_quality,
    _precheck_per_fr_gate1_and_phase_truth,
    _precheck_scope_violations,
    _precheck_stage_pass_staging,
)

# Round 82 站3: the seven steps advance-phase takes once its prechecks have
# passed now live in cli/advance_steps.py, with `_run_doctor_after_advance` —
# the last thing the command does and the only name here they read. Re-exported
# for the thirteen call sites in `cmd_advance_phase` and for the tests that
# reach them through this module.
from cli.advance_steps import (  # noqa: F401  re-export after Round 82 站3 split
    _advance_step_commit_and_push,
    _advance_step_refuse_open_obligations,
    _advance_step_refuse_phase_9,
    _advance_step_refuse_uncommitted,
    _advance_step_run_fsm_transition,
    _advance_step_seed_p8_archive,
    _advance_step_write_next_plan_header,
    _run_doctor_after_advance,
)
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
            # state-io-exempt: out_dir honors --output-dir and may differ from
            # <project>/.methodology — core/state_io.py's API is hard-wired to
            # the ProjectLayout-derived path, so it can't serve this probe.
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
        f"Generated: {__import__('datetime').datetime.now(__import__('datetime').timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
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


def _phase_gate_tools(phase: int, project: str) -> tuple[bool, list[str], list[str]]:
    """Split gate-tool gaps into (critical, anticipated) for the target phase.

    Phase 1 entering and the call layer demanding `scancode --version` to be
    green is the same class of bug as a CI script asking for a compile-time
    tool that the build doesn't link to yet: the tool IS required — but at a
    different lifecycle stage. The verification path needs to surface that
    delta instead of collapsing both kinds into one BLOCKED verdict.

    Which gates a phase can reach comes from `core.phase_topology`, which
    declares itself the SSOT for exactly that mapping. Round 56 站1 deleted
    the hand-written `PHASE_GATES` table that used to live here: it agreed
    with the topology at P1–P6 and had no entry at all for P7/P8/P9, so
    `.get(phase, [])` returned nothing and `critical` was empty at two of the
    four phases that run Gate 1 per-FR.

    Returns ``(ok, critical_missing, anticipated_missing)``:
      * critical_missing   — tools a gate that the current phase WILL run
        needs, plus any unreadable gate config regardless of phase. Missing
        these blocks phase entry.
      * anticipated_missing — tools a gate a FUTURE phase will run needs.
        Missing these only warns (the matching phase entry will block then,
        so the user can't slip through indefinitely).
    """
    phase_gates = gates_for_phase(phase)
    critical: list[str] = []
    anticipated: list[str] = []
    for gate_num in (1, 2, 3, 4):
        config_errors, missing = tool_checks.gate_tool_gaps(gate_num, project)
        # A gate config the framework cannot read is a broken checkout, not a
        # dependency a later phase will supply (docs/ERROR_HANDLING.md owner
        # taxonomy: harness/infra fault). It blocks at every phase.
        for diag in config_errors:
            critical.append(f"gate{gate_num}: {diag}")
        bucket = critical if gate_num in phase_gates else anticipated
        for diag in missing:
            bucket.append(f"gate{gate_num}: {diag}")
    return (not critical), critical, anticipated


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

    # Round 73 站1 (mirrors Round 72 站1 cmd_advance_phase fix): when the
    # prepare-commit-msg hook detected an advance-phase handover commit, it
    # passed --prev-record-pending. Forward that flag so _verify_entry_gate
    # skips the phase_completed[N-1] absence/defects check (the record is
    # being written by the SAME cmd_advance_phase call, post-commit). The
    # authoritative manifest gate check still runs and still fails if the
    # underlying gate did not actually pass.
    entry_gate = _verify_entry_gate(
        project, args.phase,
        prev_record_pending=getattr(args, "prev_record_pending", False),
    )
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


def cmd_preview_next_phase(args: argparse.Namespace) -> int:
    """Round 15 §2: read-only preview of P(N+1) entry-blocking findings.

    Does not write state.json, does not write HANDOVER.md, does not create
    any commit — a non-destructive query so an operator can check whether
    the next phase would block BEFORE the current phase's exit gate has
    even passed (cmd_advance_phase's own preview only runs after
    _advance_prechecks succeeds).
    """
    from core.phase_hooks import PhaseHooks

    project = Path(args.project).resolve()
    hooks = PhaseHooks(str(project), phase=args.phase, enable_kill_switch=False,
                        drift_threshold=get_value(project, "drift_threshold"))
    next_phase = args.phase + 1
    try:
        obligations = hooks.preview_next_phase_blocking(next_phase)
    except ValueError as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        return 2

    if not obligations:
        print(f"\n[preview-next-phase] Phase {next_phase} entry: clean — "
              "no blocking obligations predicted.")
        return 0

    print(f"\n[preview-next-phase] Phase {next_phase} entry: "
          f"{len(obligations)} obligation(s) predicted:\n")
    for ob in obligations:
        loc = f" {ob.file}:{ob.line}" if ob.file else ""
        print(f"  [{ob.check_id}] {ob.rule_id}{loc} — {ob.message}")
    return 1


def _regenerate_mutmut_scope(project: Path) -> bool:
    """Render `[mutmut] paths_to_mutate` into setup.cfg from the SAB. P2→P3 only.

    Returns True when setup.cfg was written, so the caller stages it in the
    advance commit: the mutation scope is a DECISION, and a decision that lands
    in an untracked working-tree file is a decision nobody reviewed.
    taskq-advance had no setup.cfg at all, mutated 3384 lines against a SPEC
    that limited NFR-08 to 1846, and recorded that as `mutation_testing 0/70`
    three times with no artifact anywhere stating the scope.

    Runs at the handoff rather than at mutation time because the SAB is final at
    P2 exit while Gate 2 is at P3 exit — generating here puts the derived scope
    in the P3 commit, ahead of every reader who needs it, and leaves
    ``_resolve_mutmut_workdir`` with a single source to read.

    Never raises: a scope that cannot be derived is a degradation to record, not
    a reason to block a phase advance that has already passed every gate.
    """
    from core.quality_gate.mutmut_scope import (
        record_runner_scope,
        resolve_mutation_scope,
        write_paths_to_mutate,
    )

    # Round 68 站2: ask about `runner` BEFORE the four early returns below.
    # This function renders `paths_to_mutate` — which code is mutated — and
    # leaves `runner` — which tests may kill a mutant, and therefore the
    # score — entirely to the project. The runner is a fact about the project
    # in every state this function can return in, and the project most likely
    # to have hand-written one is the project with no SAB.
    record_runner_scope(project)

    sab_path = project / ".methodology" / "SAB.json"
    if not sab_path.exists():
        return False

    layout = ProjectLayout(project)
    src_root = layout.get_relative_str(layout.phase3_development_dir / "src")
    try:
        sab = json.loads(sab_path.read_text(encoding="utf-8"))
        # project_root lets the resolver tell a leaf module from a package
        # (Round 50 站4b). Without it every `foo.bar.baz` becomes a directory
        # path, the existence check below rejects the whole scope, and mutation
        # testing silently widens to the entire source tree.
        paths = resolve_mutation_scope(sab, src_root, project_root=project)
    except (OSError, json.JSONDecodeError, AttributeError, TypeError) as exc:
        record_degradation(
            project, "mutation:scope",
            f"SAB.json could not be read for mutation scope ({exc})",
            why="mutation testing will cover the whole source tree", owner="project"
        )
        return False

    if not paths:
        # Not an error — a project may legitimately mutate everything. It is
        # still a ledger line: "we mutated the whole tree" is the most common
        # reason Gate 2 exceeds its time budget, and the ledger is where the
        # next reader looks for why a run was slow.
        record_degradation(
            project, "mutation:scope",
            f"no scope_layers on the mutation_testing NFR; "
            f"mutation testing will cover all of {src_root}",
            why="declare scope_layers in the SAB when the spec limits the scope", owner="project"
        )
        return False

    # Refuse to write a scope naming directories the project does not have.
    # setup.cfg would otherwise be a config that makes compute_mutation_score
    # abort, and the failure would surface at Gate 2 pointing at mutmut.
    missing = [
        p for p in (x.strip() for x in paths.split(","))
        if p and not (project / p).exists()
    ]
    if missing:
        record_degradation(
            project, "mutation:scope",
            f"SAB scope_layers resolve to non-existent director(ies) {missing}",
            why="setup.cfg left unchanged; fix the layer→module mapping in the SAB", owner="harness"
        )
        return False

    wrote, previous = write_paths_to_mutate(project, paths)
    if wrote and previous is not None:
        record_degradation(
            project, "mutation:scope",
            f"setup.cfg [mutmut] paths_to_mutate replaced: {previous!r} -> {paths!r}",
            why="the SAB owns this value; a hand edit does not survive P2→P3", owner="harness"
        )
    if wrote:
        print(f"  [P2→P3] setup.cfg [mutmut] paths_to_mutate → {paths}")
    return wrote





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
    _pre_rc = _advance_step_refuse_phase_9(args)
    if _pre_rc is not None:
        return _pre_rc

    # Round 38 站4: the exit gate must have a PASS verdict for THIS tree, and
    # the question is asked before advance-phase writes anything. It writes
    # setup.cfg (the P2→P3 mutation-scope sync) on its way through, so a check
    # placed after that point compares against a tree advance-phase itself just
    # changed — the first version of this block sat there and blocked every
    # P2→P3 advance. The tests caught it; the ordering is the fix.
    #
    # state.json::last_gate says the gate was finalized. It says nothing about
    # spec-coverage or the CRG architecture floor, which the workflow checked
    # separately and then discarded: `crg_rc` appears zero times in
    # taskq-renew's entire .methodology/ after a complete P1-P8 run, which is
    # why the contradiction between its P6 baseline (77.8, below its own floor
    # of 80) and its first-round gate4-verify PASS cannot be adjudicated at
    # all. A missing verdict blocks rather than warns: a check we cannot show
    # was run is not a check that passed.
    # Round 44 站2: and the tree that verdict was measured on must be the tree
    # this command is about to record. Placed first among the pre-write
    # checks: a dirty tree makes `has_matching_pass` pass (the verdict was
    # measured on the same dirty tree), so asking for the commit first is the
    # only order in which the operator is told the actionable thing.
    #
    # Measured on taskq-advance, P3→P4, 2026-08-11: the entry obligation for
    # FR-02/FR-06 was cleared at 13:14 by writing `@given` into two test
    # files, `81bbeb4 handover: advance to Phase 4` recorded the phase at
    # 13:17:55 without them, and they entered git at 13:32. `git archive
    # 81bbeb4 | grep -rl "@given"` is empty. The HANDOVER.md that commit
    # generates tells the next session to `git clone`, and that clone fails
    # the check the advance had just satisfied.
    _uncommitted = _uncommitted_deliverables(project, args.completed_phase,
                                             args.completed_phase + 1)
    _pre_rc = _advance_step_refuse_uncommitted(_uncommitted, args, project)
    if _pre_rc is not None:
        return _pre_rc

    if args.completed_phase in EXIT_GATE_MAP:
        from cli.exit_codes import EX_ADVANCE_GATE_VERDICT_MISSING
        from core.quality_gate.gate_verify import has_matching_pass
        _req_gate = EXIT_GATE_MAP[args.completed_phase]
        _verdict_ok, _verdict_why = has_matching_pass(project, _req_gate)
        if not _verdict_ok:
            print(
                f"\n[BLOCKED] advance-phase: gate {_req_gate} has no PASS "
                f"verdict for the tree being advanced.\n"
                f"  {_verdict_why}\n"
                f"  Fix: python3 harness_cli.py verify-gate --project {project} "
                f"--gate {_req_gate} --phase {args.completed_phase} "
                f"--spec-threshold <this gate's D4 threshold>\n"
                f"  It runs the exit gate's three checks (last_gate, "
                f"spec-coverage, crg-arch) and records them together in "
                f".methodology/gate_verify.jsonl.",
                file=sys.stderr,
            )
            return EX_ADVANCE_GATE_VERDICT_MISSING

    # CV-2: Validate args.completed_phase against state.json::current_phase.
    #
    # Three cases:
    #   1. current == completed  → normal advance (run prechecks, advance FSM)
    #   2. current >  completed  → re-verify (run prechecks only, no FSM change)
    #   3. current <  completed  → skip attempt — BLOCKED (prevent phase skips)
    state_path = project / ".methodology" / "state.json"
    if state_path.exists():
        # B4 (CV-2): hold the state lock for the read so a concurrent
        # advance-phase process cannot write between our read and the check.
        with file_lock(state_lock_path(project)):
            _state = load_state(project, lenient=True)
        try:
            _current = int(_state.get("current_phase", 0))
        except (ValueError, TypeError):
            _current = 0

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
            # Fix (Round 39): mirror cmd_run_phase:1695 and cmd_pre_commit_check:203.
            # Re-verify must also surface a dangling phase_completed[N].sha so
            # the user can fix it via re-run, not via a hand-edit of state.json.
            # Recovery helper is non-raising; the gate only fails when no
            # phase{prev}(review-complete) marker is reachable in HEAD.
            entry_gate = _verify_entry_gate(project, args.completed_phase + 1)
            if not entry_gate["passed"]:
                print(
                    f"\n[ENTRY GATE FAILED] {entry_gate['gate']} — "
                    f"{entry_gate['reason']}"
                )
                return 10
            print(f"\n[ENTRY GATE] {entry_gate['gate']}: {entry_gate['reason']}")
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


    next_phase = args.completed_phase + 1

    # Look up gate/FR state from quality_manifest.json for accurate state.json
    manifest = load_quality_manifest(project, lenient=True)
    last_gate_num = None
    last_fr_id = None

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

    # Fix (Round 39): P{N+1} entry gate BEFORE _advance_fsm (L583) and
    # BEFORE the `git add` at L802. Without this call, the only recovery
    # call site was the prepare-commit-msg hook (scripts/hooks/prepare-
    # commit-msg:63 → cmd_pre_commit_check:203 → _verify_entry_gate:1780),
    # but `git add` at L802 snapshotted the pre-recovery state.json into
    # the index BEFORE the hook fired, so the commit materialized the
    # orphan SHA, not the recovered one. Observed on taskq-api 2026-08-05:
    # cadbd6a's state.json carried d061387 even though the hook ran the
    # recovery. By calling _verify_entry_gate here, recovery writes to
    # the working tree BEFORE staging, so the commit captures the healed
    # SHA. The post-commit writer at L934-942 already preserves
    # recovered_from_sha / recovered_at — this patch makes that wiring
    # effective. _advance_fsm at L583 holds state_lock; this call sits
    # BEFORE _advance_fsm so the lock-reentrancy hazard the comment at
    # L833-836 warns about does not apply. Mirror of cmd_run_phase:1695.
    #
    # Round 72 站1: `prev_record_pending=True`. This call is the one place the
    # phase_completed[completed_phase] record does not exist yet BY DESIGN —
    # the write is at the end of this same function, after the commit whose
    # SHA it has to carry.
    entry_gate = _verify_entry_gate(
        project, next_phase, prev_record_pending=True,
    )
    if not entry_gate["passed"]:
        print(
            f"\n[ENTRY GATE FAILED] {entry_gate['gate']} — "
            f"{entry_gate['reason']}"
        )
        return 10
    print(f"\n[ENTRY GATE] {entry_gate['gate']}: {entry_gate['reason']}")

    # Round 14 A: predict P(N+1) entry blocking findings (cross-phase
    # carry-over obligations). The preflight_* methods pattern-match on
    # `phase` to decide whether a finding is blocking — by asking a sibling
    # PhaseHooks with `phase=next_phase` to simulate a preflight run, we
    # surface findings that would silently trip the next session's gate.
    # Stdout is suppressed inside preview_next_phase_blocking so this
    # simulation does not pollute the advance-phase output.
    _obligations: list = []
    try:
        from core.phase_hooks import PhaseHooks
        _phase_hooks = PhaseHooks(
            str(project), phase=args.completed_phase,
            enable_kill_switch=False,
            drift_threshold=get_value(project, "drift_threshold"),
        )
        _obligations = _phase_hooks.preview_next_phase_blocking(next_phase)
    except Exception as _oblig_err:  # pylint: disable=broad-exception-caught
        # Preview failure is non-fatal — surface, do not block advance.
        print(f"  [WARN] preview_next_phase_blocking failed: {_oblig_err}",
              file=sys.stderr)
        _obligations = []
    _pre_rc = _advance_step_refuse_open_obligations(_obligations, args, next_phase, project)
    if _pre_rc is not None:
        return _pre_rc
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
    _advance_step_run_fsm_transition(args, last_fr_id, last_gate_num, project)

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
    _setup_cfg_written = False
    if args.completed_phase == 2:
        sad_path = ProjectLayout(project).sad_path
        if sad_path.exists():
            try:
                from harness.harness_bridge import HarnessBridge
                # Reuse fr_ids from current manifest, fall back to SRS.md scan
                _fr_ids: list[str] = load_quality_manifest(project, lenient=True).get("fr_ids", [])
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
        _setup_cfg_written = _regenerate_mutmut_scope(project)

    # P7→P8: deterministic baseline for CONFIG_RECORDS.md / RELEASE_CHECKLIST.md.
    # LLM agents had been authoring these from scratch and stalling in P8 (4 stalls
    # in the workflow record before this change). The deterministic generator
    # builds both files from state.json + quality_manifest.json + git state;
    # the LLM agent that runs P8 can then review and append human-only context
    # instead of re-deriving the whole structure.
    _advance_step_seed_p8_archive(next_phase, project)

    gen = HandoverGenerator(project)
    _advance_step_write_next_plan_header(gen, next_phase, status, task_bg)

    # Commit locally (no push — next milestone push publishes to origin)
    _pre_rc = _advance_step_commit_and_push(_advance_snap, _manifest_regenerated, _saved_cwd, _setup_cfg_written, args, next_phase, project)
    if _pre_rc is not None:
        return _pre_rc

    # The contract's fall-through. Unreachable today — `_advance_step_commit_and_push`
    # carries this function's original terminal `return 0` and always
    # returns — but `-> int` has to be true of the annotation, not
    # only of today's implementation.
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
    current_phase: int = phase_hint or 3
    last_gate: int | None = None
    last_fr: str | None = None
    state = load_state(project, lenient=True)
    if state:
        try:
            current_phase = phase_hint or int(state.get("current_phase", 3))
        except (ValueError, TypeError):
            pass
        last_gate = state.get("last_gate")
        last_fr = state.get("last_fr")

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

    # Strict (not lenient): this read was previously unguarded entirely — a
    # corrupt manifest raised an uncaught JSONDecodeError, which the crash
    # boundary misclassified as [HARNESS-BUG]. Now it's [FATAL] exit 26.
    manifest = load_quality_manifest(project)
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

_TRACE_CONTENT_CURRENT = (
    "trace attestation content is current (mtime refreshed; matrix unchanged)"
)


def _attestation_content_still_current(project_path: Path) -> bool:
    """Slow-path adjudication of an mtime-stale attestation: is it REALLY stale?

    The mtime probe below is a proxy, and git does not preserve mtimes — every
    pull, checkout, or fresh clone rewrites them, so a perfectly current
    attestation reads as stale. Re-deriving the matrix (~0.4s, and only on the
    probe's unhappy path) answers the actual question. When the content
    matches, write_attestation touches the file without changing a byte, so
    the probe passes from here on and nothing needs to be committed.

    This is what ends the loop that produced six consecutive `chore: refresh
    attestation post-pull` commits, all six carrying the same
    content_sha256 — the matrix had not changed once (Round 18 站3).

    Returns False on any failure: an unreadable overlay or a broken build must
    fall back to the mtime verdict and block, never silently pass.
    """
    try:
        from scripts.build_trace_attestation import (
            attestation_is_current,
            build_attestation,
            write_attestation,
        )

        fresh = build_attestation(project_path)
        if not attestation_is_current(project_path, fresh):
            return False
        write_attestation(project_path, fresh)
        return True
    except Exception:  # pylint: disable=broad-exception-caught
        return False


def _trace_dirty_state(project_path: Path) -> Dict[str, Any]:
    """PR 6: mtime-based trace staleness probe — <50ms, no rglob.

    Compares `attestation.json` mtime against `SAD.md` mtime and the
    newest test file's mtime. Returns the *first* staleness
    cause found, in this order: missing attestation, SAD newer,
    tests newer.

    Round 80 站5: the second comparand used to be documented as "the newest
    `tests/test_fr*.py`" and has not been that since the scan became
    language-aware — `iter_test_files` walks every test file the project's
    language declares, which is why adding any new test to this repo trips the
    probe. The sentence is corrected rather than the code: the wider scan is
    the intended one, and a docstring that names a narrower population than the
    code reads is the defect Round 78 站6 measured.

    Catches the common case where a developer edited
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
    current_phase = load_state(project_path, lenient=True).get("current_phase", 1)
    strict_trace = current_phase < 3  # P1/P2: hard-block; P3+: warn-only

    # SAD.md (canonical locations)
    for sad_candidate in ("02-architecture/SAD.md", "SAD.md"):
        sad_path = project_path / sad_candidate
        if sad_path.exists():
            try:
                if sad_path.stat().st_mtime > att_mtime:
                    # mtime says stale; ask the matrix itself before blocking.
                    if _attestation_content_still_current(project_path):
                        return {"passed": True, "reason": _TRACE_CONTENT_CURRENT,
                                "staler": None, "newer": None}
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
                        # Same adjudication as the SAD branch: a test file
                        # touched by `git pull` is newer by mtime while the
                        # matrix it feeds is unchanged.
                        if _attestation_content_still_current(project_path):
                            return {"passed": True,
                                    "reason": _TRACE_CONTENT_CURRENT,
                                    "staler": None, "newer": None}
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
        # (problem_type="missing_traceability"), which gets one bounded
        # auto-fix attempt (per-strategy allowlist inside AutoFixEngine — only
        # fix_missing_traceability is wired; the other strategies emit stubs
        # and are not production-wired).
        #
        # Round 43 站1: that attempt used to live INSIDE
        # PhaseHooks.preflight_traceability, which made the one command that
        # asks for a measurement — preview_next_phase_blocking, whose
        # docstring promises it mutates nothing — repair the tree it was
        # measuring. The repair belongs to this command, which is the caller
        # that intends to change the project. Behaviour here is unchanged:
        # the same bounded attempt, the same re-verify, and the check re-run
        # so `all_passed` reflects the repaired tree.
        _trace = pre["details"].get("traceability", {})
        if _trace.get("blocking") and not _trace.get("passed") and (
            _trace.get("untested") or _trace.get("uncoded")
        ):
            if hooks.repair_traceability_gap(
                list(_trace.get("untested") or []),
                list(_trace.get("uncoded") or []),
            ):
                pre["details"]["traceability"] = hooks.preflight_traceability()
                pre["all_passed"] = all(
                    r.get("passed", False) for r in pre["details"].values()
                )

    if not pre["all_passed"]:
        # If we reach this point, all preflights are still failing — block.
        print(f"\nPRE-FLIGHT FAILED: {pre['details']}")
        return 1

    # Required-component check (hard dependencies — incl. code-review-graph, which
    # scores the architecture dimension). Verified at every phase entry so a missing
    # component surfaces at setup, not deep inside Gate 3/4. Two-tier verdict:
    # tools needed by a gate the current phase actually runs BLOCK; tools needed
    # only by FUTURE-phase gates WARN (the matching phase entry will block then).
    _tools_ok, _critical_components, _anticipated_components = _phase_gate_tools(
        args.phase, str(project)
    )
    if _anticipated_components:
        print(
            "\n[WARN] run-phase: future-phase gate tools not yet installed"
            " (degraded, not blocking this phase):"
        )
        for m in _anticipated_components:
            print(f"  - {m}")
        print(
            "  These will be required when the matching phase begins."
            " Install to silence the warning."
        )
    if not _tools_ok:
        # Round 47 站3: repair before blocking. run-phase is a caller that
        # intends to prepare the tree, so it owns the fix — the same division
        # Round 43 站1 drew when it moved the traceability auto-fix out of the
        # measurement and into this command. The install commands used to be
        # prose pointing at two documents; they are one SSOT now.
        from harness.env_repair import repair_missing_tools
        _outcome = repair_missing_tools(
            project, tool_checks.all_missing_gate_tool_ids(str(project))
        )
        if _outcome.attempted_steps:
            print(f"\n[REPAIR] run-phase: installed {', '.join(_outcome.attempted_steps)}")
        _tools_ok, _critical_components, _anticipated_components = _phase_gate_tools(
            args.phase, str(project)
        )
    if not _tools_ok:
        print(
            "\n[BLOCKED] run-phase: required components not installed for this phase:\n"
            + "\n".join(f"  - {m}" for m in _critical_components)
            + "\n  These are hard dependencies for the current phase. Repair was attempted\n"
            "  and did not resolve them — the framework installs pip packages into\n"
            "  the project venv and nothing else (external binaries and npm-owned\n"
            "  tools are yours). Install commands: harness/toolchains/bootstrap.py."
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
    # Round 29: run-phase in CI does structural enforcement only (FSM/drift/
    # traceability) — it never dispatches an interactive per-FR loop (see
    # harness_quality_gate.yml: "Gate score evaluation requires an
    # interactive Claude session — always local"). The substrate probe exists
    # to protect that interactive dispatch from a broken sandbox; in CI there
    # is no `claude` CLI and no dispatch to protect, so probing there can only
    # ever fail (the .sessi-work/ cache is gitignored — CI never has it).
    _ci_env = os.environ.get("CI") or os.environ.get("GITHUB_ACTIONS")
    if _ci_env:
        print(
            "\n[SUBSTRATE PROBE] CI environment detected (CI/GITHUB_ACTIONS) — "
            "skipping (no interactive per-FR dispatch runs in CI)."
        )
    elif args.phase in PER_FR_GATE1_PHASES and not getattr(args, "skip_substrate_probe", False):
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
        fr_ids = load_quality_manifest(project, lenient=True).get("fr_ids", [])
        if fr_ids:
            print(f"        Per-FR Gate 1 ({len(fr_ids)} FRs): {', '.join(fr_ids)}")
            for fr_id in fr_ids:
                print(f"          python harness_cli.py run-gate --gate 1 --phase {args.phase} --project {project} --fr-id {fr_id}")
        else:
            print(f"        python harness_cli.py run-gate --gate 1 --phase {args.phase} --project {project} --fr-id FR-XX")
            print("        (quality_manifest.json not found — run 'plan-phase' first to populate FR IDs)")
    return 0

def _verify_entry_gate(
    project: Path, phase: int, *, prev_record_pending: bool = False,
) -> dict:
    """Automatically verify entry gate conditions before phase execution.

    CONSTITUTION.md SS2.3 defines:
    - P1: None
    - P2: Agent B¹ (P1) — git log APPROVE
    - P3: Agent B¹ (P2) — git log APPROVE
    - P4-P8: quality_manifest.json gate PASS

    `prev_record_pending` — Round 72 站1. The one caller that is ITSELF the
    writer of `phase_completed[phase - 1]` passes True. See the comment on that
    check below for why the alternative — writing the record earlier — is not
    available.
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
                state = load_state(project)
            except StateCorruptError as exc:
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
                # Self-heal dangling SHA. See core/quality_gate/
                # phase_completed_recovery.py for the lock/reload/merge
                # protocol. Triggered when push-checkpoint's pre-push HEAD
                # write was raced by an out-of-band `git reset HEAD~N`
                # before its commit landed — recovery searches HEAD-reachable
                # history for the phase marker, atomic-writes the repair,
                # and appends to state.json's append-only recovery log.
                _recovery = try_recover_dangling_phase_completed(
                    project, prev, entry["sha"],
                )
                if _recovery:
                    return {"passed": True, "gate": f"Human1 (P{prev})",
                            "reason": (f"phase_completed[{prev}].sha="
                                       f"{entry['sha'][:8]} was orphaned; "
                                       f"self-healed to "
                                       f"{_recovery['to_sha'][:8]} via "
                                       f"{_recovery['marker']}")}
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

    # Round 53 站5c: the previous phase has to have left a record of itself.
    #
    # From phase 4 on, this function asked quality_manifest.json whether the
    # previous GATE passed and nothing else, so a missing
    # `state.json.phase_completed[N]` could not stop anything. taskq-super
    # reached Phase 9 with entries for 1, 2, 3, 4, 6, 7 — no 5 — and no check
    # ever objected. That record is where Round 24 站4a, Round 26 and Round 44
    # 站2 each put a fact nobody else holds: the SHA the phase completed at,
    # the enforcer that judged it, and `delivered_tree_sha256` — WHICH TREE the
    # checks read. `doctor`'s verdict re-derivation and
    # `_fr_step_lineage_boundary` both read it.
    #
    # How it went missing is visible in that file's git history: the record for
    # phase N is never inside the commit that completes phase N, because
    # cmd_advance_phase makes the handover commit first and only then writes an
    # entry whose `sha` is HEAD *after* it. The value rides along in whatever
    # commits state.json next, and phase 5's ride never arrived — a later
    # whole-document writer that had loaded state before it dropped the key,
    # silently, because the write itself had reported success.
    #
    # The ordering is left alone: writing before the commit would mean the
    # entry could not carry that commit's own SHA, which is what every consumer
    # of it uses (`git merge-base --is-ancestor <sha> HEAD`). This catches the
    # loss one phase later instead of preventing it — later than ideal, and
    # still the difference between a project that stops and a project that
    # reaches Phase 9 missing a record.
    #
    # Round 72 站1: and it must not catch it ZERO phases later. cmd_advance_phase
    # calls this function with `phase = completed_phase + 1` BEFORE its own
    # write at the top of this file, so from `--completed 3` on it was asking
    # for a record only that same call produces — absent on every FIRST advance
    # out of a phase, and the command exited 10. taskq-new, the only project to
    # run P4+ after this check landed, shipped six hand-written entries to get
    # past it, the last of them `{"sha": "PLACEHOLDER_WILL_BE_REPLACED_ON_
    # ADVANCE", "delivered_tree_sha256": "PLACEHOLDER"}`. That is why the check
    # below now reads the record's CONTENT and not merely its presence: this
    # gate is what a project routes around, so what it accepts is what it gets.
    #
    # The other three callers (cmd_run_phase, cmd_pre_commit_check, and this
    # command's re-verify branch) ask about a phase that is already finished,
    # so for them the absence is a real finding and Round 53 站5c's purpose —
    # taskq-super reached Phase 9 with no entry for phase 5 — survives intact.
    prev_phase = phase - 1
    try:
        _entry_state = load_state(project, lenient=True)
    except StateCorruptError as exc:
        return {"passed": False, "gate": f"Gate {ENTRY_GATE_MAP.get(phase)}",
                "reason": f"state.json unreadable: {exc}"}
    _prev_record = (_entry_state.get("phase_completed") or {}).get(str(prev_phase))
    if not prev_record_pending:
        if not _prev_record:
            return {
                "passed": False,
                "gate": f"Gate {ENTRY_GATE_MAP.get(phase)}",
                "reason": (
                    f"state.json.phase_completed[{prev_phase}] is absent — phase "
                    f"{prev_phase} left no record of which tree it was judged on, "
                    f"so nothing downstream can re-derive its verdict. Run "
                    f"`harness_cli.py doctor` to see whether the handover commit "
                    f"for phase {prev_phase} exists and can be reconciled."
                ),
            }
        _record_defects = phase_record_defects(project, _prev_record)
        if _record_defects:
            return {
                "passed": False,
                "gate": f"Gate {ENTRY_GATE_MAP.get(phase)}",
                "reason": (
                    f"state.json.phase_completed[{prev_phase}] is present but "
                    f"not a record of anything: "
                    + "; ".join(_record_defects)
                    + f". Re-run `advance-phase --completed {prev_phase}` so "
                    f"the framework writes it, or `harness_cli.py doctor` to "
                    f"see what the handover commit for phase {prev_phase} was."
                ),
            }

    manifest_path = project / ".methodology" / "quality_manifest.json"
    if not manifest_path.exists():
        return {"passed": False, "gate": f"Gate {ENTRY_GATE_MAP.get(phase)}",
                "reason": "quality_manifest.json not found"}

    try:
        manifest = load_quality_manifest(project)
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


# ── The nine `_precheck_*` helpers below (Round 81 站6) ──────────────────────
#
# `_advance_prechecks` was 818 lines. Round 80 froze it rather than split it,
# because "extracting blocks changes the function's own text, so the
# byte-equality rule that made the Round 49-B splits safe does not apply".
# Half of that is true and the half that matters is not.
#
# Each helper below is one contiguous statement run that tests/support/
# dataflow.py identified as binding NOTHING its caller reads afterwards. Under
# that condition the whole of a run's effect on `_advance_prechecks` is what it
# does itself — the same statements, in the same order, in the same place —
# plus the value it returns, which the call site propagates explicitly:
#
#     _pre_rc = _precheck_x(...)
#     if _pre_rc is not None:
#         return _pre_rc
#
# Nothing is threaded, so nothing can be threaded wrongly. And because both the
# original run and a module-level helper's body sit at one indent level, the
# lines moved with NO reindentation: every body here is byte-identical to the
# text it replaced, which tests/test_extraction_moved_not_rewrote.py checks
# against the pre-extraction file rather than taking on trust.
#
# Parameters are the names a run reads BEFORE it binds them. Both simpler rules
# were tried and both shipped a bug in minutes: passing everything the caller
# had bound produced `F821 Undefined name 'm'` (a comprehension variable is not
# in the enclosing scope) and then `UnboundLocalError: _fs` (the caller binds it
# only inside a loop that may not run).
#
# The order of the calls in `_advance_prechecks` is the order of the runs. It
# has not changed, and it is load-bearing: the manifest-integrity check is
# deliberately first because every check below it reads what it validates.
# ─────────────────────────────────────────────────────────────────────────────



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
      21 = WRITE_SCOPE: a delivered test reads evidence from a cleared directory
      22 = Ghost paper-trail detected (agent claimed progress but made no code changes) (P3+)
      27 = quality_manifest.json structurally corrupt — refusing to commit it
    """
    # ── WRITE_SCOPE: evidence in a directory this command is about to ──
    # delete (Round 72 站6). Ahead of manifest integrity because it is the one
    # check here that asks nothing about gates, phases or manifests — it reads
    # the delivered tree (`git ls-files`) and the Python in it, so it can
    # answer before any of this phase's work has been judged, and what it
    # reports is a defect that will refuse the NEXT advance rather than this
    # one. Cheapest question, earliest answer.
    #
    # taskq-new's NFR-07/NFR-11 tests read
    # `.sessi-work/round_1/tools/pip_licenses.json` and `readability_v2.txt`
    # and skip when they are absent. advance clears `.sessi-work`, so the next
    # phase those tests skip, Round 46 站1 turns both NFRs PARTIAL,
    # completeness drops under 90%, advance is refused, and the agent
    # regenerates the artifacts by hand. `cd47fae` (leaving P5) and `8b9a309`
    # (leaving P7) carry the same subject and the same body.
    #
    # Reported and refused, not repaired: whether an artifact should be
    # retained or regenerated is the project's call, and moving its files would
    # be this framework writing the tree it judges (Round 53). What the block
    # owes the operator is the file, the line, and where evidence may live.
    _cleared_reads = evidence_in_cleared_dirs(project)
    _pre_rc = _precheck_cleared_dir_evidence(_cleared_reads, project)
    if _pre_rc is not None:
        return _pre_rc

    # ── WRITE_SCOPE, third direction: a tool's leftover copy in the tree ──
    # (Round 72 站7). Same placement and the same reason as the check above —
    # it reads the delivered tree and nothing else.
    #
    # `_scope_violation_scripts` further down has guarded this ground since a
    # workflow agent stranded `_diag_constitution.py` at a repo root, but it
    # asks `git status` for UNTRACKED files and the gate/release path commits
    # with `git add -A`. One commit later the file is tracked and that check
    # can never see it again. taskq-new shipped two through P1-P8 and Gate 4.
    _backups = backup_artifacts(project)
    _pre_rc = _precheck_backup_artifacts(_backups)
    if _pre_rc is not None:
        return _pre_rc

    # ── Manifest integrity — FIRST, because every check below reads the ──
    # manifest, and because advance-phase commits .methodology/ wholesale.
    #
    # Round 22 站2: this check lived only in workflow JS, which spent one
    # sub-agent dispatch per advance round calling `check-manifest-integrity`
    # (itself a thin wrapper around this very method). Two consequences: the
    # dispatch was pure overhead on the workflow path, and every OTHER path
    # into advance-phase — a human running it by hand, a resumed session, CI —
    # had no protection at all. tests/test_workflow_plan_alignment.py's own
    # registry recorded that hole in prose ("A human running the plan by hand
    # has no equivalent step"). Running it here closes both.
    from core.phase_hooks import PhaseHooks

    _mi_hooks = PhaseHooks(
        str(project), phase=completed_phase, enable_kill_switch=False,
        drift_threshold=get_value(project, "drift_threshold"),
    )
    _mi = _mi_hooks.preflight_manifest_integrity()
    _pre_rc = _precheck_manifest_and_p1_baselines(_mi, completed_phase, project)
    if _pre_rc is not None:
        return _pre_rc

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

    # ── Finalize-gate receipt check ────────────────────────────────────
    # Verify finalize-gate was actually called — prevents the agent from
    # fabricating gate{N}_result.json + quality_manifest.json directly
    # without the harness running S3/S4 cross-validation.
    #
    # Round 32 站2: this used to ask `.exists()` of a file whose whole content
    # was a timestamp. It now reads the receipt and cross-checks it against the
    # two registries finalize-gate writes alongside it, through the same
    # function core/doctor.py calls — one rule, two consumers.
    _missing_finalize: list[str] = []
    _invalid_finalize: list[str] = []
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
        else:
            _invalid_finalize.extend(gate1_evidence.verify_finalize_evidence(
                project, _exit_gate, completed_phase, None,
            ))
    # Gate 1 per-FR check: every FR must have a finalized Gate 1 sentinel
    _fr_ids_for_finalize: list[str] = load_quality_manifest(project, lenient=True).get("fr_ids", [])
    _pre_rc = _precheck_per_fr_gate1_and_phase_truth(_fr_ids_for_finalize, _invalid_finalize, _missing_finalize, completed_phase, project)
    if _pre_rc is not None:
        return _pre_rc


    # ── Ensure Phase{N}_STAGE_PASS.md exists before the internal Phase
    # Auditor call below — its own C2 check CRITICAL-fails when the file is
    # entirely missing (first-ever advance for this phase). This early pass
    # may write a stale quality_complete value (state.json.phase_truth_passed
    # hasn't been finalized yet) — that's fine, it only needs to exist here.
    # The authoritative content is written by the final regeneration pass
    # near the end of this function (see truth_override=True below), after
    # every blocking check (including this same Phase Auditor call) passes.
    _early_sp_path = project / "00-summary" / f"Phase{completed_phase}_STAGE_PASS.md"
    _precheck_early_stage_pass(_early_sp_path, completed_phase, project)

    # ── Anchors are an invariant of the deliverable, not of one load ──
    # Placement is the design: this runs AFTER the regen above, so the
    # render-only views the framework owns have already been repaired and
    # anything still failing is a file the framework has no right to rewrite.
    # See _broken_deliverable_anchors for the measurement.
    _anchor_breaks = _broken_deliverable_anchors(project)
    _pre_rc = _precheck_deliverable_anchors(_anchor_breaks, completed_phase, project)
    if _pre_rc is not None:
        return _pre_rc

    # ── Phase Auditor: full C1-C12 for all phases ────────────────────
    audit_rc = _shared._run_phase_auditor(project, completed_phase)
    if audit_rc != 0:
        return audit_rc

    # ── WRITE_SCOPE guard: no orphan diagnostic scripts at the repo root ──
    # Mechanism (not agent self-discipline) that keeps debug artifacts out of the
    # source tree. A workflow advance agent once stranded _diag_constitution.py here
    # while diagnosing a constitution BLOCK; BLOCK the advance until it is cleaned.
    _orphans = _scope_violation_scripts(project)
    _pre_rc = _precheck_scope_violations(_orphans, completed_phase, project)
    if _pre_rc is not None:
        return _pre_rc

    # ── TDD checks: pytest + coverage, spec-coverage (P3+) ──────
    # Return code map for this block (pre-existing codes occupy 1-17):
    #   17 → finalize-gate sentinel missing (see check above)
    #   18 → ruff: lint errors in src
    #   19 → mypy: type errors in src
    #   20 → gitleaks: hardcoded secrets detected
    _pre_rc = _precheck_p3_security_and_quality(completed_phase, project)
    if _pre_rc is not None:
        return _pre_rc

    # ── Criteria review (P3 exit) — Round 87 站5 ──────────────────
    # Placed after the P3 quality block so the cheap, deterministic checks
    # report first; this one reads every FR's approval and re-measures the
    # assertions it pinned.
    _pre_rc = _precheck_p3_criteria_review(completed_phase, project)
    if _pre_rc is not None:
        return _pre_rc

    # Round 25 站3a: the fastapi/httpx "integration packages not installed"
    # advisory used to sit here. It fired unconditionally for every P3+ advance,
    # naming two Python web-stack packages regardless of what the project is or
    # what language it is written in, and enforced nothing. On the
    # run-all-by-workflow run it told a CLI job-queue tool to `pip install
    # fastapi` six times. A guess about the next phase's dependencies, with no
    # evidence behind it and no consequence attached, is noise in the one output
    # stream the agent is instructed to read verbatim.

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

    # Round 25 站3b: the harness/ submodule drift advisory used to run here. It
    # was advance-phase's only network call (a `git fetch` per advance), it
    # blocked nothing, and it was 60% of the wall time of a P1/P2 advance. It
    # now lives in `doctor` as core.doctor._check_submodule_behind — being a few
    # commits behind origin does not make this phase's work wrong, so it does
    # not belong on the phase-transition critical path.

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
    _pre_rc = _precheck_stage_pass_staging(_existing_bytes_hash, _sp_gate, _stage_pass_path, completed_phase, project)
    if _pre_rc is not None:
        return _pre_rc

    # The contract's fall-through. Unreachable today — the last helper
    # carries this function's original terminal `return 0` and always
    # returns — but `-> int` has to be true of the annotation, not only
    # of today's implementation.
    return 0



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
    # Round 73 站1 (mirrors the Round 72 站1 cmd_advance_phase fix): when the
    # prepare-commit-msg hook detects an advance-phase handover commit
    # (commit message "handover: advance to Phase N"), pass
    # --prev-record-pending so _verify_entry_gate skips the
    # phase_completed[N-1] absence/defects check (the record is written by
    # cmd_advance_phase AFTER the commit lands, so it carries the commit's
    # own SHA). The authoritative manifest gate check still runs.
    pcc.add_argument("--prev-record-pending", action="store_true",
                     help="Skip phase_completed[phase-1] absence/defects check (advance-phase handover commits only).")
    pcc.set_defaults(func=cmd_pre_commit_check)

    # preview-next-phase (Round 15 §2 — read-only, no state/HANDOVER/commit writes)
    pn = sub.add_parser(
        "preview-next-phase",
        help="Read-only: predict P(N+1) entry-blocking findings (no writes)",
    )
    pn.add_argument("--phase", type=int, required=True,
                     help="Current phase number to preview from")
    pn.add_argument("--project", default=".", help="Project root (default: .)")
    pn.set_defaults(func=cmd_preview_next_phase)

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
    adv.add_argument(
        "--push", action="store_true",
        help="Also push the handover commit to origin (default: commit locally "
             "only — the next milestone push publishes it)",
    )
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
