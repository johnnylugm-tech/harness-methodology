#!/usr/bin/env python3
"""
Error Classification — Symphony-inspired two-level error classification with
differentiated retry strategies per error class.

Level 1: ErrorClass (what kind of error)
Level 2: problem_type (specific sub-type within the class)

Each ErrorClass has a profile defining default strategy, confidence, retry strategy,
and escalation threshold.
"""

from __future__ import annotations
from dataclasses import dataclass
from enum import Enum


class ErrorClass(Enum):
    """Top-level error classification for differentiated recovery."""
    MISSING_ARTIFACT = "missing_artifact"
    QUALITY_DEFICIT = "quality_deficit"
    HARD_VIOLATION = "hard_violation"
    DRIFT_DETECTED = "drift_detected"
    GATE_FAILURE = "gate_failure"


class RetryStrategy(Enum):
    """How retry delays should behave across rounds."""
    LINEAR = "linear"
    EXPONENTIAL_BACKOFF = "exponential_backoff"
    CONSTANT = "constant"


@dataclass
class ErrorClassProfile:
    """Profile defining recovery behavior for an ErrorClass."""
    error_class: ErrorClass
    default_confidence: float
    escalation_threshold: int
    retry_strategy: RetryStrategy = RetryStrategy.LINEAR


# Each ErrorClass has a distinct recovery profile
ERROR_CLASS_PROFILES: dict[ErrorClass, ErrorClassProfile] = {
    ErrorClass.MISSING_ARTIFACT: ErrorClassProfile(
        error_class=ErrorClass.MISSING_ARTIFACT,
        default_confidence=85.0,
        escalation_threshold=3,
        retry_strategy=RetryStrategy.LINEAR,
    ),
    ErrorClass.QUALITY_DEFICIT: ErrorClassProfile(
        error_class=ErrorClass.QUALITY_DEFICIT,
        default_confidence=65.0,
        escalation_threshold=5,
        retry_strategy=RetryStrategy.EXPONENTIAL_BACKOFF,
    ),
    ErrorClass.HARD_VIOLATION: ErrorClassProfile(
        error_class=ErrorClass.HARD_VIOLATION,
        default_confidence=0.0,
        escalation_threshold=0,  # immediate escalation
        retry_strategy=RetryStrategy.CONSTANT,
    ),
    ErrorClass.DRIFT_DETECTED: ErrorClassProfile(
        error_class=ErrorClass.DRIFT_DETECTED,
        default_confidence=50.0,
        escalation_threshold=3,
        retry_strategy=RetryStrategy.LINEAR,
    ),
    ErrorClass.GATE_FAILURE: ErrorClassProfile(
        error_class=ErrorClass.GATE_FAILURE,
        default_confidence=60.0,
        escalation_threshold=3,
        retry_strategy=RetryStrategy.CONSTANT,
    ),
}

# Map CLASSIFICATION_TABLE source/problem_type keys → ErrorClass
# Keys follow the pattern used in classifier.py CLASSIFICATION_TABLE
ERROR_CLASS_MAP: dict[str, ErrorClass] = {
    # Constitution runner entries
    "constitution_runner/low_constitution_score": ErrorClass.QUALITY_DEFICIT,
    "constitution_runner/low_keyword_density": ErrorClass.QUALITY_DEFICIT,
    "constitution_runner/missing_section_headers": ErrorClass.MISSING_ARTIFACT,
    "constitution_runner/hollow_content": ErrorClass.QUALITY_DEFICIT,
    "constitution_runner/hardcoded_secrets": ErrorClass.HARD_VIOLATION,
    "constitution_runner/hard_rule_violation": ErrorClass.HARD_VIOLATION,

    # Framework enforcer entries
    "framework_enforcer/hard_rule_violation": ErrorClass.HARD_VIOLATION,
    "framework_enforcer/low_constitution_score": ErrorClass.QUALITY_DEFICIT,
    "framework_enforcer/missing_artifact": ErrorClass.MISSING_ARTIFACT,

    # Policy engine entries
    "policy_engine/hard_rule_violation": ErrorClass.HARD_VIOLATION,
    "policy_engine/low_constitution_score": ErrorClass.QUALITY_DEFICIT,

    # Constitution as code entries
    "constitution_as_code/hard_rule_violation": ErrorClass.HARD_VIOLATION,
    "constitution_as_code/low_constitution_score": ErrorClass.QUALITY_DEFICIT,

    # Gap detector entries
    "gap_detector/gap_critical": ErrorClass.MISSING_ARTIFACT,
    "gap_detector/low_constitution_score": ErrorClass.QUALITY_DEFICIT,

    # Drift detector entries
    "drift_detector/drift_detected": ErrorClass.DRIFT_DETECTED,

    # Kill switch entries
    "kill_switch/hard_rule_violation": ErrorClass.HARD_VIOLATION,

    # Phase truth verifier
    "phase_truth_verifier/phase_truth_low": ErrorClass.GATE_FAILURE,
    "phase_truth_verifier/low_constitution_score": ErrorClass.QUALITY_DEFICIT,

    # Steering loop
    "steering_loop/low_constitution_score": ErrorClass.QUALITY_DEFICIT,

    # Gate blocked entries
    "gate/gate1_blocked": ErrorClass.GATE_FAILURE,
    "gate/gate2_blocked": ErrorClass.GATE_FAILURE,
    "gate/gate3_blocked": ErrorClass.GATE_FAILURE,
    "gate/gate4_blocked": ErrorClass.GATE_FAILURE,

    # Phase hooks
    "phase_hooks/low_constitution_score": ErrorClass.QUALITY_DEFICIT,
    "phase_hooks/missing_artifact": ErrorClass.MISSING_ARTIFACT,
    "phase_hooks/hard_rule_violation": ErrorClass.HARD_VIOLATION,
    "phase_hooks/pytest_failures": ErrorClass.QUALITY_DEFICIT,

    # Auto-fix specific sub-types
    "auto_fix/missing_artifact": ErrorClass.MISSING_ARTIFACT,
    "auto_fix/missing_spec_tracking": ErrorClass.MISSING_ARTIFACT,
    "auto_fix/missing_traceability": ErrorClass.MISSING_ARTIFACT,
    "auto_fix/missing_aspice_docs": ErrorClass.MISSING_ARTIFACT,
    "auto_fix/low_keyword_density": ErrorClass.QUALITY_DEFICIT,
    "auto_fix/missing_section_headers": ErrorClass.MISSING_ARTIFACT,
    "auto_fix/hollow_content": ErrorClass.QUALITY_DEFICIT,
    "auto_fix/low_coverage": ErrorClass.QUALITY_DEFICIT,
    "auto_fix/pytest_failures": ErrorClass.QUALITY_DEFICIT,
    "auto_fix/low_constitution_score": ErrorClass.QUALITY_DEFICIT,
    "auto_fix/gap_critical": ErrorClass.MISSING_ARTIFACT,
    "auto_fix/drift_detected": ErrorClass.DRIFT_DETECTED,
}


def get_error_class(source: str, problem_type: str) -> ErrorClass:
    """Look up ErrorClass for a source/problem_type pair."""
    key = f"{source}/{problem_type}"
    if key in ERROR_CLASS_MAP:
        return ERROR_CLASS_MAP[key]
    # Fallback: try source-only key
    for k, v in ERROR_CLASS_MAP.items():
        if k.startswith(f"{source}/"):
            return v
    return ErrorClass.QUALITY_DEFICIT


def get_retry_config(error_class: ErrorClass) -> tuple[int, RetryStrategy]:
    """Get (max_rounds, retry_strategy) for an ErrorClass."""
    profile = ERROR_CLASS_PROFILES.get(error_class)
    if profile is None:
        return (3, RetryStrategy.LINEAR)
    return (profile.escalation_threshold, profile.retry_strategy)


def compute_retry_delay(attempt: int, strategy: RetryStrategy, base_ms: int = 10_000, cap_ms: int = 300_000) -> int:
    """Compute retry delay in ms for a given attempt and strategy."""
    if strategy == RetryStrategy.CONSTANT:
        return base_ms
    if strategy == RetryStrategy.EXPONENTIAL_BACKOFF:
        return min(base_ms * (2 ** (attempt - 1)), cap_ms)
    # LINEAR: same delay each attempt
    return base_ms
