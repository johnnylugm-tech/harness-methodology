"""Round 114 站4 — halts are closed by the phase completing, and exit 25 has one owner.

taskq-open stopped eleven times on its way through P1-P8 and every phase was
eventually completed. `.methodology/workflow_blocks.jsonl` still lists all
eleven as open: `resolve_block` has one caller, `repair-harness`, and none of
those repairs went through it. `open_blocks` / `unattributed_open_blocks` /
doctor / run-report therefore read a finished project as eleven unanswered
halts, and `recurred_after_resolution` could never fire.

`advance-phase --completed N` is the moment the framework knows every halt in
phases <= N was passed, so that is where they are closed. The row also says
which harness enforced at the halt and at the pass. Replayed on taskq-open
(the enforcer reconstructed from the project's submodule pointer at each
halt): ten of eleven halts passed on a different harness, the one that did
not is FR-03's Gate 1 — the one the project fixed. That is evidence beside
the owner, never a rewrite of it.

Exit 25 is produced in one place (`_abort_dispatch_infra_or_harness_bug`),
and its INFRA branch is unreachable — both signatures it would match are
claimed first by UNREGISTERED. So 25 means code->SAB drift: the codebase has
a module the SAB does not declare. Its mirror, PHANTOM (45), is PROJECT; 25
was INFRA. taskq-open's sixteen `run-fr-step:GATE1` "infra" rows were the
undeclared `taskq_api.__main__`.
"""

from __future__ import annotations

import json
from pathlib import Path

from cli.phase_cmds import _advance_commit_targets
from core.workflow_blocks import open_blocks, read_blocks, record_block, resolve_completed_phase_blocks
from tests.test_advance_commit_rollback import _advance, _git, _install_rejecting_hook, advance_project  # noqa: F401

_LEDGER = ".methodology/workflow_blocks.jsonl"


def _block(project: Path, phase: int, step: str, enforcer: "str | None" = None) -> None:
    record_block(project, phase=phase, step=step, owner="unknown",
                 message=f"Phase {phase} {step} did not PASS")
    if enforcer is not None:
        path = project / _LEDGER
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        rows[-1]["enforcer_sha"] = enforcer
        path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def test_a_halt_records_the_enforcer_it_stopped_under(tmp_path, monkeypatch):
    import core.workflow_blocks as wb

    monkeypatch.setattr(wb, "current_enforcer", lambda: "abc123")
    record_block(tmp_path, phase=3, step="preflight", owner="unknown", message="x")
    assert read_blocks(tmp_path)[-1]["enforcer_sha"] == "abc123"


def test_completing_a_phase_closes_its_halts_and_no_later_ones(tmp_path):
    _block(tmp_path, 2, "advance-phase", enforcer="old")
    _block(tmp_path, 3, "preflight", enforcer="old")
    _block(tmp_path, 4, "gate3", enforcer="old")

    resolve_completed_phase_blocks(tmp_path, 3)

    assert [row["phase"] for row in open_blocks(tmp_path)] == [4]
    closed = [r for r in read_blocks(tmp_path) if r.get("resolved")]
    assert {r["phase"] for r in closed} == {2, 3}
    assert all(r["resolution"].startswith("phase 3 completed") for r in closed)


def test_the_closing_row_says_whether_the_harness_changed(tmp_path, monkeypatch):
    import core.workflow_blocks as wb

    monkeypatch.setattr(wb, "current_enforcer", lambda: "new")
    _block(tmp_path, 3, "preflight", enforcer="old")
    _block(tmp_path, 3, "gate1", enforcer="new")
    _block(tmp_path, 3, "env-check")
    # A row written before Round 114 carries no enforcer at all.
    path = tmp_path / _LEDGER
    data = [json.loads(line) for line in path.read_text().splitlines()]
    data[-1].pop("enforcer_sha", None)
    path.write_text("".join(json.dumps(r) + "\n" for r in data))

    resolve_completed_phase_blocks(tmp_path, 3)

    by_step = {r["step"]: r for r in read_blocks(tmp_path) if r.get("resolved")}
    assert by_step["preflight"]["harness_changed"] is True
    assert by_step["gate1"]["harness_changed"] is False
    assert by_step["env-check"]["harness_changed"] is None  # unknown, not guessed
    assert by_step["preflight"]["owner"] == "unknown", "the owner is never rewritten"


def test_the_ledger_is_staged_by_the_advance_commit_when_present():
    targets = _advance_commit_targets(
        1, 2, manifest_regenerated=False, fr_progress_exists=False,
        workflow_blocks_exists=True,
    )
    assert _LEDGER in targets


def test_an_advance_commits_the_closed_halt(advance_project):  # noqa: F811
    proj = advance_project
    _block(proj, 1, "review-escalation", enforcer="old")
    assert _advance(proj) == 0
    assert open_blocks(proj) == []
    at_head = _git(proj, "show", f"HEAD:{_LEDGER}").stdout
    assert '"resolved": true' in at_head


def test_a_failed_advance_closes_nothing(advance_project):  # noqa: F811
    proj = advance_project
    _block(proj, 1, "review-escalation", enforcer="old")
    _install_rejecting_hook(proj)
    assert _advance(proj) != 0
    assert [r["step"] for r in open_blocks(proj)] == ["review-escalation"]


def test_exit_25_is_the_projects_like_its_mirror_45():
    from core.fault_owner import Owner, classify_fault

    assert classify_fault(exit_code=25).owner == Owner.PROJECT
    assert classify_fault(exit_code=45).owner == Owner.PROJECT
