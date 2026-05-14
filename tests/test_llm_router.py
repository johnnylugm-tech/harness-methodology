"""Tests for harness/ssi/scripts/llm_router.py — LLM tier routing logic."""

from pathlib import Path

# Insert harness/ssi/scripts into path for import
import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "harness" / "ssi" / "scripts"))

from llm_router import route, build_gemini_prompt, TIER_MAP, TIER_CONFIG, IMPROVE_CONFIG  # pyright: ignore[reportMissingImports]


class TestRoute:
    def test_tier1_dimension(self):
        result = route("linting")
        assert result["tier"] == 1
        assert result["provider"] == "gemini"
        assert result["use_gemini"] is True

    def test_tier2_dimension(self):
        result = route("security")
        assert result["tier"] == 2
        assert result["provider"] == "gemini"

    def test_tier3_dimension(self):
        result = route("architecture")
        assert result["tier"] == 3
        assert result["provider"] == "claude_native"
        assert result["use_gemini"] is False

    def test_unknown_dimension_defaults_to_tier3(self):
        result = route("nonexistent_dim")
        assert result["tier"] == 3
        assert result["provider"] == "claude_native"

    def test_all_12_core_dimensions_routable(self):
        core = ["linting", "type_safety", "test_coverage", "security",
                "secrets_scanning", "license_compliance", "mutation_testing",
                "architecture", "readability", "error_handling",
                "documentation", "performance"]
        for dim in core:
            result = route(dim)
            assert result["tier"] in (1, 2, 3), f"{dim} returned tier {result['tier']}"
            assert "provider" in result

    def test_tier3_no_gemini_prompt_template(self):
        result = route("architecture")
        assert result["gemini_prompt_template"] is None

    def test_tier1_has_gemini_prompt_template(self):
        result = route("linting")
        assert result["gemini_prompt_template"] is not None

    def test_hermes_disabled_by_default(self):
        result = route("linting")
        assert "hermes_notification" not in result

    def test_tier1_provider_chain_hermes_first(self):
        result = route("linting")
        assert "provider_chain" in result
        assert result["provider_chain"][0] == "hermes"
        assert result["provider_chain"][1] == "gemini"
        assert result["provider_chain"][2] == "claude_native"

    def test_tier2_provider_chain_hermes_first(self):
        result = route("security")
        assert result["provider_chain"][0] == "hermes"

    def test_tier3_provider_chain_claude_only(self):
        result = route("architecture")
        assert result["provider_chain"] == ["claude_native"]

    def test_unknown_dim_provider_chain_claude_only(self):
        result = route("nonexistent_dim")
        assert result["provider_chain"] == ["claude_native"]


class TestBuildGeminiPrompt:
    def test_basic_prompt(self):
        prompt = build_gemini_prompt("linting", "tool output here")
        assert "linting" in prompt
        assert "tool output here" in prompt
        assert "JSON" in prompt

    def test_with_code_sample(self):
        prompt = build_gemini_prompt("type_safety", "tool out", code_sample="def foo(): pass")
        assert "def foo(): pass" in prompt

    def test_truncates_long_output(self):
        long_output = "x" * 10000
        prompt = build_gemini_prompt("linting", long_output)
        # Truncation must have occurred: original 10000-char string must not appear intact
        assert "x" * 10000 not in prompt
        # Output portion is capped at ~6000 chars, so prompt must be substantially shorter
        assert len(prompt) < 8000


class TestTierMap:
    def test_tier1_dims_use_gemini(self):
        tier1 = [d for d, t in TIER_MAP.items() if t == 1]
        assert "linting" in tier1
        assert "mutation_testing" in tier1

    def test_tier3_dims_use_claude(self):
        tier3 = [d for d, t in TIER_MAP.items() if t == 3]
        assert "architecture" in tier3
        assert "readability" in tier3
        assert "error_handling" in tier3


class TestImproveConfig:
    def test_improve_is_claude(self):
        assert IMPROVE_CONFIG["provider"] == "claude_native"


class TestTierConfig:
    def test_tier1_token_budget(self):
        assert TIER_CONFIG[1]["token_budget"]["input"] == 8000
        assert TIER_CONFIG[1]["token_budget"]["output"] == 800

    def test_tier3_token_budget(self):
        assert TIER_CONFIG[3]["token_budget"]["input"] == 20000
        assert TIER_CONFIG[3]["token_budget"]["output"] == 3000
