"""A phase may not be entered while the previous one left no record (Round 53 站0).

`state.json.phase_completed[N]` is where Round 24 站4a, Round 26 and Round 44
站2 each put a fact nobody else records: the SHA at which phase N completed,
the enforcer that judged it, and `delivered_tree_sha256` — *which tree* the
checks read. `doctor`'s verdict re-derivation and
`cli/fr_cmds.py::_fr_step_lineage_boundary` both read it.

taskq-super reached Phase 9 with entries for 1, 2, 3, 4, 6, 7 — **no 5**. The
git history of that one file says how (each row is the first commit at which
the value changed):

    ed0a32c9 cp=5 pc=[1,2,3]        handover: advance to Phase 5
    4fadb841 cp=5 pc=[1,2,3,4]      chore: phase 4 clean-up
    2484d0e0 cp=6 pc=[1,2,3,4]      handover: advance to Phase 6
    279da09d cp=7 pc=[1,2,3,4]      handover: advance to Phase 7
    eff49a53 cp=7 pc=[1,2,3,4,6]    feat(FR-01): Gate1 PASS [phase=7]

Two facts, and the second is the one that matters.

First, the record for phase N is *never* inside the commit that completes
phase N. `cmd_advance_phase` makes the handover commit and only then writes
`phase_completed[N]`, because the entry's `sha` is HEAD **after** that commit —
so the value rides along in whatever happens to commit `state.json` next. Every
phase shows the one-commit lag.

Second, phase 5's write never landed at all, and nothing objected. The write
is `load_state` → mutate → `atomic_write_json` under a lock held only for that
window, so any other whole-document writer that loaded before it and wrote
after silently drops the key. The run has a hand-made state edit in exactly
that window (`ecfac3f phase6(state): restore last_gate=4 after Gate 4 release`),
which is a candidate rather than a proof. What is certain is that the loss left
no `advance-phase` row in the degradation ledger, because the write reported
success — it was undone later.

Nothing downstream noticed, because `_verify_entry_gate` reads
`phase_completed` only for phases 2 and 3. From phase 4 on it asks
`quality_manifest.json` whether the previous gate passed and nothing else, so
a missing record cannot stop anything. That is the gap this closes: the record
whose whole purpose is to say which tree was judged must exist before the next
phase may be entered.

The ordering is left alone deliberately. Writing before the commit would mean
the entry could not carry that commit's own SHA, which is the one thing every
consumer of it uses (`git merge-base --is-ancestor <sha> HEAD`). The precondition
catches the loss one phase later instead of preventing it — later than ideal,
and still the difference between a project that stops and a project that reaches
Phase 9 missing a record.
"""

from __future__ import annotations

import json
from pathlib import Path


def _project(tmp_path: Path, *, completed: dict) -> Path:
    project = tmp_path / "proj"
    (project / ".methodology").mkdir(parents=True)
    (project / ".methodology" / "state.json").write_text(
        json.dumps({"current_phase": 5, "phase_completed": completed}),
        encoding="utf-8",
    )
    (project / ".methodology" / "quality_manifest.json").write_text(
        json.dumps({"gate_results": {
            "gate2": {"quality_complete": True},
            "gate3": {"quality_complete": True},
            "gate4": {"quality_complete": True},
        }}),
        encoding="utf-8",
    )
    return project


def test_entering_a_phase_requires_the_previous_phase_to_have_left_a_record(
    tmp_path: Path,
) -> None:
    """Phase 6 may not be entered when `phase_completed[5]` is absent."""
    from cli.phase_cmds import _verify_entry_gate

    project = _project(tmp_path, completed={
        "1": {"sha": "a" * 40}, "2": {"sha": "b" * 40},
        "3": {"sha": "c" * 40}, "4": {"sha": "d" * 40},
    })

    verdict = _verify_entry_gate(project, 6)

    assert verdict["passed"] is False, (
        "taskq-super entered phase 6 with no record that phase 5 completed, "
        "and reached phase 9 that way"
    )
    assert "phase_completed" in verdict["reason"] and "5" in verdict["reason"], (
        "the refusal must name the missing record so it can be repaired"
    )


def test_a_phase_whose_record_exists_still_passes_its_gate_check(
    tmp_path: Path,
) -> None:
    """The positive control: a check that can only say no is not a check.

    Round 52 站0's rule, applied to its own station — the same six-project
    control group that caught two of my own defects caught them by running the
    passing case too.
    """
    from cli.phase_cmds import _verify_entry_gate

    project = _project(tmp_path, completed={
        "1": {"sha": "a" * 40}, "2": {"sha": "b" * 40},
        "3": {"sha": "c" * 40}, "4": {"sha": "d" * 40},
        "5": {"sha": "e" * 40},
    })

    verdict = _verify_entry_gate(project, 6)

    assert verdict["passed"] is True, verdict["reason"]
