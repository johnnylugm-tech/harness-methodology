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


def _commit_head(repo: Path, *commit_args: str) -> str:
    """git commit prints 'create mode' lines etc., so .stdout's last line
    is NOT the SHA. Capture it via `git rev-parse HEAD` after the commit."""
    r = _git(repo, "commit", *commit_args)
    assert r.returncode == 0, f"commit failed: {r.stderr}"
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


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


# =============================================================================
# Read-time self-heal of dangling phase_completed[N].sha
# =============================================================================
#
# Confirmed repro (taskq-api 2026-08-05):
#   push-checkpoint captured _pre_push_sha = d061387 at 16:28:14 UTC, then
#   the orchestrator ran `git reset HEAD~2` before `git add && git commit`
#   landed, so commit 3836985 (parent 4355bb3) carried state.json with
#   phase_completed[2].sha = d061387 — now an unreachable commit. The
#   recovery helper searches HEAD-reachable history for the phase marker
#   and atomic-writes the repair.


@pytest.fixture
def dangling_sha_project(tmp_path):
    """Reproduce the exact taskq-api P2 shape:
       baseline → orphan checkpoint → reset HEAD~2 → replacement checkpoint.
       state.json records the orphan's SHA as phase_completed[2].sha.
    """
    from core.quality_gate import phase_completed_recovery as pcr

    proj = tmp_path / "proj"
    proj.mkdir()
    _git(proj, "init")
    _git(proj, "config", "user.email", "t@example.com")
    _git(proj, "config", "user.name", "t")
    _git(proj, "config", "commit.gpgsign", "false")
    meth = proj / ".methodology"
    meth.mkdir()
    (meth / "state.json").write_text(json.dumps({"phase_completed": {}}) + "\n")
    _git(proj, "add", "-A")
    baseline = _commit_head(proj, "-m", "baseline")

    # Orphan checkpoint — captured by state.json then reset away.
    (proj / "p2-orphan.txt").write_text("orphan\n")
    _git(proj, "add", "-A")
    orphan_sha = _commit_head(
        proj, "-m", "phase2(review-complete): SAD + ADR + quality manifest"
    )

    # Replacement checkpoint on the same baseline (simulates reset+recommit).
    _git(proj, "reset", "--hard", baseline)
    (proj / "p2-replacement.txt").write_text("replacement\n")
    _git(proj, "add", "-A")
    replacement_sha = _commit_head(
        proj, "-m", "phase2(review-complete): SAD + ADR + quality manifest"
    )

    state = {
        "phase_completed": {
            "2": {
                "sha": orphan_sha,
                "timestamp": "2026-08-05T16:28:14.674115+00:00",
                "enforcer_sha": "c09fae1f7443a570d7434888bb5fae8fc3591c80",
                "enforcer_surface": {
                    "core/quality_gate": "b8c8d62c0b7bc584024b3c10afba5bbdc017206b",
                },
            }
        },
        "phase_truth_passed": True,
    }
    (meth / "state.json").write_text(json.dumps(state, indent=2) + "\n")
    _git(proj, "add", "-A")
    _commit_head(proj, "-m", "carry dangling state.json")

    return pcr, proj, orphan_sha, replacement_sha


def test_recovery_finds_reachable_replacement_sibling(dangling_sha_project):
    """The exact taskq-api repro: orphan SHA, reset-away, replacement on
    same baseline. Recovery must pick the reachable replacement, not the
    unreachable orphan."""
    pcr, proj, _orphan_sha, replacement_sha = dangling_sha_project

    result = pcr.try_recover_dangling_phase_completed(proj, 2, _orphan_sha)
    assert result is not None, "recovery must succeed for reachable marker"
    assert result["to_sha"] == replacement_sha
    assert result["from_sha"] == _orphan_sha
    assert result["observed_head"] == _git(proj, "rev-parse", "HEAD").stdout.strip()
    assert result["phase"] == 2
    assert result["marker"] == "phase2(review-complete)"
    assert result["already_healed"] is False


def test_recovery_atomic_write_keeps_state_invariants(dangling_sha_project):
    """After recovery, the recorded SHA must satisfy the only consumer
    contract: `git merge-base --is-ancestor <sha> HEAD`."""
    pcr, proj, orphan_sha = dangling_sha_project[:3]
    pcr.try_recover_dangling_phase_completed(proj, 2, orphan_sha)

    state = json.loads((proj / ".methodology" / "state.json").read_text())
    entry = state["phase_completed"]["2"]
    rc = _git(
        proj, "merge-base", "--is-ancestor", entry["sha"], "HEAD"
    ).returncode
    assert rc == 0, "recorded SHA must now be an ancestor of HEAD"
    assert entry["recovered_from_sha"] == orphan_sha
    assert "recovered_at" in entry
    assert entry["enforcer_sha"]  # preserved


def test_recovery_appends_to_top_level_log(dangling_sha_project):
    """The Plan-agent-verified audit-durability contract: cmd_advance_phase
    replaces phase_completed[N] wholesale, but the top-level
    phase_completed_recovery_log is independent and survives."""
    pcr, proj, orphan_sha = dangling_sha_project[:3]
    pcr.try_recover_dangling_phase_completed(proj, 2, orphan_sha)

    state = json.loads((proj / ".methodology" / "state.json").read_text())
    log = state.get("phase_completed_recovery_log")
    assert isinstance(log, list) and len(log) == 1
    event = log[0]
    assert event["phase"] == 2
    assert event["from_sha"] == orphan_sha
    assert event["observed_head"] == _git(proj, "rev-parse", "HEAD").stdout.strip()
    assert "at" in event
    assert event["marker"] == "phase2(review-complete)"


def test_recovery_is_idempotent(dangling_sha_project):
    """Second call must NOT append a duplicate audit event when the entry
    already points at the healed SHA."""
    pcr, proj, orphan_sha = dangling_sha_project[:3]
    pcr.try_recover_dangling_phase_completed(proj, 2, orphan_sha)
    pcr.try_recover_dangling_phase_completed(proj, 2, orphan_sha)

    state = json.loads((proj / ".methodology" / "state.json").read_text())
    log = state.get("phase_completed_recovery_log", [])
    assert len(log) == 1, "idempotent recovery must not append duplicate audit events"


def test_recovery_returns_none_when_no_marker_present(tmp_path):
    """If no `phase{prev}(review-complete)` commit exists in HEAD-reachable
    history, the caller's gate must still hard-fail — recovery returns None."""
    from core.quality_gate import phase_completed_recovery as pcr

    proj = tmp_path / "proj"
    proj.mkdir()
    _git(proj, "init")
    _git(proj, "config", "user.email", "t@example.com")
    _git(proj, "config", "user.name", "t")
    (proj / "f.txt").write_text("x")
    _git(proj, "add", "-A")
    _git(proj, "commit", "-m", "baseline with no phase markers at all")

    (proj / ".methodology").mkdir()
    (proj / ".methodology" / "state.json").write_text(json.dumps(
        {"phase_completed": {"2": {"sha": "f" * 40, "timestamp": "2026-01-01T00:00:00+00:00"}}}
    ))
    result = pcr.try_recover_dangling_phase_completed(proj, 2, "f" * 40)
    assert result is None


def test_recovery_does_not_match_body_only_marker(tmp_path):
    """`git log --grep` matches subject by default. A marker that appears
    only in the commit body must NOT trigger recovery."""
    from core.quality_gate import phase_completed_recovery as pcr

    proj = tmp_path / "proj"
    proj.mkdir()
    _git(proj, "init")
    _git(proj, "config", "user.email", "t@example.com")
    _git(proj, "config", "user.name", "t")
    (proj / "f.txt").write_text("x")
    _git(proj, "add", "-A")
    _commit_head(proj, "-m", "baseline", "-m",
                 "this body mentions phase2(review-complete) but not the subject")

    (proj / ".methodology").mkdir()
    (proj / ".methodology" / "state.json").write_text(json.dumps(
        {"phase_completed": {"2": {"sha": "f" * 40, "timestamp": "2026-01-01T00:00:00+00:00"}}}
    ))
    result = pcr.try_recover_dangling_phase_completed(proj, 2, "f" * 40)
    assert result is None


def test_recovery_does_not_match_orphan_sibling(tmp_path):
    """A commit on a reset-away branch must NOT match — `git log <head>`
    (no --all) restricts the search to current HEAD reachability."""
    from core.quality_gate import phase_completed_recovery as pcr

    proj = tmp_path / "proj"
    proj.mkdir()
    _git(proj, "init")
    _git(proj, "config", "user.email", "t@example.com")
    _git(proj, "config", "user.name", "t")
    (proj / "f.txt").write_text("x")
    _git(proj, "add", "-A")
    baseline = _commit_head(proj, "-m", "baseline")

    # Create an orphan-sibling commit on the current branch.
    (proj / "g.txt").write_text("orphan branch state")
    _git(proj, "add", "-A")
    orphan_sha = _commit_head(
        proj, "-m", "phase2(review-complete): orphan on current branch"
    )

    # Reset back to baseline so the orphan marker is no longer reachable.
    _git(proj, "reset", "--hard", baseline)
    (proj / "f.txt").write_text("after reset")

    (proj / ".methodology").mkdir()
    (proj / ".methodology" / "state.json").write_text(json.dumps(
        {"phase_completed": {"2": {"sha": orphan_sha, "timestamp": "2026-01-01T00:00:00+00:00"}}}
    ))
    result = pcr.try_recover_dangling_phase_completed(proj, 2, orphan_sha)
    assert result is None, "an unreachable orphan must not be returned"

