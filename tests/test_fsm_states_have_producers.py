"""Round 108 站A — a state nothing writes is not a state.

`core/fsm/fsm.py` accepts eight FSM values. Measured 2026-09-08 across the
fifteen corpus projects on this machine: every `.methodology/state.json`
carries `"state": "RUNNING"`, and `RUNNING` is the only value any code path
writes (`core/phase_hooks.py::preflight_fsm_check`'s auto-init and
`cli/project_cmds.py`'s init-project). The other seven have no producer.

That is not a tidiness complaint. Three things read them:

  * `core/phase_hooks.py::preflight_fsm_check` refuses to start a phase when
    the state is `FREEZE` or `PAUSED` — a block whose trigger no production
    code can create. The only things that have ever written `FREEZE` are five
    test fixtures (`test_e2e_cli.py`, `test_edge_coverage.py`,
    `test_bvs_runner.py`, `test_w6_gap_fill.py`, `test_fsm.py`).
  * `constitution/CONSTITUTION.md:251` and `SKILL.md:365` carry HR-14,
    "Integrity < 40 → FREEZE". Nothing writes `FREEZE`, and — measured the
    same day — nothing writes `state["integrity"]` either, so the rule was
    unreachable from both ends.
  * `docs/USER_MANUAL.md` §3.4 drew a lifecycle whose three main names
    (`INITIAL`, `ACTIVE`, `COMPLETE`) are not values this framework accepts
    at all; `ACTIVE` is the deprecated spelling `fsm.py` auto-corrects away.

WHY THE TABLE IS DECLARED AND NOT INFERRED

The first plan for this station was an AST scan that would derive the
producers. It was dropped because it was measured to be wrong: scanning for a
dict literal with a `"state"` key holding a string constant finds five sites,
and three of them are `preflight_fsm_check`'s RETURN envelope, which uses the
same key name for a different vocabulary (`UNKNOWN`, `CORRUPT` — neither is an
FSM state). And there is no single write path to scan: `core/state_io.py` is
documented as the single entry point for READING state.json, while writes are
spread across `phase_hooks`, `push_cmds`, `project_cmds`, `advance_commit` and
`phase_completed_recovery`. With nothing sound to infer from, the answer is
declared by the people who know it, and this file pins that the declaration
stays complete.

WHAT THIS FILE DOES NOT DO

It does not check that the producer strings are accurate — a declared table
can drift from the tree, and pretending otherwise would be a worse claim than
the honest one. It checks the property that actually failed: that a state can
be accepted, validated, documented and blocked on while no one has ever
written down whether anything creates it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.fsm.fsm import (
    STATE_PRODUCERS,
    VALID_FSM_STATES,
    _DEPRECATED_STATE_MAP,
)

pytestmark = [pytest.mark.core]

REPO = Path(__file__).resolve().parents[1]


def test_every_accepted_state_declares_whether_anything_writes_it() -> None:
    """The table and the accepted set are the same shape.

    A row may honestly say `None`. What it may not do is be absent — that
    absence is how seven of these came to be accepted, validated, documented
    and blocked on with nothing creating them.
    """
    undeclared = sorted(VALID_FSM_STATES - set(STATE_PRODUCERS))
    assert not undeclared, (
        f"these FSM states are accepted by validate_fsm_state but no one "
        f"recorded what writes them: {undeclared}. Add a row to "
        f"core/fsm/fsm.py::STATE_PRODUCERS — `None` plus the reason is a "
        f"legitimate answer, silence is not")

    stray = sorted(set(STATE_PRODUCERS) - VALID_FSM_STATES)
    assert not stray, (
        f"STATE_PRODUCERS names states validate_fsm_state would reject: "
        f"{stray}. A producer table for values nothing accepts is a third "
        f"vocabulary, which is the defect it exists to prevent")


def test_the_state_the_corpus_actually_carries_has_a_producer() -> None:
    """Reverse control. The table must not read as 'nothing produces anything'.

    Every corpus state.json says RUNNING; if this row could go `None` the
    test above would pass over a table that had stopped describing reality.
    """
    assert STATE_PRODUCERS["RUNNING"], (
        "RUNNING is the value every project on disk carries and the table "
        "says nothing writes it")


def test_a_deprecated_spelling_is_not_smuggled_in_as_a_producer_row() -> None:
    """`ACTIVE` is auto-corrected away, so it is not a state to declare."""
    for deprecated in _DEPRECATED_STATE_MAP:
        assert deprecated not in STATE_PRODUCERS, (
            f"{deprecated!r} is a deprecated spelling that "
            f"validate_fsm_state rewrites; a producer row for it would make "
            f"the table disagree with the validator")


def test_the_user_manual_no_longer_draws_states_that_do_not_exist() -> None:
    """§3.4 shipped `INITIAL → ACTIVE → … → COMPLETE` for the whole of this
    framework's life. None of those three is a value `fsm.py` accepts.

    Scoped to the §3.4 block rather than the whole file on purpose: a scan
    over every document for these words was tried first and produced three
    false accusations out of four findings — `SAD.md`'s line documenting the
    `ACTIVE → RUNNING` deprecation, and two "PHASE COMPLETE" / "P1 COMPLETE"
    lines that are about phases, not FSM states. A guard that misfires three
    times out of four is the defect this repository keeps closing (Round 46).
    """
    manual = (REPO / "docs" / "USER_MANUAL.md").read_text(encoding="utf-8")
    marker = "### 3.4 FSM States"
    assert marker in manual, "the section this test is about was renamed"
    start = manual.index(marker)
    end = manual.index("\n## ", start)
    section = manual[start:end]

    offenders = [w for w in ("INITIAL", "ACTIVE", "COMPLETE") if w in section]
    assert not offenders, (
        f"docs/USER_MANUAL.md §3.4 names {offenders} as FSM states. "
        f"validate_fsm_state accepts {sorted(VALID_FSM_STATES)} and rewrites "
        f"ACTIVE; a manual that tells the operator to look for a value the "
        f"framework never writes sends them to the wrong file")

    for state in sorted(VALID_FSM_STATES):
        if state in section:
            break
    else:  # pragma: no cover - only reachable if the section stops naming any
        pytest.fail("§3.4 no longer names a single real FSM state")
