"""Tests for harness/ssi/scripts/llm_router.py — LLM tier routing (Claude-only)."""

from pathlib import Path

# Insert harness/ssi/scripts into path for import
import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "harness" / "ssi" / "scripts"))

from llm_router import route, TIER_MAP, TIER_CONFIG, IMPROVE_CONFIG  # pyright: ignore[reportMissingImports]


class TestRoute:
    def test_tier1_dimension(self):
        result = route("linting")
        assert result["tier"] == 1
        assert result["provider"] == "claude_native"

    def test_tier2_dimension(self):
        result = route("security")
        assert result["tier"] == 2
        assert result["provider"] == "claude_native"

    def test_tier3_dimension(self):
        result = route("architecture")
        assert result["tier"] == 3
        assert result["provider"] == "claude_native"

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

    def test_no_gemini_key_in_output(self):
        """Gemini-specific keys must not appear in routing output."""
        result = route("linting")
        assert "use_gemini" not in result
        assert "gemini_prompt_template" not in result

    def test_no_hermes_notification(self):
        result = route("linting")
        assert "hermes_notification" not in result

    def test_all_tiers_provider_chain_claude_only(self):
        for dim in ("linting", "security", "architecture", "nonexistent_dim"):
            result = route(dim)
            assert result["provider_chain"] == ["claude_native"], dim


class TestTierMap:
    def test_tier1_dims(self):
        tier1 = [d for d, t in TIER_MAP.items() if t == 1]
        assert "linting" in tier1
        assert "mutation_testing" in tier1

    def test_tier3_dims(self):
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

    def test_all_tiers_claude_native(self):
        for tier in (1, 2, 3):
            assert TIER_CONFIG[tier]["provider"] == "claude_native"
