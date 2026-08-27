"""Round 31 站3 — the resume that was promised for a round and never happened.

`compute_mutation_score`'s TimeoutExpired branch has said since Bug v26:

    "(partial cache (N bytes) published to .mutmut-cache; next run will resume)"

It could not. Every call opens a fresh `tempfile.mkdtemp` workdir and runs
mutmut with `cwd=workdir`; mutmut 2.x reads `.mutmut-cache` from its cwd.
Nothing ever copied the project-root cache in — all five `copy2` calls in that
module copy outward (stash, promote, restore).

The cost was paid in the open: on the project that motivated Bug v26 the agent
gave up on the framework path and ran mutmut file by file, accumulating the
project-root cache by hand ("plugins.py targeted run: 100 mutants" →
"Cumulative cache: 548 mutants"), because every framework retry restarted from
zero and never finished inside the 60-minute cap.

Inheriting the cache is safe for the reason mutmut's own design gives:
`get_cached_mutation_statuses` looks a mutant up by its source LINE CONTENT and
compares `tested_against_hash` against the current test suite; a changed line
produces a different Line row and a changed suite forces UNTESTED. Only still-
valid work is inherited. `run_mutation_precheck` is deliberately excluded — its
contract is a fresh verdict every time, and its docstring says so.
"""
from __future__ import annotations

import inspect
import sqlite3
import subprocess
from pathlib import Path

import pytest

import harness_cli  # noqa: F401  entry-first load order
import core.quality_gate.mutation_enforcer as me  # noqa: E402

pytestmark = [pytest.mark.core]


@pytest.fixture(autouse=True)
def _faked_mutmut_is_a_supported_one(monkeypatch):
    """Same reason as tests/test_mutation_enforcer.py's fixture of this name.

    These tests fake `shutil.which("mutmut")` with a path that does not exist,
    so Round 80 站2's version precondition would answer "unreadable" and refuse
    before the resume behaviour under test runs. The precondition has its own
    tests in tests/test_zero_mutants_is_not_zero_percent.py.
    """
    monkeypatch.setattr(me, "mutmut_major_version", lambda _path: 2)


def _real_mutmut_cache(path: Path, killed: int = 4, survived: int = 1) -> bytes:
    """Write a genuine mutmut-2.x-shaped sqlite cache and return its bytes.

    A real database rather than a stub header: `_count_mutmut_results` reads
    this file for real, so the test exercises the resume end to end instead of
    patching the reader out of the way.
    """
    db = sqlite3.connect(str(path))
    db.execute("CREATE TABLE Mutant (id INTEGER PRIMARY KEY, status TEXT)")
    db.executemany(
        "INSERT INTO Mutant (status) VALUES (?)",
        [("ok_killed",)] * killed + [("bad_survived",)] * survived,
    )
    db.commit()
    db.close()
    return path.read_bytes()


@pytest.fixture()
def project(tmp_path, monkeypatch):
    src = tmp_path / "03-development" / "src" / "app"
    src.mkdir(parents=True)
    (src / "core.py").write_text("def f(x):\n    return x + 1\n", encoding="utf-8")
    (tmp_path / "03-development" / "tests").mkdir(parents=True)
    (tmp_path / "setup.cfg").write_text(
        "[mutmut]\npaths_to_mutate = 03-development/src/app\n", encoding="utf-8"
    )
    monkeypatch.setattr(me.shutil, "which", lambda _n: "/usr/bin/mutmut")
    return tmp_path


def _run_capturing_workdir(monkeypatch, project, *, cache_bytes: "bytes | None"):
    """Run compute_mutation_score against a stubbed mutmut, returning what the
    workdir contained at the moment `mutmut run` was invoked.

    Only `subprocess.run` is stubbed — the editable-install probe is a pip
    subprocess and goes through the same stub, so nothing private is patched.
    """
    if cache_bytes is not None:
        (project / ".mutmut-cache").write_bytes(cache_bytes)

    seen: dict = {}

    def _fake_run(cmd, **kwargs):
        if cmd[:2] == ["mutmut", "run"]:
            cache = Path(kwargs["cwd"]) / ".mutmut-cache"
            seen["inherited"] = cache.read_bytes() if cache.is_file() else None
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(me, "run_isolated", _fake_run)
    me.compute_mutation_score(project)
    return seen


def test_a_prior_cache_is_carried_into_the_workdir(project, monkeypatch, tmp_path):
    payload = _real_mutmut_cache(tmp_path / "prior.sqlite")
    seen = _run_capturing_workdir(monkeypatch, project, cache_bytes=payload)
    assert seen.get("inherited") == payload, (
        "mutmut ran in a workdir with no .mutmut-cache — the timeout branch "
        "promises the next run resumes, and it cannot"
    )


def test_no_prior_cache_means_a_clean_run(project, monkeypatch):
    seen = _run_capturing_workdir(monkeypatch, project, cache_bytes=None)
    assert seen.get("inherited") is None


def test_an_empty_cache_file_is_not_inherited(project, monkeypatch):
    """A zero-byte cache is the shape a crashed run leaves behind."""
    seen = _run_capturing_workdir(monkeypatch, project, cache_bytes=b"")
    assert seen.get("inherited") is None


def test_a_corrupt_cache_is_not_inherited(project, monkeypatch):
    """Resuming makes an unusable cache the NEW run's problem: sqlite raises
    "file is not a database" out of _count_mutmut_results and a run that would
    have worked reports an error instead. A partially-written cache is exactly
    what the timeout path can leave behind, so this is the case resuming has
    to survive, not an invented one."""
    seen = _run_capturing_workdir(monkeypatch, project, cache_bytes=b"not a db\n")
    assert seen.get("inherited") is None


def test_the_inherited_cache_is_the_one_the_score_is_read_from(
    project, monkeypatch, tmp_path
):
    """End to end: the resumed cache is a real database, and the score
    compute_mutation_score returns is the one read out of it. The header check
    must not be so strict that it rejects what it exists to accept."""
    _real_mutmut_cache(tmp_path / "prior.sqlite", killed=9, survived=1)
    (project / ".mutmut-cache").write_bytes(
        (tmp_path / "prior.sqlite").read_bytes()
    )

    def _fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(me, "run_isolated", _fake_run)
    ok, score, msg = me.compute_mutation_score(project)
    assert ok, msg
    assert score == 90.0, msg


def test_the_precheck_still_starts_from_nothing():
    """run_mutation_precheck answers "are there ANY survivors right now"; a
    verdict inherited from a previous source tree would be a different
    question. Its docstring has always said so — this keeps a future
    "make them consistent" sweep from making them wrongly consistent."""
    src = inspect.getsource(me.run_mutation_precheck)
    assert "Do NOT copy existing .mutmut-cache into workdir" in src, (
        "the precheck's fresh-run contract lost the comment that explains it"
    )
    assert "shutil.copy2(cache_file, workdir_cache)" not in src


def test_the_timeout_message_describes_the_mechanism_that_exists():
    """Round 30 station 3's rule, applied to prose: a message that promises
    behaviour the code does not have is worse than no message, because the
    reader stops looking."""
    # Round 35 站2: the timeout branch lives in `_compute_mutation_score`;
    # the public name is the wrapper that records an unmeasurable run.
    src = inspect.getsource(me._compute_mutation_score)
    assert "next run will resume)" not in src, (
        "the old wording promised a resume with nothing behind it"
    )
    # Matched inside a single string literal: the sentence is split across two
    # f-strings, so a phrase spanning the break would never appear in source.
    assert "the next run copies it " in src
