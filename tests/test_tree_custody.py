"""The framework writes into the tree it judges, and nothing accounts for it (Round 53 站0).

Every one of this repository's guards is of one shape: read the judged
project's tree or its artifacts, and decide. Not one is of the other shape —
*the framework just modified that tree; who accounted for the modification?*

Mutation testing is the sharpest case because it is the only tool that edits
the delivered source on purpose. `core/quality_gate/source_tree_lock.py` says
so in its own docstring: mutmut "mutates ``paths_to_mutate`` files in place at
their real project path", and `cwd=workdir` "never isolated the mutated files
themselves". The mitigation shipped with that finding was a lock — which keeps
a *concurrent reader* from seeing a mutant, and says nothing about a mutation
window that never closes.

Station 0 measured the open window on a throwaway fixture. Kill `mutmut run`
mid-flight and the tree is left holding both halves:

    probeapp/calc.py        `return a * 2`  ->  `return a / 2`
    probeapp/calc.py.bak    the original, written by mutmut

That is byte-for-byte the shape taskq-super shipped. Commit `5535033`, whose
message is `release(P6): Gate4 PASS score=93.9 — pipeline complete`, contains

    -        "sqlite:///:memory:",
    +        "XXsqlite:///:memory:XX",

plus `rate_repo.py.bak`. `XX…XX` is mutmut's string-mutation signature. The
framework damaged the tree, and the framework's own milestone commit shipped
the damage.

What makes it worse than a stray edit is what happened next. The delivered
source is the only authority on intent an agent has (Round 51's root cause), so
the damage was read as design. The same release commit rewrote two tests to
assert it:

    ``TASKQ_RATE_DB_URL`` unset, ``_build_engine`` raises on the sentinel
    ``XXsqlite:///:memory:XX`` URL — that sentinel is the rate_repo's
    deliberate production-side "missing env" guard. The test now documents
    the failure as the intended behaviour …

and a later Phase 8 commit restored the mutant on purpose after someone had
fixed it, because by then a test required it.

Round 44 站1 and Round 38 站4 already forbid advancing on a tree the gate did
not measure — and did not catch this, because the reference point is taken
*after* the framework has written. The digest matched a tree that was already
corrupt. The invariant is right; it was anchored downstream of the framework's
own hand.
"""

from __future__ import annotations

from pathlib import Path

import pytest


def _fixture(tmp_path: Path) -> Path:
    project = tmp_path / "proj"
    (project / "pkg").mkdir(parents=True)
    (project / ".methodology").mkdir()
    (project / "pkg" / "mod.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    return project


def test_a_framework_write_into_the_delivered_tree_is_byte_restored(tmp_path: Path) -> None:
    """Whatever the framework writes into the judged tree, it puts back exactly.

    Station 0's measurement: `run_mutation_precheck` restores on the clean
    path and does not on the killed one. The custody window is what makes the
    two cases the same case — the restore lives in a `finally`, and the bytes
    are compared afterwards rather than assumed.
    """
    from core.tree_custody import custody

    project = _fixture(tmp_path)
    target = project / "pkg" / "mod.py"
    original = target.read_bytes()

    with custody(project, "mutation:src", paths=[target]):
        target.write_text("def f():\n    return 2\n", encoding="utf-8")

    assert target.read_bytes() == original, (
        "a transient framework write must leave the delivered tree byte-identical"
    )


def test_a_residue_the_framework_cannot_restore_halts_and_names_the_files(
    tmp_path: Path,
) -> None:
    """A tree the framework could not put back is a harness bug, not a warning.

    Round 13's taxonomy already has the exit code and the owner for this
    (`EX_HARNESS_BUG` = 70, owner `harness`) and the route to them: any
    exception that reaches `core/errors.py`'s crash boundary becomes exit 70
    with a `[HARNESS-BUG]` banner and a crash bundle. What was missing is
    anything that raises. The message must name the files, because Round 48's
    rule is that a finding a human cannot act on is not a finding.
    """
    from core.tree_custody import TreeCustodyResidue, custody

    project = _fixture(tmp_path)
    target = project / "pkg" / "mod.py"

    with pytest.raises(TreeCustodyResidue) as exc:
        with custody(project, "mutation:src", paths=[target]):
            target.write_text("mutant\n", encoding="utf-8")
            target.chmod(0o444)
            (project / "pkg").chmod(0o555)

    (project / "pkg").chmod(0o755)
    target.chmod(0o644)
    assert "pkg/mod.py" in str(exc.value), (
        "the halt must name the file it could not restore"
    )


def test_the_framework_may_not_commit_its_own_scratch(tmp_path: Path) -> None:
    """An open transient window is a refusal to commit, at the sites that stage everything.

    Station 0's premise P3 refuted the plan's assumption that
    `GitStrategy._commit` is the only commit site: there are seven. Five pass
    an explicit pathspec (`commit -- <file>`) and so cannot pick up a stray
    mutant; two commit the whole index. This is the predicate those two ask.
    """
    from core.tree_custody import assert_no_open_custody, open_custody_ids

    project = _fixture(tmp_path)
    target = project / "pkg" / "mod.py"

    from core.tree_custody import _mark_open  # window opened, process died

    _mark_open(project, "mutation:src", [target])
    assert "mutation:src" in open_custody_ids(project)

    reason = assert_no_open_custody(project)
    assert reason is not None and "mutation:src" in reason, (
        "a commit that stages the whole worktree must refuse while a "
        "framework write window is open"
    )
