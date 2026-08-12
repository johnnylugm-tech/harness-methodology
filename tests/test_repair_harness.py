"""Round 48 站0 — the repair executor the crash-triage ticket never had.

`crash-triage --open-cr` files a CR-BUG into harness's own
`.methodology/change_requests/` and stops there; docs/ERROR_HANDLING.md states
outright that "a production run never calls --open-cr automatically". So the
one path that already identifies a harness defect ends at a ticket with no
executor — R43's shape, one layer up.

This round gives it one. These tests pin the four refusals that keep an
agent-authored fix from becoming a fix that only makes the checker quiet:

  1. a fix whose self-gate is not green is not pushed,
  2. a fix that edits a GENERATED workflow file is not committed,
  3. a fix that lowers a gate threshold or deletes a regression guard is
     not committed,
  4. a fix nobody could reproduce first is not a fix.

None of them is a matter of taste. Each one names an incident this repo has
already paid for.
"""

from __future__ import annotations

import pytest


# --------------------------------------------------------------------------
# 1. The self-gate decides whether a push may happen.
# --------------------------------------------------------------------------

def test_a_green_self_gate_allows_the_push():
    from core.harness_repair import SelfGateOutcome

    outcome = SelfGateOutcome(results={"pytest": True, "guards": True, "ruff": True,
                                       "workflowgen": True, "node": True, "sim": True})
    assert outcome.green is True
    assert outcome.blocking == []


def test_a_single_red_check_blocks_the_push_and_names_itself():
    """老闆's ruling for this round: 全套本地 gate 綠才 push.

    "Mostly green" is the shape that ships a regression to every consumer
    project at once, because harness is a submodule and main is what they
    all track.
    """
    from core.harness_repair import SelfGateOutcome

    outcome = SelfGateOutcome(results={"pytest": True, "guards": False, "ruff": True,
                                       "workflowgen": True, "node": True, "sim": True})
    assert outcome.green is False
    assert outcome.blocking == ["guards"]


def test_a_check_that_could_not_run_is_not_a_pass():
    """Round 30's rule: a check that could not run must not return the value
    that means "ran, clean". `None` is its own answer and it blocks."""
    from core.harness_repair import SelfGateOutcome

    outcome = SelfGateOutcome(results={"pytest": True, "guards": None, "ruff": True,
                                       "workflowgen": True, "node": True, "sim": True})
    assert outcome.green is False
    assert "guards" in outcome.blocking


def test_the_gate_covers_every_check_the_round_47_close_out_ran():
    """The six checks are the ones this repo actually closes a round with.
    Dropping one silently is how a repair starts shipping unregenerated
    workflows or an unregistered guard."""
    from core.harness_repair import SELF_GATE_CHECKS

    assert set(SELF_GATE_CHECKS) == {
        "pytest", "guards", "ruff", "workflowgen", "node", "sim"
    }


# --------------------------------------------------------------------------
# 2. A generated file may not be hand-edited — 883e9ca's lesson, enforced.
# --------------------------------------------------------------------------

def test_editing_a_generated_workflow_without_its_generator_is_refused():
    """883e9ca hand-edited four shipped `.claude/workflows/*.js` files. All 94
    workflowgen tests stayed green while `generate_workflows.py --check`
    reported 4/9 DRIFT, because the golden suite compares generator output
    against generator output and never opens the shipped directory.

    A repair agent handed a workflow bug will reach for the .js file first —
    it is the file the stack trace names. The refusal has to be at the commit,
    with the correct command in the message.
    """
    from core.harness_repair import generated_file_violations

    violations = generated_file_violations(
        [".claude/workflows/phase3-implementation.js", "docs/ERROR_HANDLING.md"]
    )
    assert violations
    assert "generate_workflows.py" in " ".join(violations)


def test_editing_the_generator_and_regenerating_is_allowed():
    from core.harness_repair import generated_file_violations

    assert generated_file_violations([
        "scripts/workflowgen/spec_phase3.py",
        ".claude/workflows/phase3-implementation.js",
        ".claude/workflows/run-all.js",
        "tests/golden/workflowgen/phase3.js",
    ]) == []


# --------------------------------------------------------------------------
# 3. A repair may fix a checker. It may not move the bar the checker enforces.
# --------------------------------------------------------------------------

def test_touching_a_gate_threshold_is_refused():
    """Round 38 站2 settled that the floor lives in harness/gate_configs/*.yaml
    and is the same one CI applies. A repair that edits it is not fixing a
    defect, it is lowering the bar for every project at once."""
    from core.harness_repair import forbidden_edit_violations

    violations = forbidden_edit_violations(["harness/gate_configs/gate3.yaml"])
    assert violations


def test_fixing_a_checker_is_allowed():
    """The point of the executor. R31/R32/R33/R45 were all defects INSIDE
    core/quality_gate — refusing to touch it would refuse every real repair."""
    from core.harness_repair import forbidden_edit_violations

    assert forbidden_edit_violations(["core/quality_gate/block_reason.py"]) == []


def test_deleting_a_regression_guard_is_refused(tmp_path):
    """Guards only ever go up. A repair that removes an entry is removing the
    memory of the incident that entry exists for."""
    from core.harness_repair import guard_count_violations

    before = "guards:\n- test: a\n- test: b\n- test: c\n"
    after = "guards:\n- test: a\n- test: b\n"
    assert guard_count_violations(before_text=before, after_text=after)
    assert guard_count_violations(before_text=before, after_text=before + "- test: d\n") == []


# --------------------------------------------------------------------------
# 4. Reality and root cause, in that order.
# --------------------------------------------------------------------------

def test_a_failure_that_will_not_reproduce_is_not_repaired():
    """老闆's first requirement: 驗證問題的真實性. A report is a claim; the
    reproduction is the evidence. R33's incident is the cost of skipping it —
    two rounds fixed a symptom whose premise ("the file is missing") was
    false, and the class survived both."""
    from core.harness_repair import RepairPreconditions

    pre = RepairPreconditions(reproduced=False, counterproof_red=False)
    assert pre.may_fix is False
    assert "reproduc" in pre.refusal.lower()


def test_a_fix_whose_revert_stays_green_is_not_a_root_cause():
    """老闆's second requirement: 根源性. If undoing the change leaves the
    reproduction passing, the change did not cause the pass — something else
    did, and the defect is still there under a different name."""
    from core.harness_repair import RepairPreconditions

    pre = RepairPreconditions(reproduced=True, counterproof_red=False)
    assert pre.may_commit is False
    assert "counter" in pre.refusal.lower() or "revert" in pre.refusal.lower()


def test_reproduced_and_counterproved_may_proceed():
    from core.harness_repair import RepairPreconditions

    pre = RepairPreconditions(reproduced=True, counterproof_red=True)
    assert pre.may_fix is True
    assert pre.may_commit is True
    assert pre.refusal == ""


# --------------------------------------------------------------------------
# 5. The submodule the repair happens in is not always on a branch.
# --------------------------------------------------------------------------

def test_a_detached_head_must_be_checked_out_before_committing():
    """Measured 2026-08-12 across the six live projects: taskq-plus and
    taskq-renew both carry the harness submodule on a detached HEAD. A commit
    made there is reachable from nothing — which is exactly how Round 29/30
    lost the enforcer_sha 01bb3bb4 that eight gate results still name."""
    from core.harness_repair import checkout_plan

    plan = checkout_plan(current_branch="HEAD", dirty_paths=[])
    assert plan.must_checkout is True
    assert plan.target_branch == "main"
    assert plan.refusal == ""


def test_uncommitted_submodule_edits_block_before_anything_is_touched():
    from core.harness_repair import checkout_plan

    plan = checkout_plan(current_branch="HEAD", dirty_paths=["core/doctor.py"])
    assert plan.refusal
    assert "core/doctor.py" in plan.refusal


def test_already_on_main_needs_no_checkout():
    from core.harness_repair import checkout_plan

    plan = checkout_plan(current_branch="main", dirty_paths=[])
    assert plan.must_checkout is False
    assert plan.refusal == ""


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__]))
