#!/usr/bin/env python3
"""
Severity classifier for detection results.

Maps every problem type to:
- FixStrategy (AUTO_FIX / AUTO_FIX_WITH_VERIFICATION / HUMAN_REQUIRED)
- Confidence estimate (0-100)
- Max auto-fix rounds
- Problem type string (for strategy selection)
"""

from __future__ import annotations

from typing import Any, Dict, Tuple

from core.auto_fix import FixStrategy

# ── Master classification table ──────────────────────────────────────────────

CLASSIFICATION_TABLE: Dict[str, Dict[str, Any]] = {
    "constitution/hardcoded_secrets": {
        "strategy": FixStrategy.HUMAN_REQUIRED,
        "confidence": 0.0,
        "max_rounds": 0,
        "problem_type": "hardcoded_secrets",
    },

    "framework_enforcer/missing_traceability": {
        "strategy": FixStrategy.AUTO_FIX,
        "confidence": 90.0,
        "max_rounds": 2,
        "problem_type": "missing_traceability",
    },

    # ── enforcement/policy_engine.py ──
    "policy_engine/missing_commit_task_id": {
        "strategy": FixStrategy.HUMAN_REQUIRED,
        "confidence": 0.0,
        "max_rounds": 0,
        "problem_type": "hard_rule_violation",
    },
    "policy_engine/no_bypass_commands": {
        "strategy": FixStrategy.HUMAN_REQUIRED,
        "confidence": 0.0,
        "max_rounds": 0,
        "problem_type": "hard_rule_violation",
    },

    # ── enforcement/constitution_as_code.py ──
    "constitution_as_code/hard_rule_violation": {
        "strategy": FixStrategy.HUMAN_REQUIRED,
        "confidence": 0.0,
        "max_rounds": 0,
        "problem_type": "hard_rule_violation",
    },



    # ── kill_switch/ ──
    "kill_switch/circuit_open": {
        "strategy": FixStrategy.HUMAN_REQUIRED,
        "confidence": 0.0,
        "max_rounds": 0,
        "problem_type": "hard_rule_violation",
    },




}

# ── Dimension-specific confidence modifiers ──────────────────────────────────

DIMENSION_CONFIDENCE: Dict[str, float] = {
    "correctness": 85.0,
    "security": 70.0,
    "maintainability": 80.0,
    "coverage": 60.0,
}

# ── Hard rule IDs ────────────────────────────────────────────────────────────

HARD_RULE_IDS = {"R001", "R002", "R003", "R004", "R005", "R006", "R007"}

# ── Actual secret patterns (distinct from missing security keywords) ─────────

ACTUAL_SECRET_PATTERNS = [
    "password = \"", "password = '",
    "secret_key = \"", "secret_key = '",
    "api_key = \"", "api_key = '",
    "token = \"", "token = '",
    "-----BEGIN", "-----END",
    "sha256$", "pbkdf2:", "bcrypt$", "argon2",
]


def classify(
    source: str, details: Dict[str, Any]
) -> Tuple[FixStrategy, float, int, str, Any]:
    """Classify a detection result.

    Args:
        source: Module that detected the problem
        details: Raw detection data for context-specific classification

    Returns:
        (FixStrategy, confidence_pct, max_rounds, problem_type_string, ErrorClass)
    """
    from core.auto_fix.error_class import ErrorClass, get_error_class  # pyright: ignore[reportMissingImports]

    # Check for hard rule violations first
    if is_hard_rule_violation(details):
        return (FixStrategy.HUMAN_REQUIRED, 0.0, 0, "hard_rule_violation", ErrorClass.HARD_VIOLATION)

    # CRG-ONLY dimensions cannot be auto-fixed: architecture/error_handling scores are
    # determined by CRG structural analysis; no automated code change can predictably
    # improve community cohesion or flow coverage without human design decisions.
    _CRG_ONLY_DIMS = {"architecture", "error_handling"}
    _dimension = details.get("dimension", "")
    if _dimension in _CRG_ONLY_DIMS:
        return (
            FixStrategy.HUMAN_REQUIRED, 0.0, 0,
            f"crg_{_dimension}_low",
            ErrorClass.GATE_FAILURE,
        )

    # Check for actual secrets
    content = details.get("content", "")
    if content and is_actual_secret(content):
        return (FixStrategy.HUMAN_REQUIRED, 0.0, 0, "hardcoded_secrets", ErrorClass.HARD_VIOLATION)

    # Look up in classification table
    problem_type = details.get("problem_type", "")

    # Try exact key: source/problem_type
    key = f"{source}/{problem_type}" if problem_type else source
    entry = CLASSIFICATION_TABLE.get(key)

    # Fallback: try source only (for source-level defaults like kill_switch)
    if entry is None:
        key = source
        entry = CLASSIFICATION_TABLE.get(key)

    # There is deliberately no third attempt.
    #
    # A prefix fallback used to live here: it walked the table and took the
    # first entry whose source family matched, in dict order. That is a guess
    # wearing a classification's clothes, and R49-C measured what it costs —
    # after the fabricating strategies were deleted, `constitution/
    # low_keyword_density` resolved to the first remaining `constitution/*`
    # entry and reported problem_type "hardcoded_secrets" for a problem that
    # has nothing to do with secrets.
    #
    # The old default was worse: an unrecognised source became
    # AUTO_FIX_WITH_VERIFICATION at 65% confidence with problem_type
    # "low_constitution_score" — a promise to repair, on a problem type the
    # caller never reported, by a strategy that no longer exists. Unknown is
    # its own answer here for the same reason it is in core/fault_owner.py.
    if entry is None:
        return (FixStrategy.HUMAN_REQUIRED, 0.0, 0, "unknown",
                ErrorClass.QUALITY_DEFICIT)

    strategy = entry["strategy"]
    confidence = entry["confidence"]
    max_rounds = entry["max_rounds"]
    resolved_type = entry["problem_type"]

    # Adjust confidence by dimension if applicable
    dimension = details.get("dimension", "")
    if dimension in DIMENSION_CONFIDENCE:
        confidence = DIMENSION_CONFIDENCE[dimension]

    error_class = get_error_class(source, resolved_type)
    if details.get("gate_num") == 4:
        error_class = ErrorClass.GATE_FAILURE
    return (strategy, confidence, max_rounds, resolved_type, error_class)


def is_hard_rule_violation(details: Dict[str, Any]) -> bool:
    """Check if this is an R001-R007 hard rule violation."""
    rule_id = details.get("rule_id", "")
    if rule_id in HARD_RULE_IDS:
        return True
    if details.get("hard_rule", False):
        return True
    return False


def is_actual_secret(content: str) -> bool:
    """Distinguish actual secrets from missing security keywords."""
    content_lower = content.lower()
    for pattern in ACTUAL_SECRET_PATTERNS:
        if pattern.lower() in content_lower:
            return True
    return False
