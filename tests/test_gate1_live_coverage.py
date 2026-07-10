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


def test_validate_fr_coverage_immediate_parses_total(project_with_fr):
    """pytest output with TOTAL line is parsed correctly."""
    from core.quality_gate.gate1_evidence import validate_fr_coverage_immediate
    fake_stdout = "===== test session starts =====\nTOTAL    10  0  100%\n"
    fake_result = mock.Mock(returncode=0, stdout=fake_stdout)
    with mock.patch("subprocess.run", return_value=fake_result):
        cov = validate_fr_coverage_immediate(project_with_fr)
    assert cov == 100.0


def test_validate_fr_coverage_immediate_no_total_returns_zero_on_success(project_with_fr):
    """No TOTAL line but pytest exit 0 → 0.0 (coverage tool not active)."""
    from core.quality_gate.gate1_evidence import validate_fr_coverage_immediate
    fake_result = mock.Mock(returncode=0, stdout="===== test session starts =====\n")
    with mock.patch("subprocess.run", return_value=fake_result):
        cov = validate_fr_coverage_immediate(project_with_fr)
    assert cov == 0.0


def test_validate_fr_coverage_immediate_test_failure_returns_none(project_with_fr):
    """pytest non-zero exit AND no TOTAL → None (caller BLOCKS)."""
    from core.quality_gate.gate1_evidence import validate_fr_coverage_immediate
    fake_result = mock.Mock(returncode=1, stdout="1 failed\n")
    with mock.patch("subprocess.run", return_value=fake_result):
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
    """subprocess.TimeoutExpired → None (don't block forever)."""
    from core.quality_gate.gate1_evidence import validate_fr_coverage_immediate
    fake_result = mock.Mock(side_effect=__import__("subprocess").TimeoutExpired(
        cmd="pytest", timeout=120,
    ))
    with mock.patch("subprocess.run", fake_result):
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
