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
    # ── constitution/runner.py ──
    "constitution/missing_artifact": {
        "strategy": FixStrategy.AUTO_FIX,
        "confidence": 95.0,
        "max_rounds": 3,
        "problem_type": "missing_artifact",
    },
    "constitution/low_score": {
        "strategy": FixStrategy.AUTO_FIX_WITH_VERIFICATION,
        "confidence": 70.0,
        "max_rounds": 3,
        "problem_type": "low_constitution_score",
    },
    "constitution/hardcoded_secrets": {
        "strategy": FixStrategy.HUMAN_REQUIRED,
        "confidence": 0.0,
        "max_rounds": 0,
        "problem_type": "hardcoded_secrets",
    },
    "constitution/low_keyword_density": {
        "strategy": FixStrategy.AUTO_FIX,
        "confidence": 85.0,
        "max_rounds": 3,
        "problem_type": "low_keyword_density",
    },
    "constitution/missing_section_headers": {
        "strategy": FixStrategy.AUTO_FIX,
        "confidence": 95.0,
        "max_rounds": 2,
        "problem_type": "missing_section_headers",
    },
    "constitution/hollow_content": {
        "strategy": FixStrategy.AUTO_FIX,
        "confidence": 90.0,
        "max_rounds": 2,
        "problem_type": "hollow_content",
    },

    # ── enforcement/framework_enforcer.py ──
    "framework_enforcer/missing_spec_tracking": {
        "strategy": FixStrategy.AUTO_FIX,
        "confidence": 95.0,
        "max_rounds": 2,
        "problem_type": "missing_spec_tracking",
    },
    "framework_enforcer/missing_traceability": {
        "strategy": FixStrategy.AUTO_FIX,
        "confidence": 90.0,
        "max_rounds": 2,
        "problem_type": "missing_traceability",
    },
    "framework_enforcer/constitution_score_low": {
        "strategy": FixStrategy.AUTO_FIX_WITH_VERIFICATION,
        "confidence": 70.0,
        "max_rounds": 3,
        "problem_type": "low_constitution_score",
    },
    "framework_enforcer/coverage_low": {
        "strategy": FixStrategy.AUTO_FIX_WITH_VERIFICATION,
        "confidence": 60.0,
        "max_rounds": 3,
        "problem_type": "low_coverage",
    },
    "framework_enforcer/missing_aspice_docs": {
        "strategy": FixStrategy.AUTO_FIX,
        "confidence": 85.0,
        "max_rounds": 2,
        "problem_type": "missing_aspice_docs",
    },

    # ── enforcement/policy_engine.py ──
    "policy_engine/missing_commit_task_id": {
        "strategy": FixStrategy.AUTO_FIX,
        "confidence": 90.0,
        "max_rounds": 1,
        "problem_type": "missing_commit_task_id",
    },
    "policy_engine/quality_gate_low": {
        "strategy": FixStrategy.AUTO_FIX_WITH_VERIFICATION,
        "confidence": 70.0,
        "max_rounds": 3,
        "problem_type": "low_constitution_score",
    },
    "policy_engine/test_coverage_low": {
        "strategy": FixStrategy.AUTO_FIX_WITH_VERIFICATION,
        "confidence": 60.0,
        "max_rounds": 3,
        "problem_type": "low_coverage",
    },
    "policy_engine/missing_aspice_docs": {
        "strategy": FixStrategy.AUTO_FIX,
        "confidence": 85.0,
        "max_rounds": 2,
        "problem_type": "missing_aspice_docs",
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

    # ── gap_detector/ ──
    "gap_detector/critical_gap": {
        "strategy": FixStrategy.AUTO_FIX_WITH_VERIFICATION,
        "confidence": 65.0,
        "max_rounds": 3,
        "problem_type": "gap_critical",
    },
    "gap_detector/major_gap": {
        "strategy": FixStrategy.AUTO_FIX,
        "confidence": 80.0,
        "max_rounds": 2,
        "problem_type": "gap_critical",
    },
    "gap_detector/minor_gap": {
        "strategy": FixStrategy.AUTO_FIX,
        "confidence": 85.0,
        "max_rounds": 2,
        "problem_type": "gap_critical",
    },

    # ── detection/drift_detector.py ──
    "drift_detector/drift_low": {
        "strategy": FixStrategy.AUTO_FIX_WITH_VERIFICATION,
        "confidence": 55.0,
        "max_rounds": 3,
        "problem_type": "drift_detected",
    },

    # ── kill_switch/ ──
    "kill_switch/circuit_open": {
        "strategy": FixStrategy.HUMAN_REQUIRED,
        "confidence": 0.0,
        "max_rounds": 0,
        "problem_type": "hard_rule_violation",
    },

    # ── core/quality_gate/phase_truth_verifier.py ──
    "phase_truth_verifier/phase_truth_low": {
        "strategy": FixStrategy.AUTO_FIX_WITH_VERIFICATION,
        "confidence": 65.0,
        "max_rounds": 3,
        "problem_type": "low_constitution_score",
    },
    "phase_truth_verifier/enforcement_block_failed": {
        "strategy": FixStrategy.AUTO_FIX_WITH_VERIFICATION,
        "confidence": 65.0,
        "max_rounds": 3,
        "problem_type": "low_constitution_score",
    },
    "phase_truth_verifier/pytest_failures": {
        "strategy": FixStrategy.AUTO_FIX_WITH_VERIFICATION,
        "confidence": 50.0,
        "max_rounds": 3,
        "problem_type": "pytest_failures",
    },

    # ── steering/steering_loop.py ──
    "steering_loop/convergence_stalled": {
        "strategy": FixStrategy.AUTO_FIX,
        "confidence": 75.0,
        "max_rounds": 5,
        "problem_type": "low_constitution_score",
    },

    # ── gates ──
    "gate/gate1_blocked": {
        "strategy": FixStrategy.AUTO_FIX_WITH_VERIFICATION,
        "confidence": 70.0,
        "max_rounds": 3,
        "problem_type": "low_constitution_score",
    },
    "gate/gate2_blocked": {
        "strategy": FixStrategy.AUTO_FIX_WITH_VERIFICATION,
        "confidence": 70.0,
        "max_rounds": 3,
        "problem_type": "low_constitution_score",
    },
    "gate/gate3_blocked": {
        "strategy": FixStrategy.AUTO_FIX_WITH_VERIFICATION,
        "confidence": 65.0,
        "max_rounds": 3,
        "problem_type": "low_constitution_score",
    },
    "gate/gate4_blocked": {
        "strategy": FixStrategy.HUMAN_REQUIRED,
        "confidence": 0.0,
        "max_rounds": 0,
        "problem_type": "hard_rule_violation",
    },

    # ── phase_hooks / preflight ──
    "phase_hooks/preflight_failure": {
        "strategy": FixStrategy.AUTO_FIX_WITH_VERIFICATION,
        "confidence": 70.0,
        "max_rounds": 3,
        "problem_type": "low_constitution_score",
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
) -> Tuple[FixStrategy, float, int, str]:
    """Classify a detection result.

    Args:
        source: Module that detected the problem (e.g. "constitution/runner", "framework_enforcer")
        details: Raw detection data for context-specific classification

    Returns:
        (FixStrategy, confidence_pct, max_rounds, problem_type_string)
    """
    # Check for hard rule violations first
    if is_hard_rule_violation(details):
        return (FixStrategy.HUMAN_REQUIRED, 0.0, 0, "hard_rule_violation")

    # Check for actual secrets
    content = details.get("content", "")
    if content and is_actual_secret(content):
        return (FixStrategy.HUMAN_REQUIRED, 0.0, 0, "hardcoded_secrets")

    # Special case: gate 4 always human-required
    gate_num = details.get("gate_num")
    if gate_num == 4:
        return (FixStrategy.HUMAN_REQUIRED, 0.0, 0, "hard_rule_violation")

    # Look up in classification table
    problem_type = details.get("problem_type", "")

    # Try exact key: source/problem_type
    key = f"{source}/{problem_type}" if problem_type else source
    entry = CLASSIFICATION_TABLE.get(key)

    # Fallback: try source only (for source-level defaults like kill_switch)
    if entry is None:
        key = source
        entry = CLASSIFICATION_TABLE.get(key)

    # Second fallback: match by source prefix
    if entry is None:
        for cls_key, cls_entry in CLASSIFICATION_TABLE.items():
            if source.startswith(cls_key.split("/")[0]):
                entry = cls_entry
                break

    if entry is None:
        # Sensible default: auto-fix with verification
        return (FixStrategy.AUTO_FIX_WITH_VERIFICATION, 65.0, 3, "low_constitution_score")

    strategy = entry["strategy"]
    confidence = entry["confidence"]
    max_rounds = entry["max_rounds"]
    resolved_type = entry["problem_type"]

    # Adjust confidence by dimension if applicable
    dimension = details.get("dimension", "")
    if dimension in DIMENSION_CONFIDENCE:
        confidence = DIMENSION_CONFIDENCE[dimension]

    return (strategy, confidence, max_rounds, resolved_type)


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
