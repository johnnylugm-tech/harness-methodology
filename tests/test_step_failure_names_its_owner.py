"""Round 72 站3 — a step failure whose class is known does not own nobody.

`core.agent_spawner._classify_dispatch_error` decides which of five classes a
failed `claude -p` is, and `_dispatch_error_entry` stamps TIMEOUT for the
wall-clock kill. `core/step_failure_memory.record_step_failure` writes that
label into the ledger row's `why` AND its `data.error_class` — and passed
`owner="unknown"` beside both.

Measured on taskq-new's `.methodology/degradations.jsonl`: 37 rows own nobody.
Twenty-six read "FR-NN GATE1-DELTA: INFRA" (eleven FRs in a row between 17:54
and 18:04 on 2026-08-23) and one reads TIMEOUT — on an FR whose budget
escalation a different writer had recorded as `owner=infra` one line earlier.

Round 48 站1 built the vocabulary so a halt would name whose tree has to
change. This writer had the answer in hand and filed it as unanswered — the
same shape as Round 43's "computed it and threw the answer away", and as
`_abort_dispatch_infra_or_harness_bug` before Round 70 站2.
"""

from __future__ import annotations

import json

import pytest

pytestmark = [pytest.mark.core]


def _rows(project) -> list[dict]:
    path = project / ".methodology" / "degradations.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _record(project, **result):
    from core.step_failure_memory import record_step_failure

    record_step_failure(project, "FR-01", "GATE1-DELTA", result, "treefp")


@pytest.mark.parametrize(
    "error_class,expected_owner",
    [
        ("INFRA", "infra"),             # the 26 taskq-new rows
        ("TIMEOUT", "infra"),           # the 27th
        ("STRUCTURAL", "infra"),        # same fact as exit 23
        ("INFRA_ERROR", "infra"),
        ("TURN_BUDGET", "infra"),
        ("HARNESS_BUG", "harness"),     # same fact as exit 70
        ("GATE1_BLOCKED", "project"),   # rc-2 blocked exits (Round 102 站2)
    ],
)
def test_the_row_owns_what_its_own_class_says(tmp_path, error_class, expected_owner):
    _record(tmp_path, error_class=error_class, output="")
    row = _rows(tmp_path)[-1]
    assert row["owner"] == expected_owner, (
        f"the row's why reads {row['why']!r} and its owner reads "
        f"{row['owner']!r}"
    )
    assert row["data"]["error_class"] == error_class


def test_an_unrecognised_class_still_owns_nobody(tmp_path):
    """EXECUTION_ERROR is what the classifier returns when nothing matched.

    Claiming an owner for it would be inventing one — the same reason exit
    code 1 is UNKNOWN in OWNER_BY_EXIT. Round 48's rule: UNKNOWN is never
    rounded down to PROJECT.
    """
    _record(tmp_path, error_class="EXECUTION_ERROR", output="")
    assert _rows(tmp_path)[-1]["owner"] == "unknown"


def test_every_class_the_dispatcher_can_produce_has_an_owner(tmp_path):
    """The table and the classifier are read off each other, not maintained
    in parallel: every literal `_classify_dispatch_error` can return, plus the
    TIMEOUT stamp beside it, must appear in OWNER_BY_ERROR_CLASS or be the
    documented UNKNOWN."""
    import inspect
    import re

    from core import agent_spawner
    from core.fault_owner import OWNER_BY_ERROR_CLASS

    src = inspect.getsource(agent_spawner._classify_dispatch_error)
    produced = set(re.findall(r'return "([A-Z_]+)"', src))
    produced.add("TIMEOUT")  # agent_spawner's own wall-clock stamp
    unowned = produced - set(OWNER_BY_ERROR_CLASS) - {"EXECUTION_ERROR"}
    assert not unowned, (
        f"_classify_dispatch_error can return {sorted(unowned)}, which "
        f"core.fault_owner.OWNER_BY_ERROR_CLASS does not decide — a ledger "
        f"row that owns nobody is the block Round 48 站1 could not route"
    )


def test_exit_25_carries_its_answer_in_the_number(tmp_path):
    """Round 70 站2's other half.

    That round gave HARNESS_BUG its own exit code (70), leaving
    `cli/fr_cmds.py::_abort_dispatch_infra_or_harness_bug` — the only producer
    of 25 — able to return it for INFRA alone. The fault table went on saying
    the number was ambiguous and kept a HARNESS_BUG discriminator for it, so
    the same block still classified as UNKNOWN without its message.
    """
    from core.fault_owner import DISCRIMINATED_EXITS, Owner, classify_fault

    assert 25 not in DISCRIMINATED_EXITS
    assert classify_fault(exit_code=25).owner == Owner.INFRA
    # And with the message alongside, which used to flip it to HARNESS.
    assert classify_fault(
        exit_code=25, text="[FATAL] FR-01 GATE1: HARNESS_BUG detected",
    ).owner == Owner.INFRA
