#!/usr/bin/env python3
"""
LLM Router: Routes dimension evaluation to Claude native (all tiers)

All tiers → Claude native (uniform backend, simpler setup)
No Gemini or Hermes MCP dependencies required — only the claude CLI.

Outputs routing decision JSON for evaluate_dimension.md to consume.
"""

import os
import sys
import json

# ---------------------------------------------------------------------------
# Env var model overrides (same vars as config_loader.py)
# ---------------------------------------------------------------------------
_CLAUDE_MODEL = os.environ.get("HARNESS_CLAUDE_MODEL", "claude-sonnet-4-5")
_IMPROVE_MODEL = os.environ.get("HARNESS_IMPROVE_MODEL", _CLAUDE_MODEL)


# Routing table: dimension → tier
TIER_MAP = {
    # Tier 1: Tool output is the full story — LLM only summarizes
    "linting": 1,
    "type_safety": 1,
    "test_coverage": 1,
    "secrets_scanning": 1,
    "license_compliance": 1,
    "mutation_testing": 1,
    # Tier 2: Light judgment needed (borderline, Flash still handles well)
    "security": 2,
    # Tier 3: Deep reasoning, subjective judgment, or code-level analysis
    "architecture": 3,
    "readability": 3,
    "error_handling": 3,
    "documentation": 3,
    "performance": 3,
    # Extended dims — classify conservatively
    "property_testing": 1,
    "fuzzing": 1,
    "accessibility": 1,
    "observability": 1,
    "supply_chain_security": 1,
}

TIER_CONFIG = {
    1: {
        "model": _CLAUDE_MODEL,
        "provider": "claude_native",
        "provider_chain": ["claude_native"],
        "rationale": "Tool output is deterministic; LLM role is summarization only",
        "token_budget": {"input": 8000, "output": 800},
    },
    2: {
        "model": _CLAUDE_MODEL,
        "provider": "claude_native",
        "provider_chain": ["claude_native"],
        "rationale": "Light judgment; Claude sub-agent handles pattern analysis",
        "token_budget": {"input": 10000, "output": 1200},
    },
    3: {
        "model": _CLAUDE_MODEL,
        "provider": "claude_native",
        "provider_chain": ["claude_native"],
        "rationale": "Deep reasoning / subjective judgment / code understanding required",
        "token_budget": {"input": 20000, "output": 3000},
    },
}

# Improve step always Claude — separate override available
IMPROVE_CONFIG = {
    "model": _IMPROVE_MODEL,
    "provider": "claude_native",
}

def route(dimension: str) -> dict:
    """Return routing decision for a dimension."""
    tier = TIER_MAP.get(dimension, 3)  # Default to Claude for unknown dims
    config = TIER_CONFIG[tier]
    result = {
        "dimension": dimension,
        "tier": tier,
        "model": config["model"],
        "provider": config["provider"],
        "provider_chain": config.get("provider_chain", ["claude_native"]),
        "rationale": config["rationale"],
        "token_budget": config["token_budget"],
    }
    # Surface env override for transparency
    if _CLAUDE_MODEL != "claude-sonnet-4-5":
        result["_env_override"] = f"HARNESS_CLAUDE_MODEL={_CLAUDE_MODEL}"
    return result


def main():
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <dimension> [tool_output_file]")
        sys.exit(1)

    dimension = sys.argv[1]
    decision = route(dimension)
    print(json.dumps(decision, indent=2))


if __name__ == "__main__":
    main()
