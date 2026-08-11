"""A verdict's tree digest must not be invalidated by the act of recording it.

Round 44 站0. `record_verdict` computes `delivered_tree_digest(project)` and
then appends a line to `.methodology/gate_verify.jsonl` — a file inside the
delivered set. `has_matching_pass` re-derives the digest later, over a tree
that now includes that append. Commit `2245e64` (2026-08-11) diagnosed this
exactly and fixed it by naming two files in a `_DIGEST_EXCLUDE` frozenset:
the verdict ledger and the degradation ledger.

Two entries is not the shape of the problem. `core/utils/delivery_scope.py`'s
own docstring says why:

    A denylist is structurally one directory behind: `.venv/` was added after
    the first incident, `.claude/worktrees/` after the second …

Every `.methodology/` file the harness writes after taking the digest is the
next occurrence: `heartbeat.json`, `sessions_spawn.log`, `decision_logs/*`,
`lessons/*`, `state.json`'s own `last_update`. Measured on taskq-advance,
`verify-gate` ran three times in the six minutes before its P3→P4 advance,
each run recording a PASS against a different `delivered_tree_sha256` at one
unchanged `git_sha` — the operator re-running until the digest happened to
hold still.

The failure direction is benign (a stale digest refuses, it never passes), so
this is friction rather than an unsound verdict. It is friction on the exact
step that produced Round 44's real defect, which is why it goes first.

The fix is a declared set of harness-volatile paths in `delivery_scope`, plus
the behavioural test below: after a verdict is recorded and the harness has
gone on writing its own bookkeeping, the verdict still matches. A future
bookkeeping file that is not registered turns that test red at development
time instead of turning a project's verify-gate red at 3am.

Deliberately NOT "exclude `.methodology/` wholesale": station 0's premise 1
measured that `.methodology/harness_config.json` carries `crg_excludes` and
`crg_cohesion_healthy`, which change the architecture score directly and do
not travel into the verdict (only `cohesion_healthy` reaches
`calibration`). Excluding the whole directory would let a scoring input move
under a recorded PASS.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

pytestmark = [pytest.mark.core]


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True,
                   capture_output=True, text=True)


@pytest.fixture()
def git_project(tmp_path: Path) -> Path:
    """A real git repo — `iter_delivered_files` takes its git path there.

    The non-git path applies `SKIP_DIRS`, which hides `.methodology`
    altogether and makes every defect in this file invisible.
    """
    _git("init", "-q", cwd=tmp_path)
    _git("config", "user.email", "t@example.com", cwd=tmp_path)
    _git("config", "user.name", "t", cwd=tmp_path)
    (tmp_path / ".methodology").mkdir()
    (tmp_path / "mod.py").write_text("x = 1\n", encoding="utf-8")
    _git("add", "-A", cwd=tmp_path)
    _git("commit", "-q", "-m", "seed", cwd=tmp_path)
    return tmp_path


def _record_pass(project: Path) -> dict:
    from core.quality_gate.gate_verify import record_verdict
    return record_verdict(
        project, gate=2, phase=3,
        checks={"last_gate_ok": True, "spec_coverage_rc": 0, "crg_rc": 0},
        verdict="PASS",
    )


# ── the invariant ───────────────────────────────────────────────────────────

def test_a_verdict_matches_itself_immediately(git_project: Path) -> None:
    """The `2245e64` case, restated as the property rather than the patch."""
    from core.quality_gate.gate_verify import has_matching_pass

    _record_pass(git_project)

    ok, why = has_matching_pass(git_project, 2)
    assert ok, why


def test_a_verdict_survives_the_harness_writing_its_own_bookkeeping(
    git_project: Path,
) -> None:
    """Every one of these is written by the harness during a normal run,
    after a gate verdict has been recorded and before advance-phase asks for
    it. None of them is an input to any score."""
    from core.quality_gate.gate_verify import has_matching_pass

    _record_pass(git_project)

    meth = git_project / ".methodology"
    (meth / "heartbeat.json").write_text('{"ts": 2}', encoding="utf-8")
    (meth / "sessions_spawn.log").write_text("one dispatch\n", encoding="utf-8")
    (meth / "decision_logs" / "2026-08-11").mkdir(parents=True)
    (meth / "decision_logs" / "2026-08-11" / "GATE_2_abc.yaml").write_text(
        "verdict: PASS\n", encoding="utf-8")
    (meth / "lessons").mkdir()
    (meth / "lessons" / "deadbeef.md").write_text("**Failure:** x\n",
                                                  encoding="utf-8")
    (meth / "state.json").write_text(
        json.dumps({"current_phase": 3, "last_update": "later"}),
        encoding="utf-8")

    ok, why = has_matching_pass(git_project, 2)
    assert ok, (
        "the harness's own bookkeeping invalidated the verdict it had just "
        f"recorded — {why}"
    )


def test_a_changed_deliverable_still_invalidates_the_verdict(
    git_project: Path,
) -> None:
    """Negative control. Round 38 站4's whole point must survive this round:
    a PASS measured on a different tree is not a PASS for this one."""
    from core.quality_gate.gate_verify import has_matching_pass

    _record_pass(git_project)
    (git_project / "mod.py").write_text("x = 2\n", encoding="utf-8")

    ok, why = has_matching_pass(git_project, 2)
    assert not ok
    assert "different tree" in why


def test_a_scoring_input_under_methodology_still_invalidates_the_verdict(
    git_project: Path,
) -> None:
    """Station 0 premise 1, as a guard.

    `crg_excludes` in `.methodology/harness_config.json` decides which files
    the architecture score is measured over (core/harness_config.py:317). It
    does not travel into the gate result — only `cohesion_healthy` reaches
    `calibration`. So it must stay inside the digest even though it lives
    under `.methodology/`, and "exclude the directory" is not the fix.
    """
    from core.quality_gate.gate_verify import has_matching_pass

    cfg = git_project / ".methodology" / "harness_config.json"
    cfg.write_text(json.dumps({"version": 1, "crg_excludes": []}),
                   encoding="utf-8")
    _record_pass(git_project)

    cfg.write_text(
        json.dumps({"version": 1, "crg_excludes": ["03-development/*"]}),
        encoding="utf-8")

    ok, _why = has_matching_pass(git_project, 2)
    assert not ok, (
        "the project narrowed what the architecture score is measured over "
        "and the recorded PASS still claimed to be about this tree"
    )


def test_the_two_ledgers_that_started_this_are_registered_volatile() -> None:
    """Parity, not a second copy of the path (R27 站4).

    `delivery_scope` sits below `quality_gate` and cannot import from it, so
    the two ledger paths appear as literals in `HARNESS_VOLATILE_PATHS`. This
    keeps them tied to the constants their owners declare — the exact pair
    `2245e64` had to name by hand.
    """
    from core.degradation_ledger import LEDGER_RELPATH as _DEGRADATIONS
    from core.quality_gate.gate_verify import _LEDGER_RELPATH as _VERDICTS
    from core.utils.delivery_scope import HARNESS_VOLATILE_PATHS

    assert _VERDICTS in HARNESS_VOLATILE_PATHS
    assert _DEGRADATIONS in HARNESS_VOLATILE_PATHS


# ── the two digests answer two questions and must agree when they overlap ───

def test_the_committed_and_delivered_digests_agree_on_a_clean_tree(
    git_project: Path,
) -> None:
    """Safety net for `committed_tree_digest`.

    It reads git objects; `delivered_tree_digest` reads the working tree.
    Line endings, gitlinks and symlinks are three ways those can disagree
    while both look right. On a clean tree they must be byte-identical, or
    the milestone check in `cmd_advance_phase` compares two different rulers.
    """
    from core.utils.delivery_scope import (
        committed_tree_digest, delivered_tree_digest,
    )

    assert committed_tree_digest(git_project, "HEAD") == delivered_tree_digest(
        git_project
    )


def test_the_committed_digest_ignores_the_working_tree(
    git_project: Path,
) -> None:
    """That is the whole point: it answers "which version did git record"."""
    from core.utils.delivery_scope import committed_tree_digest

    before = committed_tree_digest(git_project, "HEAD")
    (git_project / "mod.py").write_text("x = 999\n", encoding="utf-8")

    assert committed_tree_digest(git_project, "HEAD") == before


def test_the_committed_digest_of_a_missing_rev_is_empty_not_a_crash(
    git_project: Path,
) -> None:
    """Round 32/35: could-not-measure is not a finding. A caller that gets
    `""` must be able to tell that apart from a real digest."""
    from core.utils.delivery_scope import committed_tree_digest

    assert committed_tree_digest(git_project, "0" * 40) == ""
