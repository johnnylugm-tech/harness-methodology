"""PR 7: SAD ↔ TEST_SPEC consistency preflight tests.

Confirms that the new `preflight_fr_spec_consistency` method:
  - Reports clean when SAD and TEST_SPEC agree
  - Reports `sad_only` orphans when SAD has FRs not in TEST_SPEC
  - Reports `spec_only` orphans when TEST_SPEC has FRs not in SAD
  - Skips when TEST_SPEC.md is missing (4b covers that case)
  - Blocks at P5+, informational at P3/P4
"""
from pathlib import Path

import pytest


# Playbook §6: dynamic mutation-oracle marker
pytestmark = pytest.mark.mutation_oracle


@pytest.fixture
def fixture_repo(tmp_path: Path) -> Path:
    """Repo with both SAD.md and TEST_SPEC.md, FR sets intentionally
    aligned initially."""
    arch = tmp_path / "02-architecture"
    arch.mkdir()
    (arch / "SAD.md").write_text(
        "# SAD\n\n## FR-01: alpha\n## FR-02: beta\n## FR-03: gamma\n"
    )
    (arch / "TEST_SPEC.md").write_text(
        "# TEST_SPEC\n\n### FR-01\ntest_alpha\n\n### FR-02\ntest_beta\n\n### FR-03\ntest_gamma\n"
    )
    return tmp_path


def _phase_hooks(project: Path, phase: int):
    sys_path = str(Path(__file__).resolve().parent.parent)
    if sys_path not in __import__("sys").path:
        __import__("sys").path.insert(0, sys_path)
    from core.phase_hooks import PhaseHooks
    return PhaseHooks(str(project), phase=phase, enable_kill_switch=False)


def test_clean_when_sad_and_spec_agree(fixture_repo):
    h = _phase_hooks(fixture_repo, phase=5)
    result = h.preflight_fr_spec_consistency()
    assert result["passed"] is True
    assert result["sad_only"] == []
    assert result["spec_only"] == []
    assert result["orphan_count"] == 0


def test_reports_sad_only_orphans(fixture_repo):
    """FR-99 in SAD but not in TEST_SPEC → sad_only=["FR-99"]."""
    (fixture_repo / "02-architecture" / "SAD.md").write_text(
        "# SAD\n\n## FR-01: alpha\n## FR-02: beta\n## FR-99: orphan\n"
    )
    h = _phase_hooks(fixture_repo, phase=5)
    result = h.preflight_fr_spec_consistency()
    assert "FR-99" in result["sad_only"]
    assert result["passed"] is False  # P5+ blocks
    assert result["blocking"] is True


def test_reports_spec_only_orphans(fixture_repo):
    """FR-99 in TEST_SPEC but not in SAD → spec_only=["FR-99"]."""
    (fixture_repo / "02-architecture" / "TEST_SPEC.md").write_text(
        "# TEST_SPEC\n\n### FR-01\nt\n\n### FR-99\nt_orphan\n"
    )
    h = _phase_hooks(fixture_repo, phase=5)
    result = h.preflight_fr_spec_consistency()
    assert "FR-99" in result["spec_only"]
    assert result["passed"] is False


def test_p3_informational_p5_blocking(fixture_repo):
    """Same orphan: P3 passed=True (informational), P5 passed=False (blocking)."""
    (fixture_repo / "02-architecture" / "SAD.md").write_text(
        "# SAD\n\n## FR-01\n## FR-99: orphan\n"
    )
    h3 = _phase_hooks(fixture_repo, phase=3)
    r3 = h3.preflight_fr_spec_consistency()
    assert r3["blocking"] is False
    assert r3["passed"] is True  # informational

    h5 = _phase_hooks(fixture_repo, phase=5)
    r5 = h5.preflight_fr_spec_consistency()
    assert r5["blocking"] is True
    assert r5["passed"] is False


def test_blocks_when_test_spec_missing_but_sad_has_frs(tmp_path):
    """No TEST_SPEC.md with FRs in SAD.md → block at P5+ (PR 13 gap fix)."""
    arch = tmp_path / "02-architecture"
    arch.mkdir()
    (arch / "SAD.md").write_text("# SAD\n\n## FR-01\n")
    h = _phase_hooks(tmp_path, phase=5)
    result = h.preflight_fr_spec_consistency()
    assert result["passed"] is False
    assert result.get("skipped") is False
    assert result.get("missing_spec") is True
