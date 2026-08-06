"""Round 41 站0 — the framework must remember a failure it has already paid for.

`cmd_run_fr_step` adapts to failure three ways: it doubles a step's turn ceiling
once (`_turn_budget_escalated`), doubles its wall-clock budget once
(`_wallclock_escalated`), and aborts after two no-progress fix rounds
(`no_progress_count`). All three are locals of `cmd_run_fr_step`. The execution
model is one process per step invocation. So every one of them resets to zero
on every retry, and the framework meets each repeat as if it were the first.

taskq-api's FR-04 measured what that costs. Between 06:51 and 09:14 on
2026-08-06, TDD-GREEN and then TDD-IMPROVE failed **eight times with a
byte-identical error** — `subtype=success API Error: Stream idle timeout - no
chunks received` — for $6.02 across 3h11m. `.methodology/degradations.jsonl`
records four lines for that whole run, and not one of them mentions the
repetition. Nothing in the framework could see the second failure as a repeat
of the first, because nothing was looking at anything older than the process.

The durable record already exists: `core/degradation_ledger.py` lives at
`.methodology/degradations.jsonl` precisely so it "outlives the work directory
it was recording" (Round 27 站3). It is written and never read.

Two bounds, both derived rather than invented:

  * how many identical attempts are enough — `_STEP_RETRY_ATTEMPTS`, the same
    number the in-process loop already spends before giving up; and
  * when the refusal lifts — when the tree changes. An identical prompt against
    an identical tree cannot produce a different answer (the reasoning
    `fr_code_changed_since_last_gate1` and `run_suite`'s fingerprint already
    run on), so a repair of any kind re-opens the step and a blind re-run does
    not.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from unittest import mock

import pytest

import harness_cli  # noqa: F401  entry-first load order
import cli.fr_cmds as fr_cmds  # noqa: E402
from cli.exit_codes import EX_FAIL, EX_STEP_REPEATED_FAILURE  # noqa: E402
from core import agent_spawner as core_spawner  # noqa: E402

pytestmark = [pytest.mark.core]


_STREAM_IDLE = "subtype=success API Error: Stream idle timeout - no chunks received"


class _AlwaysFails:
    """One fixed transport failure, counted."""

    def __init__(self, output: str = _STREAM_IDLE):
        self.output = output
        self.calls = 0

    def spawn(self, **kwargs):
        self.calls += 1
        _ = kwargs
        return {
            "status": "ERROR", "error_class": "INFRA_ERROR",
            "exit_code": 1, "output": self.output,
        }


def _project(tmp_path: Path) -> Path:
    for sub in ("03-development/tests", "03-development/src",
                "01-requirements", ".methodology"):
        (tmp_path / sub).mkdir(parents=True, exist_ok=True)
    (tmp_path / ".methodology" / "quality_manifest.json").write_text(
        json.dumps({"fr_ids": ["FR-01"]}), encoding="utf-8"
    )
    (tmp_path / "01-requirements" / "SRS.md").write_text(
        "### FR-01: Widget\n\nMUST accept input.\n\n---\n", encoding="utf-8"
    )
    subprocess.run(["git", "init", "-q"], cwd=str(tmp_path), check=True)
    return tmp_path


def _run(project: Path, spawner) -> int:
    """One `run-fr-step` invocation — i.e. one process, in production."""
    args = argparse.Namespace(
        phase=3, fr_id="FR-01", step="TDD-IMPROVE", project=str(project), src_dir=None,
        dry_run=False, strict=False, srs=None, timeout=100, max_turns=30,
        max_fix_rounds=3, no_mcp=False, no_push=True, prompt_file=None,
    )
    with mock.patch.object(core_spawner, "AgentSpawner", lambda *a, **k: spawner):
        try:
            return fr_cmds.cmd_run_fr_step(args)
        except SystemExit as exc:  # pragma: no cover - defensive
            return int(exc.code or 0)


def test_an_identical_failure_on_an_unchanged_tree_is_refused(tmp_path, capsys):
    """The second invocation must not buy the same failure a second time."""
    project = _project(tmp_path)

    first = _AlwaysFails()
    assert _run(project, first) == EX_FAIL
    assert first.calls >= 1, "the first invocation must actually try"

    second = _AlwaysFails()
    rc = _run(project, second)
    out = capsys.readouterr()
    assert second.calls == 0, (
        f"dispatched {second.calls}x more at a failure the framework had already "
        f"seen {first.calls}x on this exact tree — this is the shape that cost "
        f"taskq-api $6.02 over eight identical dispatches"
    )
    assert rc == EX_STEP_REPEATED_FAILURE, f"exit {rc}, not a distinguishable code"
    assert "degradations.jsonl" in (out.out + out.err), (
        "the refusal must point at the record that justifies it"
    )


def test_the_refusal_is_recorded_in_the_ledger(tmp_path):
    """The repeat itself is a degradation: it must leave a trail, since the
    whole defect is that eight repeats left none."""
    project = _project(tmp_path)
    _run(project, _AlwaysFails())
    _run(project, _AlwaysFails())
    ledger = (project / ".methodology" / "degradations.jsonl")
    assert ledger.is_file(), "no ledger written at all"
    entries = [json.loads(line) for line in ledger.read_text().splitlines() if line.strip()]
    assert any("FR-01" in json.dumps(e) and "TDD-IMPROVE" in json.dumps(e) for e in entries), (
        f"the ledger records no repeat for FR-01 TDD-IMPROVE: {entries}"
    )


def test_a_changed_tree_re_opens_the_step(tmp_path):
    """The refusal is not a permanent trap: repair anything and it lifts.

    Without this the framework would trade an unbounded retry loop for an
    unbreakable stop, which is the same defect facing the other way.
    """
    project = _project(tmp_path)
    _run(project, _AlwaysFails())
    refused = _AlwaysFails()
    _run(project, refused)
    assert refused.calls == 0, "precondition for this test: the repeat is refused"

    (project / "03-development" / "src" / "fix.py").write_text(
        "VALUE = 1\n", encoding="utf-8"
    )
    third = _AlwaysFails()
    _run(project, third)
    assert third.calls >= 1, (
        "the tree changed and the step was still refused — a repair has no way "
        "to prove itself"
    )


def test_a_different_failure_is_not_the_same_failure(tmp_path):
    """Counting must key on the signature, not merely on (FR, step): a new
    failure mode is new information and deserves its own attempt."""
    project = _project(tmp_path)
    _run(project, _AlwaysFails())
    other = _AlwaysFails("Agent produced no output at all")
    _run(project, other)
    assert other.calls >= 1, (
        "a different failure signature was refused as a repeat — the framework "
        "stopped distinguishing failures from each other"
    )
