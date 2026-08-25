"""Round 77 站1 — S4-B asks the run the framework just made.

S4 executes `pytest-cov` itself: gate1_per_fr.yaml declares
`requires_tool_execution: true` for test_coverage, and
`_run_harness_cross_validation` holds the full stdout in `output` and writes
it to `.methodology/gate_evidence/test_coverage_harness.txt`. Forty lines
later S4-B decided "are this FR's tests red?" by regex over the agent's
500-character `tool_evidence` excerpt. Round 67 / Round 72's mother pattern:
the framework computed the truth and the verdict read somewhere else.

Round 76 (1d111daa) scoped that regex per FR. The diagnosis was right —
sibling failures must not block a healthy FR (Round 42) — but the parse it
scoped is over prose the agent pastes, and it turned fail-closed into
fail-open: `if failed_paths: … return []` sits ahead of the `N failed`
summary check, so ONE recognisable FAILED line waived every failure the
regex could not see.

The inputs below are the reproductions from the code review of that commit,
run against HEAD before this station. Each is asserted here against the
framework's own run instead, and each asserts the NUMBER rather than only
that a message came back — the two tests 1d111daa added could not tell a
scoped count from a summary count (see
tests/test_harness_bridge.py::TestCheckTestsFailed).
"""

from __future__ import annotations

import pytest

from core.quality_gate.fr_test_scope import (
    scoped_test_failures,
    waived_test_failures,
)
from harness.harness_bridge import _check_tests_failed

pytestmark = [pytest.mark.core]


def _run(output: str, tool: str = "pytest-cov", rc: int = 1):
    return (tool, output, rc)


def _evidence(text: str) -> dict:
    return {"breakdown": {"test_coverage": {"tool_evidence": text}}}


# The shape harness/toolchains/registry.py's pytest-cov ToolSpec produces:
# `pytest {test_target} --cov=… --cov-report=term-missing -q --tb=no
# --no-header`. Captured verbatim from a real run of that command.
_REAL_OUTPUT = (
    ".F..FFsx.F                                                               [100%]\n"
    "=========================== short test summary info ============================\n"
    "FAILED tests/test_fr01.py::test_fr01_bad - assert False\n"
    "FAILED tests/test_fr01.py::test_fr01_param[3] - assert 3 < 3\n"
    "FAILED tests/test_fr01.py::TestGroup::test_fr01_method_bad - assert False\n"
    "FAILED tests/test_fr02.py::test_fr02_bad - assert False\n"
    "4 failed, 4 passed, 1 skipped, 1 xfailed in 0.01s\n"
)


def test_the_framework_scopes_its_own_run_to_the_fr_being_gated():
    assert scoped_test_failures("FR-01", _run(_REAL_OUTPUT)) == (
        [
            "tests/test_fr01.py::test_fr01_bad",
            "tests/test_fr01.py::test_fr01_param[3]",
            "tests/test_fr01.py::TestGroup::test_fr01_method_bad",
        ],
        ["tests/test_fr02.py::test_fr02_bad"],
    ), "parametrize ids and class methods are this FR's tests too"

    assert scoped_test_failures("FR-02", _run(_REAL_OUTPUT)) == (
        ["tests/test_fr02.py::test_fr02_bad"],
        [
            "tests/test_fr01.py::test_fr01_bad",
            "tests/test_fr01.py::test_fr01_param[3]",
            "tests/test_fr01.py::TestGroup::test_fr01_method_bad",
        ],
    )

    assert scoped_test_failures("FR-03", _run(_REAL_OUTPUT)) == (
        [],
        [
            "tests/test_fr01.py::test_fr01_bad",
            "tests/test_fr01.py::test_fr01_param[3]",
            "tests/test_fr01.py::TestGroup::test_fr01_method_bad",
            "tests/test_fr02.py::test_fr02_bad",
        ],
    ), "an FR with no failing tests of its own owns none of the four"


def test_a_partial_failed_list_no_longer_waives_the_rest():
    """Finding 1 — the most severe, and the one this station exists for.

    Measured at HEAD before this station: this evidence with fr_id='FR-08'
    returned `[]` (PASS), while the identical evidence with fr_id=None
    returned a block. The 19 unexamined failures may all be test_fr08's.
    `tool_evidence` is agent-authored and the same prompt caps it at
    "<first 500 chars of coverage/pytest stdout>", so a partial FAILED list
    is the NORMAL case, not a corner one.
    """
    evidence = _evidence(
        "FAILED tests/test_fr01.py::test_x - AssertionError\n"
        "20 failed, 59 passed in 6.17s"
    )
    # No framework run: the pre-Round-76 rule, which blocked on the summary.
    assert _check_tests_failed(evidence, fr_id="FR-08") == [
        "test_coverage: 20 test(s) FAILED in tool_evidence — gate cannot pass "
        "with failing tests. Fix all failures before re-submitting."
    ]
    # With a framework run, the agent's excerpt is not consulted at all.
    ok = "1 failed, 59 passed in 6.17s\nFAILED tests/test_fr01.py::test_x - e\n"
    assert _check_tests_failed(evidence, fr_id="FR-08", framework_run=_run(ok)) == []
    assert waived_test_failures("FR-08", _run(ok)) == ["tests/test_fr01.py::test_x"]


def test_an_unreconcilable_run_is_not_a_clean_one():
    """The other half of finding 1: a list that does not add up is unreadable.

    `failing_nodeids` returns None whenever the enumerated failures disagree
    with pytest's own counts line, and `scoped_test_failures` turns that
    into "could not scope", never into "nothing failed".
    """
    partial = ("pytest-cov",
               "FAILED tests/test_fr01.py::test_x - e\n20 failed, 59 passed in 6.17s", 1)
    assert scoped_test_failures("FR-08", partial) is None
    assert waived_test_failures("FR-08", partial) == []
    # …and the fall-back is the fail-closed rule, not a pass.
    assert len(_check_tests_failed(
        _evidence("20 failed, 59 passed in 6.17s"),
        fr_id="FR-08", framework_run=partial)) == 1


@pytest.mark.parametrize(
    "fr_id,nodeid,mine",
    [
        # Finding 2 — the unpadded spelling cli/gate_cmds.py:620 explicitly
        # blesses ("Accepts test_fr07.py or test_fr7.py naming"). At HEAD
        # FR-07's own red test was waived as somebody else's.
        ("FR-07", "tests/test_fr7.py::test_x", True),
        ("FR-07", "tests/test_fr07.py::test_x", True),
        # Finding 5 — `fr_pattern in p` was an unanchored substring test, so
        # FR-10's pattern matched test_fr100.py and blocked FR-10 for FR-100's
        # bug: the exact defect Round 76 exists to remove, re-introduced.
        ("FR-10", "tests/test_fr100.py::test_x", False),
        ("FR-100", "tests/test_fr10.py::test_x", False),
        ("FR-100", "tests/test_fr100.py::test_x", True),
        # …and it matched non-test paths and nested directories.
        ("FR-08", "src/test_fr08_util.py::test_x", False),
        # Finding 4 — a failure no FR owns. Not this FR's, and (station 2) not
        # promised to anyone else's gate either.
        ("FR-08", "tests/integration/test_api_flow.py::test_e2e", False),
        ("FR-08", "tests/test_nfr09_ac3.py::test_x", False),
        # The `test_frNN_xxx` name convention CLAUDE.md requires, in a file
        # named for no FR.
        ("FR-08", "tests/test_shared.py::test_fr08_login_ok", True),
    ],
)
def test_ownership_matches_the_frameworks_own_convention(fr_id, nodeid, mine):
    out = f"FAILED {nodeid} - e\n1 failed, 5 passed in 0.5s\n"
    scoped = scoped_test_failures(fr_id, _run(out))
    assert scoped is not None
    assert (scoped[0] == [nodeid]) is mine, scoped
    assert (scoped[1] == [nodeid]) is not mine, scoped


def test_a_collection_error_is_this_frs_problem():
    """Finding 3 — a regression 1d111daa introduced.

    `FAILED\\s+(\\S+?)::` cannot see `ERROR tests/test_fr08.py - ImportError`
    (a collection error has no `::`), so an FR whose own test module fails to
    import passed Gate 1 as long as one sibling FAILED line was present.
    Measured at HEAD: `[]`. Zero of FR-08's tests ran.

    pytest interrupts collection, so this is the whole run — which is why the
    nodeid has no test name and the file-level ownership test has to reach it.
    """
    out = ("ERROR tests/test_fr08.py\n"
           "!!!!!!!! Interrupted: 1 error during collection !!!!!!!!\n"
           "1 error in 0.04s\n")
    scoped = scoped_test_failures("FR-08", _run(out, rc=2))
    assert scoped == (["tests/test_fr08.py"], [])
    violations = _check_tests_failed({}, fr_id="FR-08", framework_run=_run(out, rc=2))
    assert len(violations) == 1
    assert "tests/test_fr08.py" in violations[0]


@pytest.mark.parametrize("fr_id", ["FR-08", "fr08", "FR_08", "FR-8", "  FR-08  "])
def test_every_spelling_canonical_form_accepts_scopes_the_same_way(fr_id):
    """Finding 9 — `^FR-(\\d+)\\s*$` rejected every non-canonical spelling and
    silently reverted to over-blocking.

    Measured at HEAD: fr_id='FR_08' with sibling-only failures reproduced the
    Round 76 bug verbatim, with no diagnostic saying the scope did not apply.
    `--fr-id` is registered as a free string (cli/gate_cmds.py:3074) and
    reaches GateContext.fr_id unnormalised, and core/canonical_form.py handles
    all of these — which is why the scoping goes through `fr_num_str`.
    """
    out = ("FAILED tests/test_fr08.py::test_x - e\n"
           "FAILED tests/test_fr01.py::test_y - e\n"
           "2 failed, 5 passed in 0.5s\n")
    scoped = scoped_test_failures(fr_id, _run(out))
    assert scoped == (["tests/test_fr08.py::test_x"], ["tests/test_fr01.py::test_y"])


@pytest.mark.parametrize(
    "label,output",
    [
        ("ansi", "\x1b[31mFAILED\x1b[0m tests/test_fr01.py::t - e\n"
                 "4 failed, 10 passed in 1.0s"),
        ("pytest -v inline", "tests/test_fr01.py::test_a FAILED [ 66%]\n"
                             "4 failed, 1 passed in 0.00s"),
        ("--no-summary", "4 failed, 1 passed in 0.00s"),
        ("truncated mid-list", "FAILED tests/test_fr01.py::test_a - e\n"
                               "FAILED tests/test_fr0"),
    ],
)
def test_output_the_framework_cannot_read_falls_back_fail_closed(label, output):
    """Finding 8 — at HEAD every one of these silently reverted to the whole-run
    rule with no indication that the per-FR path was never entered.

    They still fall back, because the fall-back is the safe direction. What
    changes is that they can no longer be mistaken for a scoped verdict: the
    scope function says None, so station 2 records nothing as waived and
    nothing is waived.
    """
    run = _run(output)
    assert scoped_test_failures("FR-08", run) is None, label
    assert waived_test_failures("FR-08", run) == [], label
    assert len(_check_tests_failed(
        _evidence("4 failed, 10 passed in 1.0s"), fr_id="FR-08",
        framework_run=run)) == 1, label


def test_a_js_runner_is_left_on_the_whole_run_rule_deliberately():
    """vitest/jest per-test outcomes are not readable here, and the framework's
    own JS run is NOT per-FR scoped — blocking on it would hand JS projects
    the defect this round removes from Python ones. So they keep the
    pre-Round-76 rule, and `_PER_TEST_OUTCOME_TOOLS` says so by name rather
    than leaving it to whether a pytest regex happens to miss.
    """
    vitest = ("vitest-cov", "Tests  2 failed | 10 passed\n Duration  1.03s\n", 1)
    assert scoped_test_failures("FR-08", vitest) is None
    assert len(_check_tests_failed(
        _evidence("Tests 2 failed | 10 passed"), fr_id="FR-08",
        framework_run=vitest)) == 1


def test_the_block_message_carries_the_count_and_the_nodeids():
    """Round 45: a verdict may not outlive its proof.

    The message names every failing test it blocked on, so the operator does
    not have to re-derive which ones they were from a count.
    """
    out = ("FAILED tests/test_fr08.py::test_a - e\n"
           "FAILED tests/test_fr08.py::test_b - e\n"
           "FAILED tests/test_fr01.py::test_c - e\n"
           "3 failed, 5 passed in 0.5s\n")
    violations = _check_tests_failed({}, fr_id="FR-08", framework_run=_run(out))
    assert len(violations) == 1
    assert violations[0].startswith("test_coverage: 2 of FR-08's own test(s) FAILED")
    assert "tests/test_fr08.py::test_a" in violations[0]
    assert "tests/test_fr08.py::test_b" in violations[0]
    assert "tests/test_fr01.py::test_c" not in violations[0], (
        "another FR's failing test must not appear in this FR's block message "
        "— that is what sends an FR to fix code its own SCOPE RULES forbid it "
        "to touch")


def test_a_green_framework_run_needs_no_evidence_at_all():
    green = _run("59 passed in 1.2s\n", rc=0)
    assert scoped_test_failures("FR-08", green) == ([], [])
    assert _check_tests_failed(_evidence("20 failed"), fr_id="FR-08",
                               framework_run=green) == []
