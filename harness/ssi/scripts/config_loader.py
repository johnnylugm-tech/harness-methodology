#!/usr/bin/env python3
"""
Config Loader: YAML → JSON resolver with defaults merging and weight normalization

Merges user config over defaults, normalizes weights across enabled dimensions,
validates ranges, and outputs resolved config as JSON.
"""

import os
import sys
import json
import yaml
from pathlib import Path

# ---------------------------------------------------------------------------
# Supported model aliases
# ---------------------------------------------------------------------------
CLAUDE_MODELS = {
    "claude-sonnet-4-5": "claude-sonnet-4-5",  # standard (default)
    "claude-sonnet-4-6": "claude-sonnet-4-6",  # latest sonnet
    "claude-opus-4": "claude-opus-4",  # highest capability
    "claude": "claude-sonnet-4-5",  # generic alias → sonnet
}

GEMINI_MODELS = {
    "gemini-2.5-flash": "gemini-2.5-flash",  # fast + cheap (default)
    "gemini-2.5-pro": "gemini-2.5-pro",  # higher accuracy
    "gemini": "gemini-2.5-flash",  # generic alias → flash
}

DEFAULT_CONFIG = {
    "version": "1.0",
    "quality": {
        "score_gate": 85,  # Minimum overall score — not a completion goal
        "max_rounds": 3,
        "early_stop": True,  # Issue-driven: score_gate AND zero open critical/high
        "saturation_rounds": 3,  # Plateau detection: N rounds with no new issues
        "commit_per_fix": True,
    },
    "workspace": {
        "work_dir": ".sessi-work",
        "preserve_git": True,
    },
    "dimensions": {
        "linting": {"enabled": True, "weight": 0.06, "target": 95, "tools": []},
        "type_safety": {"enabled": True, "weight": 0.10, "target": 95, "tools": []},
        "test_coverage": {"enabled": True, "weight": 0.13, "target": 80, "tools": []},
        "security": {"enabled": True, "weight": 0.10, "target": 90, "tools": []},
        "performance": {"enabled": True, "weight": 0.07, "target": 80, "tools": []},
        "architecture": {"enabled": True, "weight": 0.07, "target": 80, "tools": []},
        "readability": {"enabled": True, "weight": 0.06, "target": 85, "tools": []},
        "error_handling": {"enabled": True, "weight": 0.09, "target": 85, "tools": []},
        "documentation": {"enabled": True, "weight": 0.10, "target": 85, "tools": []},
        "secrets_scanning": {
            "enabled": True,
            "weight": 0.08,
            "target": 100,
            "tools": [],
        },
        "mutation_testing": {
            "enabled": True,
            "weight": 0.08,
            "target": 70,
            "tools": [],
            "time_budget_seconds": 300,
        },
        "license_compliance": {
            "enabled": True,
            "weight": 0.06,
            "target": 95,
            "tools": [],
        },
        "property_testing": {
            "enabled": False,
            "weight": 0.07,
            "target": 75,
            "tools": [],
        },
        "fuzzing": {"enabled": False, "weight": 0.08, "target": 70, "tools": []},
        "accessibility": {"enabled": False, "weight": 0.06, "target": 85, "tools": []},
        "observability": {"enabled": False, "weight": 0.05, "target": 80, "tools": []},
        "supply_chain_security": {
            "enabled": False,
            "weight": 0.06,
            "target": 80,
            "tools": [],
        },
    },
    "scoring": {
        "reconcile_method": "min",
        "evidence_threshold": 10,
        "tool_diff_min_lines": 3,
        "cap_unsupported_delta": 3,
        "regression_detection": True,
        "revert_on_regression": True,
    },
    "evaluation": {
        "tool_first": True,
        "evidence_required": True,
        "per_dimension_depth": "thorough",
        "explain_gaps": True,
    },
    "llm_routing": {
        "enabled": True,
        "tier1": {
            "provider": "gemini",
            "model": "gemini-2.5-flash",
            "dimensions": [
                "linting",
                "type_safety",
                "test_coverage",
                "secrets_scanning",
                "license_compliance",
                "mutation_testing",
            ],
        },
        "tier2": {
            "provider": "gemini",
            "model": "gemini-2.5-flash",
            "dimensions": ["security"],
        },
        "tier3": {
            "provider": "claude_native",
            "model": "claude",
            "dimensions": [
                "architecture",
                "readability",
                "error_handling",
                "documentation",
                "performance",
            ],
        },
        "improve": {"provider": "claude_native"},
        "token_budget": {
            "tier1_input_max": 8000,
            "tier2_input_max": 10000,
            "tier3_input_max": 20000,
        },
    },
}


def deep_merge(base, override):
    """Deep merge override dict into base dict"""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def normalize_weights(config):
    """Normalize dimension weights to sum to 1.0 across enabled dimensions"""
    dimensions = config["dimensions"]
    enabled_dims = {k: v for k, v in dimensions.items() if v.get("enabled", False)}

    if not enabled_dims:
        raise ValueError("No dimensions enabled in config")

    # Sum of enabled dimension weights
    total_weight = sum(d["weight"] for d in enabled_dims.values())

    if total_weight == 0:
        raise ValueError("Total weight of enabled dimensions is 0")

    # Normalize
    for dim_name in enabled_dims:
        dimensions[dim_name]["weight"] = dimensions[dim_name]["weight"] / total_weight

    return config


def validate_config(config):
    """Validate config values are in valid ranges"""
    quality = config["quality"]

    # Accept either score_gate (new) or target (legacy). Mirror to both.
    if "score_gate" not in quality and "target" in quality:
        quality["score_gate"] = quality["target"]
    if "target" not in quality and "score_gate" in quality:
        quality["target"] = quality["score_gate"]  # backward compat alias

    score_gate = quality["score_gate"]
    if not (0 <= score_gate <= 100):
        raise ValueError(f"quality.score_gate must be 0-100, got {score_gate}")

    # Validate max_rounds
    max_rounds = quality["max_rounds"]
    if max_rounds < 1:
        raise ValueError(f"quality.max_rounds must be >= 1, got {max_rounds}")

    # Validate saturation_rounds
    saturation_rounds = quality.get("saturation_rounds", 3)
    if saturation_rounds < 1:
        raise ValueError(
            f"quality.saturation_rounds must be >= 1, got {saturation_rounds}"
        )
    quality["saturation_rounds"] = saturation_rounds

    # Validate dimension targets
    for dim_name, dim_config in config["dimensions"].items():
        if dim_config.get("enabled", False):
            dim_target = dim_config.get("target", 100)
            if not (0 <= dim_target <= 100):
                raise ValueError(
                    f"dimensions.{dim_name}.target must be 0-100, got {dim_target}"
                )

    return config


def load_config(config_path):
    """Load YAML config file"""
    if not Path(config_path).exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with open(config_path, "r") as f:
        user_config = yaml.safe_load(f) or {}

    return user_config


def apply_env_overrides(config):
    """
    Apply environment variable overrides to llm_routing model selection.

    Supported env vars:
      HARNESS_GEMINI_MODEL   — override Tier 1/2 model  (e.g. gemini-2.5-pro)
      HARNESS_CLAUDE_MODEL   — override Tier 3 model    (e.g. claude-opus-4)
      HARNESS_IMPROVE_MODEL  — override improve model   (defaults to HARNESS_CLAUDE_MODEL)

    These override config.yaml values, which override built-in defaults.
    Order of precedence: env var > config.yaml > default
    """
    routing = config.setdefault("llm_routing", {})

    gemini_env = os.environ.get("HARNESS_GEMINI_MODEL")
    claude_env = os.environ.get("HARNESS_CLAUDE_MODEL")
    improve_env = os.environ.get("HARNESS_IMPROVE_MODEL") or claude_env

    if gemini_env:
        resolved = GEMINI_MODELS.get(gemini_env, gemini_env)
        routing.setdefault("tier1", {})["model"] = resolved
        routing.setdefault("tier2", {})["model"] = resolved
        config["_env_overrides"] = config.get("_env_overrides", [])
        config["_env_overrides"].append(
            f"HARNESS_GEMINI_MODEL={gemini_env} → {resolved}"
        )

    if claude_env:
        resolved = CLAUDE_MODELS.get(claude_env, claude_env)
        routing.setdefault("tier3", {})["model"] = resolved
        config["_env_overrides"] = config.get("_env_overrides", [])
        config["_env_overrides"].append(
            f"HARNESS_CLAUDE_MODEL={claude_env} → {resolved}"
        )

    if improve_env:
        resolved = CLAUDE_MODELS.get(improve_env, improve_env)
        routing.setdefault("improve", {})["model"] = resolved
        if improve_env != claude_env:
            config["_env_overrides"] = config.get("_env_overrides", [])
            config["_env_overrides"].append(
                f"HARNESS_IMPROVE_MODEL={improve_env} → {resolved}"
            )

    return config


def resolve(config_path):
    """
    Resolve config: load user config, merge with defaults, normalize weights, validate.
    Then apply env var overrides (highest precedence).

    Args:
        config_path: path to config.yaml

    Returns:
        Resolved config dict
    """
    user_config = load_config(config_path)
    resolved = deep_merge(DEFAULT_CONFIG, user_config)
    resolved = normalize_weights(resolved)
    resolved = validate_config(resolved)
    resolved = apply_env_overrides(resolved)  # env vars win
    return resolved


def main():
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <config.yaml>", file=sys.stderr)
        print("\nEnv var model overrides (highest precedence):", file=sys.stderr)
        print(
            "  HARNESS_GEMINI_MODEL   gemini-2.5-flash | gemini-2.5-pro",
            file=sys.stderr,
        )
        print(
            "  HARNESS_CLAUDE_MODEL   claude-sonnet-4-5 | claude-sonnet-4-6 | claude-opus-4",
            file=sys.stderr,
        )
        print(
            "  HARNESS_IMPROVE_MODEL  (defaults to HARNESS_CLAUDE_MODEL if unset)",
            file=sys.stderr,
        )
        sys.exit(1)

    config_path = sys.argv[1]

    try:
        config = resolve(config_path)
        if config.get("_env_overrides"):
            for override in config["_env_overrides"]:
                print(f"[env override] {override}", file=sys.stderr)
        print(json.dumps(config, indent=2))
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
