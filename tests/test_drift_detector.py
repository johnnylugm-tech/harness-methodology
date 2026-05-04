"""
Unit tests for DriftDetector.
"""

from detection.drift_detector import DriftDetector


class TestDriftDetector:
    """Tests for the DriftDetector class."""

    def test_detect_phase_drift_no_state(self, tmp_path):
        """Verify drift detection when state.json is missing."""
        detector = DriftDetector(str(tmp_path))
        result = detector.detect_phase_drift()
        assert result.has_drift is False
        assert result.score == 1.0

    def test_detect_phase_drift_with_missing_artifacts(self, tmp_path):
        """Verify drift detection when expected artifacts are missing."""
        # Setup methodology state
        method_dir = tmp_path / ".methodology"
        method_dir.mkdir()
        state_path = method_dir / "state.json"
        # Phase 2 expects SAD.md and ADR.md
        state_path.write_text('{"current_phase": 2}')
        
        detector = DriftDetector(str(tmp_path))
        result = detector.detect_phase_drift()
        
        assert result.has_drift is True
        # At least 2 artifacts checked for phase 1 and 2
        assert result.checked > 0
        assert any("SAD.md" in i.description for i in result.drift_items)

    def test_detect_spec_drift_finds_missing_frs(self, tmp_path):
        """Verify drift detection when code doesn't cover all SRS FRs."""
        # Create SRS with FR-01 and FR-02
        srs_path = tmp_path / "SRS.md"
        srs_path.write_text("Requirements: FR-01, FR-02")
        
        # Create implementation with only FR-01
        app_file = tmp_path / "app.py"
        app_file.write_text('""" [FR-01] """\ndef main(): pass')
        
        detector = DriftDetector(str(tmp_path))
        result = detector.detect_spec_drift()
        
        assert result.has_drift is True
        assert result.drifted == 1
        assert "FR-02" in result.drift_items[0].location

    def test_detect_sad_drift_finds_missing_files(self, tmp_path):
        """Verify drift detection when SAD points to non-existent files."""
        sad_path = tmp_path / "SAD.md"
        sad_path.write_text("| FR-01 | `missing.py` |")

        detector = DriftDetector(str(tmp_path))
        result = detector.detect_sad_drift()

        assert result.has_drift is True
        assert "missing.py" in result.drift_items[0].description

    def test_detect_sad_drift_no_sad_file(self, tmp_path):
        detector = DriftDetector(str(tmp_path))
        result = detector.detect_sad_drift()
        assert result.has_drift is False
        assert result.score == 1.0

    def test_detect_all_returns_dict(self, tmp_path):
        detector = DriftDetector(str(tmp_path))
        results = detector.detect_all()
        assert "sad" in results
        assert "spec" in results
        assert "phase" in results

    def test_drift_result_to_dict(self, tmp_path):
        from detection.drift_detector import DriftResult
        r = DriftResult(drift_type="phase", has_drift=True, checked=3,
                       drifted=1, score=0.67)
        d = r.to_dict()
        assert d["has_drift"] is True
        assert d["drifted"] == 1

    def test_find_file_found(self, tmp_path):
        (tmp_path / "SRS.md").write_text("test")
        detector = DriftDetector(str(tmp_path))
        found = detector._find_file(["SRS.md", "01-requirements/SRS.md"])
        assert found is not None

    def test_find_file_not_found(self, tmp_path):
        detector = DriftDetector(str(tmp_path))
        assert detector._find_file(["nonexistent.md"]) is None
