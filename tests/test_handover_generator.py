# tests/test_handover_generator.py
# Tests for HandoverGenerator and GitStrategy handover integration.
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from harness.handover_generator import HandoverGenerator, DEFAULT_NOTES
from harness.git_strategy import GitStrategy


# ── HandoverGenerator unit tests ────────────────────────────────────────────

class TestHandoverGenerator:
    def test_write_creates_handover_md(self, tmp_path: Path):
        gen = HandoverGenerator(tmp_path)
        path = gen.write(
            checkpoint_id="P3-pre-ssi-20260504",
            phase=3,
            task_background="Implementing FR-001..FR-005.",
            current_status="5/5 FRs Gate 1 PASS. SSI not yet run.",
            next_steps=["Run SSI 3 rounds", "Fix Gate 2 failures"],
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

    def test_unknown_phase_uses_fallback_name(self, tmp_path: Path):
        gen = HandoverGenerator(tmp_path)
        gen.write(
            checkpoint_id="P9-exit-20260504",
            phase=9,
            task_background="bg",
            current_status="status",
            next_steps=["step 1"],
        )
        content = (tmp_path / "HANDOVER.md").read_text(encoding="utf-8")
        assert "Phase 9" in content

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

    def test_p3_pre_ssi_writes_handover(self, tmp_path: Path):
        gs = self._make_strategy(tmp_path)
        gs.commit_and_push_p3_pre_ssi(fr_ids=["FR-001", "FR-002", "FR-003"])
        content = (tmp_path / "HANDOVER.md").read_text()
        assert "P3-pre-ssi" in content
        assert "SSI" in content

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
        assert "P5-baseline" in (tmp_path / "HANDOVER.md").read_text()

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

    def test_no_plan_override_uses_phase_default(self, tmp_path: Path):
        """Without plan_override, defaults to phase{N}_plan.md."""
        gen = HandoverGenerator(tmp_path)
        gen.write(
            checkpoint_id="P3-test",
            phase=3,
            task_background="bg",
            current_status="status",
            next_steps=["step 1"],
        )
        content = (tmp_path / "HANDOVER.md").read_text()
        assert ".methodology/phase3_plan.md" in content

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

    def test_p1_handover_includes_hermes_status(self, tmp_path: Path, monkeypatch):
        """P1 HANDOVER 附加資訊 includes HERMES_REVIEWER_TARGET status."""
        monkeypatch.setenv("HERMES_REVIEWER_TARGET", "telegram:123456")
        gs = self._make_strategy(tmp_path)
        gs.commit_and_push_p1(fr_ids=["FR-01"])
        content = (tmp_path / "HANDOVER.md").read_text()
        assert "HERMES_REVIEWER_TARGET" in content
        assert "✅ set" in content

    def test_p1_handover_hermes_not_set(self, tmp_path: Path, monkeypatch):
        """P1 HANDOVER flags HERMES_REVIEWER_TARGET as not set when absent."""
        monkeypatch.delenv("HERMES_REVIEWER_TARGET", raising=False)
        gs = self._make_strategy(tmp_path)
        gs.commit_and_push_p1(fr_ids=["FR-01"])
        content = (tmp_path / "HANDOVER.md").read_text()
        assert "❌ not set" in content

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
        """_deliverable_files(1) returns ✅ for all 4 P1 files when present."""
        (tmp_path / "01-requirements").mkdir(parents=True, exist_ok=True)
        for name in ["01-requirements/SRS.md", "01-requirements/CONSTRAINTS.md",
                      "01-requirements/SPEC_TRACKING.md", "01-requirements/TRACEABILITY_MATRIX.md"]:
            (tmp_path / name).write_text("# content\n" * 10, encoding="utf-8")
        gs = GitStrategy(tmp_path, enabled=False)
        items = gs._deliverable_files(1)
        assert len(items) == 4
        assert all("✅" in item for item in items)
        assert any("10L" in item or "L)" in item for item in items)  # line count included

    def test_deliverable_files_p1_missing(self, tmp_path: Path):
        """_deliverable_files(1) marks absent files as ❌ missing."""
        (tmp_path / "01-requirements").mkdir(parents=True, exist_ok=True)
        (tmp_path / "01-requirements" / "SRS.md").write_text("# SRS\n", encoding="utf-8")
        # CONSTRAINTS.md, SPEC_TRACKING.md, TRACEABILITY_MATRIX.md absent
        gs = GitStrategy(tmp_path, enabled=False)
        items = gs._deliverable_files(1)
        assert len(items) == 4
        assert any("✅" in item and "SRS.md" in item for item in items)
        assert sum("❌ missing" in item for item in items) == 3

    def test_deliverable_files_p2(self, tmp_path: Path):
        """_deliverable_files(2) covers SAD.md, ADR.md, ARCHITECTURE_DIAGRAM.md."""
        (tmp_path / "02-architecture").mkdir(parents=True, exist_ok=True)
        (tmp_path / "02-architecture" / "SAD.md").write_text("# SAD\n", encoding="utf-8")
        gs = GitStrategy(tmp_path, enabled=False)
        items = gs._deliverable_files(2)
        assert len(items) == 3
        assert any("SAD.md" in item and "✅" in item for item in items)
        assert any("ADR.md" in item and "❌" in item for item in items)

    def test_deliverable_files_unknown_phase(self, tmp_path: Path):
        """_deliverable_files() returns [] for phases without a hardcoded list."""
        gs = GitStrategy(tmp_path, enabled=False)
        assert gs._deliverable_files(5) == []

    def test_p1_handover_contains_deliverable_section(self, tmp_path: Path):
        """P1 HANDOVER.md includes '交付物清單' section listing deliverables."""
        (tmp_path / "01-requirements").mkdir(parents=True, exist_ok=True)
        for name in ["01-requirements/SRS.md", "01-requirements/CONSTRAINTS.md",
                      "01-requirements/SPEC_TRACKING.md", "01-requirements/TRACEABILITY_MATRIX.md"]:
            (tmp_path / name).write_text("# content\n", encoding="utf-8")
        gs = self._make_strategy(tmp_path)
        gs.commit_and_push_p1(fr_ids=["FR-01"])
        content = (tmp_path / "HANDOVER.md").read_text()
        assert "交付物清單" in content
        assert "SRS.md" in content
        assert "CONSTRAINTS.md" in content

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

    def test_hermes_value_substituted_in_startup_section(self, tmp_path: Path, monkeypatch):
        """HERMES_REVIEWER_TARGET real value appears in ▶ 立即開始 (not <value> placeholder)."""
        monkeypatch.setenv("HERMES_REVIEWER_TARGET", "telegram:12345")
        gs = self._make_strategy(tmp_path)
        gs.commit_and_push_p1(fr_ids=["FR-01"])
        content = (tmp_path / "HANDOVER.md").read_text()
        # real value must appear in the startup block
        assert "telegram:12345" in content
        # generic placeholder must NOT appear
        assert "=<value>" not in content

    def test_deliverables_section_has_blank_line_before_status(self, tmp_path: Path):
        """交付物清單 section must be followed by a blank line before ## 目前執行狀況."""
        (tmp_path / "01-requirements").mkdir(parents=True, exist_ok=True)
        for name in ["01-requirements/SRS.md", "01-requirements/CONSTRAINTS.md",
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
        import sys
        import io
        from harness_cli import cmd_push_milestone

        # Build a fake args namespace
        class Args:
            pass
        a = Args()
        a.type = milestone_type
        a.project = str(tmp_path)
        a.fr_ids = kwargs.get("fr_ids", "")
        a.fr_done = kwargs.get("fr_done")
        a.fr_total = kwargs.get("fr_total")
        a.no_git = True

        # Disable actual git operations
        captured = io.StringIO()
        monkeypatch.setattr("sys.stdout", captured)
        monkeypatch.setattr("sys.exit", lambda code: code)
        monkeypatch.setattr("harness_cli._make_git", lambda args, project: GitStrategy(project, enabled=False))
        try:
            exit_code = cmd_push_milestone(a)
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

    def test_p3_pre_ssi(self, tmp_path, monkeypatch):
        exit_code, _ = self._call_push_milestone(
            monkeypatch, tmp_path, "p3-pre-ssi",
            fr_ids="FR-01,FR-02,FR-03",
        )
        assert exit_code == 0

    def test_p5_baseline(self, tmp_path, monkeypatch):
        exit_code, _ = self._call_push_milestone(monkeypatch, tmp_path, "p5-baseline")
        assert exit_code == 0

    def test_p7(self, tmp_path, monkeypatch):
        exit_code, _ = self._call_push_milestone(monkeypatch, tmp_path, "p7")
        assert exit_code == 0

    def test_p8(self, tmp_path, monkeypatch):
        exit_code, _ = self._call_push_milestone(monkeypatch, tmp_path, "p8")
        assert exit_code == 0

    def test_unknown_type(self, tmp_path, monkeypatch):
        exit_code, output = self._call_push_milestone(monkeypatch, tmp_path, "unknown")
        assert exit_code == 1
        assert "Unknown milestone type" in output
