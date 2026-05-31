"""Smoke tests for scripts/generate_quality_report.py and generate_release_notes.py."""

import json


class TestGenerateQualityReport:
    """Smoke tests for generate_quality_report()."""

    def test_creates_output_file_with_sections(self, tmp_path):
        """Output file is created with expected top-level sections."""
        from scripts.generate_quality_report import generate_quality_report

        project = tmp_path / "testproj"
        project.mkdir()
        (project / ".methodology").mkdir(parents=True)
        sessi = project / ".sessi-work"
        sessi.mkdir()

        # Minimal quality_manifest.json
        manifest = {
            "schema_version": "1.0",
            "fr_ids": ["FR-01", "FR-02"],
            "gate_results": {
                "gate1": {
                    "FR-01": {"score": 92.0, "quality_complete": True},
                    "FR-02": {"score": 88.0, "quality_complete": True},
                },
                "gate2": {"score": 78.0, "quality_complete": True},
            },
        }
        (project / ".methodology" / "quality_manifest.json").write_text(json.dumps(manifest))

        # Gate 2 result (.sessi-work/gate2_result.json)
        gate_result = {
            "score": 78.0,
            "dimensions": {
                "completeness": {"score": 80, "detail": ""},
                "correctness": {"score": 82, "detail": ""},
                "consistency": {"score": 75, "detail": ""},
            },
            "issues": [
                {"severity": "critical", "desc": "Test issue"},
                {"severity": "high", "desc": "Test high"},
                {"severity": "medium", "desc": "Test medium"},
            ],
        }
        (sessi / "gate2_result.json").write_text(json.dumps(gate_result))

        out_path = generate_quality_report(str(project))

        content = (project / out_path).read_text()
        assert "Quality Report" in content
        assert "12-Dimension Assessment" in content
        assert "Per-FR Gate 1 Summary" in content
        assert "Defect / Issue Summary" in content
        assert "ASPICE Traceability" in content
        # Defect counts
        assert "Critical**: 1" in content
        assert "High**: 1" in content
        assert "Medium**: 1" in content
        # Gate rows present
        assert "FR-01" in content
        assert "FR-02" in content

    def test_empty_project_does_not_crash(self, tmp_path):
        """Calling on a project with no manifest/result files should not raise."""
        from scripts.generate_quality_report import generate_quality_report

        project = tmp_path / "empty"
        project.mkdir()
        out_path = generate_quality_report(str(project))
        assert (project / out_path).exists()

    def test_custom_output_path(self, tmp_path):
        """--output flag writes to the specified path."""
        from scripts.generate_quality_report import generate_quality_report

        project = tmp_path / "proj"
        project.mkdir()
        (project / ".methodology").mkdir(parents=True)
        (project / ".sessi-work").mkdir()
        (project / ".methodology" / "quality_manifest.json").write_text(
            json.dumps({"gate_results": {}}))
        (project / ".sessi-work" / "gate1_result.json").write_text(
            json.dumps({"score": 90}))

        custom = tmp_path / "custom" / "QR.md"
        generate_quality_report(str(project), output_path=str(custom))
        assert custom.exists()
        assert "Quality Report" in custom.read_text()


class TestGenerateReleaseNotes:
    """Smoke tests for generate_release_notes()."""

    def test_creates_output_file_with_sections(self, tmp_path):
        """Output file is created with expected top-level sections."""
        from scripts.generate_release_notes import generate_release_notes

        project = tmp_path / "testproj"
        project.mkdir()
        (project / ".methodology").mkdir(parents=True)

        # Minimal manifest with gate score
        manifest = {
            "gate_results": {
                "gate2": {"score": 78.0, "quality_complete": True},
            },
        }
        (project / ".methodology" / "quality_manifest.json").write_text(json.dumps(manifest))

        # Without a git repo, git log will return empty
        out_path = generate_release_notes(str(project))
        content = (project / out_path).read_text()

        assert "Release Notes" in content
        assert "Quality Score" in content
        assert "Gate 2" in content
        assert "78.0/100" in content
        assert "Known Issues" in content

    def test_empty_project_does_not_crash(self, tmp_path):
        """Calling on a project with no manifest should not raise."""
        from scripts.generate_release_notes import generate_release_notes

        project = tmp_path / "empty"
        project.mkdir()
        out_path = generate_release_notes(str(project))
        assert (project / out_path).exists()

    def test_custom_output_path(self, tmp_path):
        """--output flag writes to the specified path."""
        from scripts.generate_release_notes import generate_release_notes

        project = tmp_path / "proj"
        project.mkdir()
        custom = tmp_path / "custom" / "RN.md"
        generate_release_notes(str(project), output_path=str(custom))
        assert custom.exists()
        assert "Release Notes" in custom.read_text()
