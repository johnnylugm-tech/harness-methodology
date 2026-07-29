"""Unit tests for ``_check_gate1_live_coverage`` + ``_validate_fr_coverage_immediate``.

These tests cover the LIVE pytest --cov verification path introduced to
replace the gate_timestamps.jsonl-only check. The previous implementation
trusted a timestamp file written by the agent, which could pass even if no
actual test ran. The new path runs pytest on the whole project and BLOCKS
on real coverage gaps.
"""
import json
from pathlib import Path
from unittest import mock

import pytest


# ---------------------------------------------------------------------------
# Fixtures: a minimal project tree that mirrors the harness layout
# ---------------------------------------------------------------------------


@pytest.fixture
def project_with_fr(tmp_path: Path) -> Path:
    """Create 03-development/src + tests/ + manifest with one FR (FR-07)."""
    src = tmp_path / "03-development" / "src" / "infrastructure"
    tests = tmp_path / "03-development" / "tests"
    src.mkdir(parents=True)
    tests.mkdir(parents=True)
    (src / "audio_converter.py").write_text(
        "def convert(x: str) -> str:\n"
        "    \"\"\"[FR-07] Convert audio.\"\"\"\n"
        "    return x.upper()\n",
        encoding="utf-8",
    )
    (tests / "test_fr07.py").write_text(
        "from infrastructure.audio_converter import convert\n"
        "def test_convert():\n"
        "    assert convert('a') == 'A'\n",
        encoding="utf-8",
    )
    methodology = tmp_path / ".methodology"
    methodology.mkdir()
    (methodology / "quality_manifest.json").write_text(
        json.dumps({
            "fr_ids": ["FR-07"],
            "quality_targets": {"min_coverage": 80.0},
            "gate_results": {"gate1": {"FR-07": {"score": 95.0, "quality_complete": True}}},
        }),
        encoding="utf-8",
    )
    return tmp_path


# ---------------------------------------------------------------------------
# _validate_fr_coverage_immediate
# ---------------------------------------------------------------------------


def _suite(**kwargs):
    """A SuiteResult with sensible defaults (Round 25 shared suite run)."""
    from core.quality_gate.test_suite_run import SuiteResult

    base = dict(
        passed=True, coverage=100.0, test_target="03-development/tests",
        cov_target="03-development/src", returncode=0, output="", ran=True,
    )
    base.update(kwargs)
    return SuiteResult(**base)  # type: ignore[arg-type]


def test_validate_fr_coverage_immediate_returns_the_measured_percentage(project_with_fr):
    """The measured coverage is passed through unchanged."""
    from core.quality_gate.gate1_evidence import validate_fr_coverage_immediate
    with mock.patch("core.quality_gate.test_suite_run.run_suite",
                    return_value=_suite(coverage=100.0)):
        cov = validate_fr_coverage_immediate(project_with_fr)
    assert cov == 100.0


def test_validate_fr_coverage_immediate_no_coverage_returns_zero_on_success(project_with_fr):
    """Green suite but no readable coverage report → 0.0 (coverage tool inactive)."""
    from core.quality_gate.gate1_evidence import validate_fr_coverage_immediate
    with mock.patch("core.quality_gate.test_suite_run.run_suite",
                    return_value=_suite(coverage=None, passed=True)):
        cov = validate_fr_coverage_immediate(project_with_fr)
    assert cov == 0.0


def test_validate_fr_coverage_immediate_test_failure_returns_none(project_with_fr):
    """Red suite with no coverage number → None (caller BLOCKS)."""
    from core.quality_gate.gate1_evidence import validate_fr_coverage_immediate
    with mock.patch("core.quality_gate.test_suite_run.run_suite",
                    return_value=_suite(coverage=None, passed=False, returncode=1,
                                        output="1 failed\n")):
        cov = validate_fr_coverage_immediate(project_with_fr)
    assert cov is None


def test_validate_fr_coverage_immediate_no_src_returns_none(tmp_path):
    """No 03-development/src/ → None (no point running pytest)."""
    from core.quality_gate.gate1_evidence import validate_fr_coverage_immediate
    (tmp_path / "03-development" / "tests").mkdir(parents=True)
    assert validate_fr_coverage_immediate(tmp_path) is None


def test_validate_fr_coverage_immediate_no_tests_returns_none(tmp_path):
    """No 03-development/tests/ → None."""
    from core.quality_gate.gate1_evidence import validate_fr_coverage_immediate
    (tmp_path / "03-development" / "src").mkdir(parents=True)
    assert validate_fr_coverage_immediate(tmp_path) is None


def test_validate_fr_coverage_immediate_timeout_returns_none(project_with_fr):
    """Suite timeout → None (don't block forever, don't invent a 0%)."""
    from core.quality_gate.gate1_evidence import validate_fr_coverage_immediate
    with mock.patch("core.quality_gate.test_suite_run.run_suite",
                    return_value=_suite(coverage=None, passed=False, returncode=124,
                                        reason="test suite timed out after 300s")):
        cov = validate_fr_coverage_immediate(project_with_fr)
    assert cov is None


# ---------------------------------------------------------------------------
# _check_gate1_live_coverage
# ---------------------------------------------------------------------------


def test_check_gate1_live_coverage_passes_at_threshold(project_with_fr):
    """Coverage ≥ min_coverage → return 0."""
    from cli.phase_cmds import _check_gate1_live_coverage
    with mock.patch(
        "core.quality_gate.gate1_evidence.validate_fr_coverage_immediate", return_value=100.0
    ):
        rc = _check_gate1_live_coverage(project_with_fr, completed_phase=3)
    assert rc == 0


def test_check_gate1_live_coverage_blocks_below_threshold(project_with_fr):
    """Coverage < min_coverage → BLOCKED 14."""
    from cli.phase_cmds import _check_gate1_live_coverage
    with mock.patch(
        "core.quality_gate.gate1_evidence.validate_fr_coverage_immediate", return_value=50.0
    ):
        rc = _check_gate1_live_coverage(project_with_fr, completed_phase=3)
    assert rc == 14


def test_check_gate1_live_coverage_blocks_pytest_failure(project_with_fr):
    """pytest errored (None) → BLOCKED 14."""
    from cli.phase_cmds import _check_gate1_live_coverage
    with mock.patch(
        "core.quality_gate.gate1_evidence.validate_fr_coverage_immediate", return_value=None
    ):
        rc = _check_gate1_live_coverage(project_with_fr, completed_phase=3)
    assert rc == 14


def test_check_gate1_live_coverage_delta_auto_skip(tmp_path):
    """P5 + no code changed since last Gate 1 → skip live pytest, return 0."""
    from cli.phase_cmds import _check_gate1_live_coverage
    methodology = tmp_path / ".methodology"
    methodology.mkdir()
    (methodology / "quality_manifest.json").write_text(
        json.dumps({"fr_ids": ["FR-07"]}),
        encoding="utf-8",
    )
    with mock.patch(
        "core.quality_gate.gate1_evidence.fr_code_changed_since_last_gate1", return_value=False
    ):
        with mock.patch("core.quality_gate.gate1_evidence.validate_fr_coverage_immediate") as mock_cov:
            rc = _check_gate1_live_coverage(tmp_path, completed_phase=5)
            mock_cov.assert_not_called()  # live pytest was skipped
    assert rc == 0


def test_check_gate1_live_coverage_no_manifest_returns_zero(tmp_path):
    """Project without quality_manifest → skip (non-FR project)."""
    from cli.phase_cmds import _check_gate1_live_coverage
    rc = _check_gate1_live_coverage(tmp_path, completed_phase=3)
    assert rc == 0


def test_check_gate1_live_coverage_reads_min_coverage_from_manifest(tmp_path):
    """A min_coverage=100 in manifest is respected (not hardcoded 80)."""
    from cli.phase_cmds import _check_gate1_live_coverage
    methodology = tmp_path / ".methodology"
    methodology.mkdir()
    (methodology / "quality_manifest.json").write_text(
        json.dumps({
            "fr_ids": ["FR-07"],
            "quality_targets": {"min_coverage": 100.0},
        }),
        encoding="utf-8",
    )
    # 99.5% < 100% threshold → BLOCKED even though 99.5 > 80 default
    with mock.patch(
        "core.quality_gate.gate1_evidence.validate_fr_coverage_immediate", return_value=99.5
    ):
        rc = _check_gate1_live_coverage(tmp_path, completed_phase=3)
    assert rc == 14
