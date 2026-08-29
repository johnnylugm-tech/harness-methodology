"""What run-fr-step dispatches through: its four steps and what they read.

Round 82 站4. Round 81 站9 extracted four statement runs out of
`cmd_run_fr_step` (940 -> 770 lines) as `_frstep_*` and left them in
cli/fr_cmds.py. Here they leave — and so do the two families they read, because
the alternative is this module importing back into cli.fr_cmds, a cycle that
resolves only by line order.

THE CARGO IS BIGGER THAN THE PAYLOAD, AND THAT IS RECORDED RATHER THAN HIDDEN

The four runs are 218 lines. What has to travel with them is 357 more:

  the dispatch-error readers   `_is_connector_disabled_failure`,
                               `_abort_dispatch_structurally_broken`,
                               `_reports_precondition_block`,
                               `_resolve_precondition_block`, and the two
                               constants they answer with — read by
                               `_frstep_route_dispatch_error`

  the idempotence family       `_fr_step_already_done` (191 lines),
                               `_fr_step_lineage_boundary`, `_fr_tests_say`
                               and `_FR_STEP_COMMIT_PATTERNS` — read by
                               `_frstep_skip_if_already_done` and
                               `_frstep_gate1_paper_trail`

Each group is a coherent family rather than an arbitrary drag, which is why
they are one module and not two: splitting them would be inventing a boundary
that does not exist in the code. cli/fr_cmds.py re-exports everything, so its
five remaining `_DISPATCH_ERROR_STATUSES` call sites, every test that imports
`_fr_step_already_done` by name, and tests/test_exit_code_registry.py's scan of
`cli/*.py` are all unaffected.

Bodies byte-identical: tests/test_god_file_split_safety.py fingerprints the
eleven functions. The three assignments are not `def`s, so that mechanism does
not cover them — the ratchets and the import checks do.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from cli.exit_codes import EX_STEP_PRECONDITION_BLOCKED
from core.agent_spawner import (
    _COMMIT_REQUIRED_STEPS,
    PRECONDITION_BLOCKED,
    blocked_inner_status_in,
    is_structurally_broken,
)
from core.canonical_form import fr_num_str
from core.degradation_ledger import record_degradation
from core.failure_modes import DISPATCH_FAILURE_STATUSES
from core.quality_gate import gate1_evidence
from core.quality_gate import test_suite_run as suite_run
from core.quality_gate.ghost_detector import (
    detect_ghost_changes,
    write_ghost_paper_trail,
)
from core.state_io import load_quality_manifest, load_state
from core.utils.project_layout import ProjectLayout


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


def _frstep_skip_if_already_done(args, fr_id, phase, project, step) -> "int | None":
    """Skip if already done — extracted verbatim from `cmd_run_fr_step`.

    Round 81 站9. See the note above the first `_frstep_*` for what
    makes this a move rather than a rewrite.
    """
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
    # Generated by the extraction, not moved with the run: mypy
    # requires the fall-through path to be explicit.
    return None


def _frstep_route_dispatch_error(_status, fr_id, phase, project, result, step) -> "int | None":
    """Route dispatch error — extracted verbatim from `cmd_run_fr_step`.

    Round 81 站9. See the note above the first `_frstep_*` for what
    makes this a move rather than a rewrite.
    """
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
    # Generated by the extraction, not moved with the run: mypy
    # requires the fall-through path to be explicit.
    return None


def _frstep_gate1_paper_trail(_pre_step_dirty, _pre_step_sha, fr_id, phase, project, result, step) -> "int | None":
    """Gate1 paper trail — extracted verbatim from `cmd_run_fr_step`.

    Round 81 站9. See the note above the first `_frstep_*` for what
    makes this a move rather than a rewrite.
    """
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
    # Generated by the extraction, not moved with the run: mypy
    # requires the fall-through path to be explicit.
    return None


def _frstep_push_checkpoint(_sp, no_push, project) -> "int | None":
    """Push checkpoint — extracted verbatim from `cmd_run_fr_step`.

    Round 81 站9. See the note above the first `_frstep_*` for what
    makes this a move rather than a rewrite.
    """
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
    # Generated by the extraction, not moved with the run: mypy
    # requires the fall-through path to be explicit.
    return None
