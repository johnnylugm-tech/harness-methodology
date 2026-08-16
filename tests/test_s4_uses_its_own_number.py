"""The framework ran the tool, got a number, and kept the agent's (Round 54 站0).

S4 exists so a dimension's score is not whatever the agent typed. It runs each
tool-scored dimension's tool itself and compares. Three outcomes are handled:

    agent said N/A, framework got a number   -> the framework's number is used
    framework got no number                  -> marked agent_unverified, blocked
    framework's number < threshold <= agent's -> fabrication, blocked

and a fourth is not. When both numbers are above the threshold,
`_run_harness_cross_validation` appends nothing and returns — and
`_dim_entry["score"]` still holds the agent's. The framework measured, and
threw the measurement away.

Station 0 measured the consequence on the exact tree each gate judged, not on
today's tree, because drift would otherwise explain any difference.
`git archive c1af37e` — the commit whose message is taskq's
`release(P6): Gate4 PASS score=97.2 — pipeline complete` — and running the
framework's own scanner over it:

    recorded score            100.0
    framework's own scanner    80.0
    the agent's own evidence
      line (total=6 source
      files; with_handler=4)   66.7

Three numbers for one dimension, and the one the verdict carries is the only
one nobody computed. The threshold is 80, so the framework's number is a pass
by exactly zero margin — recorded as a perfect score.

This is Round 35 站3's principle (`the framework's own number precedes the
agent's claim`) with only its `agent_score is None` half built.

The fabrication violation is deliberately left alone. "The agent claimed a pass
the tool contradicts" is a different and worse fact than "the agent's number
was approximately right", and Round 13's routing rule is that two different
failures must not arrive under one heading.

**The interaction that has to be preserved.** Round 51 站3 marks
test_coverage and integration_coverage `stubbed_boundary` *before* S4 runs
(`harness_bridge.py:3044` vs `:3081`), and `measurement_scope` reads that
marker to drop them from `weight_covered`. Both are `requires_tool_execution`,
so S4 sees them. Writing `score_source = framework` unconditionally would erase
Round 51 站3 without a word. The two markers answer different questions — "who
produced this number" and "is this number about the delivered code" — and only
the first is S4's to answer.
"""

from __future__ import annotations

import pytest


class TestS4ScoreVerdict:
    """The decision for one dimension, once both numbers are in.

    Tested through a public pure function rather than by patching five private
    seams around `finalize_gate` — `tests/test_patch_discipline.py` refuses
    that, and Round 32 站4 already answered this exact question the same way
    with `s4_block_details`.
    """

    def test_the_framework_number_wins_when_both_are_above_threshold(self):
        from harness.harness_bridge import SCORE_SOURCE_FRAMEWORK, s4_score_verdict

        verdict = s4_score_verdict(
            agent_score=100.0, harness_score=80.0, threshold=80.0,
            current_source=None,
        )

        assert verdict["score"] == 80.0, (
            "taskq's Gate 4 recorded error_handling 100.0 while the framework's "
            "own scanner on that same commit measured 80.0"
        )
        assert verdict["score_source"] == SCORE_SOURCE_FRAMEWORK
        assert verdict["fabrication"] is False, (
            "both numbers pass — the agent claimed nothing false, it was "
            "merely imprecise, and Round 13's routing keeps those apart"
        )

    def test_a_claimed_pass_the_tool_contradicts_is_still_fabrication(self):
        """The positive control: the existing block must survive the change."""
        from harness.harness_bridge import s4_score_verdict

        verdict = s4_score_verdict(
            agent_score=95.0, harness_score=60.0, threshold=80.0,
            current_source=None,
        )
        assert verdict["score"] == 60.0
        assert verdict["fabrication"] is True

    @pytest.mark.parametrize("agent", [100.0, 80.0])
    def test_a_stubbed_boundary_marker_survives(self, agent):
        """`who measured it` may not overwrite `what it is a measurement of`.

        Round 51 站3 puts this marker on test_coverage and integration_coverage
        when the suite replaced a declared boundary, and `measurement_scope`
        drops them from `weight_covered` because of it. The number is still
        replaced with the framework's — both describe the same stubbed suite,
        and only one of them was measured.
        """
        from harness.harness_bridge import (
            SCORE_SOURCE_STUBBED_BOUNDARY, s4_score_verdict,
        )

        verdict = s4_score_verdict(
            agent_score=agent, harness_score=88.0, threshold=80.0,
            current_source=SCORE_SOURCE_STUBBED_BOUNDARY,
        )
        assert verdict["score"] == 88.0
        assert verdict["score_source"] == SCORE_SOURCE_STUBBED_BOUNDARY
