"""Round 77 站5/站6 — the fields beside the verdict get the same source.

站5. `tests_failed` has been marked REQUIRED in the GATE1 prompt since it was
written. One grep across the tree, Round 77: zero production readers. The
number a project is made to declare was never honoured and never contradicted
— Round 24's "a field that exists is not a field whose content is true", in
the field the prompt shouts about loudest.

It now has two readers, split by whether the framework can see for itself:

  measured      the framework's own count is written into the result
                `build_persisted_gate_result` commits — the same rule
                `s4_score_verdict` applies to `score`. A disagreement is
                recorded, not blocked (see MEASUREMENT_SINKS for why).
  not measured  a self-declared non-zero blocks. An admission needs no
                cross-check, and this is the branch where the framework has
                nothing else to go on.

站6. `_parse_skip_counts` and the W1 ratio read the framework's run too. The
coverage percentage this ratio qualifies already comes from that run —
`_score_pytest` takes `TOTAL … N%` out of the same stdout — so numerator and
denominator now come from one execution (Round 37 / Round 42: the denominator
travels with the number). It also removes the way the row could vanish: Round
76 told the agent to put FAILED lines in `tool_evidence` "before the summary
line", inside a field the same prompt caps at 500 characters, and measured,
that evicts `N passed / N skipped` entirely — so `gate:test-skips` disappeared
for exactly the FRs that had failing tests.
"""

from __future__ import annotations

import json

import pytest

from core.quality_gate.fr_test_scope import (
    declared_tests_failed,
    readable_run_output,
    record_measured_tests_failed,
)
from harness.harness_bridge import _check_test_skip_ratio, _check_tests_failed, _parse_skip_counts

pytestmark = [pytest.mark.core]

_COMPONENT = "gate:tests-failed-declared"


def _run(output: str, tool: str = "pytest-cov", rc: int = 1):
    return (tool, output, rc)


def _raw(**test_coverage) -> dict:
    return {"breakdown": {"test_coverage": dict(test_coverage)}}


def _rows(project) -> list[dict]:
    path = project / ".methodology" / "degradations.jsonl"
    if not path.is_file():
        return []
    return [json.loads(line) for line in
            path.read_text(encoding="utf-8").splitlines() if line.strip()]


# ── 站5: measured ─────────────────────────────────────────────────────────────

_MIXED = _run(
    "FAILED tests/test_fr08.py::test_a - e\n"
    "FAILED tests/test_fr01.py::test_b - e\n"
    "FAILED tests/test_fr02.py::test_c - e\n"
    "3 failed, 20 passed in 1.0s\n"
)


def test_the_frameworks_count_is_the_one_that_ships(tmp_path):
    """`raw` is `ctx.finalized_result`, which `build_persisted_gate_result`
    merges over the agent's document — so this write reaches the committed
    artifact, not just the console."""
    raw = _raw(score=100, tests_failed=3, tool_evidence="3 failed, 20 passed")
    assert record_measured_tests_failed(raw, "FR-08", 3, _MIXED, tmp_path) == 1
    assert raw["breakdown"]["test_coverage"]["tests_failed"] == 1, (
        "the agent counted the whole run; only one of those tests is FR-08's")


def test_a_disagreement_is_recorded_and_not_blocked(tmp_path):
    raw = _raw(tests_failed=3)
    record_measured_tests_failed(raw, "FR-08", 3, _MIXED, tmp_path)
    rows = [r for r in _rows(tmp_path) if r.get("component") == _COMPONENT]
    assert len(rows) == 1
    assert rows[0]["data"] == {"declared": 3, "measured": 1,
                               "fr_id": "FR-08", "phase": 3}


def test_agreement_leaves_no_row(tmp_path):
    raw = _raw(tests_failed=1)
    record_measured_tests_failed(raw, "FR-08", 3, _MIXED, tmp_path)
    assert _rows(tmp_path) == []


def test_a_missing_declaration_is_filled_in_without_an_accusation(tmp_path):
    """Absent is not wrong. Nothing read this field until now, so a result
    without it is not a project that lied about it."""
    raw = _raw(score=100)
    assert record_measured_tests_failed(raw, "FR-08", 3, _MIXED, tmp_path) == 1
    assert raw["breakdown"]["test_coverage"]["tests_failed"] == 1
    assert _rows(tmp_path) == []


def test_nothing_is_written_when_the_framework_could_not_measure(tmp_path):
    raw = _raw(tests_failed=3)
    unreadable = _run("FAILED tests/test_fr01.py::t - e\n20 failed, 1 passed in 1s")
    assert record_measured_tests_failed(raw, "FR-08", 3, unreadable, tmp_path) is None
    assert raw["breakdown"]["test_coverage"]["tests_failed"] == 3, (
        "the agent's number must stand where the framework has none of its "
        "own — overwriting it with a guess is worse than leaving it")
    assert _rows(tmp_path) == []


@pytest.mark.parametrize("value,expected", [
    (0, 0), (3, 3), (3.0, 3), ("3", None), (None, None), (True, None),
])
def test_the_declaration_is_read_as_a_number_or_not_at_all(value, expected):
    """`True` is an `int` in Python and would read as 1. A gate blocking on
    that would be blocking on a type error."""
    assert declared_tests_failed(_raw(tests_failed=value)) is expected


def test_a_self_declared_failure_blocks_where_the_framework_is_blind():
    """The other reader. No framework run, no evidence the old rule could
    parse — and the agent itself says tests failed."""
    raw = _raw(tests_failed=2, tool_evidence="TOTAL  882  0  100%")
    violations = _check_tests_failed(raw, fr_id="FR-08")
    assert len(violations) == 1
    assert "tests_failed=2" in violations[0]

    # …and it does not fire when the framework did measure: the verdict there
    # is the framework's, and an over-report is not a failing test.
    assert _check_tests_failed(_raw(tests_failed=99), fr_id="FR-08",
                               framework_run=_run("20 passed in 1s", rc=0)) == []


def test_zero_declared_still_falls_through_to_the_evidence_rule():
    """Round 76's fail-closed half is untouched: an agent claiming zero while
    its own evidence says otherwise is still caught."""
    raw = _raw(tests_failed=0, tool_evidence="5 failed, 100 passed in 2.0s")
    violations = _check_tests_failed(raw, fr_id="FR-08")
    assert len(violations) == 1
    assert "5 test(s) FAILED" in violations[0]


# ── 站6: the skip counts read the same run ────────────────────────────────────

_WITH_SKIPS = _run("100 passed, 50 skipped in 2.0s\nTOTAL  882  0  100%\n", rc=0)


def test_the_skip_counts_come_from_the_run_the_coverage_number_came_from():
    """Both halves of the ratio out of one execution."""
    raw = _raw(tool_evidence="9 passed, 1 skipped in 0.1s")
    assert _parse_skip_counts(raw) == (1, 10), "agent's excerpt, unchanged"
    assert _parse_skip_counts(raw, _WITH_SKIPS) == (50, 150), (
        "the framework's own run answers when there is one")
    assert _check_test_skip_ratio(raw) is None
    warn = _check_test_skip_ratio(raw, framework_run=_WITH_SKIPS)
    assert warn is not None and "50 of 150" in warn


def test_an_evidence_only_summary_still_answers():
    """Round 76 asked the agent to push FAILED lines into a 500-character
    field; measured, that evicts the summary line and this returns None, so
    the `gate:test-skips` row vanished for the FRs that had failing tests.
    站3 removed the instruction — this removes the dependency."""
    evicted = ("FAILED tests/test_fr01.py::t - e\n" * 8)[:500]
    raw = _raw(tool_evidence=evicted)
    assert _parse_skip_counts(raw) is None, "the shape that lost the row"
    assert _parse_skip_counts(raw, _WITH_SKIPS) == (50, 150), (
        "…and the framework's run answers it anyway")


def test_a_js_run_leaves_the_skip_counts_on_the_agents_evidence():
    """`readable_run_output` is the one predicate for "did the framework's run
    answer this?", so the `source` recorded in the ledger row cannot disagree
    with where the numbers actually came from."""
    vitest = ("vitest-cov", "Tests  2 failed | 10 passed\n Duration  1.03s\n", 1)
    assert readable_run_output(vitest) == ""
    assert readable_run_output(None) == ""
    assert readable_run_output(_WITH_SKIPS) == _WITH_SKIPS[1]

    raw = _raw(tool_evidence="9 passed, 1 skipped in 0.1s")
    assert _parse_skip_counts(raw, vitest) == (1, 10)
