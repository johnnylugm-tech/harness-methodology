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
    "tests/test_harness_bridge.py": 72,
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
    "tests/test_mutation_enforcer.py": 46,
    "tests/cli/test_phase_cmds_cli.py": 40,
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
    "tests/test_advance_commit_rollback.py": 1,
    # 2026-07-29 Round 24 站4: test_phase_completed_authority.py reuses
    # test_advance_commit_rollback.py's fixture, which stubs
    # cli.phase_cmds._advance_prechecks — the precondition aggregator, not
    # the behaviour under test (phase_completed recording after the commit
    # lands). Same seam, same justification, same ceiling.
    "tests/test_phase_completed_authority.py": 1,
    "tests/test_generate_full_plan.py": 1,
    "tests/test_harness_bridge_highs2.py": 1,
    "tests/test_kill_switch_complete.py": 1,
    "tests/test_phase_hooks_adapter.py": 1,
    "tests/test_reviewer_router_mediums2.py": 1,
    "tests/test_rotate_decision_logs.py": 1,
    "tests/test_sab_parser.py": 1,
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
