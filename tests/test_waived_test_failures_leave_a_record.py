"""Round 77 站2 — the waiver leaves a record, and names its real executor.

Round 76 decided that failures outside this FR's scope must not block its
Gate 1. That decision is right (Round 42: a project obeying the substance
must not be charged for the letter — the run's own SCOPE RULES forbid FR-08
from touching FR-01's code). What it left behind was one `print()`:

  * gone with the console — no ledger row, no field in the result, no
    `run-report` column, which is Round 43's shape in the oldest place
  * a count that was per-test beside a sample that was per-file, so
    "20 sibling test(s) failed (tests/test_fr01.py, tests/test_fr02.py,
    tests/test_fr03.py)" named three files for twenty tests with no ellipsis
    and never mentioned FR-04 or FR-05 at all
  * a docstring saying the failures were "noted in the verify log" — that is
    `.methodology/gate_verify.jsonl` (core/quality_gate/gate_verify.py), and
    nothing on this path has ever written to it
  * `if scoped: return [...]` ahead of the WARN, so when this FR was ALSO red
    the others produced no output whatsoever

and a justification that does not hold: "the owning FR's GATE1 will catch the
real failure on its own pass". The Phase 3 FR loop is forward-only
(`for (const frId of frIds)` with an `alreadyDone` skip) and S4-B runs only at
`gate_num == 1`, so an FR already behind never re-runs — and a failing test in
a file no FR owns has no owning gate at all.

The executor that does exist is `cli/phase_cmds.py::_advance_prechecks`. It
runs the whole suite at every phase transition and refuses to advance while
any test is red. This station's row is what makes that block attributable
when it arrives, instead of an un-owned "[BLOCKED] TDD test/coverage failure"
one phase later.
"""

from __future__ import annotations

import json

import pytest

from core.quality_gate.fr_test_scope import (
    record_waived_test_failures,
    waived_test_failures,
)

pytestmark = [pytest.mark.core]

_LEDGER = ".methodology/degradations.jsonl"
_COMPONENT = "gate:out-of-scope-test-failures"


def _run(output: str, tool: str = "pytest-cov", rc: int = 1):
    return (tool, output, rc)


def _rows(project) -> list[dict]:
    path = project / _LEDGER
    if not path.is_file():
        return []
    return [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line.strip()]


_FIVE_OTHER_FRS = _run(
    "FAILED tests/test_fr01.py::test_a - e\n"
    "FAILED tests/test_fr02.py::test_b - e\n"
    "FAILED tests/test_fr03.py::test_c - e\n"
    "FAILED tests/test_fr04.py::test_d - e\n"
    "FAILED tests/test_fr05.py::test_e - e\n"
    "5 failed, 59 passed in 6.17s\n"
)


def test_the_record_names_every_waived_test_not_a_sample(tmp_path):
    """The old WARN sampled three FILES for a count of twenty TESTS.

    A record that says "20" and then names three of them cannot answer, after
    the run, which twenty they were.
    """
    waived = record_waived_test_failures(tmp_path, "FR-08", 3, _FIVE_OTHER_FRS)
    assert len(waived) == 5

    rows = [r for r in _rows(tmp_path) if r.get("component") == _COMPONENT]
    assert len(rows) == 1, rows
    assert rows[0]["data"]["nodeids"] == [
        "tests/test_fr01.py::test_a",
        "tests/test_fr02.py::test_b",
        "tests/test_fr03.py::test_c",
        "tests/test_fr04.py::test_d",
        "tests/test_fr05.py::test_e",
    ]
    assert rows[0]["data"]["fr_id"] == "FR-08"
    assert rows[0]["data"]["phase"] == 3


def test_the_record_is_written_when_this_fr_is_red_too(tmp_path):
    """The mixed case. `if scoped: return [...]` used to return before the
    WARN, so an FR with its own failure published nothing about anyone
    else's — the run where the information is most useful was the one where
    it was thrown away."""
    mixed = _run(
        "FAILED tests/test_fr08.py::test_mine - e\n"
        "FAILED tests/test_fr01.py::test_theirs - e\n"
        "2 failed, 59 passed in 6.17s\n"
    )
    assert record_waived_test_failures(tmp_path, "FR-08", 3, mixed) == [
        "tests/test_fr01.py::test_theirs"]
    rows = [r for r in _rows(tmp_path) if r.get("component") == _COMPONENT]
    assert len(rows) == 1
    assert rows[0]["data"]["nodeids"] == ["tests/test_fr01.py::test_theirs"]


def test_the_record_names_the_executor_not_a_promise_it_cannot_keep(tmp_path):
    """`why` must not say "the owning FR's gate will catch it".

    It is false twice over: the FR loop is forward-only, and a failure in a
    file no FR owns has no owning gate. What is true is _advance_prechecks.
    """
    record_waived_test_failures(tmp_path, "FR-08", 3, _FIVE_OTHER_FRS)
    why = _rows(tmp_path)[0]["why"]
    assert "_advance_prechecks" in why
    assert "owning FR" not in why, why


def test_a_failure_no_fr_owns_is_waived_without_claiming_an_owner(tmp_path):
    """Round 76 waived `tests/integration/test_api_flow.py` and
    `tests/test_nfr09_ac3.py` on the promise that "owning FRs' gates will
    catch" them. Gate 1 is per-FR and NFRs are judged through traceability,
    never through S4-B, so no gate was ever going to.

    It is still not this FR's to fix. What changes is that the record says
    which tests they are and points at the executor that really runs.
    """
    unowned = _run(
        "FAILED tests/integration/test_api_flow.py::test_e2e - e\n"
        "FAILED tests/test_nfr09_ac3.py::test_x - e\n"
        "2 failed, 40 passed in 1.0s\n"
    )
    waived = record_waived_test_failures(tmp_path, "FR-08", 3, unowned)
    assert waived == ["tests/integration/test_api_flow.py::test_e2e",
                      "tests/test_nfr09_ac3.py::test_x"]
    assert _rows(tmp_path)[0]["owner"] == "project"


def test_nothing_is_recorded_when_nothing_was_waived(tmp_path):
    green = _run("59 passed in 1.2s\n", rc=0)
    assert record_waived_test_failures(tmp_path, "FR-08", 3, green) == []
    assert _rows(tmp_path) == []


def test_nothing_is_recorded_when_the_framework_could_not_scope(tmp_path):
    """The critical negative: "I could not read this run" must not be filed as
    "I read it and waived nothing". `_check_tests_failed` falls back to the
    fail-closed rule for exactly these, so a row here would claim a decision
    that was never made."""
    for label, run in [
        ("unreconcilable",
         _run("FAILED tests/test_fr01.py::t - e\n20 failed, 59 passed in 6.17s")),
        ("js runner",
         ("vitest-cov", "Tests  2 failed | 10 passed\n Duration  1.03s\n", 1)),
        ("no framework run", None),
        ("no fr named", _FIVE_OTHER_FRS),
    ]:
        fr = None if label == "no fr named" else "FR-08"
        assert waived_test_failures(fr, run) == [], label
        assert record_waived_test_failures(tmp_path, fr, 3, run) == [], label
    assert _rows(tmp_path) == []


def test_the_warning_still_reaches_the_console(tmp_path, capsys):
    """The ledger row is the durable half; the WARN is the immediate one.
    Round 76's ten new tests asserted neither — deleting the whole `if other:`
    block left every one of them green."""
    record_waived_test_failures(tmp_path, "FR-08", 3, _FIVE_OTHER_FRS)
    out = capsys.readouterr().out
    assert "[WARN] FR-08 GATE1: 5 failing test(s) outside this FR's scope" in out
    assert "tests/test_fr05.py::test_e" in out, (
        "the console line names every waived test, not the first three")
