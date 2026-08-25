"""Round 77 站3 — one prompt, one statement about `tests_failed`.

Round 76 rewrote the prose block near the bottom of the GATE1 prompt and left
the JSON schema comment above it alone, so one rendered prompt carried both
rules twelve lines apart. Verified byte-level in the golden that commit's
follow-up regenerated, `tests/golden/fr_prompts/gate1.txt`:

    line 64   "tests_failed": <int>,   // REQUIRED: must be 0 — any failed
                                       //           test blocks the gate
    line 76   …sibling-only failures are logged + skipped (their owning FR
              catches them)

An agent reading top-down obeys the first, writes the sibling-inflated raw
summary count, and enters a fix loop on other FRs' tests — the behaviour the
commit existed to remove, and one the run-all SCOPE RULES explicitly forbid
("DO NOT implement any FR OTHER than " + frId). Round 17's prompt-to-gate
drift with both statements inside a single prompt.

The station also deletes the instruction to "Include the FAILED path lines
(one per failed test) in tool_evidence before the summary line". Two measured
consequences, both removed at the cause rather than patched:

  * 20 FAILED lines are 1430 characters, and the same JSON block specifies
    `"<first 500 chars of coverage/pytest stdout>"` for that field. An agent
    obeying both writes 500 characters of FAILED lines and nothing else, and
    `_validate_tool_content(evidence, "pytest-cov", "test_coverage")` returns
    two violations — "does not match any expected output pattern" and
    "contains no coverage measurement" — which harness_bridge raises as
    `tool_evidence_missing` and cli/fr_cmds.py classifies as "the sub-agent
    fabricated scores". The agent that followed the instruction is told it
    fabricated.
  * that same eviction takes the `N passed / N skipped` summary line with it,
    so `_parse_skip_counts` returns None and Round 46 站2's `gate:test-skips`
    ledger row is never written — for exactly the FRs that have failing
    tests. Measured: full evidence gives (20, 50) and a 40% WARN; the
    first-500 form gives None and no WARN.

After Round 77 站1 the harness measures this FR's failures from its own
`pytest-cov` run, so it never needed the agent's FAILED lines at all.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from cli.fr_prompts.gate import TESTS_FAILED_RULE

pytestmark = [pytest.mark.core]

REPO = Path(__file__).resolve().parents[1]
GOLDEN = REPO / "tests" / "golden" / "fr_prompts"
_GATE1_GOLDENS = ("gate1.txt", "gate1_blocked.txt")


def _lines_mentioning_tests_failed(text: str) -> list[str]:
    """Every line about the FIELD, not about failing tests in general.

    Anchored on the backticked/quoted field name so an unrelated sentence
    ("gate cannot pass with failing tests") is not swept in.
    """
    return [ln for ln in text.splitlines() if "tests_failed" in ln]


@pytest.mark.parametrize("name", _GATE1_GOLDENS)
def test_every_statement_about_the_field_comes_from_the_one_rule(name):
    """The rendered prompt may say this in more than one place. It may not say
    two different things."""
    mentions = _lines_mentioning_tests_failed((GOLDEN / name).read_text("utf-8"))
    assert mentions, f"{name} no longer mentions tests_failed at all"
    for line in mentions:
        assert TESTS_FAILED_RULE in line, (
            f"{name} states a rule for `tests_failed` that is not "
            f"cli/fr_prompts/gate.py::TESTS_FAILED_RULE:\n"
            f"  {line.strip()}\n"
            f"Two statements twelve lines apart is how Round 76 shipped a "
            f"prompt telling the agent both 'must be 0 — any failed test "
            f"blocks the gate' and 'sibling-only failures are logged + "
            f"skipped'. Render from the constant; do not restate it."
        )


@pytest.mark.parametrize("name", _GATE1_GOLDENS)
def test_the_prompt_no_longer_tells_the_agent_to_paste_failed_lines(name):
    """S4-B reads the harness's own run (Round 77 站1). Asking for the FAILED
    lines in a field the same prompt caps at 500 characters put an obedient
    agent into `tool_evidence_missing`, and evicted the summary line the
    `gate:test-skips` ledger row is parsed from."""
    text = (GOLDEN / name).read_text("utf-8")
    assert "FAILED path lines" not in text, (
        "the instruction is back; it costs the agent an S3 fabrication block "
        "and costs the run its skip ledger row — see this module's docstring")
    # The cap that made it impossible is still stated, so the conflict cannot
    # be re-created by putting the instruction back without noticing.
    assert re.search(r"first 500 chars of coverage/pytest stdout", text), (
        "tool_evidence's size cap is no longer stated in the prompt — the "
        "check above is only meaningful while it is")


@pytest.mark.parametrize("name", _GATE1_GOLDENS)
def test_the_prompt_does_not_promise_another_frs_gate_will_catch_it(name):
    """Round 76 told the agent "their owning FR catches them". The Phase 3 FR
    loop is forward-only and S4-B runs only at `gate_num == 1`, so an FR
    already behind never re-runs; a failing test in a file no FR owns has no
    owning gate at all. The real executor is advance-phase."""
    text = (GOLDEN / name).read_text("utf-8")
    assert "owning FR catches" not in text
    assert "owning FRs' gates will catch" not in text


def test_the_rule_says_which_tests_are_this_frs():
    """A rule that only says "this FR's failures" tells the agent nothing it
    can apply. The naming convention is the operative part, and it is the same
    one `test_suite_run.select_fr_outcomes` scopes by."""
    assert "test_fr<NN>.py" in TESTS_FAILED_RULE
    assert "test_fr<NN>_" in TESTS_FAILED_RULE
