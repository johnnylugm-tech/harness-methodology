"""How many tests did not run at this gate, answerable after the run.

Round 46 站2. `_check_test_skip_ratio` has existed since the early gates and
does exactly one thing with what it finds: `print`. The line goes to whoever
was watching the console and is then gone — no ledger row, no field in the
result, no `run-report` column. Round 43's shape, in the oldest possible
place.

Its 10% threshold is honest about the question it asks ("is coverage being
computed from a subset of the suite?"). It is the wrong instrument for the
other question. taskq-advance's 17 skips are 6.25% of 272 — under the
threshold, so nothing printed at all — while three of its NFRs had their
own guards skipping themselves. That second question is enforced by
`compute_trace_dimension`'s absent-witness rule (站1), which blocks. This
station only makes the count survive the run.

`_parse_skip_counts` is the shared parse. Both readers get the same numbers
from the same evidence, so the WARN and the ledger row can never disagree
about how many tests ran.
"""

from __future__ import annotations

import pytest

from harness.harness_bridge import _check_test_skip_ratio, _parse_skip_counts

pytestmark = [pytest.mark.core]


def _raw(evidence: str) -> dict:
    return {"breakdown": {"test_coverage": {"tool_evidence": evidence}}}


def test_the_summary_line_is_parsed_once_for_both_readers():
    assert _parse_skip_counts(_raw("255 passed, 17 skipped in 10.07s")) == (17, 272)


@pytest.mark.parametrize("evidence", [
    "",                                   # no evidence at all
    "TOTAL   882    0   100%",            # a coverage table, no pytest summary
    "255 passed in 10.07s",               # passed but no skipped count
])
def test_unparseable_evidence_abstains(evidence: str):
    """No number is not the same as zero — the caller must be able to tell."""
    assert _parse_skip_counts(_raw(evidence)) is None


def test_zero_of_zero_is_not_a_ratio():
    assert _parse_skip_counts(_raw("0 passed, 0 skipped in 0.01s")) is None


def test_the_ratio_warning_keeps_its_threshold():
    """taskq-advance's real numbers: 6.25%, under 10%, so nothing is printed.
    That is correct for the coverage question and is why it cannot be the
    mechanism that notices a requirement's own guard was skipped."""
    assert _check_test_skip_ratio(_raw("255 passed, 17 skipped in 10.07s")) is None

    loud = _check_test_skip_ratio(_raw("100 passed, 50 skipped in 1s"))
    assert loud is not None and "50 of 150" in loud


def test_the_ledger_row_has_no_ratio_threshold():
    """The finalize call site records whenever `skipped > 0`. Pinning the
    predicate here keeps the two questions from collapsing back into one:
    a single skipped test is worth a row even though it is not worth a WARN.
    """
    counts = _parse_skip_counts(_raw("271 passed, 1 skipped in 9s"))
    assert counts == (1, 272)
    assert counts is not None and counts[0] > 0, (
        "one skipped test still gets a ledger row"
    )
    assert _check_test_skip_ratio(_raw("271 passed, 1 skipped in 9s")) is None
