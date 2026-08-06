"""Round 42 站2 — the wiring: from the count to the two durable records.

`spec_coverage_report` computes the names (see
`test_declared_tests_must_exist.py`). This file pins that they arrive:

  * in the degradation ledger, which answers "what did this run not deliver?"
    — the same destination and the same shape `dimension_scope` uses for a
    switched-off dimension;
  * on the namespace the gate-result patch block reads, so the committed
    `gate{N}_result.json` answers "what was this score computed over?" from
    the one count rather than a second scan.

Recorded whether or not the check passed. taskq-renew's run is the case: 81/89
= 91.0% against Gate 4's 90.0, over the line, eight absent tests — two p95
budgets and five crash-atomicity cases — and nothing durable said so.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from cli.gate_cmds import _record_undelivered_tests
from core.degradation_ledger import read_degradations


_TEST_SPEC = """\
# TEST_SPEC.md

## FR-01: task submission

| # | Test Function | Type | Derivation |
|---|---|---|---|
| 1 | `test_fr01_submit_returns_id` | happy | SPEC §8 #4 |
| 2 | `test_nfr01_submit_status_p95_under_50ms` | nfr_pattern | NP-01 |
"""


def _project(tmp_path: Path, *, implement_all: bool) -> Path:
    (tmp_path / "02-architecture").mkdir(parents=True)
    (tmp_path / "02-architecture" / "TEST_SPEC.md").write_text(
        _TEST_SPEC, encoding="utf-8"
    )
    (tmp_path / "03-development" / "tests").mkdir(parents=True)
    body = "def test_fr01_submit_returns_id():\n    assert True\n"
    if implement_all:
        body += "\n\ndef test_nfr01_submit_status_p95_under_50ms():\n    assert True\n"
    (tmp_path / "03-development" / "tests" / "test_fr01.py").write_text(
        body, encoding="utf-8"
    )
    return tmp_path


def test_the_missing_names_reach_the_ledger_and_the_verdict(tmp_path: Path):
    project = _project(tmp_path, implement_all=False)
    args = argparse.Namespace(gate=4)

    missing = _record_undelivered_tests(args, project)

    assert [m["test_fn"] for m in missing] == [
        "test_nfr01_submit_status_p95_under_50ms"
    ]
    assert args._spec_undelivered == missing
    assert args._spec_declared == 2

    entries = [e for e in read_degradations(project)
               if e.get("component") == "spec:undelivered"]
    assert len(entries) == 1
    assert "1 of 2 declared tests do not exist" in entries[0]["what"]
    assert entries[0]["data"]["missing"][0]["derivation"] == "NP-01", (
        "the derivation has to survive — it is what says which requirement "
        "lost its evidence"
    )


def test_a_fully_delivered_spec_writes_no_ledger_line(tmp_path: Path):
    """The ledger is for events. "Everything declared exists" is not one.

    The namespace field is still set, and still empty: a reader of the gate
    result must be able to tell "nothing missing" from "this record predates
    the field".
    """
    project = _project(tmp_path, implement_all=True)
    args = argparse.Namespace(gate=4)

    assert _record_undelivered_tests(args, project) == []
    assert args._spec_undelivered == []
    assert args._spec_declared == 2
    assert [e for e in read_degradations(project)
            if e.get("component") == "spec:undelivered"] == []


def test_the_recorded_names_are_json_serialisable(tmp_path: Path):
    """The gate-result patch block writes these straight into JSON."""
    args = argparse.Namespace(gate=4)
    _record_undelivered_tests(args, _project(tmp_path, implement_all=False))
    assert json.loads(json.dumps(args._spec_undelivered)) == args._spec_undelivered
