"""The seven steps advance-phase takes once its prechecks have passed.

Round 82 站3, the sibling of 站2 and the same move: Round 81 站7 extracted these
seven statement runs out of `cmd_advance_phase` (845 -> 413 lines) and left them
in cli/phase_cmds.py. Here they leave, and the thirteen call sites in
`cmd_advance_phase` do not change.

`_run_doctor_after_advance` travels with them. It is the last thing
`cmd_advance_phase` does, it is the only name in phase_cmds these seven read
(measured: zero back-dependencies of its own), and leaving it behind would mean
importing back into phase_cmds — the cycle this round refuses to resolve by
line order.

The bodies are byte-identical to what they replaced.
tests/test_god_file_split_safety.py fingerprints all eight by AST source-segment
sha256 and tests/test_extraction_moved_not_rewrote.py checks the seven against
the recording of phase_cmds.py as 站6 of Round 81 left it. Neither golden was
regenerated for this move.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from cli.advance_commit import (
    _advance_commit_targets,
    _advance_fsm,
    _enforcer_moved_note,
    _git_head_short,
)
from core import claude_md
from core.atomic_io import atomic_write_json, file_lock, state_lock_path
from core.degradation_ledger import record_degradation
from core.doctor import run_doctor
from core.evidence_retention import ADVANCE_CLEARED_DIRS
from core.harness_config import get_timeout
from core.harness_provenance import (
    enforcer_sha,
    enforcer_surface,
    phase_record_defects,
)
from core.phase_topology import EXIT_GATE_MAP
from core.state_io import load_state
from core.utils.delivery_scope import committed_tree_digest
from core.utils.timefmt import utc_now_iso


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


def _advance_step_refuse_phase_9(args) -> "int | None":
    """Efuse phase 9 — extracted verbatim from `cmd_advance_phase`.

    Round 81 站6. See the note above the first `_precheck_*` for what
    makes this a move rather than a rewrite.
    """
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
    # Generated by the extraction, not moved with the run: mypy
    # requires the fall-through path to be explicit.
    return None


def _advance_step_refuse_uncommitted(_uncommitted, args, project) -> "int | None":
    """Efuse uncommitted — extracted verbatim from `cmd_advance_phase`.

    Round 81 站6. See the note above the first `_precheck_*` for what
    makes this a move rather than a rewrite.
    """
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
    # Generated by the extraction, not moved with the run: mypy
    # requires the fall-through path to be explicit.
    return None


def _advance_step_refuse_open_obligations(_obligations, args, next_phase, project) -> "int | None":
    """Efuse open obligations — extracted verbatim from `cmd_advance_phase`.

    Round 81 站6. See the note above the first `_precheck_*` for what
    makes this a move rather than a rewrite.
    """
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
    # Generated by the extraction, not moved with the run: mypy
    # requires the fall-through path to be explicit.
    return None


def _advance_step_run_fsm_transition(args, last_fr_id, last_gate_num, project) -> None:
    """Un fsm transition — extracted verbatim from `cmd_advance_phase`.

    Round 81 站6. See the note above the first `_precheck_*` for what
    makes this a move rather than a rewrite.
    """
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


def _advance_step_seed_p8_archive(next_phase, project) -> None:
    """Eed p8 archive — extracted verbatim from `cmd_advance_phase`.

    Round 81 站6. See the note above the first `_precheck_*` for what
    makes this a move rather than a rewrite.
    """
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


def _advance_step_write_next_plan_header(gen, next_phase, status, task_bg) -> None:
    """Rite next plan header — extracted verbatim from `cmd_advance_phase`.

    Round 81 站6. See the note above the first `_precheck_*` for what
    makes this a move rather than a rewrite.
    """
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


def _advance_step_commit_and_push(_advance_snap, _manifest_regenerated, _saved_cwd, _setup_cfg_written, args, next_phase, project) -> "int | None":
    """Ommit and push — extracted verbatim from `cmd_advance_phase`.

    Round 81 站6. See the note above the first `_precheck_*` for what
    makes this a move rather than a rewrite.
    """
    _record_was_writable = False
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
            # Round 89: from here the framework CAN produce a record, so from
            # here its absence is the framework's failure. The two branches
            # that cannot — no git HEAD to name, and HARNESS_NO_GIT — leave
            # this False and already record their own degradation.
            _record_was_writable = True
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

    # ── Read back the record this command just wrote (Round 89) ──────────
    #
    # taskq-super reached Phase 9 with `phase_completed` entries for 1, 2, 3,
    # 4, 6, 7 — no 5 — and nothing objected. Its `handover: advance to Phase
    # 6` commit exists and carries `last_milestone_command: advance-phase
    # --completed-phase 5`, so the command ran and the FSM advanced; `"5": {`
    # is absent from that project's entire git history, and the next commit to
    # touch state.json (5h23m later) carries no `phase_completed` change, so
    # the entry never reached the working tree either.
    #
    # Round 89 ruled out four explanations — `postflight_update_state` cannot
    # be the lost-update writer (its write is behind `self.phase > old_phase`,
    # false after an advance, and it never runs concurrently), every other
    # state writer keeps `load` inside its own `file_lock`, the commit-failure
    # path rolls the FSM back, and both write-failure branches record a
    # degradation of which that project's 626-row ledger holds none. The cause
    # is still unknown. The OUTCOME is not, and it is the same sentence
    # whichever cause it turns out to be: this command said phase N was
    # complete and the record for it is not there.
    #
    # Deliberately OUTSIDE the `HARNESS_NO_GIT` branch above, at this
    # function's own indent. Both writes live inside that `else`, so a check
    # placed beside them is skipped by exactly the condition that is the last
    # remaining path consistent with what taskq-super shows.
    #
    # `phase_record_defects` is Round 72 站1's — a second opinion on what a
    # record looks like is what this repository keeps having to merge back.
    _record = (load_state(project, lenient=True).get("phase_completed")
               or {}).get(str(args.completed_phase))
    _defects = ([f"phase_completed[{args.completed_phase}] is absent"]
                if not _record
                else phase_record_defects(project, _record))
    if _defects:
        from cli.exit_codes import EX_PHASE_RECORD_NOT_WRITTEN

        # `_record_was_writable` is the whole distinction: "tried and did not
        # land" versus "never could". No git HEAD to name and HARNESS_NO_GIT
        # are both the second — there is no sha for the record to carry, the
        # branch that skipped it already recorded its own degradation, and
        # blocking would turn a documented switch and a non-repo directory
        # into framework failures. Listing environment conditions here instead
        # would be a second, drifting copy of what those branches already know.
        record_degradation(
            project, "advance-phase",
            f"phase_completed[{args.completed_phase}] did not land",
            "; ".join(_defects) + ("" if _record_was_writable
                                   else " (no writable record on this path)"),
            owner="harness" if _record_was_writable else "project",
        )
        if _record_was_writable:
            print(
                f"\n[BLOCKED] advance-phase: {'; '.join(_defects)}.\n"
                f"  The phase advanced and the handover commit was made, but "
                f"the record naming the commit, the enforcer and the tree it "
                f"was judged on is not in state.json — `doctor`'s verdict "
                f"re-derivation, `_fr_step_lineage_boundary` and the entry "
                f"gate all read it.\n"
                f"  Fix: re-run so the framework writes it:\n"
                f"    python harness_cli.py advance-phase --completed "
                f"{args.completed_phase} --project {project}",
                file=sys.stderr,
            )
            return EX_PHASE_RECORD_NOT_WRITTEN
        # An abstention nobody can see is indistinguishable from a pass
        # (Round 27), which is why it still reaches the ledger above.
        print(f"[advance-phase] phase_completed[{args.completed_phase}] not "
              f"recorded — this path could not produce one (logged)")

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
