"""constitution — team constitution runner.

Exports:
    run_constitution_check: primary entry point for all constitution checks.
    ConstitutionResult: dataclass holding score, passed, violations.
"""

from .runner import (
    run_constitution_check,
    ConstitutionResult,
)

__all__ = ["run_constitution_check", "ConstitutionResult"]
