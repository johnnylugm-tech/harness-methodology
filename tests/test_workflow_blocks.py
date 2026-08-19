"""Round 48 站0 — the one pipeline event nobody records is where the pipeline stopped.

`run-report` aggregates three sources: `.sessi-work/sessions_spawn.log`,
`.methodology/degradations.jsonl`, and the per-gate result files. A workflow
halt appears in none of them. It exists only as the JS return value, which
reaches the conversation and is then gone — so "this project blocks at P4
preflight every single time" is not a queryable fact about any project.

These tests pin the ledger's behaviour: a halt is recorded with the coordinate
that identifies it, an unresolved halt stays visible, and a relaunch that hits
the SAME coordinate is distinguishable from one that got past it. That last
one is what makes "the repair worked" checkable instead of claimed.
"""

from __future__ import annotations

import json

import pytest


@pytest.fixture()
def project(tmp_path):
    (tmp_path / ".methodology").mkdir(parents=True)
    return tmp_path


def _ledger(project):
    return project / ".methodology" / "workflow_blocks.jsonl"


def test_a_halt_is_recorded_with_its_coordinate(project):
    from core.fault_owner import Owner
    from core.workflow_blocks import record_block

    record_block(
        project,
        phase=4,
        step="Entry & Preflight",
        owner=Owner.UNKNOWN,
        message="Phase 4 preflight did not PASS in 3 orchestrator attempts",
    )

    rows = [json.loads(line) for line in _ledger(project).read_text().splitlines()]
    assert len(rows) == 1
    row = rows[0]
    assert row["phase"] == 4
    assert row["step"] == "Entry & Preflight"
    assert row["owner"] == Owner.UNKNOWN
    assert row["signature"], "a block must carry the coordinate that identifies it"
    assert row["resolved"] is False


def test_the_same_halt_twice_shares_one_signature(project):
    """Two runs blocking at the same place is the fact worth seeing. A
    signature that changed every time (a timestamp, a pid) would make the
    repeat invisible — the same defect Round 41 站3 paid for."""
    from core.fault_owner import Owner
    from core.workflow_blocks import read_blocks, record_block

    for _ in range(2):
        record_block(
            project,
            phase=4,
            step="Entry & Preflight",
            owner=Owner.UNKNOWN,
            message="Phase 4 preflight did not PASS in 3 orchestrator attempts",
        )
    signatures = {row["signature"] for row in read_blocks(project)}
    assert len(signatures) == 1


def test_a_different_step_is_a_different_block(project):
    from core.fault_owner import Owner
    from core.workflow_blocks import read_blocks, record_block

    record_block(project, phase=4, step="Entry & Preflight", owner=Owner.UNKNOWN, message="a")
    record_block(project, phase=4, step="Gate 3", owner=Owner.UNKNOWN, message="a")
    assert len({row["signature"] for row in read_blocks(project)}) == 2


def test_open_blocks_excludes_resolved_ones(project):
    from core.fault_owner import Owner
    from core.workflow_blocks import open_blocks, record_block, resolve_block

    sig = record_block(
        project, phase=4, step="Gate 3", owner=Owner.HARNESS, message="crg_independent_failed"
    )
    assert len(open_blocks(project)) == 1
    resolve_block(project, sig, resolution="repair-harness pushed 0123abc")
    assert open_blocks(project) == []


def test_doctor_warns_on_a_harness_owned_block_and_names_the_route(project):
    """The consumer that decides what happens next.

    A block the framework attributed to ITSELF must not be handed to a fix
    agent pointed at the project — Round 13 站2's routing rule, applied to the
    one signal that did not exist when that rule was written.
    """
    from core.doctor import _check_open_workflow_blocks
    from core.fault_owner import Owner
    from core.workflow_blocks import record_block

    record_block(project, phase=3, step="Gate 2", owner=Owner.HARNESS,
                 message="crg_independent_failed")
    findings = _check_open_workflow_blocks(project)
    assert len(findings) == 1
    assert findings[0].severity == "WARN"
    assert "not project quality failures" in findings[0].message
    assert "P3/Gate 2" in findings[0].message


def test_doctor_stays_quiet_about_blocks_it_does_not_own_the_route_for(project):
    """run-report lists every open block. doctor reports only the subset whose
    route differs, so the two readers do not print the same rows."""
    from core.doctor import _check_open_workflow_blocks
    from core.fault_owner import Owner
    from core.workflow_blocks import record_block

    record_block(project, phase=4, step="Gate 3", owner=Owner.PROJECT, message="x")
    record_block(project, phase=4, step="Preflight", owner=Owner.UNKNOWN, message="y")
    assert _check_open_workflow_blocks(project) == []


def test_a_block_that_returns_after_being_resolved_says_so(project):
    """Round 48 站5 — the re-run reconciliation.

    "The repair worked" is a claim. The check is whether the SAME coordinate
    comes back, and it is made at the moment it does, so a relaunch costs no
    extra dispatch to learn what only the ledger knows.
    """
    from core.fault_owner import Owner
    from core.workflow_blocks import read_blocks, record_block, resolve_block

    sig = record_block(project, phase=3, step="Gate 2", owner=Owner.HARNESS,
                       message="crg_independent_failed")
    resolve_block(project, sig, resolution="repair-harness pushed 0123abc")
    record_block(project, phase=3, step="Gate 2", owner=Owner.HARNESS,
                 message="crg_independent_failed")

    latest = read_blocks(project)[-1]
    assert latest["recurred_after_resolution"] is True
    assert "0123abc" in latest["previous_resolution"]


def test_a_first_sighting_is_not_a_recurrence(project):
    from core.fault_owner import Owner
    from core.workflow_blocks import read_blocks, record_block

    record_block(project, phase=3, step="Gate 2", owner=Owner.HARNESS, message="x")
    record_block(project, phase=3, step="Gate 2", owner=Owner.HARNESS, message="x")
    assert all(not row["recurred_after_resolution"] for row in read_blocks(project)), (
        "blocking twice without a repair in between is one unresolved block, "
        "not a repair that failed to hold"
    )


def test_a_recurrence_survives_a_second_record_of_the_same_block(project):
    """Round 64 站0 — recording a block twice must not erase that it returned.

    `recurred` is computed from the PRIOR row's `resolved` flag, and every
    reader (`open_blocks`, doctor, run-report) takes the LAST row per
    signature. So a second record of a block that had already come back
    reads its own unresolved predecessor, writes `recurred_after_resolution:
    false`, and last-write-wins turns doctor's ERROR into a WARN and drops
    run-report's `<- RETURNED AFTER A REPAIR`.

    Not hypothetical since 6e7942e: recordBlock's prompt no longer tells the
    agent to report a failed record-block "rather than retrying", so a retry
    that the first call had in fact completed writes the second row.
    """
    from core.fault_owner import Owner
    from core.workflow_blocks import open_blocks, read_blocks, record_block, resolve_block

    sig = record_block(project, phase=3, step="Gate 2", owner=Owner.HARNESS,
                       message="crg_independent_failed")
    resolve_block(project, sig, resolution="repair-harness pushed 0123abc")
    record_block(project, phase=3, step="Gate 2", owner=Owner.HARNESS,
                 message="crg_independent_failed")
    record_block(project, phase=3, step="Gate 2", owner=Owner.HARNESS,
                 message="crg_independent_failed")

    assert read_blocks(project)[-1]["recurred_after_resolution"] is True, (
        "the second record of a returned block reported it as a first sighting"
    )
    assert "0123abc" in read_blocks(project)[-1]["previous_resolution"], (
        "the repair that did not hold is no longer named, so the warning "
        "points at nothing"
    )
    assert open_blocks(project)[0]["recurred_after_resolution"] is True, (
        "every reader takes the latest row, so the recurrence is now invisible "
        "to doctor and run-report"
    )


def test_resolving_a_signature_that_was_never_recorded_is_refused(project):
    """A receipt for a block nobody recorded is a claim with no subject —
    Round 45's rule (a verdict outlives its proof) at the ledger layer."""
    from core.workflow_blocks import UnknownBlockError, resolve_block

    with pytest.raises(UnknownBlockError):
        resolve_block(project, "sha256:deadbeef", resolution="claimed fixed")
