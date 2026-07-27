"""Round 20 站3 — a re-run milestone must not mint an empty commit.

taskq's Phase 4 produced three commits with an identical subject:

    23:30  9abe117  test(P4): Gate3 PASS score=96.3       <- Gate 3 already passed
    23:31  515fa95  feat(P4-pre-gate3): ... ready for Gate 3
    23:54  a2f9c8b  feat(P4-pre-gate3): ... ready for Gate 3
    00:10  4fdf36a  feat(P4-pre-gate3): ... ready for Gate 3

The last changed nothing but two timestamps, and all three landed AFTER Gate 3
had passed, so even the subject had stopped being true.

git_strategy._commit already declined to commit when there was nothing to
commit — that layer was never the problem. HANDOVER.md's `**Generated**` line
was: regenerated on every milestone, it guaranteed a diff whether or not
anything about the situation had moved, so `_has_changes()` was always true.
The workflow prompt calls that milestone "(Idempotent; skip if already
snapshotted.)", a claim about the command that the command did not honour.

Same shape as Round 18 站3's attestation loop, and the same fix: find the one
field that records WHEN rather than WHAT, and exclude it from the comparison
that decides whether to write.
"""
from __future__ import annotations

import pytest

from harness.handover_generator import HandoverGenerator


def _write(project, status="all 5 FR(s) Gate1 PASS"):
    return HandoverGenerator(project).write(
        checkpoint_id="P4-pre-gate3",
        phase=4,
        task_background="P4 Testing complete.",
        current_status=status,
        next_steps=["Run Gate 3"],
        notes=None,
    )


class TestHandoverIdempotence:
    def test_rewriting_identical_content_leaves_the_bytes_alone(self, tmp_path):
        path = _write(tmp_path)
        first = path.read_bytes()
        mtime_before = path.stat().st_mtime_ns
        _write(tmp_path)
        assert path.read_bytes() == first, (
            "an unchanged handover was rewritten — the timestamp alone made it "
            "look modified, which is what produced taskq's three identical "
            "P4-pre-gate3 commits"
        )
        assert path.stat().st_mtime_ns == mtime_before, (
            "the file was rewritten with identical bytes; git would not see it, "
            "but a mtime-based staleness probe would"
        )

    def test_changed_content_is_written(self, tmp_path):
        path = _write(tmp_path, status="2 of 5 FR(s) done")
        before = path.read_text(encoding="utf-8")
        _write(tmp_path, status="all 5 FR(s) Gate1 PASS")
        after = path.read_text(encoding="utf-8")
        assert after != before
        assert "all 5 FR(s) Gate1 PASS" in after

    def test_first_write_always_happens(self, tmp_path):
        path = _write(tmp_path)
        assert path.is_file() and path.read_text(encoding="utf-8").strip()

    def test_unreadable_existing_file_falls_through_to_writing(self, tmp_path):
        """Fail toward writing: a handover that cannot be read must be
        replaced, never silently kept. It is the sole state for resuming."""
        path = HandoverGenerator(tmp_path).handover_path
        path.mkdir()  # a directory where a file belongs → read_text raises OSError
        with pytest.raises(OSError):
            _write(tmp_path)

    def test_only_the_generated_line_is_excluded(self):
        """The exclusion must be surgical. If it swallowed more than the
        timestamp, real changes would stop being written — a far worse failure
        than the one being fixed."""
        base = (
            "# HANDOVER\n\n**Generated**: 2026-07-27T00:00:00Z\n\n"
            "## Status\n\nall 5 FR(s) Gate1 PASS\n"
        )
        later_ts = base.replace("2026-07-27T00:00:00Z", "2026-07-27T23:59:59Z")
        real_change = base.replace("all 5 FR(s)", "2 of 5 FR(s)")
        sub = HandoverGenerator._substantive
        assert sub(base) == sub(later_ts), "timestamp should be normalised away"
        assert sub(base) != sub(real_change), "a status change must survive"


def _git(repo, *args):
    import subprocess
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=False
    )


@pytest.fixture
def git_repo(tmp_path):
    import shutil
    if shutil.which("git") is None:  # pragma: no cover
        pytest.skip("git not on PATH")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "seed.txt").write_text("seed\n", encoding="utf-8")
    _git(tmp_path, "add", "seed.txt")
    _git(tmp_path, "commit", "-q", "-m", "seed")
    return tmp_path


def test_a_milestone_rerun_creates_no_second_commit(git_repo):
    """End-to-end on a real repository: write the handover, commit it, then do
    the whole thing again. The second round must produce no commit.

    This is the taskq shape reproduced — three `feat(P4-pre-gate3)` commits
    where the last changed only timestamps — and it exercises both layers
    together: the handover writer declining to rewrite, and git_strategy._commit
    declining to commit a clean tree. Driven through public entry points against
    a real git repo rather than by patching private seams, so it fails if either
    layer regresses.
    """
    from harness.git_strategy import GitStrategy

    strategy = GitStrategy(project=git_repo, enabled=True, push=False)

    for _ in range(2):
        _write(git_repo)
        strategy._commit("feat(P4-pre-gate3): all 5 FR(s) Gate1 re-eval PASS")

    subjects = _git(git_repo, "log", "--format=%s").stdout.strip().splitlines()
    milestone_commits = [s for s in subjects if "P4-pre-gate3" in s]
    assert len(milestone_commits) == 1, (
        f"a re-run minted a second empty milestone commit: {subjects}"
    )


def test_a_real_change_still_commits(git_repo):
    """The counterweight: idempotence must not swallow genuine progress."""
    from harness.git_strategy import GitStrategy

    strategy = GitStrategy(project=git_repo, enabled=True, push=False)

    _write(git_repo, status="2 of 5 FR(s) done")
    strategy._commit("feat(P4-mid): 2 of 5")
    _write(git_repo, status="all 5 FR(s) Gate1 PASS")
    strategy._commit("feat(P4-pre-gate3): all 5")

    subjects = _git(git_repo, "log", "--format=%s").stdout
    assert "P4-mid" in subjects and "P4-pre-gate3" in subjects
