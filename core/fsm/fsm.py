"""
FSM State Validation
====================
Validates state.json FSM state values against the allowed set.

The FSM uses a subset of the circuit-breaker state model for phase lifecycle:
    INIT, RUNNING, PAUSED, FREEZE, DONE, OPEN, HALF_OPEN, CLOSED

Usage:
    from core.fsm.fsm import validate_fsm_state

    validated = validate_fsm_state("ACTIVE")      # raises FSMError
    validated = validate_fsm_state("RUNNING")     # returns "RUNNING"
"""

from __future__ import annotations

import logging
from typing import Final

logger = logging.getLogger(__name__)

# Full set of valid FSM circuit-breaker states.
# Not all states are used in normal phase flow (e.g. HALF_OPEN, CLOSED
# are reserved for circuit-breaker scenarios).
VALID_FSM_STATES: Final[frozenset[str]] = frozenset({
    "INIT",
    "RUNNING",
    "PAUSED",
    "FREEZE",
    "DONE",
    "OPEN",
    "HALF_OPEN",
    "CLOSED",
})

# Common invalid states that appear in legacy projects
_DEPRECATED_STATE_MAP: Final[dict[str, str]] = {
    "ACTIVE": "RUNNING",  # ACTIVE was used before validation was introduced
}


class FSMError(ValueError):
    """Raised when an FSM state value is invalid or cannot be auto-corrected."""


def validate_fsm_state(state: str, *, auto_correct: bool = True) -> str:
    """Validate (and optionally auto-correct) an FSM state value.

    Args:
        state: The raw state string from state.json.
        auto_correct: If True, known deprecated values (e.g. ACTIVE) are
                      automatically corrected to their modern equivalent.
                      If False, raises FSMError for invalid values.

    Returns:
        Validated (and possibly corrected) state string.

    Raises:
        FSMError: If the state is invalid and cannot be auto-corrected,
                  or auto_correct is False.
    """
    if not isinstance(state, str) or not state.strip():
        raise FSMError(f"FSM state must be a non-empty string, got {type(state).__name__}: {state!r}")

    cleaned = state.strip().upper()

    if cleaned in VALID_FSM_STATES:
        return cleaned

    # Try deprecated-state auto-correction
    if auto_correct:
        corrected = _DEPRECATED_STATE_MAP.get(cleaned)
        if corrected:
            logger.warning("Auto-corrected FSM state %r → %r", state, corrected)
            return corrected

    raise FSMError(
        f"Invalid FSM state: {state!r}. "
        f"Valid: {sorted(VALID_FSM_STATES)}. "
        f"Known deprecated: {dict(_DEPRECATED_STATE_MAP)}."
    )


def is_valid_fsm_state(state: str) -> bool:
    """Check if a state value is valid (without raising or auto-correcting)."""
    try:
        validate_fsm_state(state, auto_correct=False)
        return True
    except FSMError:
        return False
