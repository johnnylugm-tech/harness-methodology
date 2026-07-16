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
from core.agent_spawner import (
    _COMMIT_REQUIRED_STEPS,
    is_structurally_broken,
)
from core.canonical_form import fr_num_str
from core.harness_config import get_timeout, get_value
from core.pre_flight import check_cli_tools
from core.quality_gate import gate1_evidence
from core.quality_gate.cov_utils import resolve_fr_scoped_src_files
from core.quality_gate.ghost_detector import (
    detect_ghost_changes,
    write_ghost_paper_trail,
)
from core.quality_gate.legal_artifacts import PHASE_DELIVERABLES
from core.utils.project_layout import ProjectLayout
from harness import tool_checks


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
    _fr_conf: dict = {}
    _fr_manifest_path = project / ".methodology" / "quality_manifest.json"
    try:
        _fr_conf = (
            json.loads(_fr_manifest_path.read_text(encoding="utf-8"))
            .get("fr_config", {}).get(fr_id, {})
        )
    except (OSError, json.JSONDecodeError, AttributeError):
        pass
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
            gate1_evidence.record_gate_timestamp(project, phase, 1, fr_id)
        return 0

    # 2. Pre-flight checks — must pass before agent dispatch
    preflight_ok, preflight_errors = _fr_step_preflight(step, project, fr_id, srs_path=srs_path)
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

    def _max_turns(step_name: str) -> int:
        """Per-step max_turns: explicit --max-turns wins, then per-FR config,
        then values.step_max_turns (per-project overlay), else _STEP_MAX_TURNS."""
        if _explicit_max_turns is not None:
            return _explicit_max_turns
        if step_name.upper() in ("CODE-FIX", "COVERAGE-FIX") and _fr_code_fix_max_turns:
            return _fr_code_fix_max_turns
        step = step_name.upper()
        if step in _turns_overlay:
            return _turns_overlay[step]
        return _STEP_MAX_TURNS.get(step, 40)

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
    except Exception:
        _pre_step_sha = ""

    # Fix H-H (P3 2026-07-15 round 4): TDD-RED/GREEN/IMPROVE/MIRROR/amend-sab/
    # ORCH-POST (_COMMIT_REQUIRED_STEPS minus GATE1/GATE1-DELTA) previously had
    # zero retry on this first dispatch — see _STEP_RETRY_ATTEMPTS docstring.
    # Bounded plain re-dispatch (identical prompt, no failure classification)
    # before falling through to the unchanged error-handling block below.
    # REGRESSION_GUARD (hard reject) and an already-exhausted STRUCTURAL
    # signature (Fix H-G already retried that 3x at the transport layer) are
    # never retried here — both fall straight through on attempt 1.
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
            task_timeout=_fr_timeout,
            max_turns=_max_turns(step),
            mcp_config=phase_ctx["mcp_config"],
            setting_sources=phase_ctx["setting_sources"],
            permission_mode=_pmode,
        )
        _status = result.get("status")
        _step_retryable = (
            step in _COMMIT_REQUIRED_STEPS
            and step not in ("GATE1", "GATE1-DELTA")
            and _status in _DISPATCH_ERROR_STATUSES
            and _status != "REGRESSION_GUARD"
            and not _is_connector_disabled_failure(result.get("output", ""))
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
            _mf_qc = False
            try:
                _mf_path = project / ".methodology" / "quality_manifest.json"
                if _mf_path.exists():
                    _mf_json = json.loads(_mf_path.read_text(encoding="utf-8"))
                    _fr_entry = (_mf_json.get("gate_results", {})
                                 .get("gate1", {}).get(fr_id, {}))
                    _mf_qc = bool(_fr_entry.get("quality_complete", False))
            except (OSError, json.JSONDecodeError):
                pass
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

        for fix_round in range(1, max_fix_rounds + 1):
            if gate_pass or _fr_step_already_done(step, fr_id, project, phase=phase):
                break

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
                        print(f"[run-fr-step] {fr_id} BLOCKED: 2 consecutive no-progress rounds"
                              f" — human intervention required\n"
                              f"  Error pattern: {curr_sig[:150]}")
                        return 2
                else:
                    no_progress_count = 0
                prev_snapshot_sig = curr_sig

                # ── A: classify failure → route to the correct fixer ─────────────
                failure_class = _classify_snapshot_failure(tool_snapshot, failing_dims=failing_dims)

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
                            except Exception:
                                pass
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
                        # Read min_coverage from manifest (default 80.0,
                        # same default _check_gate1_live_coverage uses).
                        _cov_min = 80.0
                        try:
                            _mfst = json.loads(
                                (Path(str(project)) / ".methodology"
                                 / "quality_manifest.json").read_text(
                                    encoding="utf-8"))
                            _cov_min = float(
                                (_mfst.get("quality_targets") or {})
                                .get("min_coverage", 80.0))
                        except (OSError, ValueError, json.JSONDecodeError):
                            pass
                        try:
                            _live_cov = gate1_evidence.validate_fr_coverage_immediate(
                                Path(str(project)))
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
                                _mfst = json.loads(
                                    _mfst_path.read_text(encoding="utf-8"))
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
                                    json.JSONDecodeError) as _mfst_exc:
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
        else:
            print(f"[run-fr-step] {fr_id} GATE1 BLOCKED after {max_fix_rounds} CODE-FIX rounds"
                  " — human intervention required")
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
    if step in _COMMIT_REQUIRED_STEPS:
        dirty = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True, text=True, cwd=str(project),
        ).stdout.strip()
        if dirty:
            print(
                f"\n[BLOCKED] {fr_id} {step}: commit did not land — "
                f"working tree still dirty after step.\n"
                f"  Likely cause: prepare-commit-msg hook rejection "
                f"(stale trace attestation, FSM check, etc.).\n"
                f"  Fix the hook-reported error, then re-run:\n"
                f"    python harness_cli.py resume-fr-step --phase {phase} "
                f"--fr-id {fr_id} --project {project}\n"
                f"  Dirty files:\n{dirty[:2000]}",
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
    manifest_path = project / ".methodology" / "quality_manifest.json"
    progress_path = project / ".methodology" / "fr_progress.json"

    fr_ids: list[str] = []
    if manifest_path.exists():
        try:
            fr_ids = json.loads(manifest_path.read_text(encoding="utf-8")).get("fr_ids", [])
        except Exception:
            pass
    if not fr_ids and progress_path.exists():
        try:
            data = json.loads(progress_path.read_text(encoding="utf-8"))
            fr_ids = list(data.get("frs", {}).keys())
        except Exception:
            pass

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
_DISPATCH_ERROR_STATUSES: frozenset[str] = frozenset({
    "REJECT", "BLOCKED", "FAILED", "ERROR", "TIMEOUT", "REGRESSION_GUARD",
    "AWAITING_CONFIRMATION", "NOTHING_TO_DO",
})

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
            m = json.loads(manifest_path.read_text(encoding="utf-8"))
            registered = m.get("fr_ids", [])
            if fr_id and fr_id not in registered:
                errors.append(
                    f"✗ FR-ID {fr_id} not in quality_manifest.json fr_ids ({', '.join(registered)})"
                )
        except Exception:
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
            except Exception:
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
        except Exception:
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
    except Exception:
        pass
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
    "GATE1":       "feat({fr_id}): Gate1 PASS",         # prefix match; phase-scoped
    "GATE1-DELTA": "feat({fr_id}): Gate1 PASS",         # same prefix + git diff check
}


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
    state_path = project / ".methodology" / "state.json"
    if not state_path.exists():
        return None
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
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
        _manifest_path = project / ".methodology" / "quality_manifest.json"
        try:
            _manifest = json.loads(_manifest_path.read_text(encoding="utf-8"))
            _qc = (
                _manifest.get("gate_results", {})
                .get("gate1", {}).get(fr_id, {}).get("quality_complete")
            )
            if _qc is not True:
                return False   # commit exists but quality_complete not True → re-run
        except (OSError, json.JSONDecodeError, ValueError, AttributeError):
            return False       # manifest unreadable → re-run to be safe

    # GATE1-DELTA: code-change detection (not just commit-pattern check)
    if step.upper() == "GATE1-DELTA":
        return not gate1_evidence.fr_code_changed_since_last_gate1(fr_id, project, phase=phase)

    # Dual verification for TDD
    if step.upper() == "TDD-RED":
        num_str = fr_num_str(fr_id)
        test_dir = ProjectLayout(project).active_test_dir
        test_file = test_dir / f"test_fr{num_str}.py"
        return test_file.exists()
    elif step.upper() == "TDD-GREEN":
        src_dir = ProjectLayout(project).active_src_dir
        if not src_dir.exists():
            return False
        num_str = fr_num_str(fr_id)
        for py_file in src_dir.glob("**/*.py"):
            if num_str in py_file.name:
                return True
            try:
                if f"[{fr_id}]" in py_file.read_text(encoding="utf-8"):
                    return True
            except Exception:
                pass
        return False
    return True






def _extract_srs_fr_section(srs_path: Path, fr_id: str) -> str:
    """Extract a single FR's full markdown section from SRS.md.

    Returns text between '### FR-XX: ...' header and the next '### FR-' or '---'.
    Falls back to empty string if the section is not found.
    """
    if not srs_path or not srs_path.exists():
        return ""
    content = srs_path.read_text(encoding="utf-8")
    pat = re.compile(
        rf"(### {re.escape(fr_id)}:[^\n]+\n)(.*?)(?=\n---\n|\n### FR-\d+|$)",
        re.DOTALL,
    )
    m = pat.search(content)
    return (m.group(1) + m.group(2)).strip() if m else ""




def _extract_test_spec_names(project: Path, fr_id: str) -> tuple[list[str], str]:
    """Parse TEST_SPEC.md and return (test_names, formatted_note) for a given FR.

    Returns ([], "") when TEST_SPEC.md is missing or has no entries for this FR.
    """
    test_spec_path = ProjectLayout(project).test_spec_path
    if not test_spec_path.exists():
        return [], ""

    spec_text = test_spec_path.read_text(encoding="utf-8")
    current_fr = ""
    spec_rows: list[str] = []
    for line in spec_text.splitlines():
        stripped = line.strip()
        m = re.match(r"^###\s+([A-Z]+-\d+)(?:[:\s]|$)", stripped)
        if m:
            current_fr = m.group(1)
            continue
        if current_fr != fr_id:
            continue
        if "Test Function" in stripped:
            continue
        if stripped.startswith("|") and stripped.endswith("|"):
            cols = [c.strip() for c in stripped.split("|")[1:-1]]
            if len(cols) >= 2:
                clean_col = cols[1].strip(" `")
                if clean_col.startswith("test_"):
                    spec_rows.append(clean_col)
            continue
    if spec_rows:
        note = (
            f"\n[TEST SPEC — match these EXACT names]\n"
            f"TEST_SPEC.md at `02-architecture/TEST_SPEC.md` defines "
            f"{len(spec_rows)} test cases for {fr_id}. Write ALL of them "
            f"using these EXACT function names:\n"
            + "\n".join(f"  - {fn}" for fn in spec_rows)
            + "\nDo NOT invent names. spec-coverage-check uses exact match.\n"
        )
        return spec_rows, note
    return [], ""


# ---------------------------------------------------------------------------
# FR step prompt helpers — each builder returns the prompt string for one step.
# Called from _build_fr_step_prompt() dispatcher below.
# ---------------------------------------------------------------------------

def _compute_fr_spec_data(project: Path, fr_id: str, test_file: str) -> dict:
    """Compute spec test coverage data needed by GATE1, CODE-FIX, COVERAGE-FIX."""
    spec_test_names, _ = _extract_test_spec_names(project, fr_id)
    test_file_path = project / test_file
    existing_spec_tests: set[str] = set()
    if spec_test_names and test_file_path.exists():
        try:
            tf_content = test_file_path.read_text(encoding="utf-8")
            _actual_fns = set()
            for line in tf_content.splitlines():
                m2 = re.match(r"^\s*(?:async\s+)?def\s+(test_\w+)\s*\(", line)
                if m2:
                    _actual_fns.add(m2.group(1))
            for fn in spec_test_names:
                raw_fn = fn.strip("`").strip()
                raw_fn = re.sub(r"\[.*\]$", "", raw_fn)
                raw_fn = re.sub(r"\(\)$", "", raw_fn)
                if raw_fn in _actual_fns:
                    existing_spec_tests.add(fn)
        except (OSError, UnicodeDecodeError):
            pass
    spec_cov_pct = (
        round(len(existing_spec_tests) / max(len(spec_test_names), 1) * 100)
        if spec_test_names else 100
    )
    missing_spec_count = len(spec_test_names) - len(existing_spec_tests)
    spec_summary = (
        f"SPEC COVERAGE: {len(existing_spec_tests)}/{len(spec_test_names)} "
        f"({spec_cov_pct}%) — {missing_spec_count} missing"
        if spec_test_names else ""
    )
    return {
        "spec_test_names": spec_test_names,
        "existing_spec_tests": existing_spec_tests,
        "spec_cov_pct": spec_cov_pct,
        "missing_spec_count": missing_spec_count,
        "spec_summary": spec_summary,
    }


def _build_fr_step_prompt(step: str, fr_id: str, phase: int,
                           project: Path, srs_path: Path | None,
                           failing_dims: list | None = None,
                           tool_snapshot: str | None = None,
                           block_reason: str | None = None) -> str:
    """Build a minimal need-to-know prompt for a single FR TDD step.

    Each prompt is self-contained — the sub-agent receives only what it needs
    for that specific step (SRS section, test file content, etc.).

    Args:
        failing_dims: Required for CODE-FIX step — list of failing Gate 1
            dimension names.  Ignored for all other steps.
        tool_snapshot: Optional pre-run tool output (ruff + pytest) captured
            at orchestration time.  Injected into CODE-FIX prompt so agents
            can fix targeted errors without re-discovering them.
        block_reason: Optional finalize-gate block reason (e.g. S3/S4 detail)
            extracted from previous GATE1 sub-agent output.  Injected into
            GATE1 and CODE-FIX prompts so agents understand WHY gate blocked.

    Dispatches to step-specific builders.  Shared pre-computation (test_file,
    src_dir, srs_path normalisation, spec data) done once here.
    """
    step = step.upper()
    num_str = fr_num_str(fr_id)
    _layout = ProjectLayout(project)
    test_dir_str = _layout.get_relative_str(_layout.active_test_dir)
    test_file = f"{test_dir_str}/test_fr{num_str}.py"
    src_dir = "03-development/src"

    # Default SRS path if not given
    if srs_path is None:
        srs_path = ProjectLayout(project).srs_path

    if step == "TDD-RED":
        srs_section = _extract_srs_fr_section(srs_path, fr_id) if srs_path else ""
        _, spec_note = _extract_test_spec_names(project, fr_id)

        # CRG semantic search: find existing related code to avoid re-implementing
        _related_ctx = ""
        try:
            from harness.crg_bridge import CRGBridge as _CRGBridge
            _crg_sr = _CRGBridge()
            _sr = _crg_sr.semantic_search(str(project), fr_id, kind="Function", limit=5)
            _hits = (_sr or {}).get("results", [])
            if _hits:
                _related_ctx = (
                    "[RELATED EXISTING CODE — CRG semantic search]\n"
                    + "\n".join(
                        f"  - {h.get('name','?')} "
                        f"({(h.get('file_path') or '').split('/')[-1]})"
                        for h in _hits[:5]
                    )
                    + "\n\n"
                )
        except Exception:
            pass  # graceful: CRG not available or no match

        return (
            f"You are a TDD developer. Your ONLY task: write failing pytest tests for {fr_id}.\n\n"
            f"{spec_note}"
            f"{_related_ctx}"
            f"[FORBIDDEN — read before anything else]\n"
            f"- Implementing any source code (test file only)\n"
            f"- app/infrastructure/ paths\n"
            f"- @covers: L1 Error | @type: edge annotations\n"
            f"- Using try/except ImportError or lazy imports to hide ModuleNotFoundError. It is EXPECTED and PERFECTLY FINE for pytest to crash with Collection Error (Exit Code 2) because the source code doesn't exist yet.\n\n"
            f"[UNIT TEST CONTRACT — avoid false-fail traps]\n"
            f"Tests must fail because the FEATURE is missing, not because of external side-effects.\n"
            f"- Use standard top-level imports (e.g. `from src.engines.xxx import yyy`). Do NOT use try/except ImportError. If pytest returns Exit Code 2 (Collection Error) due to missing modules, this is a VALID RED STATE. Do not try to \"fix\" it by hiding the import.\n"
            f"- If tests call methods that perform real external operations (HMAC signature\n"
            f"  verification, DB connections, HTTP calls), use a pytest autouse fixture in\n"
            f"  `tests/conftest.py` (or an inline @pytest.fixture) to mock them. This is\n"
            f"  NOT 'implementing the feature' — it is required test isolation.\n"
            f"- Example: a pipeline.process() call performs HMAC verification internally.\n"
            f"  Add an autouse fixture: monkeypatch.setattr(Verifier, 'verify', lambda *a: True)\n"
            f"  so the test fails because the pipeline logic is absent, not because of bad sig.\n"
            f"- If you use patch.object(obj, 'method_name', ...) in a test, add a comment\n"
            f"  directly above that test explaining what the GREEN agent must implement:\n"
            f"  # GREEN TODO: <ClassName> must have <method_name>(self, *args) -> <return_type>\n"
            f"  Do NOT add stubs to source files yourself — GREEN does that.\n\n"
            f"[INTEGRATION FR GUIDELINES — applies when this FR exercises CLI / subprocess / cross-process state]\n"
            f"(v2.13.0 — covers FR-05 P3 2026-07-16 lesson. Skip this block if your FR is\n"
            f"purely a library function; read it if test_file ever calls `subprocess.run`,\n"
            f"`cli.main(...)`, or exercises a stateful fixture like breaker/cache/store.)\n\n"
            f"- When using `subprocess.run([sys.executable, \"-m\", \"<your_package>\", ...])`:\n"
            f"  * Always propagate PYTHONPATH to the child env (pytest's `pythonpath = ...`\n"
            f"    in setup.cfg does NOT propagate to child processes):\n"
            f"        env = os.environ.copy()\n"
            f"        env[\"<PROJECT_HOME_VAR>\"] = str(child_home)\n"
            f"        src_root = Path(__file__).resolve().parent.parent / 'src'\n"
            f"        env['PYTHONPATH'] = str(src_root) + os.pathsep + env.get('PYTHONPATH','')\n"
            f"  * Decide in-process vs out-of-process explicitly; add a comment naming the choice.\n"
            f"- When one test function exercises multiple scenarios (e.g. exit code 0/1/2/3/4):\n"
            f"  * Split into N separate test functions, one per scenario. The TEST_SPEC may\n"
            f"    list N scenarios as ONE Inputs row when the prose AC enumerates them; you\n"
            f"    MUST translate that into N test_frNN_MM_* functions, each testing one\n"
            f"    scenario in isolation.\n"
            f"  * Use function-scoped fixtures (not module-scoped) so per-case state cannot\n"
            f"    leak (e.g. breaker.json OPEN from case 3 must not affect case 5).\n"
            f"  * NEVER rely on `monkeypatch` ordering to override earlier state mutations.\n"
            f"- Sub-assertion local-variable names must NOT shadow stdlib modules:\n"
            f"  FORBIDDEN as a local name in your test: json, os, sys, time, subprocess,\n"
            f"  pathlib, asyncio, typing, logging, path, file, id, type, dict, list, set,\n"
            f"  tuple, str, int, bool, bytes. If a TEST_SPEC sub-assertion predicate uses\n"
            f"  one of these (e.g. `json == \"true\"`), RENAME your local (e.g. `json_flag`)\n"
            f"  but preserve the rule_id comment intact. The check-test-spec-consistency\n"
            f"  gate would have rejected the spec already; if you see a collision here,\n"
            f"  use a domain-specific synonym.\n"
            f"- When TEST_SPEC Inputs + SRS.md prose AC seem inconsistent (e.g. AC says\n"
            f"  \"5 of which 3 done\" but Inputs lists 5 identical commands), DO NOT invent\n"
            f"  impossible assertions. Add `# SPEC_AMBIGUITY: <one-line>` comment in the\n"
            f"  test, prefer the prose AC's scenario, and write the test to construct it\n"
            f"  mechanically (e.g. mix success+failure commands to produce the desired\n"
            f"  distribution). If you truly cannot construct the scenario, write the test\n"
            f"  against the SIMPLER invariant (>= 1 instead of == 3) and note the deviation.\n\n"
            f"[FR REQUIREMENTS]\n"
            f"{srs_section or f'See SRS.md for {fr_id} requirements'}\n\n"
            f"[TASK]\n"
            f"1. Create/edit `{test_file}` with failing tests covering the acceptance criteria above. "
            f"If the file already exists (e.g. from a prior interrupted run), verify its test names "
            f"match the TEST SPEC exactly, fix any mismatch, but do NOT skip step 5 — an existing-but-"
            f"uncommitted file is exactly the state this step must resolve, not something to leave as-is.\n"
            f"2. Every test function name MUST match the TEST SPEC names listed above exactly.\n"
            f"3. The tests MUST FAIL — do NOT implement the feature yet.\n"
            f"4. Run `python3 -m pytest {test_file} -q`. Tests failing or raising Collection Error (ModuleNotFoundError) means SUCCESS for this RED step.\n"
            f"5. Commit: `git add {test_file} && git commit -m 'test(RED): failing test for {fr_id}'`\n\n"
            f'[OUTPUT FORMAT]\nReturn JSON: {{"status": "DONE", "test_file": "{test_file}", '
            f'"commit": "<hash>", "summary": "<under 50 chars>"}}'
        )

    if step == "TDD-GREEN":
        srs_section = _extract_srs_fr_section(srs_path, fr_id) if srs_path else ""
        test_content = ""
        tf = project / test_file
        if tf.exists():
            test_content = tf.read_text(encoding="utf-8")
        return (
            f"You are a TDD developer. Your task: implement {fr_id} until the failing test passes.\n\n"
            f"[FORBIDDEN — read before anything else]\n"
            f"- Modifying test files\n"
            f"- app/infrastructure/ paths\n\n"
            f"[IMPLEMENTATION CONTRACT]\n"
            f"Before writing any code, scan `{test_file}` for:\n"
            f"  1. patch.object(obj, 'method_name', ...) — every patched method_name MUST\n"
            f"     exist in your implementation (even as a stub returning {{}}). Missing\n"
            f"     attributes cause AttributeError before the test even runs.\n"
            f"  2. autouse fixtures that mock verifiers — means the test bypasses real HMAC/auth.\n"
            f"     Do NOT add HMAC bypass to production code; the fixture already handles it.\n"
            f"  3. Any test that asserts on status codes (200/500/429/401) from a top-level\n"
            f"     orchestrator or pipeline method — verify the implementation handles unexpected\n"
            f"     exceptions and returns a structured error response rather than propagating.\n"
            f"     Only add try/except if the tests actually require it; do not add for utilities.\n\n"
            f"[FAILING TEST — {test_file}]\n"
            f"{test_content or f'(read from {test_file})'}\n\n"
            f"[FR REQUIREMENTS]\n"
            f"{srs_section or f'See SRS.md for {fr_id} requirements'}\n\n"
            f"[TASK]\n"
            f"1. Scan test file per [IMPLEMENTATION CONTRACT] above before writing any code.\n"
            f"2. Create/edit source files in `{src_dir}/` to make `{test_file}` pass.\n"
            f"3. Run `python3 -m pytest {test_file} -q` — all tests must pass.\n"
            f"4. Docstrings must include `[{fr_id}]` tag + `Citations:` with line numbers (HR-15).\n"
            f"5. Commit: `git add {src_dir}/ && git commit -m 'feat({fr_id}): GREEN'`\n\n"
            f'[OUTPUT FORMAT]\nReturn JSON: {{"status": "DONE", "files_changed": [...], '
            f'"commit": "<hash>", "summary": "<under 50 chars>"}}'
        )

    if step == "TDD-IMPROVE":
        test_content = ""
        tf = project / test_file
        if tf.exists():
            test_content = tf.read_text(encoding="utf-8")[:1500]
        return (
            f"You are a TDD refactorer. Your task: improve {fr_id} WITHOUT breaking tests.\n\n"
            f"[FORBIDDEN — read before anything else]\n"
            f"- Modifying test files (any file under tests/)\n"
            f"- Setting enum values to None (e.g. STATUS = None, EXIT = None)\n"
            f"- Changing sys.exit() codes from their current values\n"
            f"- Injecting XX...XX placeholder markers into source files\n\n"
            f"[TEST INVARIANTS — {test_file} (first 1500 chars)]\n"
            f"{test_content or f'(read from {test_file})'}\n\n"
            f"[TASK]\n"
            f"1. Run `python3 -m pytest {test_file} -q` first — confirm all pass before any changes.\n"
            f"2. Refactor source code in `{src_dir}/` for clarity, remove duplication, improve naming.\n"
            f"3. Re-run `python3 -m pytest {test_file} -q` — must still pass.\n"
            f"4. If changes made: `git commit -m 'refactor({fr_id}): IMPROVE'`\n"
            f"5. If no refactor needed: no commit required.\n\n"
            f'[OUTPUT FORMAT]\nReturn JSON: {{"status": "DONE", "refactored": true/false, '
            f'"commit": "<hash or null>", "summary": "<under 50 chars>"}}'
        )

    # Spec data: compute once, pass to GATE1 / CODE-FIX / COVERAGE-FIX builders.
    spec = _compute_fr_spec_data(project, fr_id, test_file)
    spec_test_names = spec["spec_test_names"]
    existing_spec_tests = spec["existing_spec_tests"]
    spec_cov_pct = spec["spec_cov_pct"]
    missing_spec_count = spec["missing_spec_count"]
    spec_summary = spec["spec_summary"]

    # GATE1-DELTA no longer passes --delta to run-gate. The skip-if-unchanged
    # decision is now made by _fr_step_already_done() via git diff before dispatch.
    # Once we reach here, code has changed → full GATE1 evaluation.
    if step in ("GATE1", "GATE1-DELTA"):

        # ── TEST_SPEC.md required test names for test_coverage evaluation ──
        spec_test_names, _ = _extract_test_spec_names(project, fr_id)
        spec_section = ""
        if spec_test_names:
            spec_section = (
                f"\n[TEST SPEC — required test cases for {fr_id}]\n"
                f"TEST_SPEC.md requires these EXACT test functions:\n"
                + "\n".join(f"  - {fn}" for fn in spec_test_names)
                + "\n\nWhen evaluating test_coverage, verify:\n"
                "  - EVERY required test EXISTS in the test file\n"
                "  - EVERY required test PASSES (not skipped, not failing)\n"
                "  - Missing or failing required test = test_coverage FAIL, "
                "regardless of raw coverage %\n\n"
            )

        # ── Previous block reason (S3/S4) surfaced for retry ──
        block_section = ""
        if block_reason:
            block_section = (
                f"\n[PREVIOUS ATTEMPT BLOCKED — read carefully]\n"
                f"{block_reason}\n"
                f"Ensure the gate1_result.json you write this time satisfies the\n"
                f"tool_evidence requirement described in step 3 below.\n\n"
            )

        # ── Spec test coverage status (inject so evaluator knows the current state) ──
        spec_section = ""
        if spec_test_names:
            spec_section = (
                f"\n[TEST SPEC — required test cases for {fr_id}]\n"
                f"TEST_SPEC.md requires these EXACT test functions:\n"
                + "\n".join(f"  - {fn}" for fn in spec_test_names)
                + f"\n\n{spec_summary}\n"
                f"→ score = min(coverage_pct, spec_cov_pct). Missing tests count as 0.\n"
                f"  All required tests MUST exist and pass — partial coverage = partial score.\n\n"
            )

        return (
            f"You are a Gate 1 evaluator. Your task: run Gate 1 evaluation for {fr_id}.\n"
            f"{spec_section}"
            f"{block_section}"
            f"[STOP RULE — follow when tools fail or you are unsure]\n"
            f"- If run-gate itself prints [BLOCKED] (SAB phantom/unregistered module, "
            f"manifest corruption — a PRECONDITION failure, the dimension tools never ran):\n"
            f"  → Do NOT write gate1_result.json and do NOT record any score=0\n"
            f"  → Report status INFRA_BLOCKED with the verbatim [BLOCKED] message\n"
            f"  → This is an infrastructure problem, not a code-quality verdict — "
            f"recording zeros here poisons the manifest and dispatches fixes at healthy code\n"
            f"- If a single dimension tool command fails to execute (error, not found, env issue):\n"
            f"  → Record score=0 for that dimension\n"
            f"  → Set tool_evidence = first 300 chars of the error output\n"
            f"  → Move on to the next dimension — do NOT retry the same command\n"
            f"- If finalize-gate prints [BLOCKED]:\n"
            f"  → Include the exact BLOCKED message in your output summary\n"
            f"  → Do NOT attempt to fix source code yourself — that is CODE-FIX's job\n"
            f"- Write gate1_result.json and call finalize-gate within 10 turns of starting.\n"
            f"  A low score with tool_evidence is always better than a timeout.\n\n"
            f"[TASK — follow EXACTLY in order]\n"
            f"1. Run: `python3 harness_cli.py run-gate --gate 1 --phase {phase} "
            f"--fr-id {fr_id} --project {project}`\n"
            f"   The output contains FR-SCOPED TOOL OVERRIDES — exact commands for each\n"
            f"   dimension.  Use those commands, not the generic ones in evaluate_dimension.md.\n\n"
            f"2. Run the three tool commands from step 1's FR-SCOPED TOOL OVERRIDES:\n"
            f"   a. linting:      ruff check ... (exact command shown in run-gate output)\n"
            f"   b. type_safety:  pyright ... (exact command shown in run-gate output)\n"
            f"   c. test_coverage: coverage run / pytest ... (exact command shown in run-gate output)\n"
            f"   Save each tool's output to .sessi-work/round_1/tools/<dimension>.txt\n\n"
            f"3. Write `.sessi-work/gate1_result.json` with this EXACT schema:\n"
            f"   {{\n"
            f'     "gate": 1, "phase": {phase}, "fr_id": "{fr_id}",\n'
            f'     "overall_score": <float>,           // weighted avg of breakdown scores\n'
            f'     "quality_complete": true,            // true if overall_score >= 80\n'
            f'     "rounds_used": 1,\n'
            f'     "breakdown": {{\n'
            f'       "linting":       {{"score": <0-100>, "threshold": 90, "tool_evidence": "<first 500 chars of ruff stdout>"}},\n'
            f'       "type_safety":   {{"score": <0-100>, "threshold": 85, "tool_evidence": "<first 500 chars of pyright stdout>"}},\n'
            f'       "test_coverage": {{\n'
            f'           "score": <0-100>, "threshold": 80,\n'
            f'           "tests_passed": <int>,   // REQUIRED: count from pytest summary line\n'
            f'           "tests_failed": <int>,   // REQUIRED: must be 0 — any failed test blocks the gate\n'
            f'           "tests_skipped": <int>,  // REQUIRED: count skipped tests\n'
            f'           "tool_evidence": "<first 500 chars of coverage/pytest stdout>"\n'
            f'       }}\n'
            f'     }}\n'
            f"   }}\n"
            f"   overall_score = (linting.score × 0.33 + type_safety.score × 0.33 + test_coverage.score × 0.34).\n"
            f"   quality_complete = (overall_score >= 80) AND (every dimension score >= its threshold).\n"
            f"   CRITICAL: `tool_evidence` is REQUIRED for every dimension.\n"
            f"   If you omit it, finalize-gate will BLOCK with S3 error regardless of scores.\n"
            f"   Score fabrication (writing a score without running the tool) also causes S3 block.\n"
            f"   CRITICAL: `tests_failed` MUST be 0. finalize-gate parses tool_evidence for\n"
            f"   '{{N}} failed' and blocks immediately if any test is red — even at 96% coverage.\n\n"
            f"   Scoring formulas:\n"
            f"   - linting:      ruff exit 0 → 100; else count violations: max(0, 100 - violations×5)\n"
            f"   - type_safety:  parse pyright JSON summary.errorCount: max(0, 100 - errorCount×5)\n"
            f"   - test_coverage: score = min(coverage_pct, spec_cov_pct).\n"
            f"     spec_cov_pct = (existing_required_tests / total_required) × 100.\n"
            f"     Currently: {missing_spec_count} required tests missing → spec_cov_pct = {spec_cov_pct}% → score capped at {spec_cov_pct}.\n"
            f"     ALL required tests must exist and pass — partial spec coverage = partial score.\n\n"
            f"4. Run: `python3 harness_cli.py finalize-gate --gate 1 --phase {phase} "
            f"--fr-id {fr_id} --project {project}`\n"
            f"   If finalize-gate prints [BLOCKED], include the exact error in your output summary.\n\n"
            f"5. Report pass/fail and failing dimensions (if any).\n\n"
            f'[OUTPUT FORMAT]\nReturn JSON: {{"status": "DONE", "gate_score": <float>, '
            f'"pass": true/false, "failing_dims": [...], "commit": "<hash or null>", '
            f'"summary": "<under 50 chars>"}}'
        )

    if step == "TEST-FIX":
        # Dispatched when _classify_snapshot_failure returns "ISOLATION":
        # tests fail because infrastructure (HMAC, DB, HTTP) intercepts before
        # feature logic runs. Fix is to add autouse fixtures — not to touch source.
        return (
            f"You are a test isolation fixer for {fr_id}.\n\n"
            f"[FORBIDDEN — read first]\n"
            f"- Modifying source files in `{src_dir}/`\n"
            f"- Deleting or xfail-marking tests\n\n"
            f"[PROBLEM]\n"
            f"Gate 1 tests are failing because of EXTERNAL SIDE-EFFECTS, not because the "
            f"feature is missing. Tests call real infrastructure (HMAC verification, DB "
            f"connections, HTTP calls) that short-circuits before feature logic is reached. "
            f"Every test returns the same infrastructure error (e.g. 401 Unauthorized).\n\n"
            f"[ACTUAL TOOL OUTPUT]\n"
            f"{tool_snapshot or '(not available)'}\n\n"
            f"[TASK]\n"
            f"1. Identify the infrastructure call that intercepts (HMAC verifier, DB, HTTP).\n"
            f"2. Add a pytest autouse fixture to `{test_file}` (or `tests/conftest.py`) "
            f"that mocks it so tests reach the feature logic:\n"
            f"   @pytest.fixture(autouse=True)\n"
            f"   def _bypass_infra(monkeypatch):\n"
            f"       monkeypatch.setattr(InfraClass, 'verify', lambda *a, **kw: True)\n"
            f"3. Run `python3 -m pytest {test_file} -q` — tests must now fail for the RIGHT reason "
            f"(AssertionError or NameError from missing feature, NOT 401/auth error).\n"
            f"4. Commit: `git add {test_file} tests/conftest.py && "
            f"git commit -m 'test({fr_id}): fix test isolation — add autouse infra mock'`\n\n"
            f'[OUTPUT FORMAT]\nReturn JSON: {{"status": "DONE", "fixture_added": true, '
            f'"commit": "<hash>", "summary": "<under 50 chars>"}}'
        )

    if step == "COVERAGE-FIX":
        # Dispatched when _classify_snapshot_failure returns "LOW_COVERAGE":
        # all Gate 1 tests pass but test_coverage dimension is still failing.
        # Two root causes:
        #   A. Existing tests don't cover enough source lines (code_cov < 80%).
        #   B. Required test functions from TEST_SPEC.md are absent (spec_cov < 80%).
        #
        # Coverage measurement here MUST match the scope run-gate --fr-id
        # already uses (fr_module_traceability), not the whole src_dir — the
        # whole tree includes OTHER FRs' not-yet-implemented stub modules
        # (0% coverage each), making an 80% whole-tree target unsatisfiable
        # from this FR's own test file alone (P3 2026-07-12: FR-01/FR-02 both
        # BLOCKED after 2 no-progress rounds chasing the wrong denominator).
        _cf_manifest: dict = {}
        try:
            _cf_manifest = json.loads(
                (project / ".methodology" / "quality_manifest.json").read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError, ValueError, AttributeError):
            pass
        _cf_src_files = resolve_fr_scoped_src_files(
            str(project), fr_id, test_file, src_dir, _cf_manifest
        )
        if _cf_src_files:
            _cf_include = ",".join(_cf_src_files)
            _cov_check_cmd = (
                f'python3 -m coverage run -m pytest {test_file} -q '
                f'&& python3 -m coverage report --include="{_cf_include}" -m'
            )
        else:
            _cov_check_cmd = f"python3 -m pytest {test_file} --cov={src_dir} --cov-report=term-missing -q"
        return (
            f"You are a coverage fixer for {fr_id}.\n\n"
            f"[FORBIDDEN — read first]\n"
            f"- Deleting or xfail-marking existing tests\n"
            f"- Adding `# pragma: no cover` to lines that CAN be tested (only use it as a "
            f"last resort for genuinely untestable lines — see ESCAPE HATCH below)\n\n"
            f"[SITUATION]\n"
            f"All Gate 1 tests currently PASS, but the test_coverage dimension is FAILING.\n"
            f"Coverage is below the 80% threshold. Two possible root causes:\n"
            f"  A. Existing tests don't cover enough source lines (code coverage < 80%).\n"
            f"  B. Required test functions from TEST_SPEC.md are absent from `{test_file}`.\n\n"
            f"[ACTUAL TOOL OUTPUT — from pre-run]\n"
            f"{tool_snapshot or '(not available)'}\n\n"
            f"[TASK]\n"
            f"1. Run `{_cov_check_cmd}` "
            f"to identify which source lines are not covered (Miss column).\n"
            f"2. Read `02-architecture/TEST_SPEC.md` section for {fr_id} to identify required "
            f"test function names. For each function missing from `{test_file}` — add it.\n"
            f"3. For each uncovered line: decide which approach applies:\n"
            f"   a. Line CAN be reached by a test → add a targeted unit test.\n"
            f"   b. Line is genuinely untestable → apply ESCAPE HATCH (see below).\n"
            f"4. Re-run until coverage reaches ≥ 80%: "
            f"`{_cov_check_cmd}`\n"
            f"5. Commit both `{test_file}` and any source changes from ESCAPE HATCH:\n"
            f"   `git add {src_dir}/ {test_file} && "
            f"git commit -m 'test({fr_id}): add coverage tests and pragma exclusions'`\n\n"
            f"[ESCAPE HATCH — pragma: no cover]\n"
            f"If after adding all reasonable tests coverage is still < 80%, you MAY annotate "
            f"lines in `{src_dir}/` with `# pragma: no cover` ONLY for lines that are "
            f"genuinely impossible or unreasonable to test:\n"
            f"  ✓ Allowed: defensive `raise NotImplementedError` / abstract stubs, "
            f"infrastructure fallback branches (e.g. `except OSError: sys.exit(1)`), "
            f"`if __name__ == '__main__':` blocks, platform-specific dead branches.\n"
            f"  ✗ Not allowed: ordinary business logic, error-handling paths that CAN be "
            f"triggered by passing a bad argument, any line reachable via monkeypatching.\n"
            f"Each `# pragma: no cover` annotation MUST be accompanied by a one-line comment "
            f"explaining WHY it is untestable, e.g.:\n"
            f"  `raise NotImplementedError  # pragma: no cover — abstract base, subclass must implement`\n\n"
            f"[PARTIAL PROGRESS NOTE]\n"
            f"If there are many missing spec tests (>50), add as many as you can and commit.\n"
            f"The meta-loop will re-run if coverage is still insufficient — each session "
            f"reads the test file fresh and picks up where the previous session left off.\n"
            f"Do NOT stop early to 'leave some for next time' — add the maximum you can.\n\n"
            f'[OUTPUT FORMAT]\nReturn JSON: {{"status": "DONE", "coverage_pct": <number>, '
            f'"tests_added": <count>, "pragmas_added": <count>, '
            f'"commit": "<hash>", "summary": "<under 50 chars>"}}'
        )

    if step == "INFRA-FIX":
        # Dispatched when _classify_snapshot_failure returns "INFRA_SKIP":
        # pytest reports N skipped (not failed) because Docker/Redis/external service
        # is unavailable in CI. Coverage is 0 for those paths.
        return (
            f"You are an infrastructure mock fixer for {fr_id}.\n\n"
            f"[FORBIDDEN — read first]\n"
            f"- Deleting or xfail-marking existing tests\n"
            f"- Removing skip markers without providing an alternative that actually runs\n\n"
            f"[SITUATION]\n"
            f"Gate 1 tests are being SKIPPED (not failing) because they depend on external "
            f"infrastructure (Docker, Redis, database, external HTTP) that is unavailable in "
            f"this environment. The skipped tests contribute 0 lines to coverage, causing "
            f"test_coverage to fail.\n\n"
            f"[ACTUAL TOOL OUTPUT — from pre-run]\n"
            f"{tool_snapshot or '(not available)'}\n\n"
            f"[TASK]\n"
            f"1. Identify which tests are skipped and WHY (read the skip condition: "
            f"`python3 -m pytest {test_file} -v --collect-only 2>&1 | grep -i skip`).\n"
            f"2. For each skipped test, choose ONE approach:\n"
            f"   a. ADD a parallel mock-based test that exercises the same logic without "
            f"real infra (e.g. monkeypatch Redis/Docker client). Keep the original skip "
            f"test as-is for integration runs.\n"
            f"   b. If the skipped code path is genuinely untestable without the real service "
            f"AND the source branch is an infrastructure-only fallback: annotate with "
            f"`# pragma: no cover` + reason comment in `{src_dir}/`.\n"
            f"3. Run `python3 -m pytest {test_file} -q` to verify no new failures are introduced.\n"
            f"4. Commit: `git add {src_dir}/ {test_file} && "
            f"git commit -m 'test({fr_id}): add mock tests for infra-skipped paths'`\n\n"
            f'[OUTPUT FORMAT]\nReturn JSON: {{"status": "DONE", "mocks_added": <count>, '
            f'"pragmas_added": <count>, "commit": "<hash>", "summary": "<under 50 chars>"}}'
        )

    if step == "LINT-FIX":
        # Dispatched when _classify_snapshot_failure returns "LINT_FAIL" or "LINT_AND_COVERAGE".
        # LINT_AND_COVERAGE: fix linting only this round; coverage handled next round.
        return (
            f"You are a linting fixer for {fr_id}.\n\n"
            f"[FORBIDDEN — read first]\n"
            f"- Modifying test files in `tests/`\n"
            f"- Suppressing violations with `# noqa` unless the violation is a false positive "
            f"(document why if you use noqa)\n\n"
            f"[SITUATION]\n"
            f"Gate 1 linting dimension is FAILING. Fix ALL ruff violations in `{src_dir}/` "
            f"so `python3 -m ruff check {src_dir}/ --extend-ignore RUF001,RUF002,RUF003` exits 0.\n\n"
            f"[ACTUAL TOOL OUTPUT — from pre-run]\n"
            f"{tool_snapshot or '(not available)'}\n\n"
            f"[TASK]\n"
            f"1. Run `python3 -m ruff check {src_dir}/ --extend-ignore RUF001,RUF002,RUF003 2>&1` to see the full violation list.\n"
            f"2. For N-series violations (naming conventions — N801, N802, N806, N816 etc.):\n"
            f"   - Rename constants/variables to follow PEP 8 naming (UPPER_CASE for module "
            f"constants, UpperCase for classes, lower_case for functions/variables).\n"
            f"   - Update ALL references to each renamed symbol (use `grep -rn '<old_name>'` "
            f"to find them, then rename systematically).\n"
            f"3. For E/W-series violations: fix in-place per ruff's suggestion.\n"
            f"4. Re-run `python3 -m ruff check {src_dir}/ --extend-ignore RUF001,RUF002,RUF003` — it MUST exit 0 before you commit.\n"
            f"5. Run `python3 -m pytest {test_file} -q` to confirm no tests broken by renames.\n"
            f"6. Commit: `git add {src_dir}/ && "
            f"git commit -m 'fix({fr_id}): resolve ruff linting violations'`\n\n"
            f"[NOTE] If BOTH linting AND test_coverage were failing, this session fixes "
            f"linting ONLY. The meta-loop will address coverage in the next round.\n\n"
            f'[OUTPUT FORMAT]\nReturn JSON: {{"status": "DONE", "violations_fixed": <count>, '
            f'"commit": "<hash>", "summary": "<under 50 chars>"}}'
        )

    if step == "CODE-FIX":
        # failing_dims=None means GATE1 timed out / errored before writing a result.
        # In this case we cannot know what failed — emit a diagnostic mode prompt
        # that tells the agent to self-diagnose first, rather than blindly fixing src.
        if failing_dims is None:
            return (
                f"You are a code fixer. Gate 1 for {fr_id} could not complete "
                f"(sub-agent timeout or error — no gate1_result.json was written).\n\n"
                f"[TASK — diagnostic mode]\n"
                f"1. Run `python3 -m pytest tests/ -q` to identify failing / missing tests.\n"
                f"2. Run `python3 -m ruff check {src_dir}/ --extend-ignore RUF001,RUF002,RUF003` to identify lint errors.\n"
                f"3. Based on actual results:\n"
                f"   a. If tests are failing or missing → add/fix tests in `{test_file}` "
                f"AND fix source code in `{src_dir}/` as needed.\n"
                f"   b. If lint errors → fix source code only.\n"
                f"4. Run `python3 -m pytest tests/ -q` to confirm all tests pass.\n"
                f"5. Commit all changed files: "
                f"`git add {src_dir}/ {test_file} && "
                f"git commit -m 'fix({fr_id}): address Gate1 failures'`\n\n"
                f"[FORBIDDEN]\n"
                f"- Deleting or modifying existing passing tests\n"
                f"- app/infrastructure/ paths\n\n"
                f'[OUTPUT FORMAT]\nReturn JSON: {{"status": "DONE", "dims_fixed": [...], '
                f'"commit": "<hash>", "summary": "<under 50 chars>"}}'
            )

        # Classify failing dims so we know what kind of fix is needed.
        _fdims_lower = {str(d).lower() for d in failing_dims}
        _test_cov_failing = "test_coverage" in _fdims_lower
        _src_failing = bool(_fdims_lower - {"test_coverage"})

        dims_str = "\n".join(str(d) for d in failing_dims)

        # ── test_coverage section ─────────────────────────────────────────
        # test_coverage can fail for two distinct reasons:
        #   A. Required test functions are MISSING from the test file.
        #   B. Required test functions EXIST but are FAILING.
        # Use the already-computed spec analysis from the top of this function.
        test_cov_section = ""
        if _test_cov_failing:
            missing_spec = [fn for fn in spec_test_names if fn not in existing_spec_tests]
            present_spec = [fn for fn in spec_test_names if fn in existing_spec_tests]

            parts: list[str] = [
                f"\n[TEST COVERAGE FIX — required for test_coverage dimension]\n"
                f"{spec_summary}\n\n"
            ]

            if missing_spec:
                parts.append(
                    f"MISSING ({len(missing_spec)} tests) — these required tests are NOT in `{test_file}`:\n"
                    + "\n".join(f"  - {fn}" for fn in missing_spec)
                    + "\n  → ADD ALL of them as real, passing tests in THIS session.\n"
                    + "  IMPORTANT: write ALL missing tests in one go — do not stop after 1-2.\n"
                    + "  The agent has enough max_turns (70) to add all remaining tests in one session.\n\n"
                )

            if present_spec:
                parts.append(
                    f"PRESENT but failing ({len(present_spec)} tests) — these tests exist in `{test_file}`:\n"
                    + "\n".join(f"  - {fn}" for fn in present_spec)
                    + "\n  → Run `python3 -m pytest {test_file} -v` and fix each failing test.\n\n"
                )

            if not spec_test_names:
                # No spec info — generic triage instruction
                parts.append(
                    f"Read `02-architecture/TEST_SPEC.md` section for {fr_id} to get\n"
                    "required test function names, then for each:\n"
                    "  - NOT in test file → ADD as a real passing test\n"
                    "  - In test file but FAILING → fix source code or assertion\n\n"
                )

            test_cov_section = "".join(parts)

        # ── TASK steps (built dynamically) ───────────────────────────────
        task_lines = [
            "1. Read `harness/ssi/prompts/evaluate_dimension.md` for each failing dimension's criteria.",
        ]
        n = 2
        if _src_failing:
            task_lines.append(
                f"{n}. Fix source code in `{src_dir}/` to address non-test-coverage failing dimensions."
            )
            n += 1
        if _test_cov_failing:
            task_lines.append(
                f"{n}. Resolve test_coverage failures (see TEST COVERAGE FIX above):\n"
                f"   a. ADD any missing required test functions to `{test_file}`.\n"
                f"   b. For tests that exist but FAIL: fix source code or the failing assertion."
            )
            n += 1
        task_lines.append(f"{n}. Run `python3 -m pytest tests/ -q` to confirm ALL tests pass.")
        n += 1
        git_paths = " ".join(filter(None, [
            f"{src_dir}/" if _src_failing else "",
            test_file if _test_cov_failing else "",
        ]))
        task_lines.append(
            f"{n}. Commit: `git add {git_paths} && "
            f"git commit -m 'fix({fr_id}): address Gate1 failing dims'`"
        )

        # ── FORBIDDEN ────────────────────────────────────────────────────
        if _test_cov_failing:
            # May need to add tests AND fix failing test assertions.
            # Only hard prohibition is deleting tests.
            forbidden = (
                "- Deleting existing tests\n"
                "- Skipping or xfail-marking tests to make them 'pass'\n"
                "- app/infrastructure/ paths"
            )
        else:
            forbidden = (
                "- Modifying test files\n"
                "- app/infrastructure/ paths"
            )

        # test_cov_section ends with \n\n when non-empty, so it provides the gap
        # before [TASK]. When empty, insert the gap explicitly.
        gap = "\n" if not test_cov_section else ""

        # ── Tool snapshot captured at orchestration time (Fix 1) ──
        snapshot_section = ""
        if tool_snapshot:
            snapshot_section = (
                f"\n[ACTUAL TOOL OUTPUT — captured at orchestration time]\n"
                f"Use these exact errors as your fix targets. "
                f"Do NOT re-run the tools to re-discover them — fix what is shown here.\n"
                f"{tool_snapshot}\n\n"
            )

        return (
            f"You are a code fixer. Gate 1 FAILED for {fr_id}. Fix the failing dimensions.\n\n"
            f"[FORBIDDEN — read before anything else]\n"
            f"{forbidden}\n\n"
            f"[FAILING DIMENSIONS]\n"
            f"{dims_str}\n"
            f"{test_cov_section}"
            f"{snapshot_section}"
            f"{gap}"
            f"[TASK]\n"
            + "\n".join(task_lines) + "\n\n"
            + '[OUTPUT FORMAT]\nReturn JSON: {"status": "DONE", "dims_fixed": [...], '
            '"commit": "<hash>", "summary": "<under 50 chars>"}'
        )

    return f"[ERROR] Unknown step: {step}"


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
        choices=["TDD-RED", "TDD-GREEN", "TDD-IMPROVE", "GATE1", "GATE1-DELTA"],
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
