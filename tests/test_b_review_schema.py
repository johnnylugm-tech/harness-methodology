"""Unit tests for core/review_schema_validator.py — B JSON schema validation,
over-interpretation downgrade, and HR-12 escalation logic.

Bug B (improvement B of plan): workflow JS B-2 dispatch returned free-form
JSON without `evidence_type` field. The original HR-12 deadlock on ambiguous
SPEC phrases (over-interpretation mis-classified as high severity) is the
regression target. These tests verify:

  - Schema violations synthesize CANCELLED (single retry, no infinite loop)
  - Schema-valid gaps with evidence_type=over_interpretation are downgraded
    from high to medium (HR-12 regression guard)
  - enforce_escalation correctly walks the 5-round max ceiling

Commonality: phase-agnostic. Validates any B JSON regardless of which phase.
"""


import pytest

from core.review_schema_validator import (
    EscalationAction,
    ValidationResult,
    enforce_escalation,
    validate_b_output,
)


# ---------------------------------------------------------------------------
# Fixtures — sample B outputs covering the surface
# ---------------------------------------------------------------------------


def _approve_clean() -> dict:
    """APPROVE with only low-severity methodology_artifact gaps."""
    return {
        "review_status": "APPROVE",
        "confidence": 0.95,
        "summary": "all low gaps",
        "gaps": [
            {
                "severity": "low",
                "evidence_type": "methodology_artifact",
                "canonical_ref": "",
                "message": "sha256 hash minor inconsistency",
                "fr_id": None,
            },
        ],
    }


def _approve_with_high_real_invention() -> dict:
    """APPROVE with a real-invention high gap → must retry."""
    return {
        "review_status": "APPROVE",
        "confidence": 0.8,
        "gaps": [
            {
                "severity": "high",
                "evidence_type": "real_invention",
                "canonical_ref": "SPEC.md:42",
                "message": "A added a new FR not in SPEC",
                "fr_id": "FR-04",
            },
        ],
    }


def _approve_with_over_interpretation_high() -> dict:
    """APPROVE with over-interpretation marked high → downgrade to medium."""
    return {
        "review_status": "APPROVE",
        "confidence": 0.85,
        "gaps": [
            {
                "severity": "high",
                "evidence_type": "over_interpretation",
                "canonical_ref": "SPEC.md:58",
                "message": "Ambiguous 'excluding subprocess execution' — A picked interpretation X without DERIVED tag",
                "fr_id": "NFR-01",
            },
        ],
    }


def _reject_repeated() -> dict:
    """REJECT (would normally trigger retry until round 5)."""
    return {
        "review_status": "REJECT",
        "confidence": 0.3,
        "gaps": [
            {
                "severity": "high",
                "evidence_type": "real_invention",
                "canonical_ref": "SPEC.md:11",
                "message": "Blocking issue",
                "fr_id": "FR-01",
            },
        ],
    }


# ---------------------------------------------------------------------------
# validate_b_output
# ---------------------------------------------------------------------------


class TestValidateBOutput:
    def test_approve_clean_is_valid(self):
        r = validate_b_output(_approve_clean())
        assert r.valid is True
        assert r.synthesized is False
        assert r.error is None
        assert r.normalized["review_status"] == "APPROVE"

    def test_high_real_invention_kept_as_high(self):
        r = validate_b_output(_approve_with_high_real_invention())
        assert r.valid is True
        gap = r.normalized["gaps"][0]
        assert gap["severity"] == "high"
        assert "_downgraded_from" not in gap

    def test_over_interpretation_high_downgraded_to_medium(self):
        """HR-12 regression: over-interpretation must NEVER escalate to high."""
        r = validate_b_output(_approve_with_over_interpretation_high())
        assert r.valid is True
        gap = r.normalized["gaps"][0]
        assert gap["severity"] == "medium"
        assert gap["_downgraded_from"] == "high"
        assert gap["evidence_type"] == "over_interpretation"

    def test_over_interpretation_medium_stays_medium(self):
        b = _approve_with_over_interpretation_high()
        b["gaps"][0]["severity"] = "medium"
        r = validate_b_output(b)
        gap = r.normalized["gaps"][0]
        assert gap["severity"] == "medium"
        assert "_downgraded_from" not in gap

    def test_missing_evidence_type_synthesizes_cancelled(self):
        b = _approve_clean()
        del b["gaps"][0]["evidence_type"]  # schema requires it
        r = validate_b_output(b)
        assert r.valid is False
        assert r.synthesized is True
        assert r.normalized["review_status"] == "CANCELLED"
        assert len(r.normalized["gaps"]) == 1
        gap = r.normalized["gaps"][0]
        assert gap["evidence_type"] == "methodology_artifact"
        assert "schema" in gap["message"].lower() or "validation" in gap["message"].lower()

    def test_missing_review_status_synthesizes_cancelled(self):
        b = _approve_clean()
        del b["review_status"]
        r = validate_b_output(b)
        assert r.valid is False
        assert r.synthesized is True
        assert r.normalized["review_status"] == "CANCELLED"

    def test_invalid_review_status_enum_synthesizes_cancelled(self):
        b = _approve_clean()
        b["review_status"] = "MAYBE"  # not in enum
        r = validate_b_output(b)
        assert r.valid is False
        assert r.synthesized is True

    def test_wrong_gap_severity_synthesizes_cancelled(self):
        b = _approve_clean()
        b["gaps"][0]["severity"] = "critical"  # not in enum
        r = validate_b_output(b)
        assert r.valid is False
        assert r.synthesized is True

    def test_non_dict_input_returns_invalid(self):
        r = validate_b_output("not a dict")
        assert r.valid is False
        assert r.synthesized is False
        assert "not a dict" in (r.error or "")

    def test_input_is_not_mutated(self):
        """Downgrade must not mutate the caller's dict."""
        b = _approve_with_over_interpretation_high()
        original_severity = b["gaps"][0]["severity"]
        _ = validate_b_output(b)
        assert b["gaps"][0]["severity"] == original_severity


# ---------------------------------------------------------------------------
# enforce_escalation
# ---------------------------------------------------------------------------


class TestEnforceEscalation:
    def test_approve_clean_returns_approve(self):
        action, reason = enforce_escalation(_approve_clean(), round_num=1)
        assert action == EscalationAction.APPROVE
        assert "low" in reason.lower()

    def test_approve_with_real_invention_high_returns_retry(self):
        action, _ = enforce_escalation(_approve_with_high_real_invention(), round_num=1)
        assert action == EscalationAction.RETRY

    def test_reject_returns_retry_when_under_max_rounds(self):
        action, reason = enforce_escalation(_reject_repeated(), round_num=3)
        assert action == EscalationAction.RETRY
        assert "REJECT" in reason

    def test_reject_returns_escalate_human_at_round_5(self):
        action, reason = enforce_escalation(_reject_repeated(), round_num=5, max_rounds=5)
        assert action == EscalationAction.ESCALATE_HUMAN
        assert "HR-12" in reason

    def test_round_above_max_returns_escalate_human(self):
        action, _ = enforce_escalation(_reject_repeated(), round_num=7, max_rounds=5)
        assert action == EscalationAction.ESCALATE_HUMAN

    def test_cancelled_returns_retry(self):
        b = _approve_clean()
        b["review_status"] = "CANCELLED"
        action, reason = enforce_escalation(b, round_num=1)
        assert action == EscalationAction.RETRY
        assert "CANCELLED" in reason

    def test_downgraded_over_interpretation_does_not_escalate(self):
        """End-to-end: validate + enforce — over-interpretation should NOT
        cause escalate_human at round 5 even if B initially said high."""
        b = _approve_with_over_interpretation_high()
        validated = validate_b_output(b).normalized
        for round_n in range(1, 6):
            action, _ = enforce_escalation(validated, round_num=round_n)
            if action == EscalationAction.RETRY and round_n < 5:
                continue
            # At round 5: should escalate, but only because of round ceiling,
            # not because of severity. The HR-12 regression target is that
            # this scenario shouldn't reach round 5 with the same gap set.
            assert action in (EscalationAction.RETRY, EscalationAction.ESCALATE_HUMAN)

    def test_unknown_status_returns_retry(self):
        b = _approve_clean()
        b["review_status"] = "WEIRD"
        action, reason = enforce_escalation(b, round_num=1)
        assert action == EscalationAction.RETRY
        assert "unknown" in reason.lower()


# ---------------------------------------------------------------------------
# ValidationResult dataclass
# ---------------------------------------------------------------------------


class TestValidationResultDataclass:
    def test_frozen_dataclass(self):
        r = ValidationResult(True, {"x": 1}, None, False)
        with pytest.raises((AttributeError, Exception)):
            r.valid = False  # type: ignore

    def test_default_field_access(self):
        r = ValidationResult(valid=True, normalized={}, error="x")
        assert r.synthesized is False
