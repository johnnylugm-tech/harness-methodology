# tests/test_handover_generator.py
# Tests for HandoverGenerator and GitStrategy handover integration.
import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from harness.handover_generator import HandoverGenerator
from harness.git_strategy import GitStrategy


# ── HandoverGenerator unit tests ────────────────────────────────────────────

class TestHandoverGenerator:
    def test_write_creates_handover_md(self, tmp_path: Path):
        gen = HandoverGenerator(tmp_path)
        path = gen.write(
            checkpoint_id="P3-pre-gate2-20260504",
            phase=3,
            task_background="Implementing FR-001..FR-005.",
            current_status="5/5 FRs Gate 1 PASS. Gate 2 not yet run.",
            next_steps=["Run Gate 2 evaluation", "Fix Gate 2 failures"],
        )
        assert path == tmp_path / "HANDOVER.md"
        assert path.exists()

    def test_content_contains_checkpoint_id(self, tmp_path: Path):
        gen = HandoverGenerator(tmp_path)
        gen.write(
            checkpoint_id="P1-exit-20260504",
            phase=1,
            task_background="Some background.",
            current_status="P1 done.",
            next_steps=["Go to P2"],
        )
        content = (tmp_path / "HANDOVER.md").read_text(encoding="utf-8")
        assert "P1-exit-20260504" in content

    def test_content_contains_compact_notice(self, tmp_path: Path):
        gen = HandoverGenerator(tmp_path)
        gen.write(
            checkpoint_id="P2-exit-20260504",
            phase=2,
            task_background="bg",
            current_status="status",
            next_steps=["step 1"],
        )
        content = (tmp_path / "HANDOVER.md").read_text(encoding="utf-8")
        assert "/compact" in content

    def test_default_notes_always_present(self, tmp_path: Path):
        gen = HandoverGenerator(tmp_path)
        gen.write(
            checkpoint_id="P4-gate3-20260504",
            phase=4,
            task_background="bg",
            current_status="status",
            next_steps=["step 1"],
            notes=[],
        )
        content = (tmp_path / "HANDOVER.md").read_text(encoding="utf-8")
        assert "100% follow SKILL.md" in content
        assert ".sessi-work/" in content

    def test_caller_notes_appended_after_defaults(self, tmp_path: Path):
        gen = HandoverGenerator(tmp_path)
        gen.write(
            checkpoint_id="P5-baseline-20260504",
            phase=5,
            task_background="bg",
            current_status="status",
            next_steps=["step 1"],
            notes=["Custom note XYZ"],
        )
        content = (tmp_path / "HANDOVER.md").read_text(encoding="utf-8")
        assert "Custom note XYZ" in content
        assert "100% follow SKILL.md" in content

    def test_extra_renders_as_additional_info(self, tmp_path: Path):
        gen = HandoverGenerator(tmp_path)
        gen.write(
            checkpoint_id="P3-mid-20260504",
            phase=3,
            task_background="bg",
            current_status="status",
            next_steps=["step 1"],
            extra={"fr_done": "6", "fr_total": "12"},
        )
        content = (tmp_path / "HANDOVER.md").read_text(encoding="utf-8")
        assert "附加資訊" in content
        assert "fr_done" in content
        assert "fr_total" in content

    def test_next_steps_numbered(self, tmp_path: Path):
        gen = HandoverGenerator(tmp_path)
        gen.write(
            checkpoint_id="P6-gate4-20260504",
            phase=6,
            task_background="bg",
            current_status="status",
            next_steps=["Alpha step", "Beta step", "Gamma step"],
        )
        content = (tmp_path / "HANDOVER.md").read_text(encoding="utf-8")
        assert "1. Alpha step" in content
        assert "2. Beta step" in content
        assert "3. Gamma step" in content

    def test_unknown_phase_raises_value_error(self, tmp_path: Path):
        """Phase 10 (and any value outside 1-9) is invalid input — the
        write() boundary must reject it with ValueError. (Previously
        the test was asserting silent fallback rendering, which was
        exactly the bug: an invalid phase would render a bash block
        pointing at `.methodology/phase10_plan.md`. Phase 9 became a
        legal phase — Maintenance — so the out-of-range probe is 10.)"""
        gen = HandoverGenerator(tmp_path)
        with pytest.raises(ValueError, match="phase"):
            gen.write(
                checkpoint_id="P10-exit-20260504",
                phase=10,
                task_background="bg",
                current_status="status",
                next_steps=["step 1"],
            )

    def test_phase9_maintenance_is_valid(self, tmp_path: Path):
        """Phase 9 (Maintenance) is a legal steady-state phase — write()
        must accept it and render the maintenance phase name."""
        gen = HandoverGenerator(tmp_path)
        gen.write(
            checkpoint_id="P9-cr-close-20260703",
            phase=9,
            task_background="bg",
            current_status="status",
            next_steps=["step 1"],
        )
        content = (tmp_path / "HANDOVER.md").read_text(encoding="utf-8")
        assert "Maintenance" in content

    def test_write_overwrites_existing(self, tmp_path: Path):
        gen = HandoverGenerator(tmp_path)
        (tmp_path / "HANDOVER.md").write_text("old content", encoding="utf-8")
        gen.write(
            checkpoint_id="P7-exit-20260504",
            phase=7,
            task_background="bg",
            current_status="status",
            next_steps=["step 1"],
        )
        content = (tmp_path / "HANDOVER.md").read_text(encoding="utf-8")
        assert "old content" not in content
        assert "P7-exit-20260504" in content


# ── GitStrategy integration tests ───────────────────────────────────────────

class TestGitStrategyHandover:
    """Verify that each push method writes HANDOVER.md (no real git calls)."""

    def _make_strategy(self, tmp_path: Path) -> GitStrategy:
        gs = GitStrategy(project=tmp_path, enabled=True, push=False)
        # Patch _commit so no real git process runs
        gs._commit = MagicMock(return_value=True)  # type: ignore[method-assign]
        return gs

    def test_p1_writes_handover(self, tmp_path: Path):
        gs = self._make_strategy(tmp_path)
        gs.commit_and_push_p1(fr_ids=["FR-001", "FR-002"])
        assert (tmp_path / "HANDOVER.md").exists()
        assert "P1-exit" in (tmp_path / "HANDOVER.md").read_text()

    def test_p2_writes_handover(self, tmp_path: Path):
        gs = self._make_strategy(tmp_path)
        gs.commit_and_push_p2(fr_ids=["FR-001"])
        assert "P2-exit" in (tmp_path / "HANDOVER.md").read_text()

    def test_p3_mid_writes_handover(self, tmp_path: Path):
        gs = self._make_strategy(tmp_path)
        gs.commit_and_push_p3_mid(fr_done=3, fr_total=6, fr_ids=["FR-001", "FR-002", "FR-003"])
        content = (tmp_path / "HANDOVER.md").read_text()
        assert "P3-mid" in content
        assert "3/6" in content

    def test_p3_pre_gate2_writes_handover(self, tmp_path: Path):
        gs = self._make_strategy(tmp_path)
        gs.commit_and_push_p3_pre_gate2(fr_ids=["FR-001", "FR-002", "FR-003"])
        content = (tmp_path / "HANDOVER.md").read_text()
        assert "P3-pre-gate2" in content
        assert "Gate 2 not yet executed" in content

    def test_gate2_writes_handover(self, tmp_path: Path):
        gs = self._make_strategy(tmp_path)
        gs.commit_and_push_gate(gate_num=2, phase=3, score=76.5, n_frs=5)
        content = (tmp_path / "HANDOVER.md").read_text()
        assert "P3-gate2" in content
        assert "76.5" in content

    def test_gate3_writes_handover(self, tmp_path: Path):
        gs = self._make_strategy(tmp_path)
        gs.commit_and_push_gate(gate_num=3, phase=4, score=82.0)
        content = (tmp_path / "HANDOVER.md").read_text()
        assert "P4-gate3" in content

    def test_gate4_writes_handover(self, tmp_path: Path):
        gs = self._make_strategy(tmp_path)
        with patch.object(gs, "_tag_release"):
            gs.commit_and_push_gate(gate_num=4, phase=6, score=87.0)
        content = (tmp_path / "HANDOVER.md").read_text()
        assert "P6-gate4" in content

    def test_p5_baseline_writes_handover(self, tmp_path: Path):
        gs = self._make_strategy(tmp_path)
        gs.commit_and_push_p5_baseline()
        assert "P5-exit" in (tmp_path / "HANDOVER.md").read_text()

    def test_p7_writes_handover(self, tmp_path: Path):
        gs = self._make_strategy(tmp_path)
        gs.commit_and_push_p7()
        assert "P7-exit" in (tmp_path / "HANDOVER.md").read_text()

    def test_p8_writes_handover(self, tmp_path: Path):
        gs = self._make_strategy(tmp_path)
        gs.commit_and_push_p8()
        assert "P8-exit" in (tmp_path / "HANDOVER.md").read_text()

    def test_disabled_strategy_skips_handover(self, tmp_path: Path):
        gs = GitStrategy(project=tmp_path, enabled=False)
        gs.commit_and_push_p1(fr_ids=["FR-001"])
        assert not (tmp_path / "HANDOVER.md").exists()

    def test_deprecated_final_routes_to_p8(self, tmp_path: Path):
        gs = self._make_strategy(tmp_path)
        gs.commit_and_push_final(phases=[7, 8])
        assert "P8-exit" in (tmp_path / "HANDOVER.md").read_text()

    def test_deprecated_final_routes_to_p7(self, tmp_path: Path):
        gs = self._make_strategy(tmp_path)
        gs.commit_and_push_final(phases=[7])
        assert "P7-exit" in (tmp_path / "HANDOVER.md").read_text()


class TestGitStrategyGitOps:
    """Tests that hit the actual git subprocess paths (_commit, _run_git, etc.)."""

    def test_has_changes_true(self, tmp_path: Path):
        gs = GitStrategy(project=tmp_path, enabled=True, push=False)
        with patch.object(gs, "_run_git") as mock_git:
            mock_git.return_value = MagicMock(stdout="M file.py", returncode=0)
            assert gs._has_changes() is True
            mock_git.assert_called_once_with("status", "--porcelain")

    def test_has_changes_false(self, tmp_path: Path):
        gs = GitStrategy(project=tmp_path, enabled=True, push=False)
        with patch.object(gs, "_run_git") as mock_git:
            mock_git.return_value = MagicMock(stdout="", returncode=0)
            assert gs._has_changes() is False

    def test_commit_no_changes(self, tmp_path: Path):
        gs = GitStrategy(project=tmp_path, enabled=True, push=False)
        with patch.object(gs, "_has_changes", return_value=False):
            assert gs._commit("msg") is True

    def test_commit_add_fails(self, tmp_path: Path):
        gs = GitStrategy(project=tmp_path, enabled=True, push=False)
        with patch.object(gs, "_has_changes", return_value=True), \
             patch.object(gs, "_run_git") as mock_git:
            mock_git.return_value = MagicMock(returncode=1, stderr="add failed")
            assert gs._commit("msg") is False

    def test_commit_commit_fails(self, tmp_path: Path):
        gs = GitStrategy(project=tmp_path, enabled=True, push=False)
        with patch.object(gs, "_has_changes", return_value=True), \
             patch.object(gs, "_run_git") as mock_git:
            mock_git.side_effect = [
                MagicMock(returncode=0),  # git add OK
                MagicMock(returncode=1, stderr="commit failed"),  # git commit FAIL
            ]
            assert gs._commit("msg") is False

    def test_commit_success(self, tmp_path: Path):
        gs = GitStrategy(project=tmp_path, enabled=True, push=False)
        with patch.object(gs, "_has_changes", return_value=True), \
             patch.object(gs, "_run_git") as mock_git:
            mock_git.side_effect = [
                MagicMock(returncode=0),  # git add OK
                MagicMock(returncode=0),  # git commit OK
                MagicMock(stdout="abc1234\n", returncode=0),  # git rev-parse
            ]
            assert gs._commit("feat: something") is True

    def test_commit_and_push_no_push(self, tmp_path: Path):
        gs = GitStrategy(project=tmp_path, enabled=True, push=False)
        with patch.object(gs, "_commit", return_value=True):
            assert gs._commit_and_push("msg") is True

    def test_commit_and_push_commit_fails(self, tmp_path: Path):
        gs = GitStrategy(project=tmp_path, enabled=True, push=True)
        with patch.object(gs, "_commit", return_value=False):
            assert gs._commit_and_push("msg") is False

    def test_commit_and_push_push_fails(self, tmp_path: Path):
        gs = GitStrategy(project=tmp_path, enabled=True, push=True)
        with patch.object(gs, "_commit", return_value=True), \
             patch.object(gs, "_run_git") as mock_git:
            mock_git.return_value = MagicMock(returncode=1, stderr="push rejected")
            assert gs._commit_and_push("msg") is False

    def test_commit_and_push_success(self, tmp_path: Path):
        gs = GitStrategy(project=tmp_path, enabled=True, push=True)
        with patch.object(gs, "_commit", return_value=True), \
             patch.object(gs, "_run_git") as mock_git:
            mock_git.return_value = MagicMock(returncode=0)
            assert gs._commit_and_push("msg") is True

    def test_tag_release_success(self, tmp_path: Path):
        gs = GitStrategy(project=tmp_path, enabled=True, push=True)
        with patch.object(gs, "_run_git") as mock_git:
            mock_git.return_value = MagicMock(returncode=0)
            gs._tag_release(87.5)
            assert mock_git.call_count >= 1

    def test_tag_release_fails(self, tmp_path: Path):
        gs = GitStrategy(project=tmp_path, enabled=True, push=True)
        with patch.object(gs, "_run_git") as mock_git:
            mock_git.return_value = MagicMock(returncode=1, stderr="tag exists")
            gs._tag_release(85.0)  # should not raise

    def test_run_git_exception(self, tmp_path: Path):
        gs = GitStrategy(project=tmp_path, enabled=True, push=True)
        with patch("subprocess.run", side_effect=OSError("git not found")):
            result = gs._run_git("status")
            assert result.returncode == 1
            assert "git not found" in result.stderr

    def test_commit_fr_gate1_fr_progress_exception(self, tmp_path: Path):
        gs = GitStrategy(project=tmp_path, enabled=True, push=False)
        with patch.object(gs, "_commit", return_value=True), \
             patch("harness.git_strategy.FRProgressTracker") as mock_tracker:
            mock_tracker.return_value.record_gate1_pass.side_effect = ValueError("boom")
            result = gs.commit_fr_gate1("FR-001", 75.0, 3)
            assert result is True  # still succeeds despite tracker error

    def test_ensure_gitignore_adds_entries(self, tmp_path: Path):
        gs = GitStrategy(project=tmp_path, enabled=True, push=False)
        gs.ensure_gitignore()
        gi = tmp_path / ".gitignore"
        assert gi.exists()
        content = gi.read_text()
        assert ".sessi-work/" in content

    def test_ensure_gitignore_no_duplicates(self, tmp_path: Path):
        gi = tmp_path / ".gitignore"
        gi.write_text(".sessi-work/\n")
        gs = GitStrategy(project=tmp_path, enabled=True, push=False)
        gs.ensure_gitignore()
        assert gi.read_text().count(".sessi-work/") == 1


# ── HandoverGenerator fix tests ─────────────────────────────────────────────

class TestHandoverGeneratorFixes:
    """Verify the HandoverGenerator enrichment fixes."""

    def _make_strategy(self, tmp_path: Path) -> "GitStrategy":
        gs = GitStrategy(project=tmp_path, enabled=True, push=False)
        gs._commit = MagicMock(return_value=True)  # type: ignore[method-assign]
        return gs

    # ── _state_snapshot ──────────────────────────────────────────────────────

    def test_state_snapshot_no_checkpoint_field(self, tmp_path: Path):
        """state.json has no 'checkpoint' key — must not show 'checkpoint=?'."""
        (tmp_path / ".methodology").mkdir()
        (tmp_path / ".methodology" / "state.json").write_text(
            '{"current_phase": 1, "state": "ACTIVE", "last_gate": null, "last_fr": null}',
            encoding="utf-8",
        )
        gen = HandoverGenerator(tmp_path)
        snapshot = gen._state_snapshot()
        assert "checkpoint=" not in snapshot, "Must not emit checkpoint=? for missing key"
        assert "phase=1" in snapshot
        assert "state=ACTIVE" in snapshot

    def test_state_snapshot_includes_last_gate_when_set(self, tmp_path: Path):
        """last_gate appears in snapshot only when non-null."""
        (tmp_path / ".methodology").mkdir()
        (tmp_path / ".methodology" / "state.json").write_text(
            '{"current_phase": 3, "state": "RUNNING", "last_gate": 1, "last_fr": "FR-05"}',
            encoding="utf-8",
        )
        gen = HandoverGenerator(tmp_path)
        snapshot = gen._state_snapshot()
        assert "last_gate=1" in snapshot
        assert "last_fr=FR-05" in snapshot

    def test_state_snapshot_omits_null_last_gate(self, tmp_path: Path):
        """Null last_gate must not appear in snapshot."""
        (tmp_path / ".methodology").mkdir()
        (tmp_path / ".methodology" / "state.json").write_text(
            '{"current_phase": 1, "state": "ACTIVE", "last_gate": null, "last_fr": null}',
            encoding="utf-8",
        )
        gen = HandoverGenerator(tmp_path)
        snapshot = gen._state_snapshot()
        assert "last_gate" not in snapshot

    # ── plan_override ─────────────────────────────────────────────────────────

    def test_plan_override_appears_in_handover(self, tmp_path: Path):
        """plan_override replaces default phase{N}_plan.md in quick-resume."""
        gen = HandoverGenerator(tmp_path)
        gen.write(
            checkpoint_id="P1-exit-test",
            phase=1,
            task_background="bg",
            current_status="status",
            next_steps=["step 1"],
            plan_override=".methodology/phase2_plan.md",
        )
        content = (tmp_path / "HANDOVER.md").read_text()
        assert ".methodology/phase2_plan.md" in content
        assert ".methodology/phase1_plan.md" not in content

    def test_no_plan_override_uses_target_phase(self, tmp_path: Path):
        """Without plan_override, defaults to target phase plan (phase+1 when no resume_phase)."""
        gen = HandoverGenerator(tmp_path)
        gen.write(
            checkpoint_id="P3-test",
            phase=3,
            task_background="bg",
            current_status="status",
            next_steps=["step 1"],
        )
        content = (tmp_path / "HANDOVER.md").read_text()
        assert ".methodology/phase4_plan.md" in content
        assert "start Phase 4" in content

    # ── SHA removed, git log added ────────────────────────────────────────────

    def test_no_last_sha_row_in_table(self, tmp_path: Path):
        """SHA row must not appear (it was stale pre-commit SHA)."""
        gen = HandoverGenerator(tmp_path)
        gen.write(
            checkpoint_id="P1-test",
            phase=1,
            task_background="bg",
            current_status="status",
            next_steps=["step 1"],
        )
        content = (tmp_path / "HANDOVER.md").read_text()
        assert "Last SHA" not in content

    def test_git_log_command_in_quick_resume(self, tmp_path: Path):
        """git log --oneline -3 must appear in quick-resume block."""
        gen = HandoverGenerator(tmp_path)
        gen.write(
            checkpoint_id="P1-test",
            phase=1,
            task_background="bg",
            current_status="status",
            next_steps=["step 1"],
        )
        content = (tmp_path / "HANDOVER.md").read_text()
        assert "git log --oneline -3" in content

    # ── GitStrategy auto-detection helpers ───────────────────────────────────

    def test_auto_fr_ids_from_srs_root(self, tmp_path: Path):
        """_auto_fr_ids() parses ### FR-XX: at repo root."""
        (tmp_path / "01-requirements").mkdir(parents=True, exist_ok=True)
        (tmp_path / "01-requirements" / "SRS.md").write_text(
            "### FR-01: Title One\n### FR-02: Title Two\n### FR-13: Title Thirteen\n",
            encoding="utf-8",
        )
        gs = GitStrategy(tmp_path, enabled=False)
        ids = gs._auto_fr_ids()
        assert ids == ["FR-01", "FR-02", "FR-13"]

    def test_auto_fr_ids_no_srs(self, tmp_path: Path):
        """Returns empty list when SRS.md is absent."""
        gs = GitStrategy(tmp_path, enabled=False)
        assert gs._auto_fr_ids() == []

    def test_auto_fr_ids_deduplication(self, tmp_path: Path):
        """Duplicate FR-IDs in SRS.md are deduplicated."""
        (tmp_path / "01-requirements").mkdir(parents=True, exist_ok=True)
        (tmp_path / "01-requirements" / "SRS.md").write_text(
            "### FR-01: Title\n### FR-01: Duplicate\n### FR-02: Other\n",
            encoding="utf-8",
        )
        gs = GitStrategy(tmp_path, enabled=False)
        ids = gs._auto_fr_ids()
        assert ids.count("FR-01") == 1

    def test_ab_session_summary_parses_log(self, tmp_path: Path):
        """_ab_session_summary() returns markdown from sessions_spawn.log."""
        (tmp_path / ".methodology").mkdir()
        (tmp_path / ".methodology" / "sessions_spawn.log").write_text(
            '{"sub_task":"SRS.md","role":"requirements_engineer","status":"success","confidence":9}\n'
            '{"sub_task":"SRS.md","role":"business_analyst","review_status":"APPROVE"}\n',
            encoding="utf-8",
        )
        gs = GitStrategy(tmp_path, enabled=False)
        summary = gs._ab_session_summary()
        assert "SRS.md" in summary
        assert "APPROVE" in summary

    def test_ab_session_summary_round2_labeled(self, tmp_path: Path):
        """Round-2 entries show r2 label in summary."""
        (tmp_path / ".methodology").mkdir()
        (tmp_path / ".methodology" / "sessions_spawn.log").write_text(
            '{"sub_task":"SPEC_TRACKING.md","role":"business_analyst","review_status":"APPROVE","round":2}\n',
            encoding="utf-8",
        )
        gs = GitStrategy(tmp_path, enabled=False)
        summary = gs._ab_session_summary()
        assert "r2" in summary

    def test_ab_session_summary_missing_log(self, tmp_path: Path):
        """Returns empty string when sessions_spawn.log absent."""
        gs = GitStrategy(tmp_path, enabled=False)
        assert gs._ab_session_summary() == ""

    def test_gap_register_summary_finds_gaps(self, tmp_path: Path):
        """_gap_register_summary() extracts GAP-XX and M-GAP-XX entries."""
        (tmp_path / "01-requirements").mkdir(parents=True, exist_ok=True)
        (tmp_path / "01-requirements" / "SPEC_TRACKING.md").write_text(
            "| GAP-01 | B-1/4 | some gap |\n"
            "| GAP-02 | B-1/4 | other |\n"
            "| M-GAP-01 | B-2/4 | medium |\n",
            encoding="utf-8",
        )
        gs = GitStrategy(tmp_path, enabled=False)
        summary = gs._gap_register_summary()
        assert "GAP-01" in summary
        assert "3 gap(s)" in summary

    def test_gap_register_summary_highlights_medium(self, tmp_path: Path):
        """Medium-priority gaps (M-GAP-XX) are highlighted."""
        (tmp_path / "01-requirements").mkdir(parents=True, exist_ok=True)
        (tmp_path / "01-requirements" / "SPEC_TRACKING.md").write_text(
            "| GAP-01 | low |\n| M-GAP-01 | medium |\n",
            encoding="utf-8",
        )
        gs = GitStrategy(tmp_path, enabled=False)
        summary = gs._gap_register_summary()
        assert "medium-priority" in summary
        assert "M-GAP-01" in summary

    def test_gap_register_summary_no_file(self, tmp_path: Path):
        """Returns empty string when SPEC_TRACKING.md absent."""
        gs = GitStrategy(tmp_path, enabled=False)
        assert gs._gap_register_summary() == ""

    def test_next_phase_plan_exists_true(self, tmp_path: Path):
        """Returns True when next-phase plan file exists."""
        (tmp_path / ".methodology").mkdir()
        (tmp_path / ".methodology" / "phase2_plan.md").write_text("# P2", encoding="utf-8")
        gs = GitStrategy(tmp_path, enabled=False)
        assert gs._next_phase_plan_exists(1) is True

    def test_next_phase_plan_exists_false(self, tmp_path: Path):
        """Returns False when next-phase plan file absent."""
        gs = GitStrategy(tmp_path, enabled=False)
        assert gs._next_phase_plan_exists(1) is False

    # ── P1 enriched HANDOVER content ─────────────────────────────────────────

    def test_p1_handover_auto_detects_frs(self, tmp_path: Path):
        """commit_and_push_p1 with empty fr_ids auto-detects from SRS.md."""
        (tmp_path / "01-requirements").mkdir(parents=True, exist_ok=True)
        (tmp_path / "01-requirements" / "SRS.md").write_text(
            "### FR-01: Platform Adapter\n### FR-02: Signature Verify\n",
            encoding="utf-8",
        )
        gs = self._make_strategy(tmp_path)
        gs.commit_and_push_p1(fr_ids=[])
        content = (tmp_path / "HANDOVER.md").read_text()
        assert "FR-01" in content
        assert "2 FR(s)" in content

    def test_p1_handover_includes_ab_summary(self, tmp_path: Path):
        """P1 HANDOVER contains A/B session results when log exists."""
        (tmp_path / ".methodology").mkdir()
        (tmp_path / ".methodology" / "sessions_spawn.log").write_text(
            '{"sub_task":"SRS.md","role":"business_analyst","review_status":"APPROVE"}\n',
            encoding="utf-8",
        )
        gs = self._make_strategy(tmp_path)
        gs.commit_and_push_p1(fr_ids=["FR-01"])
        content = (tmp_path / "HANDOVER.md").read_text()
        assert "A/B Session Results" in content
        assert "APPROVE" in content

    def test_p1_handover_uses_phase2_plan_when_exists(self, tmp_path: Path):
        """P1 HANDOVER next-steps reference phase2_plan.md when it already exists."""
        (tmp_path / ".methodology").mkdir()
        (tmp_path / ".methodology" / "phase2_plan.md").write_text("# P2", encoding="utf-8")
        gs = self._make_strategy(tmp_path)
        gs.commit_and_push_p1(fr_ids=["FR-01"])
        content = (tmp_path / "HANDOVER.md").read_text()
        assert "phase2_plan.md" in content

    def test_p1_handover_no_hermes_env(self, tmp_path: Path):
        """P1 HANDOVER must not reference HERMES_REVIEWER_TARGET (backend removed)."""
        gs = self._make_strategy(tmp_path)
        gs.commit_and_push_p1(fr_ids=["FR-01"])
        content = (tmp_path / "HANDOVER.md").read_text()
        assert "HERMES_REVIEWER_TARGET" not in content

    def test_p1_handover_includes_gap_register(self, tmp_path: Path):
        """P1 HANDOVER includes gap register summary when SPEC_TRACKING.md exists."""
        (tmp_path / "01-requirements").mkdir(parents=True, exist_ok=True)
        (tmp_path / "01-requirements" / "SPEC_TRACKING.md").write_text(
            "| GAP-01 | B-1/4 | low |\n| M-GAP-01 | B-2/4 | medium |\n",
            encoding="utf-8",
        )
        gs = self._make_strategy(tmp_path)
        gs.commit_and_push_p1(fr_ids=["FR-01"])
        content = (tmp_path / "HANDOVER.md").read_text()
        assert "Review Gaps" in content
        assert "GAP-01" in content

    # ── New improvements (feedback) ───────────────────────────────────────────

    def test_handover_contains_recurse_submodules(self, tmp_path: Path):
        """Clone command in HANDOVER.md must include --recurse-submodules."""
        gs = self._make_strategy(tmp_path)
        gs.commit_and_push_p1(fr_ids=["FR-01"])
        content = (tmp_path / "HANDOVER.md").read_text()
        assert "--recurse-submodules" in content

    def test_handover_contains_startup_section(self, tmp_path: Path):
        """HANDOVER.md must include '▶ 立即開始' three-step section."""
        gs = self._make_strategy(tmp_path)
        gs.commit_and_push_p1(fr_ids=["FR-01"])
        content = (tmp_path / "HANDOVER.md").read_text()
        assert "▶ 立即開始" in content

    def test_deliverable_files_p1_all_exist(self, tmp_path: Path):
        """_deliverable_files(1) returns ✅ for all 3 P1 files when present."""
        (tmp_path / "01-requirements").mkdir(parents=True, exist_ok=True)
        for name in ["01-requirements/SRS.md",
                      "01-requirements/SPEC_TRACKING.md", "01-requirements/TRACEABILITY_MATRIX.md"]:
            (tmp_path / name).write_text("# content\n" * 10, encoding="utf-8")
        gs = GitStrategy(tmp_path, enabled=False)
        items = gs._deliverable_files(1)
        assert len(items) == 3
        assert all("✅" in item for item in items)
        assert any("10L" in item or "L)" in item for item in items)  # line count included

    def test_deliverable_files_p1_missing(self, tmp_path: Path):
        """_deliverable_files(1) marks absent files as ❌ missing."""
        (tmp_path / "01-requirements").mkdir(parents=True, exist_ok=True)
        (tmp_path / "01-requirements" / "SRS.md").write_text("# SRS\n", encoding="utf-8")
        # SPEC_TRACKING.md, TRACEABILITY_MATRIX.md absent
        gs = GitStrategy(tmp_path, enabled=False)
        items = gs._deliverable_files(1)
        assert len(items) == 3
        assert any("✅" in item and "SRS.md" in item for item in items)
        assert sum("❌ missing" in item for item in items) == 2

    def test_deliverable_files_p2(self, tmp_path: Path):
        """_deliverable_files(2) covers SAD.md only."""
        (tmp_path / "02-architecture").mkdir(parents=True, exist_ok=True)
        (tmp_path / "02-architecture" / "SAD.md").write_text("# SAD\n", encoding="utf-8")
        gs = GitStrategy(tmp_path, enabled=False)
        items = gs._deliverable_files(2)
        assert len(items) == 1
        assert any("SAD.md" in item and "✅" in item for item in items)

    def test_deliverable_files_unknown_phase(self, tmp_path: Path):
        """_deliverable_files() returns [] for phases without a hardcoded list."""
        gs = GitStrategy(tmp_path, enabled=False)
        assert gs._deliverable_files(5) == []

    def test_p1_handover_contains_deliverable_section(self, tmp_path: Path):
        """P1 HANDOVER.md includes '交付物清單' section listing deliverables."""
        (tmp_path / "01-requirements").mkdir(parents=True, exist_ok=True)
        for name in ["01-requirements/SRS.md",
                      "01-requirements/SPEC_TRACKING.md", "01-requirements/TRACEABILITY_MATRIX.md"]:
            (tmp_path / name).write_text("# content\n", encoding="utf-8")
        gs = self._make_strategy(tmp_path)
        gs.commit_and_push_p1(fr_ids=["FR-01"])
        content = (tmp_path / "HANDOVER.md").read_text()
        assert "交付物清單" in content
        assert "SRS.md" in content

    def test_gap_register_rich_table_with_disposition(self, tmp_path: Path):
        """_gap_register_summary() returns a markdown table with disposition column."""
        (tmp_path / "01-requirements").mkdir(parents=True, exist_ok=True)
        (tmp_path / "01-requirements" / "SPEC_TRACKING.md").write_text(
            "| GAP-01 | B-1/4 | NFR-04 | security_logs fix | P3 |\n"
            "| M-GAP-01 | B-2/4 | Cost model | Clarify budget | P2 |\n",
            encoding="utf-8",
        )
        gs = GitStrategy(tmp_path, enabled=False)
        summary = gs._gap_register_summary()
        assert "GAP-01" in summary
        assert "security_logs fix" in summary   # disposition column
        assert "P3" in summary                  # target column
        assert "⚠️" in summary                  # medium-priority flag on M-GAP-01
        assert "| Gap ID |" in summary          # markdown table header

    def test_recently_committed_files_deduplicates(self, tmp_path: Path):
        """_recently_committed_files() returns deduplicated file list (no repeated entries)."""
        gs = GitStrategy(tmp_path, enabled=False)
        # When git is unavailable, returns [] — just verify no crash
        files = gs._recently_committed_files()
        assert isinstance(files, list)
        assert len(files) == len(set(files))   # no duplicates

    def test_startup_section_no_hermes_export(self, tmp_path: Path):
        """▶ 立即開始 startup block must not contain a Hermes env export line."""
        gs = self._make_strategy(tmp_path)
        gs.commit_and_push_p1(fr_ids=["FR-01"])
        content = (tmp_path / "HANDOVER.md").read_text()
        assert "HERMES_REVIEWER_TARGET" not in content

    def test_deliverables_section_has_blank_line_before_status(self, tmp_path: Path):
        """交付物清單 section must be followed by a blank line before ## 目前執行狀況."""
        (tmp_path / "01-requirements").mkdir(parents=True, exist_ok=True)
        for name in ["01-requirements/SRS.md",
                      "01-requirements/SPEC_TRACKING.md", "01-requirements/TRACEABILITY_MATRIX.md"]:
            (tmp_path / name).write_text("# x\n", encoding="utf-8")
        gs = self._make_strategy(tmp_path)
        gs.commit_and_push_p1(fr_ids=["FR-01"])
        content = (tmp_path / "HANDOVER.md").read_text()
        # There must be a blank line (double newline) between last list item and next heading
        assert "\n\n## 目前執行狀況" in content


# ─── resume_phase ──────────────────────────────────────────────────────────

class TestResumePhase:
    def test_default_resumes_phase_plus_one(self, tmp_path: Path):
        """Without resume_phase, resume section says start Phase N+1."""
        gen = HandoverGenerator(tmp_path)
        content = gen._render(
            checkpoint_id="P3-exit-20260513",
            phase=3,
            task_background="test",
            current_status="test",
            next_steps=["Proceed to P4"],
            notes=[],
            extra={},
        )
        assert "start Phase 4" in content
        assert "continue Phase" not in content
        assert "Follow SKILL.md §0.1 Phase 4 entry check" in content

    def test_resume_phase_same_as_phase_continues(self, tmp_path: Path):
        """resume_phase == phase says continue Phase N."""
        gen = HandoverGenerator(tmp_path)
        content = gen._render(
            checkpoint_id="P3-mid-20260513",
            phase=3,
            task_background="test",
            current_status="test",
            next_steps=["Complete remaining FRs"],
            notes=[],
            extra={},
            resume_phase=3,
        )
        assert "continue Phase 3" in content
        assert "start Phase" not in content
        assert "Follow the active plan and continue from where you left off" in content

    def test_resume_phase_explicit_next_is_not_same_as_phase(self, tmp_path: Path):
        """resume_phase explicitly set to phase+1 behaves like default."""
        gen = HandoverGenerator(tmp_path)
        content = gen._render(
            checkpoint_id="P3-exit-20260513",
            phase=3,
            task_background="test",
            current_status="test",
            next_steps=["Proceed to P4"],
            notes=[],
            extra={},
            resume_phase=4,
        )
        assert "start Phase 4" in content


# ─── cmd_push_milestone ────────────────────────────────────────────────────

class TestCmdPushMilestone:
    """Tests for harness_cli.py cmd_push_milestone branches."""

    @staticmethod
    def _call_push_milestone(monkeypatch, tmp_path, milestone_type, **kwargs):
        """Call cmd_push_milestone with given args and return (exit_code, printed)."""
        import io
        from harness_cli import cmd_push_milestone

        # Build a fake args namespace
        class Args:
            pass
        a = Args()
        a.type = milestone_type  # type: ignore[reportAttributeAccessIssue]
        a.project = str(tmp_path)  # type: ignore[reportAttributeAccessIssue]
        a.fr_ids = kwargs.get("fr_ids", "")  # type: ignore[reportAttributeAccessIssue]
        a.fr_done = kwargs.get("fr_done")  # type: ignore[reportAttributeAccessIssue]
        a.fr_total = kwargs.get("fr_total")  # type: ignore[reportAttributeAccessIssue]
        a.no_git = True  # type: ignore[reportAttributeAccessIssue]

        # Disable actual git operations
        captured = io.StringIO()
        monkeypatch.setattr("sys.stdout", captured)
        monkeypatch.setattr("sys.exit", lambda code: code)
        monkeypatch.setattr("harness_cli._make_git", lambda args, project: GitStrategy(project, enabled=False))
        try:
            exit_code = cmd_push_milestone(a)  # type: ignore[reportArgumentType]
        except SystemExit as e:
            exit_code = e.code
        return exit_code, captured.getvalue()

    def test_p3_mid_missing_fr_done_total(self, tmp_path, monkeypatch):
        exit_code, output = self._call_push_milestone(monkeypatch, tmp_path, "p3-mid")
        assert exit_code == 1
        assert "--fr-done" in output

    def test_p3_mid_with_ids(self, tmp_path, monkeypatch):
        exit_code, _ = self._call_push_milestone(
            monkeypatch, tmp_path, "p3-mid",
            fr_ids="FR-01,FR-02",
            fr_done=2, fr_total=4,
        )
        assert exit_code == 0

    def test_p3_pre_gate2(self, tmp_path, monkeypatch):
        exit_code, _ = self._call_push_milestone(
            monkeypatch, tmp_path, "p3-pre-gate2",
            fr_ids="FR-01,FR-02,FR-03",
        )
        assert exit_code == 0

    @staticmethod
    def _write_gate_evidence(tmp_path, gate_num: int) -> None:
        """p5-baseline/p7/p8 milestones are entry-gated (E2E C-1/C-2 fix):
        the manifest must carry a passing record for the required gate."""
        meth = tmp_path / ".methodology"
        meth.mkdir(parents=True, exist_ok=True)
        (meth / "quality_manifest.json").write_text(
            json.dumps({"fr_ids": [],
                        "gate_results": {f"gate{gate_num}": {"quality_complete": True}}}),
            encoding="utf-8",
        )

    def test_p5_baseline(self, tmp_path, monkeypatch):
        self._write_gate_evidence(tmp_path, 3)
        exit_code, _ = self._call_push_milestone(monkeypatch, tmp_path, "p5-baseline")
        assert exit_code == 0

    def test_p5_baseline_without_gate3_evidence_blocks(self, tmp_path, monkeypatch):
        """C-1 regression probe at the unit level: no Gate 3 record → BLOCKED
        before any commit_and_push call."""
        exit_code, output = self._call_push_milestone(monkeypatch, tmp_path, "p5-baseline")
        assert exit_code == 2
        assert "Gate 3" in output

    def test_p7(self, tmp_path, monkeypatch):
        self._write_gate_evidence(tmp_path, 4)
        exit_code, _ = self._call_push_milestone(monkeypatch, tmp_path, "p7")
        assert exit_code == 0

    def test_p8(self, tmp_path, monkeypatch):
        self._write_gate_evidence(tmp_path, 4)
        # P8 pre-flight requires .methodology-archive/ with methodology content.
        # `cp -r .methodology/ .methodology-archive/` copies plan files to the root
        # of the archive dir (no "methodology/" subdir), so seed it that way.
        archive = tmp_path / ".methodology-archive"
        archive.mkdir()
        (archive / "phase8_plan.md").write_text("# P8 plan\n", encoding="utf-8")
        exit_code, _ = self._call_push_milestone(monkeypatch, tmp_path, "p8")
        assert exit_code == 0

    def test_p4_mid_missing_fr_done_total(self, tmp_path, monkeypatch):
        exit_code, output = self._call_push_milestone(monkeypatch, tmp_path, "p4-mid")
        assert exit_code == 1
        assert "--fr-done" in output

    def test_p4_mid_with_ids(self, tmp_path, monkeypatch):
        exit_code, _ = self._call_push_milestone(
            monkeypatch, tmp_path, "p4-mid",
            fr_ids="FR-01,FR-02",
            fr_done=2, fr_total=4,
        )
        assert exit_code == 0

    def test_p4_pre_gate3(self, tmp_path, monkeypatch):
        exit_code, _ = self._call_push_milestone(
            monkeypatch, tmp_path, "p4-pre-gate3",
            fr_ids="FR-01,FR-02,FR-03",
        )
        assert exit_code == 0

    def test_unknown_type(self, tmp_path, monkeypatch):
        exit_code, output = self._call_push_milestone(monkeypatch, tmp_path, "unknown")
        assert exit_code == 1
        assert "Unknown milestone type" in output


# ─── cmd_advance_phase ──────────────────────────────────────────────────────

class TestCmdAdvancePhase:
    """Tests for cmd_advance_phase HANDOVER regeneration and git operations."""

    @staticmethod
    def _call_advance_phase(monkeypatch, tmp_path, completed=3,
                             skip_prechecks=True, mock_auditor=True, **kwargs):
        """Call cmd_advance_phase and return (exit_code, output_str).

        Sets up mock _advance_fsm and HandoverGenerator by default.
        When skip_prechecks is True (default for git-behavior tests),
        _advance_prechecks is also mocked to 0 since the tmp_path env
        lacks the real project structure those checks require.

        When skip_prechecks is False, PhaseTruthVerifier is still mocked
        (requires sessions_spawn.log + FrameworkEnforcer data that no
        tmp_path test sets up), but plan audit / deliverable / gate
        variance checks run for real against the test's .methodology/ files.
        """
        import io
        from harness_cli import cmd_advance_phase

        class _Args:
            def __init__(self):
                self.completed_phase = None
                self.project = None

        if skip_prechecks:
            monkeypatch.setattr(
                "harness_cli._advance_prechecks", lambda project, phase: 0,
            )
        else:
            # Create finalize-gate sentinels so the sentinel check passes
            import harness_cli as _hc
            _hc._write_finalize_sentinels_for_tests(tmp_path, phase=completed)
            # PhaseTruthVerifier needs sessions_spawn.log + real project
            # structure — mock it since no tmp_path test provides those.
            class _FakeVer:
                def __init__(self, project_root, phase): pass
                def verify(self): return {"passed": True, "total_score": 100.0}
            monkeypatch.setattr(
                "core.quality_gate.phase_truth_verifier.PhaseTruthVerifier",
                _FakeVer,
            )
            # Phase Auditor and Agent B approval check need real project
            # structure — mock both for tmp_path tests that test advance-phase
            # specific behaviors.
            # Set mock_auditor=False for tests that specifically test C1/C11/agent-B.
            if mock_auditor:
                monkeypatch.setattr(
                    "harness_cli._run_phase_auditor", lambda project, phase: 0,
                )
                monkeypatch.setattr(
                    "harness_cli._verify_agent_b_approvals_core",
                    lambda project, phase, ids: (True, "mocked"),
                )
                # Also mock constitution postflight (new in _advance_prechecks)
                from core.quality_gate.constitution.runner import ConstitutionResult
                _vacuous = ConstitutionResult(score=100.0, passed=True, violations=[])
                monkeypatch.setattr(
                    "core.quality_gate.constitution.run_constitution_check",
                    lambda *a, **kw: _vacuous,
                )
                monkeypatch.setattr(
                    "core.quality_gate.constitution.profile.get_profile",
                    lambda: type("_P", (), {"composite_threshold": lambda s, p: 75.0})(),
                )
        a = _Args()
        a.completed_phase = completed  # type: ignore[reportAttributeAccessIssue]
        a.project = str(tmp_path)  # type: ignore[reportAttributeAccessIssue]

        captured = io.StringIO()
        monkeypatch.setattr("sys.stdout", captured)
        monkeypatch.setattr("harness_cli._advance_fsm", lambda project, phase, **kw: None)
        monkeypatch.setattr(
            "harness.handover_generator.HandoverGenerator.write",
            lambda self, **kw: tmp_path / "HANDOVER.md",
        )

        # Allow per-test subprocess.run override
        if "subprocess_run" in kwargs:
            monkeypatch.setattr("harness_cli.subprocess.run", kwargs["subprocess_run"])

        # Allow per-test HARNESS_NO_GIT override
        if "harness_no_git" in kwargs:
            monkeypatch.setenv("HARNESS_NO_GIT", kwargs["harness_no_git"])

        try:
            exit_code = cmd_advance_phase(a)  # type: ignore[reportArgumentType]
        except SystemExit as e:
            exit_code = e.code
        return exit_code, captured.getvalue()

    # ── HARNESS_NO_GIT skip ──────────────────────────────────────────────────

    def test_harness_no_git_skips_commit(self, tmp_path, monkeypatch):
        """HARNESS_NO_GIT=1 skips git add/commit entirely."""
        exit_code, output = self._call_advance_phase(
            monkeypatch, tmp_path, harness_no_git="1",
        )
        assert exit_code == 0
        assert "HARNESS_NO_GIT=1" in output
        assert "skip" in output.lower()

    # ── Happy path: add succeeds, commit succeeds ────────────────────────────

    def test_git_add_and_commit_succeed(self, tmp_path, monkeypatch):
        """git add + commit both return 0 → committed message printed."""
        def fake_run(cmd, **kw):
            class R:
                returncode = 0
                stdout = ""
                stderr = ""
            return R()

        exit_code, output = self._call_advance_phase(
            monkeypatch, tmp_path, subprocess_run=fake_run,
        )
        assert exit_code == 0
        assert "Committed HANDOVER.md" in output

    # ── git add failure ──────────────────────────────────────────────────────

    def test_git_add_fails_surfaces_warning(self, tmp_path, monkeypatch):
        """git add non-zero → warning printed, commit skipped."""
        def fake_run(cmd, **kw):
            class R:
                pass
            r = R()
            if "add" in cmd:
                r.returncode = 1  # type: ignore[reportAttributeAccessIssue]
                r.stderr = "fatal: not a git repository"  # type: ignore[reportAttributeAccessIssue]
                r.stdout = ""  # type: ignore[reportAttributeAccessIssue]
            else:
                r.returncode = 0  # type: ignore[reportAttributeAccessIssue]
                r.stdout = ""  # type: ignore[reportAttributeAccessIssue]
                r.stderr = ""  # type: ignore[reportAttributeAccessIssue]
            return r

        exit_code, output = self._call_advance_phase(
            monkeypatch, tmp_path, subprocess_run=fake_run,
        )
        assert exit_code == 0
        assert "WARN: git add failed" in output
        assert "not a git repository" in output
        assert "Committed" not in output

    # ── git commit "nothing to commit" ───────────────────────────────────────

    def test_git_commit_nothing_to_commit(self, tmp_path, monkeypatch):
        """git commit exits 1 with 'nothing to commit' → not treated as error."""
        def fake_run(cmd, **kw):
            class R:
                pass
            r = R()
            if "commit" in cmd:
                r.returncode = 1  # type: ignore[reportAttributeAccessIssue]
                r.stdout = "nothing to commit, working tree clean"  # type: ignore[reportAttributeAccessIssue]
                r.stderr = ""  # type: ignore[reportAttributeAccessIssue]
            else:
                r.returncode = 0  # type: ignore[reportAttributeAccessIssue]
                r.stdout = ""  # type: ignore[reportAttributeAccessIssue]
                r.stderr = ""  # type: ignore[reportAttributeAccessIssue]
            return r

        exit_code, output = self._call_advance_phase(
            monkeypatch, tmp_path, subprocess_run=fake_run,
        )
        assert exit_code == 0
        assert "Nothing to commit" in output
        assert "WARN" not in output

    def test_git_commit_nothing_to_commit_in_stderr(self, tmp_path, monkeypatch):
        """git commit exits 1 with 'nothing to commit' in stderr."""
        def fake_run(cmd, **kw):
            class R:
                pass
            r = R()
            if "commit" in cmd:
                r.returncode = 1  # type: ignore[reportAttributeAccessIssue]
                r.stdout = ""  # type: ignore[reportAttributeAccessIssue]
                r.stderr = "error: nothing to commit"  # type: ignore[reportAttributeAccessIssue]
            else:
                r.returncode = 0  # type: ignore[reportAttributeAccessIssue]
                r.stdout = ""  # type: ignore[reportAttributeAccessIssue]
                r.stderr = ""  # type: ignore[reportAttributeAccessIssue]
            return r

        exit_code, output = self._call_advance_phase(
            monkeypatch, tmp_path, subprocess_run=fake_run,
        )
        assert exit_code == 0
        assert "Nothing to commit" in output

    # ── git commit failure ───────────────────────────────────────────────────

    def test_git_commit_fails_surfaces_warning(self, tmp_path, monkeypatch):
        """git commit non-zero (not nothing-to-commit) → warning printed."""
        def fake_run(cmd, **kw):
            class R:
                pass
            r = R()
            if "commit" in cmd:
                r.returncode = 128  # type: ignore[reportAttributeAccessIssue]
                r.stdout = ""  # type: ignore[reportAttributeAccessIssue]
                r.stderr = "fatal: unable to create commit"  # type: ignore[reportAttributeAccessIssue]
            else:
                r.returncode = 0  # type: ignore[reportAttributeAccessIssue]
                r.stdout = ""  # type: ignore[reportAttributeAccessIssue]
                r.stderr = ""  # type: ignore[reportAttributeAccessIssue]
            return r

        exit_code, output = self._call_advance_phase(
            monkeypatch, tmp_path, subprocess_run=fake_run,
        )
        assert exit_code == 0
        assert "WARN: git commit failed" in output
        assert "unable to create commit" in output

    # ── Phase number correctness ─────────────────────────────────────────────

    def test_phase3_advances_to_phase4(self, tmp_path, monkeypatch):
        """completed=3 → next_phase=4 in output and HANDOVER checkpoint."""
        def fake_run(cmd, **kw):
            class R:
                returncode = 0
                stdout = ""
                stderr = ""
            return R()

        exit_code, output = self._call_advance_phase(
            monkeypatch, tmp_path, completed=3, subprocess_run=fake_run,
        )
        assert exit_code == 0
        assert "advancing to 4" in output
        assert "target phase 4" in output

    def test_advance_phase_enriches_with_manifest_data(self, tmp_path, monkeypatch):
        """HANDOVER enrichment: manifest gate scores + FR counts flow into output."""
        import io
        import json
        from harness_cli import cmd_advance_phase

        manifest_dir = tmp_path / ".methodology"
        manifest_dir.mkdir()
        manifest = {
            "fr_ids": ["FR-01", "FR-02", "FR-03"],
            "gate_results": {
                "gate1": {
                    "FR-01": {"quality_complete": True, "score": 95.0},
                    "FR-02": {"quality_complete": True, "score": 90.0},
                    "FR-03": {"quality_complete": True, "score": 88.0},
                },
                "gate2": {"quality_complete": True, "score": 96.5},
            },
        }
        (manifest_dir / "quality_manifest.json").write_text(json.dumps(manifest))

        write_kwargs = {}

        def fake_write(self, **kw):
            write_kwargs.update(kw)
            return tmp_path / "HANDOVER.md"

        def fake_run(cmd, **kw):
            class R:
                returncode = 0
                stdout = ""
                stderr = ""
            return R()

        class Args:
            pass
        a = Args()
        a.completed_phase = 3  # type: ignore[reportAttributeAccessIssue]
        a.project = str(tmp_path)  # type: ignore[reportAttributeAccessIssue]

        captured = io.StringIO()
        monkeypatch.setattr("sys.stdout", captured)
        monkeypatch.setattr("harness_cli._advance_fsm", lambda project, phase, **kw: None)
        monkeypatch.setattr("harness_cli.subprocess.run", fake_run)
        monkeypatch.setattr("harness.handover_generator.HandoverGenerator.write", fake_write)
        monkeypatch.setattr("harness_cli._advance_prechecks", lambda project, phase: 0)

        exit_code = cmd_advance_phase(a)  # type: ignore[reportArgumentType]
        assert exit_code == 0
        assert write_kwargs.get("checkpoint_id", "").startswith("P4-entry")
        assert "3/3 FRs" in write_kwargs.get("task_background", "")
        assert "Gate 2" in write_kwargs.get("task_background", "")
        assert "3/3 FRs" in write_kwargs.get("current_status", "")
        assert "Gate 2" in write_kwargs.get("current_status", "")

    def test_advance_phase_handles_null_gate1_gracefully(self, tmp_path, monkeypatch):
        """manifest with gate1: null should not crash fr_done comprehension (Bug #1)."""
        import io
        import json
        from harness_cli import cmd_advance_phase

        manifest_dir = tmp_path / ".methodology"
        manifest_dir.mkdir()
        manifest = {
            "fr_ids": ["FR-01", "FR-02"],
            "gate_results": {
                "gate1": None,
                "gate2": {"quality_complete": True, "score": 80.0},
            },
        }
        (manifest_dir / "quality_manifest.json").write_text(json.dumps(manifest))
        write_kwargs = {}

        def fake_write(self, **kw):
            write_kwargs.update(kw)
            return tmp_path / "HANDOVER.md"

        def fake_run(cmd, **kw):
            class R:
                returncode = 0
                stdout = ""
                stderr = ""
            return R()

        class Args:
            pass
        a = Args()
        a.completed_phase = 3  # type: ignore[reportAttributeAccessIssue]
        a.project = str(tmp_path)  # type: ignore[reportAttributeAccessIssue]

        captured = io.StringIO()
        monkeypatch.setattr("sys.stdout", captured)
        monkeypatch.setattr("harness_cli._advance_fsm", lambda project, phase, **kw: None)
        monkeypatch.setattr("harness_cli.subprocess.run", fake_run)
        monkeypatch.setattr("harness.handover_generator.HandoverGenerator.write", fake_write)
        monkeypatch.setattr("harness_cli._advance_prechecks", lambda project, phase: 0)

        exit_code = cmd_advance_phase(a)  # type: ignore[reportArgumentType]
        assert exit_code == 0
        # gate1 is null → fr_done should be 0, no AttributeError raised
        assert "0/2 FRs" in write_kwargs.get("task_background", "")
        assert "Gate 2" in write_kwargs.get("task_background", "")


    def test_p1_missing_deliverable_blocks_advance(self, tmp_path, monkeypatch):
        """P1 missing required deliverables → blocked with exit 8 via C1."""
        (tmp_path / ".methodology").mkdir()

        def _fake_run(cmd, **kw):
            class R:
                returncode = 0
                stdout = ""
                stderr = ""
            return R()

        exit_code, _ = self._call_advance_phase(
            monkeypatch, tmp_path, completed=1, skip_prechecks=False,
            mock_auditor=False, subprocess_run=_fake_run,
        )
        assert exit_code == 8

    def test_p1_all_deliverables_passes(self, tmp_path, monkeypatch):
        """P1 with all required deliverables passes advance (auditor mocked; flow test only).

        _run_phase_auditor now runs full C1-C12 for all phases including P1.
        C2-C12 behavior at P1 is covered by test_phase_auditor.py; here we only
        verify that advance-phase returns 0 when the auditor itself passes.
        """
        method_dir = tmp_path / ".methodology"
        method_dir.mkdir(parents=True)
        (tmp_path / "01-requirements").mkdir()
        for f in ["SRS.md", "SPEC_TRACKING.md", "TRACEABILITY_MATRIX.md"]:
            (tmp_path / "01-requirements" / f).write_text("FR-01 content")
        (method_dir / "sessions_spawn.log").write_text("{}")
        (tmp_path / "TEST_INVENTORY.yaml").write_text("tests: []")

        def _fake_run(cmd, **kw):
            class R:
                returncode = 0
                stdout = ""
                stderr = ""
            return R()

        exit_code, _ = self._call_advance_phase(
            monkeypatch, tmp_path, completed=1, skip_prechecks=False,
            mock_auditor=True, subprocess_run=_fake_run,
        )
        assert exit_code == 0

    # ── deliverable existence block ─────────────────────────────────────────

    def test_missing_deliverable_blocks_advance(self, tmp_path, monkeypatch):
        """P4: missing TEST_RESULTS.md → blocked with exit 8."""
        method_dir = tmp_path / ".methodology"
        method_dir.mkdir(parents=True)
        # Make TEST_PLAN.md exist but not TEST_RESULTS.md
        (tmp_path / "04-testing").mkdir(parents=True)
        (tmp_path / "04-testing" / "TEST_PLAN.md").write_text("Plan\n")
        # Also need state.json so finalize doesn't crash on advance
        import json
        (method_dir / "state.json").write_text(json.dumps({
            "state": "ACTIVE", "current_phase": 4, "phase_truth_passed": True, "last_gate": 3,
        }))
        (method_dir / "quality_manifest.json").write_text(json.dumps({
            "fr_ids": [], "gate_results": {"gate1": {}, "gate3": {}},
        }))
        # next-phase plan required by _advance_prechecks (phase >= 3)
        (method_dir / "phase5_plan.md").touch()

        exit_code, output = self._call_advance_phase(
            monkeypatch, tmp_path, completed=4, skip_prechecks=False,
            mock_auditor=False,
        )
        assert exit_code == 8
        assert "TEST_RESULTS.md" in output

    # ── deliverable git-tracking ─────────────────────────────────────────

    def test_deliverable_untracked_blocks_advance(self, tmp_path, monkeypatch):
        """File on disk but not git-tracked → blocked with exit 8."""
        import json as _json

        # Set up P4 project with TEST_RESULTS.md on disk but NOT committed
        method_dir = tmp_path / ".methodology"
        method_dir.mkdir(parents=True)
        (tmp_path / "04-testing").mkdir(parents=True)
        (tmp_path / "04-testing" / "TEST_PLAN.md").write_text("Plan\n")
        (tmp_path / "04-testing" / "TEST_RESULTS.md").write_text("Results\n")

        (method_dir / "state.json").write_text(_json.dumps({
            "state": "ACTIVE", "current_phase": 4, "phase_truth_passed": True, "last_gate": 3,
        }))
        (method_dir / "quality_manifest.json").write_text(_json.dumps({
            "fr_ids": [], "gate_results": {"gate1": {}, "gate3": {}},
        }))
        # next-phase plan required by _advance_prechecks (phase >= 3)
        (method_dir / "phase5_plan.md").touch()

        # git ls-files --error-unmatch returns non-zero for untracked files
        def fake_run(cmd, **kw):
            class R:
                pass
            # LocalFetcher calls ["git", "-C", root, "ls-files", "--error-unmatch", path]
            if "ls-files" in cmd and "--error-unmatch" in cmd:
                r = R()
                r.returncode = 1  # file not tracked  # type: ignore[reportAttributeAccessIssue]
                return r
            r = R()
            r.returncode = 0  # type: ignore[reportAttributeAccessIssue]
            r.stdout = ""  # type: ignore[reportAttributeAccessIssue]
            r.stderr = ""  # type: ignore[reportAttributeAccessIssue]
            return r

        exit_code, output = self._call_advance_phase(
            monkeypatch, tmp_path, completed=4, skip_prechecks=False,
            mock_auditor=False, subprocess_run=fake_run,
        )
        assert exit_code == 8
        assert "git-tracked" in output.lower() or "git" in output.lower()

    def test_deliverable_tracked_passes(self, tmp_path, monkeypatch):
        """File on disk AND git-tracked → passes deliverable check."""
        import json as _json

        method_dir = tmp_path / ".methodology"
        method_dir.mkdir(parents=True)
        (tmp_path / "04-testing").mkdir(parents=True)
        (tmp_path / "04-testing" / "TEST_PLAN.md").write_text("Plan\n")
        (tmp_path / "04-testing" / "TEST_RESULTS.md").write_text("Results\n")

        (method_dir / "state.json").write_text(_json.dumps({
            "state": "ACTIVE", "current_phase": 4, "phase_truth_passed": True, "last_gate": 3,
        }))
        (method_dir / "quality_manifest.json").write_text(_json.dumps({
            "fr_ids": [], "gate_results": {"gate1": {}, "gate3": {}},
        }))
        # next-phase plan required by _advance_prechecks (phase >= 3)
        (method_dir / "phase5_plan.md").touch()

        # git ls-files --error-unmatch returns 0 for tracked files
        def fake_run(cmd, **kw):
            class R:
                returncode = 0
                stdout = ""
                stderr = ""
            return R()

        exit_code, output = self._call_advance_phase(
            monkeypatch, tmp_path, completed=4, skip_prechecks=False,
            subprocess_run=fake_run,
        )
        assert exit_code == 0

    # ── gate score variance ─────────────────────────────────────────────

    def test_gate_score_variance_identical_blocks(self, tmp_path, monkeypatch):
        """All FR gate scores identical (>2 entries) → blocked with exit 1."""
        import json as _json
        import yaml as _yaml

        method_dir = tmp_path / ".methodology"
        method_dir.mkdir(parents=True)
        (method_dir / "state.json").write_text(_json.dumps({
            "state": "ACTIVE", "current_phase": 3, "phase_truth_passed": True, "last_gate": 2,
        }))
        (method_dir / "quality_manifest.json").write_text(_json.dumps({
            "fr_ids": ["FR-01", "FR-02", "FR-03"],
            "gate_results": {"gate1": {"FR-01": True, "FR-02": True, "FR-03": True}},
        }))

        # Write 3 identical gate score files
        decision_dir = method_dir / "decision_logs"
        decision_dir.mkdir(parents=True)
        for fr in ("FR-01", "FR-02", "FR-03"):
            lf = decision_dir / f"GATE_3_{fr}.yaml"
            lf.write_text(_yaml.dump({
                "ctx": {"fr_id": fr},
                "scores": {"gate_score": 85.0},
            }))

        # P3 deliverables: src + tests dirs now required by PhaseArtifactRegistry
        (tmp_path / "03-development").mkdir()
        (tmp_path / "03-development" / "src").mkdir()
        (tmp_path / "03-development" / "tests").mkdir()
        # Gate 1 per-FR live pytest needs a real test file per manifest FR; the
        # fake _fake_run below returns 100% coverage so the stub files are
        # enough to satisfy _fr_test_file() (file-exists check).
        for fr in ["FR-01", "FR-02", "FR-03"]:
            num = fr.split("-")[1].zfill(2)
            (tmp_path / "03-development" / "tests" / f"test_fr{num}.py").write_text(
                f"# stub for {fr}\n", encoding="utf-8"
            )

        def _fake_run(cmd, **kw):
            class R:
                returncode = 0
                # Gate 1 per-FR live pytest needs TOTAL coverage in stdout
                stdout = "===== test session starts =====\nTOTAL    10  0  100%\n"
                stderr = ""
            return R()

        exit_code, output = self._call_advance_phase(
            monkeypatch, tmp_path, completed=3, skip_prechecks=False,
            subprocess_run=_fake_run,
        )
        assert exit_code == 1
        assert "variance" in output.lower() or "identical" in output.lower()

    def test_gate_score_variance_ok(self, tmp_path, monkeypatch):
        """Different gate scores across FRs → variance check passes."""
        import json as _json
        import yaml as _yaml

        method_dir = tmp_path / ".methodology"
        method_dir.mkdir(parents=True)
        (method_dir / "state.json").write_text(_json.dumps({
            "state": "ACTIVE", "current_phase": 3, "phase_truth_passed": True, "last_gate": 2,
        }))
        (method_dir / "quality_manifest.json").write_text(_json.dumps({
            "fr_ids": ["FR-01", "FR-02", "FR-03"],
            "gate_results": {"gate1": {"FR-01": True, "FR-02": True, "FR-03": True}},
        }))

        decision_dir = method_dir / "decision_logs"
        decision_dir.mkdir(parents=True)
        for fr, score in [("FR-01", 82.0), ("FR-02", 88.0), ("FR-03", 85.0)]:
            lf = decision_dir / f"GATE_3_{fr}.yaml"
            lf.write_text(_yaml.dump({
                "ctx": {"fr_id": fr},
                "scores": {"gate_score": score},
            }))

        # Gate 1 per-FR coverage: gate_timestamps.jsonl must have one entry per FR
        ts_lines = [
            _json.dumps({"phase": 3, "gate": 1, "fr_id": fr, "ts": 1.0})
            for fr in ["FR-01", "FR-02", "FR-03"]
        ]
        (method_dir / "gate_timestamps.jsonl").write_text("\n".join(ts_lines) + "\n")
        # next-phase plan required by _advance_prechecks (phase >= 3)
        (method_dir / "phase4_plan.md").touch()

        # P3 deliverables: src + tests dirs now required by PhaseArtifactRegistry
        (tmp_path / "03-development").mkdir()
        (tmp_path / "03-development" / "src").mkdir()
        (tmp_path / "03-development" / "tests").mkdir()
        # Gate 1 per-FR live pytest needs a real test + source file per manifest
        # FR; the fake _fake_run below returns 100% coverage so stubs suffice.
        for fr in ["FR-01", "FR-02", "FR-03"]:
            num = fr.split("-")[1].zfill(2)
            (tmp_path / "03-development" / "tests" / f"test_fr{num}.py").write_text(
                f"# stub for {fr}\n", encoding="utf-8"
            )
            (tmp_path / "03-development" / "src" / f"mod_{num}.py").write_text(
                f"\"\"\"[{fr}] stub module.\"\"\"\n", encoding="utf-8"
            )

        def _fake_run(cmd, **kw):
            class R:
                returncode = 0
                # Gate 1 per-FR live pytest needs TOTAL coverage in stdout
                stdout = "===== test session starts =====\nTOTAL    10  0  100%\n"
                stderr = ""
            return R()

        exit_code, output = self._call_advance_phase(
            monkeypatch, tmp_path, completed=3, skip_prechecks=False,
            subprocess_run=_fake_run,
        )
        assert exit_code == 0
        assert "variance ok" in output.lower()

    def test_gate_score_variance_saturated_allows(self, tmp_path, monkeypatch):
        """Near-ceiling scores (mean >= 99.5) with stddev < 0.5 are NOT blocked.

        When every FR is at-or-near the ceiling, per-FR variance is bounded
        by the distance to the ceiling, so a low stddev is a legitimate
        outcome of a clean codebase — not fabrication. Mirrors the gate-3
        dimension-variance `_saturated` exemption (mean >= 99.5).
        """
        import json as _json
        import yaml as _yaml

        method_dir = tmp_path / ".methodology"
        method_dir.mkdir(parents=True)
        (method_dir / "state.json").write_text(_json.dumps({
            "state": "ACTIVE", "current_phase": 3, "phase_truth_passed": True, "last_gate": 2,
        }))
        (method_dir / "quality_manifest.json").write_text(_json.dumps({
            "fr_ids": ["FR-01", "FR-02", "FR-03"],
            "gate_results": {"gate1": {"FR-01": True, "FR-02": True, "FR-03": True}},
        }))

        decision_dir = method_dir / "decision_logs"
        decision_dir.mkdir(parents=True)
        # stddev = 0.205 (< 0.5) but mean = 99.77 (>= 99.5) → saturated, allowed
        for fr, score in [("FR-01", 99.5), ("FR-02", 100.0), ("FR-03", 99.8)]:
            lf = decision_dir / f"GATE_3_{fr}.yaml"
            lf.write_text(_yaml.dump({
                "ctx": {"fr_id": fr},
                "scores": {"gate_score": score},
            }))

        # Gate 1 per-FR coverage: gate_timestamps.jsonl must have one entry per FR
        ts_lines = [
            _json.dumps({"phase": 3, "gate": 1, "fr_id": fr, "ts": 1.0})
            for fr in ["FR-01", "FR-02", "FR-03"]
        ]
        (method_dir / "gate_timestamps.jsonl").write_text("\n".join(ts_lines) + "\n")
        # next-phase plan required by _advance_prechecks (phase >= 3)
        (method_dir / "phase4_plan.md").touch()

        # P3 deliverables: src + tests dirs now required by PhaseArtifactRegistry
        (tmp_path / "03-development").mkdir()
        (tmp_path / "03-development" / "src").mkdir()
        (tmp_path / "03-development" / "tests").mkdir()
        for fr in ["FR-01", "FR-02", "FR-03"]:
            num = fr.split("-")[1].zfill(2)
            (tmp_path / "03-development" / "tests" / f"test_fr{num}.py").write_text(
                f"# stub for {fr}\n", encoding="utf-8"
            )
            (tmp_path / "03-development" / "src" / f"mod_{num}.py").write_text(
                f"\"\"\"[{fr}] stub module.\"\"\"\n", encoding="utf-8"
            )

        def _fake_run(cmd, **kw):
            class R:
                returncode = 0
                stdout = "===== test session starts =====\nTOTAL    10  0  100%\n"
                stderr = ""
            return R()

        exit_code, output = self._call_advance_phase(
            monkeypatch, tmp_path, completed=3, skip_prechecks=False,
            subprocess_run=_fake_run,
        )
        assert exit_code == 0, f"saturated near-ceiling scores must not be blocked:\n{output}"
        assert "variance ok" in output.lower()


# ═══════════════════════════════════════════════════════════════════════════════
# Test _advance_fsm state.json preservation
# ═══════════════════════════════════════════════════════════════════════════════


class TestAdvanceFsm:
    """Tests for _advance_fsm writing last_gate / last_fr to state.json."""

    def test_last_gate_fr_preserved_in_state_json(self, tmp_path, monkeypatch):
        """last_gate and last_fr are written to state.json (not reset to null)."""
        import subprocess

        def fake_run(cmd, **kw):
            class R:
                returncode = 0
                stdout = ""
                stderr = ""
            return R()

        monkeypatch.setattr(subprocess, "run", fake_run)

        from harness_cli import _advance_fsm
        _advance_fsm(tmp_path, 3, last_gate=2, last_fr="FR-13")

        import json
        state = json.loads((tmp_path / ".methodology" / "state.json").read_text())
        assert state["last_gate"] == 2
        assert state["last_fr"] == "FR-13"
        assert state["current_phase"] == 4

    def test_last_gate_fr_none_when_not_passed(self, tmp_path, monkeypatch):
        """When last_gate/last_fr are not provided, they stay None."""
        import subprocess

        def fake_run(cmd, **kw):
            class R:
                returncode = 0
                stdout = ""
                stderr = ""
            return R()

        monkeypatch.setattr(subprocess, "run", fake_run)

        from harness_cli import _advance_fsm
        _advance_fsm(tmp_path, 1)

        import json
        state = json.loads((tmp_path / ".methodology" / "state.json").read_text())
        assert state["last_gate"] is None
        assert state["last_fr"] is None
        assert state["current_phase"] == 2

    def test_advance_fsm_updates_fr_progress_phase(self, tmp_path, monkeypatch):
        """fr_progress.json phase advances together with state.json."""
        import subprocess

        def fake_run(cmd, **kw):
            class R:
                returncode = 0
                stdout = ""
                stderr = ""
            return R()

        monkeypatch.setattr(subprocess, "run", fake_run)

        # Seed a fr_progress.json with phase=3
        import json
        from harness.fr_progress_tracker import FRProgressTracker
        tracker = FRProgressTracker(tmp_path, phase=3)
        tracker.record_gate1_pass("FR-01", score=90.0)
        # Verify initial phase
        assert json.loads(
            (tmp_path / ".methodology" / "fr_progress.json").read_text()
        )["phase"] == 3

        from harness_cli import _advance_fsm
        _advance_fsm(tmp_path, 3, last_gate=2, last_fr="FR-01")

        # fr_progress.json should now have phase=4
        data = json.loads((tmp_path / ".methodology" / "fr_progress.json").read_text())
        assert data["phase"] == 4
        assert data["frs"]["FR-01"]["status"] == "gate1_pass"


# ═══════════════════════════════════════════════════════════════════════════════
# HR-10 sessions_spawn.log enforcement in finalize-gate
# ═══════════════════════════════════════════════════════════════════════════════


class TestDispatch:
    """Tests for cmd_dispatch — spawns agent + auto-logs to sessions_spawn.log."""

    def test_dispatch_developer_spawns_and_returns_ok(self, tmp_path, monkeypatch):
        import io
        from harness_cli import cmd_dispatch

        _captured_kwargs = {}

        def fake_spawn(self, role, prompt, context, phase, fr_id=None, **kwargs):
            _captured_kwargs.update(kwargs)
            _captured_kwargs["role"] = role
            _captured_kwargs["fr_id"] = fr_id
            # Write a minimal log entry to simulate _log_dispatch behavior
            import json
            log_path = self.project_path / ".methodology" / "sessions_spawn.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text(json.dumps({
                "fr_id": fr_id, "role": role, "status": "success",
                "session_id": "fake-001", "phase": phase,
            }) + "\n")
            return {"status": "success", "session_id": "fake-001"}

        monkeypatch.setattr("core.agent_spawner.AgentSpawner.spawn", fake_spawn)

        class Args:
            pass
        a = Args()
        a.project = str(tmp_path)  # type: ignore[reportAttributeAccessIssue]
        a.fr_id = "FR-01"  # type: ignore[reportAttributeAccessIssue]
        a.role = "developer"  # type: ignore[reportAttributeAccessIssue]
        a.prompt = "Implement FR-01"  # type: ignore[reportAttributeAccessIssue]
        a.phase = 3  # type: ignore[reportAttributeAccessIssue]
        a.no_persona = False  # type: ignore[reportAttributeAccessIssue]
        a.timeout = 300  # type: ignore[reportAttributeAccessIssue]

        captured = io.StringIO()
        monkeypatch.setattr("sys.stdout", captured)
        exit_code = cmd_dispatch(a)  # type: ignore[reportArgumentType]
        assert exit_code == 0
        assert "FR-01 | developer | success" in captured.getvalue()
        # developer: no persona override, full turns
        assert _captured_kwargs.get("persona_override") is None
        assert _captured_kwargs.get("max_turns") == 20

    def test_dispatch_reviewer_returns_ok(self, tmp_path, monkeypatch):
        import io
        from harness_cli import cmd_dispatch

        _captured_kwargs = {}

        def fake_spawn(self, role, prompt, context, phase, fr_id=None, **kwargs):
            _captured_kwargs.update(kwargs)
            return {"status": "APPROVE", "session_id": "fake-002"}

        monkeypatch.setattr("core.agent_spawner.AgentSpawner.spawn", fake_spawn)

        class Args:
            pass
        a = Args()
        a.project = str(tmp_path)  # type: ignore[reportAttributeAccessIssue]
        a.fr_id = "FR-01"  # type: ignore[reportAttributeAccessIssue]
        a.role = "reviewer"  # type: ignore[reportAttributeAccessIssue]
        a.prompt = "Review FR-01"  # type: ignore[reportAttributeAccessIssue]
        a.phase = 3  # type: ignore[reportAttributeAccessIssue]
        a.no_persona = False  # type: ignore[reportAttributeAccessIssue]
        a.timeout = 300  # type: ignore[reportAttributeAccessIssue]

        captured = io.StringIO()
        monkeypatch.setattr("sys.stdout", captured)
        exit_code = cmd_dispatch(a)  # type: ignore[reportArgumentType]
        assert exit_code == 0
        # reviewer: persona skipped and turns capped at 3
        assert _captured_kwargs.get("persona_override") == ""
        assert _captured_kwargs.get("max_turns") == 3

    def test_dispatch_non_ok_status_returns_1(self, tmp_path, monkeypatch):
        import io
        from harness_cli import cmd_dispatch

        _captured_kwargs = {}

        def fake_spawn(self, role, prompt, context, phase, fr_id=None, **kwargs):
            _captured_kwargs.update(kwargs)
            return {"status": "REJECT", "session_id": "fake-003"}

        monkeypatch.setattr("core.agent_spawner.AgentSpawner.spawn", fake_spawn)

        class Args:
            pass
        a = Args()
        a.project = str(tmp_path)  # type: ignore[reportAttributeAccessIssue]
        a.fr_id = "FR-02"  # type: ignore[reportAttributeAccessIssue]
        a.role = "reviewer"  # type: ignore[reportAttributeAccessIssue]
        a.prompt = "Review FR-02"  # type: ignore[reportAttributeAccessIssue]
        a.phase = 3  # type: ignore[reportAttributeAccessIssue]
        a.no_persona = False  # type: ignore[reportAttributeAccessIssue]
        a.timeout = 300  # type: ignore[reportAttributeAccessIssue]

        captured = io.StringIO()
        monkeypatch.setattr("sys.stdout", captured)
        exit_code = cmd_dispatch(a)  # type: ignore[reportArgumentType]
        assert exit_code == 1
        # reviewer: persona skipped and turns capped at 3
        assert _captured_kwargs.get("persona_override") == ""
        assert _captured_kwargs.get("max_turns") == 3

    def test_dispatch_business_analyst_uses_reviewer_params(self, tmp_path, monkeypatch):
        """BUSINESS_ANALYST matches 'analyst' in role_lower → reviewer parameters."""
        import io
        from harness_cli import cmd_dispatch

        _captured_kwargs = {}

        def fake_spawn(self, role, prompt, context, phase, fr_id=None, **kwargs):
            _captured_kwargs.update(kwargs)
            return {"status": "APPROVE", "session_id": "fake-006"}

        monkeypatch.setattr("core.agent_spawner.AgentSpawner.spawn", fake_spawn)

        class Args:
            pass
        a = Args()
        a.project = str(tmp_path)  # type: ignore[reportAttributeAccessIssue]
        a.fr_id = "SRS.md"  # type: ignore[reportAttributeAccessIssue]
        a.role = "BUSINESS_ANALYST"  # type: ignore[reportAttributeAccessIssue]
        a.prompt = "Review business alignment"  # type: ignore[reportAttributeAccessIssue]
        a.phase = 1  # type: ignore[reportAttributeAccessIssue]
        a.no_persona = False  # type: ignore[reportAttributeAccessIssue]
        a.timeout = 300  # type: ignore[reportAttributeAccessIssue]

        captured = io.StringIO()
        monkeypatch.setattr("sys.stdout", captured)
        exit_code = cmd_dispatch(a)  # type: ignore[reportArgumentType]
        assert exit_code == 0
        # BUSINESS_ANALYST: persona skipped and turns capped at 3 (same as reviewer)
        assert _captured_kwargs.get("persona_override") == ""
        assert _captured_kwargs.get("max_turns") == 3

    def test_dispatch_complete_status_succeeds(self, tmp_path, monkeypatch):
        """Bug #1: 'complete' status (Task tool non-dict result path) must exit 0."""
        import io
        from harness_cli import cmd_dispatch

        _captured_kwargs = {}

        def fake_spawn(self, role, prompt, context, phase, fr_id=None, **kwargs):
            _captured_kwargs.update(kwargs)
            # AgentSpawner._parse_result wraps non-dict Task results as status="complete"
            return {"status": "complete", "session_id": "fake-004", "output": "done"}

        monkeypatch.setattr("core.agent_spawner.AgentSpawner.spawn", fake_spawn)

        class Args:
            pass
        a = Args()
        a.project = str(tmp_path)  # type: ignore[reportAttributeAccessIssue]
        a.fr_id = "FR-03"  # type: ignore[reportAttributeAccessIssue]
        a.role = "developer"  # type: ignore[reportAttributeAccessIssue]
        a.prompt = "Implement FR-03"  # type: ignore[reportAttributeAccessIssue]
        a.phase = 3  # type: ignore[reportAttributeAccessIssue]
        a.no_persona = False  # type: ignore[reportAttributeAccessIssue]
        a.timeout = 300  # type: ignore[reportAttributeAccessIssue]

        captured = io.StringIO()
        monkeypatch.setattr("sys.stdout", captured)
        exit_code = cmd_dispatch(a)  # type: ignore[reportArgumentType]
        assert exit_code == 0
        assert "complete" in captured.getvalue()
        # developer: no persona override, full turns
        assert _captured_kwargs.get("persona_override") is None
        assert _captured_kwargs.get("max_turns") == 20

    def test_dispatch_spawned_status_succeeds(self, tmp_path, monkeypatch):
        """Bug #1: 'SPAWNED' default status (unknown/new status) must exit 0."""
        import io
        from harness_cli import cmd_dispatch

        _captured_kwargs = {}

        def fake_spawn(self, role, prompt, context, phase, fr_id=None, **kwargs):
            _captured_kwargs.update(kwargs)
            return {"status": "SPAWNED", "session_id": "fake-005"}

        monkeypatch.setattr("core.agent_spawner.AgentSpawner.spawn", fake_spawn)

        class Args:
            pass
        a = Args()
        a.project = str(tmp_path)  # type: ignore[reportAttributeAccessIssue]
        a.fr_id = "FR-04"  # type: ignore[reportAttributeAccessIssue]
        a.role = "developer"  # type: ignore[reportAttributeAccessIssue]
        a.prompt = "Implement FR-04"  # type: ignore[reportAttributeAccessIssue]
        a.phase = 3  # type: ignore[reportAttributeAccessIssue]
        a.no_persona = False  # type: ignore[reportAttributeAccessIssue]
        a.timeout = 300  # type: ignore[reportAttributeAccessIssue]

        captured = io.StringIO()
        monkeypatch.setattr("sys.stdout", captured)
        exit_code = cmd_dispatch(a)  # type: ignore[reportArgumentType]
        assert exit_code == 0
        # developer: no persona override, full turns
        assert _captured_kwargs.get("persona_override") is None
        assert _captured_kwargs.get("max_turns") == 20



class TestFinalizeGate1:
    """finalize-gate --gate 1 pass path + gate-result persistence (HR-10/HR-01 audit removed)."""

    @staticmethod
    def _call_finalize(monkeypatch, tmp_path, gate=1, phase=1, fr_id="FR-01",
                       spawn_entries=None, gate1_result=None):
        """Call cmd_finalize_gate and return (exit_code, output_str)."""
        import io
        from harness_cli import cmd_finalize_gate

        class Args:
            pass
        a = Args()
        a.gate = gate  # type: ignore[reportAttributeAccessIssue]
        a.phase = phase  # type: ignore[reportAttributeAccessIssue]
        a.project = str(tmp_path)  # type: ignore[reportAttributeAccessIssue]
        a.fr_id = fr_id  # type: ignore[reportAttributeAccessIssue]

        # Write gate1_result.json (needed for bridge.finalize_gate)
        sessi = tmp_path / ".sessi-work"
        sessi.mkdir(parents=True, exist_ok=True)
        if gate1_result is None:
            gate1_result = {
                "gate": 1, "phase": phase, "fr_id": fr_id,
                "score": 95.0, "quality_complete": True,
                "dimensions": {"linting": 95, "type_safety": 95, "test_coverage": 95},
            }
        import json as _json
        (sessi / "gate1_result.json").write_text(_json.dumps(gate1_result))

        # Write quality_manifest.json
        manifest_dir = tmp_path / ".methodology"
        manifest_dir.mkdir(parents=True, exist_ok=True)
        (manifest_dir / "quality_manifest.json").write_text(_json.dumps({
            "fr_ids": [fr_id],
            "gate_results": {"gate1": {}},
        }))

        # Write state.json
        (manifest_dir / "state.json").write_text(_json.dumps({
            "state": "ACTIVE", "current_phase": phase,
        }))

        # Write sessions_spawn.log
        log_path = manifest_dir / "sessions_spawn.log"
        if spawn_entries is not None:
            log_path.write_text("\n".join(_json.dumps(e) for e in spawn_entries) + "\n")

        # Write run-gate sentinel so the sentinel check passes (all tests below
        # test HR-10/HR-01 logic, not the sentinel; sentinel has its own test).
        _sentinel_key = (fr_id or "phase").replace("-", "").lower()
        _sentinel_dir = tmp_path / ".sessi-work" / "sentinels"
        _sentinel_dir.mkdir(parents=True, exist_ok=True)
        (_sentinel_dir / f"g{gate}_p{phase}_{_sentinel_key}.flag").write_text("test")

        # Disable git ops
        monkeypatch.setattr("harness_cli._make_git",
                            lambda args, project: __import__("harness.git_strategy").git_strategy.GitStrategy(project, enabled=False))
        captured = io.StringIO()
        monkeypatch.setattr("sys.stdout", captured)
        try:
            exit_code = cmd_finalize_gate(a)  # type: ignore[reportArgumentType]
        except SystemExit as e:
            exit_code = e.code
        return exit_code, captured.getvalue()

    def test_missing_sentinel_blocks(self, tmp_path, monkeypatch):
        """Exit code 1 when run-gate sentinel is missing (finalize-gate called directly)."""
        import io
        from harness_cli import cmd_finalize_gate
        import json as _json

        class Args:
            pass
        a = Args()
        a.gate = 1  # type: ignore[reportAttributeAccessIssue]
        a.phase = 3  # type: ignore[reportAttributeAccessIssue]
        a.project = str(tmp_path)  # type: ignore[reportAttributeAccessIssue]
        a.fr_id = "FR-01"  # type: ignore[reportAttributeAccessIssue]

        # Write gate1_result.json but intentionally NO sentinel
        sessi = tmp_path / ".sessi-work"
        sessi.mkdir(parents=True, exist_ok=True)
        (sessi / "gate1_result.json").write_text(_json.dumps({
            "gate": 1, "phase": 3, "fr_id": "FR-01",
            "score": 95.0, "quality_complete": True,
            "dimensions": {"linting": 95, "type_safety": 95, "test_coverage": 95},
        }))
        manifest_dir = tmp_path / ".methodology"
        manifest_dir.mkdir(parents=True, exist_ok=True)
        (manifest_dir / "quality_manifest.json").write_text(_json.dumps(
            {"fr_ids": ["FR-01"], "gate_results": {"gate1": {}}}
        ))
        (manifest_dir / "state.json").write_text(_json.dumps(
            {"state": "ACTIVE", "current_phase": 3}
        ))

        monkeypatch.setattr("harness_cli._make_git",
                            lambda args, project: __import__("harness.git_strategy").git_strategy.GitStrategy(project, enabled=False))
        captured = io.StringIO()
        monkeypatch.setattr("sys.stdout", captured)
        try:
            exit_code = cmd_finalize_gate(a)  # type: ignore[reportArgumentType]
        except SystemExit as e:
            exit_code = e.code
        assert exit_code == 1
        assert "BLOCKED" in captured.getvalue()
        assert "run-gate" in captured.getvalue()




    def test_dev_and_reviewer_passes(self, tmp_path, monkeypatch):
        """Gate 1 passes when 2 distinct roles with different session_ids exist."""
        exit_code, output = self._call_finalize(monkeypatch, tmp_path, spawn_entries=[
            {"fr_id": "FR-01", "role": "developer", "session_id": "d1",
             "status": "success", "confidence": 9},
            {"fr_id": "FR-01", "role": "reviewer", "session_id": "r1",
             "status": "success", "review_status": "APPROVE"},
        ])
        assert exit_code == 0
        assert "HR-10" not in output
        assert "HR-01" not in output

    def test_missing_spawn_log_no_longer_blocks(self, tmp_path, monkeypatch):
        """HR-10 removed: a Gate 1 finalize with NO sessions_spawn.log is not blocked on that basis."""
        exit_code, output = self._call_finalize(monkeypatch, tmp_path, spawn_entries=None)
        assert exit_code == 0
        assert "HR-10" not in output

    def test_gate_result_persisted_to_methodology(self, tmp_path, monkeypatch):
        """A (Bug 1/2): a passed gate copies gate{N}_result.json to .methodology/
        (committable + survives advance-phase rmtree of .sessi-work/)."""
        exit_code, _ = self._call_finalize(monkeypatch, tmp_path, spawn_entries=[
            {"fr_id": "FR-01", "role": "developer", "session_id": "d1",
             "status": "success", "confidence": 9},
            {"fr_id": "FR-01", "role": "reviewer", "session_id": "r1",
             "status": "success", "review_status": "APPROVE"},
        ])
        assert exit_code == 0
        persisted = tmp_path / ".methodology" / "gate1_result.json"
        assert persisted.exists(), "gate result must be persisted to .methodology/ on pass"




    def test_phase3_skips_hr10_check(self, tmp_path, monkeypatch):
        """Phase 3+ does not enforce HR-10/HR-01 (A/B removed)."""
        import json as _json
        sessi = tmp_path / ".sessi-work"
        sessi.mkdir(parents=True)
        gate1_result = {
            "gate": 1, "phase": 3, "fr_id": "FR-01",
            "score": 95.0, "quality_complete": True,
            "dimensions": {"linting": 95, "type_safety": 95, "test_coverage": 95},
        }
        (sessi / "gate1_result.json").write_text(_json.dumps(gate1_result))
        manifest_dir = tmp_path / ".methodology"
        manifest_dir.mkdir(parents=True)
        (manifest_dir / "quality_manifest.json").write_text(_json.dumps({
            "fr_ids": ["FR-01"],
            "gate_results": {"gate1": {}},
        }))
        (manifest_dir / "state.json").write_text(_json.dumps({
            "state": "ACTIVE", "current_phase": 3,
        }))
        # No sessions_spawn.log — would normally block for P1-P2
        _sentinel_dir = sessi / "sentinels"
        _sentinel_dir.mkdir(parents=True, exist_ok=True)
        # v2.13: per-phase sentinel path (Bug #121)
        (_sentinel_dir / "g1_p3_fr01.flag").write_text("test")

        import io
        from harness_cli import cmd_finalize_gate
        monkeypatch.setattr(
            "harness_cli._make_git",
            lambda args, project: __import__("harness.git_strategy").git_strategy.GitStrategy(
                project, enabled=False),
        )
        captured = io.StringIO()
        monkeypatch.setattr("sys.stdout", captured)
        try:
            exit_code = cmd_finalize_gate(
                type("Args", (), {  # type: ignore[reportArgumentType]
                    "gate": 1, "phase": 3, "project": str(tmp_path), "fr_id": "FR-01",
                })()
            )
        except SystemExit as e:
            exit_code = e.code

        output = captured.getvalue()
        assert exit_code in (0, 1), f"Expected exit 0 or 1 (not hard-block 5), got {exit_code}"
        assert "HR-10" not in output, "HR-10 should not be enforced for Phase 3+"


# ---------------------------------------------------------------------------
# Tests: --emergency-override (the only bypass mechanism; --force is abolished)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Tests: Gate 4 prerequisite checks (_check_gate4_prerequisites)
# ---------------------------------------------------------------------------

class TestGate4Prerequisites:
    """_check_gate4_prerequisites returns True (blocked) when requirements unmet."""

    def _make_project(self, tmp_path: Path) -> Path:
        """Minimal project with all Gate 4 prerequisites satisfied."""
        import json as _json
        methodology = tmp_path / ".methodology"
        methodology.mkdir(parents=True, exist_ok=True)

        # gate4_result.json (A2/A3/A4/A5)
        scores_dir = tmp_path / ".sessi-work" / "round_1" / "scores"
        scores_dir.mkdir(parents=True, exist_ok=True)
        (scores_dir / "architecture.json").write_text(_json.dumps({"score": 88}))

        issue_registry = methodology / "issue_registry.json"
        issue_registry.write_text(_json.dumps([{"id": "I-01", "dim": "architecture"}]))

        result_file = tmp_path / ".sessi-work" / "gate4_result.json"
        result_file.write_text(_json.dumps({
            "composite_score": 87.5,
            "breakdown": {
                "architecture": {"llm_score": 88, "score": 88},
                "linting": {"llm_score": 92, "score": 92},
            },
            "model_used": {
                "architecture": "claude-sonnet",
                "linting": "claude",
            },
            "devil_advocate": {
                "architecture": True,
                "readability": True,
                "error_handling": True,
                "documentation": True,
                "performance": True,
            },
            "devil_advocate_evidence": {
                d: {
                    "challenger_model": "claude",
                    "challenge": ("Challenger critique of the " + d + " evaluation: " + "x" * 130),
                    "response": ("Defence of the " + d + " design and score: " + "y" * 130),
                }
                for d in ("architecture", "readability", "error_handling",
                          "documentation", "performance")
            },
            "issue_registry_path": ".methodology/issue_registry.json",
        }))

        return tmp_path

    def test_all_prerequisites_met_not_blocked(self, tmp_path):
        """When all prerequisites are satisfied, returns False (not blocked)."""
        from harness_cli import _check_gate4_prerequisites
        project = self._make_project(tmp_path)
        assert _check_gate4_prerequisites(project)[0] is False

    def test_missing_hermes_receipt_not_blocked(self, tmp_path):
        """Hermes receipt is no longer required (A1 removed) — Gate 4 proceeds without it."""
        from harness_cli import _check_gate4_prerequisites
        project = self._make_project(tmp_path)
        # Receipt file is never created — A1 check removed, missing receipt must NOT block
        assert not (project / ".methodology" / "hermes_g4_receipt.json").exists()
        assert _check_gate4_prerequisites(project)[0] is False

    def test_tier1_dim_using_claude_allowed(self, tmp_path):
        """A2 accepts Claude for all dims — model name is no longer restricted."""
        import copy as _copy
        import json as _json
        from harness_cli import _check_gate4_prerequisites
        project = self._make_project(tmp_path)
        result_file = project / ".sessi-work" / "gate4_result.json"
        data = _copy.deepcopy(_json.loads(result_file.read_text()))
        data["model_used"]["linting"] = "claude-sonnet"   # now valid — all dims use Claude
        result_file.write_text(_json.dumps(data))
        # A2 only checks presence; not blocked by model name.
        assert _check_gate4_prerequisites(project)[0] is False

    def test_devil_advocate_missing_dim_blocked(self, tmp_path):
        """Tier 3 dim without devil_advocate=True blocks (A3)."""
        import copy as _copy
        import json as _json
        from harness_cli import _check_gate4_prerequisites
        project = self._make_project(tmp_path)
        result_file = project / ".sessi-work" / "gate4_result.json"
        data = _copy.deepcopy(_json.loads(result_file.read_text()))
        data["devil_advocate"]["architecture"] = False
        result_file.write_text(_json.dumps(data))
        assert _check_gate4_prerequisites(project)[0] is True

    def test_a4_high_score_confirmations_removed(self, tmp_path):
        """A4 removed: a project with NO high_score_confirmations is not blocked."""
        import copy as _copy
        import json as _json
        from harness_cli import _check_gate4_prerequisites
        project = self._make_project(tmp_path)
        result_file = project / ".sessi-work" / "gate4_result.json"
        data = _copy.deepcopy(_json.loads(result_file.read_text()))
        data.pop("high_score_confirmations", None)  # field gone entirely
        result_file.write_text(_json.dumps(data))
        assert _check_gate4_prerequisites(project)[0] is False

    def test_a3_requires_da_evidence(self, tmp_path):
        """A3 hardened: devil_advocate=true without devil_advocate_evidence → blocked."""
        import copy as _copy
        import json as _json
        from harness_cli import _check_gate4_prerequisites
        project = self._make_project(tmp_path)
        result_file = project / ".sessi-work" / "gate4_result.json"
        data = _copy.deepcopy(_json.loads(result_file.read_text()))
        data.pop("devil_advocate_evidence", None)  # bare boolean only
        result_file.write_text(_json.dumps(data))
        assert _check_gate4_prerequisites(project)[0] is True

    def test_a3_da_evidence_too_short_blocked(self, tmp_path):
        """A3: placeholder/too-short challenge text is rejected."""
        import copy as _copy
        import json as _json
        from harness_cli import _check_gate4_prerequisites
        project = self._make_project(tmp_path)
        result_file = project / ".sessi-work" / "gate4_result.json"
        data = _copy.deepcopy(_json.loads(result_file.read_text()))
        data["devil_advocate_evidence"]["architecture"]["challenge"] = "too short"
        result_file.write_text(_json.dumps(data))
        assert _check_gate4_prerequisites(project)[0] is True

    def test_a3_da_waiver_requires_evidence(self, tmp_path):
        """da_waiver only takes effect when artifact-backed; missing evidence → blocked."""
        import copy as _copy
        import json as _json
        from harness_cli import _check_gate4_prerequisites
        project = self._make_project(tmp_path)
        result_file = project / ".sessi-work" / "gate4_result.json"
        data = _copy.deepcopy(_json.loads(result_file.read_text()))
        data["da_waiver"] = {"architecture": True}
        data["devil_advocate_evidence"].pop("architecture", None)
        result_file.write_text(_json.dumps(data))
        blocked, waivers = _check_gate4_prerequisites(project)
        assert blocked is True
        assert "architecture" not in waivers

    def test_missing_issue_registry_no_longer_blocks(self, tmp_path):
        """A5 is advisory now — a missing issue_registry file does NOT block Gate 4."""
        from harness_cli import _check_gate4_prerequisites
        project = self._make_project(tmp_path)
        (project / ".methodology" / "issue_registry.json").unlink()
        assert _check_gate4_prerequisites(project)[0] is False

    def test_empty_scores_dir_blocked(self, tmp_path):
        """Empty per-dim scores directory blocks (B2)."""
        from harness_cli import _check_gate4_prerequisites
        project = self._make_project(tmp_path)
        for f in (project / ".sessi-work" / "round_1" / "scores").glob("*.json"):
            f.unlink()
        assert _check_gate4_prerequisites(project)[0] is True

    def test_missing_scores_dir_blocked(self, tmp_path):
        """Missing scores directory blocks (B2)."""
        import shutil as _shutil
        from harness_cli import _check_gate4_prerequisites
        project = self._make_project(tmp_path)
        _shutil.rmtree(project / ".sessi-work" / "round_1" / "scores")
        assert _check_gate4_prerequisites(project)[0] is True
