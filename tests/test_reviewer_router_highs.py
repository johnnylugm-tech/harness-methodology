"""
Regression tests for 3 HIGH bugs in reviewer_router:

  1. _try_chain (line 136) — cancel_event is checked only at entry.
     If a sibling REJECT fires during this chain's sub-agent spawn,
     the in-flight Claude subprocess keeps running to completion
     (Python threads cannot be interrupted, and ThreadPoolExecutor's
     cancel_futures only drops queued-not-started futures). The
     minimal local fix: _try_subagent must observe cancel_event
     after spawn() returns and surface a CANCELLED status so the
     caller knows the result is stale.

  2. _try_subagent (line 165) — _build_prompt (line 570) is defined
     but has zero production callers; the LLM is invoked without
     the JSON schema footer, so _parse_response routinely REJECTs
     well-formed responses. Fix: _try_subagent must call
     _build_prompt before dispatch.

  3. _parse_response (line 589) — uses greedy `\{.*\}` regex with
     DOTALL, capturing from first `{` to last `}`. When the
     response contains extra braces (code fences, examples,
     nested arrays), the captured span is invalid JSON, and
     _parse_response silently REJECTs. Fix: walk the response
     with json.JSONDecoder().raw_decode to find the first
     valid JSON object.
"""
from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

from harness.reviewer_router import ReviewerRouter


def _make_router():
    return ReviewerRouter()


# ── Bug 1: cancel_event observed post-spawn ──────────────────────────────────

class TestCancelEventObserved:
    def test_cancel_set_during_spawn_surfaces_as_cancelled(self):
        """If cancel_event is set DURING a sub-agent spawn (e.g. a
        sibling REJECT fired in the same wave), the result of this
        chain must surface a CANCELLED status so the merge layer
        knows the response is stale — not silently APPROVE the
        sibling's stale work."""
        router = _make_router()
        cancel_event = threading.Event()
        cancel_event.set()  # sibling already REJECTed

        # Mock the spawn to take some time, so we can simulate a
        # race where the cancel happens "during" — but since spawn
        # is synchronous in this test, the cancel is set BEFORE the
        # call. The fix must check cancel_event AFTER spawn returns
        # and override the result.
        mock_spawner = MagicMock()
        mock_spawner.spawn.return_value = {
            "review_status": "APPROVE", "confidence": 0.9,
            "violations": [], "summary": "stale result",
        }
        with patch("core.agent_spawner.AgentSpawner", return_value=mock_spawner):
            result = router._try_subagent(
                role="reviewer", prompt="x", phase=3, fr_id="FR-001",
                cancel_event=cancel_event,
            )
        # The post-spawn cancel check must override the stale APPROVE.
        assert result.get("review_status") == "CANCELLED", (
            f"cancel_event set before spawn must be observed post-spawn; "
            f"got {result.get('review_status')!r}"
        )


# ── Bug 2: _build_prompt must be used in production ──────────────────────────

class TestBuildPromptUsedInProduction:
    def test_try_subagent_passes_prompt_with_json_footer(
        self,
    ):
        """_try_subagent must invoke _build_prompt (or otherwise
        include the JSON schema footer) so the LLM knows what
        format to return. Without the footer, _parse_response
        routinely REJECTs well-formed responses."""
        router = _make_router()
        mock_spawner = MagicMock()
        mock_spawner.spawn.return_value = {
            "review_status": "APPROVE", "confidence": 0.9,
            "violations": [], "summary": "ok",
        }
        with patch("core.agent_spawner.AgentSpawner", return_value=mock_spawner):
            router._try_subagent(
                role="reviewer", prompt="check this",
                phase=3, fr_id="FR-001", task_timeout_s=60,
            )
        # The prompt passed to spawn must include the JSON schema
        # footer so the LLM knows the expected response shape.
        call_kwargs = mock_spawner.spawn.call_args
        sent_prompt = call_kwargs.kwargs.get("prompt", "")
        assert "review_status" in sent_prompt, (
            f"prompt sent to sub-agent must include the JSON schema "
            f"footer with 'review_status' key; got: {sent_prompt!r}"
        )
        assert "APPROVE" in sent_prompt and "REJECT" in sent_prompt


# ── Bug 3: _parse_response handles extra braces ─────────────────────────────

class TestParseResponseGreedyRegex:
    def test_extra_braces_in_response_does_not_reject(self):
        """A response with extra braces (code fences, examples,
        arrays) must NOT cause _parse_response to silently REJECT
        due to the greedy regex capturing the wrong span."""
        router = _make_router()
        # The malicious-shaped response: a code-fence example with
        # nested braces, followed by the real review.
        raw = (
            '```json\n'
            '{"example": "this is just an example", "nested": {"a": 1}}\n'
            '```\n'
            'My review: {"review_status": "APPROVE", "confidence": 0.85, '
            '"violations": [], "summary": "ok"}'
        )
        result = router._parse_response(raw)
        assert result["review_status"] == "APPROVE", (
            f"must extract the real review object, not the example; "
            f"got {result!r}"
        )
        assert result["confidence"] == 0.85

    def test_response_with_array_before_object(self):
        """Edge case: an array appears before the actual review
        object. The greedy regex previously captured the array
        as part of the JSON span and failed to parse it."""
        router = _make_router()
        raw = (
            'Here are some hints: [1, 2, 3]\n'
            'Result: {"review_status": "REJECT", "confidence": 0.4, '
            '"violations": ["x"], "summary": "bad"}'
        )
        result = router._parse_response(raw)
        assert result["review_status"] == "REJECT"
        assert result["violations"] == ["x"]
