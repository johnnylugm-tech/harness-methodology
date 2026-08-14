"""
tests/test_peer_review_prompt_citation_rule.py — Regression test pinning the
line-range citation rule added to render_build_b_prompt in js_blocks.py.

Background (2026-08-14): run-all.js Phase 1 halt on taskq-super. Agent B's
holistic peer review cited `TEST_INVENTORY.yaml:791-860` for a file that
contained only 859 lines (off-by-one). The 4 approval JSONs it persisted all
carried the bad citation, and advance-phase's `unresolvable_citations`
correctly rejected them — but the orchestrator had no opportunity to
self-correct at peer-review time.

The defensive fix has 3 layers:
  1. Prompt rule (this test pins it): SCHEMA REQUIREMENTS tells Agent B to
     verify `wc -l <path>` before writing range citations.
  2. Pre-write validation (cmd_write_approval, see
     test_write_approval_citation_validation.py): blocks bad citations before
     they land on disk.
  3. Retry on reject (render_persist_approval attempt-aware wrapper +
     spec_phase1.runPeerReview try/catch): surfaces stderr back into the
     next Agent B round.

If layer 1's prompt rule is ever dropped, layers 2 and 3 still catch the
bug — but layer 1 is the cheapest defense (LLM self-corrects without a
round trip). This test ensures layer 1 stays in place.

The function under test is render_build_b_prompt in
harness/scripts/workflowgen/js_blocks.py (the JS-source-of-truth for the
phase1/phase2 buildBPrompt code emitted into the generated workflow JS
files). It is a Python function returning a JS code string; the string is
concatenated into the final phase*.js / run-all.js by
generate_workflows.py.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# js_blocks.py lives at harness/scripts/workflowgen/js_blocks.py; we add its
# grandparent (harness/) to sys.path so `scripts.workflowgen.js_blocks` imports
# cleanly when pytest is run from the project root.
_HARNESS_ROOT = Path(__file__).resolve().parents[1]
if str(_HARNESS_ROOT) not in sys.path:
    sys.path.insert(0, str(_HARNESS_ROOT))

from scripts.workflowgen.js_blocks import render_build_b_prompt  # noqa: E402


# ---------------------------------------------------------------------------
# The prompt MUST teach Agent B to verify range citation end <= file length
# ---------------------------------------------------------------------------

class TestBuildBPromptCitationRule:

    @pytest.fixture
    def rendered_prompt(self) -> str:
        """Render buildBPrompt's JS source with the canonical phase-1
        parameters (matches the call inside spec_phase1.py)."""
        return render_build_b_prompt(
            min_reason_chars=40,
            docs_embedded_note=(
                "looks for PURE basenames like \"SRS.md\", \"TEST_INVENTORY.yaml\", "
                "NOT descriptive strings like \"SRS.md §1-§9 full content\". "
                "Use bare basenames only."
            ),
            critical_docs_note=(
                "for Phase 1, `docs_embedded` MUST include \"SRS.md\" regardless "
                "of which deliverable you are reviewing. The harness verifier "
                "(_REQUIRED_EMBEDDED_DOCS[1]) rejects any P1 approval missing it."
            ),
            evidence_type_note=(
                "real_invention=truly new requirement (escalates to high); "
                "over_interpretation=ambiguous canonical phrase, missing DERIVED "
                "tag (caps at medium); methodology_artifact=framework-side gap, "
                "sha256/regex tables etc. (always low)."
            ),
        )

    def test_prompt_mentions_range_citation_rule(self, rendered_prompt: str):
        assert "range citation" in rendered_prompt or "range citations" in rendered_prompt, (
            "buildBPrompt must teach Agent B about range citation rules"
        )

    def test_prompt_warns_about_off_by_one(self, rendered_prompt: str):
        assert "off-by-one" in rendered_prompt.lower() or "exceeds" in rendered_prompt.lower(), (
            "buildBPrompt must warn Agent B about off-by-one / range-exceeds-file"
        )

    def test_prompt_instructs_wc_l_verification(self, rendered_prompt: str):
        assert "wc -l" in rendered_prompt, (
            "buildBPrompt must instruct Agent B to run `wc -l <path>` before "
            "writing range citations"
        )

    def test_prompt_says_end_must_not_exceed(self, rendered_prompt: str):
        # The rule: "the end line M MUST NOT exceed the file's actual line count".
        # The exact string is encoded with escaped quotes in JS; check the
        # rendered (un-escaped) form so the assertion is robust to whitespace.
        rule_fragment = "MUST NOT exceed"
        assert rule_fragment in rendered_prompt, (
            f"buildBPrompt must contain {rule_fragment!r} to instruct Agent B "
            f"that range end cannot exceed file length"
        )

    def test_prompt_mentions_advance_phase_block(self, rendered_prompt: str):
        # The rule must name the consequence (advance-phase blocks) so Agent
        # B knows it's a hard failure, not just a soft preference.
        assert "advance-phase" in rendered_prompt.lower(), (
            "buildBPrompt must mention advance-phase as the thing that blocks"
        )


# ---------------------------------------------------------------------------
# render_persist_approval wrapper prompt — attempt-aware (retry shows stderr)
# ---------------------------------------------------------------------------

class TestPersistApprovalAttemptAware:

    @pytest.fixture
    def rendered_persist(self) -> str:
        """Render render_persist_approval with canonical phase-1 parameters."""
        from scripts.workflowgen.js_blocks import render_persist_approval
        return render_persist_approval(
            synthesize_reason=False,
            use_schema_verdict=True,
            label_prefix="persist",
            phase_label="Persist Approval",
        )

    def test_attempt_1_prompt_runs_baseline(self, rendered_persist: str):
        # attempt === 1 path — the original behavior is preserved.
        assert "SHELL WRAPPER AGENT" in rendered_persist
        assert "[write-approval] OK" in rendered_persist

    def test_retry_prompt_carries_previous_stderr(self, rendered_persist: str):
        # On attempt > 1 the wrapper sees the previous lastErr so it can
        # surface stderr back to the orchestrator.
        assert "lastErr" in rendered_persist or "Previous attempt stderr" in rendered_persist, (
            "render_persist_approval must thread previous stderr into the "
            "retry prompt so the wrapper can report it"
        )

    def test_retry_prompt_names_citation_block_signal(self, rendered_persist: str):
        # The retry prompt must explicitly call out the citation-block signal
        # so the wrapper knows how to report it.
        assert "BLOCKED: citation(s) do not resolve" in rendered_persist, (
            "render_persist_approval retry prompt must name the BLOCKED "
            "stderr signature so the wrapper can surface it"
        )

    def test_retry_prompt_instructs_wc_l_for_agent_b(self, rendered_persist: str):
        # The retry prompt should remind the orchestrator to send Agent B
        # the wc -l hint (this surfaces into the wrapper's report).
        assert "wc -l" in rendered_persist, (
            "render_persist_approval retry prompt must include the wc -l "
            "reminder so the orchestrator can rebuild the citation list"
        )


# ---------------------------------------------------------------------------
# v33b: prose-shape citation defense (added 2026-08-14, after taskq-super P2
# ADR.md crash). Agent B saw `taskq_api.app:app aligns with SAD §1.2` in
# ADR.md:50 and wrote it back as a citation — the validator correctly
# rejected it, the 3× retry loop on Phase 2 abLoop threw, run-all halted.
#
# The fix is a positive example + a negative example in the prompt, plus
# Phase 2's missing persist_error re-dispatch (pinned in
# tests/test_phase2_persist_error_redispatch.py).
# ---------------------------------------------------------------------------

class TestBuildBPromptPositiveAndNegativeExamples:
    """Layer 1 (prose-shape): pin that buildBPrompt teaches Agent B what a
    citation IS (positive example) and what prose shapes to AVOID (negative
    example + "digits after `:`" rule)."""

    @pytest.fixture
    def rendered(self) -> str:
        return render_build_b_prompt(
            min_reason_chars=40,
            docs_embedded_note="looks for PURE basenames",
            critical_docs_note="for Phase 1, docs_embedded MUST include SRS.md",
            evidence_type_note="real_invention vs over_interpretation vs methodology_artifact",
        )

    def test_prompt_has_positive_citation_example(self, rendered: str):
        # Mirror the fix: a concrete `<rel_path>:<digits>` shape Agent B can
        # pattern-match against. Pinning the exact substring prevents future
        # edits from accidentally dropping the example.
        assert "SRS.md:42" in rendered, (
            "buildBPrompt must include a positive citation example "
            "(SRS.md:42) so Agent B has a concrete shape to copy"
        )

    def test_prompt_has_negative_prose_example(self, rendered: str):
        # Mirror the bug shape: `taskq_api.app:app aligns with ...` is
        # prose, not a citation. The prompt must name this exact pattern
        # so Agent B does not reproduce it.
        assert "taskq_api.app:app" in rendered, (
            "buildBPrompt must include the negative example "
            "(taskq_api.app:app aligns with §X.Y) so Agent B knows "
            "prose-after-colon is rejected"
        )

    def test_prompt_requires_digits_after_colon(self, rendered: str):
        # The validator requires DIGITS after `:`; the rule must say so
        # explicitly so Agent B self-corrects when its LLM tries to write
        # prose.
        assert "DIGITS" in rendered and "`:`" in rendered, (
            "buildBPrompt must state that the part after `:` must be DIGITS"
        )


class TestPhase6InlineCitationRule:
    """Phase 6's peer-review verdict template is inlined (not via
    render_build_b_prompt). It must still carry the same citation rule
    via the shared `render_citation_contract_line()` helper — no per-phase
    divergence."""

    @pytest.fixture
    def rendered(self) -> str:
        from scripts.workflowgen.spec_phase6 import _render_phase6_peer_review
        return _render_phase6_peer_review()

    def test_phase6_inline_template_has_positive_example(self, rendered: str):
        assert "SRS.md:42" in rendered, (
            "Phase 6 inline verdicts template must include the positive "
            "citation example (SRS.md:42) via the shared helper"
        )

    def test_phase6_inline_template_forbids_prose(self, rendered: str):
        assert "taskq_api.app:app" in rendered, (
            "Phase 6 inline verdicts template must include the negative "
            "example (taskq_api.app:app aligns with §X.Y) via the shared "
            "helper"
        )

    def test_phase6_inline_template_mentions_digits_rule(self, rendered: str):
        assert "DIGITS" in rendered, (
            "Phase 6 inline verdicts template must state the part after `:` "
            "must be DIGITS (shared helper)"
        )