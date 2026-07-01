"""Tests for constitution phase-dimension activation (Task 7).

Bug 1: runner.py unconditionally requests security keywords for all phases,
       even when security is not in active_dimensions (e.g. P3).
Bug 2: profile.py stale comment block says P3 has threshold=80 and
       maintainability="kept", but actual values are threshold=30.0
       and active_dimensions=["correctness"] only.
"""
import tempfile
from pathlib import Path
from unittest.mock import patch

from core.quality_gate.constitution import runner, profile


class TestSecurityKwNotRequestedWhenNotActive:
    """runner.py must not request security keywords for phases where
    security is not in active_dimensions."""

    def _make_tracking_profile(self):
        """Build a mock profile that tracks dimension_keywords_for_phase calls."""
        real_profile = profile.get_profile()
        calls = []

        class TrackingProfile:
            def __init__(self):
                self.phases = real_profile.phases

            def dimension_keywords_for_phase(self, dim, phase):
                calls.append((dim, phase))
                return real_profile.dimension_keywords_for_phase(dim, phase)

            def file_filter_keywords(self, check_type):
                return real_profile.file_filter_keywords(check_type)

        return TrackingProfile(), calls

    def test_p3_security_kw_not_requested(self):
        """Phase 3 has active_dimensions=[correctness]. Security keywords
        must NOT be requested from dimension_keywords_for_phase."""
        mock_profile, calls = self._make_tracking_profile()
        with patch.object(runner, "get_profile", return_value=mock_profile):
            with tempfile.TemporaryDirectory() as tmp:
                f = Path(tmp) / "example.py"
                f.write_text("def foo():\n    pass\n" * 20)
                runner._scan_file_compliance(f, phase=3)
                security_calls = [c for c in calls if c[0] == "security" and c[1] == 3]
                assert len(security_calls) == 0, (
                    f"security keywords requested for inactive phase 3: {security_calls}"
                )

    def test_p4_security_kw_not_requested(self):
        """Phase 4 has active_dimensions=[correctness]. Security keywords
        must NOT be requested from dimension_keywords_for_phase."""
        mock_profile, calls = self._make_tracking_profile()
        with patch.object(runner, "get_profile", return_value=mock_profile):
            with tempfile.TemporaryDirectory() as tmp:
                f = Path(tmp) / "test_foo.py"
                f.write_text("def test_foo():\n    assert True\n" * 20)
                runner._scan_file_compliance(f, phase=4)
                security_calls = [c for c in calls if c[0] == "security" and c[1] == 4]
                assert len(security_calls) == 0, (
                    f"security keywords requested for inactive phase 4: {security_calls}"
                )

    def test_p5_security_kw_requested(self):
        """Phase 5 has active_dimensions=[correctness, security].
        Security keywords SHOULD be requested."""
        mock_profile, calls = self._make_tracking_profile()
        with patch.object(runner, "get_profile", return_value=mock_profile):
            with tempfile.TemporaryDirectory() as tmp:
                f = Path(tmp) / "VERIFICATION_REPORT.md"
                f.write_text("# Verification Report\n" + "auth and validation\n" * 50)
                runner._scan_file_compliance(f, phase=5)
                security_calls = [c for c in calls if c[0] == "security" and c[1] == 5]
                assert len(security_calls) == 1, (
                    f"security keywords NOT requested for phase 5: {security_calls}"
                )


class TestP3ProfileActiveDimensions:
    """P3 actual profile values must match documented behavior."""

    def test_p3_active_dimensions_is_correctness_only(self):
        """Phase 3 active_dimensions must be ['correctness'] only."""
        p = profile.get_profile()
        p3 = p.phases.get(3)
        assert p3 is not None, "Phase 3 profile not found"
        assert p3.active_dimensions == ["correctness"], (
            f"P3 active_dimensions is {p3.active_dimensions}, expected ['correctness']"
        )

    def test_p3_composite_threshold_is_30(self):
        """Phase 3 composite_threshold must be 30.0."""
        p = profile.get_profile()
        p3 = p.phases.get(3)
        assert p3 is not None, "Phase 3 profile not found"
        assert p3.composite_threshold == 30.0, (
            f"P3 composite_threshold is {p3.composite_threshold}, expected 30.0"
        )

    def test_p3_has_no_security_dimension_keywords(self):
        """Phase 3 dimension_keywords must NOT contain a 'security' override
        (security is not active, so it should not have per-phase keywords)."""
        p = profile.get_profile()
        p3 = p.phases.get(3)
        assert p3 is not None, "Phase 3 profile not found"
        assert "security" not in p3.dimension_keywords, (
            f"P3 dimension_keywords contains 'security': {p3.dimension_keywords}"
        )
