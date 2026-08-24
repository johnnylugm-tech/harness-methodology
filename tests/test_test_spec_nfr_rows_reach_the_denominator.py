"""Round 73 站1 — the header says which column, and nobody read it.

`_parse_test_spec` decides a markdown table has started by searching its header
row for "Test Function", then reads the name out of `cols[1]` regardless of
where that header actually put it.

`templates/TEST_SPEC.md` writes the NFR table as
``| # | Test Function | Type | Derivation |``, so the assumption held for the
shape the framework ships. What the framework ships is not what its own rules
produce: `cli/checks/specs.py:91`'s NFR Layering Hard Rule REQUIRES every
unit/static NFR test to sit in a "Deferred to Downstream Phases" section, and
every project on this machine writes that table as

    | # | NFR | Test Function | Layer | Title |

`cols[1]` is then `NFR-09`, `raw_fn.startswith("test_")` is False, and the row
is dropped without a word.

Measured, taskq-new:

    _parse_test_spec        81 rows      pct 100.0
    the file declares      115 rows
    of the 34 dropped, 25 have no `def` in the delivered tree

and among those 25 —

    test_nfr06_ac2_sqlalchemy_forbidden_outside_repository
    test_nfr09_ac1_no_skip_skipif_xfail_assertion_free_stub
    test_nfr09_ac2_pytest_skipped_count_zero
    test_nfr09_ac4_no_ignore_deselect_collect_ignore_testpaths_removal

— the four checks an external audit later reported as missing. The project had
declared each one; the framework's own D4 denominator could not see them.
taskq-super drops 36 the same way, taskq-advance 8.

The `Type` and `Derivation` columns move with the same fix. They were read
from `cols[2]`/`cols[3]`, which on the five-column FR table every project
writes (`| # | Test Function | Inputs | Type | Derivation |`) is `Inputs` and
`Type` — one column off, and carried into the `spec:undelivered` ledger row.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.core]


# Verbatim shape from taskq-new/02-architecture/TEST_SPEC.md:717 and :54.
# Written out rather than derived from the parser's own vocabulary: a fixture
# built from the thing under test is Round 19's mother defect, and the whole
# reason this row shape went unread for as long as it did is that no fixture
# here had ever contained one.
_NFR_TABLE = """\
## FR-01: Task CRUD

| # | Test Function | Inputs | Type | Derivation |
|---|---|---|---|---|
| 1 | `test_fr01_ac1_post_creates_task_201` | valid body | integration | AC-1.1 |

## Deferred to Downstream Phases

| # | NFR | Test Function | Layer | Title |
|---|---|---|---|---|
| 1 | NFR-06 | `test_nfr06_ac2_sqlalchemy_forbidden_outside_repository` | lint | forbidden contract (AC-N6.2) |
| 2 | NFR-09 | `test_nfr09_ac2_pytest_skipped_count_zero` | unit | skipped == 0 (AC-N9.2) |
"""


def _spec(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "02-architecture" / "TEST_SPEC.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def test_the_nfr_table_reaches_the_denominator(tmp_path):
    """The whole point: a declared test the parser cannot see is not declared."""
    from core.quality_gate.spec_coverage import _parse_test_spec

    rows = _parse_test_spec(_spec(tmp_path, _NFR_TABLE))
    names = [r["test_fn"] for r in rows]

    assert names == [
        "test_fr01_ac1_post_creates_task_201",
        "test_nfr06_ac2_sqlalchemy_forbidden_outside_repository",
        "test_nfr09_ac2_pytest_skipped_count_zero",
    ], f"the five-column NFR table was dropped: {names}"


def test_the_type_column_is_the_one_the_header_named(tmp_path):
    """`cols[2]` is `Inputs` on the table every project actually writes.

    The value travels: `_record_spec_undelivered` copies `type` and
    `derivation` into the `spec:undelivered` ledger row, so a reader asking
    which layer lost its evidence was being told the inputs.
    """
    from core.quality_gate.spec_coverage import _parse_test_spec

    rows = {r["test_fn"]: r for r in _parse_test_spec(_spec(tmp_path, _NFR_TABLE))}

    fr = rows["test_fr01_ac1_post_creates_task_201"]
    assert fr["type"] == "integration", fr
    assert fr["derivation"] == "AC-1.1", fr

    # The NFR table has no `Type` or `Derivation` column at all. Empty is the
    # honest answer; carrying `lint` under a key named `type` would be reading
    # a header this table never wrote.
    nfr = rows["test_nfr09_ac2_pytest_skipped_count_zero"]
    assert nfr["type"] == "", nfr
    assert nfr["derivation"] == "", nfr


def test_a_column_index_does_not_leak_between_tables(tmp_path):
    """The failure mode of the fix itself.

    A first draft resolved the index once and never reset it, so a
    four-column table following a five-column one read its names out of
    column 2. Measured on taskq-renew, that draft went from 89 declared rows
    to 53 — a "fix" that hides more than it recovers.
    """
    from core.quality_gate.spec_coverage import _parse_test_spec

    rows = _parse_test_spec(_spec(tmp_path, _NFR_TABLE + """
## FR-02: Execution

| # | Test Function | Inputs | Type | Derivation |
|---|---|---|---|---|
| 1 | `test_fr02_ac1_run_returns_202` | POST /run | integration | AC-2.1 |
"""))
    assert "test_fr02_ac1_run_returns_202" in [r["test_fn"] for r in rows]


def test_a_header_that_disagrees_with_its_own_rows_still_yields_the_name(tmp_path):
    """taskq-renew, verbatim: the header says column 2, the rows use column 1.

    This is why the name is found by what a cell IS rather than where the
    header says it sits. The first version of this station trusted the header
    alone and dropped all 36 of that project's deferred-NFR declarations —
    a change that would have recovered 25 rows in one project by losing 36 in
    another. The corpus monotonicity run is what said so:

        taskq-renew   old 89 → new 53, 36 lost

    and after the fix, nine projects, zero lost.
    """
    from core.quality_gate.spec_coverage import _parse_test_spec

    rows = _parse_test_spec(_spec(tmp_path, """\
## Deferred to Downstream Phases

| # | NFR | Test Function | Layer | Title |
|---|---|---|---|---|
| 9 | `test_nfr02_bandit_has_no_high_or_medium_findings` | NFR-02 | static | bandit: 0 HIGH / 0 MEDIUM |
"""))
    assert [r["test_fn"] for r in rows] == [
        "test_nfr02_bandit_has_no_high_or_medium_findings"]


def test_a_prose_cell_beginning_with_test_is_not_a_function_name(tmp_path):
    """The tie-break's floor.

    A Title column reading `test_x fails when the key is absent` is prose, not
    an identifier. Without this an ambiguity would be manufactured in every row
    of a table whose titles happen to start with the test's own name, and the
    header — the thing that just proved unreliable — would be arbitrating it.
    """
    from core.quality_gate.spec_coverage import _parse_test_spec

    rows = _parse_test_spec(_spec(tmp_path, """\
## Deferred to Downstream Phases

| # | NFR | Test Function | Layer | Title |
|---|---|---|---|---|
| 1 | NFR-03 | `test_nfr03_cancelled_error_reraise` | unit | test_nfr03 asserts CancelledError is re-raised |
"""))
    assert [r["test_fn"] for r in rows] == ["test_nfr03_cancelled_error_reraise"]


def test_the_declared_name_with_no_def_is_reported_missing(tmp_path):
    """End to end: the row reaches `missing`, which is what the gate reads.

    `cli/gate_cmds.py::_record_spec_undelivered` turns this list into
    `spec_undelivered` in gate{N}_result.json and a ledger row. taskq-new
    published `"spec_undelivered": []` beside a Gate 4 of 94.59.
    """
    from core.quality_gate.spec_coverage import spec_coverage_report

    _spec(tmp_path, _NFR_TABLE)
    tests = tmp_path / "03-development" / "tests"
    tests.mkdir(parents=True)
    (tests / "test_fr01.py").write_text(
        "def test_fr01_ac1_post_creates_task_201():\n    assert True\n",
        encoding="utf-8",
    )

    report = spec_coverage_report(tmp_path)

    assert report["declared"] == 3, report
    assert report["implemented"] == 1, report
    assert sorted(m["test_fn"] for m in report["missing"]) == [
        "test_nfr06_ac2_sqlalchemy_forbidden_outside_repository",
        "test_nfr09_ac2_pytest_skipped_count_zero",
    ]


def test_a_table_with_no_test_function_header_is_still_ignored(tmp_path):
    """The counter-direction.

    TEST_SPEC.md carries sub-assertion tables (`| rule_id | predicate | … |`)
    and requirement tables that name no test function. They were ignored
    before this change and must stay ignored — a header-driven index that
    fired on any table would put predicates into the denominator.
    """
    from core.quality_gate.spec_coverage import _parse_test_spec

    rows = _parse_test_spec(_spec(tmp_path, """\
## FR-01: Task CRUD

**Sub-assertions**

| rule_id | predicate | phase |
|---|---|---|
| AC1.1-status-201 | `status == 201` | 3 |
| DEPLOY-healthz-200 | `healthz_status == "200"` | 1 |
"""))
    assert rows == []
