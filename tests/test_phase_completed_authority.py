"""Round 24 站4 — advance-phase is the authority on "phase N is complete".

`state.json.phase_completed` had exactly one writer:
`cli/push_cmds.py::cmd_push_checkpoint`, which the generated workflows invoke
for P1 and P2 only. Every project therefore ended up with
`phase_completed == {1, 2}` regardless of how far it got — confirmed on the
run-all-by-workflow P1-P8 run, which reached Phase 9 carrying exactly those
two entries.

The consequence was silent and specific. `cli/fr_cmds.py`'s
`_fr_step_lineage_boundary` reads `phase_completed[phase-1]` to scope an
idempotency grep to the current phase's lineage; with no entry it returns None
and callers fall back to an UNSCOPED grep — which is the precise bug the
2026-07-11 fix was written to remove (after `git reset --hard`, a stale
`refactor(FR-02): IMPROVE` commit from a reset-away lineage still matched, so
the step was skipped as already-done). That fix could only ever work for phase
3, where `phase-1 == 2` happens to have an entry. Its docstring attributed the
gap to "projects without reset history", which is not what was happening.

advance-phase now records `phase_completed[N]` after its handover commit
lands. push-checkpoint keeps its own write (idempotent, P1/P2 unchanged).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

pytestmark = [pytest.mark.core]

REPO = Path(__file__).resolve().parent.parent


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True,
    )


@pytest.fixture
def advance_project(tmp_path, monkeypatch):
    """Clean tmp git repo one advance away from Phase 2, prechecks stubbed.

    Same shape as tests/test_advance_commit_rollback.py's fixture — that suite
    already proved this is the minimal path through cmd_advance_phase that
    actually reaches the git commit block.
    """
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
        json.dumps({"state": "RUNNING", "current_phase": 1}) + "\n"
    )
    (proj / "CLAUDE.md").write_text("# Project\n")
    _git(proj, "add", "-A")
    assert _git(proj, "commit", "-m", "baseline").returncode == 0
    monkeypatch.setattr(phase_cmds, "_advance_prechecks", lambda *_a, **_k: 0)
    monkeypatch.delenv("HARNESS_NO_GIT", raising=False)
    return proj


def _advance(proj: Path, completed: int = 1) -> int:
    import argparse

    from cli import phase_cmds

    return phase_cmds.cmd_advance_phase(argparse.Namespace(
        completed_phase=completed, project=str(proj), push=False,
    ))


def test_advance_phase_records_phase_completed_with_an_ancestor_sha(advance_project):
    """Live call. The recorded SHA must satisfy the only consumer contract
    there is: `git merge-base --is-ancestor <sha> HEAD` (harness_cli CI and
    _verify_entry_gate)."""
    proj = advance_project
    assert _advance(proj) == 0

    state = json.loads((proj / ".methodology" / "state.json").read_text(encoding="utf-8"))
    entry = state["phase_completed"]["1"]
    assert entry["timestamp"].endswith("+00:00"), "Round 24 站3 time base"
    rc = subprocess.run(
        ["git", "-C", str(proj), "merge-base", "--is-ancestor", entry["sha"], "HEAD"],
        capture_output=True,
    ).returncode
    assert rc == 0, "recorded SHA must be an ancestor of HEAD"
    assert entry["sha"] == _git(proj, "rev-parse", "HEAD").stdout.strip(), (
        "advance-phase records the handover commit itself; push-checkpoint records "
        "its PRE-push HEAD. Both satisfy is-ancestor, and each names the repo "
        "state at which that phase completed."
    )


def test_advance_phase_does_not_clobber_earlier_phase_entries(advance_project):
    proj = advance_project
    state_path = proj / ".methodology" / "state.json"
    sd = json.loads(state_path.read_text(encoding="utf-8"))
    sd["phase_completed"] = {"0": {"sha": "f" * 40, "timestamp": "2026-01-01T00:00:00+00:00"}}
    state_path.write_text(json.dumps(sd))
    _git(proj, "add", "-A")
    _git(proj, "commit", "-m", "seed phase_completed")

    assert _advance(proj) == 0
    after = json.loads(state_path.read_text(encoding="utf-8"))["phase_completed"]
    assert after["0"]["sha"] == "f" * 40
    assert "1" in after


def test_rolled_back_advance_records_no_phase_completed(advance_project):
    """A commit that never landed must not leave a phase_completed entry —
    the same split-brain rule the B1 rollback enforces for current_phase."""
    proj = advance_project
    hooks = proj / ".git" / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    hook = hooks / "pre-commit"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)

    assert _advance(proj) == 6
    state = json.loads((proj / ".methodology" / "state.json").read_text(encoding="utf-8"))
    assert "1" not in state.get("phase_completed", {})
    assert state["current_phase"] == 1


def test_advance_phase_source_records_phase_completed():
    """Source-level pin: the write must live in cmd_advance_phase, after the
    commit block. A phase_completed written by push-checkpoint alone is what
    produced the {1, 2}-only maps."""
    import inspect

    from cli.phase_cmds import cmd_advance_phase

    src = inspect.getsource(cmd_advance_phase)
    assert 'setdefault("phase_completed", {})' in src, (
        "cmd_advance_phase must record phase_completed[N] — it is the command "
        "that verifies the exit gate and writes the handover commit"
    )
    assert src.index("git\", \"-C\", str(project), \"commit\"") < src.index(
        'setdefault("phase_completed", {})'
    ), "phase_completed must be recorded AFTER the handover commit, not before"


def test_lineage_boundary_now_resolves_past_phase_three(tmp_path):
    """_fr_step_lineage_boundary returned None for every phase >= 4 because
    only {1, 2} were ever recorded."""
    from cli.fr_cmds import _fr_step_lineage_boundary

    (tmp_path / ".methodology").mkdir()
    (tmp_path / ".methodology" / "state.json").write_text(
        json.dumps({"phase_completed": {
            "1": {"sha": "a" * 40}, "2": {"sha": "b" * 40},
        }}), encoding="utf-8"
    )
    # Pre-station reality: phases 4+ unresolvable.
    assert _fr_step_lineage_boundary(tmp_path, 5) is None

    (tmp_path / ".methodology" / "state.json").write_text(
        json.dumps({"phase_completed": {
            "1": {"sha": "a" * 40}, "2": {"sha": "b" * 40},
            "3": {"sha": "c" * 40}, "4": {"sha": "d" * 40},
        }}), encoding="utf-8"
    )
    assert _fr_step_lineage_boundary(tmp_path, 5) == "d" * 40


def test_push_checkpoint_no_longer_writes_the_zero_consumer_fields():
    """Round 24 站4b. last_push_checkpoint / last_push_checkpoint_phase had one
    writer and zero readers across cli/, core/, scripts/, harness/ and the
    generated workflow JS.

    The scan behind this claim explicitly covers harness/ — the R21-D' scan
    that concluded gate_score_overrides had "zero consumers" did not, and
    missed harness/harness_bridge.py:2281 where it is applied as a threshold
    floor. That retraction is recorded in docs/PROPOSAL_ADJUDICATIONS.md.
    """
    import inspect

    from cli import push_cmds

    src = inspect.getsource(push_cmds)
    # Direct: no assignment of either field survives anywhere in the module.
    assert '_state_data["last_push_checkpoint"]' not in src
    assert '_state_data["last_push_checkpoint_phase"]' not in src


def test_zero_consumer_scan_covers_the_harness_directory():
    """Meta-guard for 站4b-3: any 'zero consumers' claim must be made with a
    scan that reads harness/ too. Pins that the two removed fields really have
    no reader there, and would fire if one appeared."""
    hits = []
    for d in ("cli", "core", "scripts", "harness", "detection"):
        root = REPO / d
        if not root.is_dir():
            continue
        for path in root.rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            for field in ("last_push_checkpoint_phase", "last_push_checkpoint"):
                if field in text and "Round 24 站4b" not in text:
                    hits.append(f"{path.relative_to(REPO)}: {field}")
    assert not hits, (
        "a removed zero-consumer field reappeared, or a reader was added without "
        "restoring the writer:\n  " + "\n  ".join(hits)
    )
