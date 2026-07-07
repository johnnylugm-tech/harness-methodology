"""Checkpoint/milestone push commands (push-checkpoint, push-milestone).

Extracted verbatim from harness_cli.py (方案六). Free names that live
in harness_cli resolve through `_hc.` at call time, so existing
monkeypatches on harness_cli attributes keep working. harness_cli
re-exports these cmd_* names, so `from harness_cli import cmd_x`
imports are unaffected.
"""

from __future__ import annotations

import harness_cli as _hc


def cmd_push_checkpoint(args: _hc.argparse.Namespace) -> int:
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
    project = _hc.Path(args.project).resolve()
    fr_ids = [f.strip() for f in args.fr_ids.split(",") if f.strip()]
    # Note: if fr_ids is empty here, GitStrategy.commit_and_push_p1/p2 will
    # auto-detect from SRS.md — no need to block here.

    git = _hc._make_git(args, project)
    git.ensure_gitignore()
    phase = args.phase
    if phase not in (1, 2):
        print(f"[ERROR] push-checkpoint only supports P1/P2 (got phase {phase}).")
        return 1

    # F-2.5-style refresh: deliverables (SAD.md/ADR.md/TEST_SPEC.md, tests) may
    # have been written since attestation.json was last built, which would fail
    # the `_trace_dirty_state` pre-commit probe mid-push. Refresh in-place
    # before the commit/push flow triggers the hook — mirrors the auto-fix
    # in-place refresh pattern in phase_hooks.py (F-2.5, fd174bf).
    try:
        from scripts.build_trace_attestation import build_attestation, write_attestation
        _att = build_attestation(project)
        write_attestation(project, _att)
    except Exception as _att_err:  # pylint: disable=broad-exception-caught
        print(f"  [WARN] attestation pre-refresh failed: {_att_err}")

    # Bug fix (P8 E2E 2026-07-04): write push-checkpoint sentinel + phase_completed
    # to state.json BEFORE commit_and_push_p1/p2 so the audit fields land in the
    # pushed commit. See plan: ~/.claude/plans/abundant-stargazing-hejlsberg.md
    #
    # SHA captured BEFORE push: pre-push HEAD is parent of the new commit. CI
    # uses `phase_completed[N].sha` only for `git merge-base --is-ancestor`
    # (harness_cli.py:1583-1596), and the pre-push HEAD satisfies that check.
    #
    # Revert-on-failure: _verify_entry_gate reads state.json's live working-tree
    # content directly (not git history), so if commit_and_push_p1/p2 fails, the
    # optimistic write above must be undone — otherwise a local push failure still
    # lets advance-phase proceed as if P{phase} had been pushed.
    state_path = project / ".methodology" / "state.json"
    _pre_push_sha = ""
    _prev_last_push_checkpoint = None
    _prev_last_push_checkpoint_phase = None
    _prev_phase_completed_entry = None
    _wrote_checkpoint_state = False
    if state_path.exists():
        import subprocess as _sp
        try:
            _run_res = _sp.run(
                ["git", "-C", str(project), "rev-parse", "HEAD"],
                capture_output=True, text=True, timeout=10, check=True
            )
            _pre_push_sha = _run_res.stdout.strip()
            if not _pre_push_sha:
                print("  [ERROR] git rev-parse HEAD returned empty SHA. Aborting push.")
                return 1
        except Exception as _sha_err:  # pylint: disable=broad-exception-caught
            print(f"  [ERROR] Failed to resolve pre-push HEAD SHA: {_sha_err}. Aborting push.")
            return 1
        try:
            with _hc.file_lock(_hc.state_lock_path(project)):
                _state_data = _hc.json.loads(state_path.read_text(encoding="utf-8"))
                _prev_last_push_checkpoint = _state_data.get("last_push_checkpoint")
                _prev_last_push_checkpoint_phase = _state_data.get("last_push_checkpoint_phase")
                _prev_phase_completed_entry = _state_data.get("phase_completed", {}).get(str(phase))
                _state_data["last_push_checkpoint"] = _hc.datetime.now(_hc.timezone.utc).isoformat()
                _state_data["last_push_checkpoint_phase"] = phase
                _state_data.setdefault("phase_completed", {})[str(phase)] = {
                    "sha": _pre_push_sha,
                    "timestamp": _hc.datetime.now(_hc.timezone.utc).isoformat(),
                }
                _hc.atomic_write_json(state_path, _state_data)
                _wrote_checkpoint_state = True
        except Exception as _state_err:  # pylint: disable=broad-exception-caught
            print(f"  [WARN] Could not write push-checkpoint sentinel to state.json: {_state_err}")

    if phase == 1:
        ok = git.commit_and_push_p1(
            fr_ids=fr_ids,
            background=f"P{phase} phase completed — pushed for record.",
            notes=["Phase checkpoint push"],
        )
    else:
        ok = git.commit_and_push_p2(
            fr_ids=fr_ids,
            background=f"P{phase} phase completed — pushed for record.",
            notes=["Phase checkpoint push"],
        )

    if not ok and _wrote_checkpoint_state:
        try:
            with _hc.file_lock(_hc.state_lock_path(project)):
                _state_data = _hc.json.loads(state_path.read_text(encoding="utf-8"))
                if _prev_last_push_checkpoint is None:
                    _state_data.pop("last_push_checkpoint", None)
                else:
                    _state_data["last_push_checkpoint"] = _prev_last_push_checkpoint
                if _prev_last_push_checkpoint_phase is None:
                    _state_data.pop("last_push_checkpoint_phase", None)
                else:
                    _state_data["last_push_checkpoint_phase"] = _prev_last_push_checkpoint_phase
                if _prev_phase_completed_entry is None:
                    _state_data.get("phase_completed", {}).pop(str(phase), None)
                else:
                    _state_data.setdefault("phase_completed", {})[str(phase)] = _prev_phase_completed_entry
                _hc.atomic_write_json(state_path, _state_data)
        except Exception as _revert_err:  # pylint: disable=broad-exception-caught
            print(f"  [WARN] Could not revert push-checkpoint sentinel after push failure: {_revert_err}")

    if ok:
        # Post-push self-check: warn loudly on dirty residue. Push itself
        # succeeded — the dirt is post-commit residue. Don't fail-fast.
        _dirty = _hc._post_push_self_check(project)
        if _dirty:
            print(
                f"  [WARN] post-push dirty tree ({len(_dirty)} path(s)):\n"
                + "\n".join(f"    • {p}" for p in _dirty[:10])
                + (f"\n    ... and {len(_dirty) - 10} more" if len(_dirty) > 10 else "")
            )
        handover = project / "HANDOVER.md"
        if handover.exists():
            print(f"  HANDOVER.md → {handover}")
        print("  [git] pushed → remote ✓")
        # Next-step hint — push-checkpoint records phase_completed[N] but does NOT
        # update current_phase. Hooks and CI continue to read the same phase until
        # advance-phase is called explicitly. Keeps phase transitions atomic.
        _next = phase + 1
        print(
            f"\n  Next: advance to Phase {_next} when ready:\n"
            f"    python3 harness_cli.py advance-phase --phase {_next} --project {project}"
        )
    return 0 if ok else 1


def cmd_push_milestone(args: _hc.argparse.Namespace) -> int:
    """Push milestone checkpoint with HANDOVER.md generation.

    Milestone pushes are the crash-recovery points for P3+:
      p3-mid      — ≥50% FRs have Gate 1 PASS (PUSH ③)
      p3-pre-gate2  — all FRs Gate 1 PASS, before Gate 2 (PUSH ④)
      p3-post-gate2 — Gate 2 PASS, all FRs Gate 1 PASS, before P4 (PUSH ⑤; v2.9.1 B.2)
      p4-mid      — ≥50% FRs Gate 1 re-eval PASS (PUSH ③ P4 variant)
      p4-pre-gate3  — all FRs Gate 1 re-eval PASS, before Gate 3 (PUSH ④ P4 variant)
      p5-baseline — BASELINE.md generated (PUSH ⑦)
      p7          — risk register complete (PUSH ⑨)
      p8          — config records complete (PUSH ⑩)
      cr-close    — P9 maintenance: per-CR closure push (re-entrant, requires --cr)

    Usage:
      python harness_cli.py push-milestone --type p3-mid --project . --fr-done 3 --fr-total 6 --fr-ids FR-01,FR-02,FR-03
      python harness_cli.py push-milestone --type p3-pre-gate2 --project . --fr-ids FR-01,FR-02,FR-03
      python harness_cli.py push-milestone --type p3-post-gate2 --project . --fr-ids FR-01,FR-02,FR-03
      python harness_cli.py push-milestone --type p5-baseline --project .
    """
    project = _hc.Path(args.project).resolve()
    git = _hc._make_git(args, project)
    git.ensure_gitignore()
    if getattr(args, "dry_run", False):
        print(f"[dry-run] push-milestone --type {args.type} would: write HANDOVER.md + "
              f"commit + push to origin (no changes made; Bug #112 safety flag)")
        return 0
    # F-2.5-style refresh: deliverables may have been written since
    # attestation.json was last built, which would fail the `_trace_dirty_state`
    # pre-commit probe mid-push. Mirrors the same pre-refresh block in
    # cmd_push_checkpoint (push_cmds.py:40-50) so every push path is symmetric.
    try:
        from scripts.build_trace_attestation import build_attestation, write_attestation
        _att = build_attestation(project)
        write_attestation(project, _att)
    except Exception as _att_err:  # pylint: disable=broad-exception-caught
        print(f"  [WARN] attestation pre-refresh failed: {_att_err}")
    milestone_type = args.type
    fr_ids = [f.strip() for f in args.fr_ids.split(",") if f.strip()]

    ok = False
    # Auto-populate fr_ids from manifest when not provided
    if not fr_ids:
        manifest_path = project / ".methodology" / "quality_manifest.json"
        if manifest_path.exists():
            try:
                _mf = _hc.json.loads(manifest_path.read_text(encoding="utf-8"))
                fr_ids = _mf.get("fr_ids", [])
            except Exception:  # pylint: disable=broad-exception-caught
                pass

    # Entry-gate evidence BEFORE any side effect (E2E round 2 C-1/C-2:
    # p5-baseline and p7 pushed fake milestones with no gate evidence; even
    # the failing path left a milestone commit behind). Fail-closed: a
    # missing or unreadable manifest is absence of evidence, not permission.
    _MILESTONE_ENTRY_GATES = {
        "p5-baseline": _hc._ENTRY_GATE_MAP[5],
        "p7": _hc._ENTRY_GATE_MAP[7],
        "p8": _hc._ENTRY_GATE_MAP[8],
    }
    _required_gate = _MILESTONE_ENTRY_GATES.get(milestone_type)
    if _required_gate is not None:
        _gate_rec: dict = {}
        try:
            _mf_gate = _hc.json.loads(
                (project / ".methodology" / "quality_manifest.json").read_text(encoding="utf-8")
            )
            _gate_rec = (_mf_gate.get("gate_results") or {}).get(f"gate{_required_gate}") or {}
        except (FileNotFoundError, _hc.json.JSONDecodeError, OSError):
            _gate_rec = {}
        if not _gate_rec.get("quality_complete"):
            print(
                f"[BLOCKED] push-milestone --type {milestone_type}: entry gate "
                f"Gate {_required_gate} has no PASS evidence "
                f"(gate_results.gate{_required_gate}.quality_complete is not True "
                "in .methodology/quality_manifest.json).\n"
                f"  Run the Gate {_required_gate} evaluation and finalize-gate first."
            )
            return 2

    # Bug fix (P8 E2E 2026-07-04): write last_milestone_command + last_milestone_at
    # to state.json BEFORE commit_and_push_* so the audit fields land in the
    # pushed commit. See plan: ~/.claude/plans/abundant-stargazing-hejlsberg.md
    #
    # Revert-on-failure: ci_state_helper.cmd_is_p8 trusts last_milestone_command
    # alone (no success flag), so any exit below this point that didn't actually
    # push (validation failure or commit_and_push_* returning False) must restore
    # these fields — otherwise a failed/blocked milestone still reads as pushed.
    state_path = project / ".methodology" / "state.json"
    _prev_last_milestone_command = None
    _prev_last_milestone_at = None
    _wrote_milestone_state = False
    if state_path.exists():
        try:
            with _hc.file_lock(_hc.state_lock_path(project)):
                _state_data = _hc.json.loads(state_path.read_text(encoding="utf-8"))
                _prev_last_milestone_command = _state_data.get("last_milestone_command")
                _prev_last_milestone_at = _state_data.get("last_milestone_at")
                _state_data["last_milestone_command"] = f"push-milestone --type {milestone_type}"
                _state_data["last_milestone_at"] = _hc.datetime.now(_hc.timezone.utc).isoformat()
                _hc.atomic_write_json(state_path, _state_data)
                _wrote_milestone_state = True
        except Exception as _state_err:  # pylint: disable=broad-exception-caught
            print(
                f"\n  [WARN] Could not write last_milestone_command to state.json: {_state_err}"
            )

    def _revert_milestone_audit_write() -> None:
        if not _wrote_milestone_state:
            return
        try:
            with _hc.file_lock(_hc.state_lock_path(project)):
                _sd = _hc.json.loads(state_path.read_text(encoding="utf-8"))
                if _prev_last_milestone_command is None:
                    _sd.pop("last_milestone_command", None)
                else:
                    _sd["last_milestone_command"] = _prev_last_milestone_command
                if _prev_last_milestone_at is None:
                    _sd.pop("last_milestone_at", None)
                else:
                    _sd["last_milestone_at"] = _prev_last_milestone_at
                _hc.atomic_write_json(state_path, _sd)
        except Exception as _revert_err:  # pylint: disable=broad-exception-caught
            print(f"  [WARN] Could not revert stale milestone audit fields: {_revert_err}")

    if milestone_type == "p3-mid":
        fr_done = args.fr_done
        fr_total = args.fr_total
        if fr_done is None or fr_total is None or fr_total == 0:
            print("[ERROR] --fr-done and --fr-total required for p3-mid (fr-total must be >0)")
            _revert_milestone_audit_write()
            return 1
        ok = git.commit_and_push_p3_mid(fr_done, fr_total, fr_ids)
    elif milestone_type == "p3-pre-gate2":
        ok = git.commit_and_push_p3_pre_gate2(fr_ids)
    elif milestone_type == "p3-post-gate2":
        # v2.9.1 B.2: validate Gate 2 PASS + all FRs Gate 1 PASS as precondition
        _pre = _hc._validate_p3_post_gate2_precondition(project, fr_ids)
        if _pre:
            print("[ERROR] p3-post-gate2 blocked — pre-flight checks failed:")
            for _e in _pre:
                print(f"  • {_e}")
            _revert_milestone_audit_write()
            return 1
        ok = git.commit_and_push_p3_post_gate2(fr_ids)
    elif milestone_type == "p4-mid":
        fr_done = args.fr_done
        fr_total = args.fr_total
        if fr_done is None or fr_total is None or fr_total == 0:
            print("[ERROR] --fr-done and --fr-total required for p4-mid (fr-total must be >0)")
            _revert_milestone_audit_write()
            return 1
        ok = git.commit_and_push_p4_mid(fr_done, fr_total, fr_ids)
    elif milestone_type == "p4-pre-gate3":
        ok = git.commit_and_push_p4_pre_gate3(fr_ids)
    elif milestone_type == "p5-baseline":
        ok = git.commit_and_push_p5_baseline()
    elif milestone_type == "p7":
        ok = git.commit_and_push_p7()
    elif milestone_type == "p8":
        p8_errors = _hc._validate_p8_completion(project)
        if p8_errors:
            print("[ERROR] P8 push blocked — pre-flight checks failed:")
            for e in p8_errors:
                print(f"  • {e}")
            _revert_milestone_audit_write()
            return 1
        ok = git.commit_and_push_p8()
    elif milestone_type == "cr-close":
        # P9 maintenance: one push per closed CR. Precondition: the CR must
        # actually be CLOSED (cr-close ran its full re-entry checklist).
        _cr_id = getattr(args, "cr", None)
        if not _cr_id:
            print("[ERROR] --cr CR-NN required for cr-close milestone")
            _revert_milestone_audit_write()
            return 1
        from core.maintenance import CRManager, CRValidationError
        try:
            _cr = CRManager(project).load(_cr_id)
        except CRValidationError as _cr_err:
            print(f"[ERROR] {_cr_err}")
            _revert_milestone_audit_write()
            return 1
        if _cr.get("status") != "CLOSED":
            print(f"[ERROR] {_cr['id']} is {_cr.get('status')!r} — run cr-close first "
                  f"(only CLOSED CRs get a milestone push)")
            _revert_milestone_audit_write()
            return 1
        ok = git.commit_and_push_cr_close(_cr["id"], _cr.get("title", ""))
    else:
        print(f"[ERROR] Unknown milestone type: {milestone_type}")
        _revert_milestone_audit_write()
        return 1

    if not ok:
        _revert_milestone_audit_write()

    if ok:
        # Post-push self-check: warn loudly on dirty residue. The push itself
        # succeeded — the dirt is post-commit residue. Don't fail-fast.
        _dirty = _hc._post_push_self_check(project)
        if _dirty:
            print(
                f"  [WARN] post-push dirty tree ({len(_dirty)} path(s)):\n"
                + "\n".join(f"    • {p}" for p in _dirty[:10])
                + (f"\n    ... and {len(_dirty) - 10} more" if len(_dirty) > 10 else "")
            )
        handover = project / "HANDOVER.md"
        if handover.exists():
            print(f"  HANDOVER.md → {handover}")
        print(f"  [git] milestone {milestone_type} pushed → remote ✓")
    return 0 if ok else 1


def register(sub) -> None:
    """Wire this family's parsers onto the main subparser action."""
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

    # (ci-ack removed with preflight_ci_readiness — the advisory it silenced
    #  no longer runs; 減法 T2.)

    # push-milestone (P3+ milestone push + HANDOVER.md)
    pm = sub.add_parser(
        "push-milestone",
        help="Push milestone checkpoint with HANDOVER.md (P3+: p3-mid, p3-pre-gate2, p5-baseline, p7, p8)",
    )
    pm.add_argument("--type", required=True,
                    choices=["p3-mid", "p3-pre-gate2", "p3-post-gate2",
                             "p4-mid", "p4-pre-gate3",
                             "p5-baseline", "p7", "p8", "cr-close"],
                    help="Milestone type (cr-close: P9 per-CR closure push, requires --cr)")
    pm.add_argument("--cr", default=None,
                    help="CR id for --type cr-close (e.g. CR-01)")
    pm.add_argument("--project", default=".", help="Project root (default: .)")
    pm.add_argument("--fr-ids",  default="", dest="fr_ids",
                    help="Comma-separated FR IDs")
    pm.add_argument("--fr-done",  type=int, default=None,
                    help="FRs completed so far (p3-mid only)")
    pm.add_argument("--fr-total", type=int, default=None,
                    help="Total FR count (p3-mid only)")
    pm.add_argument("--no-git", action="store_true", dest="no_git",
                    help="Disable git operations")
    pm.add_argument("--dry-run", action="store_true", dest="dry_run",
                    help="Print planned actions (HANDOVER.md content + git commands) "
                         "without committing or pushing (Bug #112: prevents accidental "
                         "origin pollution when exercising the command)")
    pm.set_defaults(func=cmd_push_milestone)
