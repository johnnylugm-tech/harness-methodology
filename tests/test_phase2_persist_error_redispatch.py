"""tests/test_phase2_persist_error_redispatch.py — Regression pins for
Phase 2 abLoop's `persist_error` re-dispatch (v33b, added 2026-08-14).

Bug context: taskq-super run-all.js halted at P2 Sub-Task 2/3 (ADR.md).
Agent B wrote a prose-shaped citation `taskq_api.app:app aligns with
SAD §1.2`; the validator correctly rejected it; the 3-attempt
`render_persist_approval` retry burned all 3 attempts on the same bad
payload; `await persistApproval(cfg.deliverable, b2)` was a bare call
in `render_generic_ab_loop` (no try/catch), so the throw bubbled up
and halted the workflow.

Phase 1 already has this defense at `spec_phase1.py:244-298` (peer-review
level): it captures the persistApproval throw into `b2.persist_error`
and prepends a `=== PREVIOUS ROUND CITE REJECT ===` block to the next
round's Agent B prompt. Phase 2's abLoop had neither.

These tests pin that the generator (`render_generic_ab_loop` in
`harness/scripts/workflowgen/js_blocks.py`) emits the same shape:
  (a) `await persistApproval(cfg.deliverable, b2)` is wrapped in try/catch.
  (b) The thrown error is captured into `b2.persist_error`.
  (c) After capture, the round loop `continue`s (does NOT return/throw).
  (d) The next-round `buildBPrompt(...)` call prepends a
      `=== PREVIOUS ROUND CITE REJECT ===` block when `b2.persist_error`
      is set.
  (e) The block reminds Agent B about `wc -l` so it can verify line numbers.
  (f) The block explicitly forbids prose-after-colon.
  (g) After `MAX_B_ROUNDS` of persist failures, the workflow halts cleanly
      instead of infinite-looping.

Together with `test_peer_review_prompt_citation_rule.py::TestBuildBPromptPositiveAndNegativeExamples`
(Layer 1 — prompt clarity), these tests pin the full v33b fix.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

_HARNESS_ROOT = Path(__file__).resolve().parents[1]
if str(_HARNESS_ROOT) not in sys.path:
    sys.path.insert(0, str(_HARNESS_ROOT))

from scripts.workflowgen import js_blocks as B  # noqa: E402


def _render_ab_loop() -> str:
    """Render render_generic_ab_loop with the canonical Phase 2 args."""
    return B.render_generic_ab_loop(b_role="TECH_LEAD", phase_num=2)


class TestAbLoopPersistErrorRedispatch:
    """Layer 2 (persist_error): pin that the generator emits a try/catch
    wrap around `await persistApproval(cfg.deliverable, b2)` plus a
    `=== PREVIOUS ROUND CITE REJECT ===` prepend into the next-round B
    prompt — mirroring spec_phase1.py:244-298."""

    @pytest.fixture
    def rendered(self) -> str:
        return _render_ab_loop()

    def test_ab_loop_wraps_persist_approval_in_try_catch(self, rendered: str):
        # The structural pattern: `try { ... await persistApproval ... } catch (e) { ... }`
        m = re.search(
            r"try\s*\{[^}]*await\s+persistApproval[^}]*\}\s*catch\s*\(\s*e\s*\)\s*\{",
            rendered,
            re.DOTALL,
        )
        assert m is not None, (
            "abLoop must wrap `await persistApproval(cfg.deliverable, b2)` "
            "in try/catch — mirror spec_phase1.py:285-298"
        )

    def test_ab_loop_captures_persist_error_into_b2(self, rendered: str):
        # When persistApproval throws, the error must be attached to
        # `b2.persist_error` so the next round's prompt can carry it.
        assert "b2.persist_error" in rendered, (
            "abLoop must assign `b2.persist_error = String(...).slice(0, 400)` "
            "after a persistApproval throw"
        )

    def test_ab_loop_continues_loop_on_persist_error(self, rendered: str):
        # After capturing persist_error, abLoop must `continue` (not
        # return/throw) so the next round's B prompt can see it. Mirror
        # spec_phase1.py:297.
        #
        # We locate the persist-error block by anchoring on the unique
        # `if (persistErr) {` guard, then check that the block contains
        # `b2.persist_error = `, a `log(` call carrying `persist_error`,
        # and a `continue` (the MAX_B_ROUNDS halt may sit between log and
        # continue — that's fine, the structural contract is just that
        # the block reaches `continue`).
        m = re.search(
            r"if\s*\(\s*persistErr\s*\)\s*\{(.*?)continue\b",
            rendered,
            re.DOTALL,
        )
        assert m is not None, (
            "abLoop must reach `continue` inside the `if (persistErr)` "
            "block (so the next round's B prompt receives the error)"
        )
        block = m.group(1)
        assert "b2.persist_error =" in block, (
            "the persistErr block must assign b2.persist_error"
        )
        assert "log(" in block, (
            "abLoop must log the persist_error before continuing"
        )

    def test_ab_loop_injects_previous_round_cite_reject_block(self, rendered: str):
        # The next-round B prompt must prepend
        # `=== PREVIOUS ROUND CITE REJECT ===` when b2.persist_error is set.
        # Mirror spec_phase1.py:245.
        assert "PREVIOUS ROUND CITE REJECT" in rendered, (
            "abLoop's next-round B prompt must prepend "
            "`=== PREVIOUS ROUND CITE REJECT ===` when b2.persist_error is set"
        )

    def test_ab_loop_reject_block_includes_wc_l_reminder(self, rendered: str):
        # The reject block must remind Agent B about `wc -l <path>` so it
        # can verify line numbers before re-writing the citation list.
        assert "wc -l" in rendered, (
            "abLoop's PREVIOUS ROUND CITE REJECT block must remind Agent B "
            "to run `wc -l <path>` before writing citations"
        )

    def test_ab_loop_reject_block_forbids_prose_after_colon(self, rendered: str):
        # The reject block must tell Agent B the citation must be exactly
        # `<rel_path>:<digits>` — not prose after `:`.
        assert "DO NOT cite prose" in rendered, (
            "abLoop's PREVIOUS ROUND CITE REJECT block must state that "
            "citations must be exactly `<rel_path>:<digits>`, not prose"
        )


class TestAbLoopEscalatesAfterMaxPersistFailures:
    """If persistApproval keeps failing through MAX_B_ROUNDS rounds, abLoop
    must halt with a clear error (don't infinite-loop). Mirror Phase 1's
    round-MAX_PEER_ROUNDS HR-12 escalation pattern at spec_phase1.py."""

    def test_ab_loop_halts_when_persist_fails_at_max_round(self):
        rendered = _render_ab_loop()
        # Look for the MAX_B_ROUNDS escalation in the persist_error branch.
        m = re.search(
            r"if\s*\(\s*round\s*===\s*MAX_B_ROUNDS\s*\)\s*return\s+halt\(",
            rendered,
        )
        assert m is not None, (
            "abLoop must escalate (halt) when persistApproval keeps failing "
            "at MAX_B_ROUNDS — not silently infinite-loop"
        )

    def test_ab_loop_halt_message_names_persist_rejected(self):
        rendered = _render_ab_loop()
        # The halt's error label should mention 'persist' so the operator
        # can see why the workflow stopped.
        assert "sbr-persist-rejected" in rendered, (
            "abLoop's persist-failure halt must use the "
            "`sbr-persist-rejected` label for clear operator attribution"
        )
