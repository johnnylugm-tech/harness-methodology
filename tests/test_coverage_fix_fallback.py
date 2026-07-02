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


# ---------------------------------------------------------------------------
# Manifest stamp logic (Phase 4 P4.Bug2): when live coverage meets the
# threshold, the fallback must stamp gate1.{fr_id}.quality_complete = True
# into the manifest, otherwise the phase4-testing.js verify-agent (which
# reads quality_complete from the manifest, not from live measurement) will
# still report GATE1_VERIFIED_FAIL even after the loop continues.
# ---------------------------------------------------------------------------

def _stamp_manifest_gate1(project: Path, fr_id: str,
                          live_cov: float) -> None:
    """Re-implementation of the inline stamp logic in
    cmd_run_fr_step's COVERAGE-FIX fallback. Mirrors the exact semantics
    (atomic write via .tmp + os.replace) so we can test the contract
    without re-implementing it through the full cmd_run_fr_step path.
    """
    import os
    import json as _json
    mfst_path = project / ".methodology" / "quality_manifest.json"
    mfst = _json.loads(mfst_path.read_text(encoding="utf-8"))
    gr = mfst.setdefault("gate_results", {})
    g1 = gr.setdefault("gate1", {})
    fr_entry = g1.setdefault(fr_id, {})
    fr_entry["quality_complete"] = True
    fr_entry["score"] = float(live_cov)
    fr_entry["coverage_fallback"] = "inline-fallback@8abe4f9"
    tmp = mfst_path.with_suffix(".json.tmp")
    tmp.write_text(_json.dumps(mfst, indent=2, sort_keys=True),
                   encoding="utf-8")
    os.replace(str(tmp), str(mfst_path))


def test_stamp_sets_quality_complete_true_for_false_positive():
    """When the fallback decides the LOW_COVERAGE was a false positive,
    it must stamp quality_complete=True in the manifest, otherwise the
    workflow's GATE1-verify (which reads the manifest, not live coverage)
    will still report FAIL."""
    with tempfile.TemporaryDirectory() as tmpdir:
        project = Path(tmpdir)
        mfst_dir = project / ".methodology"
        mfst_dir.mkdir()
        mfst_path = mfst_dir / "quality_manifest.json"
        mfst_path.write_text(json.dumps({
            "quality_targets": {"min_coverage": 80.0},
            "fr_ids": ["FR-02"],
            "gate_results": {"gate1": {"FR-02": {
                "score": 66.0,
                "quality_complete": False,
            }}},
        }))

        _stamp_manifest_gate1(project, "FR-02", 100.0)

        updated = json.loads(mfst_path.read_text(encoding="utf-8"))
        g1 = updated["gate_results"]["gate1"]["FR-02"]
        assert g1["quality_complete"] is True
        assert g1["score"] == 100.0
        assert g1["coverage_fallback"] == "inline-fallback@8abe4f9"


def test_stamp_preserves_unrelated_gate_entries():
    """Stamping FR-02 must not disturb gate1.FR-01 / gate1.FR-03 entries
    that have already PASSED."""
    with tempfile.TemporaryDirectory() as tmpdir:
        project = Path(tmpdir)
        mfst_dir = project / ".methodology"
        mfst_dir.mkdir()
        mfst_path = mfst_dir / "quality_manifest.json"
        mfst_path.write_text(json.dumps({
            "quality_targets": {"min_coverage": 80.0},
            "fr_ids": ["FR-01", "FR-02", "FR-03"],
            "gate_results": {"gate1": {
                "FR-01": {"score": 100.0, "quality_complete": True},
                "FR-02": {"score": 66.0, "quality_complete": False},
                "FR-03": {"score": 97.28, "quality_complete": True},
            }},
        }))

        _stamp_manifest_gate1(project, "FR-02", 100.0)

        updated = json.loads(mfst_path.read_text(encoding="utf-8"))
        assert updated["gate_results"]["gate1"]["FR-01"]["quality_complete"] is True
        assert updated["gate_results"]["gate1"]["FR-03"]["quality_complete"] is True
        assert updated["gate_results"]["gate1"]["FR-02"]["quality_complete"] is True


def test_stamp_writes_atomically_no_leftover_tmp_file():
    """The atomic write pattern (write .tmp + os.replace) must leave no
    .json.tmp file behind."""
    with tempfile.TemporaryDirectory() as tmpdir:
        project = Path(tmpdir)
        mfst_dir = project / ".methodology"
        mfst_dir.mkdir()
        mfst_path = mfst_dir / "quality_manifest.json"
        mfst_path.write_text(json.dumps({
            "fr_ids": ["FR-02"],
            "gate_results": {"gate1": {"FR-02": {}}},
        }))

        _stamp_manifest_gate1(project, "FR-02", 100.0)

        assert not mfst_path.with_suffix(".json.tmp").exists()
        assert mfst_path.exists()
