"""Regression tests for spec_logic_checker.verify() (Bug H2).

Bug H2: verify() silently returned (True, "Logic conforms to SRS") for
verification strategies it didn't implement (no_extra_char_insertion,
L1_L2_retryable_L3_L4_not, consecutive_failures_trigger_circuit_break,
timeout_raises_TimeoutError, etc.) — a false PASS that hid missing
auto-checks from downstream callers.
"""

from __future__ import annotations

import importlib

import pytest


def _load_module():
    """Import scripts.spec_logic_checker with the scripts/ dir on sys.path."""
    import sys
    from pathlib import Path
    scripts_dir = str(Path(__file__).resolve().parents[1] / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    return importlib.import_module("spec_logic_checker")


@pytest.fixture
def module():
    return _load_module()


def _make_validator(module, requirements):
    """Build a SemanticValidator bypassing SRS parsing."""
    val = module.SemanticValidator.__new__(module.SemanticValidator)
    val.requirements = requirements
    return val


class TestVerifyUnhandledStrategy:
    """Bug H2: unhandled verification strategies must NOT silently PASS."""

    UNHANDLED = [
        "no_extra_char_insertion",
        "L1_L2_retryable_L3_L4_not",
        "consecutive_failures_trigger_circuit_break",
        "timeout_raises_TimeoutError",
        "totally_unknown_strategy_xyz",
    ]

    @pytest.mark.parametrize("strategy", UNHANDLED)
    def test_unhandled_strategy_surfaces_manual_review(self, module, strategy):
        """For each unhandled strategy, verify() must return a message
        that explicitly says manual review is required."""
        val = _make_validator(module, {
            "FR-01": {"description": "test", "verification": strategy},
        })
        msg = val.verify("", "FR-01")[1]
        assert "manual review required" in msg, (
            f"strategy={strategy!r}: msg must mention 'manual review required', "
            f"got {msg!r}"
        )
        assert "not auto-checkable" in msg, (
            f"strategy={strategy!r}: msg must say 'not auto-checkable', "
            f"got {msg!r}"
        )

    def test_unhandled_strategy_does_not_pretend_to_pass(self, module):
        """The generic 'Logic conforms to SRS' message must NOT be used
        for unhandled strategies — it would silently mark them as
        auto-verified."""
        val = _make_validator(module, {
            "FR-01": {"description": "test", "verification": "unknown_xyz"},
        })
        msg = val.verify("", "FR-01")[1]
        assert msg != "Logic conforms to SRS", (
            f"unhandled strategy must not return generic 'Logic conforms to SRS': {msg!r}"
        )


class TestVerifyHandledStrategies:
    """Sanity: handled strategies continue to behave as before."""

    def test_output_len_le_input_flags_insertion(self, module):
        val = _make_validator(module, {
            "FR-01": {"description": "x", "verification": "output_len_le_input"},
        })
        passed, msg = val.verify('s = a + "."', "FR-01")
        assert not passed
        assert "extra characters" in msg

    def test_output_len_le_input_passes_clean_code(self, module):
        val = _make_validator(module, {
            "FR-01": {"description": "x", "verification": "output_len_le_input"},
        })
        passed, msg = val.verify('s = a + b', "FR-01")
        assert passed
        assert "output_len_le_input" in msg

    def test_single_file_format_equals_multi_passes(self, module):
        val = _make_validator(module, {
            "FR-01": {"description": "x", "verification": "single_file_format_equals_multi"},
        })
        passed, msg = val.verify('if len(xs) == 1:', "FR-01")
        assert passed
        assert "special handling" in msg

    def test_manual_verification_required_marker(self, module):
        """manual_verification_required is a by-design marker; keep its
        return value but make the message explicit about why."""
        val = _make_validator(module, {
            "FR-01": {"description": "x", "verification": "manual_verification_required"},
        })
        passed, msg = val.verify("", "FR-01")
        assert passed
        assert "manual" in msg.lower()


class TestVerifyMissingRequirement:
    def test_missing_fr_id_returns_message(self, module):
        val = _make_validator(module, {})
        passed, msg = val.verify("", "FR-99")
        assert passed
        assert "not found" in msg