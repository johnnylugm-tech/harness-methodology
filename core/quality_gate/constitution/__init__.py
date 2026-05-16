"""constitution — team constitution runner.

Exports:
    run_constitution_check: primary entry point for all constitution checks.
    ConstitutionResult: dataclass holding score, passed, violations.
    PHASE_CHECK_TYPES: canonical phase-int → check_type str mapping (1–8).
"""

from .runner import (
    run_constitution_check,
    ConstitutionResult,
    PHASE_CHECK_TYPES,
)

__all__ = ["run_constitution_check", "ConstitutionResult", "PHASE_CHECK_TYPES"]
