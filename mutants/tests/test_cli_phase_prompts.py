"""Tests for core/cli_phase_prompts.py."""

from core.cli_phase_prompts import get_phase_prompts, get_phase_role, PHASE_PROMPTS


class TestGetPhasePrompts:
    def test_phase_1_returns_requirements_spec(self):
        prompts = get_phase_prompts(1)
        assert prompts["name"] == "Requirements Specification"
        assert "developer" in prompts
        assert "reviewer" in prompts

    def test_phase_8_returns_config_management(self):
        prompts = get_phase_prompts(8)
        assert prompts["name"] == "Configuration Management"

    def test_unknown_phase_defaults_to_3(self):
        prompts = get_phase_prompts(99)
        assert prompts == PHASE_PROMPTS[3]


class TestGetPhaseRole:
    def test_agent_a_returns_correct_role(self):
        assert get_phase_role(1, is_agent_a=True) == "requirements_engineer"
        assert get_phase_role(3, is_agent_a=True) == "developer"

    def test_agent_b_returns_correct_role(self):
        assert get_phase_role(1, is_agent_a=False) == "business_analyst"
        assert get_phase_role(3, is_agent_a=False) == "reviewer"

    def test_unknown_phase_defaults_to_3(self):
        assert get_phase_role(99, is_agent_a=True) == PHASE_PROMPTS[3]["agent_a"]
