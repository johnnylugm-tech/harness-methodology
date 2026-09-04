"""An unattributed halt is a state, not "not the framework's problem".

Round 96. `core/doctor_checks/ledgers.py` has one reader that routes a stopped
run to the harness repair workflow, and it asks `harness_owned_open_blocks` —
`row["owner"] == Owner.HARNESS`. Everything else is invisible to it.

Measured on taskq-final at the end of its P1-P8 run:

    open_blocks                 3
    harness_owned_open_blocks   0

    P5 verification-docs   owner=unknown   <- Round 92 proved this a harness bug
    P6 tag-and-advance     owner=unknown   <- Round 93 proved this a harness bug
    P8 gate1               owner=project

Two of the three were confirmed harness defects and fixed in this repository
within two days. The check whose whole purpose is to say "do not point a fix
agent at this project, run the repair workflow" never fired for either of them,
because `unknown` reads as "not ours" to the only predicate that looks.

`unknown` is the honest classification and stays. Round 92 refused to invent an
owner for the P5 halt on the grounds that the halt genuinely does not know one,
and Round 50 站4 kept `unknown` as an owner for the same reason: the
alternative is a guess wearing a decision's clothes. What it must not be is
silent — Round 50's other half is that `unknown` does not get permanence.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = [pytest.mark.core]


def _ledger(project: Path, rows: list[dict]) -> None:
    from core.workflow_blocks import LEDGER_RELPATH

    path = project / LEDGER_RELPATH
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps({
                "ts": 1788000000.0, "signature": f"sha256:{row['step']}",
                "exit_code": None, "message": f"{row['step']} did not PASS",
                "evidence": "no exit code, and the message matched no rule",
                "resolved": False, "recurred_after_resolution": False,
                **row,
            }) + "\n")


_TASKQ_FINAL = [
    {"phase": 5, "step": "verification-docs", "owner": "unknown"},
    {"phase": 6, "step": "tag-and-advance", "owner": "unknown"},
    {"phase": 8, "step": "gate1", "owner": "project"},
]


def test_unattributed_open_blocks_are_their_own_query(tmp_path):
    """Same layer as `harness_owned_open_blocks`, and for the same reason its
    docstring gives: a predicate every reader re-derives is how two readers end
    up disagreeing about what the word means."""
    from core.workflow_blocks import (
        harness_owned_open_blocks,
        open_blocks,
        unattributed_open_blocks,
    )

    _ledger(tmp_path, _TASKQ_FINAL)
    assert len(open_blocks(tmp_path)) == 3
    assert harness_owned_open_blocks(tmp_path) == []
    got = unattributed_open_blocks(tmp_path)
    assert [r["step"] for r in got] == ["verification-docs", "tag-and-advance"]


def test_doctor_says_a_halt_has_no_owner(tmp_path):
    """The finding that was missing for the two halts that most needed it."""
    from core.doctor_checks.ledgers import _check_open_workflow_blocks

    _ledger(tmp_path, _TASKQ_FINAL)
    findings = _check_open_workflow_blocks(tmp_path)
    unattributed = [f for f in findings if "no one has decided" in f.message]
    assert unattributed, (
        "doctor reports nothing about two halts nobody attributed — and on the "
        f"run this was measured from, both were harness bugs. Got: "
        f"{[f.message[:70] for f in findings]}"
    )
    message = unattributed[0].message
    assert "P5/verification-docs" in message and "P6/tag-and-advance" in message
    # Both routes named, neither chosen: the framework does not know which.
    assert "repair" in message.lower() and "project" in message.lower()


def test_it_does_not_guess_an_owner(tmp_path):
    """Round 92's adjudication survives: these rows stay `unknown`."""
    from core.doctor_checks.ledgers import _check_open_workflow_blocks
    from core.workflow_blocks import read_blocks

    _ledger(tmp_path, _TASKQ_FINAL)
    _check_open_workflow_blocks(tmp_path)
    owners = [r.get("owner") for r in read_blocks(tmp_path)]
    assert owners == ["unknown", "unknown", "project"]


def test_a_harness_owned_block_still_gets_the_repair_route(tmp_path):
    """Negative control: the existing finding is untouched, and the two are
    different sentences because they carry different instructions."""
    from core.doctor_checks.ledgers import _check_open_workflow_blocks

    _ledger(tmp_path, [{"phase": 4, "step": "gate3", "owner": "harness"}])
    findings = _check_open_workflow_blocks(tmp_path)
    assert findings and "harness-owned" in findings[0].message
    assert "no one has decided" not in findings[0].message


def test_a_project_owned_block_alone_produces_neither(tmp_path):
    """Negative control: a halt the framework attributed to the project is not
    doctor's business here — the project's own gate already said so."""
    from core.doctor_checks.ledgers import _check_open_workflow_blocks

    _ledger(tmp_path, [{"phase": 8, "step": "gate1", "owner": "project"}])
    assert _check_open_workflow_blocks(tmp_path) == []


def test_a_resolved_unattributed_block_is_not_reported(tmp_path):
    """`resolve_block` closes it; a closed halt is not an open question."""
    from core.doctor_checks.ledgers import _check_open_workflow_blocks
    from core.workflow_blocks import resolve_block

    _ledger(tmp_path, [{"phase": 5, "step": "verification-docs", "owner": "unknown"}])
    resolve_block(tmp_path, "sha256:verification-docs", resolution="fixed upstream")
    assert _check_open_workflow_blocks(tmp_path) == []
