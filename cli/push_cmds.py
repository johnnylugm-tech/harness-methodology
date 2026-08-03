"""Checkpoint/milestone push commands (push-checkpoint, push-milestone).

Extracted verbatim from harness_cli.py (方案六); helpers moved home in
絞殺者續章 S4 — this module no longer imports harness_cli (all
dependencies are direct stdlib/core/harness imports). harness_cli still
re-exports the cmd_* names, so `from harness_cli import cmd_x` works.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

from cli import _shared
from core.atomic_io import atomic_write_json, file_lock, state_lock_path
from core.phase_topology import ENTRY_GATE_MAP
from core.state_io import load_quality_manifest, load_state


def cmd_push_checkpoint(args: argparse.Namespace) -> int:
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
    project = Path(args.project).resolve()
    fr_ids = [f.strip() for f in args.fr_ids.split(",") if f.strip()]
    # Note: if fr_ids is empty here, GitStrategy.commit_and_push_p1/p2 will
    # auto-detect from SRS.md — no need to block here.

    git = _shared._make_git(args, project)
    git.ensure_gitignore()
    phase = args.phase
    if phase not in (1, 2):
        print(f"[ERROR] push-checkpoint only supports P1/P2 (got phase {phase}).")
        return 1

    # Shared pre-push attestation refresh (refresh_attestation's docstring
    # carries the "every push path is symmetric" invariant and its history);
    # the try only guards the import — the helper itself never raises.
    try:
        from scripts.build_trace_attestation import refresh_attestation
        refresh_attestation(project)
    except Exception as _att_err:  # pylint: disable=broad-exception-caught
        print(f"  [WARN] attestation pre-refresh failed: {_att_err}")

    # Bug fix (P8 E2E 2026-07-04): write phase_completed to state.json BEFORE
    # commit_and_push_p1/p2 so the audit field lands in the pushed commit.
    # See plan: ~/.claude/plans/abundant-stargazing-hejlsberg.md
    #
    # Round 24 站4b: last_push_checkpoint / last_push_checkpoint_phase were
    # written here and read NOWHERE (re-scanned across cli/, core/, scripts/,
    # harness/ and the generated workflow JS — the earlier R21-D' scan that
    # missed harness/harness_bridge.py is why this one names its coverage).
    # Removed; phase_completed stays because _verify_entry_gate,
    # _fr_step_lineage_boundary and constitution/runner.py all read it.
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
            with file_lock(state_lock_path(project)):
                _state_data = load_state(project)
                _prev_phase_completed_entry = _state_data.get("phase_completed", {}).get(str(phase))
                _state_data.setdefault("phase_completed", {})[str(phase)] = {
                    "sha": _pre_push_sha,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
                atomic_write_json(state_path, _state_data)
                _wrote_checkpoint_state = True
        except Exception as _state_err:  # pylint: disable=broad-exception-caught
            print(f"  [WARN] Could not write push-checkpoint sentinel to state.json: {_state_err}")

    # Round 12 站2b: heal a stale attestation BEFORE the checkpoint commit
    # fires the prepare-commit-msg hook — replaces the prompt-side
    # TRACE-PRECHECK ritual (P2's SAD.md is always newer than the
    # attestation right after generation, so this fired on every P2 push).
    _shared.ensure_fresh_attestation(project)

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
            with file_lock(state_lock_path(project)):
                _state_data = load_state(project)
                if _prev_phase_completed_entry is None:
                    _state_data.get("phase_completed", {}).pop(str(phase), None)
                else:
                    _state_data.setdefault("phase_completed", {})[str(phase)] = _prev_phase_completed_entry
                atomic_write_json(state_path, _state_data)
        except Exception as _revert_err:  # pylint: disable=broad-exception-caught
            print(f"  [WARN] Could not revert push-checkpoint sentinel after push failure: {_revert_err}")

    if ok:
        # Post-push self-check: warn loudly on dirty residue. Push itself
        # succeeded — the dirt is post-commit residue. Don't fail-fast.
        _dirty = _shared._post_push_self_check(project)
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


def _record_milestone_head(project: Path, milestone_type: str) -> None:
    """Remember the HEAD this milestone landed on, so the next identical one
    can tell there is nothing new to record.

    Best-effort by design: failing to write this must not turn a successful
    push into a failure. A missed write costs one duplicate milestone, which
    is the behaviour that existed before this round.
    """
    import subprocess as _sp

    try:
        head = _sp.run(["git", "rev-parse", "HEAD"], cwd=str(project),
                       capture_output=True, text=True, timeout=10)
        if head.returncode != 0 or not head.stdout.strip():
            return
        head_sha = head.stdout.strip()
        with file_lock(state_lock_path(project)):
            state = load_state(project, lenient=True)
            recorded = state.get("last_milestone_head")
            if not isinstance(recorded, dict):
                recorded = {}
            recorded[milestone_type] = head_sha
            state["last_milestone_head"] = recorded
            atomic_write_json(project / ".methodology" / "state.json", state)
    except (OSError, _sp.SubprocessError, ValueError) as exc:
        print(f"  [WARN] could not record milestone HEAD ({exc}) — a repeat of "
              f"this milestone at the same HEAD will not be caught")


def _milestone_already_recorded(project: Path, milestone_type: str) -> str:
    """The HEAD sha when this milestone type was last recorded at THIS HEAD.

    Empty string when it was not — including when state.json is unreadable,
    because "we cannot tell" must not silently suppress a milestone.
    """
    import subprocess as _sp

    try:
        head = _sp.run(["git", "rev-parse", "HEAD"], cwd=str(project),
                       capture_output=True, text=True, timeout=10)
        if head.returncode != 0:
            return ""
        head_sha = head.stdout.strip()
    except (OSError, _sp.SubprocessError):
        return ""
    if not head_sha:
        return ""

    state = load_state(project, lenient=True)
    recorded = state.get("last_milestone_head") or {}
    if not isinstance(recorded, dict):
        return ""
    return head_sha[:9] if recorded.get(milestone_type) == head_sha else ""


def _validate_gate1_sweep(project: Path, phase: int, fr_ids: list) -> str:
    """Empty string when every FR really has a recorded Gate 1 verdict.

    Round 32 站6. `p3-pre-gate2` and `p4-pre-gate3` commit a message that says
    all N FRs passed Gate 1. Both went straight to git with nothing between
    them and the claim, while `p3-post-gate2` next door has validated its
    precondition since v2.9.1. Measured on a live P4: two byte-identical
    "all 8 FR(s) Gate1 re-eval PASS" milestones ten minutes apart, zero
    recorded verdicts behind either, and the FR-01 gate BLOCKED fourteen
    minutes after the second.

    The count in the message and the count checked here come from the same
    call, so the check cannot be checking a different sentence than the one
    that gets committed.
    """
    from core.quality_gate import gate1_evidence

    summary = gate1_evidence.gate1_phase_summary(project, phase, fr_ids or None)
    if not summary["missing"]:
        return ""
    missing = summary["missing"]
    shown = ", ".join(missing[:5]) + (f" +{len(missing) - 5} more"
                                      if len(missing) > 5 else "")
    return (
        f"[ERROR] milestone blocked — it would claim all "
        f"{len(summary['expected'])} FR(s) passed Gate 1 in phase {phase}, but "
        f"{len(missing)} have no recorded verdict: {shown}\n"
        f"  A recorded verdict means both a .gate1_scores.json entry and a "
        f"`finalize` row in gate_timestamps.jsonl — finalize-gate writes both.\n"
        f"  Run `finalize-gate --gate 1 --phase {phase} --fr-id <FR>` for each, "
        f"then push the milestone."
    )


def cmd_push_milestone(args: argparse.Namespace) -> int:
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
    project = Path(args.project).resolve()
    git = _shared._make_git(args, project)
    git.ensure_gitignore()
    if getattr(args, "dry_run", False):
        print(f"[dry-run] push-milestone --type {args.type} would: write HANDOVER.md + "
              f"commit + push to origin (no changes made; Bug #112 safety flag)")
        return 0
    # Shared pre-push attestation refresh (refresh_attestation's docstring
    # carries the "every push path is symmetric" invariant and its history);
    # the try only guards the import — the helper itself never raises.
    try:
        from scripts.build_trace_attestation import refresh_attestation
        refresh_attestation(project)
    except Exception as _att_err:  # pylint: disable=broad-exception-caught
        print(f"  [WARN] attestation pre-refresh failed: {_att_err}")
    milestone_type = args.type
    fr_ids = [f.strip() for f in args.fr_ids.split(",") if f.strip()]

    ok = False
    # Auto-populate fr_ids from manifest when not provided
    if not fr_ids:
        fr_ids = load_quality_manifest(project, lenient=True).get("fr_ids", [])

    # Entry-gate evidence BEFORE any side effect (E2E round 2 C-1/C-2:
    # p5-baseline and p7 pushed fake milestones with no gate evidence; even
    # the failing path left a milestone commit behind). Fail-closed: a
    # missing or unreadable manifest is absence of evidence, not permission.
    _MILESTONE_ENTRY_GATES = {
        "p5-baseline": ENTRY_GATE_MAP[5],
        "p7": ENTRY_GATE_MAP[7],
        "p8": ENTRY_GATE_MAP[8],
    }
    _required_gate = _MILESTONE_ENTRY_GATES.get(milestone_type)
    if _required_gate is not None:
        _mf_gate = load_quality_manifest(project, lenient=True)
        _gate_rec = (_mf_gate.get("gate_results") or {}).get(f"gate{_required_gate}") or {}
        if not _gate_rec.get("quality_complete"):
            print(
                f"[BLOCKED] push-milestone --type {milestone_type}: entry gate "
                f"Gate {_required_gate} has no PASS evidence "
                f"(gate_results.gate{_required_gate}.quality_complete is not True "
                "in .methodology/quality_manifest.json).\n"
                f"  Run the Gate {_required_gate} evaluation and finalize-gate first."
            )
            return 2

    # Round 32 站6: the same milestone, twice, against an unchanged HEAD.
    # Measured on a live P4: 34235b6 and 9807b22 ten minutes apart with
    # byte-identical subject lines, the second changing one line of state.json
    # and six of HANDOVER.md. Round 20 站3 removed the EMPTY milestone commit
    # at this decision point; a repeated one is the neighbouring case — a
    # commit that records nothing that happened since the last one.
    _dup = _milestone_already_recorded(project, milestone_type)
    if _dup:
        print(
            f"[SKIP] push-milestone --type {milestone_type}: already recorded "
            f"at this HEAD ({_dup}).\n"
            f"  Nothing has been committed since, so a second milestone would "
            f"record no new work. Re-run after the next commit, or use a "
            f"different --type."
        )
        return 0

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
            with file_lock(state_lock_path(project)):
                _state_data = load_state(project)
                _prev_last_milestone_command = _state_data.get("last_milestone_command")
                _prev_last_milestone_at = _state_data.get("last_milestone_at")
                _state_data["last_milestone_command"] = f"push-milestone --type {milestone_type}"
                _state_data["last_milestone_at"] = datetime.now(timezone.utc).isoformat()
                atomic_write_json(state_path, _state_data)
                _wrote_milestone_state = True
        except Exception as _state_err:  # pylint: disable=broad-exception-caught
            print(
                f"\n  [WARN] Could not write last_milestone_command to state.json: {_state_err}"
            )

    def _revert_milestone_audit_write() -> None:
        if not _wrote_milestone_state:
            return
        try:
            with file_lock(state_lock_path(project)):
                _sd = load_state(project)
                if _prev_last_milestone_command is None:
                    _sd.pop("last_milestone_command", None)
                else:
                    _sd["last_milestone_command"] = _prev_last_milestone_command
                if _prev_last_milestone_at is None:
                    _sd.pop("last_milestone_at", None)
                else:
                    _sd["last_milestone_at"] = _prev_last_milestone_at
                atomic_write_json(state_path, _sd)
        except Exception as _revert_err:  # pylint: disable=broad-exception-caught
            print(f"  [WARN] Could not revert stale milestone audit fields: {_revert_err}")

    # Round 12 站2b: heal a stale attestation BEFORE the milestone commit
    # fires the prepare-commit-msg hook (see _shared.ensure_fresh_attestation
    # — replaces the prompt-side TRACE-PRECHECK ritual).
    _shared.ensure_fresh_attestation(project)

    if milestone_type == "p3-mid":
        fr_done = args.fr_done
        fr_total = args.fr_total
        if fr_done is None or fr_total is None or fr_total == 0:
            print("[ERROR] --fr-done and --fr-total required for p3-mid (fr-total must be >0)")
            _revert_milestone_audit_write()
            return 1
        ok = git.commit_and_push_p3_mid(fr_done, fr_total, fr_ids)
    elif milestone_type == "p3-pre-gate2":
        _sweep = _validate_gate1_sweep(project, 3, fr_ids)
        if _sweep:
            print(_sweep)
            _revert_milestone_audit_write()
            return 1
        ok = git.commit_and_push_p3_pre_gate2(fr_ids)
    elif milestone_type == "p3-post-gate2":
        # v2.9.1 B.2: validate Gate 2 PASS + all FRs Gate 1 PASS as precondition
        _pre = _shared._validate_p3_post_gate2_precondition(project, fr_ids)
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
        _sweep = _validate_gate1_sweep(project, 4, fr_ids)
        if _sweep:
            print(_sweep)
            _revert_milestone_audit_write()
            return 1
        ok = git.commit_and_push_p4_pre_gate3(fr_ids)
    elif milestone_type == "p5-baseline":
        ok = git.commit_and_push_p5_baseline()
    elif milestone_type == "p7":
        ok = git.commit_and_push_p7()
    elif milestone_type == "p8":
        p8_errors = _validate_p8_completion(project)
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
        _dirty = _shared._post_push_self_check(project)
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
        _record_milestone_head(project, milestone_type)
    return 0 if ok else 1




# --- helpers moved verbatim from harness_cli.py (絞殺者續章 S4b) ---

def _validate_p8_completion(project: Path) -> list[str]:
    """Pre-flight checks required before push-milestone --type p8 is allowed."""
    errors: list[str] = []

    # 1. .methodology-archive/ — auto-create if absent
    archive_dir = project / ".methodology-archive"
    if not archive_dir.exists():
        archive_dir.mkdir(parents=True, exist_ok=True)

    # 2. (removed) HANDOVER.md used to be forbidden from referencing Phase 9
    # back when P8 was the terminal phase. Phase 9 (Maintenance) is now a
    # legal steady state entered via `advance-phase --completed 8`, so a
    # P8-exit HANDOVER legitimately points at Phase 9 next steps.

    # 3. Finding #24: archive must contain .methodology/ contents (not .sessi-work/).
    # Old P8 plan had a typo: 'cp -r .sessi-work/ .methodology-archive/' which copied
    # the gitignored runtime scratch dir instead of the methodology artifacts the
    # archive name semantically implies. Validator now checks the archive actually
    # has methodology content (e.g. a phase*_plan.md or quality_manifest.json).
    # Also catches the inverse case: archive contains a `sessi-work/` subdir
    # (i.e. the agent ran the buggy cp command verbatim and produced
    # .methodology-archive/sessi-work/).
    archive_sessi = archive_dir / "sessi-work"
    if archive_sessi.exists():
        errors.append(
            ".methodology-archive/sessi-work/ exists — this is the Finding #24 "
            "typo outcome. The P8 archive must contain .methodology/ contents, "
            "not the gitignored runtime scratch dir. Re-run: "
            "`rm -rf .methodology-archive && mkdir -p .methodology-archive && "
            "cp -r .methodology/ .methodology-archive/`."
        )

    # Positive content check: `cp -r .methodology/ .methodology-archive/` (trailing
    # slash on source, destination already created by mkdir) copies the CONTENTS of
    # .methodology/ directly into .methodology-archive/ — phase*_plan.md and
    # quality_manifest.json land at archive_dir/*.  There is no "methodology/"
    # subdirectory.  Catch both an empty archive (mkdir ran but cp didn't) and any
    # other wrong-source copy, but skip when sessi-work was already reported above.
    if not archive_sessi.exists():
        _has_methodology_content = any(archive_dir.glob("phase*_plan.md")) or (
            archive_dir / "quality_manifest.json"
        ).exists()
        if not _has_methodology_content:
            errors.append(
                ".methodology-archive/ contains no methodology artifacts "
                "(phase*_plan.md / quality_manifest.json). "
                "Re-run: `rm -rf .methodology-archive && mkdir -p .methodology-archive"
                " && cp -r .methodology/ .methodology-archive/` "
                "(do NOT copy .sessi-work/ — that is the Finding #24 typo)."
            )

    return errors


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
