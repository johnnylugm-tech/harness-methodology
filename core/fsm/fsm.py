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

#: Which code path writes each state into `.methodology/state.json`, or `None`
#: with the reason nothing does.
#:
#: Round 108 站A. The set above has eight members and one of them has ever been
#: written: `RUNNING`, at the two sites named below. Measured across the fifteen
#: corpus projects on 2026-09-08, every `state.json` reads `"state": "RUNNING"`.
#: The consequences are not theoretical — `core/phase_hooks.py`'s
#: `preflight_fsm_check` blocks a run when the state is `FREEZE` or `PAUSED`,
#: and the only thing that has ever produced either is a test fixture;
#: `constitution/CONSTITUTION.md` and `SKILL.md` carried HR-14 as
#: "Integrity < 40 → FREEZE" — a rule with no writer at either end. Round 109
#: 站5 gave it the input; Round 110 站2 made both documents say what it
#: actually raises (`HR14_INTEGRITY`), because a rule whose stated effect
#: cannot happen is worse than a rule with no stated effect at all.
#:
#: This table is DECLARED, not inferred. Inferring it was the first plan and
#: the measurement killed it: an AST scan for a `"state"` key holding a string
#: constant finds five sites, of which three are `preflight_fsm_check`'s return
#: envelope using the same key for a different vocabulary (`UNKNOWN`,
#: `CORRUPT` — neither is an FSM state), and `core/state_io.py` is the single
#: entry point for READING state.json only: writers sit in `phase_hooks`,
#: `push_cmds`, `project_cmds`, `advance_commit` and
#: `phase_completed_recovery`. With no single write path there is nothing
#: sound to infer from, so the answer is written down by the people who know
#: it and `tests/test_fsm_states_have_producers.py` pins that the table and
#: the set stay the same shape.
#:
#: Adding a state means adding a row. A row may honestly say `None` — what it
#: may not do is be absent, which is how seven of these came to be accepted,
#: validated, documented and blocked on without anyone noticing that nothing
#: creates them.
STATE_PRODUCERS: Final[dict[str, str | None]] = {
    "RUNNING": ("core/phase_hooks.py::preflight_fsm_check (bare `run-phase "
                "--phase 1` auto-init) and cli/project_cmds.py::init-project"),
    "INIT": None,       # no writer; `validate_fsm_state`'s default in
                        # cli/advance_commit.py is the only place the word
                        # appears at runtime, and it is a read fallback
    "PAUSED": None,     # docs/USER_MANUAL.md names "manual pause" as the
                        # cause and the manual gives no command that sets it
    "FREEZE": None,     # HR-14's stated effect; nothing writes it. Round 109
                        # 站5 gave HR-14 an input — harness_bridge's
                        # `_record_integrity` writes state["integrity"] at
                        # every gate finalize — so the rule can now fire, but
                        # what it produces is still an EscalationCondition
                        # (HR14_INTEGRITY) and not this state. Who may move a
                        # project into FREEZE is undecided; see
                        # docs/PROPOSAL_ADJUDICATIONS.md Round 109 §5
    "DONE": None,       # no writer; a finished project keeps RUNNING
    "OPEN": None,       # circuit-breaker vocabulary; the kill switch keeps
                        # its circuits in core/phase_hooks.py's own registry
    "HALF_OPEN": None,  # as OPEN
    "CLOSED": None,     # as OPEN
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
