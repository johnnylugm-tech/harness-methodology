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
