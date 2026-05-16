"""Tests for core.fsm.fsm — FSM state validation."""

import pytest
from core.fsm.fsm import validate_fsm_state, FSMError, is_valid_fsm_state


class TestValidateFsmState:
    """Tests for validate_fsm_state() — the main validation entry point."""

    # ── Valid states pass through unchanged ────────────────────────────
    @pytest.mark.parametrize("state", [
        "INIT", "RUNNING", "PAUSED", "FREEZE", "DONE",
        "OPEN", "HALF_OPEN", "CLOSED",
    ])
    def test_valid_state_passes_through(self, state):
        assert validate_fsm_state(state) == state

    @pytest.mark.parametrize("state", [
        "init", "running", "paused", "freeze", "done",
        "open", "half_open", "closed",
    ])
    def test_lowercase_valid_state_normalized(self, state):
        assert validate_fsm_state(state) == state.upper()

    @pytest.mark.parametrize("state", [
        "  RUNNING  ", " INIT\n", "\tPAUSED",
    ])
    def test_whitespace_stripped(self, state):
        assert validate_fsm_state(state) == state.strip().upper()

    # ── Deprecated states auto-correct ────────────────────────────────
    def test_active_auto_corrects_to_running(self):
        assert validate_fsm_state("ACTIVE") == "RUNNING"

    def test_lowercase_active_auto_corrects(self):
        assert validate_fsm_state("active") == "RUNNING"

    def test_active_padded_auto_corrects(self):
        assert validate_fsm_state("  ACTIVE  ") == "RUNNING"

    # ── auto_correct=False raises on deprecated ───────────────────────
    def test_deprecated_raises_when_no_auto_correct(self):
        with pytest.raises(FSMError, match="Invalid FSM state"):
            validate_fsm_state("ACTIVE", auto_correct=False)

    # ── Invalid states raise FSMError ────────────────────────────────
    @pytest.mark.parametrize("bad_state", [
        "INVALID", "UNKNOWN", "ACTIVE_OLD", "STOPPED", "IDLE",
        "random-string", "123", "",
    ])
    def test_invalid_state_raises(self, bad_state):
        with pytest.raises(FSMError, match="FSM state"):
            validate_fsm_state(bad_state)

    def test_empty_string_raises(self):
        with pytest.raises(FSMError, match="non-empty string"):
            validate_fsm_state("")

    def test_whitespace_only_raises(self):
        with pytest.raises(FSMError, match="non-empty string"):
            validate_fsm_state("   ")

    # ── Type errors ───────────────────────────────────────────────────
    def test_none_raises(self):
        with pytest.raises(FSMError, match="non-empty string"):
            validate_fsm_state(None)  # type: ignore[arg-type]

    def test_int_raises(self):
        with pytest.raises(FSMError, match="non-empty string"):
            validate_fsm_state(42)  # type: ignore[arg-type]


class TestIsValidFsmState:
    """Tests for is_valid_fsm_state() — the boolean check variant."""

    @pytest.mark.parametrize("state", ["INIT", "RUNNING", "PAUSED", "DONE"])
    def test_valid_states_return_true(self, state):
        assert is_valid_fsm_state(state) is True

    @pytest.mark.parametrize("state", ["ACTIVE", "INVALID", "GARBAGE", ""])
    def test_invalid_or_deprecated_return_false(self, state):
        assert is_valid_fsm_state(state) is False

    def test_does_not_raise(self):
        # is_valid_fsm_state should never raise
        assert is_valid_fsm_state("anything-at-all") is False
        assert is_valid_fsm_state("") is False
