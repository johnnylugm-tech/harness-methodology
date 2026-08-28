"""The safety net for the R49-B god-file split, built before anything moves.

`cli/check_cmds.py` (1682 lines, 24 `cmd_*`) and `core/doctor.py` (923 lines,
14 `_check_*`) are being split into families, one commit per family. A split is
a MOVE: the same code, in a different file. Everything that makes that claim
checkable is here, and it is here FIRST — a net woven after the fall proves
nothing about the fall.

Two properties, because a move can fail in two different ways:

  the code changed      A function that was edited while being moved is a
                        rewrite wearing a refactor's name. Round 15 split
                        phase_specs 2985 -> 20 lines under a byte-equality
                        rule and Round 17 did the same for fr_cmds; this is
                        that rule for Python functions rather than generated
                        text.

  the wiring dropped    A command whose `register()` call did not follow it
                        into its new module still imports, still passes every
                        unit test, and is simply gone from the CLI. The
                        argparse surface is snapshotted whole — every
                        subcommand and every one of its option strings.

WHY A GOLDEN AND NOT A LINE COUNT

Both files are already in tests/test_file_size_ratchet.py, which will notice
the shrink. It cannot notice the difference between "moved" and "rewritten to
be shorter", and that difference is the entire risk.

REGENERATING

    REGEN_SPLIT_GOLDEN=1 python3 -m pytest tests/test_god_file_split_safety.py

Regenerate ONLY when a function is deliberately changed, in the same commit as
the change, and say so in the commit message. Regenerating to make a split
pass would delete the only evidence that it was a split.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
GOLDEN = REPO / "tests" / "golden" / "god_file_split" / "surface.json"

#: The functions under the microscope: every top-level def in the two files as
#: of the split's first commit. Names, not locations — locating them is the
#: test's job, and the point is that the location is allowed to change.
_TRACKED: dict[str, tuple[str, ...]] = {
    "cli.check_cmds": (
        "cmd_bug_hunt_targets", "cmd_spec_coverage_check", "cmd_verify_gate",
        "cmd_verify_ci", "cmd_crg_arch_check", "cmd_check_test_spec_consistency",
        "cmd_check_spec_alignment", "cmd_check_property_spec",
        "cmd_check_artifact_consistency", "cmd_check_manifest_integrity",
        "cmd_check_test_mirrors_spec", "cmd_manifest",
        "cmd_generate_verification_report", "cmd_verify_agent_b_approvals",
        "cmd_write_approval", "cmd_verify_file", "cmd_run_gap_analysis",
        "cmd_verify_spec", "cmd_migrate_trace_overlay",
        "cmd_build_trace_attestation", "cmd_verify_trace", "cmd_check_logic",
        "cmd_check_constitution", "cmd_print_legal_artifacts",
        "_generate_sab_json", "_resolve_deliverable_ids", "_run_gap_analysis",
        "_print_constitution_result",
    ),
    # Round 80 站8. 5051 lines, touched in 28 of this repo's 80 rounds, ratchet
    # raised 56 times and lowered never — the largest hotspot here. Only the 32
    # MODULE-LEVEL functions are tracked: `_source_of` resolves a name through
    # the module and then looks for a top-level `def`, so the 18 methods on
    # HarnessBridge are outside its reach, and 站8 moves none of them. There is
    # no argparse surface for this module; the wiring check is `_source_of`'s
    # own assertion that every tracked name is still importable from
    # `harness.harness_bridge` after the move.
    "harness.harness_bridge": (
        "_first_non_null", "path_escapes_root",
        "_atomic_write_gate_result", "na_is_framework_verified",
        "_mark_framework_na", "absent_declared_dimensions",
        "framework_measured", "composite_over", "declared_dimensions",
        "measurement_scope", "_override_traceability_dim_score",
        "_override_adversarial_review_dim_score",
        "_mutation_artifact_violations", "_validate_tool_content",
        "_crg_enrich_gate_findings", "_check_infra_fail_pollution",
        "_check_tool_evidence", "_check_tests_failed",
        "_parse_skip_counts", "_check_test_skip_ratio",
        "_architecture_regression_reason",
        "_mark_stubbed_boundary_dimensions",
        "_record_coverage_denominator", "_gate_dimension_names",
        "_verify_system_reach_block", "_run_harness_cross_validation",
        "s4_rescopes_to_fr", "per_fr_coverage_evidence",
        "s4_score_verdict", "s4_block_details", "_extract_fr_section",
        "_parse_spec_names_for_fr",
    ),
    # Round 80 站7. Added BEFORE the split, for the reason the module docstring
    # gives: a net woven after the fall proves nothing about the fall. 4233
    # lines, 47 top-level defs, ceiling raised 44 times across the history and
    # never lowered.
    "cli.phase_cmds": (
        "cmd_plan_phase", "cmd_plan_all", "_phase_gate_tools",
        "cmd_run_phase", "cmd_pre_commit_check", "cmd_preview_next_phase",
        "_regenerate_mutmut_scope", "_run_doctor_after_advance",
        "cmd_advance_phase", "cmd_generate_next_plan",
        "cmd_validate_handoff", "cmd_sync_harness",
        "_attestation_content_still_current", "_trace_dirty_state",
        "_run_fast_preflight", "_advance_commit_targets",
        "_git_head_short", "_uncommitted_deliverables",
        "_porcelain_paths", "_enforcer_moved_note", "_advance_fsm",
        "_run_substrate_probe", "_cmd_run_phase_impl",
        "_verify_entry_gate", "_check_ghost_paper_trail",
        "_advance_prechecks", "_validate_handoff_p1_to_p2",
        "_validate_handoff_p2_to_p3", "_validate_handoff_p3_to_p4",
        "_validate_handoff_p4_to_p5", "_validate_handoff_p5_to_p6",
        "_validate_handoff_p6_to_p7", "_validate_handoff_p7_to_p8",
        "_validate_handoff_p8_to_p9", "_validate_handoff",
        "_resolve_fr_ids_from_manifest", "_check_deferred_fixes_resolved",
        "_check_gate1_live_coverage", "_gate1_per_fr_coverage_verdict",
        "_check_gate_score_variance", "_regen_traceability_views",
        "_regen_and_stage_view", "_broken_deliverable_anchors",
        "_warn_if_view_lost_its_anchor", "_scope_violation_scripts",
        "_scope_debug_name_match", "register",
        # Round 81 站6: the nine runs extracted out of `_advance_prechecks`.
        # Tracked here so a later split moves them with their wiring; their
        # bodies are pinned against the pre-extraction file by
        # tests/test_extraction_moved_not_rewrote.py, which is the evidence
        # this golden cannot carry (it fingerprints, it does not compare).
        "_precheck_cleared_dir_evidence", "_precheck_backup_artifacts",
        "_precheck_manifest_and_p1_baselines",
        "_precheck_per_fr_gate1_and_phase_truth",
        "_precheck_early_stage_pass", "_precheck_deliverable_anchors",
        "_precheck_scope_violations", "_precheck_p3_security_and_quality",
        "_precheck_stage_pass_staging",
    ),
    "core.doctor": (
        "run_doctor", "_check_ci_template_drift",
        "_check_submodule_behind", "_check_enforcer_provenance",
        "_check_phase_verdict_staleness", "_check_milestone_tree_matches_verdict",
        "_enforcer_shas_in", "_check_heartbeat", "_check_spawn_log_authenticity",
        "_check_enforcement_zombie_keys", "_check_crash_bundles",
        "_check_open_workflow_blocks", "_check_gate1_evidence",
        "_check_testpaths_drift", "_phase_from_sentinel_name", "_check_git_sync",
    ),
}


def _source_of(module_name: str, func_name: str) -> str:
    """The function's source text, read from whichever file now defines it.

    Deliberately not `inspect.getsource`: it resolves through the object's
    line numbers against a cached file read, which has produced stale answers
    in this repo before. Going through `__module__` -> file -> AST asks the
    filesystem the same question the reviewer would.
    """
    import importlib

    mod = importlib.import_module(module_name)
    obj = getattr(mod, func_name, None)
    assert obj is not None, (
        f"{func_name} is no longer importable from {module_name} — a split "
        f"moved it without leaving a re-export behind"
    )
    home = importlib.import_module(obj.__module__)
    home_file = getattr(home, "__file__", None)
    assert home_file, f"{obj.__module__} has no file on disk"
    src = Path(home_file).read_text(encoding="utf-8")
    for node in ast.parse(src).body:
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            segment = ast.get_source_segment(src, node)
            assert segment is not None
            return segment
    raise AssertionError(f"{func_name} not found as a top-level def in {home.__file__}")


def _fingerprints() -> dict[str, str]:
    out: dict[str, str] = {}
    for module_name, names in _TRACKED.items():
        for name in names:
            body = _source_of(module_name, name)
            out[f"{module_name}::{name}"] = hashlib.sha256(
                body.encode("utf-8")
            ).hexdigest()[:16]
    return out


def _cli_surface() -> dict[str, list[str]]:
    """Every subcommand and its option strings, as argparse sees them."""
    import harness_cli

    parser = harness_cli.build_parser()
    surface: dict[str, list[str]] = {}
    for action in parser._actions:
        choices = getattr(action, "choices", None)
        if not isinstance(choices, dict):
            continue
        for name, subparser in choices.items():
            opts: list[str] = []
            for sub_action in subparser._actions:
                opts.extend(sub_action.option_strings or [f"<{sub_action.dest}>"])
            surface[name] = sorted(opts)
    return surface


def _snapshot() -> dict:
    return {"functions": _fingerprints(), "cli": _cli_surface()}


def _load_or_write(current: dict) -> dict:
    if os.environ.get("REGEN_SPLIT_GOLDEN"):
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_text(
            json.dumps(current, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    assert GOLDEN.exists(), (
        f"{GOLDEN.relative_to(REPO)} is missing — regenerate with "
        f"REGEN_SPLIT_GOLDEN=1"
    )
    return json.loads(GOLDEN.read_text(encoding="utf-8"))


def test_every_split_function_moved_without_being_rewritten():
    current = _snapshot()
    golden = _load_or_write(current)

    drifted = [
        key for key, digest in current["functions"].items()
        if golden["functions"].get(key) != digest
    ]
    missing = sorted(set(golden["functions"]) - set(current["functions"]))
    assert not drifted and not missing, (
        "a god-file split must MOVE code, not change it.\n"
        f"  source changed: {sorted(drifted)}\n"
        f"  disappeared:    {missing}\n"
        "If the change is deliberate, make it in its own commit and "
        "regenerate with REGEN_SPLIT_GOLDEN=1 — never in the same commit as a "
        "move, where the two become indistinguishable."
    )


def test_the_cli_surface_survives_the_split():
    """A command whose register() call did not follow it is simply gone."""
    current = _snapshot()
    golden = _load_or_write(current)

    lost = sorted(set(golden["cli"]) - set(current["cli"]))
    changed = sorted(
        name for name, opts in current["cli"].items()
        if name in golden["cli"] and golden["cli"][name] != opts
    )
    assert not lost and not changed, (
        "the CLI lost or altered a subcommand across the split.\n"
        f"  subcommands gone: {lost}\n"
        f"  options changed:  {changed}\n"
        "Every module that owns commands owns their register() too; the "
        "façade must call each one."
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__]))
