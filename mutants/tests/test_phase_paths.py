"""Tests for core/quality_gate/phase_paths.py."""

from core.quality_gate.phase_paths import PHASE_ARTIFACT_PATHS


class TestPhaseArtifactPaths:
    def test_all_8_phases_have_paths(self):
        for phase in range(1, 9):
            assert phase in PHASE_ARTIFACT_PATHS, f"Phase {phase} missing"
            assert isinstance(PHASE_ARTIFACT_PATHS[phase], list)
            assert len(PHASE_ARTIFACT_PATHS[phase]) > 0

    def test_phase_1_has_srs_and_spec_tracking(self):
        paths = PHASE_ARTIFACT_PATHS[1]
        assert any("SRS.md" in p for p in paths)
        assert any("SPEC_TRACKING" in p for p in paths)

    def test_phase_3_has_src_and_tests(self):
        paths = PHASE_ARTIFACT_PATHS[3]
        assert any("src/" in p for p in paths)
        assert any("tests/" in p for p in paths)
