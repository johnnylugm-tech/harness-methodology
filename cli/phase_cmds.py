"""Phase lifecycle commands (plan-phase, plan-all, run-phase, pre-commit-check, advance-phase, generate-next-plan, validate-handoff, sync-harness).

Extracted verbatim from harness_cli.py (方案六). Free names that live
in harness_cli resolve through `_hc.` at call time, so existing
monkeypatches on harness_cli attributes keep working. harness_cli
re-exports these cmd_* names, so `from harness_cli import cmd_x`
imports are unaffected.
"""

from __future__ import annotations

import harness_cli as _hc


def cmd_plan_phase(args: _hc.argparse.Namespace) -> int:
    """Generate phase execution plan from SRS/SAD artifacts."""
    from scripts.generate_full_plan import generate_full_plan

    repo_path = _hc.Path(args.project).resolve()
    output_path = _hc.Path(args.output) if args.output else None

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


def cmd_plan_all(args: _hc.argparse.Namespace) -> int:
    """Generate all 8 phase plans in dynamic mode at project start."""
    from scripts.generate_full_plan import generate_full_plan

    project = _hc.Path(args.project).resolve()
    out_dir = _hc.Path(args.output_dir) if args.output_dir else project / ".methodology"

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
            _hc.json.loads(_manifest.read_text(encoding="utf-8"))
            _manifest_usable = True
        except (OSError, _hc.json.JSONDecodeError):
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
    for phase_num in _hc.VALID_PHASES:
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
        status_lines.append(f"| {phase_num} | {status} | {_hc.Path(path).name} |")
    status_lines.append("")
    status_path.write_text("\n".join(status_lines), encoding="utf-8")
    print(f"\nplan_status.md → {status_path}")

    failed = [p for p, s, _ in results if s == "FAIL"]
    if failed:
        print(f"[ERROR] Failed phases: {failed}")
        return 1
    return 0


def cmd_run_phase(args: _hc.argparse.Namespace) -> int:
    """OTEL span wrapper for run-phase. Business logic in _cmd_run_phase_impl."""
    try:
        from core.observability import init_tracer
        _tracer = init_tracer(_hc.Path(args.project).resolve())
    except Exception:
        _tracer = None
    if _tracer is None:
        return _hc._cmd_run_phase_impl(args)
    with _tracer.start_as_current_span("run_phase") as _span:
        _span.set_attribute("harness.phase", args.phase)
        _exit = _hc._cmd_run_phase_impl(args)
        _span.set_attribute("harness.exit_code", _exit)
        _span.set_attribute("harness.blocked", _exit != 0)
        return _exit


def cmd_pre_commit_check(args: _hc.argparse.Namespace) -> int:
    """Lightweight pre-commit hook check (FSM + kill-switch only).

    Intended exclusively for git commit hooks where speed matters.
    Skips constitution (verified at advance-phase postflight), drift,
    traceability, gap analysis, and CI readiness — those are
    enforced by run-phase / finalize-gate.

    Do NOT use this command in pipelines or as a substitute for run-phase.
    """
    from core.phase_hooks import PhaseHooks

    project = _hc.Path(args.project).resolve()
    hooks = PhaseHooks(str(project), phase=args.phase)

    print(f"\n{'='*60}\npre-commit-check: Phase {args.phase}\n{'='*60}")

    entry_gate = _hc._verify_entry_gate(project, args.phase)
    if not entry_gate["passed"]:
        print(f"\n[ENTRY GATE FAILED] {entry_gate['gate']} — {entry_gate['reason']}")
        return 10
    print(f"\n[ENTRY GATE] {entry_gate['gate']}: {entry_gate['reason']}")

    pre = _hc._run_fast_preflight(hooks)
    if not pre["all_passed"]:
        print(f"\nPRE-FLIGHT FAILED: {pre['details']}")
        return 1

    print("\n[INFO] Fast preflight passed (FSM + constitution + kill-switch).")
    print("[INFO] Full enforcement (drift, traceability) runs at run-phase / finalize-gate.")

    print("[INFO] Skipped: drift, traceability, gap analysis, CI readiness.")
    print("[INFO] Next steps:")
    return 0


def cmd_advance_phase(args: _hc.argparse.Namespace) -> int:
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
    _saved_cwd = _hc.os.getcwd()
    project = _hc.Path(args.project).resolve()

    # Phase 9 (Maintenance) is a terminal steady state: work happens as
    # re-entrant CR tickets (cr-open/cr-close), never as a phase exit.
    if args.completed_phase >= 9:
        print(
            "\n[BLOCKED] advance-phase: Phase 9 (Maintenance) is a terminal "
            "steady state — there is no Phase 10.\n"
            "  Maintenance work is ticket-driven and re-entrant:\n"
            "    python3 harness_cli.py cr-open --type bug|feat --title ... --project .\n"
            "    python3 harness_cli.py cr-close --cr CR-NN --project .",
            file=_hc.sys.stderr,
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
            with _hc.file_lock(_hc.state_lock_path(project)):
                _state = _hc.json.loads(state_path.read_text(encoding="utf-8"))
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
                rc = _hc._advance_prechecks(project, args.completed_phase)
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
                    file=_hc.sys.stderr,
                )
                return 2
            # Check phase_truth_passed for phases with exit gates
            if args.completed_phase in _hc._PHASE_EXIT_GATES:
                _req_gate = _hc._PHASE_EXIT_GATES[args.completed_phase]
                _passed = _state.get("phase_truth_passed")
                _last_gate = _state.get("last_gate")
                # P5-BUG-02 defense: Ensure both phase_truth_passed and the last_gate match the exit gate
                if not _passed or _last_gate != _req_gate:
                    print(
                        f"\n[BLOCKED] advance-phase: phase_truth_passed not recorded "
                        f"in state.json for Phase {args.completed_phase}.\n"
                        f"  Run: python harness_cli.py finalize-gate "
                        f"--gate {_hc._PHASE_EXIT_GATES[args.completed_phase]} "
                        f"--phase {args.completed_phase} --project {project}\n"
                        f"  and ensure Phase Truth ≥ 90% before advancing.",
                        file=_hc.sys.stderr,
                    )
                    # Exit 12 = phase_truth_passed missing in state.json.
                    # Distinct from exit 11 (Phase Truth score < 90%) so pipeline
                    # automation and humans can apply the correct remediation:
                    #   11 → re-run Phase Truth until score ≥ 90%
                    #   12 → run finalize-gate for the exit gate of this phase
                    return 12
        except (ValueError, OSError, _hc.json.JSONDecodeError) as exc:
            print(
                f"  [WARN] Could not read state.json::current_phase for validation: {exc} — proceeding.",
                file=_hc.sys.stderr,
            )

    next_phase = args.completed_phase + 1

    # Look up gate/FR state from quality_manifest.json for accurate state.json
    manifest_path = project / ".methodology" / "quality_manifest.json"
    manifest = {}
    last_gate_num = None
    last_fr_id = None
    if manifest_path.exists():
        try:
            manifest = _hc.json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception:  # pylint: disable=broad-exception-caught
            pass

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
    rc = _hc._advance_prechecks(project, args.completed_phase)
    if rc != 0:
        return rc

    print(f"\n[advance-phase] Completed phase {args.completed_phase} → advancing to {next_phase}")
    _hc._advance_fsm(project, args.completed_phase,
                 last_gate=last_gate_num, last_fr=last_fr_id)
    _hc._update_claude_md(project)               # phase number just changed → refresh CLAUDE.md
    _hc._llm_clean_stale_claude_md(project)      # remove stale manual harness status text

    # Generate CRG wiki on P3+ advance (architecture docs for agents, incremental).
    # Driven via the code-review-graph CLI so it works in any environment — the old
    # mcp_tools import only existed inside interactive Claude Code and silently no-op'd,
    # so .code-review-graph/wiki/ was never produced.
    if args.completed_phase >= 2:
        _crg_bin = _hc.shutil.which("code-review-graph")
        if _crg_bin:
            try:
                _hc.subprocess.run(
                    [_crg_bin, "wiki", "--repo", str(project)],
                    check=True, capture_output=True, text=True, timeout=_hc.get_timeout("subprocess"),
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
    _sentinels_backup: _hc.Optional[_hc.Path] = None
    # Bug H1 fix: wrap backup→rm→restore in try/finally so the temp dir is
    # cleaned up even if shutil.rmtree / copytree raises a non-OSError
    # (KeyboardInterrupt, RuntimeError, etc.) that ignore_errors won't swallow.
    try:
        if sentinels_dir.is_dir():
            _sentinels_backup = _hc.Path(_hc.tempfile.mkdtemp(prefix="harness-sentinels-"))
            _hc.shutil.copytree(sentinels_dir, _sentinels_backup / "sentinels")
        if sessi_work.is_dir():
            _hc.shutil.rmtree(sessi_work, ignore_errors=True)
            print(f"  [advance-phase] Cleared stale {sessi_work}")
        if _sentinels_backup is not None:
            sentinels_dir.mkdir(parents=True, exist_ok=True)
            _hc.shutil.copytree(_sentinels_backup / "sentinels", sentinels_dir, dirs_exist_ok=True)
            _n = sum(1 for _ in sentinels_dir.iterdir() if _.is_file())
            print(f"  [advance-phase] Preserved {_n} sentinel(s) under {sentinels_dir}")
    finally:
        if _sentinels_backup is not None:
            _hc.shutil.rmtree(_sentinels_backup, ignore_errors=True)

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
        sad_path = _hc.ProjectLayout(project).sad_path
        if sad_path.exists():
            try:
                from harness.harness_bridge import HarnessBridge
                # Reuse fr_ids from current manifest, fall back to SRS.md scan
                _mf_path = project / ".methodology" / "quality_manifest.json"
                _fr_ids: list[str] = []
                if _mf_path.exists():
                    try:
                        _fr_ids = _hc.json.loads(
                            _mf_path.read_text(encoding="utf-8")
                        ).get("fr_ids", [])
                    except Exception:  # pylint: disable=broad-exception-caught
                        pass
                if not _fr_ids:
                    # Fallback: scan SRS.md for FR markers. Match "### FR-XX" headers
                    # (separator can be `:`, `—`, `-`, `|`, or whitespace after the
                    # number) and table rows "| FR-XX | ...". Previous regex required
                    # `\s*:|\s*|` after the digits, which silently dropped SRS files
                    # using em-dash (`### FR-01 — ...`) — leaving fr_ids empty and
                    # tripping the manifest-integrity pre-flight (Bug #140).
                    import re as _re_fr
                    _srs = _hc.ProjectLayout(project).srs_path
                    if _srs.exists():
                        _fr_ids = [
                            f"FR-{n}" for n in _re_fr.findall(
                                r"^(?:###\s+FR-|\|\s*FR-)(\d+)\b",
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
                        f"by `^(?:###\\s+FR-|\\|\\s*FR-)(\\d+)\\b`",
                        file=_hc.sys.stderr,
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
                    file=_hc.sys.stderr,
                )
        else:
            print(
                f"  [P2→P3] {sad_path} not found — manifest regeneration skipped.\n"
                f"    P3 entry will use the existing manifest. Create SAD.md and run:\n"
                f"    python3 harness_cli.py manifest --fr-ids FR-XX [...] --sad {sad_path}",
                file=_hc.sys.stderr,
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
                file=_hc.sys.stderr,
            )

    gen = _hc.HandoverGenerator(project)
    gen.write(
        checkpoint_id=f"P{next_phase}-entry-{_hc.datetime.now(_hc.timezone.utc).strftime('%Y%m%d')}",
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
    if _hc.os.environ.get("HARNESS_NO_GIT"):
        print("[advance-phase] HARNESS_NO_GIT=1 — skipping git commit")
    else:
        # Fix Finding #3: include regenerated quality_manifest.json in commit when
        # P2→P3 just regenerated it, so the advance commit captures the fresh data
        # atomically (state.json + manifest). Without this, the regenerated file
        # would only land in the next push, leaving a window where CI sees stale
        # manifest.
        _add_targets = _hc._advance_commit_targets(
            args.completed_phase, next_phase, _manifest_regenerated,
            (project / ".methodology" / "fr_progress.json").exists(),
            (project / ".methodology" / "gate_timestamps.jsonl").exists(),
            (project / "00-summary" / f"Phase{args.completed_phase}_STAGE_PASS.md").exists(),
        )
        add_result = _hc.subprocess.run(
            ["git", "-C", str(project), "add", *_add_targets],
            capture_output=True, text=True,
        )
        if add_result.returncode != 0:
            print(f"[advance-phase] WARN: git add failed — {add_result.stderr.strip()}")
        else:
            commit_result = _hc.subprocess.run(
                ["git", "-C", str(project), "commit", "-m",
                 f"handover: advance to Phase {next_phase}"],
                capture_output=True, text=True,
            )
            if commit_result.returncode == 0:
                print("[advance-phase] Committed HANDOVER.md + state.json locally.")
            elif "nothing to commit" in (commit_result.stdout + commit_result.stderr):
                print("[advance-phase] Nothing to commit (already clean).")
            else:
                print(f"[advance-phase] WARN: git commit failed — {commit_result.stderr.strip()}")

    print(f"[advance-phase] Done — local hooks and CI now target phase {next_phase}")
    # Restore CWD if any internal Python code (hook, library) changed it.
    # Subprocess calls do NOT change the parent process CWD.
    try:
        if _hc.os.getcwd() != _saved_cwd:
            _hc.os.chdir(_saved_cwd)
            print(f"[advance-phase] CWD restored to {_saved_cwd}")
    except OSError:
        pass
    return 0


def cmd_generate_next_plan(args: _hc.argparse.Namespace) -> int:
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
    project = _hc.Path(getattr(args, "project", ".")).resolve()
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
            state = _hc.json.loads(state_path.read_text(encoding="utf-8"))
            current_phase = phase_hint or int(state.get("current_phase", 3))
            last_gate = state.get("last_gate")
            last_fr = state.get("last_fr")
        except Exception:  # pylint: disable=broad-exception-caught
            pass

    print(f"\nPhase      : {current_phase} ({_hc._topology_phase_name(current_phase, default='?')})")

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

    manifest = _hc.json.loads(manifest_path.read_text(encoding="utf-8"))
    fr_ids: list[str] = manifest.get("fr_ids", [])
    gate_results: dict = manifest.get("gate_results", {})
    gate1_results: dict = gate_results.get("gate1", {})

    # ── Build ordered checkpoint list for current phase ──────────────────────
    # Each entry: (label, is_complete_fn)
    checkpoints: list[tuple[str, bool]] = []

    if current_phase in _hc._PER_FR_GATE1_PHASES:
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

    if current_phase in _hc._PHASE_EXIT_GATES:
        gate_num = _hc._PHASE_EXIT_GATES[current_phase]
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
        m = _hc.re.search(r"Gate (\d+)", next_label)
        if m:
            g = m.group(1)
            print(f"\n  Quick-start Gate {g}:")
            print(f"  python harness_cli.py run-gate --gate {g} "
                  f"--phase {current_phase} --project {project}")

    print(f"\n{'='*W}")
    return 0


def cmd_validate_handoff(args: _hc.argparse.Namespace) -> int:
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
    project = _hc.Path(args.project).resolve()
    from_phase = args.from_phase
    errors = _hc._validate_handoff(project, from_phase)
    if not errors:
        print(f"[validate-handoff] P{from_phase} → P{from_phase + 1}: OK")
        return 0
    print(f"[validate-handoff] P{from_phase} → P{from_phase + 1}: BLOCKED")
    for e in errors:
        print(f"  • {e}")
    return 1


def cmd_sync_harness(args: _hc.argparse.Namespace) -> int:
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
    project = _hc.Path(getattr(args, "project", "."))
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
        print(f"[sync-harness] FAILED: {e}", file=_hc.sys.stderr)
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
    rp.set_defaults(func=cmd_run_phase)

    # pre-commit-check (git commit hook only — FSM + constitution + kill-switch)
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
