# tests/test_reviewer_router.py
# Phase B deliverable B14 — M4: framework self-tests for ReviewerRouter.
import pytest
from unittest.mock import patch


# ── ReviewerRouter unit tests ────────────────────────────────────────────────

class TestReviewerRouter:
    def _make_router(self, target="telegram:12345"):
        with patch.dict("os.environ", {"HERMES_REVIEWER_TARGET": target}):
            with patch("harness.reviewer_router._HERMES_AVAILABLE", True):
                from harness.reviewer_router import ReviewerRouter
                return ReviewerRouter(target=target)

    def test_raises_when_no_target(self):
        from harness.reviewer_router import ReviewerRouter
        with pytest.raises(ValueError, match="HERMES_REVIEWER_TARGET"):
            ReviewerRouter(target="")

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
    def test_p3_uses_hermes(self):
        from harness.reviewer_router import get_reviewer_model
        assert get_reviewer_model(phase=3) == "hermes"

    def test_p4_uses_hermes(self):
        from harness.reviewer_router import get_reviewer_model
        assert get_reviewer_model(phase=4) == "hermes"

    def test_p6_uses_hermes(self):
        from harness.reviewer_router import get_reviewer_model
        assert get_reviewer_model(phase=6) == "hermes"

    def test_p7_uses_claude(self):
        from harness.reviewer_router import get_reviewer_model
        assert get_reviewer_model(phase=7) == "claude"

    def test_p8_uses_claude(self):
        from harness.reviewer_router import get_reviewer_model
        assert get_reviewer_model(phase=8) == "claude"
