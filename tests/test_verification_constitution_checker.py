"""Tests for constitution/verification_constitution_checker.py."""

import pytest
from constitution.verification_constitution_checker import VerificationConstitutionChecker


class TestVerificationConstitutionChecker:
    def test_check_with_cac_available(self):
        """With ConstitutionAsCode importable, delegates to it."""
        checker = VerificationConstitutionChecker()
        result = checker.check({})
        assert "passed" in result
        assert "violations" in result
        assert isinstance(result["violations"], list)

    def test_check_with_quality_score(self):
        checker = VerificationConstitutionChecker()
        result = checker.check({"quality_score": 95.0})
        assert "passed" in result

    def test_check_with_commit_message(self):
        checker = VerificationConstitutionChecker()
        result = checker.check({"commit_message": "feat: add feature"})
        assert "passed" in result

    def test_enforce_noop_when_cac_none(self, monkeypatch):
        checker = VerificationConstitutionChecker()
        checker._cac = None
        checker.enforce({"quality_score": 50})
        # Should not raise

    def test_enforce_with_cac(self):
        checker = VerificationConstitutionChecker()
        if checker._cac is not None:
            checker.enforce({"quality_score": 100})
            # Should not raise for good score

    def test_init_creates_cac(self):
        checker = VerificationConstitutionChecker()
        assert checker._cac is not None
