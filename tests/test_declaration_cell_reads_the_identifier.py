"""Round 99 站1 — a declaration cell's identifier, whatever order it is written in.

`_row_test_fn` decided which cell of a TEST_SPEC.md row names a test by
stripping markdown code-span backticks and then removing a trailing
`[...]` annotation, in that order:

    name = re.sub(r"\\[.*\\]$", "", cell.strip("`").strip())

`str.strip` only removes characters at the ENDS of the string. A cell
written ``  `test_x` [AC-1.1]  `` ends with `]`, so the CLOSING backtick is
not at an end and survives; `re.sub` then removes the annotation and leaves
``test_x` `` — a trailing space and a stranded backtick — and the guard
`" " not in name` rejects the row. The row declared a test and was counted
as declaring none.

Measured on taskq-done at the moment this was written, with the framework's
own functions:

  spec_coverage_report        declared=11   unread=109   (120 rows in the file)
  _parse_spec_names_for_fr    FR-01 → 1 name, against 11 declared rows
  P1 Naming Authority         91 of 91 TEST_INVENTORY.yaml names reported
                              "missing in TEST_SPEC.md" — every one of them
                              present, spelled correctly, in a row the parser
                              dropped. The block told the agent
                              "Agent A may have hallucinated names. Re-run
                              derive_test_cases.md", which regenerates the
                              same file.

The framework asks for those AC-ids: Step 1d of derive_test_cases.md and
`check_ac_test_spec_coverage` require every declared AC-id to appear in
TEST_SPEC.md, and the table template (derive_test_cases.md:389) has five
columns and no AC column. Writing the id beside the test name is the
obvious place, and doing it deleted the row.

The fix is order-independence, not a new accepted spelling: normalise to a
fixed point, so backticks and a trailing annotation give the same answer
whichever is written outermost. A/B over the 13 corpus TEST_SPEC.md files:
twelve produce a byte-identical name list, taskq-done goes 11 → 119 with
nothing lost.
"""

from __future__ import annotations

import pytest

from core.quality_gate.spec_coverage import _row_test_fn

pytestmark = [pytest.mark.core]


def _cells(row: str) -> list:
    return [c.strip() for c in row.split("|")[1:-1]]


# ---- the live wound -------------------------------------------------------

def test_a_backticked_name_followed_by_an_ac_id_is_read() -> None:
    """taskq-done's TEST_SPEC.md line 50, verbatim."""
    row = ('| 1 | `test_create_task_returns_201_with_id` [AC-1.1] | '
           'name="task-alpha-001"; command="echo hello" | happy_path | Q1 |')
    assert _row_test_fn(_cells(row), None) == "test_create_task_returns_201_with_id"


def test_an_unbackticked_name_followed_by_an_ac_id_is_read() -> None:
    row = '| 2 | test_create_task_missing_api_key_returns_401 [AC-1.2] | x | failure | Q2 |'
    assert _row_test_fn(_cells(row), None) == "test_create_task_missing_api_key_returns_401"


# ---- regression guards: shapes that already worked ------------------------

def test_a_plain_backticked_name_still_reads() -> None:
    row = '| 1 | `test_frXX_behaviour` | x="colour" | happy_path | Q1 |'
    assert _row_test_fn(_cells(row), None) == "test_frXX_behaviour"


def test_a_parametrized_id_is_still_stripped() -> None:
    """`test_x[case1]` → `test_x` was the original regex's whole purpose;
    Round 87 站9 found parametrized declarations misread as not_collected,
    so this shape is load-bearing and must not change."""
    row = '| 1 | `test_frXX_boundary[case1]` | x | boundary | Q3 |'
    assert _row_test_fn(_cells(row), None) == "test_frXX_boundary"


def test_a_parametrized_id_carrying_an_ac_id_reads_the_bare_name() -> None:
    """Both annotations at once — the case that needs more than one pass."""
    row = '| 1 | `test_frXX_boundary[case1]` [AC-3.2] | x | boundary | Q3 |'
    assert _row_test_fn(_cells(row), None) == "test_frXX_boundary"


def test_a_row_naming_no_test_still_names_none() -> None:
    """Negative control: the shapes taskq-advance and taskq-cc write in a
    declaration table and that legitimately declare nothing."""
    for row in (
        '| 9 | NFR-07 | (cross-cutting tooling) | static | pip-licenses --format=json |',
        '| 1 | (none declared for this round — single-version service) | — | — |',
    ):
        assert _row_test_fn(_cells(row), None) == "", row


def test_two_candidates_are_still_arbitrated_by_the_header() -> None:
    """A Title column repeating the name is genuine ambiguity, and the
    header decides — unchanged by this round."""
    row = '| 1 | `test_alpha_beta` | x | unit | test_alpha_beta runs |'
    cells = _cells(row)
    assert _row_test_fn(cells, 1) == "test_alpha_beta"


def test_the_normalisation_does_not_depend_on_write_order() -> None:
    """The defect in one sentence: the same identifier, written four ways,
    must produce one answer. Before this round the second and fourth
    produced nothing."""
    answers = {
        _row_test_fn(_cells(f'| 1 | {cell} | x | unit | — |'), None)
        for cell in (
            "test_alpha_beta",
            "`test_alpha_beta`",
            "test_alpha_beta [AC-1.1]",
            "`test_alpha_beta` [AC-1.1]",
        )
    }
    assert answers == {"test_alpha_beta"}, answers
