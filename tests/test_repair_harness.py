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

import argparse
import json
import subprocess

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


# --------------------------------------------------------------------------
# 6. The command itself — the refusals reached through argparse, not the
#    predicates in isolation. A policy that only exists in a helper nobody
#    calls is the shape this whole round is about.
# --------------------------------------------------------------------------

def _harness_repo(tmp_path, *, guards="guards:\n- test: a\n- test: b\n- test: c\n"):
    """A minimal harness checkout mounted the way every live project mounts it."""
    root = tmp_path / "harness"
    (root / ".claude" / "workflows").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "scripts" / "workflowgen").mkdir(parents=True)
    (tmp_path / ".methodology").mkdir()
    (root / "harness_cli.py").write_text("", encoding="utf-8")
    (root / "tests" / "REGRESSION_GUARDS.yaml").write_text(guards, encoding="utf-8")
    (root / ".claude" / "workflows" / "phase1-requirements.js").write_text(
        "console.log(1)\n", encoding="utf-8")
    (root / "scripts" / "workflowgen" / "spec_phase1.py").write_text(
        "x = 1\n", encoding="utf-8")
    # `-b main` explicitly: a bare `git init` takes its branch name from
    # ambient `init.defaultBranch`, and this fixture's verdicts depend on that
    # name. Measured 2026-08-12 — CI run 31613445606 printed "HEAD was at
    # master" while this machine's git creates `main`, so two `--check-repro`
    # tests passed locally and failed on the runner. A test whose result is
    # decided by the host's git config is not measuring the code.
    for argv in (["init", "-q", "-b", "main", "."], ["config", "user.email", "b@e.com"],
                 ["config", "user.name", "Bot"], ["add", "-A"],
                 ["commit", "-q", "-m", "init"]):
        subprocess.run(["git", "-C", str(root), *argv], check=True,
                       capture_output=True)
    return root


def _ticket(tmp_path, **overrides):
    payload = {"signature": "sha256:abc", "phase": 3, "step": "Gate 2",
               "message": "crg_independent_failed", "repro": "exit 1"}
    payload.update(overrides)
    path = tmp_path / "ticket.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _run(tmp_path, ticket, *flags):
    from cli.repair_cmds import cmd_repair_harness

    args = argparse.Namespace(project=str(tmp_path), ticket=str(ticket),
                              check_repro=False, land=False, push=False)
    for flag in flags:
        setattr(args, flag, True)
    return cmd_repair_harness(args)


def test_cli_refuses_a_guard_deletion_before_it_runs_anything_expensive(tmp_path):
    _harness_repo(tmp_path)
    (tmp_path / "harness" / "tests" / "REGRESSION_GUARDS.yaml").write_text(
        "guards:\n- test: a\n- test: b\n", encoding="utf-8")
    assert _run(tmp_path, _ticket(tmp_path), "land", "push") == 1


def test_cli_refuses_a_generated_file_edited_without_its_generator(tmp_path):
    root = _harness_repo(tmp_path)
    (root / ".claude" / "workflows" / "phase1-requirements.js").write_text(
        "console.log(2)\n", encoding="utf-8")
    assert _run(tmp_path, _ticket(tmp_path), "land") == 1


def test_cli_refuses_when_the_failure_does_not_reproduce(tmp_path):
    _harness_repo(tmp_path)
    ticket = _ticket(tmp_path, repro="exit 0")
    assert _run(tmp_path, ticket, "check_repro") == 1


def test_cli_confirms_a_failure_that_does_reproduce(tmp_path):
    _harness_repo(tmp_path)
    assert _run(tmp_path, _ticket(tmp_path), "check_repro") == 0


def test_cli_requires_exactly_one_phase_flag(tmp_path):
    """Neither flag must not silently land. Both is a contradiction."""
    _harness_repo(tmp_path)
    ticket = _ticket(tmp_path)
    assert _run(tmp_path, ticket) == 1
    assert _run(tmp_path, ticket, "check_repro", "land") == 1


def test_cli_refuses_a_ticket_with_no_reproduction_command(tmp_path):
    _harness_repo(tmp_path)
    path = tmp_path / "ticket.json"
    path.write_text(json.dumps({"signature": "sha256:abc"}), encoding="utf-8")
    assert _run(tmp_path, path, "check_repro") == 1


# --------------------------------------------------------------------------
# 7. The tree the self-gate measured must be the tree that gets committed.
#
#    The first version of this command ran the six checks and THEN checked out
#    main, so on the two live projects whose submodule sits on a detached HEAD
#    the checkout swapped the working tree — carrying the fix across — and the
#    commit landed on a tree no check had ever seen. Round 44's shape (被判定的
#    樹 vs 被記錄的樹) inside the repair that this round shipped. Branch
#    normalisation belongs BEFORE the edit, when the tree is still clean.
# --------------------------------------------------------------------------

def _detach(root):
    subprocess.run(["git", "-C", str(root), "checkout", "--detach", "-q"],
                   check=True, capture_output=True)


def _head_sha(root):
    return subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                          capture_output=True, text=True).stdout.strip()


def _branch(root):
    return subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True, text=True).stdout.strip()


def test_land_refuses_a_detached_head_rather_than_checking_out_after_the_gate(
        tmp_path, capsys):
    root = _harness_repo(tmp_path)
    _detach(root)
    before = _head_sha(root)
    (root / "harness_cli.py").write_text("# the fix\n", encoding="utf-8")

    assert _run(tmp_path, _ticket(tmp_path), "land") == 1
    err = capsys.readouterr().err
    assert "detached" in err.lower(), err
    assert "--check-repro" in err, "the refusal must name the phase that fixes it"
    assert _head_sha(root) == before, "nothing may be committed from a detached HEAD"


def test_check_repro_returns_the_submodule_to_main_while_the_tree_is_clean(tmp_path):
    root = _harness_repo(tmp_path)
    _detach(root)

    assert _run(tmp_path, _ticket(tmp_path), "check_repro") == 0
    assert _branch(root) == "main"


def test_check_repro_refuses_to_move_a_submodule_with_uncommitted_edits(
        tmp_path, capsys):
    """The live caller `checkout_plan`'s clobber refusal never had.

    Its only production call site passed a hard-coded empty dirty list, so the
    refusal could not fire — a guard whose executor was wired to nothing, which
    is the exact defect this round is about.
    """
    root = _harness_repo(tmp_path)
    _detach(root)
    (root / "harness_cli.py").write_text("# someone else's work\n", encoding="utf-8")

    assert _run(tmp_path, _ticket(tmp_path), "check_repro") == 1
    assert "harness_cli.py" in capsys.readouterr().err
    assert _branch(root) == "HEAD", "a refusal must not move HEAD"


def test_the_fixture_does_not_inherit_the_hosts_default_branch(tmp_path, monkeypatch):
    """Otherwise this file's verdicts are the host's, not the code's.

    CI run 31613445606: two `--check-repro` tests passed on the author's
    machine and failed on the runner, whose git creates `master`. The refusal
    printed "HEAD was at master" — the fixture had inherited
    `init.defaultBranch`. Asserting the branch is not enough, because on a
    main-defaulting host that assertion holds even with the bug; the config has
    to be injected for the guard to bite everywhere.
    """
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "init.defaultBranch")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", "master")

    root = _harness_repo(tmp_path)
    assert _branch(root) == "main"


def test_a_dirty_tree_already_on_main_needs_no_checkout_and_is_not_refused():
    """`--land` always runs on a dirty tree — that is its precondition. Only a
    checkout can clobber, so only a checkout justifies the refusal."""
    from core.harness_repair import checkout_plan

    plan = checkout_plan(current_branch="main", dirty_paths=["core/doctor.py"])
    assert plan.must_checkout is False
    assert plan.refusal == ""


# --------------------------------------------------------------------------
# 8. A rejected push says why it was rejected.
# --------------------------------------------------------------------------

def test_a_non_fast_forward_push_is_not_reported_as_a_network_problem():
    from core.harness_repair import push_failure_reason

    reason = push_failure_reason(
        " ! [rejected]        main -> main (fetch first)\n"
        "error: failed to push some refs to 'github.com:o/r.git'\n")
    assert "connectivity" not in reason.lower()
    assert "fetch" in reason.lower() or "behind" in reason.lower()


def test_an_unrecognised_push_failure_does_not_claim_to_know_the_cause():
    from core.harness_repair import push_failure_reason

    assert push_failure_reason("something nobody has seen before") == ""


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__]))
