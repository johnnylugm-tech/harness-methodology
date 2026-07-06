"""Tests for core.atomic_io.StateTransaction — multi-file staged commit.

The class exists because the point-fix history (#104, #118, 28864f7,
dd9129b, a6f5c24, b9493b9) kept re-fixing the same bug at individual write
sites: a command writes file A, then fails producing file B, leaving the
project half-advanced. The transaction guarantees: nothing visible before
commit(); a mid-commit crash leaves a journal that `doctor` can detect.
"""

import json

import pytest

from core.atomic_io import StateTransaction


def _txn(tmp_path):
    (tmp_path / ".methodology").mkdir(exist_ok=True)
    return StateTransaction(tmp_path)


class TestStagingInvisibility:
    def test_nothing_visible_before_commit(self, tmp_path):
        txn = _txn(tmp_path)
        target = tmp_path / ".methodology" / "state.json"
        txn.stage_json(target, {"current_phase": 5})
        assert not target.exists()

    def test_commit_makes_all_files_visible(self, tmp_path):
        txn = _txn(tmp_path)
        state = tmp_path / ".methodology" / "state.json"
        handover = tmp_path / "HANDOVER.md"
        txn.stage_text(handover, "# Handover\n")
        txn.stage_json(state, {"current_phase": 5})
        txn.commit()
        assert handover.read_text(encoding="utf-8") == "# Handover\n"
        assert json.loads(state.read_text(encoding="utf-8")) == {"current_phase": 5}
        assert not txn.journal_path.exists(), "journal must be cleared after commit"

    def test_commit_overwrites_existing_targets(self, tmp_path):
        state = tmp_path / ".methodology" / "state.json"
        state.parent.mkdir(exist_ok=True)
        state.write_text('{"current_phase": 4}', encoding="utf-8")
        txn = _txn(tmp_path)
        txn.stage_json(state, {"current_phase": 5})
        txn.commit()
        assert json.loads(state.read_text(encoding="utf-8"))["current_phase"] == 5

    def test_empty_commit_is_a_noop(self, tmp_path):
        txn = _txn(tmp_path)
        txn.commit()
        assert not txn.journal_path.exists()


class TestAbort:
    def test_abort_removes_staged_tmps(self, tmp_path):
        txn = _txn(tmp_path)
        target = tmp_path / ".methodology" / "state.json"
        txn.stage_json(target, {"current_phase": 5})
        txn.abort()
        assert not target.exists()
        assert list(tmp_path.rglob("*.txn.tmp")) == []

    def test_context_manager_aborts_on_exception(self, tmp_path):
        target = tmp_path / ".methodology" / "state.json"
        with pytest.raises(RuntimeError):
            with StateTransaction(tmp_path) as txn:
                txn.stage_json(target, {"current_phase": 5})
                raise RuntimeError("validation blew up")
        assert not target.exists()
        assert list(tmp_path.rglob("*.txn.tmp")) == []


class TestInterruptedCommit:
    def test_crash_mid_commit_leaves_journal_and_detectable_state(
        self, tmp_path, monkeypatch
    ):
        """Simulate a crash after the first rename: the journal must survive
        so doctor can report the interrupted transaction."""
        import core.atomic_io as aio

        txn = _txn(tmp_path)
        first = tmp_path / "HANDOVER.md"
        second = tmp_path / ".methodology" / "state.json"
        txn.stage_text(first, "# Handover\n")
        txn.stage_json(second, {"current_phase": 5})

        real_replace = aio.os.replace
        calls = {"n": 0}

        def exploding_replace(src, dst):
            # Call #1 is the journal's own atomic write; #2 = first staged
            # rename; #3 = second staged rename — crash there.
            calls["n"] += 1
            if calls["n"] == 3:
                raise OSError("disk detached mid-commit")
            return real_replace(src, dst)

        monkeypatch.setattr(aio.os, "replace", exploding_replace)
        with pytest.raises(OSError):
            txn.commit()
        monkeypatch.undo()

        # First file landed, second did not — the journal records the plan.
        assert first.exists()
        assert not second.exists()
        assert txn.journal_path.exists()
        journal = json.loads(txn.journal_path.read_text(encoding="utf-8"))
        targets = [entry["target"] for entry in journal["pending"]]
        assert str(second) in targets

    def test_journal_absent_after_clean_lifecycle(self, tmp_path):
        txn = _txn(tmp_path)
        txn.stage_json(tmp_path / ".methodology" / "state.json", {"current_phase": 2})
        txn.commit()
        assert not txn.journal_path.exists()
        assert list(tmp_path.rglob("*.txn.tmp")) == []
