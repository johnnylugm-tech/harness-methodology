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


from cli._shared import _finalize_sentinel_path, _validate_p3_post_gate2_precondition
from core.quality_gate.gate1_evidence import GATE_TIMESTAMPS_FILE


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


class TestAdvancePrechecksMilestoneGate:
    """Regression (2026-07-22, integration-test Phase 3 run): advance-phase's
    own precondition chain (`_advance_prechecks`) verified quality evidence
    (finalize-gate sentinels, Phase Truth) but never checked whether the
    p3-post-gate2 milestone precondition — the same one push-milestone and
    validate-handoff already enforce via `_validate_p3_post_gate2_precondition`
    — was ever satisfied. That let Phase 3 exit to Phase 4 with PUSH
    ③/④/⑤ never having been pushed. Fix wires the existing validator into
    `_advance_prechecks` for completed_phase == 3 only (Phase 4/6 have no
    equivalent milestone type in push-milestone's --type choices)."""

    @staticmethod
    def _seed_bare_p3_project(tmp_path: Path) -> None:
        """Fixture-only setup (no private-seam patching): every
        _advance_prechecks check that runs BEFORE the milestone gate is a
        real no-op on a bare project with an empty manifest —
        _check_gate_score_variance (no decision_logs/GATE_3_*.yaml),
        _check_deferred_fixes_resolved (no deferred_fixes.md),
        _check_ghost_paper_trail (no ghost trail records) — verified by
        reading their source: each returns 0 when its corresponding file/
        record is simply absent. Only the EXIT_GATE_MAP finalize-gate
        sentinel (real file, written below) and the milestone check itself
        need explicit fixture state.
        """
        (tmp_path / ".methodology").mkdir(parents=True, exist_ok=True)
        # Empty manifest → _resolve_fr_ids_from_manifest() and the existing
        # per-FR finalize-sentinel check both resolve to [], isolating the
        # milestone check to gate2_result.json alone (the per-FR sentinel
        # branch is already covered exhaustively by TestP3PostGate2Precondition
        # above).
        (tmp_path / ".methodology" / "quality_manifest.json").write_text("{}", encoding="utf-8")
        # Exit-gate (Gate 2) finalize-gate sentinel — required by the
        # existing EXIT_GATE_MAP check that runs immediately before ours.
        fs = _finalize_sentinel_path(tmp_path, 2, None, phase=3)
        fs.parent.mkdir(parents=True, exist_ok=True)
        fs.write_text("test-finalized\n", encoding="utf-8")

    def test_blocks_when_milestone_not_pushed(self, tmp_path: Path):
        """Core regression case: everything upstream passes, but
        push-milestone --type p3-post-gate2 never ran (no gate2_result.json)
        — advance-phase must BLOCK (rc=12), not silently advance to Phase 4."""
        import cli.phase_cmds as phase_cmds

        self._seed_bare_p3_project(tmp_path)
        rc = phase_cmds._advance_prechecks(tmp_path, 3)
        assert rc == 12

    def test_passes_when_milestone_satisfied(self, tmp_path: Path):
        """Once the milestone precondition is met, our check must not be
        what blocks the run (rc != 12) — proving it doesn't spuriously
        block valid runs. (Full end-to-end rc==0 for the whole remaining
        chain — Phase Truth, submodule guard, STAGE_PASS regen — is already
        covered by the existing TestCmdAdvancePhase fixtures in
        test_handover_generator.py, which this fix required updating.)"""
        import cli.phase_cmds as phase_cmds

        self._seed_bare_p3_project(tmp_path)
        _seed_gate2_pass(tmp_path, composite=92.25)
        rc = phase_cmds._advance_prechecks(tmp_path, 3)
        assert rc != 12, f"milestone precondition satisfied but still blocked: rc={rc}"

    def test_only_applies_to_phase_3(self):
        """Structural guard: the milestone check must be scoped to
        completed_phase == 3 only. Phase 4/6 have no p3-post-gate2-equivalent
        milestone type in push-milestone's --type choices (only p3-mid,
        p3-pre-gate2, p3-post-gate2 exist for P3; P4 only has p4-mid,
        p4-pre-gate3), so generalizing this check to them would invent a
        requirement harness's own design doesn't define."""
        import inspect

        import cli.phase_cmds as phase_cmds

        src = inspect.getsource(phase_cmds._advance_prechecks)
        marker = "_validate_p3_post_gate2_precondition"
        assert marker in src
        call_idx = src.index(marker)
        guard_idx = src.rindex("if completed_phase == 3:", 0, call_idx)
        # No unrelated "if completed_phase == 3" block sits between the
        # guard and the call — i.e. the call is directly gated by it.
        assert src[guard_idx:call_idx].count("if completed_phase") == 1


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


# ════════════════════════════════════════════════════════════════════════
# O1 (2026-07-07): error message + docstring correction
# ════════════════════════════════════════════════════════════════════════


class TestP3ErrorMessageTwoStepGuidance:
    """O1: P3→P4 handoff error message must point to BOTH run-gate and
    finalize-gate (run-gate writes `.flag`, finalize-gate alone is insufficient)."""

    def test_error_message_directs_user_to_run_gate_first(self, tmp_path: Path):
        """Error must mention `run-gate` as the first step."""
        _seed_gate2_pass(tmp_path, composite=92.25)
        errs = _validate_p3_post_gate2_precondition(tmp_path, ["FR-01"])
        joined = " ".join(errs)
        assert "run-gate" in joined
        assert "1." in joined  # step numbering

    def test_error_message_does_not_promote_finalize_gate_alone(self, tmp_path: Path):
        """Error must NOT say "Run `finalize-gate`" alone — that was the misleading
        trap. Both steps must be listed; finalize-gate should not appear as the
        only fix."""
        _seed_gate2_pass(tmp_path, composite=92.25)
        errs = _validate_p3_post_gate2_precondition(tmp_path, ["FR-01"])
        joined = " ".join(errs)
        # Both steps present, run-gate as step 1
        assert "1." in joined
        assert "2." in joined
        assert "run-gate" in joined
        assert "finalize-gate" in joined

    def test_docstring_documents_run_gate_writer(self):
        """Docstring must reference `run-gate` (the actual `.flag` writer), not
        `finalize-gate` (which writes `.finalized`, a different marker)."""
        from cli._shared import _validate_p3_post_gate2_precondition

        doc = _validate_p3_post_gate2_precondition.__doc__ or ""
        assert "run-gate" in doc, "docstring must reference run-gate (the .flag writer)"
        # The misleading line "matches what `finalize-gate`" must be gone.
        assert "matches what `finalize-gate`" not in doc


# ════════════════════════════════════════════════════════════════════════
# O2 (2026-07-07): multi-source Gate 1 evidence
# ════════════════════════════════════════════════════════════════════════


def _seed_fr_finalized(project: Path, fr_id: str) -> None:
    """O2: simulate finalize-gate having written its .finalized marker."""
    (project / ".sessi-work" / "sentinels").mkdir(parents=True, exist_ok=True)
    (project / ".sessi-work" / "sentinels" / f"g1_p3_{fr_id.replace('-', '').lower()}.finalized").write_text(
        "test-finalized\n", encoding="utf-8"
    )


def _seed_gate_timestamp(project: Path, *, phase: int, gate: int, fr_id: str) -> None:
    """O2: simulate gate1_evidence.record_gate_timestamp having appended a row."""
    (project / ".methodology").mkdir(parents=True, exist_ok=True)
    ts_file = project / ".methodology" / GATE_TIMESTAMPS_FILE
    entry = {"phase": phase, "gate": gate, "fr_id": fr_id, "ts": 1700000000.0}
    with open(str(ts_file), "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


class TestP3MultiSourceGate1Evidence:
    """O2: handoff validator accepts any of three co-equal Gate 1 evidence channels
    (.flag, .finalized, gate_timestamps.jsonl) — eliminates single-source-of-evidence
    design defect that blocked P3→P4 after clean restart wiped .sessi-work/."""

    def test_only_finalized_sentinel_passes_handoff(self, tmp_path: Path):
        """Only `.finalized` (no `.flag`, no jsonl) → must PASS handoff."""
        _seed_gate2_pass(tmp_path, composite=92.25)
        _seed_fr_finalized(tmp_path, "FR-01")
        errs = _validate_p3_post_gate2_precondition(tmp_path, ["FR-01"])
        assert errs == [], f"expected no errors, got: {errs}"

    def test_only_gate_timestamps_jsonl_row_passes_handoff(self, tmp_path: Path):
        """Only `gate_timestamps.jsonl` (no sentinel files) → must PASS handoff."""
        _seed_gate2_pass(tmp_path, composite=92.25)
        _seed_gate_timestamp(tmp_path, phase=3, gate=1, fr_id="FR-01")
        errs = _validate_p3_post_gate2_precondition(tmp_path, ["FR-01"])
        assert errs == [], f"expected no errors, got: {errs}"

    def test_none_of_the_three_sources_fails_handoff(self, tmp_path: Path):
        """All three channels empty → must FAIL with the improved 2-step message."""
        _seed_gate2_pass(tmp_path, composite=92.25)
        errs = _validate_p3_post_gate2_precondition(tmp_path, ["FR-01"])
        assert any("FR-01" in e and "run-gate" in e for e in errs)

    def test_gate_timestamps_with_wrong_phase_does_not_satisfy(self, tmp_path: Path):
        """Phase scoping must hold — a phase-5 entry must not satisfy phase-3 gate 1."""
        _seed_gate2_pass(tmp_path, composite=92.25)
        _seed_gate_timestamp(tmp_path, phase=5, gate=1, fr_id="FR-01")
        errs = _validate_p3_post_gate2_precondition(tmp_path, ["FR-01"])
        assert any("FR-01" in e and "sentinel" in e for e in errs), \
            f"phase-5 entry must not satisfy phase-3 precondition; got: {errs}"

    def test_gate_timestamps_fr_id_case_insensitive(self, tmp_path: Path):
        """fr_id normalization (`replace("-", "").lower()`) must match `_sentinel_path`."""
        _seed_gate2_pass(tmp_path, composite=92.25)
        # Mixed-case row, hyphen stripped by caller
        _seed_gate_timestamp(tmp_path, phase=3, gate=1, fr_id="fr-01")
        errs = _validate_p3_post_gate2_precondition(tmp_path, ["FR-01"])
        assert errs == [], f"case-insensitive fr_id should pass; got: {errs}"
