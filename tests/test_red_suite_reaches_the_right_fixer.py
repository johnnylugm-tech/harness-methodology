"""A test that fails only in the whole suite must reach a fixer that can see it.

Round 96. Measured on taskq-final's Phase 8: FR-07 spent **22 rounds and 9.5
hours** (09-04 06:27 -> 15:49) producing twenty-two identical ledger rows —
`gate:tests-failed-declared`, `declared=0 measured=5`, the same five tests every
time. Sixty-seven of that whole P1-P8 run's 620 dispatches went to that one FR.
What finally closed it is in the project's own history:

    e1befbf test(FR-07): repair 5 spec cases erroring in whole-suite runs

The five tests passed when run alone and failed when run with the suite —
shared database state, fixed by adding a reset fixture. Nothing the framework
dispatched for 22 rounds was looking for that.

THREE LINKS, EACH BROKEN SEPARATELY

1. The framework files a red suite under `tool_score_fabrication`, whose
   registered headline reads "Claimed dimension score could not be reproduced
   by running the tool". The agent claimed nothing; its tests are red. The same
   key goes into `core.lessons.record_gate_block`, so the cross-run memory
   learns the wrong lesson. `harness/gate_checks.py` records why Round 35 站3
   split `infra_fail` out of that same key — outcomes that carry opposite
   instructions must not share one — and this is that rule unapplied.

2. `_extract_block_reason` looks for a line containing BOTH `[BLOCKED]` and a
   detail key. `_format_block_diagnostic` writes `GATE 1 BLOCKED` (no brackets)
   and `  [1] {kind}: {headline}`. No line in the repository has both, so the
   scanner returned "" for every one of those 22 rounds.

3. `_classify_snapshot_failure` therefore had only the snapshot to go on, and
   `_capture_tool_snapshot` runs `pytest {test_file}` — ONE FILE — while the
   gate blocked on `run_tool`'s whole-directory run. Single file: green. So:
   no failures + `test_coverage` in failing_dims + "passed" in output
   => LOW_COVERAGE => COVERAGE-FIX, "You are a coverage fixer", 22 times.

The gate was right to block: `select_fr_outcomes` had already scoped the five
failures to FR-07's own tests. Nothing here changes what blocks. What changes
is that the reason keeps its name, the name survives the trip to the router,
and the fixer is shown the run the gate actually judged.
"""

from __future__ import annotations

import pytest

from core.quality_gate.block_reason import derive_block_reasons

pytestmark = [pytest.mark.core]

#: The violation `harness/gate_checks.py::_check_tests_failed` emits, verbatim
#: in shape. Two nodeids so a test can tell "carried the list" from "carried a
#: count".
_RED_SUITE_VIOLATION = (
    "test_coverage: 2 of FR-07's own test(s) FAILED in the harness's own run "
    "— gate cannot pass while they are red: "
    "03-development/tests/test_fr07.py::test_a, "
    "03-development/tests/test_fr07.py::test_b"
)


class _Result:
    """Duck-typed GateResult, the shape harness_bridge raises with."""
    dimensions: list = []
    score = 0.0
    open_critical = 1
    open_high = 0
    quality_complete = False
    rounds_used = 0


def test_a_red_suite_is_not_filed_as_a_fabricated_score():
    """A1. The name of the cause is the first thing every reader gets."""
    from harness.gate_checks import RED_SUITE_DETAIL_KEY

    reasons = derive_block_reasons(
        1, _Result(), {RED_SUITE_DETAIL_KEY: [_RED_SUITE_VIOLATION]},
    )
    assert len(reasons) == 1, reasons
    reason = reasons[0]
    assert reason.kind != "tool_score_fabrication", (
        "a red suite is not a fabricated score — the agent claimed nothing, its "
        "tests are failing. Sharing the key means last_block.md, the console "
        "and core.lessons all describe it as the wrong defect"
    )
    assert "fail" in reason.headline.lower() or "red" in reason.headline.lower(), (
        f"the headline does not say what happened: {reason.headline!r}"
    )
    assert _RED_SUITE_VIOLATION in reason.items


def test_the_remediation_names_the_run_that_reproduces_it():
    """The whole incident is that the agent could not reproduce the failure.

    A remediation that does not name the command is the same dead end in
    politer words.
    """
    from harness.gate_checks import RED_SUITE_DETAIL_KEY

    reason = derive_block_reasons(
        1, _Result(), {RED_SUITE_DETAIL_KEY: [_RED_SUITE_VIOLATION]},
    )[0]
    assert "pytest" in reason.remediation, reason.remediation
    assert "suite" in reason.remediation.lower() or "whole" in reason.remediation.lower(), (
        "the remediation must say the failure is only visible in the whole-suite "
        f"run — running the file alone is what the agent already did: "
        f"{reason.remediation!r}"
    )


def _rendered_block_diagnostic(details: dict) -> str:
    """What finalize-gate actually prints, from the one function that prints it."""
    from pathlib import Path

    from cli.gate_cmds import _format_block_diagnostic
    from harness.harness_bridge import GateBlockedError, GateResult

    exc = GateBlockedError(
        1,
        GateResult(gate_num=1, score=0.0, dimensions=[], open_critical=1,
                   open_high=0, quality_complete=False, rounds_used=0),
        details=details,
    )
    return _format_block_diagnostic(exc, 1, 8, "FR-07", 3, Path("/tmp/nowhere"))


def test_the_router_reads_the_block_the_framework_printed(tmp_path, monkeypatch):
    """A2. The two ends of this channel are pinned in one test on purpose.

    They drifted because nothing ever fed one into the other: the writer's
    format and the reader's predicate were edited in different files, years
    apart, and the only evidence they disagreed was 22 wasted rounds.
    """
    from harness.gate_checks import RED_SUITE_DETAIL_KEY

    monkeypatch.chdir(tmp_path)
    rendered = _rendered_block_diagnostic({RED_SUITE_DETAIL_KEY: [_RED_SUITE_VIOLATION]})

    from cli.fr_cmds import _parse_gate_output
    _, _, block = _parse_gate_output(rendered)
    assert block, (
        "the router extracted nothing from the framework's own block "
        f"diagnostic. Rendered output was:\n{rendered}"
    )
    assert block.kind == RED_SUITE_DETAIL_KEY, block
    assert any("test_fr07.py::test_a" in item for item in block.items), block.items


def test_the_scanner_still_reads_the_older_detail_keys(tmp_path, monkeypatch):
    """Negative control: fixing the predicate must not lose what it did catch.

    `tool_score_fabrication` and `tool_evidence_missing` are the two keys the
    old condition named. A genuine fabrication still has to arrive.
    """
    monkeypatch.chdir(tmp_path)
    rendered = _rendered_block_diagnostic(
        {"tool_score_fabrication": ["linting: agent 100.0, harness 42.0"]}
    )
    from cli.fr_cmds import _parse_gate_output
    _, _, block = _parse_gate_output(rendered)
    assert block and block.kind == "tool_score_fabrication", block


def test_a_clean_agent_output_yields_no_block(tmp_path, monkeypatch):
    """Negative control: a passing gate must not manufacture a block reason."""
    monkeypatch.chdir(tmp_path)
    from cli.fr_cmds import _parse_gate_output
    passed, dims, block = _parse_gate_output('{"pass": true, "failing_dims": []}')
    assert passed is True
    assert dims == []
    assert not block


def test_a_red_suite_routes_to_the_test_fixer_not_the_coverage_fixer():
    """A3. The classifier decides from the framework's fact, not the snapshot.

    The snapshot below is exactly what taskq-final's FR-07 produced for 22
    rounds — a green single-file run — and `failing_dims` carries
    `test_coverage`, because that is the prefix the violation is written under.
    Every input here says LOW_COVERAGE except the one that knows what happened.
    """
    from cli.fr_cmds import _classify_snapshot_failure
    from harness.gate_checks import RED_SUITE_DETAIL_KEY

    green_single_file = (
        "pytest 03-development/tests/test_fr07.py -v --tb=short (exit 0):\n"
        "collected 31 items\n"
        "31 passed in 4.02s\n"
    )
    assert _classify_snapshot_failure(
        green_single_file, failing_dims=["test_coverage"],
    ) == "LOW_COVERAGE", "fixture drift: this is the input that used to misroute"

    assert _classify_snapshot_failure(
        green_single_file, failing_dims=["test_coverage"],
        block_kind=RED_SUITE_DETAIL_KEY,
    ) == "SUITE_TEST_FAILURE"


def test_the_framework_fact_outranks_every_snapshot_heuristic():
    """It goes first, because each heuristic below it reads a run that cannot
    show the failure. A snapshot that also shows an unrelated import error
    must not send this to ENV."""
    from cli.fr_cmds import _classify_snapshot_failure
    from harness.gate_checks import RED_SUITE_DETAIL_KEY

    for snapshot in (
        "ModuleNotFoundError: No module named 'taskq'",
        "AttributeError: 'str' object has no attribute 'loads'",
        "31 passed in 4.02s",
        "",
    ):
        assert _classify_snapshot_failure(
            snapshot, failing_dims=["test_coverage"],
            block_kind=RED_SUITE_DETAIL_KEY,
        ) == "SUITE_TEST_FAILURE", snapshot


def test_without_the_framework_fact_nothing_changes():
    """Negative control: every existing classification is untouched."""
    from cli.fr_cmds import _classify_snapshot_failure

    cases = {
        "ModuleNotFoundError: No module named 'taskq'": "ENV",
        "AttributeError: 'str' object has no attribute 'loads'": "ISOLATION_LIKELY",
        "status_code=401 unauthorized": "ISOLATION",
        "E   AssertionError: expected 3": "MISSING_FEATURE",
    }
    for snapshot, expected in cases.items():
        assert _classify_snapshot_failure(snapshot) == expected, snapshot


def test_the_test_fixer_prompt_carries_the_failing_tests_and_how_to_see_them():
    """The prompt is the only thing the fixer gets. Both halves have to be in it.

    `build_test_fix_prompt`'s step 3 told the agent to run
    `pytest {test_file} -q` — the command that shows these tests green. A fixer
    verifying its own work with that command cannot tell a fix from no fix.
    """
    from pathlib import Path

    from cli.fr_prompts.fix import build_test_fix_prompt

    nodeids = [
        "03-development/tests/test_fr07.py::test_a",
        "03-development/tests/test_fr07.py::test_b",
    ]
    prompt = build_test_fix_prompt(
        "FR-07", 8, Path("/tmp/p"), Path("/tmp/p/srs.md"),
        "03-development/tests/test_fr07.py", "03-development/src",
        tool_snapshot="(suite run)", suite_only_failures=nodeids,
    )
    for nodeid in nodeids:
        assert nodeid in prompt, f"{nodeid} not named in the prompt"
    # The DIRECTORY, not the file. Round 96's own counter-proof caught this:
    # asserting `"03-development/tests" in prompt` passes on
    # `pytest 03-development/tests/test_fr07.py` too, so reverting the
    # reproduce command to the single-file form left the guard green — a guard
    # that cannot tell the defect from the fix.
    assert "python3 -m pytest 03-development/tests -q" in prompt, (
        "the prompt does not name a whole-suite command, so the fixer cannot "
        "reproduce what the gate blocked on"
    )
    assert "pollution" in prompt.lower() or "shared state" in prompt.lower(), (
        "the prompt's only isolation story is the 401/HMAC one; a test that "
        "passes alone and fails in the suite is a different shape and the "
        "fixer is given no hint of it"
    )


def test_the_test_fixer_prompt_is_unchanged_without_suite_only_failures():
    """Negative control: the existing ISOLATION path must not gain suite text."""
    from pathlib import Path

    from cli.fr_prompts.fix import build_test_fix_prompt

    prompt = build_test_fix_prompt(
        "FR-07", 8, Path("/tmp/p"), Path("/tmp/p/srs.md"),
        "03-development/tests/test_fr07.py", "03-development/src",
        tool_snapshot="401 unauthorized",
    )
    assert "pollution" not in prompt.lower()
