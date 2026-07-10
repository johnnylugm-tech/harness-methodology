# tests/test_reviewer_router.py
# Framework self-tests for ReviewerRouter (Claude sub-agent — no MCP backends).


# ── ReviewerRouter unit tests ────────────────────────────────────────────────

class TestReviewerRouter:
    def _make_router(self):
        from harness.reviewer_router import ReviewerRouter
        return ReviewerRouter()

    def test_no_target_required(self):
        """ReviewerRouter initialises with no env vars or target argument."""
        from harness.reviewer_router import ReviewerRouter
        router = ReviewerRouter()
        assert router is not None

    def test_build_prompt_includes_phase(self):
        router = self._make_router()
        prompt = router._build_prompt("reviewer", "check this", phase=3, fr_id=None)
        assert "Phase 3" in prompt
        assert "Role: reviewer" in prompt

    def test_build_prompt_includes_fr_id(self):
        router = self._make_router()
        prompt = router._build_prompt("reviewer", "check", phase=3, fr_id="FR-001")
        assert "FR FR-001" in prompt

    def test_build_prompt_output_json_footer(self):
        router = self._make_router()
        prompt = router._build_prompt("reviewer", "check", phase=4)
        assert "review_status" in prompt
        assert "APPROVE|REJECT" in prompt

    def test_parse_response_valid_json(self):
        router = self._make_router()
        raw = '{"review_status": "APPROVE", "confidence": 0.9, "violations": [], "summary": "ok"}'
        result = router._parse_response(raw)
        assert result["review_status"] == "APPROVE"
        assert result["confidence"] == 0.9

    def test_parse_response_json_embedded_in_text(self):
        router = self._make_router()
        raw = 'Here is my review: {"review_status": "REJECT", "confidence": 0.3, "violations": ["x"], "summary": "bad"}'
        result = router._parse_response(raw)
        assert result["review_status"] == "REJECT"

    def test_parse_response_invalid_returns_reject(self):
        router = self._make_router()
        result = router._parse_response("not json at all")
        assert result["review_status"] == "REJECT"
        assert result["violations"] == ["parse_error"]

    def test_parse_response_summary_truncated(self):
        router = self._make_router()
        result = router._parse_response("x" * 300)
        assert len(result["summary"]) <= 200


# ── get_reviewer_model ───────────────────────────────────────────────────────

class TestGetReviewerModel:
    def test_all_phases_use_claude(self):
        """Every phase routes to Claude sub-agent (no Hermes/Gemini)."""
        from harness.reviewer_router import get_reviewer_model
        for phase in (1, 2, 3, 4, 5, 6, 7, 8):
            assert get_reviewer_model(phase=phase) == "claude", f"phase {phase}"


# ── Phase-aware decomposition (Layer 1) ─────────────────────────────────────

class TestPhaseAwareDecomposition:
    def _make_router(self):
        from harness.reviewer_router import ReviewerRouter
        return ReviewerRouter()

    def test_p1_always_single_subtask_regardless_of_size(self):
        router = self._make_router()
        large_prompt = "FR-01: spec\n" * 5000  # ~70KB
        subtasks = router._decompose_with_deps(large_prompt, "reviewer", phase=1)
        assert len(subtasks) == 1
        assert subtasks[0].label == "full_deliverable"

    def test_p2_always_single_subtask_regardless_of_size(self):
        router = self._make_router()
        large_prompt = "### FR-01: title\ndetail\n" * 3000  # ~60KB
        subtasks = router._decompose_with_deps(large_prompt, "reviewer", phase=2)
        assert len(subtasks) == 1
        assert subtasks[0].label == "full_deliverable"

    def test_p3_decomposes_large_prompt(self):
        from harness.reviewer_router import TASK_SIZE_THRESHOLD
        router = self._make_router()
        # Build prompt with multiple unique FR sections, total > TASK_SIZE_THRESHOLD
        fr_sections = "\n\n".join(
            f"### FR-{i:02d}: Feature {i}\n" + ("detail " * 40)
            for i in range(1, 20)
        )
        assert len(fr_sections) > TASK_SIZE_THRESHOLD
        subtasks = router._decompose_with_deps(fr_sections, "reviewer", phase=3)
        # P3 should decompose into multiple subtasks
        assert len(subtasks) > 1

    def test_p1_small_prompt_also_single_subtask(self):
        router = self._make_router()
        subtasks = router._decompose_with_deps("short", "reviewer", phase=1)
        assert len(subtasks) == 1

    def test_default_phase_zero_respects_threshold(self):
        from harness.reviewer_router import TASK_SIZE_THRESHOLD
        router = self._make_router()
        small = "x" * (TASK_SIZE_THRESHOLD - 1)
        subtasks = router._decompose_with_deps(small, "reviewer", phase=0)
        assert len(subtasks) == 1


# ── Timeout propagation (Layer 2) ────────────────────────────────────────────

class TestTimeoutPropagation:
    def _make_router(self):
        from harness.reviewer_router import ReviewerRouter
        return ReviewerRouter()

    def test_try_subagent_accepts_task_timeout_s(self):
        from unittest.mock import patch, MagicMock
        router = self._make_router()
        mock_spawner = MagicMock()
        mock_spawner.spawn.return_value = {
            "review_status": "APPROVE", "confidence": 0.9,
            "violations": [], "summary": "ok",
        }
        with patch("core.agent_spawner.AgentSpawner", return_value=mock_spawner):
            router._try_subagent("reviewer", "prompt", 3, "FR-01", task_timeout_s=1200)
        call_kwargs = mock_spawner.spawn.call_args
        assert call_kwargs.kwargs.get("task_timeout") == 1200

    def test_try_subagent_default_timeout_is_300(self):
        from unittest.mock import patch, MagicMock
        router = self._make_router()
        mock_spawner = MagicMock()
        mock_spawner.spawn.return_value = {
            "review_status": "APPROVE", "confidence": 0.9,
            "violations": [], "summary": "ok",
        }
        with patch("core.agent_spawner.AgentSpawner", return_value=mock_spawner):
            router._try_subagent("reviewer", "prompt", 3, None)
        call_kwargs = mock_spawner.spawn.call_args
        assert call_kwargs.kwargs.get("task_timeout") == 300

    def test_try_chain_routes_to_subagent(self):
        """_try_chain dispatches directly to the Claude sub-agent — verified
        by the observable routing outcome (_reviewer_used), not by asserting
        the private _try_subagent call itself (弱點強化 Round 2 Station H)."""
        from unittest.mock import patch
        router = self._make_router()
        approve = {"review_status": "APPROVE", "confidence": 0.9, "violations": [], "summary": "ok"}
        with patch.object(router, "_try_subagent", return_value=dict(approve)):
            result = router._try_chain("reviewer", "prompt", 3, "FR-01", timeout_ms=None)
        assert result["review_status"] == "APPROVE"
        assert result["_reviewer_used"] == "subagent"


# ── Parallel waves (Layer 3) ─────────────────────────────────────────────────

class TestParallelWaves:
    def _make_router(self):
        from harness.reviewer_router import ReviewerRouter
        return ReviewerRouter()

    def test_parallel_waves_all_approve_merges_results(self):
        from unittest.mock import patch
        from harness.reviewer_router import SubTask
        router = self._make_router()
        subtasks = [
            SubTask(content=f"content-{i}", label=f"FR-{i:03d}", index=i + 1, total=5)
            for i in range(5)
        ]
        approve = {"review_status": "APPROVE", "confidence": 0.9, "violations": [], "summary": "ok"}
        with patch.object(router, "_try_chain", return_value=approve):
            result = router._execute_parallel_waves("reviewer", subtasks, phase=3, fr_id=None, timeout_ms=None)
        assert result["review_status"] == "APPROVE"
        assert result.get("_merged") is True
        assert result.get("_subtask_count") == 5

    def test_parallel_waves_reject_short_circuits(self):
        from unittest.mock import patch
        from harness.reviewer_router import SubTask
        router = self._make_router()
        subtasks = [
            SubTask(content=f"content-{i}", label=f"FR-{i:03d}", index=i + 1, total=3)
            for i in range(3)
        ]
        reject = {"review_status": "REJECT", "confidence": 0.1, "violations": ["bad"], "summary": "fail"}
        with patch.object(router, "_try_chain", return_value=reject):
            result = router._execute_parallel_waves("reviewer", subtasks, phase=3, fr_id=None, timeout_ms=None)
        assert result["review_status"] == "REJECT"

    def test_parallel_waves_respects_dependencies(self):
        import threading
        from unittest.mock import patch
        from harness.reviewer_router import SubTask
        router = self._make_router()
        # FR-001 has no deps; FR-002 depends on FR-001
        subtasks = [
            SubTask(content="c1", label="FR-001", dependencies=[], index=1, total=2),
            SubTask(content="c2", label="FR-002", dependencies=["FR-001"], index=2, total=2),
        ]
        call_order: list[str] = []
        lock = threading.Lock()

        def fake_try_chain(role, prompt, phase, fr_id, timeout_ms, task_idx=1, task_total=1, *args, **kwargs):
            # Extract label from prompt (enrich injects label in header)
            label = "FR-001" if "c1" in prompt else "FR-002"
            with lock:
                call_order.append(label)
            return {"review_status": "APPROVE", "confidence": 0.9, "violations": [], "summary": label}

        with patch.object(router, "_try_chain", side_effect=fake_try_chain):
            result = router._execute_parallel_waves("reviewer", subtasks, phase=3, fr_id=None, timeout_ms=None)

        assert result["review_status"] == "APPROVE"
        # FR-001 must appear before FR-002
        assert call_order.index("FR-001") < call_order.index("FR-002")
