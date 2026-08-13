"""The framework's own number for the performance dimension (Round 50 站0).

`_score_pytest_benchmark` parsed the human-readable table pytest-benchmark
prints to a terminal. Measured 2026-08-13 against a real run of
`pytest --benchmark-only --benchmark-columns mean,max` — the exact command
`harness/toolchains/registry.py` issues — the parser matched ZERO rows:

    test_perf_alpha        4.7578 (1.0)      46.5000 (1.0)

The row regex expects two bare numeric columns. It gets them only in the
narrowest case — ONE benchmark, every value under 1000. Measured:

  1 benchmark   `test_perf_solo   4.6792   45.2080`          parses
  2 benchmarks  `test_perf_alpha  4.7578 (1.0)  46.5 (1.0)`  zero rows
  any, >= 1000  `... 1,050.7090 (1.0)`                       zero rows

pytest-benchmark adds a relative-multiplier column once there is something to
compare against, and thousands-separates at four digits. `--benchmark-columns`
affects neither. So the parser's success depended on how many benchmarks the
project had written: a project that added a second one lost the framework's
ability to score the dimension at all, which is the incentive exactly
backwards.

Zero rows then returns None (Round 46 站3's rule that a suite which measured
nothing has not earned 100). That rule is right. What was wrong is that the
zero was manufactured by the parser, not reported by the data — so a project
with working benchmarks was indistinguishable from one with none, and the
verdict fell back to the agent's own unverified number.

WHAT THESE TESTS ASSERT, AND A CORRECTION TO THEIR FIRST DRAFT

Station 0 wrote these to say "real table output must yield a number", which
assumed the fix would be a better regex. It should not be: the table is
pytest-benchmark's terminal rendering for humans, and its shape is a function
of the tool version, the column flags and the magnitude of the values. The
tool also writes a machine-readable report on request, and this repo already
consumes exactly that shape for the JavaScript toolchain
(`_score_coverage_summary`, `_score_js_bench`).

So a run whose stdout is the table and nothing else is now correctly scored
None — but for a different reason than before, and the difference matters:
it means the framework did not ask for the report, which is a framework-side
configuration fault, not a project with no benchmarks. Both variants of the
real table are still pinned below, as the evidence for why parsing them is
not the answer.
"""

from __future__ import annotations

import json

import pytest

from harness.tool_runners import _ARTIFACT_MARKER, compute_tool_score

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


def _run_output(table: str, *means_seconds: float) -> str:
    """What `run_tool` hands a scorer: stdout, the marker, then the report."""
    return table + _ARTIFACT_MARKER + _json_report(*means_seconds)


def test_scores_a_real_benchmark_run():
    """Two fast benchmarks that really ran are not "no measurement"."""
    score = compute_tool_score(
        "pytest-benchmark", _run_output(REAL_TABLE_OUTPUT, 0.0000047, 0.0000142), 0)
    assert score is not None, (
        "the framework ran pytest-benchmark, got two measured rows, and "
        "produced no number of its own — so the verdict has nothing to "
        "cross-validate the agent's claim against"
    )
    assert score == pytest.approx(100.0), (
        "both means are microseconds; nothing here is slow"
    )


def test_the_number_comes_from_the_report_not_the_table():
    """The table's own shape must not be able to change the score.

    Same two benchmarks, rendered with the thousands separators that broke
    the old parser. If the score still comes out 100, the rendering is not
    being read — which is the point.
    """
    score = compute_tool_score(
        "pytest-benchmark",
        _run_output(REAL_TABLE_OUTPUT_THOUSANDS, 0.0007547, 0.0009328), 0)
    assert score == pytest.approx(100.0), (
        "754us and 932us are both far under the 1000ms warning threshold — "
        "reading the comma as a decimal point would make them look like "
        "1050ms and 1933ms and cost the project 50 points"
    )


def test_a_run_with_no_report_scores_nothing():
    """Table-only output means the framework never asked for the report.

    Not a project with no benchmarks — a framework that did not pass
    --benchmark-json. Both end in None, and the caller's ledger row is what
    distinguishes them; what must not happen is a score being invented from
    the rendering.
    """
    assert compute_tool_score("pytest-benchmark", REAL_TABLE_OUTPUT, 0) is None
    assert compute_tool_score(
        "pytest-benchmark", REAL_TABLE_OUTPUT_THOUSANDS, 0) is None


def test_scores_a_json_report():
    """The structured path, which is what the fix routes through."""
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


def test_the_tool_spec_asks_for_the_report_it_will_be_scored_from():
    """A scorer that reads a report nobody requested is a half-built mechanism.

    The command and the artifact path must name the same file, or the scorer
    reads a stale report — or none — while the run looks healthy.
    """
    from harness.toolchains import get_tool_spec

    spec = get_tool_spec("pytest-benchmark")
    assert spec is not None
    assert spec.output_artifact, (
        "pytest-benchmark declares no output_artifact, so run_tool never "
        "appends a report and the scorer has nothing structured to read"
    )
    flag = [a for a in spec.cmd if a.startswith("--benchmark-json")]
    assert flag, "the command never asks pytest-benchmark to write a report"
    assert flag[0].split("=", 1)[1] == spec.output_artifact, (
        f"the command writes the report to {flag[0].split('=', 1)[1]!r} and "
        f"run_tool looks for it at {spec.output_artifact!r}"
    )


def test_the_prompt_hands_the_agent_the_same_command_the_framework_runs():
    """Prompt and registry must not drift (the Round 17 shape).

    S4 re-runs the tool itself, so a stale prompt does not corrupt the
    framework's number — but it does send the agent to read a rendering the
    framework has stopped reading, and the two then disagree about what the
    evidence for this dimension even is.
    """
    from pathlib import Path

    from harness.toolchains import get_tool_spec

    spec = get_tool_spec("pytest-benchmark")
    flag = next(a for a in spec.cmd if a.startswith("--benchmark-json"))

    prompt = (Path(__file__).resolve().parents[1]
              / "harness" / "ssi" / "prompts" / "evaluate_dimension.md"
              ).read_text(encoding="utf-8")
    assert flag in prompt, (
        f"evaluate_dimension.md does not give the agent {flag!r}, so the "
        f"command it runs produces no report while the framework's does"
    )
