"""Verification Constitution Checker — wraps ConstitutionAsCode for SteeringIntegrator.

Bridges the steering integration layer to enforcement/constitution_as_code.py
(rules R001-R007). Falls back to a no-op if enforcement package is unavailable.

Used by:
    steering/integrations.py :: SteeringIntegrator.iterate_with_full_check()

Interface contract:
    check(context: dict) -> {"passed": bool, "violations": list[str]}
"""

from __future__ import annotations

from typing import Any


class VerificationConstitutionChecker:
    """
    Thin wrapper that delegates context checks to enforcement.constitution_as_code.

    Gracefully degrades to a pass-through if enforcement/ is not importable
    (e.g., running in a minimal environment without the full package tree).
    """

    def __init__(self) -> None:
        """Lazy-load ConstitutionAsCode; degrade gracefully on ImportError."""
        try:
            from enforcement.constitution_as_code import ConstitutionAsCode
            self._cac: Any = ConstitutionAsCode()
        except ImportError:
            self._cac = None

    def check(self, context: dict[str, Any]) -> dict[str, Any]:
        """
        Run constitution rules against the provided context.

        Args:
            context: dict with optional keys:
                - "commit_message": str  (checked by R001)
                - "command": str         (checked by R002)
                - "quality_score": float (checked by R003)
                - "coverage": float      (checked by R004)
                - "security_score": float(checked by R005)
                - "approval_context": dict (checked by R006)

        Returns:
            {"passed": bool, "violations": list[str]}
        """
        if self._cac is None:
            return {"passed": True, "violations": []}

        try:
            violations = self._cac.check(context)
            return {
                "passed": len(violations) == 0,
                "violations": [v.error_message for v in violations],
            }
        except MemoryError:
            raise
        except Exception as e:  # pylint: disable=broad-exception-caught
            return {
                "passed": False,
                "violations": [f"Constitution checking error: {e}"],
            }


    def enforce(self, context: dict[str, Any]) -> None:
        """
        Enforce constitution rules — raises ConstitutionViolation on breach.

        Delegates to ConstitutionAsCode.enforce() if available.
        """
        if self._cac is None:
            return
        self._cac.enforce(context)
