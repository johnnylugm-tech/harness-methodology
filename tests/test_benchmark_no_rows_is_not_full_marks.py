"""A benchmark suite that produced no measurement did not measure anything.

Round 46 站0. `harness/ssi/prompts/evaluate_dimension.md` states the rule the
framework wants: "**No benchmarks** … → score is *None* (dimension not yet
applicable — **not a free 100**)". The scorer implements half of it.

`_score_pytest_benchmark` returns None on exit 5 and (since Round 32 站4) on
every other non-zero code, but on `rc == 0` it walks a row regex that can only
*subtract*. Zero rows therefore means zero subtractions, and the function
returns the initial 100 — the exact "free 100" the prompt forbids.

taskq-advance's `gate4_result.json` records `performance: 100.0` with this
verbatim justification:

    pytest-benchmark (--benchmark-only): rc=0, 7188 skipped (no benchmark
    tests in suite). Scorer contract: rc=0 with no benchmark rows →
    score = max(0.0, 100) = 100

Honest boundary: the same tree today emits one row (`test_placeholder_benchmark`,
added in `1b98c93`, the first of that phase's two release commits), so whether
the recorded 100 came through the zero-row branch or the one-row branch cannot
be settled from the artifact. The branch itself is reachable and wrong either
way, which is what this file pins.

Round 32 站4's comment already wrote the reasoning down for the non-zero half:
"the row regex below only ever SUBTRACTS, so output with no benchmark rows
scores 100." It fixed the crash case and left the clean-exit case.
"""

from __future__ import annotations

import pytest

from harness.tool_runners import compute_tool_score

pytestmark = [pytest.mark.core]


_ONE_ROW = """\
--------------- benchmark: 1 tests ---------------
Name (time in ms)            Mean       Max
--------------------------------------------------
test_read_state            0.0623    0.1970
--------------------------------------------------
1 passed, 271 skipped in 2.54s
"""

_NO_ROWS = """\
sssssssssssssssssssssssssssssssssssssssssssssssss
272 skipped in 1.98s
"""


def test_a_clean_exit_with_no_benchmark_rows_scores_nothing():
    """The prompt's rule, applied to the branch that does not honour it."""
    assert compute_tool_score("pytest-benchmark", _NO_ROWS, 0) is None, (
        "rc=0 with no benchmark rows means the suite ran and measured nothing. "
        "That is 'not applicable', not 'perfect'."
    )


def test_a_real_fast_benchmark_still_scores_100():
    """The fix must not turn a genuinely fast suite into an abstention."""
    assert compute_tool_score("pytest-benchmark", _ONE_ROW, 0) == 100.0


def test_a_slow_benchmark_is_still_penalised():
    slow = _ONE_ROW.replace("0.0623    0.1970", "3500.0    4000.0")
    assert compute_tool_score("pytest-benchmark", slow, 0) == 50.0


def test_exit_five_is_unchanged():
    """Round 32 站4's half. Pinned so this round cannot regress it."""
    assert compute_tool_score("pytest-benchmark", _NO_ROWS, 5) is None
