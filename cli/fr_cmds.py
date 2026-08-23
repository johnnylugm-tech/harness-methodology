"""FR-level TDD orchestration commands (dispatch, run-fr-step, resume-fr-phase, run-tool, reload-policy).

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
import subprocess
import sys
from pathlib import Path

from cli import gate_cmds
from cli.exit_codes import (
    EX_FR_STEP_INFRA_ABORT,
    EX_STEP_PRECONDITION_BLOCKED,
    EX_STEP_REPEATED_FAILURE,
)
from core.agent_spawner import (
    _COMMIT_REQUIRED_STEPS,
    PRECONDITION_BLOCKED,
    blocked_inner_status_in,
    is_structurally_broken,
    turn_budget_exhausted,
)
from core.canonical_form import fr_num_str
from core.degradation_ledger import record_degradation
from core.failure_modes import DISPATCH_FAILURE_STATUSES
from core.harness_config import get_timeout, get_value
from core.pre_flight import check_cli_tools
from core.quality_gate import gate1_evidence
from core.quality_gate.ghost_detector import (
    detect_ghost_changes,
    write_ghost_paper_trail,
)
from core import step_failure_memory as step_memory
from core.quality_gate import test_suite_run as suite_run
from core.quality_gate.legal_artifacts import PHASE_DELIVERABLES
from core.state_io import StateCorruptError, load_quality_manifest, load_state
from core.utils.project_layout import ProjectLayout
from harness import tool_checks

from cli.fr_prompts import (  # noqa: F401
    _build_fr_step_prompt,
    _compute_fr_spec_data,
    _extract_srs_fr_section,
    _extract_test_spec_names,
)


def cmd_dispatch(args: argparse.Namespace) -> int:
    """Dispatch Agent A or B via AgentSpawner, auto-logging to sessions_spawn.log.

    Usage:
        python harness_cli.py dispatch --role developer --fr-id FR-01 \\
            --prompt "Implement FR-01: Platform Adapter" --phase 3 --project .
        python harness_cli.py dispatch --role reviewer --fr-id FR-01 \\
            --prompt "Review FR-01 implementation against SRS" --phase 3 --project .
    """
    from core.agent_spawner import AgentSpawner

    project = Path(args.project).resolve()

    # --prompt-file: read prompt from file to avoid shell escaping issues
    # with {} curly braces, backticks, JSON examples, or $() in the prompt text.
    _prompt = args.prompt
    _prompt_file = getattr(args, "prompt_file", None)
    if _prompt_file:
        if _prompt:
            print("[dispatch] WARNING: --prompt-file takes precedence; --prompt ignored")
        try:
            _prompt = Path(_prompt_file).read_text(encoding="utf-8")
        except (FileNotFoundError, OSError) as exc:
            print(f"[dispatch] ERROR: cannot read --prompt-file: {exc}")
            return 1
        if not _prompt.strip():
            print("[dispatch] ERROR: --prompt-file is empty")
            return 1
    elif not _prompt:
        print("[dispatch] ERROR: --prompt or --prompt-file is required")
        return 1
    else:
        # When prompt is passed via --prompt (inline), the shell may have a
        # command-line length limit for large prompts. Suggest --prompt-file.
        if len(_prompt) > 500_000:
            print("[dispatch] WARNING: --prompt exceeds 500k chars — use --prompt-file instead")

    # P1/P2: validate --fr-id is a recognised deliverable ID (approval file naming).
    # --skip-deliverable-validation bypasses this check for custom reviews
    # (e.g. holistic cross-document review, P1_HOLISTIC / P2_HOLISTIC).
    _skip_dv = getattr(args, "skip_deliverable_validation", False)
    if args.phase in PHASE_DELIVERABLES and not _skip_dv:
        _valid_ids = PHASE_DELIVERABLES[args.phase]
        if not args.fr_id:
            print(
                f"[dispatch] ERROR: phase {args.phase} requires --fr-id (deliverable name).\n"
                f"  Valid IDs for P{args.phase}: {', '.join(_valid_ids)}\n"
                f"  Example: --fr-id {_valid_ids[0]}\n"
                f"  Or use --skip-deliverable-validation for custom review IDs."
            )
            return 1
        if args.fr_id not in _valid_ids:
            print(
                f"[dispatch] ERROR: phase {args.phase} requires --fr-id to be a deliverable name.\n"
                f"  Valid IDs for P{args.phase}: {', '.join(_valid_ids)}\n"
                f"  Got: {args.fr_id!r}\n"
                f"  Or use --skip-deliverable-validation for custom review IDs."
            )
            return 1
    spawner = AgentSpawner(project_path=project)
    role_lower = args.role.lower()
    # Detect Agent B (stateless reviewer) roles: names containing "review" or "analyst".
    # For custom roles not matching this heuristic, use --no-persona explicitly.
    is_reviewer = "review" in role_lower or "analyst" in role_lower
    no_persona = getattr(args, "no_persona", False)
    # STATELESS Agent B (reviewer): skip persona — persona causes Claude to enter
    # multi-step tool exploration mode instead of returning JSON directly (see SAD §reviewer_router).
    persona_override = "" if (is_reviewer or no_persona) else None
    # STATELESS Agent B: also skip SOP — the SOP is a large reference doc that
    # causes Claude to enter exploration mode instead of returning JSON directly.
    # TASK + CONTEXT alone is enough for a reviewer to produce structured output.
    sop_override = "" if (is_reviewer or no_persona) else None
    # Reviewer dispatches only need a single response turn; cap at 3 to prevent runaway.
    _explicit_max_turns = getattr(args, "max_turns", None)
    effective_max_turns = _explicit_max_turns if _explicit_max_turns is not None else (3 if is_reviewer else 20)
    # P1/P2 developer dispatches need more time to process large SPEC documents.
    # Use None sentinel to distinguish "user didn't specify" from explicit --timeout 300.
    _raw_timeout: int | None = args.timeout
    if _raw_timeout is None:
        _raw_timeout = (get_timeout("task_dev", project)
                        if (args.phase in {1, 2} and not is_reviewer)
                        else get_timeout("task_default", project))
    result = spawner.spawn(
        role=args.role,
        prompt=_prompt,
        context={"phase": args.phase, "fr_id": args.fr_id},
        phase=args.phase,
        fr_id=args.fr_id,
        task_timeout=_raw_timeout,
        max_turns=effective_max_turns,
        persona_override=persona_override,
        phase_sop_override=sop_override,
    )
    status = result.get("status", "SPAWNED")
    session_id = result.get("session_id", "")
    print(f"[dispatch] {args.fr_id or 'phase'} | {args.role} | {status} | session={session_id}")
    if status in _DISPATCH_ERROR_STATUSES:
        return 1

    # For completed reviewer dispatches, extract and persist Agent B approval JSON.
    if (
        status == "complete"
        and is_reviewer
        and args.fr_id
    ):
        output_text = result.get("output", "")
        review_data = _extract_review_json(output_text)
        if review_data:
            approvals_dir = project / ".methodology" / "agent_b_approvals"
            approvals_dir.mkdir(parents=True, exist_ok=True)
            approval_file = approvals_dir / f"{args.fr_id}.json"
            approval_file.write_text(
                json.dumps(review_data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            print(f"  [dispatch] approval JSON → {approval_file}")
        else:
            print(
                f"  [WARN] dispatch (reviewer): no review JSON found in agent output — "
                f"{args.fr_id}.json not written.\n"
                "  Ensure Agent B output includes a JSON block with 'review_status'."
            )

    # For completed developer dispatches, extract and persist Agent A structured output.
    if (
        status == "complete"
        and not is_reviewer
        and args.fr_id
    ):
        output_text = result.get("output", "")
        agent_output = _extract_agent_output_json(output_text)
        if agent_output:
            outputs_dir = project / ".methodology" / "agent_a_outputs"
            outputs_dir.mkdir(parents=True, exist_ok=True)
            output_file = outputs_dir / f"{args.fr_id}.json"
            output_file.write_text(
                json.dumps(agent_output, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            print(f"  [dispatch] agent output JSON → {output_file}")
        else:
            print(
                f"  [WARN] dispatch ({args.role}): no structured output JSON found — "
                f"{args.fr_id}.json not written.\n"
                "  Ensure Agent A output includes a JSON block with 'status', 'files', "
                "'confidence', 'citations', and 'summary'."
            )

    return 0


def cmd_run_fr_step(args: argparse.Namespace) -> int:
    """Dispatch a single FR TDD step as sub-agent + push to GitHub on completion.

    Steps: TDD-RED | TDD-GREEN | TDD-IMPROVE | GATE1 | GATE1-DELTA
    Idempotent: skips silently if the step's commit already exists in git log.
    On GATE1 FAIL: auto-dispatches CODE-FIX sub-agent then retries (max --max-fix-rounds).
    Returns: 0=OK, 1=ERROR, 2=BLOCKED (Gate1 exhausted retries — human needed)
    """
    import subprocess as _sp
    from core.agent_spawner import AgentSpawner

    phase = args.phase
    fr_id = args.fr_id
    step = args.step.upper()
    project = Path(args.project).resolve()
    srs_path = Path(args.srs).resolve() if args.srs else None

    # Compute src_dir and test_file — used by GATE1 retry and _capture_tool_snapshot.
    _num_str = fr_num_str(fr_id)
    src_dir = "03-development/src"
    _layout = ProjectLayout(project)
    test_dir_str = _layout.get_relative_str(_layout.active_test_dir)
    test_file = f"{test_dir_str}/test_fr{_num_str}.py"

    # Per-FR config: read fr_config from quality_manifest.json.
    # Allows large / complex FRs (e.g. FR-19 with 11-stage pipeline) to declare
    # longer timeouts and more fix rounds without changing global defaults.
    # Precedence (locked by tests/test_fr_cmds_values_wiring.py): per-FR
    # fr_config > explicit CLI flag > harness_config values > built-in.
    # (fr_config outranking an explicit CLI flag is pre-existing behavior,
    # kept verbatim — Round 9 only slots `values` in above the built-ins.)
    # Example manifest entry:
    #   {"fr_config": {"FR-19": {"timeout": 1200, "max_fix_rounds": 5,
    #                             "code_fix_max_turns": 90}}}
    _fr_conf = load_quality_manifest(project, lenient=True).get("fr_config", {}).get(fr_id, {})
    _fr_timeout = _fr_conf.get("timeout", getattr(args, "timeout", None))
    if _fr_timeout is None:
        _fr_timeout = get_timeout("fr_step", project)
    _fr_max_fix_rounds = _fr_conf.get("max_fix_rounds", getattr(args, "max_fix_rounds", None))
    if _fr_max_fix_rounds is None:
        _fr_max_fix_rounds = get_value(project, "max_fix_rounds")
    _fr_code_fix_max_turns: int | None = _fr_conf.get("code_fix_max_turns")

    # 1. Idempotency — skip if already committed
    if _fr_step_already_done(step, fr_id, project, phase=phase):
        print(f"[run-fr-step] {fr_id} {step}: already done → skip")
        #   gate1_evidence.record_gate_timestamp (GATE1-DELTA only) — prevents exit-14 block
        #     from _check_gate1_live_coverage when ALL FRs skip (no code changes)
        if step.upper() == "GATE1-DELTA":
            gate1_evidence.record_gate_timestamp(
                project, phase, 1, fr_id,
                source=gate1_evidence.EVIDENCE_SOURCE_SKIP,
            )
        return 0

    # 1a. Deterministic tools — skip LLM dispatch
    # `amend-sab` is a pure-mechanical tool (`core.quality_gate.sab_amender.amend_sab`)
    # that scans `03-development/src/` and writes SAB.json atomically. It is in
    # `_COMMIT_REQUIRED_STEPS` (core/agent_spawner.py:123) but does NOT need an
    # LLM — delegating directly to `cmd_amend_sab` (cli/project_cmds.py) avoids
    # spawning a sub-agent for a no-eval scan.
    #
    # This branch returns BEFORE the general post-step dirty-tree guard /
    # `_COMMIT_REQUIRED_STEPS` check below (they never run for an early
    # return), and `_COMMIT_REQUIRED_STEPS` itself stores this step as
    # lowercase "amend-sab" while `step` here is always upper-cased — so
    # neither backstop would have fired even if reached. `cmd_amend_sab`
    # never commits by design, so an uncommitted SAB.json mutation would
    # otherwise persist silently. Check it directly here instead.
    if step == "AMEND-SAB":
        from cli.project_cmds import cmd_amend_sab
        if not getattr(args, "src_dir", None):
            args.src_dir = "03-development/src"
        if not hasattr(args, "dry_run"):
            args.dry_run = False
        if not hasattr(args, "strict"):
            args.strict = False
        rc = cmd_amend_sab(args)
        if rc == 0 and not args.dry_run:
            _sab_dirty = subprocess.run(
                ["git", "status", "--porcelain", "--", ".methodology/SAB.json"],
                capture_output=True, text=True, cwd=str(project),
            ).stdout.strip()
            if _sab_dirty:
                print(
                    f"\n[BLOCKED] {fr_id} AMEND-SAB: SAB.json was updated but not "
                    f"committed.\n"
                    f"  cmd_amend_sab deliberately does not commit — the caller "
                    f"must:\n"
                    f"    git -C {project} add .methodology/SAB.json && "
                    f"git -C {project} commit -m \"amend: register SAB modules "
                    f"({fr_id})\"\n"
                    f"  then re-run this step (idempotent — will no-op once "
                    f"committed).\n"
                    f"  Uncommitted status:\n{_sab_dirty}",
                    file=sys.stderr,
                )
                return 6  # Same exit code as the general dirty-tree guard below
        return rc

    # 2. Pre-flight checks — must pass before agent dispatch
    preflight_ok, preflight_errors = _fr_step_preflight(step, project, fr_id, srs_path=srs_path)
    if not preflight_ok and step in ("GATE1", "GATE1-DELTA", "CODE-FIX"):
        # Round 47 站3: repair lives HERE, not inside _fr_step_preflight.
        # A function whose whole contract is "(ok, errors)" must not install
        # things — Round 43 站1 drew that line when it moved the traceability
        # auto-fix out of preflight_traceability and into its caller. A P3 run
        # is hours long; a tool can go missing after the phase entry that
        # verified it, and this is the step that then needs it.
        from harness.env_repair import repair_missing_tools
        _gate1_missing = tool_checks.missing_gate_tool_ids(1, str(project))
        if _gate1_missing:
            _outcome = repair_missing_tools(project, _gate1_missing)
            if _outcome.attempted_steps:
                print(f"[REPAIR] run-fr-step: installed {', '.join(_outcome.attempted_steps)}")
            preflight_ok, preflight_errors = _fr_step_preflight(
                step, project, fr_id, srs_path=srs_path
            )
    if not preflight_ok:
        print(f"\n[PRE-FLIGHT FAILED] run-fr-step --fr-id {fr_id} --step {step}", file=sys.stderr)
        for err in preflight_errors:
            print(f"  {err}", file=sys.stderr)
        print(file=sys.stderr)
        return 1

    # 3. Build minimal need-to-know prompt (only after pre-flight passes)
    prompt = _build_fr_step_prompt(step, fr_id, phase, project, srs_path)

    # 4. Dispatch sub-agent (phase_sop_override="" skips full SOP load)
    spawner = AgentSpawner(project_path=project)
    phase_ctx = _resolve_phase3_context(project)
    if getattr(args, "no_mcp", False):
        phase_ctx["mcp_config"] = None

    _explicit_max_turns = getattr(args, "max_turns", None)

    _turns_overlay = get_value(project, "step_max_turns")
    _unknown_steps = sorted(set(_turns_overlay) - set(_STEP_MAX_TURNS))
    if _unknown_steps:
        print(f"[run-fr-step] WARN: values.step_max_turns key(s) {_unknown_steps} "
              f"match no step; valid: {sorted(_STEP_MAX_TURNS)}")

    # Round 26: steps already cut off at their ceiling in THIS invocation. A
    # re-dispatch at the identical ceiling is what the log shows happening — all
    # three of taskq-plus P3's turn-budget kills went out again at the same number
    # — so the second attempt gets the doubled budget once, and only once.
    _turn_budget_escalated: set[str] = set()

    def _max_turns(step_name: str) -> int:
        """Per-step max_turns: explicit --max-turns wins, then per-FR config,
        then values.step_max_turns (per-project overlay), else _STEP_MAX_TURNS.

        A step in `_turn_budget_escalated` gets twice its resolved ceiling. The
        factor and the once-per-step bound are the whole limit — no absolute cap
        constant, because a magic number here would be the same unexplained
        threshold Round 20 站G removed from doctor.
        """
        if _explicit_max_turns is not None:
            return _explicit_max_turns
        step = step_name.upper()
        if step in ("CODE-FIX", "COVERAGE-FIX") and _fr_code_fix_max_turns:
            base = _fr_code_fix_max_turns
        elif step in _turns_overlay:
            base = _turns_overlay[step]
        else:
            base = _STEP_MAX_TURNS.get(step, 40)
        return base * 2 if step in _turn_budget_escalated else base

    def _note_turn_budget_kill(step_name: str, result: dict) -> bool:
        """Record a turn-budget kill so the next dispatch of `step_name` escalates.

        Returns True when this failure was a budget kill (the caller then knows the
        step ran out of room rather than hitting a code defect). Escalation happens
        at most once per step: a second kill at the doubled ceiling means the step
        genuinely cannot finish, and the caller aborts with the normal diagnostic
        rather than doubling forever.
        """
        # Re-derive rather than trust the stamp: same reasoning as
        # core.failure_modes._effective_error_class — a decision that depends on
        # one upstream layer having labelled the entry goes silently dead when
        # that layer changes, and this round exists because exactly that happened
        # to the INFRA guard.
        if result.get("error_class") != "TURN_BUDGET" and not turn_budget_exhausted(
            str(result.get("output") or "")
        ):
            return False
        step = step_name.upper()
        if step in _turn_budget_escalated:
            print(f"[run-fr-step] {fr_id} {step}: turn budget exhausted AGAIN at the "
                  f"escalated ceiling ({_max_turns(step)}) — not escalating further",
                  file=sys.stderr)
            return True
        _turn_budget_escalated.add(step)
        # The `why` stays generic on purpose: it is written into the CONSUMING
        # project's ledger, and a framework string that names another project is
        # the 76b849c prompt-leak shape (tests/test_no_hardcoded_paths.py). The
        # measured history lives in this file's comments, not in their artifacts.
        record_degradation(
            project, component=f"run-fr-step:{step}",
            what=f"max_turns escalated {_max_turns(step) // 2} -> {_max_turns(step)}",
            why=f"{fr_id} {step} was cut off at its turn ceiling; re-dispatching at "
                f"the same ceiling cannot finish what did not fit", owner="infra"
        )
        print(f"[run-fr-step] {fr_id} {step}: turn budget exhausted — re-dispatching "
              f"at {_max_turns(step)} turns (escalation recorded in the degradation "
              f"ledger). This is NOT a code defect; no fix agent is dispatched.")
        return True

    # Round 30 站5 — the wall-clock half of the same idea.
    #
    # Round 29 站5 made a wall-clock timeout visible (error_class + a ledger
    # line) and left the retry unchanged: "retrying with identical prompt", same
    # ceiling. taskq-advance's P3 shows what that buys — 12 of its 18 failed
    # dispatches were 600.0s timeouts, four of them consecutive on FR-02 at
    # ten-minute intervals, every one re-dispatched into the same wall. Two hours
    # of wall time whose only outcome was the same failure four times.
    #
    # The turn-budget path already had the answer next door: a step cut off at
    # its ceiling gets that ceiling doubled once. The same reasoning applies
    # verbatim — "re-dispatching at the same ceiling cannot finish what did not
    # fit" — and the only reason wall-clock did not have it is that nobody wrote
    # it. Once per step, same as turns: a second timeout at the doubled budget
    # means the step genuinely cannot finish, and the caller aborts normally.
    _wallclock_escalated: set[str] = set()

    def _timeout_for(step_name: str) -> int:
        """Per-step wall-clock budget, doubled once after a timeout.

        No absolute cap constant: the factor and the once-per-step bound are the
        whole limit, and a magic ceiling here would be the unexplained threshold
        Round 20 站G removed from doctor. A project that needs a different base
        sets it through values.timeouts / fr_config, the channels _fr_timeout
        already reads.
        """
        return _fr_timeout * 2 if step_name.upper() in _wallclock_escalated else _fr_timeout

    def _note_wallclock_kill(step_name: str, result: dict) -> bool:
        """Record a wall-clock timeout so the next dispatch of `step_name` gets
        longer. Returns True when this failure was a timeout."""
        if result.get("status") != "TIMEOUT":
            return False
        step = step_name.upper()
        if step in _wallclock_escalated:
            print(f"[run-fr-step] {fr_id} {step}: timed out AGAIN at the escalated "
                  f"budget ({_timeout_for(step)}s) — not escalating further",
                  file=sys.stderr)
            return True
        _wallclock_escalated.add(step)
        # Generic `why` for the same reason as the turn-budget ledger entry
        # above: it is written into the CONSUMING project's ledger.
        record_degradation(
            project, component=f"run-fr-step:{step}",
            what=f"task_timeout escalated {_fr_timeout} -> {_timeout_for(step)}",
            why=f"{fr_id} {step} hit its wall-clock budget; re-dispatching at the "
                f"same budget cannot finish what did not fit", owner="infra"
        )
        print(f"[run-fr-step] {fr_id} {step}: wall-clock timeout — re-dispatching "
              f"with {_timeout_for(step)}s (escalation recorded in the degradation "
              f"ledger). This is NOT a code defect; no fix agent is dispatched.")
        return True

    # All FR steps need shell access:
    #   GATE1/GATE1-DELTA: ruff, pyright, pytest, coverage
    #   TDD-RED/GREEN/IMPROVE: pytest to verify fail/pass
    #   CODE-FIX: pytest to confirm fix doesn't break other tests
    # acceptEdits blocks Bash → agents skip verification steps and commit
    # broken code, causing the next GATE1 to fail again.
    _explicit_pmode = getattr(args, "permission_mode", None)
    _pmode = (_explicit_pmode if _explicit_pmode is not None
              else get_value(project, "permission_mode"))

    # ── Ghost paper-trail detection: capture pre-dispatch HEAD SHA ──────────
    # Reuses AgentSpawner's static method so we can diff pre/post state after
    # the agent finishes and verify it made substantive code changes.
    # Try/except guards against mock spawners in tests that don't implement
    # _git_head_sha.
    try:
        _pre_step_sha = AgentSpawner._git_head_sha(project) or ""
    except Exception as exc:
        print(f"[WARN] run-fr-step: could not read pre-dispatch HEAD sha "
              f"(ghost-detection diff will compare against empty): {exc}", file=sys.stderr)
        _pre_step_sha = ""

    # ── Dirty-tree guard baseline: capture pre-step `git status --porcelain` ──
    # The post-step dirty-tree guard (below) only blocks on directory lines
    # NEWLY introduced by this step, not on pre-existing unrelated dirt.
    # Compare raw porcelain lines (XY-prefixed, e.g. " M foo.py" vs "M  foo.py")
    # — same path with different XY encodes a staged↔unstaged transition that
    # is the exact signal the guard exists to catch. Only captured for steps
    # in _COMMIT_REQUIRED_STEPS (the guard itself only checks those).
    _pre_step_dirty: set[str] = set()
    if step in _COMMIT_REQUIRED_STEPS:
        try:
            _pre_step_dirty = set(
                subprocess.run(
                    ["git", "status", "--porcelain"],
                    capture_output=True, text=True, cwd=str(project),
                ).stdout.splitlines()
            )
        except Exception as exc:
            print(f"[WARN] run-fr-step: could not capture pre-step dirty-tree "
                  f"baseline (guard will fall back to whole-tree check): {exc}",
                  file=sys.stderr)
            _pre_step_dirty = set()

    # Fix H-H (P3 2026-07-15 round 4): TDD-RED/GREEN/IMPROVE/MIRROR/amend-sab/
    # ORCH-POST (_COMMIT_REQUIRED_STEPS minus GATE1/GATE1-DELTA) previously had
    # zero retry on this first dispatch — see _STEP_RETRY_ATTEMPTS docstring.
    # Bounded plain re-dispatch (identical prompt, no failure classification)
    # before falling through to the unchanged error-handling block below.
    # REGRESSION_GUARD (hard reject) and an already-exhausted STRUCTURAL
    # signature (Fix H-G already retried that 3x at the transport layer) are
    # never retried here — both fall straight through on attempt 1.
    # Round 41 站3: what earlier PROCESSES already spent on this step. The
    # in-process retry below is bounded; the outer loop that re-invokes this
    # command was not bounded by anything, because every counter it could have
    # read lived in a process that had exited.
    _tree = step_memory.tree_fingerprint(project)
    _seen_before = step_memory.repeated_failure(
        project, fr_id, step, _tree, _STEP_RETRY_ATTEMPTS
    )
    if _seen_before is not None:
        return _abort_repeated_failure(fr_id, step, phase, project, _seen_before)

    result: dict = {}
    _status: str | None = None
    for _step_attempt in range(1, _STEP_RETRY_ATTEMPTS + 1):
        result = spawner.spawn(
            role="developer",
            prompt=prompt,
            context={"phase": phase, "fr_id": fr_id, "step": step},
            phase=phase,
            fr_id=fr_id,
            phase_sop_override="",
            task_timeout=_timeout_for(step),
            max_turns=_max_turns(step),
            mcp_config=phase_ctx["mcp_config"],
            setting_sources=phase_ctx["setting_sources"],
            permission_mode=_pmode,
        )
        _status = result.get("status")
        # Round 41 站3: every failed dispatch leaves a durable record, keyed by
        # (FR, step, signature, tree). Written before the retry decision so the
        # in-process retry's own failures count too — they are dispatches the
        # framework paid for, and the next process must know they happened.
        if _status in _DISPATCH_ERROR_STATUSES:
            step_memory.record_step_failure(project, fr_id, step, result, _tree)
        # Round 26: a turn-budget kill is retryable for EVERY step, GATE1 included,
        # because the remedy is more room rather than a different agent. Handing a
        # cut-off GATE1 to CODE-FIX sent a fixer at code with no defect — and the
        # prompt told it "sub-agent timeout or error", a diagnosis the framework had
        # already contradicted by classifying the kill.
        _budget_kill = _note_turn_budget_kill(step, result)
        # Round 30 站5: same treatment for the wall-clock half. A timeout is a
        # budget kill too — the agent was working when the clock ran out — so it
        # is retryable for every step and escalates the budget once.
        _budget_kill = _note_wallclock_kill(step, result) or _budget_kill
        _step_retryable = (
            (_budget_kill or (
                step in _COMMIT_REQUIRED_STEPS
                and step not in ("GATE1", "GATE1-DELTA")
            ))
            and _status in _DISPATCH_ERROR_STATUSES
            and _status != "REGRESSION_GUARD"
            and not _is_connector_disabled_failure(result.get("output", ""))
            # Round 26: a reported precondition blocker (agent_spawner's
            # _INNER_BLOCKED_SIGNATURES -> error_class "INFRA") is deterministic
            # — the tools never ran, and an identical re-dispatch cannot change
            # that. Same reasoning as the connector-disabled carve-out above; the
            # fix loop's Round 13 站2a short-circuit turns it into an abort with
            # the operator's remediation instead of a wasted round.
            and result.get("error_class") != "INFRA"
        )
        if not _step_retryable or _step_attempt == _STEP_RETRY_ATTEMPTS:
            break
        print(f"[run-fr-step] {fr_id} {step}: sub-agent {_status} "
              f"(attempt {_step_attempt}/{_STEP_RETRY_ATTEMPTS}) — "
              f"retrying with identical prompt")

    if _status in _DISPATCH_ERROR_STATUSES:
        # GATE1/GATE1-DELTA: ERROR or TIMEOUT means sub-agent exhausted
        # turns before writing gate1_result.json. Treat as GATE1 FAIL so
        # the CODE-FIX retry loop gets a chance to re-run with fresh context.
        # REJECT/BLOCKED/FAILED are hard-fail (non-turn issues).
        if step in ("GATE1", "GATE1-DELTA") and _status in {"ERROR", "TIMEOUT"}:
            print(
                f"[run-fr-step] {fr_id} GATE1 {_status} "
                f"— treating as GATE1 FAIL, entering CODE-FIX retry"
            )
        elif _status == "REGRESSION_GUARD":
            # Sub-agent made suspicious destructive edits — print
            # the captured flags so the operator can see what was caught
            # (e.g. "TaskStatus.RUNNING=None" sentinel injection, or a
            # single-file line-removal spike).
            flags = result.get("regression_flags", {})
            print(f"[run-fr-step] {fr_id} {step}: REGRESSION_GUARD")
            for fname, flist in flags.items():
                print(f"  {fname}: {flist}")
            print("[run-fr-step] Sub-agent dispatch REJECTED — manual review required.")
            return 1
        else:
            _output = result.get("output", "")
            if _is_connector_disabled_failure(_output):
                return _abort_dispatch_structurally_broken(fr_id, step, phase, project)
            # Round 41 站2: "I could not run because my precondition is unmet"
            # is a result, not a failure — but only if it is true.
            if _reports_precondition_block(result):
                _rc = _resolve_precondition_block(fr_id, step, phase, project, _output)
                if _rc is not None:
                    return _rc
            print(f"[run-fr-step] {fr_id} {step}: sub-agent {_status}")
            print(_output[:500])
            return 1

    # 4. GATE1: auto-retry with CODE-FIX sub-agent on failure
    if step in ("GATE1", "GATE1-DELTA"):
        # FIX II: after sub-agent returns, verify manifest was actually
        # updated. Sub-agents sometimes skip finalize-gate (write
        # gate1_result.json but never call finalize-gate itself), which
        # means quality_manifest.json gate1[fr].quality_complete stays
        # False regardless of evaluation score. Detect this and run
        # finalize-gate directly so the manifest stays in sync.
        if _status not in {"ERROR", "TIMEOUT"}:
            _mf_json = load_quality_manifest(project, lenient=True)
            _fr_entry = (_mf_json.get("gate_results", {})
                         .get("gate1", {}).get(fr_id, {}))
            _mf_qc = bool(_fr_entry.get("quality_complete", False))
            _sub_reported_pass = (
                isinstance(result.get("output", ""), str)
                and "GATE1: PASS" in result["output"]
            )
            if _sub_reported_pass and not _mf_qc:
                print(
                    f"[run-fr-step] {fr_id} sub-agent reported GATE1 PASS "
                    f"but manifest quality_complete is False — "
                    f"sub-agent likely skipped finalize-gate. "
                    f"Running finalize-gate directly."
                )
                try:
                    _fix_args = argparse.Namespace(**vars(args))
                    _fix_args.gate = 1
                    _fix_args.fr_id = fr_id
                    _fix_args.phase = args.phase
                    _fix_args.project = str(project)
                    _fix_args.delta = False
                    _fix_rc = gate_cmds._cmd_finalize_gate_impl(_fix_args)
                    if _fix_rc == 0:
                        print(
                            f"[run-fr-step] {fr_id} finalize-gate "
                            f"recovered — manifest patched"
                        )
                    else:
                        print(
                            f"[run-fr-step] {fr_id} finalize-gate "
                            f"recovery failed (rc={_fix_rc})"
                        )
                except Exception as _fix_exc:
                    print(
                        f"[run-fr-step] {fr_id} finalize-gate recovery "
                        f"exception: {_fix_exc}"
                    )

        # When agent timed-out or errored, no gate1_result.json was written —
        # failing_dims cannot be parsed. Signal full re-check to CODE-FIX.
        if _status in {"ERROR", "TIMEOUT"}:
            gate_pass = False
            failing_dims: list | None = None
            block_reason = ""
        else:
            gate_pass, failing_dims, block_reason = _parse_gate_output(result.get("output", ""))
        if not gate_pass:
            gate_pass = _fr_step_already_done(step, fr_id, project, phase=phase)

        # ── Pragma no-cover audit (GATE1/DELTA) ──────────────────────────
        # Run during GATE1 evaluation — not just at pre-push — so agents
        # are forced to fix pragma issues as part of the TDD loop.
        # Semgrep cannot match Python comments, so this runs independently.
        _pragma_targets = [str(project / d) for d in ("03-development/src", "src")
                           if (project / d).is_dir()]
        if _pragma_targets:
            from core.phase_hooks import _audit_pragma_no_cover
            _pf = _audit_pragma_no_cover(_pragma_targets)
            if _pf:
                _pf_files = {f["file"] for f in _pf}
                print(
                    f"\n[PRAGMA AUDIT] {fr_id} GATE1: "
                    f"{len(_pf)} # pragma: no cover workaround(s) in "
                    f"{len(_pf_files)} file(s):"
                )
                for f in _pf[:8]:
                    print(f"    {f['file']}:{f['line']} — {f['message'][:100]}")
                if len(_pf) > 8:
                    print(f"    ... and {len(_pf) - 8} more")
                if gate_pass:
                    print(
                        "  Sub-agent reported GATE1 PASS but pragma audit "
                        "found untested code — overriding to FAIL."
                    )
                gate_pass = False
                failing_dims = (failing_dims or []) + ["pragma-no-cover"]
                block_reason = (
                    f"pragma-no-cover: {len(_pf)} workaround(s) — "
                    f"write unit tests and remove # pragma: no cover"
                )

        max_fix_rounds = _fr_max_fix_rounds
        # B: progress tracking — detect lateral variation (same error, no progress)
        prev_snapshot_sig: str = ""
        no_progress_count: int = 0

        _last_failure_class: str | None = None
        fix_round = 0  # stays 0 if max_fix_rounds <= 0 and the loop body never runs
        for fix_round in range(1, max_fix_rounds + 1):
            if gate_pass or _fr_step_already_done(step, fr_id, project, phase=phase):
                break

            # ── Round 13 站2a: HARNESS_BUG/INFRA short-circuit — takes priority
            # over the S3 check below (retrying GATE1 does not help either).
            _infra_check = _classify_infra_or_harness_bug(result.get("output", ""))
            if _infra_check:
                return _abort_dispatch_infra_or_harness_bug(
                    fr_id, step, phase, project, *_infra_check
                )

            # ── S3 short-circuit: evaluation JSON was malformed, not code error ──
            # tool_evidence_missing means the sub-agent fabricated scores.
            # CODE-FIX (source code fixer) cannot help — skip it and retry GATE1
            # directly with the block_reason injected so the evaluator understands
            # what went wrong with its predecessor's gate1_result.json.
            is_s3 = bool(block_reason and "tool_evidence_missing" in block_reason)
            if not is_s3:
                # ── Pre-run tools at orchestration time ──────────────────────────
                # Capture actual ruff + pytest output so fix agents target real errors.
                tool_snapshot = _capture_tool_snapshot(project, src_dir, test_file)

                # ── B: lateral variation detection ───────────────────────────────
                curr_sig = tool_snapshot[:300] if tool_snapshot else ""
                if curr_sig and curr_sig == prev_snapshot_sig:
                    no_progress_count += 1
                    print(f"[run-fr-step] {fr_id} NO PROGRESS detected (round {fix_round})"
                          f" — same error signature as previous round")
                    if no_progress_count >= 2:
                        return _abort_no_progress_with_self_doubt(
                            fr_id, step, project, _last_failure_class, curr_sig
                        )
                else:
                    no_progress_count = 0
                prev_snapshot_sig = curr_sig

                # ── A: classify failure → route to the correct fixer ─────────────
                failure_class = _classify_snapshot_failure(tool_snapshot, failing_dims=failing_dims)
                _last_failure_class = failure_class

                if failure_class == "ENV":
                    print(f"[run-fr-step] {fr_id} ENV error — human intervention required\n"
                          f"  Hint: check PYTHONPATH / package installation")
                    break

                if failure_class == "ISOLATION":
                    print(f"[run-fr-step] {fr_id} ISOLATION failure "
                          f"(round {fix_round}/{max_fix_rounds})"
                          f" — dispatching TEST-FIX (add autouse infra mock)")
                    fix_prompt = _build_fr_step_prompt(
                        "TEST-FIX", fr_id, phase, project, srs_path,
                        tool_snapshot=tool_snapshot,
                    )
                    fix_step_name = "TEST-FIX"
                elif failure_class == "ISOLATION_LIKELY":
                    # v2.13.0 (FR-05 P3 2026-07-16 lesson): the snapshot shows a
                    # test_shape bug (stdlib-shadow AttributeError or subprocess
                    # ModuleNotFoundError) but pytest's summary line still reports
                    # `21 passed` — so the simple `tests_failed > 0` branch above
                    # never triggers. Route to TEST-FIX anyway so the sub-agent
                    # sees the stderr signal and can rewrite the failing test.
                    print(f"[run-fr-step] {fr_id} ISOLATION_LIKELY failure "
                          f"(round {fix_round}/{max_fix_rounds})"
                          f" — dispatching TEST-FIX (test_shape bug: "
                          f"stdlib-shadow or subprocess env propagation)")
                    fix_prompt = _build_fr_step_prompt(
                        "TEST-FIX", fr_id, phase, project, srs_path,
                        tool_snapshot=tool_snapshot,
                    )
                    fix_step_name = "TEST-FIX"
                elif failure_class == "INFRA_SKIP":
                    print(f"[run-fr-step] {fr_id} INFRA_SKIP failure "
                          f"(round {fix_round}/{max_fix_rounds})"
                          f" — dispatching INFRA-FIX (add mock tests for skipped paths)")
                    fix_prompt = _build_fr_step_prompt(
                        "INFRA-FIX", fr_id, phase, project, srs_path,
                        tool_snapshot=tool_snapshot,
                    )
                    fix_step_name = "INFRA-FIX"
                elif failure_class in ("LINT_FAIL", "LINT_AND_COVERAGE"):
                    label = ("linting only" if failure_class == "LINT_FAIL"
                             else "linting + coverage — linting first")
                    print(f"[run-fr-step] {fr_id} {failure_class} failure "
                          f"(round {fix_round}/{max_fix_rounds})"
                          f" — dispatching LINT-FIX ({label})")
                    fix_prompt = _build_fr_step_prompt(
                        "LINT-FIX", fr_id, phase, project, srs_path,
                        tool_snapshot=tool_snapshot,
                    )
                    fix_step_name = "LINT-FIX"
                elif failure_class == "PATCH_OBJECT":
                    print(f"[run-fr-step] {fr_id} PATCH_OBJECT failure "
                          f"(round {fix_round}/{max_fix_rounds})"
                          f" — dispatching CODE-FIX with stub hint")
                    patch_hint = (
                        "[PATCH_OBJECT HINT]\n"
                        "A test uses patch.object() on a method that does not exist yet.\n"
                        "Add the missing method stub to your implementation FIRST, "
                        "before any other logic.\n\n"
                    )
                    fix_prompt = patch_hint + _build_fr_step_prompt(
                        "CODE-FIX", fr_id, phase, project, srs_path,
                        failing_dims=failing_dims, tool_snapshot=tool_snapshot,
                    )
                    fix_step_name = "CODE-FIX"
                elif failure_class == "LOW_COVERAGE":
                    print(f"[run-fr-step] {fr_id} LOW_COVERAGE failure "
                          f"(round {fix_round}/{max_fix_rounds})"
                          f" — dispatching COVERAGE-FIX (tests pass, coverage < 80%)")
                    fix_prompt = _build_fr_step_prompt(
                        "COVERAGE-FIX", fr_id, phase, project, srs_path,
                        tool_snapshot=tool_snapshot,
                    )
                    fix_step_name = "COVERAGE-FIX"
                else:
                    print(f"[run-fr-step] {fr_id} GATE1 FAIL (round {fix_round}/{max_fix_rounds})"
                          f" — dispatching CODE-FIX sub-agent"
                          f" [failure_class={failure_class}]")
                    fix_prompt = _build_fr_step_prompt(
                        "CODE-FIX", fr_id, phase, project, srs_path,
                        failing_dims=failing_dims, tool_snapshot=tool_snapshot,
                    )
                    fix_step_name = "CODE-FIX"

                fix_result = spawner.spawn(
                    role="developer", prompt=fix_prompt,
                    context={"phase": phase, "fr_id": fr_id, "step": fix_step_name},
                    phase=phase, fr_id=fr_id, phase_sop_override="",
                    task_timeout=_fr_timeout,
                    max_turns=_max_turns(fix_step_name),
                    mcp_config=phase_ctx["mcp_config"],
                    setting_sources=phase_ctx["setting_sources"],
                    retry_round=fix_round,  # Fix H-F: surface iteration in sessions_spawn.log
                )
                # Round 26: a fix agent cut off at its ceiling did not fail to fix
                # the code — it ran out of room. Record it so the next fix round
                # dispatches with the doubled budget instead of walking into the
                # same wall (taskq-plus FR-05's CODE-FIX died at turn 51 of 50).
                _note_turn_budget_kill(fix_step_name, fix_result)
                if fix_result.get("status") in _DISPATCH_ERROR_STATUSES:
                    _fix_output = fix_result.get('output', '')
                    print(f"[run-fr-step] {fix_step_name} failed: "
                          f"{_fix_output[:200]}")
                    if _is_connector_disabled_failure(_fix_output):
                        return _abort_dispatch_structurally_broken(fr_id, step, phase, project)
                    # [LINT-FIX fallback] If the spawned LINT-FIX sub-agent
                    # failed (e.g. ANTHROPIC_API_KEY precedence disabling
                    # claude.ai connectors), try an inline repair with ruff
                    # before giving up. This avoids the "sub-agent bypass"
                    # pattern where the TDD agent hand-writes
                    # gate1_result.json because LINT-FIX was blocked.
                    if fix_step_name == "LINT-FIX":
                        _did = False
                        for _tool_cmd, _fix_flow in (
                            ("python3", ["-m", "ruff", "check", src_dir, "--fix"]),
                            ("python3", ["-m", "ruff", "format", src_dir]),
                        ):
                            try:
                                _tr = subprocess.run(
                                    [_tool_cmd] + _fix_flow,
                                    capture_output=True, timeout=30,
                                )
                                if _tr.returncode == 0:
                                    _did = True
                            except Exception as _tool_err:
                                print(f"  [WARN] run-fr-step: LINT-FIX inline fallback "
                                      f"attempt '{' '.join([_tool_cmd] + _fix_flow)}' "
                                      f"could not run: {_tool_err}", file=sys.stderr)
                        if _did:
                            print("  [run-fr-step] LINT-FIX inline fallback "
                                  "applied (ruff) — continuing with GATE1")
                            continue  # retry GATE1 with fixed lint
                        print("  [run-fr-step] LINT-FIX inline fallback "
                              "failed — try manual fix")
                    # [COVERAGE-FIX fallback] Same dispatch-error class as
                    # LINT-FIX: the spawned COVERAGE-FIX sub-agent failed
                    # because claude.ai connectors were blocked. Unlike
                    # LINT-FIX (where ruff can auto-repair) we cannot
                    # auto-write missing tests, but we CAN measure ground
                    # truth: if live pytest --cov already meets min_coverage
                    # the agent's LOW_COVERAGE classification was a false
                    # positive — fall through to GATE1 instead of blocking.
                    # If coverage is genuinely below threshold, still
                    # break for human intervention, but now print the REAL
                    # number (not the agent's possibly-fabricated 66.0) so
                    # the operator knows the actual delta.
                    elif fix_step_name == "COVERAGE-FIX":
                        from core.quality_gate import min_coverage_floor
                        _mfst = load_quality_manifest(Path(str(project)), lenient=True)
                        _cov_min = min_coverage_floor(_mfst)
                        try:
                            _live_cov = gate1_evidence.validate_fr_coverage_immediate(
                                Path(str(project)), fr_id=fr_id)
                        except Exception as _exc:
                            _live_cov = None
                            print("  [run-fr-step] COVERAGE-FIX inline "
                                  f"measurement failed: {_exc}")
                        if _live_cov is not None and _live_cov >= _cov_min:
                            # [manifest update] sub-agent's LOW_COVERAGE
                            # classification was a false positive. The
                            # authoritative Gate 1 verdict (phase4-testing.js
                            # line ~290) reads gate_results.gate1.{fr_id}.
                            # quality_complete directly from the manifest —
                            # just `continue` would still leave the stale
                            # quality_complete=False from the prior run and
                            # trip the verify-agent check. Stamp the live
                            # ground truth into the manifest so the next
                            # GATE1 re-evaluation sees the corrected flag.
                            try:
                                _mfst_path = Path(str(project)) / ".methodology" \
                                    / "quality_manifest.json"
                                _mfst = load_quality_manifest(Path(str(project)))
                                _gr = _mfst.setdefault(
                                    "gate_results", {})
                                _g1 = _gr.setdefault("gate1", {})
                                _fr_entry = _g1.setdefault(fr_id, {})
                                _fr_entry["quality_complete"] = True
                                _fr_entry["score"] = float(_live_cov)
                                _fr_entry["coverage_fallback"] = (
                                    "inline-fallback@8abe4f9"
                                )
                                # Write atomically (write-temp + os.replace)
                                # to avoid mid-write corruption that
                                # manifest integrity check would catch.
                                _tmp = _mfst_path.with_suffix(
                                    ".json.tmp")
                                _tmp.write_text(
                                    json.dumps(_mfst, indent=2,
                                               sort_keys=True),
                                    encoding="utf-8")
                                import os as _os
                                _os.replace(str(_tmp), str(_mfst_path))
                                print("  [run-fr-step] COVERAGE-FIX inline "
                                      "fallback: stamped "
                                      f"gate1.{fr_id}.quality_complete=True "
                                      f"(score={_live_cov:.1f})")
                            except (OSError, ValueError,
                                    StateCorruptError) as _mfst_exc:
                                print("  [run-fr-step] COVERAGE-FIX inline "
                                      "fallback: manifest stamp failed "
                                      f"({_mfst_exc}) — continuing GATE1 "
                                      "anyway; verify-agent may still trip")
                            print("  [run-fr-step] COVERAGE-FIX inline "
                                  "fallback: whole-project coverage "
                                  f"{_live_cov:.1f}% ≥ {_cov_min:.0f}% — "
                                  "sub-agent LOW_COVERAGE was likely a "
                                  "false positive (whole-project is a "
                                  "noisy proxy, not per-FR — humans should "
                                  "still verify FR-scoped coverage at "
                                  "advance-phase), continuing GATE1")
                            continue
                        print("  [run-fr-step] COVERAGE-FIX inline "
                              "fallback: whole-project coverage "
                              f"{_live_cov if _live_cov is not None else 'unmeasurable'}% "
                              f"< {_cov_min:.0f}% — human needs to add tests")
                    break
            else:
                print(f"[run-fr-step] {fr_id} GATE1 S3 block (round {fix_round}/{max_fix_rounds})"
                      f" — retrying GATE1 directly (no CODE-FIX needed for tool_evidence issue)")

            # Re-dispatch GATE1 (with block_reason if S3, otherwise clean)
            gate_prompt = _build_fr_step_prompt(
                step, fr_id, phase, project, srs_path,
                block_reason=block_reason if is_s3 else None,
            )
            result = spawner.spawn(
                role="developer", prompt=gate_prompt,
                context={"phase": phase, "fr_id": fr_id, "step": step},
                phase=phase, fr_id=fr_id, phase_sop_override="",
                task_timeout=_fr_timeout,
                max_turns=_max_turns(step),
                mcp_config=phase_ctx["mcp_config"],
                setting_sources=phase_ctx["setting_sources"],
                permission_mode=_pmode,
            )
            if (result.get("status") in _DISPATCH_ERROR_STATUSES
                    and _is_connector_disabled_failure(result.get("output", ""))):
                return _abort_dispatch_structurally_broken(fr_id, step, phase, project)
            gate_pass, failing_dims, block_reason = _parse_gate_output(result.get("output", ""))
            if not gate_pass:
                gate_pass = _fr_step_already_done(step, fr_id, project, phase=phase)

        # Bug fix (P3 2026-07-17): this used to be the for-loop's `else:`
        # clause, which Python only runs when the loop completes WITHOUT
        # hitting `break`. Three break sites inside the loop above (gate_pass
        # success, ENV-classified failure, and a fix-dispatch itself erroring
        # — e.g. CODE-FIX hitting error_max_turns) all `break` for reasons
        # OTHER than success, but `break` silently skipped this block too —
        # so an ENV error or a fix-dispatch error fell through to the
        # post-loop success path (record gate timestamp, push, print ✅) even
        # though GATE1 never actually passed. Checking `gate_pass` directly
        # after the loop, instead of relying on for/else, catches every
        # early-exit reason uniformly — it also fixes the latent case where
        # GATE1 only passes on the very last allowed round: the loop then
        # ends by exhausting `range()` (no break), which for/else also
        # treated as "never passed".
        if not gate_pass and not _fr_step_already_done(step, fr_id, project, phase=phase):
            print(f"[run-fr-step] {fr_id} GATE1 BLOCKED at round {fix_round}/{max_fix_rounds}"
                  " — human intervention required")
            if _last_failure_class == "UNKNOWN":
                # Round 13 站2b: UNKNOWN still falls through to CODE-FIX (unchanged
                # behavior) but exhausting every round without ever classifying the
                # failure is itself a signal worth surfacing — it may not be a
                # quality problem at all.
                print(
                    "  [run-fr-step] Every round's failure classified as UNKNOWN — "
                    "this may not be a quality problem. Check "
                    ".methodology/degradations.jsonl and .methodology/crash/ for a "
                    "harness-side cause before re-running CODE-FIX."
                )
            return 2  # BLOCKED

    # P0-B: record gate timestamp so advance-phase _check_gate1_live_coverage
    # finds a gate=1 entry for this FR (it reads gate_timestamps.jsonl; without
    # this, advance-phase always exits 14 when run-fr-step is used instead of
    # finalize-gate --gate 1 per FR).
    #
    # Skip when the step's own commit already landed (e.g. the dispatched
    # sub-agent called `finalize-gate` itself, which records its own
    # gate_timestamps.jsonl entry at gate_cmds.py:2190 immediately before its
    # commit). Recording again here would append a second, never-committed
    # entry — always leaving the working tree dirty — which the dirty-tree
    # guard below then misreports as a hook rejection. record_gate_timestamp
    # itself must stay a plain unconditional append (it is also relied on by
    # the anti-batch-fabrication detector in check_commit_intervals, which
    # needs to see genuinely repeated finalizations); the redundancy is
    # avoided here, at the call site that has the git-log context to know
    # whether a new record is actually needed.
    if step in ("GATE1", "GATE1-DELTA") and not _fr_step_already_done(step, fr_id, project, phase=phase):
        gate1_evidence.record_gate_timestamp(project, phase, 1, fr_id)
        # Bug fix (P3 2026-07-17): the append above modifies a TRACKED file
        # (.methodology/gate_timestamps.jsonl) and nothing committed it before
        # the dirty-tree guard below runs — so this write alone always tripped
        # its own guard, misreporting a genuine GATE1 PASS as "commit did not
        # land" and routing it into a pointless CODE-FIX retry (which finds no
        # real defect and dies on `error_max_turns`). gate_cmds.py's
        # finalize-gate does the equivalent append (line ~2043) immediately
        # before its own commit for exactly this reason — mirror that here.
        # Scoped to this one file only (not `git add -A`): the dirty-tree
        # guard must still catch a genuinely orphaned sub-agent commit.
        _gt_path = project / ".methodology" / "gate_timestamps.jsonl"
        subprocess.run(["git", "add", str(_gt_path)], cwd=str(project), capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", f"chore: record gate1 evidence ({fr_id})", "--", str(_gt_path)],
            cwd=str(project), capture_output=True, text=True,
        )

    # 5. Verify commit exists (non-fatal warning — defense-in-depth below
    # AgentSpawner._validate_inner_json which already ERRORs no-op results).
    # Uses the commit-required SSOT so this list stays in sync with the
    # validator and the dirty-tree guard below.
    if step in _COMMIT_REQUIRED_STEPS and not _fr_step_already_done(step, fr_id, project, phase=phase):
        print(f"[run-fr-step] {fr_id} {step}: WARNING — expected commit not found in git log")


    # 6. Dirty-tree guard: verify commit actually landed.
    # If git commit was blocked by prepare-commit-msg hook, implementation
    # files remain uncommitted and the next FR's step will sweep them up
    # (cascade bug — e.g. FR-02 GREEN blocked → orphan executor.py/store.py
    # staged into FR-03 RED commit). Only check steps that are expected to
    # produce a commit (skip CODE-FIX/COVERAGE-FIX which fix code for the
    # next GATE1 round to commit). Same SSOT as line 739.
    #
    # Scoped via pre/post diff (see pre-step snapshot captured above): pre-
    # existing dirt unrelated to this FR's step must NOT trip the guard —
    # only directory lines NEWLY introduced by this step count.
    if step in _COMMIT_REQUIRED_STEPS:
        _post_step_dirty = set(
            subprocess.run(
                ["git", "status", "--porcelain"],
                capture_output=True, text=True, cwd=str(project),
            ).stdout.splitlines()
        )
        new_dirty = sorted(_post_step_dirty - _pre_step_dirty)
        if new_dirty:
            dirty = "\n".join(new_dirty)
            print(
                f"\n[BLOCKED] {fr_id} {step}: commit did not land — "
                f"working tree still dirty after step.\n"
                f"  Likely cause: prepare-commit-msg hook rejection "
                f"(stale trace attestation, FSM check, etc.).\n"
                f"  Fix the hook-reported error, then re-run:\n"
                f"    python harness_cli.py resume-fr-step --phase {phase} "
                f"--fr-id {fr_id} --project {project}\n"
                f"  New dirty files (introduced during this step):\n{dirty[:2000]}",
                file=sys.stderr,
            )
            return 6  # Same exit code as finalize-gate commit-failed

    # ── Ghost paper-trail detection ──────────────────────────────────────
    # Verify that the sub-agent's CLAIMED work matches ACTUAL code changes.
    # Catches self-reports like "fixed lint error" where git diff shows
    # zero substantive changes (only whitespace/comments/config files).
    if _pre_step_sha:
        _ghost_result = detect_ghost_changes(
            project, _pre_step_sha, step, fr_id,
            agent_output=result.get("output", ""),
        )
        if _ghost_result["ghost_detected"]:
            write_ghost_paper_trail(project, {
                **_ghost_result, "phase": phase, "fr_id": fr_id, "step": step,
            })
            print(
                f"\n[GHOST DETECTED] {fr_id} {step}: "
                f"agent claimed work but made no substantive code changes.\n"
                f"  Reason: {_ghost_result['reason']}\n"
                f"  Paper trail: .sessi-work/ghost_detected/{fr_id}_{step}.json\n"
                f"  Re-run the step with genuine code changes.",
                file=sys.stderr,
            )
            return 22  # GHOST_DETECTED

    no_push = getattr(args, "no_push", False) or os.environ.get("HARNESS_NO_GIT")
    if no_push:
        print("[run-fr-step] --no-push or HARNESS_NO_GIT specified — skipping git push")
    else:
        push = _sp.run(
            ["git", "push", "origin", "HEAD"],
            capture_output=True, text=True, cwd=str(project),
        )
        if push.returncode != 0:
            print(f"[run-fr-step] git push failed: {push.stderr[:300].strip()}")
            return 1

    suffix = "" if no_push else " + pushed to GitHub"
    print(f"[run-fr-step] ✅ {fr_id} {step} complete{suffix}")
    return 0


def cmd_resume_fr_phase(args: argparse.Namespace) -> int:
    """Print the next pending run-fr-step command for crash recovery.

    Scans git log for completed step commit patterns and quality_manifest.json
    for the FR list.  Prints the exact command to run to continue.
    """
    phase = args.phase
    project = Path(args.project).resolve()
    progress_path = project / ".methodology" / "fr_progress.json"

    fr_ids: list[str] = load_quality_manifest(project, lenient=True).get("fr_ids", [])
    if not fr_ids and progress_path.exists():
        try:
            data = json.loads(progress_path.read_text(encoding="utf-8"))
            fr_ids = list(data.get("frs", {}).keys())
        except Exception as exc:
            print(f"[WARN] resume-fr-phase: fr_progress.json unreadable: {exc}", file=sys.stderr)

    if not fr_ids:
        print("[resume-fr-phase] No FR list found — check .methodology/quality_manifest.json")
        return 1

    # Carry-forward phases (5/7/8) default to GATE1-DELTA.
    # If FR code changed since last Gate 1 → switch to full TDD cycle.
    carryforward = phase in (5, 7, 8)
    for fr_id in fr_ids:
        if carryforward:
            if gate1_evidence.fr_code_changed_since_last_gate1(fr_id, project, phase=phase):
                steps = ["TDD-RED", "TDD-GREEN", "TDD-IMPROVE", "GATE1"]
            else:
                steps = ["GATE1-DELTA"]
        else:
            steps = ["TDD-RED", "TDD-GREEN", "TDD-IMPROVE", "GATE1"]
        for step in steps:
            if not _fr_step_already_done(step, fr_id, project, phase=phase):
                print(
                    f"Next step: python3 harness_cli.py run-fr-step "
                    f"--phase {phase} --fr-id {fr_id} --step {step} --project {project}"
                )
                return 0

    print("[resume-fr-phase] All FRs complete for this phase.")
    return 0


def cmd_run_tool(args: argparse.Namespace) -> int:
    """CLI dispatcher for individual tool invocations (Bug #110).

    Thin wrapper around `harness.tool_runners.run_tool` + `compute_tool_score`.
    Plan templates reference this command directly:
      `python3 harness_cli.py run-tool ast-error-handling --project .`
    """
    from harness.tool_runners import run_tool, compute_tool_score
    import json as _json

    project_root = str(Path(args.project).resolve())
    output, returncode = run_tool(
        args.tool,
        project_root,
        timeout_override=args.timeout_override,
    )
    score = compute_tool_score(args.tool, output, returncode)

    if args.json:
        print(_json.dumps({
            "tool": args.tool,
            "project": project_root,
            "returncode": returncode,
            "score": score,
            "output": output,
        }, indent=2, ensure_ascii=False))
    else:
        print(f"run-tool  tool={args.tool}  project={project_root}")
        print(f"  returncode: {returncode}")
        if score is not None:
            print(f"  score:      {score:.1f}")
        else:
            print("  score:      (unscored — tool skipped / timed out / not found / unknown)")
        if output:
            print("  --- output (first 500 chars) ---")
            print("\n".join(output.splitlines()[:25])[:500])

    # Exit codes: 0 = success (incl. zero violations), 1 = tool reported failure,
    # 2 = tool not found / skipped (preserved from run_tool() negative codes).
    if returncode < 0:
        return 2
    return 0 if returncode == 0 else 1


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




# --- helpers moved verbatim from harness_cli.py (絞殺者續章 S4c) ---

# Statuses that indicate an agent dispatch failure (all others treated as success).
# P3 2026-07-15 FR-03: include the inner-JSON semantic no-op signatures here as
# defense-in-depth — AgentSpawner._validate_inner_json already converts them to
# ERROR, but a direct caller passing through these strings (e.g. an outer
# workflow agent reflecting inner status) should also be caught.
#
# Round 19 站1: the set itself now lives in core.failure_modes, which needs the
# same answer to scope its unclassified-failure denominator. One list, two
# readers — the alias keeps this module's 5 call sites unchanged.
_DISPATCH_ERROR_STATUSES: frozenset[str] = DISPATCH_FAILURE_STATUSES

# Distinct from BLOCKED (2) / commit-dirty (6) / GHOST_DETECTED (22): means
# "do not retry — the environment itself is broken."
DISPATCH_STRUCTURALLY_BROKEN_EXIT_CODE = 23

# Fix H-H (P3 2026-07-15 round 4): the first dispatch for TDD-RED/TDD-GREEN/
# TDD-IMPROVE/MIRROR/amend-sab/ORCH-POST (_COMMIT_REQUIRED_STEPS minus
# GATE1/GATE1-DELTA, which already get the richer fix-round retry loop below)
# had ZERO retry on any dispatch ERROR — a single transient failure (or a
# Fix H-A no-op catch) permanently killed that FR's progress for the whole
# run. Production sessions_spawn.log evidence (see Fix H-G) shows these
# failures are frequently transient. This is a PLAIN re-dispatch of the
# identical step prompt (no failure classification / specialized fixer —
# unlike GATE1's CODE-FIX/LINT-FIX/COVERAGE-FIX routing, which depends on
# tool_snapshot/failing_dims signals these steps never produce) — 2 total
# attempts, i.e. 1 retry.
_STEP_RETRY_ATTEMPTS = 2


def _is_connector_disabled_failure(output: str) -> bool:
    """True on a deterministic-breakage signature — delegates to
    core.agent_spawner.is_structurally_broken (single-source registry; the
    module-level import stays real even when tests fake AgentSpawner in
    sys.modules). Wrapper keeps c1bacf4's two call sites unchanged."""
    return is_structurally_broken(output)


def _abort_dispatch_structurally_broken(fr_id: str, step: str, phase: int, project: Path) -> int:
    """FATAL diagnostic + abort code, shared by every dispatch site in the
    fix-round loop — callers must return this immediately, not retry."""
    print(
        f"\n[FATAL] {fr_id} {step}: sub-agent dispatch is structurally broken — "
        "Claude Code reports claude.ai connectors are disabled (an "
        "ANTHROPIC_API_KEY/ANTHROPIC_AUTH_TOKEN-style env var is overriding "
        "claude.ai login). Every retry will fail identically; not attempting "
        "further rounds.\n"
        "  Fix: unset the auth-override env var in the shell that launches "
        "this process, then re-run:\n"
        f"    python harness_cli.py resume-fr-step --phase {phase} "
        f"--fr-id {fr_id} --project {project}",
        file=sys.stderr,
    )
    return DISPATCH_STRUCTURALLY_BROKEN_EXIT_CODE


def _classify_infra_or_harness_bug(out: str) -> "tuple[str, str] | None":
    """Round 13 站2a: detect a [HARNESS-BUG] banner (core/errors.py's crash
    boundary) or an R12 INFRA_FAIL precondition-block signature
    (harness.harness_bridge._INFRA_FAIL_EVIDENCE_SIGNATURES — phantom/
    unregistered-module run-gate blocks) in a sub-agent's raw dispatch
    output. Returns (class, evidence) where class is "HARNESS_BUG" or
    "INFRA", or None if neither is present.

    Distinct from `_extract_block_reason`'s S3/S4 tool_evidence_missing
    scan (cheap to fix — just retry GATE1): these two are NOT code-fixable
    at all — the fix round must abort instead of dispatching CODE-FIX/
    COVERAGE-FIX at a problem no code change can resolve.
    """
    if not out:
        return None
    if "[HARNESS-BUG]" in out:
        for line in out.splitlines():
            if "[HARNESS-BUG]" in line:
                return "HARNESS_BUG", line.strip()
        return "HARNESS_BUG", "[HARNESS-BUG] banner present"
    from harness.harness_bridge import _INFRA_FAIL_EVIDENCE_SIGNATURES
    for sig in _INFRA_FAIL_EVIDENCE_SIGNATURES:
        if sig in out:
            return "INFRA", sig
    return None


def _abort_repeated_failure(
    fr_id: str, step: str, phase: int, project: Path, record: dict
) -> int:
    """Refuse to buy an identical failure again, and say what it was.

    Round 41 站3. The refusal is not a verdict about the code — it is a
    statement that this dispatch has already been made, against this tree, with
    this outcome, as many times as the framework's own retry policy allows.
    Repair anything in the tree and the refusal lifts on its own; there is no
    flag to override it, because a flag would be a way to keep paying.
    """
    print(
        f"\n[BLOCKED] {fr_id} {step}: this dispatch has already failed "
        f"{record.get('seen')}x with an identical result on this exact tree "
        f"({record.get('error_class') or 'failure'}, signature "
        f"{record.get('signature')}). Refusing to spend another one.\n"
        f"  Nothing has changed since those attempts — same commit, same "
        f"working tree — so the next attempt meets the same conditions.\n"
        f"  The failures are recorded in .methodology/degradations.jsonl "
        f"(component run-fr-step:{step}).\n"
        f"  Fix the cause; any change to the tree re-opens this step. Then:\n"
        f"    python harness_cli.py resume-fr-step --phase {phase} "
        f"--fr-id {fr_id} --project {project}",
        file=sys.stderr,
    )
    record_degradation(
        project, component=f"run-fr-step:{step}",
        what="dispatch refused — identical failure already recorded",
        why=f"{fr_id} {step}: signature {record.get('signature')} seen "
            f"{record.get('seen')}x on an unchanged tree", owner="unknown"
    )
    return EX_STEP_REPEATED_FAILURE


def _reports_precondition_block(result: dict) -> bool:
    """Did this dispatch report that the step's precondition is unmet?

    Round 41 站2. Read from `inner_status` where spawn() put it, and re-derived
    from the output text otherwise — the same two-directional read
    `core.failure_modes._effective_error_class` uses, so a result that came
    back through a path which did not stamp the field is still recognised.
    """
    if (result.get("inner_status") or "").upper() == PRECONDITION_BLOCKED:
        return True
    return blocked_inner_status_in(str(result.get("output") or "")) == PRECONDITION_BLOCKED


def _resolve_precondition_block(
    fr_id: str, step: str, phase: int, project: Path, output: str
) -> int | None:
    """Honour a reported precondition block, but only after checking it.

    Returns the abort code when the block is real (or unverifiable), or None to
    let the caller fall through to the ordinary error path when the claim is
    contradicted by the framework's own measurement.

    Round 35's rule, applied to a new claim: the framework's number comes before
    the agent's. Without the check, "PRECONDITION_BLOCKED" would be a universal
    opt-out from the commit requirement — any step could decline any work by
    naming a precondition nobody verifies.

    UNKNOWN honours the block and says so. The framework cannot measure the
    project (non-Python, no source or test directory, no test of this FR
    collected), so it has no ground to call the agent wrong; refusing on that
    basis would put the step straight back into the loop this round exists to
    end, and Round 39's rule says an abstention has to be visible rather than
    silently resolved either way.
    """
    verdict = suite_run.fr_suite_verdict(project, fr_id)
    if verdict == suite_run.GREEN:
        print(
            f"[run-fr-step] {fr_id} {step}: reported a blocked precondition, but "
            f"this FR's tests pass — the claim is not supported by the tree it "
            f"describes. Treating as an ordinary step failure.",
            file=sys.stderr,
        )
        return None
    if verdict == suite_run.UNKNOWN:
        record_degradation(
            project, component=f"run-fr-step:{step}",
            what="precondition block accepted without verification",
            why=f"{fr_id}: the suite could not be measured here, so the reported "
                f"block is honoured on the sub-agent's word alone", owner="harness"
        )
    print(
        f"\n[BLOCKED] {fr_id} {step}: the step's precondition is not met, so it "
        f"correctly did nothing.\n"
        f"  Verified: this FR's own tests are {'failing' if verdict == suite_run.RED else 'not measurable here'}.\n"
        f"  This is NOT an agent-logic failure and re-dispatching it changes "
        f"nothing — the next attempt meets the same baseline.\n"
        f"  Sub-agent report: {output[:300]}\n"
        f"  Fix the baseline, or revert the step that broke it, then re-run:\n"
        f"    python harness_cli.py resume-fr-step --phase {phase} "
        f"--fr-id {fr_id} --project {project}",
        file=sys.stderr,
    )
    return EX_STEP_PRECONDITION_BLOCKED


def _abort_dispatch_infra_or_harness_bug(
    fr_id: str, step: str, phase: int, project: Path, cls: str, evidence: str
) -> int:
    """FATAL diagnostic + abort code for a HARNESS_BUG/INFRA hit — mirrors
    _abort_dispatch_structurally_broken's shape. Not dispatching CODE-FIX/
    COVERAGE-FIX: this is not a code-quality problem, and retrying GATE1
    (the S3 short-circuit's response to a different, code-fixable class of
    parse failure) would not resolve it either."""
    kind = ("a bug in harness-methodology itself" if cls == "HARNESS_BUG"
            else "an infrastructure precondition failure (the tool never ran)")
    print(
        f"\n[FATAL] {fr_id} {step}: {cls} detected in sub-agent output — {kind}, "
        f"not a code-quality problem. Not dispatching a fix agent.\n"
        f"  Evidence: {evidence}\n"
        "  Escalate to a human operator; re-run after the underlying issue "
        "is fixed:\n"
        f"    python harness_cli.py resume-fr-step --phase {phase} "
        f"--fr-id {fr_id} --project {project}",
        file=sys.stderr,
    )
    return EX_FR_STEP_INFRA_ABORT


def _abort_no_progress_with_self_doubt(
    fr_id: str, step: str, project: Path, failure_class: "str | None", sig: str
) -> int:
    """Round 17 站2 (finding B): the fix-round loop hit 2 consecutive
    no-progress rounds — the SAME tool error signature twice, no forward
    motion. Before this, the loop just printed BLOCKED and returned 2: the
    event was invisible to run-report, and the operator was left to assume a
    code defect.

    The plan's original signal — contrast S4's independent coverage against
    the gate verdict — turned out not to exist: _run_harness_cross_validation
    returns only violation messages (not a coverage number), and
    _capture_tool_snapshot runs pytest WITHOUT --cov. With no reliable
    'gate-internal contradiction' number available, this does the two honest
    things that ARE possible at this already-terminal point:

      1. record the exhausted loop to the degradation ledger (R13) so
         run-report (R14/R16) can see how often an FR hits an inescapable
         fix-round — today it is a silent return 2.
      2. a self-doubt channel (R12 站3a shape, red_assertion_check:911): a
         deterministic same-error loop that survives two fix rounds MAY be a
         harness gate-calculation bug (the #20 spec-cap class), not a code
         defect — surface that hypothesis so it is reported as [HARNESS-BUG]
         rather than code-fixed forever.
    """
    record_degradation(
        project, "fr-step-no-progress",
        f"{fr_id} {step}: 2 consecutive no-progress fix rounds "
        f"(failure_class={failure_class})",
        why=f"identical tool signature across rounds: {sig[:150]}", owner="project"
    )
    # Round 2026-08-23 (FR-99 finalization recovery): if the ephemeral
    # evaluator verdict says quality_complete=true at score>=score_gate
    # while the durable manifest stamp says quality_complete=false, the
    # loop aborted not because the code is wrong but because finalize-gate
    # never re-attempted the commit. Emit a [HARNESS-BUG] banner that
    # run-all.js's in-loop regex (line 2342 et seq.) routes to
    # harness-repair instead of the regular fix-loop halt, and prints
    # the exact recovery command. Discovered via the FR-99 block pattern:
    # the verifier is correct to read only the manifest (see
    # verify_gate1_qc.py docstring lines 34-39), but the dispatch loop
    # has no early-exit when the manifest was rolled back from a
    # never-recovered commit failure — this is that exit.
    diag = _detect_evaluator_passed_but_commit_uncommitted(project, fr_id)
    print(
        f"[run-fr-step] {fr_id} BLOCKED: 2 consecutive no-progress rounds"
        f" — human intervention required\n"
        f"  Error pattern: {sig[:150]}\n"
        f"  SELF-CHECK before treating this as a code defect: a deterministic"
        f" same-error loop that survives 2 fix rounds may be a harness"
        f" gate-calculation bug (e.g. a spec-cap / scope miscount like the #20"
        f" class), NOT your code. If the tests and coverage are actually"
        f" correct, report this as [HARNESS-BUG] rather than dispatching"
        f" another fix round."
    )
    if diag is not None:
        print(
            f"\n[HARNESS-BUG] This is a bug in harness-methodology itself\n"
            f"  {fr_id}: evaluator verdict was PASS "
            f"(score={diag['score']:.1f}, gate1_result.json quality_complete=true),\n"
            f"  but the durable git commit did not land "
            f"(manifest quality_complete=false despite score>=score_gate).\n"
            f"  The dispatch loop cannot detect this — it only sees the manifest.\n"
            f"  Recovery: finalize the commit manually with --force:\n"
            f"    python harness_cli.py finalize-gate --gate 1 --phase {step or 3} "
            f"--fr-id {fr_id} --project {project}"
        )
    return 2


def _detect_evaluator_passed_but_commit_uncommitted(
    project: Path, fr_id: str, *, score_gate: float = 100.0
) -> dict | None:
    """Detect when an FR's evaluator verdict passed but the durable commit
    failed — i.e. .sessi-work/gate1_result.json (LLM-written, ephemeral)
    says quality_complete=true at score>=score_gate while the manifest
    stamp (durable) at .methodology/quality_manifest.json says
    quality_complete=false despite score>=score_gate.

    Round 2026-08-23 — recovery path for FR-99 and any future FR whose
    finalize-gate was interrupted after the optimistic stamp and never
    re-attempted. Returns a small diagnostic dict so the caller can print a
    recovery hint, or None if the condition does not hold.

    IMPORTANT: this reads the LLM-written .sessi-work/gate1_result.json
    (which is gitignored and ephemeral), but ONLY as one half of an
    AND-conjunction against the durable manifest. The verifier at
    harness/scripts/verify_gate1_qc.py correctly trusts only the manifest
    for its PASS/FAIL verdict (per its docstring lines 34-39); here we use
    the ephemeral artifact only to detect "evaluator said PASS but commit
    never landed", not as a pass condition in itself.
    """
    try:
        rj_path = project / ".sessi-work" / "gate1_result.json"
        if not rj_path.exists():
            return None
        rj = json.loads(rj_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(rj, dict) or rj.get("fr_id") != fr_id:
        return None
    if rj.get("quality_complete") is not True:
        return None
    rj_score = rj.get("overall_score")
    if not isinstance(rj_score, (int, float)) or rj_score < score_gate:
        return None

    mfst = load_quality_manifest(project, lenient=True)
    if not isinstance(mfst, dict):
        return None
    g1 = ((mfst.get("gate_results") or {}).get("gate1") or {})
    fr_entry = g1.get(fr_id)
    if not isinstance(fr_entry, dict):
        return None
    if fr_entry.get("quality_complete") is not False:
        return None
    mfst_score = fr_entry.get("score")
    if not isinstance(mfst_score, (int, float)) or mfst_score < score_gate:
        return None

    return {
        "score": float(rj_score),
        "manifest_score": float(mfst_score),
        "rounds_used": rj.get("rounds_used"),
    }


# Per-step default max_turns for run-fr-step. --max-turns override takes priority.
# GATE1 needs more turns: 5-step workflow (run-gate → evaluate → write result.json
# → finalize-gate → report) plus multi-dimension assessment on brownfield codebases.
_STEP_MAX_TURNS: dict[str, int] = {
    "TDD-RED":      40,
    "TDD-GREEN":    40,
    "TDD-IMPROVE":  40,
    "GATE1":        70,
    "GATE1-DELTA":  70,
    "CODE-FIX":     50,
    "TEST-FIX":     40,
    "INFRA-FIX":    40,
    "LINT-FIX":     70,   # 20+ constant renames with reference updates need many turns
    "COVERAGE-FIX": 90,   # bulk spec-test writing (100+ tests) needs headroom
}

def _fr_step_preflight(step: str, project: Path, fr_id: str | None, srs_path: "Path | str | None" = None) -> tuple[bool, list[str]]:
    """Verify environment and artifacts are ready before spawning a sub-agent for an FR step.

    Returns (ok, error_lines). On ok=[], sub-agent spawn proceeds. On failure,
    caller prints error_lines to stderr and returns 1 before any agent dispatch.

    Step-aware: GATE1/CODE-FIX need full tool + DB checks; TDD-RED only needs pytest.
    """
    errors: list[str] = []
    step = step.upper()

    # ── 1. Git repo check ────────────────────────────────────────────────────
    if not project.exists() or not (project / ".git").exists():
        errors.append(f"✗ {project} is not a git repo or does not exist")

    # ── 2. SRS.md (required for all steps — traceability back to requirements) ─
    srs = project / "SRS.md"
    if srs_path:
        srs_arg = Path(srs_path)
        srs = srs_arg if srs_arg.is_absolute() else project / srs_arg
    else:
        srs = ProjectLayout(project).srs_path

    if not srs.exists():
        try:
            rel_path = srs.relative_to(project)
        except ValueError:
            rel_path = srs
        errors.append(f"✗ SRS.md not found at {rel_path} (required for all FR steps)")

    # ── 3. quality_manifest.json + FR-ID registration ────────────────────────
    manifest_path = project / ".methodology" / "quality_manifest.json"
    if not manifest_path.exists():
        errors.append("✗ .methodology/quality_manifest.json not found (run run-phase first)")
    else:
        try:
            m = load_quality_manifest(project)
            registered = m.get("fr_ids", [])
            if fr_id and fr_id not in registered:
                errors.append(
                    f"✗ FR-ID {fr_id} not in quality_manifest.json fr_ids ({', '.join(registered)})"
                )
        except StateCorruptError as exc:
            print(f"[WARN] run-fr-step precheck: quality_manifest.json malformed: {exc}", file=sys.stderr)
            errors.append("✗ quality_manifest.json is malformed JSON")

    # ── 4. TEST_SPEC.md (required for TDD-RED — test names come from here) ───
    # Must match _extract_test_spec_names: canonical location is 02-architecture/
    test_spec = ProjectLayout(project).test_spec_path
    if step == "TDD-RED":
        if not test_spec.exists():
            errors.append(
                "✗ 02-architecture/TEST_SPEC.md not found (TDD-RED requires test catalog)"
            )
        else:
            # Basic validity: must contain FR-ID sections
            try:
                content = test_spec.read_text(encoding="utf-8")
                if fr_id and not re.search(rf'#+\s+{re.escape(fr_id)}\b', content):
                    errors.append(
                        f"✗ 02-architecture/TEST_SPEC.md has no section for {fr_id}"
                        " (run derive_test_cases.md skill first)"
                    )
            except Exception as exc:
                print(f"[WARN] run-fr-step precheck: TEST_SPEC.md unreadable: {exc}", file=sys.stderr)
                errors.append("✗ 02-architecture/TEST_SPEC.md exists but is unreadable")

    # ── 5. Tool checks (step-aware) ───────────────────────────────────────────
    def _missing_tool(name: str) -> str:
        return f"✗ {name} not found in PATH — install with: pip install {name}"

    if step in ("GATE1", "GATE1-DELTA", "CODE-FIX"):
        _, gate_errors = tool_checks.verify_gate_tools(1, str(project))
        errors.extend(gate_errors)
        # Delegate env readiness to LLM-driven run-env-check — no hardcoded
        # DATABASE_URL/pytest/ruff here. Claude evaluates project-specific needs
        # from SAD.md + SRS.md at run-env-check time.
        env_result = project / ".sessi-work" / "env_check_result.json"
        if not env_result.exists():
            errors.append(
                "✗ env_check_result.json not found. "
                f"Run: python harness_cli.py run-env-check --phase <phase> --project {project} "
                "then evaluate inline and run finalize-env-check."
            )

    if step in ("TDD-RED", "TDD-GREEN", "TDD-IMPROVE"):
        missing_tools = check_cli_tools(["pytest", "ruff"])
        for tool in missing_tools:
            errors.append(_missing_tool(tool))

    return len(errors) == 0, errors

def _extract_review_json(text: str, _depth: int = 0) -> "dict | None":
    """Extract the first JSON object containing 'review_status' from free text.

    Scans from every '{' position so it works whether the agent output is plain
    JSON, JSON inside a markdown code fence, or JSON embedded in prose.

    Also unwraps the Claude CLI JSON envelope (``{"result": "...", "session_id": "..."}``)
    when the agent output was captured as the raw CLI response rather than the
    unwrapped ``result`` field.  Recursion is bounded at 2 levels (Claude CLI
    envelope is always exactly 1 level deep).
    Returns None if no valid review JSON is found.
    """
    if not text or not isinstance(text, str):
        return None

    decoder = json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch != '{':
            continue
        try:
            obj, _ = decoder.raw_decode(text, i)
            if not isinstance(obj, dict):
                continue
            if "review_status" in obj:
                return obj
            # Unwrap Claude CLI envelope: {"result": "...", "session_id": "..."}
            if "result" in obj and isinstance(obj["result"], str) and _depth < 2:
                inner = _extract_review_json(obj["result"], _depth + 1)
                if inner is not None:
                    return inner
        except (json.JSONDecodeError, ValueError):
            pass
    return None

def _extract_agent_output_json(text: str) -> "dict | None":
    """Extract Agent A's structured output JSON from free text.

    Looks for a dict that has 'status' plus at least one of the Agent A
    output fields (files, confidence, citations, summary).  This is distinct
    from Agent B's review JSON which carries 'review_status'.
    Returns None if no matching JSON block is found.
    """
    _AGENT_A_FIELDS = frozenset({"files", "confidence", "citations", "summary"})
    decoder = json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch != '{':
            continue
        try:
            obj, _ = decoder.raw_decode(text, i)
            if (
                isinstance(obj, dict)
                and "status" in obj
                and "review_status" not in obj  # not an Agent B block
                and _AGENT_A_FIELDS & obj.keys()
            ):
                return obj
        except json.JSONDecodeError:
            pass
    return None

def _parse_gate_output(out: str) -> tuple[bool, list, str]:
    """Extract gate_pass, failing_dims, and block_reason from sub-agent output.

    Tries full-string JSON parse first, then scans for embedded JSON objects
    by tracking brace depth — handles nested structures in failing_dims.
    Also scans for finalize-gate [BLOCKED] lines to surface S3/S4 details.

    Returns (gate_pass, failing_dims, block_reason).
    block_reason is a non-empty string when finalize-gate blocked with S3/S4;
    empty string otherwise.  Falls back to (False, [], "") on parse failure.
    """
    def _try(s: str) -> dict | None:
        try:
            obj = json.loads(s)
            if isinstance(obj, dict) and "pass" in obj:
                return obj
        except (json.JSONDecodeError, ValueError):
            pass
        return None

    def _extract_dims(obj: dict) -> list:
        # Accept both the prompt-specified key ("failing_dims") and the score.py
        # schema key ("failing_dimensions") — agents sometimes copy the wrong one.
        return obj.get("failing_dims") or obj.get("failing_dimensions") or []

    def _extract_block_reason(text: str) -> str:
        """Scan agent output for finalize-gate [BLOCKED] lines (S3/S4 errors)."""
        for line in text.splitlines():
            if "[BLOCKED]" in line and (
                "tool_evidence_missing" in line or "tool_score_fabrication" in line
            ):
                return line.strip()
        return ""

    block_reason = _extract_block_reason(out)

    # Try whole string first (agent returned bare JSON)
    obj = _try(out.strip())
    if obj:
        return bool(obj.get("pass", False)), _extract_dims(obj), block_reason

    # Scan for any embedded JSON object via brace-depth tracking
    i = 0
    while i < len(out):
        if out[i] == "{":
            depth = 0
            for j in range(i, len(out)):
                if out[j] == "{":
                    depth += 1
                elif out[j] == "}":
                    depth -= 1
                    if depth == 0:
                        obj = _try(out[i : j + 1])
                        if obj is not None:
                            return bool(obj.get("pass", False)), _extract_dims(obj), block_reason
                        break
        i += 1

    return False, [], block_reason


def _resolve_phase3_context(project: Path) -> dict:
    """Resolve MCP config and settings isolation for Phase 3+ sub-agents.

    Auto-detects whether code-review-graph MCP tools are available.
    setting_sources is ALWAYS "" (Round 12 站0d): a live probe matrix
    (2026-07-16, per-value claude -p runs asking whether a distinctive
    user-CLAUDE.md token is in context) measured:

        ""        → no CLAUDE.md memory loads
        "user"    → user CLAUDE.md loads
        "project" → project CLAUDE.md AND user global CLAUDE.md BOTH load
        "local"   → no user CLAUDE.md

    The previous behaviour (return "project" when the project has a
    CLAUDE.md) therefore shipped the user's interactive-collaboration
    protocol ("wait for confirmation before acting", "ask one question
    when unsure") into every headless sub-agent — production agents on
    the 2026-07-16 P3 run stalled awaiting an approval that cannot come
    (600s timeouts, "pytest/ruff and commit require approval" replies).
    Step prompts are self-contained (need-to-know packing in
    _build_prompt), so dropping project CLAUDE.md costs nothing; the
    isolation is the point.

    Returns:
        dict with keys:
            mcp_config: str | None  -- relative path to .mcp.json, or None
            setting_sources: str    -- always "" (isolation; see above)
    """
    import shutil as _shutil
    result: dict[str, str | None] = {"mcp_config": None, "setting_sources": ""}

    # MCP: only enable if uvx is on PATH (required by our .mcp.json)
    if _shutil.which("uvx"):
        for candidate in ["harness/.mcp.json", ".mcp.json"]:
            if (project / candidate).exists():
                result["mcp_config"] = candidate
                break

    return result

def _capture_tool_snapshot(
    project: Path, src_dir: str, test_file: str
) -> str:
    """Run ruff + pytest at orchestration time and return combined output (max 2000 chars).

    Used to give CODE-FIX agents concrete, targeted error messages rather than
    forcing them to re-discover failures from scratch.  Failures are non-fatal —
    returns "" on any subprocess error so the CODE-FIX prompt degrades gracefully.
    """
    import subprocess as _sp
    lines: list[str] = []
    # PYTHONPATH must include the src root for src-layout projects.
    # Using PYTHONPATH=project alone causes ModuleNotFoundError for packages
    # under 03-development/src/, masking the real assertion failures from fixers.
    # We include BOTH project root (original behaviour) and src_dir (new) so
    # that nothing that previously worked can regress.
    import os as _os
    _pythonpath = (
        _os.pathsep.join([str(project / src_dir), str(project)])
        if src_dir else str(project)
    )
    # Try ruff from PATH first; fall back to python3 -m ruff when ruff is
    # installed only inside a specific Python environment (e.g. Python 3.9 venv
    # while the system python3 is 3.14).  exit code 127 = command not found.
    _ruff_r = None
    for _ruff_cmd in (
        ["ruff", "check", f"{src_dir}/", "--extend-ignore", "RUF001,RUF002,RUF003"],
        ["python3", "-m", "ruff", "check", f"{src_dir}/", "--extend-ignore", "RUF001,RUF002,RUF003"],
    ):
        try:
            _ruff_r = _sp.run(
                _ruff_cmd, capture_output=True, text=True,
                cwd=str(project), timeout=30,
            )
            if _ruff_r.returncode != 127:
                break
        except Exception as exc:
            print(f"[WARN] _capture_tool_snapshot: ruff invocation "
                  f"'{' '.join(_ruff_cmd)}' failed: {exc}", file=sys.stderr)
            _ruff_r = None
    if _ruff_r and (_ruff_r.stdout.strip() or _ruff_r.stderr.strip()):
        lines.append(f"ruff check {src_dir}/ --extend-ignore RUF001,RUF002,RUF003 (exit {_ruff_r.returncode}):")
        lines.append((_ruff_r.stdout + _ruff_r.stderr).strip()[:600])
        lines.append("")
    try:
        r = _sp.run(
            ["python3", "-m", "pytest", test_file, "-v", "--tb=short", "-q"],
            capture_output=True, text=True, cwd=str(project),
            timeout=60, env={**__import__("os").environ, "PYTHONPATH": _pythonpath},
        )
        output = (r.stdout + r.stderr).strip()
        if output:
            lines.append(f"pytest {test_file} -v --tb=short (exit {r.returncode}):")
            # Tail: most useful failures are at the end
            lines.append(output[-800:])
    except Exception as exc:
        print(f"[WARN] _capture_tool_snapshot: pytest capture failed — "
              f"CODE-FIX prompt will get a shorter/empty snapshot: {exc}", file=sys.stderr)
    return "\n".join(lines)[:2000]


def _classify_snapshot_failure(snapshot: str, failing_dims: list | None = None) -> str:
    """Classify the root cause of a Gate 1 failure from tool snapshot output.

    Returns one of:
      "ENV"             — ModuleNotFoundError / ImportError (environment not set up)
      "ISOLATION"       — tests fail due to auth/HMAC short-circuit, not missing feature
      "ISOLATION_LIKELY" — v2.13.0: subprocess / ModuleNotFoundError / stdlib-shadow
                           AttributeError visible in stderr even though pytest summary
                           reports tests passed (test_shape bug masquerading as coverage)
      "PATCH_OBJECT"    — AttributeError: obj has no attribute 'method' (stub missing)
      "LOW_COVERAGE"    — all tests pass but test_coverage dim failing (coverage < threshold)
      "MISSING_FEATURE" — AssertionError / genuine logic failure (CODE-FIX can help)
      "UNKNOWN"         — cannot classify (fall through to CODE-FIX)
    """
    if not snapshot:
        return "UNKNOWN"
    s = snapshot.lower()
    if "no module named" in s or "modulenotfounderror" in s or "importerror" in s:
        return "ENV"
    if "attributeerror" in s and "has no attribute" in s:
        # Heuristic: `AttributeError: 'str' object has no attribute 'loads'`
        # or `AttributeError: module 'X' has no attribute 'Y'` indicates the test
        # shadows a stdlib module name with a string (v2.13.0 — FR-05 P3 lesson).
        # These are TEST_WRITING bugs, not stub-missing bugs. Route to TEST-FIX
        # via the ISOLATION_LIKELY class so the dispatch surfaces the issue even
        # when pytest summary still says `21 passed`.
        if ("'str' object has no attribute" in s
                or "module 'json'" in s or "module 'os'" in s
                or "module 'time'" in s or "module 'subprocess'" in s
                or "module 'pathlib'" in s or "module 'asyncio'" in s
                or "module 'logging'" in s or "module 'typing'" in s):
            return "ISOLATION_LIKELY"
        return "PATCH_OBJECT"
    # Isolation: infrastructure intercepts before feature logic — all tests return 401/auth
    if ("status_code=401" in s or "source='auth'" in s
            or 'source="auth"' in s or "401 unauthorized" in s):
        return "ISOLATION"
    # v2.13.0 ISOLATION_LIKELY: subprocess failure pattern (N-series tests with
    # Generic foreign-project-name detector (v2.13.0 — FR-05 P3 lesson):
    # a subprocess-launched child can't import its own package because
    # pytest's `pythonpath = ...` setting does NOT propagate to child envs.
    # Pytest summary may show `4 failed` or `21 passed` depending on whether
    # the children were collected at all; the subprocess returncode!=0 / exit 1
    # with `ModuleNotFoundError: No module named '<project_name>'` is the signal.
    # We match a generic "No module named '<single-word-or-dotted>'" pattern
    # + the keyword "subprocess" co-occurring in stderr.
    if ("modulenotfounderror" in s and "subprocess" in s
            and "no module named" in s):
        return "ISOLATION_LIKELY"
    # Compute shared flags early — referenced by INFRA_SKIP, LINT, and LOW_COVERAGE checks.
    _test_cov_failing = (
        failing_dims is not None
        and any("test_coverage" in str(d).lower() for d in failing_dims)
    )
    _has_test_failures = "failed" in s or "assertionerror" in s
    # INFRA_SKIP: tests skipped (not failed) because Docker/Redis/external service unavailable.
    # Coverage is low because skipped tests contribute 0 executed lines. Distinct from
    # ISOLATION: no 401/auth signal — pytest just reports "N skipped".
    if _test_cov_failing and "skipped" in s and not _has_test_failures:
        return "INFRA_SKIP"
    # LINT_FAIL / LINT_AND_COVERAGE: ruff linting dimension is failing.
    # Always fix linting first — mixing linting + coverage in one CODE-FIX round causes timeout.
    _lint_failing = (
        failing_dims is not None
        and any("linting" in str(d).lower() for d in failing_dims)
    )
    if _lint_failing:
        # LINT_AND_COVERAGE: both failing — fix linting this round, coverage next round.
        return "LINT_AND_COVERAGE" if _test_cov_failing else "LINT_FAIL"
    # LOW_COVERAGE: test_coverage dim failing but all tests pass — coverage % below threshold.
    # Snapshot is collected without --cov, so coverage % is not visible; detect via
    # failing_dims (test_coverage listed) + no test failures in snapshot + tests did pass.
    if _test_cov_failing and not _has_test_failures and "passed" in s:
        return "LOW_COVERAGE"
    if "assertionerror" in s or "failed" in s or "error" in s:
        return "MISSING_FEATURE"
    return "UNKNOWN"




# --- FR-step idempotency + prompt chain (moved verbatim from harness_cli.py, S4f) ---

# Commit patterns for idempotency check — must match git_strategy.py commit messages.
_FR_STEP_COMMIT_PATTERNS: dict[str, str] = {
    "TDD-RED":     "test(RED): failing test for {fr_id}",
    "TDD-GREEN":   "feat({fr_id}): GREEN",
    "TDD-IMPROVE": "refactor({fr_id}): IMPROVE",
    "AMEND-SAB":   "amend: register SAB modules ({fr_id})",         # idempotency pattern for amend-sab dispatch
    "GATE1":       "feat({fr_id}): Gate1 PASS",         # prefix match; phase-scoped
    "GATE1-DELTA": "feat({fr_id}): Gate1 PASS",         # same prefix + git diff check
}


def _fr_tests_say(project: Path, fr_id: str, *, expected: str) -> bool:
    """Does this FR's own test family report *expected* (suite_run.RED/GREEN)?

    Round 41 站1. The step-completion check used to answer "has this step been
    done" from the commit log alone; this is the half that asks the step's own
    definition. `fr_suite_verdict` runs the project's suite through
    `run_suite`'s per-process memo, so the several calls one `run-fr-step`
    makes cost one pytest invocation, not several.

    UNKNOWN maps to True — "keep the answer the commit evidence already gave".
    A project the framework cannot measure (non-Python, no source directory, no
    test of this FR collected) must not have its steps declared incomplete by a
    measurement that was never taken; Round 32 站4 settled that could-not-
    measure is not a failing measurement, and settling it the other way here
    would block every js/ts project's TDD chain outright.
    """
    verdict = suite_run.fr_suite_verdict(project, fr_id)
    if verdict == suite_run.UNKNOWN:
        return True
    return verdict == expected


def _fr_step_lineage_boundary(project: Path, phase: int | None) -> str | None:
    """Resolve the commit SHA marking the start of this phase's lineage.

    Read from the tracked `.methodology/state.json` `phase_completed` map —
    it survives `git reset --hard` (unlike sentinels under the gitignored
    .sessi-work/), so idempotency greps can be scoped to
    `<this-boundary>..HEAD` and stop matching commits from a lineage that
    was reset away but is still reachable as an ancestor of the current
    boundary commit (2026-07-11 repro: the chosen P3-pre boundary was
    itself a descendant of an earlier complete P3 run, so its own ancestry
    already contained a stale `refactor(FR-02): IMPROVE` commit).

    Returns None when unresolvable (no phase, no state.json, no recorded
    entry for phase-1) — callers must fall back to the unscoped grep so
    projects without reset history see no behavior change.
    """
    if phase is None or phase < 2:
        return None
    state = load_state(project, lenient=True)
    entry = state.get("phase_completed", {}).get(str(phase - 1))
    if not isinstance(entry, dict):
        return None
    sha = entry.get("sha")
    return sha if isinstance(sha, str) and sha else None


def _fr_step_already_done(step: str, fr_id: str, project: Path, phase: int | None = None) -> bool:
    """Idempotency check: is this step already done for THIS phase?

    For GATE1 / GATE1-DELTA (when phase is given): the authoritative signal
    is the phase-scoped finalize-gate sentinel (gate1_evidence._finalize_sentinel_path),
    not a commit-text grep. The sentinel is only ever written right after a
    genuine bridge.finalize_gate() PASS for this exact phase (gate_cmds.py
    cmd_finalize_gate) — it can't be produced by the COVERAGE-FIX manifest
    fallback or by a commit from a different phase/lineage. A plain
    `git log --grep "feat({fr_id}): Gate1 PASS"` has no phase boundary at
    all and can match a stale commit reachable from HEAD (e.g. after a
    `git reset --hard` back to a phase boundary followed by a re-run),
    causing this FR's real GATE1 deliverable to be silently skipped.

    For GATE1-DELTA: additionally checks whether FR code has changed since
    the last Gate 1 PASS commit. If code changed, returns False so the
    step re-runs with a full evaluation (not a delta-skip).

    Returns True if the step can be safely skipped (crash recovery / no-change).
    """
    # Bug Fix Idempotency-Cascade (2026-07-21): if GATE1 sentinel + manifest
    # quality_complete=true exist for THIS phase, the TDD-RED/GREEN/IMPROVE
    # prerequisites are transitively done. Without this shortcut, FRs whose
    # GREEN/IMPROVE commits pre-date the phase boundary commit (e.g. FR-02's
    # GREEN commits 6a0b272/71cb187/e6e2fee are ancestors of e91cc23) are
    # mis-classified as "not done" because
    # `git log --grep <pattern> <boundary>..HEAD` is empty AND the docstring
    # scan (multi-tag) may also fail — compounding to false-negative
    # re-dispatch on every resume-fr-phase.
    #
    # Reuses `load_quality_manifest` (imported at line 35) — same lenient=True,
    # same JSON key path (`gate_results.gate1.<fr_id>.quality_complete`) as
    # the GATE1 idempotency check at lines 1695-1707 below, so the cascade
    # reads the SAME source of truth.
    #
    # Deliberately excludes GATE1 / GATE1-DELTA from the cascade — those
    # branches have their own sentinel + quality_complete logic below.
    if step.upper() in ("TDD-RED", "TDD-GREEN", "TDD-IMPROVE") and phase is not None:
        _cascade_sentinel = gate1_evidence._finalize_sentinel_path(
            project, 1, fr_id, phase=phase,
        )
        if _cascade_sentinel.exists():
            _cascade_manifest = load_quality_manifest(project, lenient=True)
            _cascade_qc = (
                _cascade_manifest.get("gate_results", {})
                .get("gate1", {}).get(fr_id, {}).get("quality_complete")
            )
            if _cascade_qc is True:
                return True

    import subprocess as _sp
    tmpl = _FR_STEP_COMMIT_PATTERNS.get(step.upper(), "")
    if not tmpl:
        return False
    pattern = tmpl.format(fr_id=fr_id)

    if step.upper() in ("GATE1", "GATE1-DELTA") and phase is not None:
        sentinel = gate1_evidence._finalize_sentinel_path(project, 1, fr_id, phase=phase)
        committed = sentinel.exists()
    else:
        # TDD steps: grep scoped to this phase's lineage when a boundary is
        # resolvable (see _fr_step_lineage_boundary) — falls back to the
        # unscoped grep (unchanged behavior) when it is not.
        cmd = ["git", "log", "--oneline", "--grep", pattern]
        boundary = _fr_step_lineage_boundary(project, phase)
        if boundary:
            cmd.append(f"{boundary}..HEAD")
        r = _sp.run(cmd, capture_output=True, text=True, cwd=str(project))
        committed = bool(r.stdout.strip())
    # Review fix (2026-07-21): an earlier version of this change relaxed
    # this to only early-return for GATE1/GATE1-DELTA, letting TDD-RED/
    # TDD-GREEN fall through to the artifact heuristic below with NO
    # commit evidence at all. That let a leftover, uncommitted artifact
    # (e.g. a test file written by a dispatch that crashed before its
    # commit landed) get silently marked "already done" — reproduced:
    # `_fr_step_already_done("TDD-RED", fr_id, project, phase=3)` with an
    # empty `git log --grep` AND an on-disk `test_frXX.py` returned True.
    # The GATE1 cascade above already closes the phase-boundary gap this
    # was meant to fix (FR-02's GREEN commits pre-dating the boundary):
    # once GATE1 has genuinely PASSED for this FR/phase, the cascade
    # short-circuits TDD-RED/GREEN/IMPROVE via the sentinel + manifest
    # quality_complete signal — no commit-grep relaxation is needed, and
    # unlike a raw unscoped-grep fallback it can't reintroduce the stale
    # reset-away-lineage bug `_fr_step_lineage_boundary` exists to prevent
    # (2026-07-11 repro documented on that function). So: commit evidence
    # remains a hard requirement for every step here, TDD included.
    if not committed:
        return False

    # GATE1 / GATE1-DELTA: commit pattern alone is insufficient — a "Gate1 PASS"
    # commit may have been written with a fabricated or sub-threshold score.
    # Verify the manifest's own quality_complete verdict — the single source
    # of truth for "did this FR actually pass" (see ssi/scripts/score.py:
    # quality_complete = meets_score_gate AND open_critical==0 AND open_high==0)
    # — before treating this step as done. Comparing overall_score against
    # quality_targets.min_coverage (as this used to do) compares two
    # differently-scaled numbers: overall_score is a weighted composite of
    # linting/type_safety/test_coverage, min_coverage is a coverage-percentage
    # threshold. They can clear each other by coincidence (e.g. overall_score
    # 80.28 vs min_coverage 80) while the real per-dimension gate (test_coverage
    # scoring 42) still fails, silently skipping re-evaluation forever.
    if step.upper() in ("GATE1", "GATE1-DELTA"):
        _manifest = load_quality_manifest(project, lenient=True)
        _qc = (
            _manifest.get("gate_results", {})
            .get("gate1", {}).get(fr_id, {}).get("quality_complete")
        )
        if _qc is not True:
            # Also fires when the manifest is missing/corrupt (lenient
            # degrades to {} → .get() chain resolves to None here) —
            # re-run to be safe is the same fallback either way.
            return False   # commit exists but quality_complete not True → re-run

    # GATE1-DELTA: code-change detection (not just commit-pattern check)
    if step.upper() == "GATE1-DELTA":
        return not gate1_evidence.fr_code_changed_since_last_gate1(fr_id, project, phase=phase)

    # Dual verification for TDD
    if step.upper() == "TDD-RED":
        num_str = fr_num_str(fr_id)
        test_dir = ProjectLayout(project).active_test_dir
        test_file = test_dir / f"test_fr{num_str}.py"
        if not test_file.exists():
            return False
        # A RED state is a test that FAILS — but only while RED is still the
        # current step. GREEN's whole job is to destroy that evidence, so after
        # GREEN has genuinely landed the tree can no longer answer for RED and
        # the commit is the only record there will ever be. Asking the tree
        # anyway sends resume-fr-phase back to TDD-RED for every completed FR
        # for the rest of the run: caught by the black-box journey in
        # tests/e2e/test_cli_journeys.py, which is the first thing in this
        # repository to walk the step machine from outside.
        #
        # Note the taskq-api case still resolves correctly: a GREEN commit whose
        # tests fail is not a landed GREEN, so RED falls through to the tree and
        # reads RED — which is exactly what it is.
        if _fr_step_already_done("TDD-GREEN", fr_id, project, phase=phase):
            return True
        return _fr_tests_say(project, fr_id, expected=suite_run.RED)
    elif step.upper() == "TDD-GREEN":
        src_dir = ProjectLayout(project).active_src_dir
        if not src_dir.exists():
            return False
        num_str = fr_num_str(fr_id)
        tagged = False
        for py_file in src_dir.glob("**/*.py"):
            if num_str in py_file.name:
                tagged = True
                break
            try:
                text = py_file.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                print(f"[WARN] docstring-reference scan: could not read {py_file}: {exc}", file=sys.stderr)
                continue
            # Match fr_id as an exact, comma-separated member of any
            # `[...]` bracket block (handles both the single-tag
            # `[FR-02]` convention and the multi-tag `[FR-02, FR-03,
            # FR-04]` docstring IMPROVE-refactor produces when it
            # consolidates modules into one shared file — Bug Fix
            # Multi-Tag-Docstring, 2026-07-21). Anchored to bracket
            # contents (not a whole-file substring search) so an
            # unrelated comment like "# see FR-03, FR-09" cannot
            # false-positive match — every match must be an exact tag,
            # not a coincidental substring anywhere in the file.
            for _tag_block in re.findall(r"\[([^\]]*)\]", text):
                if fr_id in {t.strip() for t in _tag_block.split(",")}:
                    tagged = True
                    break
            if tagged:
                break
        if not tagged:
            return False
        # Round 41 站1 — and the tests it was written to make pass, pass.
        return _fr_tests_say(project, fr_id, expected=suite_run.GREEN)
    # TDD-IMPROVE / AMEND-SAB / GATE1: commit-grep success is sufficient
    # to mark the step done. GATE1 phase-scoping was already verified
    # at line 1700-1738 above; TDD-IMPROVE / AMEND-SAB rely solely on
    # the commit pattern (added by PR #18 for AMEND-SAB).
    #
    # `committed` is True only when the commit grep (line 1695-1712) hit
    # a matching commit. Without this guard, AMEND-SAB's first call
    # (before any amend-sab commit lands) would short-circuit True and
    # the dispatch would NEVER invoke cmd_amend_sab — defeating the
    # whole purpose of the dispatch.
    if committed and step.upper() in ("TDD-IMPROVE", "AMEND-SAB", "GATE1"):
        return True
    # Unknown step (MIRROR, ORCH-POST, ...): the commit grep at line
    # 1695-1712 is the only authoritative signal. If a future PR adds
    # their dict keys, the corresponding branch above should be added
    # here. Until then, conservative default is False.
    return False








def register(sub) -> None:
    """Wire this family's parsers onto the main subparser action."""
    # dispatch
    dp = sub.add_parser("dispatch", help="Spawn Agent A/B + auto-log to sessions_spawn.log (HR-10)")
    dp.add_argument("--role",    required=True, help="Agent role (developer, reviewer, etc.)")
    dp.add_argument("--fr-id",   default=None, dest="fr_id", help="FR ID (FR-01, etc.)")
    dp.add_argument("--prompt",  default="", help="Task prompt for the agent")
    dp.add_argument("--phase",   type=int, default=0, help="Phase number")
    dp.add_argument("--project", default=".", help="Project root (default: .)")
    dp.add_argument("--timeout", type=int, default=None, dest="timeout",
                    help="Max execution time in seconds (default: 1200 for P1/P2 developer, 300 otherwise).")
    dp.add_argument("--max-turns", type=int, default=None, dest="max_turns",
                    help="Max tool-using turns (default: 3 for reviewer roles, 20 for others).")
    dp.add_argument("--no-persona", action="store_true", dest="no_persona",
                    help="Skip persona for this dispatch (auto-applied for reviewer/analyst roles; use for other stateless roles).")
    dp.add_argument("--prompt-file", default=None, dest="prompt_file",
                    help="Read prompt from file instead of --prompt (avoids shell escaping issues with {} or backticks).")
    dp.add_argument("--skip-deliverable-validation", action="store_true",
                    dest="skip_deliverable_validation",
                    help="Allow custom --fr-id values for P1/P2 (e.g. P1_HOLISTIC for cross-document review).")
    dp.set_defaults(func=cmd_dispatch)

    # run-fr-step
    rfp = sub.add_parser(
        "run-fr-step",
        help="Dispatch one FR TDD step as sub-agent + push to GitHub (Phase 3-8 orchestration)",
    )
    rfp.add_argument("--phase", type=int, required=True, help="Phase number")
    rfp.add_argument("--fr-id", required=True, dest="fr_id", help="FR ID (e.g. FR-14)")
    rfp.add_argument(
        "--step", required=True, dest="step",
        choices=["TDD-RED", "TDD-GREEN", "TDD-IMPROVE", "GATE1", "GATE1-DELTA", "AMEND-SAB"],
        type=str.upper,
        help="TDD step to dispatch",
    )
    rfp.add_argument("--project", default=".", help="Project root (default: .)")
    rfp.add_argument(
        "--srs", default=None,
        help="Path to SRS.md for FR context extraction (default: .methodology/SRS.md)",
    )
    rfp.add_argument("--timeout", type=int, default=None,
                     help="Sub-agent max execution time in seconds "
                          "(default: values.timeouts.fr_step or 600)")
    rfp.add_argument("--max-turns", type=int, default=None, dest="max_turns",
                     help="Sub-agent max tool-using turns (default: per-step, 40-70)")
    rfp.add_argument("--max-fix-rounds", type=int, default=None, dest="max_fix_rounds",
                     help="Max CODE-FIX + GATE1 retry rounds on GATE1 FAIL "
                          "(default: values.max_fix_rounds or 3)")
    rfp.add_argument("--no-push", action="store_true", help="Skip git push origin HEAD after completion")
    rfp.add_argument("--no-mcp", action="store_true", dest="no_mcp",
                     help="Disable code-review-graph MCP for this FR step (debugging)")
    rfp.add_argument("--permission-mode", default=None, dest="permission_mode",
                     choices=["acceptEdits", "bypassPermissions", "default", "plan"],
                     help="Override sub-agent permission mode for every step "
                          "(default: values.permission_mode or bypassPermissions)")
    rfp.set_defaults(func=cmd_run_fr_step)

    # resume-fr-phase
    rrp = sub.add_parser(
        "resume-fr-phase",
        help="Find next pending FR step after a crash — prints the run-fr-step command to run",
    )
    rrp.add_argument("--phase", type=int, required=True, help="Phase number")
    rrp.add_argument("--project", default=".", help="Project root (default: .)")
    rrp.set_defaults(func=cmd_resume_fr_phase)

    # run-tool (Bug #110) — CLI dispatcher for individual tool invocations
    # referenced by the generated plan templates. Thin wrapper around
    # harness.tool_runners.run_tool + compute_tool_score.
    rt = sub.add_parser(
        "run-tool",
        help="Run a single framework tool (e.g. ast-error-handling) and print its "
             "score. Used by plan templates that previously referenced a non-existent "
             "subcommand (Bug #110).",
    )
    rt.add_argument("tool", help="Tool id (e.g. ast-error-handling, ruff, mypy, …)")
    rt.add_argument("--project", default=".", help="Project root (default: .)")
    rt.add_argument("--timeout-override", type=int, default=None,
                    help="Override per-tool default timeout in seconds")
    rt.add_argument("--json", action="store_true",
                    help="Emit {tool, returncode, output, score} as JSON")
    rt.set_defaults(func=cmd_run_tool)

    # reload-policy
    rl = sub.add_parser("reload-policy", help="Hot-reload enforcement policies from enforcement.json")
    rl.add_argument(
        "--policy-file",
        default="enforcement/enforcement.json",
        help="Path to enforcement.json (default: enforcement/enforcement.json)",
    )
    rl.set_defaults(func=cmd_reload_policy)
