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
    # raised 56 times and lowered never — the largest hotspot here. There is
    # no argparse surface for this module; the wiring check is `_source_of`'s
    # own assertion that every tracked name is still importable from
    # `harness.harness_bridge` after the move.
    #
    # Round 80 站8 tracked only the MODULE-LEVEL functions, and said why:
    # `_source_of` looked for a top-level `def`, so the 18 methods on
    # HarnessBridge were outside its reach, and 站8 moved none of them.
    # Round 82 站0 removed that limit and 站6 moves sixteen of them into a
    # mixin, so they are tracked here too, as `HarnessBridge._stage_*`. The
    # class is reached through this module either way; the body is read from
    # wherever the function itself lives.
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
        # Round 81 站8: the sixteen runs extracted out of `finalize_gate`.
        # Round 82 站6 moves them to harness/gate_stages.py as a mixin, which
        # is the only shape that keeps them byte-identical — a method's body
        # sits at two indent levels under any class, and dedenting it to a
        # module-level function would be a rewrite.
        "HarnessBridge._stage_shape_contract",
        "HarnessBridge._stage_infra_fail_pollution",
        "HarnessBridge._stage_persist_cited_evidence",
        "HarnessBridge._stage_tool_evidence",
        "HarnessBridge._stage_declared_constraints",
        "HarnessBridge._stage_coverage_denominator",
        "HarnessBridge._stage_required_artifacts",
        "HarnessBridge._stage_verify_target",
        "HarnessBridge._stage_s4_cross_validation",
        "HarnessBridge._stage_system_reach",
        "HarnessBridge._stage_spec_coverage_cap",
        "HarnessBridge._stage_absent_dimensions",
        "HarnessBridge._stage_stubbed_boundaries",
        "HarnessBridge._stage_dimension_thresholds",
        "HarnessBridge._stage_declared_absent",
        "HarnessBridge._stage_record_verdict",
        # Round 82 站5 moves the shared vocabulary the stages read into
        # harness/gate_result.py ahead of them, so the mixin's module never
        # has to import back into harness_bridge. Its four functions
        # (`framework_measured`, `declared_dimensions`, `measurement_scope`,
        # `s4_block_details`) are already tracked above — added by Round 80
        # 站8, and their fingerprints are what will prove that move too.
        # `DimResult`, `GateResult`, `GateBlockedError` and the
        # `SCORE_SOURCE_*` constants travel with them and are NOT fingerprinted
        # here: this mechanism reads `def`s only, so a class moved by hand is
        # covered by the ratchets and the import checks, not by this.
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
        # Round 81 站7: the seven runs extracted out of `cmd_advance_phase`.
        "_advance_step_refuse_phase_9", "_advance_step_refuse_uncommitted",
        "_advance_step_refuse_open_obligations", "_advance_step_run_fsm_transition",
        "_advance_step_seed_p8_archive", "_advance_step_write_next_plan_header",
        "_advance_step_commit_and_push",
    ),
    # Round 82 站0. Added BEFORE 站4 moves anything out, for the reason the
    # module docstring gives. Not every top-level def in the file — only the
    # thirteen names 站4 moves: the four `_frstep_*` runs Round 81 站9
    # extracted, and the two families they read, which have to travel with
    # them because the alternative is a new module importing back into
    # `cli.fr_cmds` and a cycle that resolves only by line order.
    #
    # `DISPATCH_STRUCTURALLY_BROKEN_EXIT_CODE` moves with them and is not
    # here: it is an assignment, and this mechanism fingerprints `def`s.
    # tests/test_exit_code_registry.py scans `cli/*.py` and keeps watching it.
    "cli.fr_cmds": (
        "_frstep_skip_if_already_done", "_frstep_route_dispatch_error",
        "_frstep_gate1_paper_trail", "_frstep_push_checkpoint",
        # read by `_frstep_route_dispatch_error`
        "_abort_dispatch_structurally_broken", "_is_connector_disabled_failure",
        "_reports_precondition_block", "_resolve_precondition_block",
        # read by `_frstep_skip_if_already_done` and `_frstep_gate1_paper_trail`
        "_fr_step_already_done", "_fr_step_lineage_boundary", "_fr_tests_say",
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

    `func_name` may be `Class.method`. Round 82 站0 added that. Before it, this
    resolved a name through the module and then looked for a TOP-LEVEL `def`,
    so every method was outside its reach — the note above the
    `harness.harness_bridge` entry said exactly that, in the same breath as
    "站8 moves none of them". Round 82 moves sixteen of them, and a byte guard
    that cannot see its subject is not a guard. The class is looked up on the
    façade module (the class stays put); the BODY is read from wherever the
    function itself now lives, which is the whole point.
    """
    import importlib

    mod = importlib.import_module(module_name)
    owner, _, attr = func_name.rpartition(".")

    container: object = mod
    if owner:
        container = getattr(mod, owner, None)
        assert container is not None, (
            f"{owner} is no longer importable from {module_name} — a split "
            f"moved the class itself, which is a different move than this "
            f"guard was written for"
        )

    obj = getattr(container, attr, None)
    assert obj is not None, (
        f"{func_name} is no longer importable from {module_name} — a split "
        f"moved it without leaving a re-export behind"
    )
    home = importlib.import_module(obj.__module__)
    home_file = getattr(home, "__file__", None)
    assert home_file, f"{obj.__module__} has no file on disk"
    src = Path(home_file).read_text(encoding="utf-8")

    # Where to look inside the home file. For a method that is the class the
    # function was DEFINED in — `__qualname__`, not the class it was reached
    # through — because a mixin's method is reached through the subclass and
    # defined in the base.
    body: "list[ast.stmt]" = ast.parse(src).body
    if owner:
        defining = getattr(obj, "__qualname__", "").split(".")[0]
        body = next(
            (n.body for n in body
             if isinstance(n, ast.ClassDef) and n.name == defining),
            [],
        )

    for node in body:
        if isinstance(node, ast.FunctionDef) and node.name == attr:
            segment = ast.get_source_segment(src, node)
            assert segment is not None
            return segment
    raise AssertionError(
        f"{func_name} not found as a def in {home.__file__} — it is importable "
        f"but its source is not where its own __module__/__qualname__ say"
    )


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


@pytest.fixture()
def synthetic_module(tmp_path, monkeypatch):
    """A real module on disk, importable under a throwaway name.

    Round 82 站0. `_source_of`'s method support is the only thing standing
    between the sixteen `_stage_*` methods and no byte guard at all, so it
    needs cases where it must FAIL — a silent "not found, skip" would
    fingerprint nothing and stay green forever.
    """
    import importlib.util
    import sys

    path = tmp_path / "synthetic_split_probe.py"
    path.write_text(
        "def loose():\n"
        "    return 0\n"
        "\n"
        "\n"
        "class Widget:\n"
        "    @staticmethod\n"
        "    def alpha():\n"
        "        return 1\n"
        "\n"
        "\n"
        "Widget.beta = loose\n",
        encoding="utf-8",
    )
    spec = importlib.util.spec_from_file_location("synthetic_split_probe", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "synthetic_split_probe", module)
    spec.loader.exec_module(module)
    return module


def test_a_method_body_is_read_from_the_class_that_defines_it(synthetic_module):
    """Positive control: the extension finds a method at all."""
    assert _source_of("synthetic_split_probe", "Widget.alpha") == (
        "def alpha():\n        return 1"
    )


def test_a_misspelled_method_is_an_error_not_a_silent_skip(synthetic_module):
    with pytest.raises(AssertionError, match="no longer importable"):
        _source_of("synthetic_split_probe", "Widget.alphaZ")


def test_a_method_whose_source_is_not_where_its_qualname_says_is_an_error(
    synthetic_module,
):
    """`Widget.beta` is importable and its `__qualname__` names no class.

    This is the shape a wiring mistake takes: the attribute resolves, so a
    guard that only checked `getattr` would fingerprint the wrong text or
    quietly pass. It must be loud instead.
    """
    with pytest.raises(AssertionError, match="not found as a def"):
        _source_of("synthetic_split_probe", "Widget.beta")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__]))
