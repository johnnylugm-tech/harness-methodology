"""Round 74 站1 — a header is the row a separator follows, not a row that
mentions "test function".

Round 73 fixed which COLUMN `_parse_test_spec` reads the name from and left
in place the question that decides whether a row is a header at all:

    if "|" in stripped and re.search(r"Test Function", stripped, re.I):

Every declaration row in a Deferred-NFR table carries a Title column
describing what the test asserts, and one of taskq-new's says "every test
function ≥ 1 assert". Read as a header it restarted the table with
`header_skipped` false, so that row and the nine after it were dropped
without a word. Measured against the delivered tree after Round 73 shipped:

    115 rows declared  →  105 read  →  4b published 100.0%

Seven of the ten hidden names have no `def` anywhere in the delivered tree,
including `test_nfr09_ac4_no_ignore_deselect_collect_ignore_testpaths_removal`
— the project's own check for the collection-hook workaround the external
audit reported as missing, which Round 73's ledger recorded as closed.

Round 73's own seven tests stayed green through all of it: not one of their
fixtures had a Title column. This file's fixture is taskq-new's line 746
verbatim, which is the only reason the assertion below means anything
(Round 19: a fixture written to the rule's shape tests the fixture).
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.core]


# taskq-new/02-architecture/TEST_SPEC.md, the Deferred table verbatim. Row 26's
# Title cell is the one that used to end the table.
_DEFERRED_WITH_PROSE_TITLE = """\
# TEST_SPEC.md

## FR-01: Task CRUD

| # | Test Function | Inputs | Type | Derivation |
|---|---|---|---|---|
| 1 | `test_fr01_ac1_post_creates_task_201` | valid body | integration | AC-1.1 |

## Deferred to Downstream Phases

| # | NFR | Test Function | Layer | Title |
|---|---|---|---|---|
| 25 | NFR-09 | `test_nfr09_ac2_pytest_skipped_count_zero` | unit | skipped == 0 (AC-N9.2) |
| 26 | NFR-09 | `test_nfr09_ac3_each_test_has_at_least_one_assert` | unit | every test function ≥ 1 assert (AC-N9.3) |
| 27 | NFR-09 | `test_nfr09_ac4_no_ignore_deselect_collect_ignore_testpaths_removal` | static | no --ignore / -k / --deselect (AC-N9.4) |
| 28 | NFR-10 | `test_nfr10_ac1_integration_line_coverage_ge_80_percent` | unit | integration line coverage ≥ 80% |
"""


def _spec(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "TEST_SPEC.md"
    path.write_text(text, encoding="utf-8")
    return path


def test_a_row_whose_title_says_test_function_is_not_a_header(tmp_path):
    """The live defect: row 26 swallowed itself and everything after it."""
    from core.quality_gate.spec_coverage import _parse_test_spec

    names = [i["test_fn"] for i in _parse_test_spec(
        _spec(tmp_path, _DEFERRED_WITH_PROSE_TITLE))]

    assert names == [
        "test_fr01_ac1_post_creates_task_201",
        "test_nfr09_ac2_pytest_skipped_count_zero",
        "test_nfr09_ac3_each_test_has_at_least_one_assert",
        "test_nfr09_ac4_no_ignore_deselect_collect_ignore_testpaths_removal",
        "test_nfr10_ac1_integration_line_coverage_ge_80_percent",
    ], names


def test_the_nfr_column_still_reaches_the_row_after_a_prose_title(tmp_path):
    """Round 73's fix has to survive this one — the columns are still per-table.

    The deferred table is `| # | NFR | Test Function | Layer | Title |`, so a
    header index of 1 would put `NFR-09` in `test_fn` and a header index
    resolved from the FR table above would put the row number there.
    """
    from core.quality_gate.spec_coverage import _parse_test_spec

    rows = {i["test_fn"]: i for i in _parse_test_spec(
        _spec(tmp_path, _DEFERRED_WITH_PROSE_TITLE))}

    assert rows["test_fr01_ac1_post_creates_task_201"]["type"] == "integration"
    assert rows["test_fr01_ac1_post_creates_task_201"]["derivation"] == "AC-1.1"
    # The deferred table names neither Type nor Derivation; both stay empty
    # rather than picking up `unit` or the Title prose from a neighbour's index.
    assert rows["test_nfr09_ac3_each_test_has_at_least_one_assert"]["type"] == ""


def test_a_header_needs_a_separator_under_it(tmp_path):
    """A `Test Function` line with no separator row is not a table at all.

    Every corpus TEST_SPEC has prose lines and summary rows quoting the phrase;
    what makes the real header real is the `|---|---|` beneath it.
    """
    from core.quality_gate.spec_coverage import _parse_test_spec

    text = """\
# TEST_SPEC.md

## FR-01: Thing

| Test Function names are preserved verbatim from TEST_INVENTORY.yaml | 63 / 63 |

| # | Test Function | Inputs | Type | Derivation |
|---|---|---|---|---|
| 1 | `test_fr01_only_real_row` | x | unit | AC-1.1 |
"""
    names = [i["test_fn"] for i in _parse_test_spec(_spec(tmp_path, text))]
    assert names == ["test_fr01_only_real_row"], names


def test_a_table_naming_no_test_function_closes_the_previous_one(tmp_path):
    """Sub-assertion rows must not be read under the last table's columns.

    `_header_columns` returns {} for a header naming no test function, and the
    rows below it are predicates, not declarations. Six of the nine corpus
    projects put such a table between two declaration tables.
    """
    from core.quality_gate.spec_coverage import _parse_test_spec

    text = """\
# TEST_SPEC.md

## FR-01: Thing

| # | Test Function | Inputs | Type | Derivation |
|---|---|---|---|---|
| 1 | `test_fr01_real` | x | unit | AC-1.1 |

| rule_id | predicate | phase |
|---|---|---|
| AC1.1-status-201 | `status == 201` | 3 |
"""
    names = [i["test_fn"] for i in _parse_test_spec(_spec(tmp_path, text))]
    assert names == ["test_fr01_real"], names


def test_an_alignment_separator_is_a_separator(tmp_path):
    """`|:---|---:|` is markdown's separator too.

    No corpus project writes one today, which is exactly why the old
    `^\\|[-| ]+\\|$` survived: the rule was never asked. A header whose
    separator uses alignment colons would otherwise never start a table.
    """
    from core.quality_gate.spec_coverage import _parse_test_spec

    text = """\
# TEST_SPEC.md

## FR-01: Thing

| # | Test Function | Inputs | Type | Derivation |
|:--|:----------------|:------|:-----|----------:|
| 1 | `test_fr01_aligned` | x | unit | AC-1.1 |
"""
    names = [i["test_fn"] for i in _parse_test_spec(_spec(tmp_path, text))]
    assert names == ["test_fr01_aligned"], names


# Every distinct `Test Function` header shape the nine corpus projects write,
# transcribed from their TEST_SPEC.md files. The name's column differs across
# them, which is what Round 73 fixed; what this file adds is that they all
# have to survive sitting in one document beside a prose Title.
_REAL_HEADER_SHAPES = [
    "| # | Test Function | Type | Derivation |",
    "| # | Test Function | Inputs | Type | Derivation |",
    "| # | Test Function | Type | Derivation | NFR |",
    "| # | Test Function | Inputs | Type | Layer | Derivation | NFR |",
    "| # | NFR | Test Function | Layer | Title |",
    "| # | Test Function | NFR | Layer | Title |",
]


def _document_of_every_real_shape() -> "tuple[str, list]":
    """One TEST_SPEC holding all six shapes, each with a prose-Title row."""
    out = ["# TEST_SPEC.md", ""]
    expected = []
    for n, header in enumerate(_REAL_HEADER_SHAPES, start=1):
        width = header.count("|") - 1
        fn_col = next(i for i, c in enumerate(header.split("|")[1:-1])
                      if "test function" in c.strip().lower())
        out += [f"## FR-{n:02d}: Shape {n}", "", header,
                "|" + "---|" * width]
        for k in (1, 2):
            name = f"test_fr{n:02d}_shape_row{k}"
            cells = ["x"] * width
            cells[0] = str(k)
            cells[fn_col] = f"`{name}`"
            # The last cell is prose in four of the six shapes; give every
            # shape one so no row is exercised without the phrase in it.
            cells[-1] = "every test function ≥ 1 assert"
            out.append("| " + " | ".join(cells) + " |")
            expected.append(name)
        out.append("")
    return "\n".join(out), expected


def test_every_real_header_shape_reads_every_row_beside_a_prose_title(tmp_path):
    """Monotonicity over the shapes actually shipped, not over invented ones.

    Round 73's first draft passed its own fixtures while dropping all 36 of
    taskq-renew's declarations, because its fixtures were written to the rule
    it was testing (Round 19). These six headers are transcriptions; the
    Title cell in every row carries the phrase that used to end the table.
    """
    from core.quality_gate.spec_coverage import _parse_test_spec

    text, expected = _document_of_every_real_shape()
    names = [i["test_fn"] for i in _parse_test_spec(_spec(tmp_path, text))]

    assert names == expected, sorted(set(expected) - set(names))


def test_a_header_that_disagrees_with_its_own_rows_still_reads_them(tmp_path):
    """taskq-renew writes `| # | NFR | Test Function | … |` over rows that put
    the name in column 1. Round 73 made the name content-located for exactly
    this; the header change here must not quietly re-introduce the trust.
    """
    from core.quality_gate.spec_coverage import _parse_test_spec

    text = """\
# TEST_SPEC.md

## Deferred to Downstream Phases

| # | NFR | Test Function | Layer | Title |
|---|---|---|---|---|
| 9 | `test_nfr02_bandit_has_no_high_or_medium_findings` | NFR-02 | static | bandit gate |
"""
    names = [i["test_fn"] for i in _parse_test_spec(_spec(tmp_path, text))]
    assert names == ["test_nfr02_bandit_has_no_high_or_medium_findings"], names
