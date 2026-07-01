"""Regression tests for Bug H4 in scripts/generate_verification_report.py.

Bug H4: certification block checked `deferred` before `pass_count < total`,
so a project with both deferred Gate 3 issues AND any Gate 1 FR FAIL was
reported as Conditional PASS — half the FRs failing was silently masked.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest


@pytest.fixture
def module():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    return importlib.import_module("generate_verification_report")


def _setup_project(tmp_path: Path, fr_ids, gate1_map, gate3=None):
    """Write a minimal project layout for generate_verification_report."""
    methodology = tmp_path / ".methodology"
    methodology.mkdir(parents=True, exist_ok=True)
    srs_dir = tmp_path / "01-requirements"
    srs_dir.mkdir(parents=True, exist_ok=True)
    (srs_dir / "SRS.md").write_text("# SRS\n", encoding="utf-8")
    manifest = {
        "schema_version": "1.0",
        "fr_ids": fr_ids,
        "gate_results": {
            "gate1": gate1_map,
            "gate3": gate3 or {},
            "gate2": None,
            "gate4": None,
        },
    }
    (methodology / "quality_manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    return tmp_path


def _read_cert(project_root: Path) -> str:
    report = project_root / "05-verification" / "VERIFICATION_REPORT.md"
    assert report.exists(), f"report not generated: {report}"
    text = report.read_text(encoding="utf-8")
    # The Certification section is between "## Certification" and the next "##"
    start = text.index("## Certification")
    end = text.index("\n## ", start + 1)
    return text[start:end]


class TestCertification:
    def test_partial_pass_with_deferred_returns_fail_not_conditional(
        self, module, tmp_path
    ):
        """Bug H4 regression: 5/10 PASS + deferred issues must report FAIL,
        not Conditional PASS."""
        gate1 = {
            "FR-01": {"quality_complete": True, "score": 100},
            "FR-02": {"score": 50},
            "FR-03": {"score": 50},
            "FR-04": {"quality_complete": True, "score": 100},
            "FR-05": {"quality_complete": True, "score": 100},
        }
        gate3 = {"deferred_issues": ["coverage gap in module X"]}
        project = _setup_project(tmp_path, list(gate1.keys()), gate1, gate3)

        module.generate_verification_report(project)

        cert = _read_cert(project)
        assert "**FAIL**" in cert, (
            f"cert must say FAIL when any FR fails Gate 1: {cert!r}"
        )
        assert "**Conditional PASS**" not in cert, (
            f"cert must NOT say Conditional PASS while any FR fails: {cert!r}"
        )

    def test_all_pass_with_no_deferred_returns_pass(self, module, tmp_path):
        """All FRs PASS, no deferred → PASS."""
        gate1 = {
            "FR-01": {"quality_complete": True, "score": 100},
            "FR-02": {"quality_complete": True, "score": 100},
        }
        project = _setup_project(tmp_path, list(gate1.keys()), gate1)

        module.generate_verification_report(project)

        cert = _read_cert(project)
        assert "**PASS**" in cert
        assert "**FAIL**" not in cert
        assert "**Conditional PASS**" not in cert

    def test_all_pass_with_deferred_returns_conditional_pass(self, module, tmp_path):
        """All FRs PASS, deferred → Conditional PASS."""
        gate1 = {
            "FR-01": {"quality_complete": True, "score": 100},
            "FR-02": {"quality_complete": True, "score": 100},
        }
        gate3 = {"deferred_issues": ["some deferred issue"]}
        project = _setup_project(tmp_path, list(gate1.keys()), gate1, gate3)

        module.generate_verification_report(project)

        cert = _read_cert(project)
        assert "**Conditional PASS**" in cert, (
            f"all-PASS + deferred must be Conditional PASS: {cert!r}"
        )

    def test_no_frs_returns_unknown(self, module, tmp_path):
        """No FRs declared → UNKNOWN."""
        project = _setup_project(tmp_path, [], {})

        module.generate_verification_report(project)

        cert = _read_cert(project)
        assert "**UNKNOWN**" in cert

    def test_fail_without_deferred_returns_fail(self, module, tmp_path):
        """Any FR fails, no deferred → FAIL."""
        gate1 = {
            "FR-01": {"quality_complete": True, "score": 100},
            "FR-02": {"score": 50},
        }
        project = _setup_project(tmp_path, list(gate1.keys()), gate1)

        module.generate_verification_report(project)

        cert = _read_cert(project)
        assert "**FAIL**" in cert
        assert "1/2 FRs PASS" in cert