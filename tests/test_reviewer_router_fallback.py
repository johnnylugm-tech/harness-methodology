"""
Regression tests for CRITICAL bug:
    broad-except in _try_subagent silently auto-APPROVEs the gate on any
    sub-agent failure (timeout, MCP error, ImportError, OOM).

Contract under test:
  - When the sub-agent backend raises any Exception, _try_subagent must
    NOT return review_status=APPROVE. The gate must fail closed.
  - The result must be distinguishable as a fallback (forensics via
    _emergency_fallback), and must propagate through review() as REJECT.
  - The happy path (sub-agent returns a valid APPROVE) is unchanged.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch


def _make_router():
    from harness.reviewer_router import ReviewerRouter
    return ReviewerRouter()


# ── Crash → REJECT (not APPROVE) ─────────────────────────────────────────────

class TestSubagentFallbackClosesGate:
    def test_runtime_error_yields_reject(self):
        """Any sub-agent exception must surface as REJECT, not APPROVE."""
        router = _make_router()
        mock_spawner = MagicMock()
        mock_spawner.spawn.side_effect = RuntimeError("simulated crash")
        with patch("core.agent_spawner.AgentSpawner", return_value=mock_spawner):
            result = router._try_subagent(
                role="reviewer", prompt="x", phase=3, fr_id="FR-001",
            )
        assert result["review_status"] == "REJECT", (
            f"gate must fail-closed on reviewer crash; got "
            f"review_status={result['review_status']!r}"
        )
        assert result["_emergency_fallback"] is True
        assert result["confidence"] == 0.0  # no signal from a crashed reviewer
        assert "subagent_crashed" in result["violations"]

    def test_os_error_yields_reject(self):
        """OSError (timeout, EACCES on the agent_spawner binary) → REJECT."""
        router = _make_router()
        mock_spawner = MagicMock()
        mock_spawner.spawn.side_effect = OSError("simulated timeout")
        with patch("core.agent_spawner.AgentSpawner", return_value=mock_spawner):
            result = router._try_subagent(
                role="reviewer", prompt="x", phase=3, fr_id=None,
            )
        assert result["review_status"] == "REJECT"
        assert result["_emergency_fallback"] is True

    def test_import_error_yields_reject(self):
        """ImportError on AgentSpawner (missing dep) must surface as REJECT,
        not silently auto-approve."""
        router = _make_router()
        with patch.dict("sys.modules", {"core.agent_spawner": None}):
            result = router._try_subagent(
                role="reviewer", prompt="x", phase=3, fr_id=None,
            )
        assert result["review_status"] == "REJECT"
        assert result["_emergency_fallback"] is True

    def test_happy_path_approval_unchanged(self):
        """Sanity guard: the fix must not break the happy path."""
        router = _make_router()
        mock_spawner = MagicMock()
        mock_spawner.spawn.return_value = {
            "review_status": "APPROVE",
            "confidence": 0.9,
            "violations": [],
            "summary": "ok",
        }
        with patch("core.agent_spawner.AgentSpawner", return_value=mock_spawner):
            result = router._try_subagent(
                role="reviewer", prompt="x", phase=3, fr_id="FR-001",
            )
        assert result["review_status"] == "APPROVE"
        assert result["confidence"] == 0.9
        assert result.get("_emergency_fallback") is None


# ── End-to-end: review() surfaces the crash as REJECT ────────────────────────

class TestSubagentFallbackEndToEnd:
    def test_review_returns_reject_on_subagent_crash(self):
        """review() is the actual contract the gate layer depends on.
        A crashing sub-agent must return review_status=REJECT at this
        layer too — a 0.3-confidence APPROVE used to silently pass."""
        router = _make_router()
        mock_spawner = MagicMock()
        mock_spawner.spawn.side_effect = RuntimeError("simulated crash")
        with patch("core.agent_spawner.AgentSpawner", return_value=mock_spawner):
            result = router.review(
                role="reviewer", prompt="check this small prompt",
                phase=3, fr_id="FR-001", timeout_ms=10000,
            )
        assert result["review_status"] == "REJECT", (
            f"review() must fail-closed on sub-agent crash; got "
            f"review_status={result['review_status']!r}"
        )
        assert result.get("_emergency_fallback") is True
