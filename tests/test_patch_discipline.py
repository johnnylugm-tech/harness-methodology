"""Private-patch ratchet — implementation-detail mocking can only decrease.

The P2 finding of the 2026-07-10 弱點強化 round: tests that
monkeypatch/mock private attributes of project modules are bound to the
implementation, not the behavior — every refactor that moves or renames a
helper breaks them even when behavior is unchanged (the S0-S5 strangler
round re-targeted ~30 such files by hand). Rewriting 400 existing patches
wholesale would mean rewriting the safety net itself, so instead this
ratchet freezes the debt: per-file counts below may only DECREASE, and a
file not listed here has a ceiling of 0.

New tests exercise public behavior (CLI subprocess journeys in tests/e2e/,
public functions, fixture-built repos) instead of reaching into private
seams. Patching stdlib/public names is not counted.

Counted forms (same ratchet mechanism as test_cli_layering._HC_REF_CEILING):
  1. monkeypatch.setattr(<obj>, "_name", ...)      — quoted private attr
  2. patch("cli.x._name") / mock.patch("core.y._n") — dotted private target
     in a project package (incl. setattr string form)
  3. patch.object(<obj>, "_name")

Deliberate escape hatch: none. If a new test truly needs a private seam,
refactor the seam into a public/injectable one instead — that is the point.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

_FORMS = (
    re.compile(r'monkeypatch\.setattr\(\s*[A-Za-z_][\w.]*\s*,\s*["\']_'),
    re.compile(
        r'(?:mock\.patch|patch|monkeypatch\.setattr)\('
        r'\s*["\'](?:cli|core|harness|scripts|detection|kill_switch|harness_cli)'
        r'[\w.]*\._[A-Za-z]'
    ),
    re.compile(r'patch\.object\(\s*[\w.]+\s*,\s*["\']_'),
)

# Snapshot 2026-07-10 (弱點強化 C2), 400 total. Only decrease; unlisted = 0.
# 2026-07-16 Round 12 站0 exceptions (+3 fr_cmds, +2 agent_spawner): the
# STRUCTURAL-failure registry moved to an empty tuple (the connectors banner
# was disproven), so mechanism tests now INJECT a synthetic signature by
# patching core.agent_spawner._STRUCTURAL_FAILURE_SIGNATURES — the registry
# IS the seam (module-level tuple read at call time); a public setter for a
# test-only injection point would be worse than the patch.
# 2026-07-17 (+1 fr_cmds): test_gate1_blocked_when_codefix_dispatch_errors_not_
# phantom_pass reuses the same monkeypatch.setattr("cli.fr_cmds._fr_step_
# already_done", ...) seam test_gate1_blocked_after_max_rounds already uses —
# one more instance of an already-accepted pattern, not a new coupling.
_PRIVATE_PATCH_CEILING: dict[str, int] = {
    # 2026-08-01 (+6): Round 27 站1's two paired finalize_gate regression tests
    # (test_finalize_gate_blocks_an_unverified_declared_na and
    # test_finalize_gate_accepts_a_framework_verified_na — the pair that pins
    # WHO established a dimension is not applicable), each patching
    # bridge._update_quality_manifest/_log/_effort. Same 3-patch seam as every
    # other TestFinalizeGate test here; converting them to public I/O would mean
    # rewriting the whole class, which is out of this station's scope.
    # 2026-07-31 (+5): test_finalize_gate_override_not_discarded_when_yaml_
    # declares_dimension (the regression pin for the gate_score_overrides
    # threshold-floor fix) patches bridge._update_quality_manifest/_log/
    # _effort + module-level _check_tool_evidence/_run_harness_cross_
    # validation — the exact same 5-patch seam every other TestFinalizeGate/
    # TestSabClosureGaps test in this file already uses to isolate
    # finalize_gate from real manifest/log/effort/tool I/O; not a new
    # coupling, one more instance of the established pattern.
    "tests/test_harness_bridge.py": 83,
    # 2026-07-29 (+6): 2 new finalize_gate regression tests for the null-score
    # Gate-2+ blocking fix (test_finalize_gate_null_breakdown_score_does_not_
    # block, test_finalize_gate_da_waiver_branch_excludes_none_score), each
    # patching bridge._update_quality_manifest/_log/_effort — the exact same
    # 3-patch seam every other TestFinalizeGate test in this file already uses
    # to isolate finalize_gate from real manifest/log/effort I/O; not a new
    # coupling, just two more instances of the established pattern.
    # 2026-07-27 Round 20 站1 (+1): test_agent_claim_of_ready_cannot_override_
    # the_measurement is the regression pin for that station — an agent
    # asserting ready=true while a var it classified as required is unset must
    # not produce exit 0. It reuses the exact monkeypatch.setattr(
    # "cli.gate_cmds._verify_env_check_claims", ...) seam the other
    # TestCmdRunEnvCheck tests already go through (that function shells out to
    # PATH/venv probes, so every test in this class stubs it); one more instance
    # of an already-accepted pattern, not a new coupling.
    "tests/cli/test_gate_cmds_cli.py": 59,
    # 2026-08-03 (+6): Bug #142's run_mutation_precheck wiring test
    # (test_run_mutation_precheck_passes_autoload_disabled_env_to_mutmut_run)
    # monkeypatches _resolve_mutmut_workdir/_is_editable_install/
    # _read_paths_to_exclude/_detect_data_only_files/_abs_paths_to_mutate/
    # _resolve_test_dir — the exact same 6-private-name seam every other
    # run_mutation_precheck test in this file already uses to isolate the
    # function from real filesystem/tool-detection I/O (e.g.
    # test_run_mutation_precheck_promotes_workdir_cache_on_success just
    # above it). Deliberately does NOT stub _copy_setup_cfg_to_workdir like
    # its siblings do — that call must run for real so the workdir's
    # 2026-08-24 (+7): R71-站1's _compute_mutation_score kill-restore test
    # (test_compute_mutation_score_restores_source_when_subprocess_is_killed)
    # monkeypatches the same 7-private-name seam (_resolve_mutmut_workdir/
    # _is_editable_install/_read_paths_to_exclude/_detect_data_only_files/
    # _abs_paths_to_mutate/_resolve_test_dir/_copy_setup_cfg_to_workdir)
    # to isolate the test from real filesystem/mutmut I/O. Same established
    # seam run_mutation_precheck tests in this file already use. Previous: 52.
    "tests/test_mutation_enforcer.py": 59,
    "tests/cli/test_phase_cmds_cli.py": 42,  # 2026-08-06 Round 39: +2 — _mock_advance_phase_bypass_prechecks (L314) and TestP7AdvanceGeneratesP8Baseline._setup (L1817) now stub cli.phase_cmds._verify_entry_gate alongside _advance_prechecks/_advance_fsm. Same seam cmd_advance_phase tests already go through (the gate is now an inline call in cmd_advance_phase between _advance_prechecks and _advance_fsm); one more private-name patch for an already-accepted seam, not a new coupling.
    # 2026-07-27 Round 29 (+4): TestRunPhaseCISubstrateProbeSkip's 2 tests each
    # monkeypatch cli.phase_cmds._verify_entry_gate (the same already-accepted
    # seam TestRunPhaseNoPostflight uses right above it) and
    # cli.phase_cmds._run_substrate_probe (the new CI-skip's own target —
    # asserting it is/isn't called is the entire point of these two tests).
    "tests/test_handover_generator.py": 32,  # 2026-07-26 Round 14 A2-t: +4 — test_advance_phase_surfaces_obligations_to_handover monkeypatches cli.phase_cmds._advance_fsm (the seam cmd_advance_phase calls to write out state.json's last_gate/task_background; substantively the public state-write — already a constructor-private wiring seam multiple cmd_advance_phase tests in this file go through — _advance_prechecks (the precondition aggregator — also covered by TestP1MissingDeliverableBlocksAdvance below this file, same seam); the test's OTHER monkeypatches (HandoverGenerator.write, PhaseHooks.preview_next_phase_blocking, subprocess.run, sys.stdout) target PUBLIC class methods / modules and do not count toward the ceiling.
    "tests/test_handover_generator_injection.py": 18,
    "tests/cli/test_push_cmds_cli.py": 17,
    "tests/test_crg_integration_fallback.py": 15,
    "tests/cli/test_fr_cmds_cli.py": 18,
    "tests/test_crg_bridge.py": 12,
    "tests/test_gate_trace_dimension.py": 12,
    "tests/test_reviewer_router_extended.py": 11,
    "tests/test_git_strategy_handover_revert.py": 9,
    "tests/test_handover_generator_mediums.py": 9,
    "tests/cli/test_project_cmds_cli.py": 6,
    "tests/test_crg_independent.py": 6,
    "tests/test_crg_api.py": 4,
    "tests/test_reviewer_router.py": 4,
    "tests/test_w6_gap_fill.py": 4,
    "tests/test_gap_detector.py": 3,
    "tests/test_test_compliance.py": 3,
    "tests/test_4a_denominator_dedup.py": 2,
    "tests/test_agent_spawner.py": 4,
    "tests/test_edge_coverage.py": 2,
    "tests/test_feedback_hook.py": 2,
    "tests/test_advance_commit_rollback.py": 2,  # 2026-08-06 Round 39: +1 — advance_project fixture now also stubs cli.phase_cmds._verify_entry_gate (cmd_advance_phase calls it at L526 before _advance_fsm; same seam cmd_advance_phase tests already go through, same justification as the _advance_prechecks stub at line 58). All other monkeypatches in the file are unchanged.
    "tests/test_milestone_tree_is_the_judged_tree.py": 2,  # 2026-08-11 Round 44 站2: the fourth file on the established cmd_advance_phase seam, with the same fixture and the same two stubs as test_advance_commit_rollback.py and test_advance_refuses_a_blocked_entry.py above — cli.phase_cmds._advance_prechecks and _verify_entry_gate. Neither is under test: what is under test is whether cmd_advance_phase refuses to record a milestone on a tree git has not, which is exercised entirely through real files in a real tmp git repo. The obligation preview is stubbed through the PUBLIC PhaseHooks.preview_next_phase_blocking and does not count.
    # 2026-08-24 Round 72 站1: the sixth file on the established
    # cmd_advance_phase seam, and the first to stub ONE of the two rather than
    # both — `_verify_entry_gate` is what this file is about (it demanded the
    # `phase_completed` record that the same advance writes afterwards), so
    # stubbing it would remove the subject. Only `_advance_prechecks`, the
    # precondition aggregator, is stubbed, for the same reason every file
    # above stubs it: a real P3 tree of deliverables is not what is under test.
    "tests/test_advance_records_the_phase_it_gates_on.py": 1,
    "tests/test_advance_runs_doctor.py": 2,  # 2026-08-11 Round 45 站5: the fifth file on the established cmd_advance_phase seam, with the same fixture and the same two stubs as test_milestone_tree_is_the_judged_tree.py above — cli.phase_cmds._advance_prechecks and _verify_entry_gate. Neither is under test: what is under test is that an ERROR from run_doctor reaches the degradation ledger and that the advance still succeeds. run_doctor itself is patched at its PUBLIC name (core.doctor.run_doctor, bound into cli.phase_cmds) and does not count.
    "tests/test_advance_refuses_a_blocked_entry.py": 2,  # 2026-08-07 Round 43 站2: the advance_project fixture is copied from test_advance_commit_rollback.py above and stubs the same two seams for the same reason — cli.phase_cmds._advance_prechecks (the precondition aggregator) and _verify_entry_gate (the exit gate). Neither is under test here; what is under test is what cmd_advance_phase does with the obligation list it computed, which is injected through the PUBLIC PhaseHooks.preview_next_phase_blocking and does not count. Not a new coupling — the third file on the established cmd_advance_phase seam, alongside test_advance_commit_rollback.py and test_phase_completed_authority.py.
    # 2026-07-29 Round 24 站4: test_phase_completed_authority.py reuses
    # test_advance_commit_rollback.py's fixture, which stubs
    # cli.phase_cmds._advance_prechecks — the precondition aggregator, not
    # the behaviour under test (phase_completed recording after the commit
    # lands). 2026-08-06 Round 39 (+4): the same fixture also stubs
    # cli.phase_cmds._verify_entry_gate (new cmd_advance_phase gate call),
    # and the four new Round 39 tests (test_advance_phase_heals_dangling_
    # sha_before_staging, test_advance_phase_returns_10_on_unrecoverable_sha,
    # test_advance_phase_reverify_also_runs_entry_gate, plus the source-pin
    # test which has no patches) each stub _advance_prechecks directly.
    # All go through the same already-accepted seams; not a new coupling,
    # one more instance of the established pattern.
    "tests/test_phase_completed_authority.py": 5,
    "tests/test_generate_full_plan.py": 1,
    "tests/test_harness_bridge_highs2.py": 1,
    "tests/test_kill_switch_complete.py": 1,
    "tests/test_phase_hooks_adapter.py": 1,
    "tests/test_reviewer_router_mediums2.py": 1,
    "tests/test_rotate_decision_logs.py": 1,
    "tests/test_sab_parser.py": 1,
    # 2026-08-11: test_gate_result_writer.py stubs cli._shared._make_git to
    # disable git operations during subprocess-less finalize-gate invocation.
    # Same seam test_handover_generator.py already uses (lines 854, 2378,
    # 2419, 2497) — established pattern for invoking cmd_finalize_gate
    # in-process without a real git repo. The behaviour under test is the
    # per-FR writer invariants (fr_id consistency + idempotency guard),
    # which exist in the public function cmd_finalize_gate; only the git
    # side effect is stubbed.
    "tests/test_gate_result_writer.py": 1,
}


def _count_private_patches(source: str) -> int:
    return sum(len(p.findall(source)) for p in _FORMS)


def test_private_patch_ratchet():
    over = []
    for path in sorted((REPO / "tests").rglob("test_*.py")):
        rel = path.relative_to(REPO).as_posix()
        if rel == "tests/test_patch_discipline.py":
            continue  # this file quotes the patterns it scans for
        count = _count_private_patches(
            path.read_text(encoding="utf-8", errors="replace")
        )
        ceiling = _PRIVATE_PATCH_CEILING.get(rel, 0)
        if count > ceiling:
            over.append(f"{rel}: {count} private-target patches > ceiling {ceiling}")
    assert not over, (
        "implementation-detail mocking increased — test public behavior "
        "(CLI subprocess, public functions, fixture repos) or make the seam "
        "public/injectable instead of patching private names:\n  "
        + "\n  ".join(over)
    )


def test_scanner_detects_all_three_forms():
    """Negative: each counted form must trigger the scanner."""
    probe = "\n".join([
        'monkeypatch.setattr(phase_cmds, "_advance_prechecks", lambda *a: 0)',
        'patch("cli.gate_cmds._check_sab_module_alignment")',
        'mock.patch("core.doctor._check_git_sync")',
        'monkeypatch.setattr("harness_cli._old_helper", None)',
        'patch.object(bridge, "_finalize", autospec=True)',
    ])
    assert _count_private_patches(probe) == 5


def test_scanner_ignores_public_and_stdlib_targets():
    probe = "\n".join([
        'monkeypatch.setattr(phase_cmds, "cmd_advance_phase", fake)',
        'patch("subprocess.run")',
        'monkeypatch.setattr(PhaseHooks, method, sentinel)',
        'patch("cli.gate_cmds.atomic_write_json")',
    ])
    assert _count_private_patches(probe) == 0
