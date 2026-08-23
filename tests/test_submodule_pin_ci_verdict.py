"""Round 67 站0 — the framework version a project pins has to be a green one.

Round 37 built `core/ci_verdict` because taskq-renew pushed 52 times onto a red
build and nothing ever asked GitHub what happened. It closed the loop for the
commit being pushed. It never closed the other direction: the harness commit a
consuming project has PINNED.

Measured 2026-08-22 across the eight projects on this machine, by asking
`gh api repos/johnnylugm-tech/harness-methodology/commits/<pin>/check-runs`
for each `git submodule status` SHA:

    taskq                a8ab61a1  ALL GREEN
    taskq-plus           d5810d68  ALL GREEN
    taskq-renew          c09fae1f  ALL GREEN
    taskq-api            11c4eafd  ALL GREEN
    taskq-advance        5a87e35f  ALL GREEN
    taskq-super          f99a8b0d  Framework Self-Tests=failure
    taskq-cc             f6d984bc  Framework Self-Tests=failure
    run-all-by-workflow  68209a97  ALL GREEN

Two of eight are running a framework whose own test suite was red at that
commit. taskq-cc's `f6d984bc` is the one Round 66 pushed and had to fix in
`36ff4e5` — the pin carries the regression, and the project ran P6 through P8
on it while nothing in the framework said a word.

`fetch_ci_verdict` already answers this question and needs no new network
code: the submodule's own origin IS the harness repo. What is missing is a
caller.

`unavailable` stays `unavailable` — Round 37's rule, restated: a verdict that
could not be obtained is not a green verdict, and it is INFRA rather than a
project failure.
"""

from __future__ import annotations

import pytest


def _runner_returning(*names_and_conclusions):
    """A `gh run list --json name,conclusion` stand-in.

    `fetch_ci_verdict` takes its runner as a parameter precisely so a test
    never patches a module global (Round 37's own note).
    """
    import json

    payload = json.dumps([
        {"name": n, "conclusion": c} for n, c in names_and_conclusions
    ])

    def _run(cmd):
        return 0, payload, ""
    return _run


def _unavailable_runner(cmd):
    return 1, "", "gh: command not found"


def test_a_pin_on_a_red_build_blocks(tmp_path):
    from core.quality_gate.submodule_pin import submodule_pin_verdict

    res = submodule_pin_verdict(
        tmp_path, pinned_sha="f6d984bc421b502ed104d9a328f053159e44f504",
        runner=_runner_returning(("Framework Self-Tests", "failure"),
                                 ("Validate Cross-References", "success")),
    )
    assert res["passed"] is False, (
        "a submodule pinned at a commit whose own CI was red was accepted. "
        "Every gate this project runs is executed by that code"
    )
    assert "Framework Self-Tests" in res["message"], (
        f"the block must name the failing job, not just say red: {res}"
    )
    assert "f6d984bc" in res["message"], (
        f"the block must name the pin it is talking about: {res}"
    )


def test_a_pin_on_a_green_build_passes(tmp_path):
    from core.quality_gate.submodule_pin import submodule_pin_verdict

    res = submodule_pin_verdict(
        tmp_path, pinned_sha="11c4eafd2bc29b102df7c02f041d0ec18b27c7e7",
        runner=_runner_returning(("Framework Self-Tests", "success"),
                                 ("Validate Module Manifests", "success")),
    )
    assert res["passed"] is True, f"a green pin was blocked: {res}"


def test_an_unobtainable_verdict_is_infra_not_a_pass(tmp_path):
    """Round 37's rule, one level up. No network is not proof of green."""
    from core.quality_gate.submodule_pin import submodule_pin_verdict

    res = submodule_pin_verdict(
        tmp_path, pinned_sha="deadbeef" * 5, runner=_unavailable_runner,
    )
    assert res["passed"] is False, (
        "an unobtainable CI verdict was treated as a pass"
    )
    assert res.get("infra") is True, (
        "no gh / no network is the framework's problem to report as INFRA, "
        f"not the project's failure to fix: {res}"
    )


def test_no_submodule_is_not_a_failure(tmp_path):
    """A project with the harness vendored rather than pinned has no SHA to
    check, and Round 46's rule is that an absent witness is reported as absent
    — not as a pass, and not as a block the project cannot act on."""
    from core.quality_gate.submodule_pin import submodule_pin_verdict

    res = submodule_pin_verdict(tmp_path, pinned_sha=None, runner=_unavailable_runner)
    assert res["passed"] is True
    assert res.get("skipped") is True, f"an absent pin must say so: {res}"


def test_a_local_only_pin_bypasses_the_ci_check(tmp_path):
    """Round 2026-08-23 (HARNESS-FIX): a pin on a commit not yet on any
    remote-tracking branch cannot have a CI verdict yet — the verdict is
    structurally absent, not red and not a real INFRA failure. The bypass
    reports `passed=True` with `skipped="local_only_pin"` (Round 46) and
    a message that points at the push command the operator must run.

    Pushed pins still go through the strict path (the existing tests above
    pin `here cover that). Only this specific temporal-state case is bypassed.
    """
    import subprocess as _sp
    from core.quality_gate.submodule_pin import (
        _commit_pushed_to_origin, submodule_pin_verdict,
    )

    # Build a real git repo at tmp_path/"harness" so the helper can query
    # remote-tracking branches without any mocked module globals.
    submodule_dir = tmp_path / "harness"
    submodule_dir.mkdir()
    _sp.run(["git", "-C", str(submodule_dir), "init", "--quiet"],
            check=True, capture_output=True)
    _sp.run(["git", "-C", str(submodule_dir), "config", "user.email",
             "test@local"], check=True, capture_output=True)
    _sp.run(["git", "-C", str(submodule_dir), "config", "user.name", "t"],
            check=True, capture_output=True)
    (submodule_dir / "f").write_text("x\n")
    _sp.run(["git", "-C", str(submodule_dir), "add", "f"], check=True,
            capture_output=True)
    _sp.run(["git", "-C", str(submodule_dir), "commit", "-m", "i",
             "--quiet"], check=True, capture_output=True)
    sha = _sp.run(["git", "-C", str(submodule_dir), "rev-parse", "HEAD"],
                  capture_output=True, text=True).stdout.strip()

    # No remote-tracking branches → commit is local-only.
    assert _commit_pushed_to_origin(submodule_dir, sha) is False, (
        "a freshly-committed SHA with no remotes should register as "
        "local-only, not as pushed"
    )

    # High-level verdict: passed, but clearly marked as skipped-by-design
    # with the push hint, NOT converted into a silent pass.
    res = submodule_pin_verdict(tmp_path, pinned_sha=sha,
                                runner=_unavailable_runner)
    assert res["passed"] is True, (
        "a local-only pin must not be flagged INFRA — its CI verdict is "
        f"structurally absent, not red; got {res}"
    )
    assert res.get("skipped") == "local_only_pin", (
        f"a local-only pin must say so explicitly, never silently; got {res}"
    )
    assert "push" in res["message"].lower(), (
        f"the bypass message must tell the operator how to get a real CI "
        f"verdict; got {res['message']}"
    )

    # After pushing to a real remote-tracking branch, the same verdict
    # goes through the STRICT path again — runner returns "unavailable"
    # → block with infra=True (preserves Round 37).
    _sp.run(["git", "-C", str(submodule_dir), "checkout", "-b", "rel",
             "--quiet"], check=True, capture_output=True)
    _sp.run(["git", "-C", str(submodule_dir), "update-ref",
             "refs/remotes/origin/rel", sha], check=True,
            capture_output=True)
    assert _commit_pushed_to_origin(submodule_dir, sha) is True, (
        "a SHA on a remote-tracking branch must register as pushed"
    )
    strict = submodule_pin_verdict(tmp_path, pinned_sha=sha,
                                    runner=_unavailable_runner)
    assert strict["passed"] is False, (
        "once pushed, a real-but-unobtainable verdict must still INFRA-block"
    )
    assert strict.get("infra") is True, (
        "Round 37's rule is preserved for the pushed-but-unavailable case"
    )


def test_a_stale_local_cache_does_not_hide_an_actually_pushed_commit(tmp_path):
    """Code-review follow-up (2026-08-23): the standard way a pin gets
    applied — `git submodule update` — fetches only the pinned commit
    OBJECT and does not reliably update `refs/remotes/origin/*`. Build the
    exact shape that produces: a real `origin` remote (a local bare repo,
    not a URL, so this test stays fully offline), a submodule clone whose
    `origin/main` cache is stale (still points at an older commit) even
    though a newer commit — the one about to be checked — has genuinely
    been pushed to that same origin. Before this round's fix, the stale
    cache alone made `_commit_pushed_to_origin` say "local-only"; the
    fetch-before-check refresh must find it."""
    import subprocess as _sp
    from core.quality_gate.submodule_pin import _commit_pushed_to_origin

    origin_bare = tmp_path / "origin.git"
    _sp.run(["git", "init", "--quiet", "--bare", str(origin_bare)],
            check=True, capture_output=True)

    submodule_dir = tmp_path / "harness"
    _sp.run(["git", "clone", "--quiet", str(origin_bare), str(submodule_dir)],
            check=True, capture_output=True)
    _sp.run(["git", "-C", str(submodule_dir), "config", "user.email",
             "test@local"], check=True, capture_output=True)
    _sp.run(["git", "-C", str(submodule_dir), "config", "user.name", "t"],
            check=True, capture_output=True)
    (submodule_dir / "f").write_text("a\n")
    _sp.run(["git", "-C", str(submodule_dir), "add", "f"], check=True,
            capture_output=True)
    _sp.run(["git", "-C", str(submodule_dir), "commit", "-m", "a", "--quiet"],
            check=True, capture_output=True)
    _sp.run(["git", "-C", str(submodule_dir), "push", "-u", "origin",
             "HEAD:main", "--quiet"], check=True, capture_output=True)

    # A second commit lands on origin from elsewhere (a different clone) —
    # submodule_dir's own `refs/remotes/origin/main` never hears about it.
    other_clone = tmp_path / "other_clone"
    _sp.run(["git", "clone", "--quiet", str(origin_bare), str(other_clone)],
            check=True, capture_output=True)
    _sp.run(["git", "-C", str(other_clone), "config", "user.email",
             "test@local"], check=True, capture_output=True)
    _sp.run(["git", "-C", str(other_clone), "config", "user.name", "t"],
            check=True, capture_output=True)
    (other_clone / "g").write_text("b\n")
    _sp.run(["git", "-C", str(other_clone), "add", "g"], check=True,
            capture_output=True)
    _sp.run(["git", "-C", str(other_clone), "commit", "-m", "b", "--quiet"],
            check=True, capture_output=True)
    _sp.run(["git", "-C", str(other_clone), "push", "origin", "main",
             "--quiet"], check=True, capture_output=True)
    sha_b = _sp.run(["git", "-C", str(other_clone), "rev-parse", "HEAD"],
                    capture_output=True, text=True).stdout.strip()

    # Mimic `git submodule update`: fetch only the pinned OBJECT into
    # submodule_dir, without updating refs/remotes/origin/main — the exact
    # gap this round's fix closes. submodule_dir's cache still says main
    # is at the FIRST commit.
    _sp.run(["git", "-C", str(submodule_dir), "fetch", "origin", sha_b,
             "--quiet"], check=True, capture_output=True)

    assert _commit_pushed_to_origin(submodule_dir, sha_b) is True, (
        "sha_b is genuinely on origin (pushed by another clone) but "
        "submodule_dir's own remote-tracking cache predates that push — "
        "the fetch-before-check refresh must still find it, not report "
        "a false local-only bypass"
    )


def test_a_path_that_is_not_a_git_dir_falls_through_to_strict(tmp_path):
    """A tmp_path that lacks the `harness` dir entirely must NOT register
    as a local-only pin — the bypass would lie about a pin that does not
    exist. Helper returns True (conservative); caller falls through to
    the existing INFRA path."""
    from core.quality_gate.submodule_pin import _commit_pushed_to_origin

    # tmp_path has no `harness` subdir at all → conservative True.
    assert _commit_pushed_to_origin(tmp_path / "harness",
                                    "a" * 40) is True, (
        "an absent submodule must NOT be classified as local-only — that "
        "would bypass the CI check on a pin that does not exist"
    )


def test_the_check_is_in_the_preflight_registry():
    """A checker nothing calls is the shape this whole round is about.

    `tests/test_preflight_registry.py` already forces every `preflight_*`
    method to be registered or excluded with a reason; this pins the other
    direction for this specific check, so it cannot be quietly dropped from
    the pipeline while the module stays in the tree.
    """
    from core.phase_hooks import PREFLIGHT_CHECKS, PhaseHooks

    keys = [k for k, _ in PREFLIGHT_CHECKS]
    assert "submodule_pin_ci" in keys, (
        f"submodule_pin_ci is not in PREFLIGHT_CHECKS: {keys}"
    )
    assert hasattr(PhaseHooks, "preflight_submodule_pin_ci")


@pytest.mark.parametrize("conclusion", ["cancelled", "timed_out", "startup_failure"])
def test_a_non_success_conclusion_is_not_success(tmp_path, conclusion):
    """`success` is the only conclusion that means the tests ran and passed."""
    from core.quality_gate.submodule_pin import submodule_pin_verdict

    res = submodule_pin_verdict(
        tmp_path, pinned_sha="a" * 40,
        runner=_runner_returning(("Framework Self-Tests", conclusion)),
    )
    assert res["passed"] is False, (
        f"conclusion={conclusion} was read as green"
    )
