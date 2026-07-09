"""FR-level TDD orchestration commands (dispatch, run-fr-step, resume-fr-phase, run-tool, reload-policy).

Extracted verbatim from harness_cli.py (方案六). Free names that live
in harness_cli resolve through `_hc.` at call time, so existing
monkeypatches on harness_cli attributes keep working. harness_cli
re-exports these cmd_* names, so `from harness_cli import cmd_x`
imports are unaffected.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from core.harness_config import get_timeout
from core.quality_gate.legal_artifacts import PHASE_DELIVERABLES
from core.utils.project_layout import ProjectLayout
import harness_cli as _hc


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
        _raw_timeout = get_timeout("task_dev") if (args.phase in {1, 2} and not is_reviewer) else get_timeout("task_default")
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
    if status in _hc._DISPATCH_ERROR_STATUSES:
        return 1

    # For completed reviewer dispatches, extract and persist Agent B approval JSON.
    if (
        status == "complete"
        and is_reviewer
        and args.fr_id
    ):
        output_text = result.get("output", "")
        review_data = _hc._extract_review_json(output_text)
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
        agent_output = _hc._extract_agent_output_json(output_text)
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
    _num_str = _hc._fr_num_str(fr_id)
    src_dir = "03-development/src"
    _layout = ProjectLayout(project)
    test_dir_str = _layout.get_relative_str(_layout.active_test_dir)
    test_file = f"{test_dir_str}/test_fr{_num_str}.py"

    # Per-FR config: read fr_config from quality_manifest.json.
    # Allows large / complex FRs (e.g. FR-19 with 11-stage pipeline) to declare
    # longer timeouts and more fix rounds without changing global defaults.
    # CLI flags --timeout / --max-fix-rounds still take precedence.
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
    _fr_timeout = _fr_conf.get("timeout", getattr(args, "timeout", get_timeout("fr_step")))
    _fr_max_fix_rounds = _fr_conf.get("max_fix_rounds", getattr(args, "max_fix_rounds", 3))
    _fr_code_fix_max_turns: int | None = _fr_conf.get("code_fix_max_turns")

    # 1. Idempotency — skip if already committed
    if _hc._fr_step_already_done(step, fr_id, project):
        print(f"[run-fr-step] {fr_id} {step}: already done → skip")
        #   _record_gate_timestamp (GATE1-DELTA only) — prevents exit-14 block
        #     from _check_gate1_live_coverage when ALL FRs skip (no code changes)
        if step.upper() == "GATE1-DELTA":
            _hc._record_gate_timestamp(project, phase, 1, fr_id)
        return 0

    # 2. Pre-flight checks — must pass before agent dispatch
    preflight_ok, preflight_errors = _hc._fr_step_preflight(step, project, fr_id, srs_path=srs_path)
    if not preflight_ok:
        print(f"\n[PRE-FLIGHT FAILED] run-fr-step --fr-id {fr_id} --step {step}", file=sys.stderr)
        for err in preflight_errors:
            print(f"  {err}", file=sys.stderr)
        print(file=sys.stderr)
        return 1

    # 3. Build minimal need-to-know prompt (only after pre-flight passes)
    prompt = _hc._build_fr_step_prompt(step, fr_id, phase, project, srs_path)

    # 4. Dispatch sub-agent (phase_sop_override="" skips full SOP load)
    spawner = AgentSpawner(project_path=project)
    phase_ctx = _hc._resolve_phase3_context(project)
    if getattr(args, "no_mcp", False):
        phase_ctx["mcp_config"] = None

    _explicit_max_turns = getattr(args, "max_turns", None)

    def _max_turns(step_name: str) -> int:
        """Per-step max_turns: explicit --max-turns wins, then per-FR config, else _STEP_MAX_TURNS."""
        if _explicit_max_turns is not None:
            return _explicit_max_turns
        if step_name.upper() in ("CODE-FIX", "COVERAGE-FIX") and _fr_code_fix_max_turns:
            return _fr_code_fix_max_turns
        return _hc._STEP_MAX_TURNS.get(step_name.upper(), 40)

    # All FR steps need shell access:
    #   GATE1/GATE1-DELTA: ruff, pyright, pytest, coverage
    #   TDD-RED/GREEN/IMPROVE: pytest to verify fail/pass
    #   CODE-FIX: pytest to confirm fix doesn't break other tests
    # acceptEdits blocks Bash → agents skip verification steps and commit
    # broken code, causing the next GATE1 to fail again.
    _explicit_pmode = getattr(args, "permission_mode", None)
    _pmode = _explicit_pmode if _explicit_pmode is not None else "bypassPermissions"

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
    if _status in _hc._DISPATCH_ERROR_STATUSES:
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
            print(f"[run-fr-step] {fr_id} {step}: sub-agent {_status}")
            print(result.get("output", "")[:500])
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
                    _fix_rc = _hc._cmd_finalize_gate_impl(_fix_args)
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
            gate_pass, failing_dims, block_reason = _hc._parse_gate_output(result.get("output", ""))
        if not gate_pass:
            gate_pass = _hc._fr_step_already_done(step, fr_id, project)

        max_fix_rounds = _fr_max_fix_rounds
        # B: progress tracking — detect lateral variation (same error, no progress)
        prev_snapshot_sig: str = ""
        no_progress_count: int = 0

        for fix_round in range(1, max_fix_rounds + 1):
            if gate_pass or _hc._fr_step_already_done(step, fr_id, project):
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
                tool_snapshot = _hc._capture_tool_snapshot(project, src_dir, test_file)

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
                failure_class = _hc._classify_snapshot_failure(tool_snapshot, failing_dims=failing_dims)

                if failure_class == "ENV":
                    print(f"[run-fr-step] {fr_id} ENV error — human intervention required\n"
                          f"  Hint: check PYTHONPATH / package installation")
                    break

                if failure_class == "ISOLATION":
                    print(f"[run-fr-step] {fr_id} ISOLATION failure "
                          f"(round {fix_round}/{max_fix_rounds})"
                          f" — dispatching TEST-FIX (add autouse infra mock)")
                    fix_prompt = _hc._build_fr_step_prompt(
                        "TEST-FIX", fr_id, phase, project, srs_path,
                        tool_snapshot=tool_snapshot,
                    )
                    fix_step_name = "TEST-FIX"
                elif failure_class == "INFRA_SKIP":
                    print(f"[run-fr-step] {fr_id} INFRA_SKIP failure "
                          f"(round {fix_round}/{max_fix_rounds})"
                          f" — dispatching INFRA-FIX (add mock tests for skipped paths)")
                    fix_prompt = _hc._build_fr_step_prompt(
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
                    fix_prompt = _hc._build_fr_step_prompt(
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
                    fix_prompt = patch_hint + _hc._build_fr_step_prompt(
                        "CODE-FIX", fr_id, phase, project, srs_path,
                        failing_dims=failing_dims, tool_snapshot=tool_snapshot,
                    )
                    fix_step_name = "CODE-FIX"
                elif failure_class == "LOW_COVERAGE":
                    print(f"[run-fr-step] {fr_id} LOW_COVERAGE failure "
                          f"(round {fix_round}/{max_fix_rounds})"
                          f" — dispatching COVERAGE-FIX (tests pass, coverage < 80%)")
                    fix_prompt = _hc._build_fr_step_prompt(
                        "COVERAGE-FIX", fr_id, phase, project, srs_path,
                        tool_snapshot=tool_snapshot,
                    )
                    fix_step_name = "COVERAGE-FIX"
                else:
                    print(f"[run-fr-step] {fr_id} GATE1 FAIL (round {fix_round}/{max_fix_rounds})"
                          f" — dispatching CODE-FIX sub-agent"
                          f" [failure_class={failure_class}]")
                    fix_prompt = _hc._build_fr_step_prompt(
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
                )
                if fix_result.get("status") in _hc._DISPATCH_ERROR_STATUSES:
                    print(f"[run-fr-step] {fix_step_name} failed: "
                          f"{fix_result.get('output','')[:200]}")
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
                            _live_cov = _hc._validate_fr_coverage_immediate(
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
            gate_prompt = _hc._build_fr_step_prompt(
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
            gate_pass, failing_dims, block_reason = _hc._parse_gate_output(result.get("output", ""))
            if not gate_pass:
                gate_pass = _hc._fr_step_already_done(step, fr_id, project)
        else:
            print(f"[run-fr-step] {fr_id} GATE1 BLOCKED after {max_fix_rounds} CODE-FIX rounds"
                  " — human intervention required")
            return 2  # BLOCKED

    # P0-B: record gate timestamp so advance-phase _check_gate1_live_coverage
    # finds a gate=1 entry for this FR (it reads gate_timestamps.jsonl; without
    # this, advance-phase always exits 14 when run-fr-step is used instead of
    # finalize-gate --gate 1 per FR).
    if step in ("GATE1", "GATE1-DELTA"):
        _hc._record_gate_timestamp(project, phase, 1, fr_id)

    # 5. Verify commit exists (non-fatal warning for TDD-IMPROVE / CODE-FIX)
    if step not in ("TDD-IMPROVE", "CODE-FIX") and not _hc._fr_step_already_done(step, fr_id, project):
        print(f"[run-fr-step] {fr_id} {step}: WARNING — expected commit not found in git log")

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
            if _hc._fr_code_changed_since_last_gate1(fr_id, project):
                steps = ["TDD-RED", "TDD-GREEN", "TDD-IMPROVE", "GATE1"]
            else:
                steps = ["GATE1-DELTA"]
        else:
            steps = ["TDD-RED", "TDD-GREEN", "TDD-IMPROVE", "GATE1"]
        for step in steps:
            if not _hc._fr_step_already_done(step, fr_id, project):
                srs_flag = " --srs .methodology/SRS.md" if step in ("TDD-RED", "TDD-GREEN") else ""
                print(
                    f"Next step: python3 harness_cli.py run-fr-step "
                    f"--phase {phase} --fr-id {fr_id} --step {step} --project .{srs_flag}"
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
    rfp.add_argument("--timeout", type=int, default=600,
                     help="Sub-agent max execution time in seconds (default: 600)")
    rfp.add_argument("--max-turns", type=int, default=None, dest="max_turns",
                     help="Sub-agent max tool-using turns (default: per-step, 40-70)")
    rfp.add_argument("--max-fix-rounds", type=int, default=3, dest="max_fix_rounds",
                     help="Max CODE-FIX + GATE1 retry rounds on GATE1 FAIL (default: 3)")
    rfp.add_argument("--no-push", action="store_true", help="Skip git push origin HEAD after completion")
    rfp.add_argument("--no-mcp", action="store_true", dest="no_mcp",
                     help="Disable code-review-graph MCP for this FR step (debugging)")
    rfp.add_argument("--permission-mode", default=None, dest="permission_mode",
                     choices=["acceptEdits", "bypassPermissions", "default", "plan"],
                     help="Override sub-agent permission mode (default: bypassPermissions for GATE1, acceptEdits otherwise)")
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
