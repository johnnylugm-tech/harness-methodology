"""
Regression tests for 3 MEDIUM bugs in reviewer_router:

  1. _try_chain (line 147) — `int(timeout_ms / 1000)` uses true
     division; a sub-second timeout (e.g. 500 ms) yields 0, which
     `subprocess.run(timeout=0)` immediately trips, caught by the
     broad except and auto-approved. Plus: no validation that
     timeout_ms is positive or numeric.

  2. _try_chain (line 147) — non-numeric timeout_ms raises TypeError
     on division (not caught), negative passes through to
     AgentSpawner unvalidated.

  3. _execute_parallel_waves (line 277) — context injection
     `self._enrich_with_context(s, approved_context)` is eagerly
     evaluated inside the dict comprehension when `executor.submit`
     is called. All wave siblings read the identical snapshot of
     `approved_context` at submit-time; the lock only guards
     post-completion append. Sibling results in the same wave
     never reach each other as context.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from harness.reviewer_router import ReviewerRouter, SubTask


def _make_router():
    return ReviewerRouter()


# ── Bug 1+2: timeout validation ─────────────────────────────────────────────

class TestTimeoutValidation:
    def test_sub_second_timeout_does_not_yield_zero(
        self,
    ):
        """A 500 ms timeout must be coerced to a positive int
        (or rejected with ValueError), not silently truncated
        to 0 by `int(timeout_ms / 1000)` — which would trip
        subprocess.run(timeout=0) immediately and auto-APPROVE
        via the broad-except fallback."""
        router = _make_router()
        with pytest.raises((ValueError, TypeError)):
            router._try_chain(
                role="reviewer", prompt="x", phase=3, fr_id="FR-001",
                timeout_ms=500,  # sub-second
            )

    def test_negative_timeout_rejected(self):
        router = _make_router()
        with pytest.raises(ValueError):
            router._try_chain(
                role="reviewer", prompt="x", phase=3, fr_id="FR-001",
                timeout_ms=-1000,
            )

    def test_non_numeric_timeout_rejected(self):
        router = _make_router()
        with pytest.raises((ValueError, TypeError)):
            router._try_chain(
                role="reviewer", prompt="x", phase=3, fr_id="FR-001",
                timeout_ms="not-a-number",  # type: ignore[arg-type]
            )

    def test_valid_timeout_unchanged(self):
        """Sanity guard: a normal 30s timeout must not raise."""
        router = _make_router()
        mock_spawner = MagicMock()
        mock_spawner.spawn.return_value = {
            "review_status": "APPROVE", "confidence": 0.9,
            "violations": [], "summary": "ok",
        }
        with patch("core.agent_spawner.AgentSpawner", return_value=mock_spawner):
            result = router._try_chain(
                role="reviewer", prompt="x", phase=3, fr_id="FR-001",
                timeout_ms=30_000,
            )
        assert result["review_status"] == "APPROVE"


# ── Bug 3: context staleness across wave siblings ───────────────────────────

class TestWaveContextFreshness:
    def test_wave_siblings_observe_each_others_summaries(self):
        """In a parallel wave, sibling A's APPROVE summary should
        be visible to sibling B's context (B depends on A).
        Currently, `_enrich_with_context` is evaluated eagerly
        at submit-time, so all siblings see the same pre-wave
        snapshot — sibling B's prompt lacks A's contribution."""
        router = _make_router()

        # Two FRs, A has no deps, B depends on A.
        subtasks = [
            SubTask(content="A: standalone review", label="A",
                    dependencies=[], index=1, total=2),
            SubTask(content="B: depends on A", label="B",
                    dependencies=["A"], index=2, total=2),
        ]

        captured_prompts: dict[str, str] = {}
        # NOTE: must match the labels the dict-comprehension produces
        # via _enrich_with_context — i.e., the actual prompt sent.

        def fake_try_chain(role, prompt, phase, fr_id,
                           timeout_ms, task_idx=1, task_total=1,
                           cancel_event=None, **_):
            # Strip header to find which label this prompt is for.
            label = "A" if "A: standalone" in prompt else "B"
            captured_prompts[label] = prompt
            return {
                "review_status": "APPROVE", "confidence": 0.9,
                "violations": [], "summary": f"approved-{label}",
            }

        with patch.object(router, "_try_chain", side_effect=fake_try_chain):
            router._execute_parallel_waves(
                role="reviewer", subtasks=subtasks,
                phase=3, fr_id="FR-001", timeout_ms=10_000,
            )

        # B's prompt must include A's APPROVE summary (from the
        # earlier wave). Currently fails because B's context was
        # captured at submit-time, before A completed.
        assert "approved-A" in captured_prompts.get("B", ""), (
            f"B's prompt must include A's summary; got: "
            f"{captured_prompts.get('B', '<missing>')!r}"
        )
