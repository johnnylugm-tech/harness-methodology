"""Tests for the FR-driven SpecComplianceChecker (scripts/verify_spec_compliance.py).

The checker derives its targets from the project's own SAD.md FR→module
mapping — it must carry zero assumptions about any particular target
project. (Its previous incarnation hardcoded another project's modules —
text_processor.py / retry_handler.py / prosody_manager.py — and
false-positive failed every other project; E2E round 2 HIGH finding.)
"""

from pathlib import Path

from scripts.verify_spec_compliance import SpecComplianceChecker


def _project(tmp_path: Path, sad: str | None, files: dict[str, str] | None = None) -> Path:
    if sad is not None:
        arch = tmp_path / "02-architecture"
        arch.mkdir(parents=True, exist_ok=True)
        (arch / "SAD.md").write_text(sad, encoding="utf-8")
    for rel, content in (files or {}).items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return tmp_path


SAD_TWO_FRS = (
    "# SAD\n\n"
    "| FR | Module |\n|----|--------|\n"
    "| FR-01 | `src/alpha.py` |\n"
    "| FR-02 | `src/beta.py` |\n"
)


class TestFrDrivenCompliance:
    def test_all_frs_implemented_passes(self, tmp_path):
        project = _project(tmp_path, SAD_TWO_FRS, {
            "src/alpha.py": "# [FR-01] alpha implementation\n",
            "src/beta.py": "# [FR-02] beta implementation\n",
        })
        result = SpecComplianceChecker(str(project)).check_all()
        assert result["issues"] == []
        assert len(result["passed"]) == 2

    def test_missing_module_is_an_issue(self, tmp_path):
        project = _project(tmp_path, SAD_TWO_FRS, {
            "src/alpha.py": "# [FR-01]\n",
            # src/beta.py deliberately absent
        })
        result = SpecComplianceChecker(str(project)).check_all()
        assert any("FR-02" in i and "beta.py" in i for i in result["issues"])

    def test_module_without_marker_is_an_issue(self, tmp_path):
        project = _project(tmp_path, SAD_TWO_FRS, {
            "src/alpha.py": "# [FR-01]\n",
            "src/beta.py": "# no traceability marker here\n",
        })
        result = SpecComplianceChecker(str(project)).check_all()
        assert any("FR-02" in i and "[FR-02]" in i for i in result["issues"])

    def test_nested_src_layout_is_found(self, tmp_path):
        """SAD commits to a basename; the file may live under a deeper
        src-layout (the 9feafc0 blindness class)."""
        project = _project(tmp_path, SAD_TWO_FRS, {
            "03-development/src/pkg/alpha.py": "# [FR-01]\n",
            "03-development/src/pkg/beta.py": "# [FR-02]\n",
        })
        result = SpecComplianceChecker(str(project)).check_all()
        assert result["issues"] == []

    def test_missing_sad_fails_closed(self, tmp_path):
        result = SpecComplianceChecker(str(_project(tmp_path, None))).check_all()
        assert result["issues"], "missing SAD.md must be an issue, not a silent pass"
        assert result["passed"] == []

    def test_sad_with_no_fr_mappings_fails_closed(self, tmp_path):
        """Zero mappings is indistinguishable from a parse failure — must not
        report a vacuous 100% pass (same rule as preflight_traceability)."""
        project = _project(tmp_path, "# SAD\n\nNo FR table here.\n")
        result = SpecComplianceChecker(str(project)).check_all()
        assert result["issues"]

    def test_result_shape_is_stable(self, tmp_path):
        """cmd_verify_spec consumes these keys — keep the contract."""
        project = _project(tmp_path, SAD_TWO_FRS, {"src/alpha.py": "# [FR-01]\n"})
        result = SpecComplianceChecker(str(project)).check_all()
        assert set(result) >= {"passed", "issues", "total", "score"}
