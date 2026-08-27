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
from typing import Any, Dict, Iterator, Optional

from cli import _shared
from core import claude_md
from core.atomic_io import (
    FileSnapshot,
    StateTransaction,
    atomic_write_json,
    file_lock,
    state_lock_path,
)
from core.evidence_retention import (
    ADVANCE_CLEARED_DIRS,
    cited_evidence_dir,
    evidence_in_cleared_dirs,
)
from core.quality_gate import agent_b_approvals, gate1_evidence
from core.quality_gate.ghost_detector import scan_phase_ghost_trails
from core.quality_gate.legal_artifacts import PHASE_DELIVERABLES
from core.quality_gate.phase_completed_recovery import (
    try_recover_dangling_phase_completed,
)
from core.quality_gate import spec_coverage
from core.state_io import StateCorruptError, load_quality_manifest, load_state
from harness import tool_checks
from core.harness_config import get_timeout, get_value
from core.phase_topology import (
    ADVANCE_GATE1_CHECK_PHASES,
    ENTRY_GATE_MAP,
    EXIT_GATE_MAP,
    PER_FR_GATE1_PHASES,
    VALID_PHASES,
    gates_for_phase,
    phase_name,
)
from core.degradation_ledger import record_degradation
from core.doctor import run_doctor
from core.harness_provenance import (
    enforcer_sha,
    enforcer_surface,
    phase_record_defects,
)
from core.utils.delivery_scope import (
    BACKUP_SUFFIXES,
    backup_artifacts,
    committed_tree_digest,
)
from core.utils.project_layout import ProjectLayout

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
from core.utils.script_loader import load_harness_script
from core.utils.timefmt import utc_now_iso
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


# Round 43: the harness submodule is always mounted at this fixed path
# (init-project / submodule_guard.py both hardcode "harness" too — there is
# no per-project override anywhere), and it ships its own test fixtures
# shaped like the framework's canonical `03-development/` layout. mypy has
# no default boundary at a nested `.git` the way ruff's file-walker does, so
# `mypy .` from a consumer project's root walks straight into
# harness/tests/fixtures/.../03-development/tests/conftest.py and collides
# with the consumer's own 03-development/tests/conftest.py ("Duplicate
# module named conftest") the moment the latter exists — aborting the whole
# type-check before it examines anything else. Named here so the exclusion
# is independently testable rather than an inline literal in the
# subprocess.run() call below.
_MYPY_EXCLUDE_ARGS = ["--exclude", "^harness/"]


def _run_doctor_after_advance(project: Path) -> None:
    """Run the framework's own read of its state, and give the ERRORs a reader.

    Round 45 站5. `grep -rn "run_doctor"` over the whole repository found ONE
    call site: cli/project_cmds.py, which IS the `doctor` command. advance-phase
    did not call it, preflight did not call it, and none of the nine generated
    workflow JS files mention it. So three mechanisms built to be read at a
    phase boundary had never been read at one — Round 43 站4's enforcer
    provenance, Round 44 站4's milestone-tree check, and Round 45 站3's per-FR
    evidence reconciliation. Round 43's mother pattern (detected, no executor)
    at the level of a whole command.

    Deliberately weak wiring, in three respects:

    * It runs AFTER the phase has turned over, so nothing here can undo a
      milestone that is already correct.
    * Only ERROR findings reach the degradation ledger. taskq-advance's doctor
      output is seven WARNs (six provenance, one submodule); writing those
      every advance would bury the thing this exists to surface.
    * The exit code does not change. A check whose false-positive rate was 100%
      four hours before this station shipped (station 2's thirty accusations)
      does not get the power to stop a pipeline. It gets a reader. Same
      standing as Round 43 站4's phase_verdict_staleness: a diagnosis, not a
      waiver.

    `run_doctor` measured 1.15s on a copy of taskq-advance, so it runs whole —
    no subset registry, one fewer mechanism than the plan budgeted for.
    """
    try:
        findings = run_doctor(project)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        # The phase has already turned over and the milestone is correct. A
        # diagnostic that raises must not look like a successful clean run
        # either (docs/ERROR_HANDLING.md), so it says so and is recorded.
        print(f"  [WARN] doctor could not run after the advance: "
              f"{type(exc).__name__}: {exc}", file=sys.stderr)
        record_degradation(
            project, "doctor:unavailable",
            f"doctor raised after the phase advanced: {type(exc).__name__}",
            why=("the advance itself is correct; nothing checked the state it "
                 "left behind"), owner="harness"
        )
        return

    errors = [f for f in findings if f.severity == "ERROR"]
    if not errors:
        return
    print(f"\n[advance-phase] doctor: {len(errors)} error(s) in the state this "
          f"advance left behind — recorded, not blocking:", file=sys.stderr)
    for finding in errors:
        print(f"  [ERROR] {finding.check}: {finding.message}", file=sys.stderr)
        record_degradation(
            project, f"doctor:{finding.check}", finding.message,
            why=("found by doctor immediately after the phase advanced; the "
                 "advance is not reversed, but this state is what the next "
                 "phase starts from"), owner="harness"
        )


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
    if _uncommitted:
        from cli.exit_codes import EX_ADVANCE_UNCOMMITTED_DELIVERABLES
        _head_short = _git_head_short(project)
        print(
            f"\n[BLOCKED] advance-phase: {len(_uncommitted)} delivered "
            f"file(s) differ from {_head_short}.\n"
            f"  Phase {args.completed_phase} is about to be recorded as a "
            f"commit on top of {_head_short}, and that commit will not contain the "
            f"content below — but the checks that let this phase exit were "
            f"measured on it.\n",
            file=sys.stderr,
        )
        for _rel in _uncommitted:
            print(f"  - {_rel}", file=sys.stderr)
        print(
            f"\n  Fix: commit them, then re-run `advance-phase --completed "
            f"{args.completed_phase}`. A file generated at runtime belongs in "
            f".gitignore instead — it is not a deliverable, and tracking it "
            f"makes every tree digest disagree with the last one.",
            file=sys.stderr,
        )
        _exit_gate = EXIT_GATE_MAP.get(args.completed_phase)
        if _exit_gate:
            print(
                f"  Committing changes the delivered tree, so gate "
                f"{_exit_gate} will ask to be re-verified against it. That is "
                f"the point: the verdict and the commit have to be about the "
                f"same tree.",
                file=sys.stderr,
            )
        for _rel in _uncommitted:
            record_degradation(
                project, "milestone:uncommitted",
                f"P{args.completed_phase} exit blocked by {_rel}",
                why=("the milestone commit would not contain this file's "
                     "current content, and the phase's checks were measured "
                     "on it"),
                data={"completed_phase": args.completed_phase, "file": _rel}, owner="project"
            )
        return EX_ADVANCE_UNCOMMITTED_DELIVERABLES

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
    if _obligations:
        # Round 43 站2: refuse the advance. Round 14 A computed this list and
        # Round 14 A4 stopped it over-promising "Ready to begin Phase N+1" —
        # but the advance then ran anyway, and the table went into HANDOVER.md,
        # which nothing in the pipeline reads back (`grep -r "Entry
        # Obligations"`: one producer, four test assertions). Measured on
        # taskq-api: the P3→P4 handover commit named five `# pragma: no cover`
        # sites by file and line, and three commits later the push was
        # rejected by the pre-push hook on exactly those five lines.
        #
        # The obligation's own definition is "would BLOCK entry to Phase N+1".
        # Advancing into it produces a state with no truth value —
        # current_phase says N+1 while N+1's entry preflight fails — which is
        # why scripts/hooks/pre-push has to guess the phase to judge a commit
        # at by pattern-matching HEAD's subject line. Not creating that state
        # is the fix; the guess becoming removable is a consequence of it.
        #
        # The message carries the findings themselves rather than a pointer to
        # them (R24 站1, tests/test_blocked_message_contract.py): each row
        # already knows its check, its rule and, where the check reports one,
        # its file and line.
        from cli.exit_codes import EX_ADVANCE_ENTRY_OBLIGATIONS
        print(
            f"\n[BLOCKED] advance-phase: P{next_phase} entry has "
            f"{len(_obligations)} unresolved obligation(s). Not advancing — "
            f"state.json still reads phase {args.completed_phase}.\n"
        )
        for _ob in _obligations:
            _where = ""
            if _ob.file:
                _where = f" {_ob.file}"
                if _ob.line is not None:
                    _where += f":{_ob.line}"
            print(f"  - [{_ob.check_id}] {_ob.rule_id}{_where}\n"
                  f"      {_ob.message}")
        print(
            f"\n  Fix: resolve each finding above, then re-run "
            f"`advance-phase --completed {args.completed_phase}`. Each one is "
            f"a check that will block at P{next_phase} entry regardless; "
            f"resolving them here costs the same work and does not leave a "
            f"phase recorded that cannot be entered.\n"
        )
        # Round 43 站4: some of these findings judge artifacts from phases
        # that already passed. When the enforcement surface has moved since
        # that phase was accepted, the operator is looking at a raised bar,
        # not a regression they introduced. Diagnosis only — nothing is
        # waived, and Round 38's rule stands.
        _stale_note = _enforcer_moved_note(project, args.completed_phase)
        if _stale_note:
            print(_stale_note)
        # Machine-readable form. The HANDOVER.md table stays (it is now the
        # record of why the last advance was refused); the ledger is what a
        # programmatic reader can consume — same shape Round 42 站2 used for
        # spec:undelivered.
        for _ob in _obligations:
            record_degradation(
                project, f"obligation:{_ob.check_id}",
                f"P{next_phase} entry blocked by {_ob.rule_id}",
                why=_ob.message,
                data={
                    "target_phase": _ob.target_phase,
                    "rule_id": _ob.rule_id,
                    "file": _ob.file,
                    "line": _ob.line,
                }, owner="project"
            )
        return EX_ADVANCE_ENTRY_OBLIGATIONS

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
    #
    # Round 50 站6: the directories cleared here are named in
    # core/evidence_retention.ADVANCE_CLEARED_DIRS, which is also the list a
    # verdict may not cite. One statement, so adding a scratch directory
    # cannot leave the two disagreeing.
    for _cleared_rel in ADVANCE_CLEARED_DIRS:
        cleared_dir = project / _cleared_rel
        sentinels_dir = cleared_dir / "sentinels"
        _sentinels_backup: Optional[Path] = None
        # Bug H1 fix: wrap backup→rm→restore in try/finally so the temp dir is
        # cleaned up even if shutil.rmtree / copytree raises a non-OSError
        # (KeyboardInterrupt, RuntimeError, etc.) that ignore_errors won't swallow.
        try:
            if sentinels_dir.is_dir():
                _sentinels_backup = Path(tempfile.mkdtemp(prefix="harness-sentinels-"))
                shutil.copytree(sentinels_dir, _sentinels_backup / "sentinels")
            if cleared_dir.is_dir():
                shutil.rmtree(cleared_dir, ignore_errors=True)
                print(f"  [advance-phase] Cleared stale {cleared_dir}")
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
            setup_cfg_written=_setup_cfg_written,
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

        # ── phase_completed (Round 24 站4a) ───────────────────────────────
        # advance-phase is the authority on "phase N is complete" — it is the
        # command that verifies the exit gate and writes the handover commit.
        # Until now the ONLY writer of state.json.phase_completed was
        # cli/push_cmds.py::cmd_push_checkpoint, which the generated workflows
        # invoke for P1 and P2 only. Every project therefore had
        # phase_completed == {1, 2} no matter how far it got: confirmed on the
        # run-all-by-workflow P1-P8 run, which reached Phase 9 with those two
        # entries and nothing else.
        #
        # The consequence was silent. cli/fr_cmds.py:_fr_step_lineage_boundary
        # reads phase_completed[phase-1] to scope its idempotency grep to the
        # current phase's lineage; with no entry it returns None and callers
        # fall back to an UNSCOPED grep — which is the exact bug the 2026-07-11
        # fix existed to remove (after a `git reset --hard`, a stale
        # `refactor(FR-02): IMPROVE` from a reset-away lineage still matched and
        # the step was skipped as already-done). That fix worked only for
        # phase 3, where phase-1 == 2 happens to have an entry. Its docstring
        # described the gap as "projects without reset history", which is not
        # what was happening.
        #
        # SHA: HEAD *after* the handover commit — the commit itself. push_cmds
        # records the PRE-push HEAD because it writes before creating its
        # commit. Both satisfy the only consumer contract there is
        # (`git merge-base --is-ancestor <sha> HEAD`, harness_cli.py CI and
        # _verify_entry_gate), and each names the repo state at which that
        # phase completed.
        _head = subprocess.run(
            ["git", "-C", str(project), "rev-parse", "HEAD"],
            capture_output=True, text=True,
        )
        if _head.returncode == 0 and _head.stdout.strip():
            try:
                with file_lock(state_lock_path(project)):
                    _sd = load_state(project, lenient=True)
                    _existing = _sd.get("phase_completed", {}).get(
                        str(args.completed_phase)
                    )
                    _rec_from = (
                        _existing.get("recovered_from_sha")
                        if isinstance(_existing, dict) else None
                    )
                    _rec_at = (
                        _existing.get("recovered_at")
                        if isinstance(_existing, dict) else None
                    )
                    _new_entry = {
                        "sha": _head.stdout.strip(),
                        "timestamp": utc_now_iso(),
                        # Round 26: WHICH framework version produced this phase.
                        # Gate results have carried this since Round 19 站3; phase
                        # artifacts did not, so a run whose harness was patched
                        # mid-flight left no trace of the skew. That happened during
                        # taskq-plus P1-P3: five framework commits landed between
                        # 06:02 and 10:24, one of them fixing the very P2 SAB-WRITE
                        # step that had completed seven hours earlier. Fixing a
                        # prompt does not retroactively fix the artifact it
                        # produced, and nothing could say so.
                        "enforcer_sha": enforcer_sha(),
                        "enforcer_surface": enforcer_surface(),
                        # Round 44 站2: WHICH TREE this phase was judged on.
                        # The three fields above say when, by whom and at
                        # which commit; none of them said what the checks
                        # actually read. On taskq-advance they read two test
                        # files that entered git fourteen minutes after this
                        # record was written. The invariant at the top of
                        # cmd_advance_phase means this equals the commit's
                        # own tree, and recording it lets `doctor` re-derive
                        # that from the artifact alone.
                        "delivered_tree_sha256": committed_tree_digest(
                            project, _head.stdout.strip(),
                        ),
                    }
                    # Preserve the recovery audit if a self-heal ran during
                    # the verify_entry_gate that gated this commit — top-level
                    # phase_completed_recovery_log survives anyway, but the
                    # entry-level pointer is what
                    # `_fr_step_lineage_boundary` and future readers will see.
                    if _rec_from:
                        _new_entry["recovered_from_sha"] = _rec_from
                    if _rec_at:
                        _new_entry["recovered_at"] = _rec_at
                    _sd.setdefault("phase_completed", {})[
                        str(args.completed_phase)
                    ] = _new_entry
                    atomic_write_json(
                        project / ".methodology" / "state.json", _sd
                    )
            except Exception as _pc_err:  # pylint: disable=broad-exception-caught
                record_degradation(
                    project, "advance-phase",
                    f"phase_completed[{args.completed_phase}] not recorded",
                    f"{type(_pc_err).__name__}: {_pc_err}", owner="harness"
                )
        else:
            record_degradation(
                project, "advance-phase",
                f"phase_completed[{args.completed_phase}] not recorded",
                f"git rev-parse HEAD failed: {_head.stderr.strip()}", owner="harness"
            )

        # ── --push (Round 23 站1) ─────────────────────────────────────────
        # Opt-in publication of the handover commit this function just made.
        # Historically advance-phase always stopped at "commit locally", and
        # every generated phase workflow bolted a Sync box on right after
        # Advance whose entire job was one `git push`. That put the push in
        # the prompt layer, where only a prompt-following caller gets it —
        # the same shape Round 22 站2 relocated for manifest integrity.
        #
        # Default OFF: the 8 generated phase workflows keep their Sync box and
        # their output stays byte-identical. run-all.js is the consumer.
        #
        # A push failure does NOT roll back. The commit-failure branch above
        # rolls back because nothing landed in git at all, so state.json would
        # otherwise claim a phase git history does not record. Here the commit
        # DID land: undoing it would destroy durable work to recover from a
        # transient network error. Report and let the operator retry.
        if getattr(args, "push", False):
            push_result = subprocess.run(
                ["git", "-C", str(project), "push", "origin", "HEAD"],
                capture_output=True, text=True,
            )
            if push_result.returncode != 0:
                _push_cmd = f"git -C {project} push origin HEAD"
                print(
                    f"\n[BLOCKED] advance-phase --push: the handover commit "
                    f"landed locally but the push failed.\n"
                    f"  Detected: "
                    f"{(push_result.stdout + push_result.stderr).strip()[:400]}\n"
                    f"  The advance was NOT rolled back — state.json and the "
                    f"commit are correct; only publication is missing.\n"
                    f"  Fix the remote/connectivity error above, then re-run "
                    f"just the push:\n"
                    f"    {_push_cmd}"
                )
                return 28
            print("[advance-phase] Pushed the handover commit to origin.")

    _run_doctor_after_advance(project)

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

def _advance_commit_targets(
    completed_phase: int,
    next_phase: int,
    manifest_regenerated: bool,
    fr_progress_exists: bool,
    gate_timestamps_exists: bool = False,
    stage_pass_exists: bool = False,
    plan_exists: bool = True,
    attestation_exists: bool = False,
    setup_cfg_written: bool = False,
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
    if setup_cfg_written:
        # Round 30 站2: the P2→P3 handoff renders [mutmut] paths_to_mutate from
        # the SAB. Only staged when it was actually written this run — same
        # missing-pathspec hazard as the entries above, and staging an unchanged
        # file would put a no-op into every advance commit.
        targets.append("setup.cfg")
    if next_phase == 8:
        targets += ["08-config/CONFIG_RECORDS.md", "08-config/RELEASE_CHECKLIST.md"]
    return targets

def _git_head_short(project: Path) -> str:
    """`HEAD`'s short sha, or the word HEAD when git cannot answer."""
    proc = subprocess.run(
        ["git", "-C", str(project), "rev-parse", "--short", "HEAD"],
        capture_output=True, text=True,
    )
    return proc.stdout.strip() if proc.returncode == 0 and proc.stdout.strip() else "HEAD"


def _uncommitted_deliverables(
    project: Path, completed_phase: int, next_phase: int,
) -> list[str]:
    """Delivered files whose content differs from HEAD's, sorted.

    Round 44 站2. Two exemptions, both taken from an existing single source
    rather than restated here:

      * `core.utils.delivery_scope.is_harness_volatile` — append-only
        ledgers, caches, locks, `.sessi-work/`. The harness writes them by
        running; they are not the project.
      * `_advance_commit_targets` called with every optional flag on — the
        maximal set of files THIS command rewrites and stages a few hundred
        lines below. Refusing to advance because HANDOVER.md is stale would
        be unsatisfiable: regenerating it is what the advance does. Measured
        on taskq-api, `M HANDOVER.md` is its resting state.

    Everything else is content a clone will not have. That deliberately
    includes `.methodology/harness_config.json` and `.methodology/SAB.json`,
    which are scoring inputs (station 0 premise 1) and are not in the
    advance's write set.

    Empty list when git cannot answer — a check that cannot run does not
    block (the gate verdict check immediately after this one is the one that
    refuses on missing evidence).
    """
    from core.utils.delivery_scope import is_harness_volatile

    proc = subprocess.run(
        # `-uall`, not the default: git collapses an entirely untracked
        # directory into one `?? dir/` record, which then matches no
        # exemption and names no file the operator can act on. Caught by
        # tests/e2e/test_cli_journeys.py, which reported `.methodology/trace/`
        # while the file inside it is one this command stages itself.
        ["git", "-C", str(project), "status", "--porcelain", "-z", "-uall"],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        return []

    owned = set(_advance_commit_targets(
        completed_phase, next_phase,
        manifest_regenerated=True, fr_progress_exists=True,
        gate_timestamps_exists=True, stage_pass_exists=True,
        plan_exists=True, attestation_exists=True, setup_cfg_written=True,
    ))

    dirty: set[str] = set()
    for rel in _porcelain_paths(proc.stdout):
        if rel in owned or is_harness_volatile(rel):
            continue
        dirty.add(rel)
    return sorted(dirty)


def _porcelain_paths(stdout: str) -> "Iterator[str]":
    """Repo-relative paths out of `git status --porcelain -z`.

    NUL-separated so a path containing a newline cannot be read as two, and
    unquoted so a non-ASCII path is not escaped — the same reasoning as
    `core/utils/delivery_scope.py`'s `git ls-files -z`. A rename record
    carries its source path in a second NUL-terminated field, which is
    dropped: the destination is the one that has to be committed.
    """
    fields = [f for f in stdout.split("\0") if f]
    index = 0
    while index < len(fields):
        entry = fields[index]
        index += 1
        if len(entry) < 4:
            continue
        status, rel = entry[:2], entry[3:]
        if status[0] in ("R", "C"):
            index += 1        # skip the source path that follows
        yield rel


def _enforcer_moved_note(project: Path, up_to_phase: int) -> str:
    """One line naming the phases whose recorded PASS predates a rule change.

    Round 43 站4. Appended to advance-phase's obligation [BLOCKED] because
    that message is where the question arises: a finding against a Phase 1
    artifact, on a project whose Phase 1 passed five framework rounds ago,
    reads as "you broke this" when the truth may be "the bar moved". The
    data has been recorded since Round 19 站3 / Round 29 站4 and never read
    for this.

    Diagnosis only. Nothing is waived — Round 38's rule is that no threshold
    may be, and grandfathering a rule to artifacts accepted before it existed
    is the same rule inverted: the framework could then never raise its own
    bar. Empty string when there is nothing to say, so a caller can print it
    unconditionally-guarded and add no noise.
    """
    from core.harness_provenance import phase_verdict_staleness
    moved: list[str] = []
    for phase in range(1, up_to_phase + 1):
        if phase_verdict_staleness(project, phase):
            moved.append(str(phase))
    if not moved:
        return ""
    return (
        f"  [note] Phase(s) {', '.join(moved)} recorded their PASS under a "
        f"different enforcement surface than the one running now. A finding "
        f"above against one of their artifacts may be a raised bar rather "
        f"than something this session broke. It is not waived either way — "
        f"`doctor` names which paths changed.\n"
    )


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
                state_data = load_state(project)
            except StateCorruptError as exc:
                from core.degradation_ledger import record_degradation
                record_degradation(
                    project, "phase_cmds._advance_fsm",
                    "state.json unreadable — treating FSM state as fresh (INIT) "
                    "and overwriting the file's other fields",
                    why=str(exc), owner="project"
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
        # carries fields this function doesn't own (phase_completed,
        # ci_readiness_ack, language, test_runner, ...); a bare
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
    if _cleared_reads:
        _cited = cited_evidence_dir(project).relative_to(project)
        print(
            f"\n[BLOCKED] {len(_cleared_reads)} delivered test path(s) read from "
            f"a directory advance-phase deletes at every transition "
            f"({', '.join(ADVANCE_CLEARED_DIRS)}):"
        )
        for _row in _cleared_reads:
            print(f"  - {_row['path']}:{_row['line']}  {_row['literal']}")
        print(
            f"  Those tests pass now and skip after this advance, which turns "
            f"the requirements they witness PARTIAL and refuses the NEXT "
            f"advance.\n"
            f"  Fix: move the evidence a later phase must still read to "
            f"{_cited}/ (or anywhere outside the cleared list) and update the "
            f"path in each line above — .methodology/ is committed and "
            f"survives the transition. Then re-run advance-phase."
        )
        return 21

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
    if _backups:
        print(
            f"\n[BLOCKED] {len(_backups)} tool leftover(s) in the delivered "
            f"tree — files ending {', '.join(BACKUP_SUFFIXES)} are an editor's "
            f"or a mutation runner's copy, not a deliverable:"
        )
        for _b in _backups:
            print(f"  - {_b}")
        print(
            "  They are scored, mutated and shipped as if they were source. "
            "Fix: delete them (or gitignore them if a tool keeps rewriting "
            "them), then re-run advance-phase."
        )
        return 21

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
    if not _mi.get("passed"):
        print(
            "\n[BLOCKED] advance-phase: quality_manifest.json is structurally "
            "corrupt — refusing to commit it.\n"
            f"  Detected: {_mi.get('reason') or _mi.get('issues') or 'see the check output above'}\n"
            "  Fix: verify HEAD's copy is healthy, then restore it:\n"
            "    git show HEAD:.methodology/quality_manifest.json | head -40\n"
            "    git checkout HEAD -- .methodology/quality_manifest.json\n"
            "  Then merge this phase's gate result back into gate_results and "
            "re-run advance-phase."
        )
        return 27

    # ── P1 exit: SRS.md's own NFR vocabulary must be legal ───────────
    # Round 33 站3. SRS.md states `type:` and `dimension:`; sab_parser is the
    # only thing in the tree that enforces the first, and it runs in Phase 2 —
    # by which point the value is inside an approved, verbatim-transcribe
    # upstream deliverable and the two phases cannot converge (measured: five
    # B-review rounds to the HR-12 hard cap). Phase 1's B-checklist asks the
    # agent to check this; that leaves the verdict with the party being
    # judged. First, because a vocabulary error makes every downstream reading
    # of this file wrong and the fix is one word in one file.
    if completed_phase == 1:
        from cli.exit_codes import EX_ADVANCE_SRS_VOCABULARY_ILLEGAL
        from core.quality_gate.srs_nfr_validate import illegal_nfr_vocabulary

        _vocab = illegal_nfr_vocabulary(project)
        if _vocab:
            print(
                "\n[BLOCKED] SRS.md's machine-readable NFR block uses a "
                "vocabulary the framework does not accept:\n  "
                + "\n  ".join(_vocab)
                + "\n\n  Fix the values in 01-requirements/SRS.md and re-run "
                "advance-phase. Refused here rather than at Phase 2's "
                "generate_sab.py --validate, where SRS.md is already an "
                "approved upstream deliverable that SAD.md must transcribe."
            )
            return EX_ADVANCE_SRS_VOCABULARY_ILLEGAL

    # ── P1 checksum: TEST_INVENTORY.yaml baseline ────────────────────
    if completed_phase == 1:
        inventory_path = project / "TEST_INVENTORY.yaml"
        if inventory_path.exists():
            import hashlib
            _cksum = hashlib.sha256(inventory_path.read_bytes()).hexdigest()
            _state_path = project / ".methodology" / "state.json"
            try:
                with file_lock(state_lock_path(_state_path.parent.parent)):
                    _state = load_state(project, lenient=True)
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
    if completed_phase >= 3 and _fr_ids_for_finalize:
        _missing_fr_finalize: list[str] = []
        for _frid in _fr_ids_for_finalize:
            # v2.13: pass completed_phase so the path matches finalize-gate's
            # per-phase write (Bug #121).
            _fs = _shared._finalize_sentinel_path(project, 1, _frid, phase=completed_phase)
            if _fs.exists():
                _invalid_finalize.extend(gate1_evidence.verify_finalize_evidence(
                    project, 1, completed_phase, _frid,
                ))
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

    if _invalid_finalize:
        # A present-but-unbacked receipt is a stronger signal than a missing
        # one: something wrote the proof without doing the work. Same exit
        # code — the remedy is identical (run the real finalize-gate) — but
        # the message names what was found rather than what was absent.
        print(
            "\n[BLOCKED] finalize-gate receipt(s) present but not backed by the\n"
            "          registries finalize-gate writes alongside them:\n"
            + "".join(f"  ✗ {m}\n" for m in _invalid_finalize)
            + "\n  finalize-gate writes the gate timestamp, the Gate 1 score and the\n"
            "  receipt in that order, so a receipt without the first two was not\n"
            "  written by finalize-gate. Re-run it for the gate(s) named above."
        )
        return 17

    # ── Milestone-push precondition (Phase 3 only) ────────────────────
    # advance-phase is the FSM's authoritative gate: any path that reaches
    # it (including a session resuming after an interruption, or a human
    # running it standalone) must be held to the same bar as the SOP's own
    # push-milestone --type p3-post-gate2 step. Without this, Phase 3 can
    # exit to Phase 4 with the PUSH ③/④/⑤ milestone checkpoints never
    # having been pushed — the quality-evidence checks above (finalize-gate
    # sentinels, Phase Truth) do not cover this, and previously nothing did.
    # Reuses the same precondition push-milestone p3-post-gate2 and
    # validate-handoff --from-phase 3 already enforce (_shared.py), so all
    # three call sites stay in sync on a single definition.
    if completed_phase == 3:
        _fr_ids_for_milestone = _resolve_fr_ids_from_manifest(project)
        _milestone_errors = _shared._validate_p3_post_gate2_precondition(
            project, _fr_ids_for_milestone
        )
        if _milestone_errors:
            print(
                "\n[BLOCKED] Phase 3 milestone precondition not met "
                "(push-milestone --type p3-post-gate2 requirements):\n"
                + "".join(f"  ✗ {m}\n" for m in _milestone_errors)
                + "\n  Run: python3 harness_cli.py push-milestone --type p3-post-gate2 "
                f"--project . --fr-ids {','.join(_fr_ids_for_milestone)}"
            )
            return 12

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

    # ── Anchors are an invariant of the deliverable, not of one load ──
    # Placement is the design: this runs AFTER the regen above, so the
    # render-only views the framework owns have already been repaired and
    # anything still failing is a file the framework has no right to rewrite.
    # See _broken_deliverable_anchors for the measurement.
    _anchor_breaks = _broken_deliverable_anchors(project)
    if _anchor_breaks:
        from cli.exit_codes import EX_ADVANCE_DELIVERABLE_ANCHOR_BROKEN

        print(
            "\n[BLOCKED] Deliverable(s) no longer start with the H1 anchor "
            "their path declares:"
        )
        for _line in _anchor_breaks:
            print(f"  - {_line}")
        print(
            "\n  The Phase 1/2 orchestrator reloads each of these with that "
            "exact prefix\n"
            "  (loadFileViaPython -> read-file --expect-prefix -> "
            "first_line.startswith).\n"
            "  A file that fails it aborts the sub-task after 3 attempts, so "
            "sealing the\n"
            "  phase now would hand the next run an artefact it cannot load.\n"
            "  Fix the first line of each file above, then re-run advance-phase.\n"
            "  If one of them is a render-only view (TRACEABILITY_MATRIX.md, "
            "SPEC_TRACKING.md),\n"
            "  the P3+ regen normally repairs it — check the "
            "[advance-phase] lines above for\n"
            "  a skipped regen (import or scan failure) rather than editing "
            "the view by hand."
        )
        return EX_ADVANCE_DELIVERABLE_ANCHOR_BROKEN

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
        #
        # Round 30 站6 — WITHDRAWN, both halves of the reasoning were wrong.
        #
        # The plan called for excluding .sessi-work/ and __pycache__ here,
        # because taskq-advance's .gitleaksignore silences 3 findings inside
        # gitleaks' own prior report and 2 inside .pyc mirrors of a test
        # fixture. Two measurements killed it:
        #   - `--exclude-path` does not exist. gitleaks 8.30.1 `detect` takes
        #     --source/--no-git/--config/--baseline-path; path allowlists live
        #     in .gitleaks.toml, not on the command line. The flag was assumed,
        #     not checked, and it made this call exit non-zero → rc 20 "secrets
        #     detected" on 6 tests.
        #   - The invocation below never sees those paths anyway. This is git
        #     mode, which scans COMMITS: with .sessi-work/ gitignored, a probe
        #     reports "1 commits scanned, ~20 bytes" versus ~56 for the same
        #     tree under --no-git. Those .gitleaksignore entries come from the
        #     AGENT's own working-tree run, which the framework does not issue
        #     and cannot scope from here.
        # What survives is in harness_bridge: the exclusion file must be tracked
        # (Round 29) and its digest travels with the verdict (this round).
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
        from core.phase_hooks import (
            PRAGMA_NO_COVER_ALLOWLIST,
            PRAGMA_NO_COVER_GUIDANCE,
            _audit_pragma_no_cover,  # Plan E: early pragma audit before coverage gate
        )
        from core.quality_gate.test_suite_run import run_suite
        _layout = ProjectLayout(project)
        src_dir = _layout.active_src_dir
        if src_dir.is_dir():
            # ── Plan E (Round 50+): early pragma audit ───────────────────
            # Detect non-allowlist `# pragma: no cover` BEFORE coverage/lint/
            # type. Closes the d0b3b9a → 476427d oscillation deterministically
            # (a commit that adds non-allowlist pragma can no longer pass the
            # coverage gate with synthetic coverage; it fails here first).
            # Why audit not a dispatch loop: auto_fix 13 strategies retired in
            # Round 48 because LLM-written tests silently passed. A
            # deterministic grep-based reject preserves the
            # human-in-the-loop contract and avoids the retry-LLM surface.
            _pragma_findings = _audit_pragma_no_cover([str(src_dir)])
            if _pragma_findings:
                _proj_root = str(project)
                for _pf in _pragma_findings:
                    _raw = _pf["file"]
                    if _raw.startswith(_proj_root):
                        _pf["file"] = _raw[len(_proj_root):].lstrip("/")
                print(
                    f"\n[BLOCKED] Non-allowlist `# pragma: no cover` found "
                    f"({len(_pragma_findings)} occurrence(s)) before "
                    f"advance-phase commit."
                )
                for _pf in _pragma_findings[:10]:
                    print(f"  - {_pf['file']}:{_pf['line']}")
                if len(_pragma_findings) > 10:
                    print(f"  ... and {len(_pragma_findings) - 10} more")
                print(f"  {PRAGMA_NO_COVER_GUIDANCE}")
                print(
                    "  Allowed pragma exemptions: "
                    + ", ".join(PRAGMA_NO_COVER_ALLOWLIST)
                )
                # exit 9 reserved for the coverage-gate series
                # (test / coverage / pragma) — keeps ops routing consistent
                # with the existing 100% coverage BLOCK.
                return 9

            # ── Plan F (Round 50+): early phantom module check ────────────
            # Detect FR-scope modules declared in SAB but missing from
            # disk BEFORE coverage/lint/type. Closes the silent-fall-through
            # shape that `validate_fr_coverage_immediate` had with the old
            # `Optional[list[str]]` return — a phantom FR scope used to
            # report a whole-project number and silently OK a non-existent
            # deliverable. Same shape as Plan E above; deterministic
            # rejection instead of a dispatch loop (auto_fix retired the
            # fabrication strategies in Round 48 for the same reason).
            #
            # Round 78 站1: the decision is sab_amender.phantom_module_block,
            # which resolves the source directory from `project` alone. The
            # inline version here read it relative to the process's working
            # directory, and from anywhere but the project root reported every
            # registered module as phantom — measured, all nine corpus
            # projects. What is left here is the call and the message.
            from core.quality_gate.sab_amender import phantom_module_block
            _phantoms = phantom_module_block(project)
            if _phantoms:
                print(
                    f"\n[BLOCKED] Phantom modules declared in SAB but "
                    f"missing from disk ({len(_phantoms)} module(s)) "
                    f"before advance-phase commit:"
                )
                for _pm in _phantoms[:10]:
                    print(f"  - {_pm}")
                if len(_phantoms) > 10:
                    print(f"  ... and {len(_phantoms) - 10} more")
                print(
                    "  Fix: implement the missing module, or amend SAB "
                    "via `harness_cli.py amend-sab --resolve-phantom "
                    "--fr-id <FR-ID> --reason <why>`."
                )
                return 9

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
                _mp_r = subprocess.run([sys.executable, "-m", "mypy", ".", "--ignore-missing-imports", *_MYPY_EXCLUDE_ARGS], cwd=str(project))
                if _mp_r.returncode != 0:
                    print("\n[BLOCKED] Type Safety (mypy) failure.")
                    print("  Please fix the type errors before advancing.")
                    return 19
            else:
                print("  [WARN] mypy not installed. Skipping type safety.")

            # Round 25: the suite runs once per advance-phase and every
            # threshold reads that one measurement. `--cov-fail-under=100` used
            # to make pytest itself render this verdict; the comparison is now
            # explicit here, against the exact percentage, so the same number
            # can also answer FrameworkEnforcer's 70/80 and Phase Truth's
            # without three more executions of the same tests.
            _suite = run_suite(project)
            if _suite.ran and not (_suite.passed and (_suite.coverage or 0.0) >= 100.0):
                if _suite.output:
                    print(_suite.output)
                print("\n[BLOCKED] TDD test/coverage failure.")
                if not _suite.passed:
                    print(f"  Tests did not pass ({_suite.reason or 'see output above'}).")
                else:
                    print(f"  Coverage {_suite.coverage:.2f}% < 100%.")
                print(f"  Fix: 100% coverage on {_suite.cov_target} required.")
                print(f"  {PRAGMA_NO_COVER_GUIDANCE}")
                print("  Allowed pragma exemptions: "
                      + ", ".join(PRAGMA_NO_COVER_ALLOWLIST))
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

    # Round 39 Station 1b: structural SAB validation at advance-phase.
    # preflight_sab_check (phase_hooks.py:613-691) already validates per-layer
    # allowed_dependencies (lines 644-648) and module existence — but it runs
    # only via preflight_all() in cmd_run_phase:1701 (the pre-push path),
    # never at advance-phase. Without this wire, a hand-edited SAB.json or
    # one generated from a SAD.md block that slipped past the now-extended
    # validate_sab_block reaches the handover commit and only trips at pre-
    # push. Running it here surfaces the violation BEFORE commit. Phase-
    # gated the same way as the DriftDetector block above (SAB itself only
    # exists from P2 exit). Wrapped in try/except to mirror the existing
    # resilience pattern — preflight_sab_check is best-effort: an unavailable
    # preflight must not block advance.
    if completed_phase >= 3:
        # Skip when SAB.json is missing — preflight_sab_check reports
        # passed=False in that case, but at advance-phase the absence is
        # only meaningful for completed_phase >= 4 (P3 itself just generated
        # the file). At completed_phase=3 the DriftDetector block above
        # already surfaces module-existence problems; the structural
        # allowed_dependencies check is only meaningful when there is a
        # SAB to validate. Treat missing-file as the same "skipped" path
        # the preflight takes for P1/P2.
        _sab_path = project / ".methodology" / "SAB.json"
        if not _sab_path.exists():
            print("  [INFO] SAB.json not present — skipping structural pre-advance check")
        else:
            try:
                from core.phase_hooks import PhaseHooks
                _sab_hooks = PhaseHooks(
                    str(project), phase=completed_phase,
                    enable_kill_switch=False,
                    drift_threshold=get_value(project, "drift_threshold"),
                )
                _sab_preflight = _sab_hooks.preflight_sab_check()
                if not _sab_preflight.get("passed", True):
                    _violations = _sab_preflight.get("violations", [])
                    print(
                        f"\n[BLOCKED] SAB structural violations — "
                        f"{len(_violations)} issue(s):"
                    )
                    for _v in _violations[:5]:
                        print(f"  - {_v}")
                    if len(_violations) > 5:
                        print(f"  ... and {len(_violations) - 5} more")
                    print(
                        "  Fix: amend SAD.md's SAB block so every layer name "
                        "referenced by allowed_dependencies is declared under "
                        "layers: []. Re-run generate_sab.py --overwrite."
                    )
                    return 12
            except ImportError:
                print("  [WARN] PhaseHooks unavailable — skipping SAB structural pre-advance check")
            except Exception as _sab_pre_err:  # pylint: disable=broad-exception-caught
                print(f"  [WARN] SAB structural pre-advance check error: {_sab_pre_err}")

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
