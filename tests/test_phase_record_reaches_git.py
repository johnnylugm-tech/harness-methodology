"""The record has to be in the commit, not only in the working tree.

Round 90. taskq-redo's CI went red on `gate-check`:

    [ENTRY GATE FAILED] Gate 4 — state.json.phase_completed[8] is absent

Its working tree holds `phase_completed` 1..8. The commit CI ran on —
`f354734 handover: advance to Phase 9`, which is also HEAD — holds 1..7.

WHY THE ENTRY IS NEVER IN THE COMMIT THAT COMPLETES ITS PHASE

`_advance_step_commit_and_push` makes the handover commit first and only then
writes an entry whose `sha` is HEAD *after* it. So the value lands in the
working tree and its only route into git is some LATER commit picking it up.
The pipeline ends at P8, so the last entry usually has no later commit.

Measured over the seven corpus projects that reached P9 with a P8 entry:

    taskq-plus     entered git by a HUMAN commit — "chore(state): commit
                   phase 8 entry written after prior advance-phase commit"
    taskq-cc       entered git by a HUMAN commit — "chore(harness): commit
                   advance-phase phase_completed[8] ride-along"
    taskq-renew    carried in by an unrelated `chore: bump harness submodule`
    taskq-advance  not in git
    taskq-api      not in git
    taskq-redo     not in git   <- the CI failure above
    taskq-super    not in git

Two of the three that made it were repaired by hand, a month apart, with
commit messages that name this defect exactly. Only a person or a coincidence
gets the entry into git.

WHY THESE TESTS READ `git show` AND NOT THE WORKING TREE

Round 88 measured this window and called it harmless — 67 of 73 entries reach
git on a later commit — because every consumer reads the working tree through
`load_state`. That was true of every consumer it checked, and all of them were
local. CI checks out a commit. Round 89's read-back has the same blind spot:
it loads the working tree, finds the entry, and passes. So the assertions here
go through `git show HEAD:.methodology/state.json` deliberately.
"""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

import pytest

pytestmark = [pytest.mark.core]

CORPUS = Path("/Users/johnny/projects")


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, check=False)


def _committed_state(repo: Path, rev: str = "HEAD") -> dict:
    """state.json as the given commit holds it — what a CI checkout would see."""
    r = _git(repo, "show", f"{rev}:.methodology/state.json")
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


@pytest.fixture
def advance_project(tmp_path, monkeypatch):
    """Same shape as tests/test_phase_completed_authority.py's fixture."""
    from cli import phase_cmds

    proj = tmp_path / "proj"
    proj.mkdir()
    _git(proj, "init")
    _git(proj, "config", "user.email", "t@example.com")
    _git(proj, "config", "user.name", "t")
    _git(proj, "config", "core.hooksPath", ".git/hooks")
    meth = proj / ".methodology"
    meth.mkdir()
    (meth / "state.json").write_text(
        json.dumps({"state": "RUNNING", "current_phase": 1}) + "\n", encoding="utf-8")
    (proj / "CLAUDE.md").write_text("# Project\n", encoding="utf-8")
    _git(proj, "add", "-A")
    assert _git(proj, "commit", "-m", "baseline").returncode == 0

    monkeypatch.setattr(phase_cmds, "_advance_prechecks", lambda *_a, **_k: 0)
    monkeypatch.setattr(
        phase_cmds, "_verify_entry_gate",
        lambda *_a, **_k: {"passed": True, "gate": "stub", "reason": "test stub"})
    monkeypatch.setattr(
        "core.phase_hooks.PhaseHooks.preview_next_phase_blocking",
        lambda _self, _next_phase: [])
    monkeypatch.delenv("HARNESS_NO_GIT", raising=False)
    return proj


def _advance(proj: Path, completed: int = 1, push: bool = False) -> int:
    from cli import phase_cmds

    return phase_cmds.cmd_advance_phase(argparse.Namespace(
        completed_phase=completed, project=str(proj), push=push,
    ))


# ── the defect ───────────────────────────────────────────────────────────


def test_the_record_is_in_the_commit_not_only_the_working_tree(advance_project):
    """The reproduction. Before Round 90 the working tree had it and git did not."""
    proj = advance_project
    assert _advance(proj) == 0

    working = json.loads((proj / ".methodology" / "state.json").read_text(encoding="utf-8"))
    assert "1" in (working.get("phase_completed") or {}), "fixture no longer reproduces"

    committed = _committed_state(proj)
    assert "1" in (committed.get("phase_completed") or {}), (
        "phase_completed[1] is in the working tree and not in any commit — a CI "
        "checkout reads the commit, which is how taskq-redo's Gate 4 entry gate "
        "failed on a project whose working tree was complete"
    )


def test_the_committed_record_names_a_commit_that_exists(advance_project):
    """Committing it must not change what it says.

    The `sha` stays the handover commit's own
    (`test_phase_completed_authority.py` pins that), so it is the parent of
    whatever commit carries the record — still an ancestor of HEAD, which is
    the only contract its consumers have.
    """
    proj = advance_project
    assert _advance(proj) == 0
    entry = _committed_state(proj)["phase_completed"]["1"]
    assert _git(proj, "merge-base", "--is-ancestor", entry["sha"], "HEAD").returncode == 0
    assert _git(proj, "cat-file", "-e", entry["sha"]).returncode == 0


def test_a_second_advance_does_not_fail_on_an_unchanged_state(advance_project):
    """Nothing to commit is not a failure.

    Re-running an advance that already recorded its phase leaves state.json
    byte-identical, and `git commit` exits non-zero on an empty index. That is
    the normal case for a re-run, not an error.
    """
    proj = advance_project
    assert _advance(proj) == 0
    head_after_first = _git(proj, "rev-parse", "HEAD").stdout.strip()
    assert _advance(proj) == 0, "a repeated advance must stay idempotent"
    assert "1" in (_committed_state(proj).get("phase_completed") or {})
    assert head_after_first, "sanity"


# ── it must not disturb the paths that cannot commit ─────────────────────


def test_no_git_is_unchanged(advance_project, monkeypatch):
    """`HARNESS_NO_GIT=1` commits nothing, before and after this round."""
    monkeypatch.setenv("HARNESS_NO_GIT", "1")
    proj = advance_project
    before = _git(proj, "rev-parse", "HEAD").stdout.strip()
    assert _advance(proj) == 0
    assert _git(proj, "rev-parse", "HEAD").stdout.strip() == before, (
        "HARNESS_NO_GIT=1 must not produce a commit")


def test_the_record_commit_happens_before_the_push() -> None:
    """A commit made after the push is a commit the push did not carry.

    Source order, because the alternative is a fixture with a real remote.
    `--push` is opt-in (Round 23 站1) and it is the only thing in this function
    that publishes; a record committed after it would sit locally and CI would
    read the commit before it — exactly the state this round is fixing.
    """
    import ast

    src = (Path(__file__).resolve().parents[1] / "cli" / "advance_steps.py")
    text = src.read_text(encoding="utf-8")
    tree = ast.parse(text)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_advance_step_commit_and_push")

    record_commit_lines = [
        n.lineno for n in ast.walk(fn)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
        and n.value.startswith("chore(state): record phase")
    ]
    assert record_commit_lines, (
        "the record-commit is gone — advance-phase no longer puts "
        "phase_completed into a commit"
    )
    push_lines = [
        n.lineno for n in ast.walk(fn)
        if isinstance(n, ast.Constant) and n.value == "push"
    ]
    assert push_lines, "the --push branch is gone; re-derive this guard"
    assert max(record_commit_lines) < min(push_lines), (
        f"the record is committed at line {max(record_commit_lines)}, after the "
        f"push at line {min(push_lines)} — that push cannot carry it"
    )


# ── the corpus measurement that found this ───────────────────────────────


def test_the_corpus_still_shows_who_paid_for_this() -> None:
    """Four of seven completed projects have their last entry only locally.

    Kept executable so the next round can see the number move. It is expected
    to stay at four until those projects re-run an advance — this round fixes
    what happens next, not what already happened. A drop to zero means the
    corpus was repaired; a rise means the fix did not take.
    """
    if not (CORPUS / "taskq-cc" / ".git").is_dir():
        pytest.skip("corpus projects not present on this machine")

    missing = []
    for project in sorted(CORPUS.iterdir()):
        state_path = project / ".methodology" / "state.json"
        if not (project / ".git").is_dir() or not state_path.is_file():
            continue
        try:
            live = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if live.get("current_phase") != 9:
            continue
        entries = live.get("phase_completed") or {}
        if not entries:
            continue
        last = max(entries, key=int)
        r = _git(project, "show", "HEAD:.methodology/state.json")
        if r.returncode != 0:
            continue
        if last not in (json.loads(r.stdout).get("phase_completed") or {}):
            missing.append(f"{project.name}/P{last}")

    assert len(missing) <= 4, (
        f"more projects now carry an uncommitted final record than when Round 90 "
        f"measured four: {missing}"
    )
