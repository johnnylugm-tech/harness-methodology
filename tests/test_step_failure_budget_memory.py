"""Round 102 站2 — budget kills and blocked-human exits remembered across
processes.

`cmd_run_fr_step` doubles a step's turn ceiling / wall-clock budget once per
process (`_turn_budget_escalated` / `_wallclock_escalated`) — and then the
process exits. The next invocation starts with empty sets, so a step that
cannot finish inside its budget was re-escalated 50 -> 100 turns by every
process that touched it: taskq-done's FR-01 did it three times, three GATE1
invocations each burning a CODE-FIX sub-agent to its 51st turn, and the
cross-process refusal (`repeated_failure`, exit 36) never fired because the
escalation rows carried no signature at all.

The fix gives those failures a durable voice: every budget kill and every
blocked-human exit now writes a step-failure row with a CANONICAL
(empty-output) signature through `record_step_failure`, so the signature is
byte-stable per (FR, step, tree, class) no matter which process wrote it —
even when the session text each process printed differed. Two identical kills
against the same tree are then refused up front, before a third dispatch is
spent.

Fixture shape is tests/test_wallclock_escalation.py's: a real
`cmd_run_fr_step` over a real (git) project, with AgentSpawner patched at its
SOURCE module.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

import pytest

import harness_cli  # noqa: F401  entry-first load order
import cli.fr_cmds as fr_cmds  # noqa: E402
from cli.exit_codes import EX_STEP_REPEATED_FAILURE  # noqa: E402

pytestmark = [pytest.mark.core]


class _BudgetKillSpawner:
    """One fixed budget-kill failure class, counted."""

    def __init__(self, error_class: str, status: str, output: str):
        self.error_class = error_class
        self.status = status
        self.output = output
        self.calls = 0

    def spawn(self, **kwargs):
        self.calls += 1
        _ = kwargs
        return {
            "status": self.status, "error_class": self.error_class,
            "exit_code": 1, "output": self.output,
        }


def _project(tmp_path: Path) -> Path:
    """Enough project for run-fr-step's preflight to reach the dispatch loop."""
    for sub in ("03-development/tests", "03-development/src",
                "02-architecture", "01-requirements", ".methodology"):
        (tmp_path / sub).mkdir(parents=True, exist_ok=True)
    (tmp_path / ".methodology" / "quality_manifest.json").write_text(
        json.dumps({"fr_ids": ["FR-01"]}), encoding="utf-8"
    )
    (tmp_path / "01-requirements" / "SRS.md").write_text(
        "### FR-01: Widget\n\nMUST accept input.\n\n---\n", encoding="utf-8"
    )
    subprocess.run(["git", "init", "-q"], cwd=str(tmp_path), check=True)
    return tmp_path


def _run(project: Path, monkeypatch, spawner) -> int:
    """One `run-fr-step` invocation — i.e. one process, in production."""
    # `cmd_run_fr_step` does `from core.agent_spawner import AgentSpawner` inside
    # the function, so the name never lands in cli.fr_cmds' namespace — patch the
    # SOURCE module (same reasoning as tests/cli/test_fr_cmds_cli.py:2477).
    from core import agent_spawner as _core_spawner

    monkeypatch.setattr(_core_spawner, "AgentSpawner", lambda *_a, **_k: spawner)
    args = argparse.Namespace(
        phase=3, fr_id="FR-01", step="TDD-GREEN", project=str(project), src_dir=None,
        dry_run=False, strict=False, srs=None, timeout=100, max_turns=30,
        max_fix_rounds=3, no_mcp=False, no_push=True, prompt_file=None,
    )
    try:
        return fr_cmds.cmd_run_fr_step(args)
    except SystemExit as exc:  # pragma: no cover - defensive
        return int(exc.code or 0)


def _canonical_rows(project: Path, error_class: str,
                    step: "str | None" = None) -> list[dict]:
    """The Round 102 canonical rows for one class: LEDGER_WHAT rows whose
    signature matches the empty-output hash for that class. The dispatch loop
    ALSO writes text-variant real rows under the same class (line 521), so the
    canonical subset has to be recognised by its signature, not its class."""
    from core.step_failure_memory import LEDGER_WHAT, failure_signature

    expected = failure_signature({"error_class": error_class, "output": ""})
    ledger = project / ".methodology" / "degradations.jsonl"
    if not ledger.is_file():
        return []
    rows = []
    for line in ledger.read_text(encoding="utf-8").splitlines():
        entry = json.loads(line)
        data = entry.get("data") or {}
        if (entry.get("what") == LEDGER_WHAT
                and data.get("error_class") == error_class
                and data.get("signature") == expected
                and data.get("fr_id") == "FR-01"
                and (step is None or data.get("step") == step)):
            rows.append(data)
    return rows


def test_turn_budget_kills_are_refused_across_processes(tmp_path, monkeypatch):
    """Process 1 burns its in-process retries at the 50-turn ceiling; process 2
    must NOT buy the same kill a third time — even though the session text it
    would have printed differs (real rows vary; canonical rows do not)."""
    project = _project(tmp_path)

    first = _BudgetKillSpawner(
        "TURN_BUDGET", "ERROR", "subtype=error_max_turns reached 50 turns (run one)")
    rc1 = _run(project, monkeypatch, first)
    assert first.calls >= 2, "precondition: the in-process retry exhausted itself"
    assert rc1 != EX_STEP_REPEATED_FAILURE

    rows = _canonical_rows(project, "TURN_BUDGET", step="TDD-GREEN")
    assert len(rows) >= 2, (
        f"budget kills left no canonical step-failure rows: {rows}"
    )
    assert len({r["signature"] for r in rows}) == 1, (
        f"canonical rows must share one signature — the cross-process refusal "
        f"keys on it: {rows}"
    )

    second = _BudgetKillSpawner(
        "TURN_BUDGET", "ERROR",
        "subtype=error_max_turns reached 50 turns (run two, text differs)")
    rc2 = _run(project, monkeypatch, second)
    assert second.calls == 0, (
        f"dispatched {second.calls}x more at a budget kill the framework had "
        f"already seen twice on this exact tree — the taskq-done FR-01 shape "
        f"(three invocations, each re-escalating 50 -> 100)"
    )
    assert rc2 == EX_STEP_REPEATED_FAILURE, f"exit {rc2}, not a distinguishable code"


def test_wallclock_timeouts_are_refused_across_processes(tmp_path, monkeypatch):
    """Same rule for the wall-clock half of the budget."""
    project = _project(tmp_path)

    first = _BudgetKillSpawner(
        "TIMEOUT", "TIMEOUT", "Agent timed out after 600s (process one)")
    _run(project, monkeypatch, first)

    rows = _canonical_rows(project, "TIMEOUT", step="TDD-GREEN")
    assert len(rows) >= 2 and len({r["signature"] for r in rows}) == 1, (
        f"timeout kills left no single-signature canonical rows: {rows}"
    )

    second = _BudgetKillSpawner(
        "TIMEOUT", "TIMEOUT", "Agent timed out after 600s (process two)")
    rc2 = _run(project, monkeypatch, second)
    assert second.calls == 0, "a second timeout on the same tree was re-bought"
    assert rc2 == EX_STEP_REPEATED_FAILURE, f"exit {rc2}, not exit 36"


def test_a_tree_change_lifts_the_budget_kill_refusal(tmp_path, monkeypatch):
    """The refusal is not a permanent trap: repair anything and it lifts."""
    project = _project(tmp_path)
    _run(project, monkeypatch,
         _BudgetKillSpawner("TURN_BUDGET", "ERROR", "kill one"))
    refused = _BudgetKillSpawner("TURN_BUDGET", "ERROR", "kill two")
    rc = _run(project, monkeypatch, refused)
    assert refused.calls == 0 and rc == EX_STEP_REPEATED_FAILURE, \
        "precondition: the repeat is refused"

    (project / "03-development" / "src" / "fix.py").write_text(
        "VALUE = 1\n", encoding="utf-8"
    )
    third = _BudgetKillSpawner("TURN_BUDGET", "ERROR", "kill three")
    _run(project, monkeypatch, third)
    assert third.calls >= 1, (
        "the tree changed and the step was still refused — a repair has no "
        "way to prove itself"
    )


def test_gate1_blocked_rows_share_a_canonical_signature_and_own_the_project(
        tmp_path):
    """The rc-2 blocked-human exit (fix rounds exhausted) writes the same
    canonical shape; its owner is PROJECT — the block is decided against the
    project's own manifest and fix rounds."""
    from core.fault_owner import Owner
    from core.step_failure_memory import (
        record_step_failure, repeated_failure, tree_fingerprint,
    )

    project = _project(tmp_path)
    fp = tree_fingerprint(project)
    canonical = {"error_class": "GATE1_BLOCKED", "status": "ERROR", "output": ""}

    # Process 1 and process 2 both exhaust their fix rounds on an unchanged
    # tree — each writes its blocked exit the way cmd_run_fr_step now does.
    record_step_failure(project, "FR-01", "GATE1", canonical, fp)
    record_step_failure(project, "FR-01", "GATE1", canonical, fp)

    rows = _canonical_rows(project, "GATE1_BLOCKED", step="GATE1")
    assert len(rows) == 2
    assert rows[0]["signature"] == rows[1]["signature"], (
        "the blocked-human exit must be byte-stable across processes"
    )
    seen = repeated_failure(project, "FR-01", "GATE1", fp, 2)
    assert seen is not None and seen["seen"] == 2, (
        "repeated_failure did not recognise the two blocked exits"
    )

    from core.degradation_ledger import read_degradations

    owner = [e for e in read_degradations(project)
             if (e.get("data") or {}).get("error_class") == "GATE1_BLOCKED"][-1]
    assert owner.get("owner") == Owner.PROJECT, (
        f"a blocked gate is decided against the project's manifest — got "
        f"owner={owner.get('owner')}"
    )
