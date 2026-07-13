"""Production-file line-count ratchet — god-file growth must be deliberate.

Round 3 claims 2/4 residue: the repo's largest files (harness_bridge,
gate_cmds, phase_cmds, fr_cmds, ...) are safety-critical surfaces
deliberately NOT decomposed this round (the M2-M4 plangen split handled the
one with a proven drift wound). This ratchet does for file growth what
test_patch_discipline does for private patches, with one deliberate
difference spelled out here: line counts legitimately grow, so ceilings MAY
be raised — but only in the same commit as the growth, with the reason in
the commit message. The product is diff-visibility of growth, not an
absolute cap. A file not listed in _LINE_CEILING must stay below
_GOD_FILE_THRESHOLD entirely; lowering a ceiling after shrinking a file is
manual, same as the patch ratchet.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

_SCAN_DIRS = ("cli", "core", "harness", "scripts", "detection")
_GOD_FILE_THRESHOLD = 900

# Snapshot 2026-07-11 (Round 3 Station L, after the M2-M4 plangen split —
# generate_full_plan.py itself is down to a ~250-line facade and off this
# list; the split's two large products are honestly listed).
_LINE_CEILING: dict[str, int] = {
    # 2026-07-12: +31 lines — env-check prompt teaches optional_missing vs
    # required distinction (fix false fabrication flag on vars with baked-in
    # config defaults). Example uses generic DATABASE_URL, not project-specific.
    "harness/harness_bridge.py": 2963,
    # 2026-07-12: +2 lines net — Round 6 站2: _check_sab_module_alignment's
    # unregistered-direction scan now delegates to sab_amender.
    # discover_modules_at() (removed inline loop, +docstring paragraph
    # explaining the delegation) instead of a locally re-implemented rglob
    # loop that had silently diverged (never skipped __pycache__).
    "cli/gate_cmds.py": 2569,
    # 2026-07-11: +26 lines — cmd_advance_phase now refreshes the
    # traceability attestation before its handover commit (mirrors the
    # existing push_cmds.py refresh in push-checkpoint/push-milestone), and
    # the P2-A SAB pre-check now matches DriftItem.actual instead of a dead
    # description substring.
    # 2026-07-12: +14 lines — Round 5 建議2站2: cmd_plan_phase/cmd_plan_all
    # migrate from cwd-relative `from scripts.generate_full_plan import` to
    # load_harness_script("generate_full_plan.py") (same P6/A1 bug class,
    # never swept for this module).
    # 2026-07-13: +25 lines — STAGE_PASS generation-order fix: the
    # "Always-regenerate Phase{N}_STAGE_PASS.md" block moved from mid-function
    # (right after HR-11) to the true end of _advance_prechecks (after Agent B
    # approvals / TDD / SAB drift / submodule guard all pass), so it can pass
    # truth_override=True to _generate_stage_pass instead of reading
    # state.json.phase_truth_passed before _advance_fsm has written it. Added
    # a small early "ensure exists" pass at the block's old position so the
    # internal Phase Auditor call (a few lines later) doesn't CRITICAL-fail
    # its own C2 check on a first-ever advance.
    "cli/phase_cmds.py": 2580,
    # 2026-07-11: +35 lines — _fr_step_already_done's idempotency grep is now
    # scoped to the current phase's lineage boundary (read from tracked
    # state.json phase_completed), fixing a false "already done" skip on
    # reset-and-rerun projects (TDD-IMPROVE had no secondary evidence check).
    # 2026-07-12: +39 lines — dispatch failures now fail fast on the
    # deterministic "claude.ai connectors are disabled" signature (shared
    # _abort_dispatch_structurally_broken() helper, 2 call sites) instead of
    # exhausting max_fix_rounds against an environment that cannot ever
    # succeed (P3 2026-07-12 FR-04 GATE1: 5.4h silent retry loop before the
    # external workflow watchdog aborted).
    # 2026-07-12: +26 lines — COVERAGE-FIX's measurement command now scopes
    # to the FR's own owned source via the shared
    # core.quality_gate.cov_utils.resolve_fr_scoped_src_files() (same
    # resolver run-gate --fr-id already uses), instead of the whole
    # 03-development/src tree — the whole tree was unsatisfiable while
    # sibling FRs' stub modules sit at 0% coverage (P3 2026-07-12: FR-01/
    # FR-02 both BLOCKED after 2 no-progress rounds chasing the wrong
    # denominator).
    # 2026-07-13: +21 lines — Round 9 station 2: run-fr-step's tunables now
    # read the harness_config `values` section (permission_mode /
    # max_fix_rounds / fr_step timeout / step_max_turns overlay with
    # unknown-step WARN); precedence chain unchanged and locked by
    # tests/test_fr_cmds_values_wiring.py.
    # 2026-07-13: +3 lines — FIX-O: cmd_run_fr_step's first-dispatch error
    # branch (TDD-RED/GREEN/IMPROVE + GATE1's pre-fix-loop attempt) now calls
    # _is_connector_disabled_failure/_abort_dispatch_structurally_broken,
    # mirroring the two other dispatch sites in this file that already had it.
    # 2026-07-13: net -2 lines — Bug D: SRS.md path resolution was hard-coded
    # to the wrong location (.methodology/SRS.md) in two call sites while
    # _fr_step_preflight's own fallback list (never reused) had the correct
    # 01-requirements/SRS.md entry (P3 FR-05: resume-fr-phase's suggested
    # command and TDD-RED's prompt builder both failed preflight with "SRS.md
    # not found"). Both sites + preflight's own fallback loop now call the
    # existing ProjectLayout(project).srs_path (single source of truth already
    # used by 14+ other call sites across the harness — phase_cmds.py,
    # harness_bridge.py, spec_alignment.py, ...) instead of guessing among
    # hard-coded candidate strings; dropped the wrong --srs flag from
    # resume-fr-phase's printed command.
    # Bug F: TDD-RED's prompt gives sub-agents no instruction for a test file
    # that already exists but is uncommitted (P3 FR-05: a sub-agent found
    # test_fr05.py surviving a mid-flight reset, chose "review, don't
    # overwrite", and never ran the commit step) — step 1 now says explicitly
    # that an existing-but-uncommitted file still requires completing step 5.
    "cli/fr_cmds.py": 2271,
    # 2026-07-12: +3 lines — Round 5 建議2站2: same load_harness_script
    # migration for the parse_srs_fr_sections/parse_sad_modules call sites.
    # 2026-07-13: +7 lines — audit-phase subparser gained a `description=`
    # clarifying it must run BEFORE advance-phase for a phase-scoped C10
    # result (no workflow JS ever calls it automatically).
    "cli/project_cmds.py": 1870,
    # 2026-07-12: +2 lines — Round 5 exception-swallow ratchet: GitHubFetcher/
    # LocalFetcher.get_file_content now log the swallowed decode/read error.
    "scripts/phase_auditor.py": 1848,
    # 2026-07-13: +5 lines — Round 10 站4: P2 Agent B checklist (both
    # _AGENT_B_CHECKS[2] and the SAD.md deliverable's own "checks" list)
    # gains a SEC-block-complete item; P4 hunt step text notes threat_model
    # targets from bug-hunt-targets.
    # 2026-07-14 (+2): _deliverable_ab_block templates updated to 3-layer
    # defense — remaining "NO access / paste full content" → Bash-cat prose.
    "scripts/plangen/blocks.py": 1665,
    # 2026-07-11: +3/+6 lines — new check_module_fr_coverage gate (module/FR-NFR
    # ownership drift between TRACEABILITY_MATRIX.md's own §5.3 and
    # SPEC_TRACKING.md's §5) wired into preflight_artifact_consistency
    # (phase_hooks.py) and cmd_check_artifact_consistency (check_cmds.py),
    # mirroring the existing check_forward_refs/check_nfr_adr_coverage wiring.
    # 2026-07-12: +1 line — Round 6 站1: preflight_sab_check now imports
    # sab_amender.sab_module_candidate() instead of a locally-duplicated
    # dict-unwrap inline.
    # 2026-07-13: +11 lines — Round 10 站3: preflight_artifact_consistency
    # now also runs check_security_design (SAD.md §6 STRIDE-lite threat
    # model completeness).
    "core/phase_hooks.py": 1594,
    # 2026-07-12: +7 lines — Round 5 建議2站1: _generate_sab_json now resolves
    # scripts/ via the shared harness_scripts_dir() SSOT instead of its own
    # (broken) Path(__file__).parent arithmetic.
    # 2026-07-13: +63 lines — Round 10 站3: cmd_check_artifact_consistency
    # now also runs check_security_design (reads current_phase from
    # state.json); cmd_bug_hunt_targets gains a 6th targeting source
    # (threat_model — SAD.md §6 threats' owner_module resolved to an
    # on-disk path, same candidate expansion preflight_sab_check uses).
    # 2026-07-13: +34 lines — T1-A (8-phase workflow-audit remediation):
    # new cmd_check_manifest_integrity + its subparser registration, a thin
    # CLI wrapper around PhaseHooks.preflight_manifest_integrity() so
    # workflow JS stops reimplementing (and getting wrong) this check inline.
    "cli/check_cmds.py": 1466,
    # 2026-07-12: +2 lines — Round 5 exception-swallow ratchet: _manifest_fr_ids
    # / _auto_fr_ids now log the swallowed parse error before returning [].
    "harness/git_strategy.py": 1292,
    # 2026-07-13: +28 lines — Round 10 站4: P2 tasks gain a
    # [SEC-WRITE]/[SEC-VALIDATE] step pair next to [SAB-WRITE], mirroring
    # the SAB block's own authoring-guidance shape.
    "scripts/plangen/phase_tasks.py": 1134,
    "core/quality_gate/mutation_enforcer.py": 967,
}


def _production_line_counts() -> dict[str, int]:
    counts: dict[str, int] = {}
    for d in _SCAN_DIRS:
        for path in sorted((REPO / d).rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            rel = path.relative_to(REPO).as_posix()
            counts[rel] = len(
                path.read_text(encoding="utf-8", errors="replace").splitlines()
            )
    return counts


def _violations(counts: dict[str, int]) -> list[str]:
    out = []
    for rel, count in sorted(counts.items()):
        ceiling = _LINE_CEILING.get(rel)
        if ceiling is not None:
            if count > ceiling:
                out.append(
                    f"{rel}: {count} lines > ceiling {ceiling} — split the "
                    f"file, or if the growth is deliberate raise the ceiling "
                    f"in THIS commit and justify it in the commit message"
                )
        elif count >= _GOD_FILE_THRESHOLD:
            out.append(
                f"{rel}: {count} lines — new god file (unlisted, threshold "
                f"{_GOD_FILE_THRESHOLD}); split it or add a justified "
                f"ceiling entry"
            )
    return out


def test_production_file_line_ratchet():
    over = _violations(_production_line_counts())
    assert not over, (
        "god-file growth must be a reviewed decision, not a silent drift:\n  "
        + "\n  ".join(over)
    )


def test_comparator_fires_on_listed_growth():
    """Negative: one line over a listed ceiling must trip the ratchet."""
    rel = "cli/gate_cmds.py"
    assert _violations({rel: _LINE_CEILING[rel] + 1})


def test_comparator_fires_on_new_god_file():
    """Negative: an unlisted file at the threshold must trip the ratchet."""
    assert _violations({"cli/newly_huge.py": _GOD_FILE_THRESHOLD})


def test_comparator_quiet_at_or_under_limits():
    rel = "cli/gate_cmds.py"
    assert _violations({
        rel: _LINE_CEILING[rel],
        "cli/small.py": _GOD_FILE_THRESHOLD - 1,
    }) == []
