"""The framework's own number for the performance dimension (Round 50 站0).

`_score_pytest_benchmark` parsed the human-readable table pytest-benchmark
prints to a terminal. Measured 2026-08-13 against a real run of
`pytest --benchmark-only --benchmark-columns mean,max` — the exact command
`harness/toolchains/registry.py` issues — the parser matched ZERO rows:

    test_perf_alpha        4.7578 (1.0)      46.5000 (1.0)

The row regex expects two bare numeric columns. pytest-benchmark always
appends the relative multiplier `(1.0)` to each column, and it thousands-
separates values at or above 1000 (`1,050.7090`). Neither is optional, and
neither is affected by `--benchmark-columns`; the comment claiming that flag
"produces exactly those two numeric columns" was never checked against
output the tool actually emits.

Zero rows then returns None (Round 46 站3's rule that a suite which measured
nothing has not earned 100). That rule is right. What was wrong is that the
zero was manufactured by the parser, not reported by the data — so a project
with working benchmarks was indistinguishable from one with none, and the
verdict fell back to the agent's own unverified number.

These tests are format-agnostic about HOW the score is obtained. They assert
only that real tool output yields a number.
"""

from __future__ import annotations

import json

import pytest

from harness.tool_runners import compute_tool_score

# Captured 2026-08-13 from an actual `pytest --benchmark-only
# --benchmark-disable-gc --benchmark-columns mean,max` run on this machine.
# Verbatim: the trailing spaces and the `(1.0)` columns are what the tool
# writes, and removing them would make this fixture agree with the parser
# instead of with the tool.
REAL_TABLE_OUTPUT = """\
..                                                                       [100%]

------------------- benchmark: 2 tests -------------------
Name (time in us)        Mean                Max
----------------------------------------------------------
test_perf_alpha        4.7578 (1.0)      46.5000 (1.0)
test_perf_beta        14.2634 (3.00)     89.4580 (1.92)
----------------------------------------------------------

Legend:
  Outliers: 1 Standard Deviation from Mean; 1.5 IQR (InterQuartile Range) from 1st Quartile and 3rd Quartile.
  OPS: Operations Per Second, computed as 1 / Mean
2 passed in 2.36s
"""

# The same shape with values above 1000, which pytest-benchmark thousands-
# separates. A parser that handles `(1.0)` but not the comma reads this as
# zero rows too, so both variants are pinned.
REAL_TABLE_OUTPUT_THOUSANDS = """\
------------------------- benchmark: 2 tests ------------------------
Name (time in us)                Mean                   Max
---------------------------------------------------------------------
test_perf_get_task_by_id     754.7562 (1.0)      1,050.7090 (1.0)
test_perf_list_tasks         932.8229 (1.24)     1,933.6250 (1.84)
---------------------------------------------------------------------

2 passed, 1 warning in 2.19s
"""


def _json_report(*means_seconds: float) -> str:
    """A pytest-benchmark --benchmark-json report.

    Schema measured 2026-08-13 from a real run: `benchmarks[].stats.mean`,
    in SECONDS (4.757764248017881e-06 for the 4.7578 us row above).
    """
    return json.dumps({
        "version": "4.0.0",
        "benchmarks": [
            {"name": f"test_perf_{i}", "fullname": f"test_b.py::test_perf_{i}",
             "stats": {"mean": m, "max": m * 2, "rounds": 5}}
            for i, m in enumerate(means_seconds)
        ],
    })


def test_scores_a_real_benchmark_run():
    """Two fast benchmarks that really ran are not "no measurement"."""
    score = compute_tool_score("pytest-benchmark", REAL_TABLE_OUTPUT, 0)
    assert score is not None, (
        "the framework ran pytest-benchmark, got two measured rows, and "
        "produced no number of its own — so the verdict has nothing to "
        "cross-validate the agent's claim against"
    )
    assert score == pytest.approx(100.0), (
        "both means are microseconds; nothing here is slow"
    )


def test_scores_a_real_benchmark_run_with_thousands_separators():
    score = compute_tool_score("pytest-benchmark", REAL_TABLE_OUTPUT_THOUSANDS, 0)
    assert score is not None
    assert score == pytest.approx(100.0), (
        "754us and 932us are both far under the 1000ms warning threshold — "
        "reading the comma as a decimal point would make them look like "
        "1050ms and 1933ms and cost the project 50 points"
    )


def test_scores_a_json_report():
    """The structured path, which is what the fix should route through."""
    score = compute_tool_score("pytest-benchmark", _json_report(0.0000047, 0.0000142), 0)
    assert score == pytest.approx(100.0)


def test_slow_benchmarks_lose_points():
    """The penalty ladder still applies once the numbers are readable.

    2.0s mean is over the 1000ms warning line and under the 3000ms hard line:
    one benchmark, -25.
    """
    score = compute_tool_score("pytest-benchmark", _json_report(2.0), 0)
    assert score == pytest.approx(75.0)


def test_no_benchmarks_is_still_not_a_free_hundred():
    """Round 46 站3's rule, kept: a run that measured nothing scores None.

    The difference this round makes is that "measured nothing" now means the
    tool reported nothing, not that the parser failed to read what it did
    report.
    """
    assert compute_tool_score("pytest-benchmark", _json_report(), 0) is None
    assert compute_tool_score("pytest-benchmark", "", 5) is None
