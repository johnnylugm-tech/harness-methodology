"""Round 30 站5 — a wall-clock timeout escalates its budget, once.

Round 29 站5 made a timeout VISIBLE: `error_class: "TIMEOUT"` on the dispatch
record and a degradation-ledger line. It left the retry untouched — "retrying
with identical prompt", same ceiling.

taskq-advance's P3 shows what that buys. Of 18 failed dispatches, 12 were
600.0s timeouts; four of them landed consecutively on FR-02 at ten-minute
intervals, each re-dispatched into the same wall. Two hours of wall time whose
only product was the same failure four times.

The answer was already next door. `_note_turn_budget_kill` doubles a step's turn
ceiling once when the agent is cut off mid-work, and its ledger entry says
"re-dispatching at the same ceiling cannot finish what did not fit" — which is
equally true of seconds. Round 29 wrote the observability and not the response.

The bound matters as much as the escalation: once per step. A second timeout at
the doubled budget means the step genuinely cannot finish, and doubling forever
would turn one stuck FR into an unbounded run.
"""
from __future__ import annotations

import argparse
import json
import subprocess

import pytest

import harness_cli  # noqa: F401  entry-first load order
import cli.fr_cmds as fr_cmds  # noqa: E402

pytestmark = [pytest.mark.core]


class _RecordingSpawner:
    """Returns TIMEOUT for a fixed number of dispatches, recording each budget."""

    def __init__(self, timeouts: int):
        self.timeouts = timeouts
        self.budgets: list[int] = []

    def spawn(self, **kwargs):
        self.budgets.append(kwargs["task_timeout"])
        if len(self.budgets) <= self.timeouts:
            return {"status": "TIMEOUT", "error_class": "TIMEOUT",
                    "output": "Agent timed out after Ns"}
        return {"status": "complete", "output": "GATE1: PASS"}


def _project(tmp_path):
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


def _run(tmp_path, monkeypatch, spawner, step="TDD-GREEN"):
    project = _project(tmp_path)
    # `cmd_run_fr_step` does `from core.agent_spawner import AgentSpawner` inside
    # the function, so the name never lands in cli.fr_cmds' namespace — patch the
    # SOURCE module (same reasoning as tests/cli/test_fr_cmds_cli.py:2477).
    from core import agent_spawner as _core_spawner
    monkeypatch.setattr(_core_spawner, "AgentSpawner", lambda *_a, **_k: spawner)
    args = argparse.Namespace(
        phase=3, fr_id="FR-01", step=step, project=str(project), src_dir=None,
        dry_run=False, strict=False, srs=None, timeout=100, max_turns=30,
        max_fix_rounds=3, no_mcp=False, no_push=True, prompt_file=None,
    )
    try:
        fr_cmds.cmd_run_fr_step(args)
    except SystemExit:
        pass
    return project


def test_the_second_dispatch_gets_a_bigger_budget(tmp_path, monkeypatch):
    spawner = _RecordingSpawner(timeouts=1)
    _run(tmp_path, monkeypatch, spawner)
    assert len(spawner.budgets) >= 2, "a timeout must be retried at all"
    assert spawner.budgets[1] > spawner.budgets[0], (
        f"re-dispatched at the same budget {spawner.budgets} — this is the "
        f"identical-prompt retry that burned two hours on taskq-advance's P3"
    )
    assert spawner.budgets[1] == spawner.budgets[0] * 2


def test_escalation_happens_once_per_step(tmp_path, monkeypatch):
    """A second timeout at the doubled budget is a real limit, not a reason to
    keep doubling — otherwise one stuck FR makes the run unbounded."""
    spawner = _RecordingSpawner(timeouts=99)
    _run(tmp_path, monkeypatch, spawner)
    assert len(set(spawner.budgets)) <= 2, (
        f"budget escalated more than once: {spawner.budgets}"
    )


def test_the_escalation_is_recorded_in_the_ledger(tmp_path, monkeypatch):
    """Symmetry with the turn-budget path: both write, in the same words, so a
    reader of the consuming project's ledger sees one story."""
    spawner = _RecordingSpawner(timeouts=1)
    project = _run(tmp_path, monkeypatch, spawner)
    ledger = project / ".methodology" / "degradations.jsonl"
    assert ledger.exists(), "a wall-clock escalation must leave a trace"
    entries = [json.loads(line) for line in ledger.read_text().splitlines() if line.strip()]
    escalations = [e for e in entries if "task_timeout escalated" in e.get("what", "")]
    assert escalations, f"no wall-clock escalation entry in {entries}"
    assert "run-fr-step:" in escalations[0]["component"]
    assert escalations[0]["why"], "an entry with no reason is a log line, not a ledger"


def test_a_successful_dispatch_never_escalates(tmp_path, monkeypatch):
    spawner = _RecordingSpawner(timeouts=0)
    project = _run(tmp_path, monkeypatch, spawner)
    assert len(set(spawner.budgets)) == 1
    ledger = project / ".methodology" / "degradations.jsonl"
    if ledger.exists():
        assert "task_timeout escalated" not in ledger.read_text()
