"""Round 74 站2 — a row the denominator did not include is named, not dropped.

Round 73's approved plan, station 1, item 2, verbatim:

    該欄位不是 `test_*` 的列不再靜默丟棄:記為 `unparsed` 並隨 report 回傳,
    由呼叫端報出來(R32/R35 —— 讀不到要說出來,不是當成乾淨)。

It was not built. `grep -r unparsed` across the tree finds it only in
`harness/crg_independent.py`, where Rounds 39/40 did this same job for the
delivered files the graph could not parse.

Had it existed, Round 73 could not have shipped station 1's defect. Measured
on the nine corpus projects with that round's header rule still in place:

    taskq 28 · taskq-renew 14 · taskq-new 25 · run-all 9 · taskq-plus 3
    · taskq-advance 2 · taskq-cc 1                      = 82 rows, 7 projects

taskq-new's 25 begin at line 747 — the row after the one that had swallowed
the rest of its table. With station 1's header rule the same signal reports
three rows, in two projects, every one a row that genuinely declares no test.

Report-only, on purpose. Both surviving rows are a project stating the truth
in the only column it has, and blocking on them would charge a project for
obeying the substance while breaking the letter (Round 42).
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.core]


def _spec(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "TEST_SPEC.md"
    path.write_text(text, encoding="utf-8")
    return path


# taskq-advance/02-architecture/TEST_SPEC.md lines 666-667, verbatim shape:
# two NFRs whose verifier is a tool, declared with no test function name.
_ROW_DECLARING_NO_TEST = """\
# TEST_SPEC.md

## Deferred to Downstream Phases

| # | NFR | Test Function | Layer | Title |
|---|---|---|---|---|
| 8 | NFR-06 | `test_nfr06_import_linter_contract_kept` | static | lint-imports |
| 9 | NFR-07 | (cross-cutting tooling) | static | pip-licenses --format=json |
| 10 | NFR-08 | (cross-cutting tooling) | static | mutmut score >= 70 |
"""


def test_a_row_that_names_no_test_is_reported_not_dropped(tmp_path):
    from core.quality_gate.spec_coverage import _parse_test_spec

    unread: list = []
    items = _parse_test_spec(_spec(tmp_path, _ROW_DECLARING_NO_TEST), unread)

    assert [i["test_fn"] for i in items] == [
        "test_nfr06_import_linter_contract_kept"]
    assert [r["line"] for r in unread] == [8, 9], unread
    assert "NFR-07" in unread[0]["text"]


def test_the_out_parameter_is_optional(tmp_path):
    """Every existing caller passes one argument and must keep working."""
    from core.quality_gate.spec_coverage import _parse_test_spec

    items = _parse_test_spec(_spec(tmp_path, _ROW_DECLARING_NO_TEST))
    assert len(items) == 1


def test_every_table_row_inside_a_table_is_a_result_or_a_report(tmp_path):
    """The accounting identity: rows in = results + reports.

    Round 73's ten-row loss was a fall-through — a data row read as a header
    left `header_skipped` false, and every row after it matched none of the
    parse branches, so no reporting branch could have caught them either.
    That gate is gone; `_is_header_row` requires a separator to follow a
    header, so it could only ever be false in a window no table row can
    occupy.

    Removing it is not observable through this parser's output while the
    shipped header rule is in place — the counter-proof put it back and every
    test here stayed green — so it is stated as what it is: a redundant
    conjunct removed from the only branch that reports, because a reporting
    branch a later change can route around is how this went silent the first
    time. Measured on the nine corpus projects: identical parse results with
    and without it, and taskq-new 105 → 115 on the removal alone once the
    wrong header rule no longer blocks the rows behind it.
    """
    from core.quality_gate.spec_coverage import _parse_test_spec

    text = """\
# TEST_SPEC.md

## FR-01: Thing

| # | Test Function | Inputs | Type | Derivation |
|---|---|---|---|---|
| 1 | `test_fr01_read` | x | unit | AC-1.1 |
| 2 | short row |
| 3 | `test_fr01_after` | x | unit | AC-1.2 |
| 4 | Totals | 2 | — | — |
"""
    unread: list = []
    parsed = _parse_test_spec(_spec(tmp_path, text), unread)

    assert [i["test_fn"] for i in parsed] == [
        "test_fr01_read", "test_fr01_after"], parsed
    assert [r["line"] for r in unread] == [8, 10], unread

    # The invariant itself: nothing between the separator and the end of the
    # table went unaccounted for.
    body = [n for n, ln in enumerate(text.splitlines(), start=1)
            if n > 6 and ln.strip().startswith("|")]
    assert len(parsed) + len(unread) == len(body), (parsed, unread, body)


def test_a_row_consumed_as_a_header_is_the_one_loss_this_station_cannot_report(
        tmp_path):
    """Where this station's reach ends, pinned rather than assumed.

    A row the parser turns into a header produced no result and is not a row
    it declined to read — it left through a different door, and no reporting
    branch here sees it. That loss is closed by station 1's second condition
    (a row naming a test function is never a header), not by this one, and
    pretending otherwise would be the half-built mechanism this repo keeps
    finding.

    The document below puts a separator directly under a declaration row, so
    the only thing standing between that row and being eaten is that second
    condition. Nothing here patches the rule to check that: the condition is
    called on the row itself and then the whole parse is checked, because a
    rule swapped out under a test is a rule the test no longer describes.
    """
    from core.quality_gate.spec_coverage import _is_header_row, _parse_test_spec

    text = """\
# TEST_SPEC.md

## Deferred to Downstream Phases

| # | NFR | Test Function | Layer | Title |
|---|---|---|---|---|
| 1 | NFR-09 | `test_nfr09_ac2_skipped_count_zero` | unit | skipped == 0 |
| 2 | NFR-09 | `test_nfr09_ac3_each_test_has_an_assert` | unit | every test function ≥ 1 assert |
|---|---|---|---|---|
| 3 | NFR-09 | `test_nfr09_ac4_no_ignore_or_deselect` | static | no --ignore / -k |
"""
    lines = text.splitlines()

    # Line 8 is a declaration row, its Title says "test function", and a
    # separator sits under it. Every condition Round 73's rule used is met.
    assert "test function" in lines[7].lower()
    assert lines[8].strip().startswith("|-")
    assert not _is_header_row(lines, 7), (
        "a row naming a test function is a declaration, never a header")

    unread: list = []
    names = {i["test_fn"] for i in _parse_test_spec(_spec(tmp_path, text), unread)}

    assert names == {"test_nfr09_ac2_skipped_count_zero",
                     "test_nfr09_ac3_each_test_has_an_assert",
                     "test_nfr09_ac4_no_ignore_or_deselect"}, names
    assert unread == [], unread


def test_a_clean_spec_reports_nothing(tmp_path):
    """The negative control — every row named a test, so the list is empty."""
    from core.quality_gate.spec_coverage import _parse_test_spec

    text = """\
# TEST_SPEC.md

## FR-01: Thing

| # | Test Function | Inputs | Type | Derivation |
|---|---|---|---|---|
| 1 | `test_fr01_a` | x | unit | AC-1.1 |
| 2 | `test_fr01_b` | y | unit | AC-1.2 |
"""
    unread: list = []
    _parse_test_spec(_spec(tmp_path, text), unread)
    assert unread == []


def test_a_sub_assertion_table_is_not_unread(tmp_path):
    """Predicates were never in this denominator and are not missing from it.

    `_header_columns` returns {} for a header naming no test function, so the
    table never opens — reporting its rows as unread would turn a correct
    exclusion into noise, which is how a report-only signal stops being read.
    """
    from core.quality_gate.spec_coverage import _parse_test_spec

    text = """\
# TEST_SPEC.md

## FR-01: Thing

| # | Test Function | Inputs | Type | Derivation |
|---|---|---|---|---|
| 1 | `test_fr01_a` | x | unit | AC-1.1 |

| rule_id | predicate | phase |
|---|---|---|
| AC1.1-status-201 | `status == 201` | 3 |
| AC1.2-body-json | `body["id"]` | 3 |
"""
    unread: list = []
    _parse_test_spec(_spec(tmp_path, text), unread)
    assert unread == []


def test_the_report_carries_the_list(tmp_path):
    """`spec_coverage_report` is what the gate and the ledger read."""
    from core.quality_gate.spec_coverage import spec_coverage_report

    project = tmp_path / "proj"
    (project / "02-architecture").mkdir(parents=True)
    (project / "02-architecture" / "TEST_SPEC.md").write_text(
        _ROW_DECLARING_NO_TEST, encoding="utf-8")

    report = spec_coverage_report(project)
    assert [r["line"] for r in report["unread"]] == [8, 9], report["unread"]
    assert report["declared"] == 1


def test_the_checker_writes_a_ledger_row(tmp_path):
    """Round 67's rule: a measurement with no sink is not a measurement.

    `spec-coverage:unread-row` is registered report-only in
    tests/MEASUREMENT_SINKS.yaml with the reason it does not block.
    """
    import json

    from core.quality_gate.spec_coverage import _run_spec_coverage_check

    project = tmp_path / "proj"
    (project / "02-architecture").mkdir(parents=True)
    (project / "02-architecture" / "TEST_SPEC.md").write_text(
        _ROW_DECLARING_NO_TEST, encoding="utf-8")

    _run_spec_coverage_check(project, threshold=0.0, verbose=False)

    ledger = project / ".methodology" / "degradations.jsonl"
    assert ledger.is_file(), "no ledger written"
    rows = [json.loads(line) for line in
            ledger.read_text(encoding="utf-8").splitlines() if line.strip()]
    mine = [r for r in rows if r.get("component") == "spec-coverage:unread-row"]
    assert len(mine) == 1, rows
    assert mine[0]["owner"] == "project"
    assert "line 8" in mine[0]["what"], mine[0]["what"]
