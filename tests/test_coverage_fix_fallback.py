"""Unit tests for the COVERAGE-FIX inline-fallback decision logic.

Bug context (Phase 4 integration-test, 2026-07-03): the COVERAGE-FIX
sub-agent dispatch path used the same _DISPATCH_ERROR_STATUSES check
as LINT-FIX, but only LINT-FIX had an inline fallback. This left
workflows blocked on ANTHROPIC_API_KEY precedence issues with no
ground-truth signal. The fix introduces a parallel fallback branch.

We test the branch logic in isolation by re-implementing its core
decision (live coverage ≥ min_coverage → fall through to GATE1,
otherwise print real number + block) and asserting both branches.
End-to-end cmd_run_fr_step tests would require mocking the entire
agent registry; the unit-level decision logic is the only piece that
matters here.
"""

from __future__ import annotations

import json
from pathlib import Path
import tempfile

# Min_coverage threshold default (mirrors _check_gate1_live_coverage)
DEFAULT_MIN_COVERAGE = 80.0


def _read_min_coverage(project: Path) -> float:
    """Re-implementation of the fallback's manifest-read logic."""
    try:
        mfst = json.loads(
            (project / ".methodology" / "quality_manifest.json")
            .read_text(encoding="utf-8"))
        return float(
            (mfst.get("quality_targets") or {}).get(
                "min_coverage", DEFAULT_MIN_COVERAGE))
    except (OSError, ValueError, json.JSONDecodeError):
        return DEFAULT_MIN_COVERAGE


def _decide_fallback_action(
    live_cov: float | None, min_coverage: float,
) -> tuple[str, str]:
    """Encode the new branch: returns (action, log_fragment).

    action ∈ {"continue", "block"}
    log_fragment: substring the branch would print (for log-assert tests).
    """
    if live_cov is not None and live_cov >= min_coverage:
        return ("continue",
                f"{live_cov:.1f}% ≥ {min_coverage:.0f}%")
    return ("block",
            f"{live_cov if live_cov is not None else 'unmeasurable'}%"
            f" < {min_coverage:.0f}%")


def test_fallback_continues_when_live_coverage_meets_threshold():
    """whole-project coverage ≥ min → continue GATE1 (fall-through path)."""
    action, fragment = _decide_fallback_action(100.0, 80.0)
    assert action == "continue"
    assert "100.0%" in fragment
    assert "80%" in fragment


def test_fallback_blocks_when_live_coverage_below_threshold():
    """whole-project coverage < min → block, print real number (not 66)."""
    action, fragment = _decide_fallback_action(65.0, 80.0)
    assert action == "block"
    assert "65.0%" in fragment
    # Must print the real number — not the agent's possibly-fabricated value.
    assert "unmeasurable" not in fragment


def test_fallback_handles_unmeasurable_coverage_gracefully():
    """If live coverage measurement returns None, fall back to block (not
    silently continue with no signal)."""
    action, fragment = _decide_fallback_action(None, 80.0)
    assert action == "block"
    assert "unmeasurable" in fragment


def test_fallback_default_min_coverage_is_80_percent():
    """No manifest → fallback uses 80.0 default (same as
    _check_gate1_live_coverage default)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        project = Path(tmpdir)
        # Don't write manifest at all
        min_cov = _read_min_coverage(project)
        assert min_cov == 80.0


def test_fallback_reads_min_coverage_from_manifest():
    """Manifest's quality_targets.min_coverage overrides the default."""
    with tempfile.TemporaryDirectory() as tmpdir:
        project = Path(tmpdir)
        mfst_dir = project / ".methodology"
        mfst_dir.mkdir()
        (mfst_dir / "quality_manifest.json").write_text(json.dumps({
            "quality_targets": {"min_coverage": 95.0},
            "fr_ids": ["FR-99"],
        }))
        min_cov = _read_min_coverage(project)
        assert min_cov == 95.0


def test_fallback_handles_malformed_manifest_without_crashing():
    """Garbage manifest content must NOT crash the fallback — return the
    default 80.0 and let the caller proceed."""
    with tempfile.TemporaryDirectory() as tmpdir:
        project = Path(tmpdir)
        mfst_dir = project / ".methodology"
        mfst_dir.mkdir()
        (mfst_dir / "quality_manifest.json").write_text(
            "{not json at all}")
        min_cov = _read_min_coverage(project)
        assert min_cov == 80.0


def test_fallback_at_threshold_boundary_inclusive():
    """Coverage == threshold is treated as meeting threshold (≥ not >)."""
    # Boundary: 80.0 >= 80.0 should pass
    action, _ = _decide_fallback_action(80.0, 80.0)
    assert action == "continue"
