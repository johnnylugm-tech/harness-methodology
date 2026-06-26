"""Regression: push-milestone --type p3-post-gate2 (PUSH ⑤) is the formal
P3-exit checkpoint with structural preconditions.

E2E framework finding (integration-test run, 2026-06-12): the previous
orchestrator called its commit `chore(P3-exit): mark all deferred fixes
completed` with no gate-level verification — the commit label was a claim,
not a check. v2.9.1 B.2 introduces `p3-post-gate2` so the milestone is
*the* way to exit P3, with:

  1. .methodology/gate2_result.json must exist, composite ≥ 75
  2. Per-FR Gate 1 sentinel (.sessi-work/sentinels/g1_<fr>.flag) must
     exist for every FR being claimed

If either precondition fails, push-milestone exits 1 with a clear error
list (mirroring _validate_p8_completion).
"""

from __future__ import annotations

import json
from pathlib import Path


from harness_cli import _validate_p3_post_gate2_precondition


def _seed_gate2_pass(project: Path, composite: float = 92.25) -> None:
    (project / ".methodology").mkdir(parents=True, exist_ok=True)
    (project / ".methodology" / "gate2_result.json").write_text(
        json.dumps({"gate": 2, "composite_score": composite, "phase": 3}),
        encoding="utf-8",
    )


def _seed_fr_sentinel(project: Path, fr_id: str) -> None:
    (project / ".sessi-work" / "sentinels").mkdir(parents=True, exist_ok=True)
    # v2.13: per-phase sentinel path (Bug #121). _validate_p3_post_gate2_precondition
    # now reads g1_p3_*.flag (Phase-3-scoped Gate 1 sentinel).
    (project / ".sessi-work" / "sentinels" / f"g1_p3_{fr_id.replace('-', '').lower()}.flag").write_text(
        "test-sentinel\n", encoding="utf-8"
    )


class TestP3PostGate2Precondition:
    def test_all_satisfied_returns_no_errors(self, tmp_path: Path):
        """Happy path: Gate 2 PASS + all 5 FR sentinels → empty error list."""
        _seed_gate2_pass(tmp_path, composite=92.25)
        for fr in ["FR-01", "FR-02", "FR-03", "FR-04", "FR-05"]:
            _seed_fr_sentinel(tmp_path, fr)

        errs = _validate_p3_post_gate2_precondition(
            tmp_path, ["FR-01", "FR-02", "FR-03", "FR-04", "FR-05"]
        )
        assert errs == []

    def test_missing_gate2_result_blocks(self, tmp_path: Path):
        """No gate2_result.json → block (must run Gate 2 first)."""
        # sentinels all present, but no gate2_result
        for fr in ["FR-01", "FR-02"]:
            _seed_fr_sentinel(tmp_path, fr)
        errs = _validate_p3_post_gate2_precondition(tmp_path, ["FR-01", "FR-02"])
        assert any("gate2_result.json" in e for e in errs)

    def test_low_gate2_composite_blocks(self, tmp_path: Path):
        """Gate 2 composite 60 < 75 → block."""
        _seed_gate2_pass(tmp_path, composite=60.0)
        for fr in ["FR-01"]:
            _seed_fr_sentinel(tmp_path, fr)
        errs = _validate_p3_post_gate2_precondition(tmp_path, ["FR-01"])
        assert any("composite" in e and "60" in e for e in errs)

    def test_missing_fr_sentinel_blocks(self, tmp_path: Path):
        """One FR (FR-05) missing sentinel → block (must run finalize-gate per-FR)."""
        _seed_gate2_pass(tmp_path, composite=92.25)
        for fr in ["FR-01", "FR-02", "FR-03", "FR-04"]:
            _seed_fr_sentinel(tmp_path, fr)
        # FR-05 deliberately missing
        errs = _validate_p3_post_gate2_precondition(
            tmp_path, ["FR-01", "FR-02", "FR-03", "FR-04", "FR-05"]
        )
        assert any("FR-05" in e and "sentinel" in e for e in errs)

    def test_all_fr_sentinels_missing_blocks_with_full_list(self, tmp_path: Path):
        """All FRs missing sentinels → block with all listed."""
        _seed_gate2_pass(tmp_path, composite=92.25)
        errs = _validate_p3_post_gate2_precondition(
            tmp_path, ["FR-01", "FR-02", "FR-03"]
        )
        # All 3 should be in the error
        assert all(fr in " ".join(errs) for fr in ["FR-01", "FR-02", "FR-03"])

    def test_corrupt_gate2_result_blocks(self, tmp_path: Path):
        """Unparseable gate2_result.json → block (do not silently pass)."""
        (project_dir := tmp_path / ".methodology").mkdir(parents=True, exist_ok=True)
        (project_dir / "gate2_result.json").write_text("not json {{{", encoding="utf-8")
        for fr in ["FR-01"]:
            _seed_fr_sentinel(tmp_path, fr)
        errs = _validate_p3_post_gate2_precondition(tmp_path, ["FR-01"])
        assert any("parse" in e.lower() or "json" in e.lower() for e in errs)


class TestP3PostGate2Cli:
    """End-to-end: push-milestone --type p3-post-gate2 is in the argparser."""

    def test_cli_p3_post_gate2_appears_in_help_choices(self):
        """The new milestone type must be in the argparser choices list."""
        from harness_cli import build_parser

        parser = build_parser()
        # If "p3-post-gate2" isn't in choices, parse_args will SystemExit(2)
        args = parser.parse_args(
            ["push-milestone", "--type", "p3-post-gate2", "--project", "/tmp/dummy"]
        )
        assert args.type == "p3-post-gate2"
