"""Regression tests: every commit_and_push_* method writes HANDOVER.md
before attempting the commit+push (so the content lands in the pushed
commit on success). If the push fails, disk must not be left claiming a
checkpoint/handoff that was never actually pushed — the prior HANDOVER.md
content must be restored (or the file removed if none existed before).
"""

from harness.git_strategy import GitStrategy


class TestHandoverRevertOnPushFailure:
    def test_restores_prior_content_on_push_failure(self, tmp_path, monkeypatch):
        handover = tmp_path / "HANDOVER.md"
        handover.write_text("OLD CONTENT", encoding="utf-8")

        gs = GitStrategy(tmp_path)
        monkeypatch.setattr(
            gs, "_write_handover",
            lambda **_kw: handover.write_text("NEW CONTENT", encoding="utf-8"),
        )
        monkeypatch.setattr(gs, "_commit_and_push", lambda *_a, **_kw: False)

        ok = gs.commit_and_push_p1(["FR-01"])

        assert ok is False
        assert handover.read_text(encoding="utf-8") == "OLD CONTENT"

    def test_removes_file_when_none_existed_before(self, tmp_path, monkeypatch):
        handover = tmp_path / "HANDOVER.md"
        assert not handover.exists()

        gs = GitStrategy(tmp_path)
        monkeypatch.setattr(
            gs, "_write_handover",
            lambda **_kw: handover.write_text("NEW CONTENT", encoding="utf-8"),
        )
        monkeypatch.setattr(gs, "_commit_and_push", lambda *_a, **_kw: False)

        ok = gs.commit_and_push_p7()

        assert ok is False
        assert not handover.exists()

    def test_keeps_new_content_on_push_success(self, tmp_path, monkeypatch):
        handover = tmp_path / "HANDOVER.md"
        handover.write_text("OLD CONTENT", encoding="utf-8")

        gs = GitStrategy(tmp_path)
        monkeypatch.setattr(
            gs, "_write_handover",
            lambda **_kw: handover.write_text("NEW CONTENT", encoding="utf-8"),
        )
        monkeypatch.setattr(gs, "_commit_and_push", lambda *_a, **_kw: True)

        ok = gs.commit_and_push_p8()

        assert ok is True
        assert handover.read_text(encoding="utf-8") == "NEW CONTENT"

    def test_gate4_revert_does_not_block_tag_release_skip(self, tmp_path, monkeypatch):
        """commit_and_push_gate reads `ok` after the revert call for its
        gate-4 tag-release branch — the revert must not interfere with that
        downstream check."""
        handover = tmp_path / "HANDOVER.md"
        handover.write_text("OLD CONTENT", encoding="utf-8")

        gs = GitStrategy(tmp_path)
        monkeypatch.setattr(
            gs, "_write_handover",
            lambda **_kw: handover.write_text("NEW CONTENT", encoding="utf-8"),
        )
        monkeypatch.setattr(gs, "_commit_and_push", lambda *_a, **_kw: False)
        tag_calls = []
        monkeypatch.setattr(gs, "_tag_release", lambda score: tag_calls.append(score))

        ok = gs.commit_and_push_gate(gate_num=4, phase=6, score=90.0)

        assert ok is False
        assert tag_calls == []  # must not tag a release that was never pushed
        assert handover.read_text(encoding="utf-8") == "OLD CONTENT"


class TestSnapshotHandoverHelper:
    def test_snapshot_returns_none_when_missing(self, tmp_path):
        gs = GitStrategy(tmp_path)
        assert gs._snapshot_handover() is None

    def test_snapshot_returns_content_when_present(self, tmp_path):
        (tmp_path / "HANDOVER.md").write_text("hello", encoding="utf-8")
        gs = GitStrategy(tmp_path)
        assert gs._snapshot_handover() == "hello"

    def test_revert_noop_when_ok_true(self, tmp_path):
        handover = tmp_path / "HANDOVER.md"
        handover.write_text("current", encoding="utf-8")
        gs = GitStrategy(tmp_path)
        gs._revert_handover_on_push_failure(True, "irrelevant prior content")
        assert handover.read_text(encoding="utf-8") == "current"
