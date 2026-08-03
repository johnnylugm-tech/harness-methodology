"""Round 32 站0/站6 — a milestone commit may only claim what its own registries hold.

Measured on a live P4 run:

    34235b6  11:30  feat(P4-pre-gate3): all 8 FR(s) Gate1 re-eval PASS; ready for Gate 3
    9807b22  11:40  feat(P4-pre-gate3): all 8 FR(s) Gate1 re-eval PASS; ready for Gate 3
    11:54    Gate 1 P4 FR-01 BLOCKED (tool_score_fabrication)

Two things are wrong at once.

1. The claim is false and nothing checked it. `.methodology/.gate1_scores.json`
   held only the key "3"; `gate_timestamps.jsonl` held no (phase=4, gate=1) row;
   `gate_results/gate1/` had not been written since P3. Zero of the eight
   "re-eval PASS" verdicts exist anywhere. The commit message is the only
   record, and it was generated from a count nobody took.

2. The same milestone was emitted twice, ten minutes apart. Round 20 站3
   removed the *empty* milestone commit; a repeated one is the neighbouring
   case at the same decision point.

Separately, the degradation ledger for that run contains four rows and they
are all the same row:

    run-fr-step:TDD-GREEN — max_turns escalated 40 -> 80    FR-03
    run-fr-step:TDD-GREEN — max_turns escalated 40 -> 80    FR-05
    run-fr-step:TDD-GREEN — max_turns escalated 40 -> 80    FR-06
    run-fr-step:TDD-GREEN — max_turns escalated 40 -> 80    FR-08

Half the FRs did not fit in the configured ceiling. The ledger recorded it
four times and nothing reads it, so the default stays 40 forever and the
escalation absorbs the cost silently. This round does not change the default
— it surfaces the number that would justify changing it.
"""
from __future__ import annotations

import json

import pytest

import harness_cli  # noqa: F401  entry-first load order

pytestmark = [pytest.mark.core]


@pytest.fixture()
def project(tmp_path):
    meth = tmp_path / ".methodology"
    meth.mkdir()
    (meth / "quality_manifest.json").write_text(
        json.dumps({"fr_ids": [f"FR-0{n}" for n in range(1, 5)]}), encoding="utf-8"
    )
    (meth / "state.json").write_text(
        json.dumps({"state": "RUNNING", "current_phase": 4, "language": "python"}),
        encoding="utf-8",
    )
    return tmp_path


# ── the claim ───────────────────────────────────────────────────────────

def test_a_gate1_sweep_claim_counts_the_registry_not_the_fr_list(project):
    """The measured failure: four FRs in the manifest, zero verdicts on
    record, and a message announcing that all four passed."""
    from core.quality_gate import gate1_evidence

    summary = gate1_evidence.gate1_phase_summary(project, phase=4)
    assert summary["recorded"] == [], summary
    assert sorted(summary["missing"]) == ["FR-01", "FR-02", "FR-03", "FR-04"], summary


def test_a_partial_sweep_names_the_gaps_instead_of_rounding_up(project):
    from core.quality_gate import gate1_evidence

    gate1_evidence.record_gate1_score(project, 4, "FR-01", 100.0)
    gate1_evidence.record_gate_timestamp(project, 4, 1, "FR-01")

    summary = gate1_evidence.gate1_phase_summary(project, phase=4)
    assert summary["recorded"] == ["FR-01"], summary
    assert sorted(summary["missing"]) == ["FR-02", "FR-03", "FR-04"], summary


def test_the_sweep_milestones_check_that_summary_before_committing():
    """`p3-post-gate2` already refuses to commit when its precondition does not
    hold (`_validate_p3_post_gate2_precondition`, cli/push_cmds.py:288). The
    two milestones that make the same all-FRs-passed claim — `p3-pre-gate2`
    and `p4-pre-gate3` — go straight to `git.commit_and_push_*` with nothing
    between them and the claim."""
    import inspect

    from cli import push_cmds

    src = inspect.getsource(push_cmds.cmd_push_milestone)
    idx = src.index('elif milestone_type == "p4-pre-gate3":')
    branch = src[idx: idx + 400]
    assert "gate1_phase_summary" in branch or "_validate_gate1_sweep" in branch, (
        "p4-pre-gate3 commits 'all N FR(s) Gate1 re-eval PASS' without reading "
        "the registry that would say whether any of them were recorded "
        "(measured: 8 claimed, 0 on record, one BLOCKED 14 minutes later)"
    )


# ── the duplicate ───────────────────────────────────────────────────────

def test_the_same_milestone_is_not_emitted_twice_at_the_same_head():
    """Round 20 站3 removed the empty milestone commit at this decision point.
    A second identical milestone against an unchanged HEAD is the same class:
    a commit that records nothing that happened."""
    import inspect

    from cli import push_cmds

    src = inspect.getsource(push_cmds)
    assert "already recorded at this HEAD" in src or "_milestone_already_recorded" in src, (
        "nothing prevents the same milestone type from being committed twice "
        "for the same phase against an unchanged HEAD (measured: 34235b6 and "
        "9807b22, ten minutes apart, byte-identical subject lines)"
    )


# ── the ledger nobody reads ─────────────────────────────────────────────

def test_turn_ceiling_escapes_reach_the_run_report(project, capsys):
    """`run-report` is the degradation ledger's consumer (Round 14 站1). The
    ceiling escapes are its highest-frequency entry and it does not count
    them."""
    import argparse

    from core.degradation_ledger import record_degradation

    for fr in ("FR-01", "FR-03"):
        record_degradation(
            project, "run-fr-step:TDD-GREEN",
            "max_turns escalated 40 -> 80",
            why=f"{fr} TDD-GREEN was cut off at its turn ceiling",
        )

    from cli import report_cmds

    rc = report_cmds.cmd_run_report(argparse.Namespace(project=str(project), json=True))
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    # Asserted against the parsed structure, not a dumped string: `tmp_path`
    # is named after the test function, so a substring check on json.dumps()
    # matches this metric's own name inside the "project" path and passes for
    # a reason that has nothing to do with the report. (It did, on the first
    # run of this file — the same shape of false green this round is about.)
    degradations = payload["degradations"]
    assert degradations.get("turn_ceiling_escapes") == 2, (
        "run-report does not surface how many steps had to escalate past their "
        "turn ceiling — the one number that would say whether the default is "
        f"set too low: {degradations}"
    )
