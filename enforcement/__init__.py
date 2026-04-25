"""
Enforcement Package
===================
Policy Engine + Execution Registry + Constitution as Code.

Transforms framework from suggestions into mandatory enforcement.
"""

from .policy_engine import (
    PolicyEngine,
    Policy,
    PolicyResult,
    EnforcementLevel,
    PolicyViolationException,
    create_hard_block_engine,
)

from .execution_registry import (
    ExecutionRegistry,
    ExecutionRecord,
    create_minimal_registry,
)

from .constitution_as_code import (
    ConstitutionAsCode,
    Rule,
    RuleSeverity,
    ConstitutionViolation,
    ConstitutionWarning,
)

from .server_enforcer import ServerEnforcer
from .framework_enforcer import FrameworkEnforcer, EnforcementResult

__all__ = [
    # Policy Engine
    "PolicyEngine",
    "Policy",
    "PolicyResult",
    "EnforcementLevel",
    "PolicyViolationException",
    "create_hard_block_engine",
    # Execution Registry
    "ExecutionRegistry",
    "ExecutionRecord",
    "create_minimal_registry",
    # Constitution as Code
    "ConstitutionAsCode",
    "Rule",
    "RuleSeverity",
    "ConstitutionViolation",
    "ConstitutionWarning",
    # Server-Side Enforcer
    "ServerEnforcer",
    # Framework Enforcer
    "FrameworkEnforcer",
    "EnforcementResult",
]
