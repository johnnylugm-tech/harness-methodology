#!/usr/bin/env python3
"""
LLM Router: Routes dimension evaluation to appropriate LLM tier

Tier 1 → Gemini Flash  (tool-output summarization, cheap)
Tier 2 → Gemini Flash  (light judgment, cheap)
Tier 3 → Claude native (deep reasoning, always)
Improve → Claude native (code generation, always)

Outputs routing decision JSON for evaluate_dimension.md to consume.
"""

import os
import sys
import json

# ---------------------------------------------------------------------------
# Env var model overrides (same vars as config_loader.py)
# ---------------------------------------------------------------------------
_GEMINI_MODEL = os.environ.get("HARNESS_GEMINI_MODEL", "gemini-2.5-flash")
_CLAUDE_MODEL = os.environ.get("HARNESS_CLAUDE_MODEL", "claude-sonnet-4-5")
_IMPROVE_MODEL = os.environ.get("HARNESS_IMPROVE_MODEL", _CLAUDE_MODEL)
_HERMES_TARGET = os.environ.get("HARNESS_HERMES_TARGET", "slack:#quality-audit")  # Default target

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
        "model": _GEMINI_MODEL,
        "provider": "gemini",
        "rationale": "Tool output is deterministic; LLM role is summarization only",
        "token_budget": {"input": 8000, "output": 800},
    },
    2: {
        "model": _GEMINI_MODEL,
        "provider": "gemini",
        "rationale": "Light judgment; Gemini Flash sufficient for pattern analysis",
        "token_budget": {"input": 10000, "output": 1200},
    },
    3: {
        "model": _CLAUDE_MODEL,
        "provider": "claude_native",
        "rationale": "Deep reasoning / subjective judgment / code understanding required",
        "token_budget": {"input": 20000, "output": 3000},
    },
}

HERMES_CONFIG = {
    "enabled": os.environ.get("HARNESS_HERMES_ENABLED", "false").lower() == "true",
    "target": _HERMES_TARGET,
    "provider": "hermes",
    "action": "mcp_hermes_messages_send"
}

# Improve step always Claude — separate override available
IMPROVE_CONFIG = {
    "model": _IMPROVE_MODEL,
    "provider": "claude_native",
}

GEMINI_PROMPT_TEMPLATE = """\
You are a code quality evaluator. Analyze the following tool output for the '{dimension}' dimension and return a JSON evaluation.

## Tool Output
{tool_output}

## Code Sample (if provided)
{code_sample}

## Task
1. Score the dimension 0-100 based ONLY on the tool output evidence
2. List up to 5 concrete findings with line references where available
3. Identify the top gap to fix

Return ONLY valid JSON in this exact format:
{{
  "dimension": "{dimension}",
  "tool_score": <0-100>,
  "llm_score": <0-100>,
  "score": <min of tool_score and llm_score>,
  "findings": [
    {{"line": <int or null>, "severity": "critical|warning|info", "message": "<text>", "evidence": "<tool output excerpt>"}}
  ],
  "gaps": ["<top gap 1>", "<top gap 2>"],
  "tool_outputs": "<raw tool output summary>",
  "reconcile": "tool_first"
}}
"""


def route(dimension: str) -> dict:
    """Return routing decision for a dimension."""
    tier = TIER_MAP.get(dimension, 3)  # Default to Claude for unknown dims
    config = TIER_CONFIG[tier]
    result = {
        "dimension": dimension,
        "tier": tier,
        "model": config["model"],
        "provider": config["provider"],
        "rationale": config["rationale"],
        "token_budget": config["token_budget"],
        "use_gemini": config["provider"] == "gemini",
        "gemini_prompt_template": GEMINI_PROMPT_TEMPLATE
        if config["provider"] == "gemini"
        else None,
    }

    if HERMES_CONFIG["enabled"]:
        result["hermes_notification"] = {
            "target": HERMES_CONFIG["target"],
            "tool": HERMES_CONFIG["action"],
            "message": f"📊 [Harness Audit] Routing dimension '{dimension}' to {config['provider']} ({config['model']})"
        }
    # Surface env overrides for transparency
    if _GEMINI_MODEL != "gemini-2.5-flash" and config["provider"] == "gemini":
        result["_env_override"] = f"HARNESS_GEMINI_MODEL={_GEMINI_MODEL}"
    if _CLAUDE_MODEL != "claude-sonnet-4-5" and config["provider"] == "claude_native":
        result["_env_override"] = f"HARNESS_CLAUDE_MODEL={_CLAUDE_MODEL}"
    return result


def build_gemini_prompt(dimension: str, tool_output: str, code_sample: str = "") -> str:
    """Build Gemini prompt for a tool-first dimension."""
    return GEMINI_PROMPT_TEMPLATE.format(
        dimension=dimension,
        tool_output=tool_output[:6000],  # Hard cap to stay in budget
        code_sample=code_sample[:2000] if code_sample else "(not provided)",
    )


def main():
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <dimension> [tool_output_file]")
        sys.exit(1)

    dimension = sys.argv[1]
    tool_output = ""

    if len(sys.argv) > 2:
        with open(sys.argv[2]) as f:
            tool_output = f.read()

    decision = route(dimension)

    if decision["use_gemini"] and tool_output:
        decision["gemini_prompt"] = build_gemini_prompt(dimension, tool_output)
    else:
        decision.pop("gemini_prompt_template", None)

    print(json.dumps(decision, indent=2))


if __name__ == "__main__":
    main()
