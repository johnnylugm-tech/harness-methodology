"""The framework must read back what its own push produced (Round 37 站0/站3).

Measured defect, taskq-renew 2026-08-05: 52 GitHub Actions runs, 48 red,
red on every single push from Phase 3 onward — while the local pipeline
declared every phase and every gate PASS and advanced state.json to Phase 9.

A full-tree search of `core/ `cli/` `harness/` `scripts/` and
`.claude/workflows/` found no code that reads a workflow run's conclusion.
`scripts/phase_auditor.py`'s GitHubFetcher reads the repo tree only. The
push-milestone gate verifies THAT a push happened; nothing verifies WHAT the
push produced. Those are two different propositions and only one had an
enforcer.

`gh` absence / no network / the run not having appeared yet is INFRA, not
PASS — the same rule Round 32 and Round 35 applied to mutation scoring: a
verdict we could not obtain is not a green verdict.
"""

from __future__ import annotations

import argparse
import json

import pytest


def _ex(name: str) -> int:
    """Read an exit code by name at call time.

    Imported lazily so this module still COLLECTS before Round 37 站3 adds
    the codes: a module-level import of a not-yet-existing name turns one
    red test into `Interrupted: 1 error during collection`, which stops the
    rest of the suite from running at all.
    """
    import cli.exit_codes as ec
    return getattr(ec, name)


def _runs(*pairs: tuple[str, str]) -> str:
    return json.dumps([
        {"name": name, "conclusion": conclusion, "databaseId": 1000 + i,
         "url": f"https://github.com/o/r/actions/runs/{1000 + i}"}
        for i, (name, conclusion) in enumerate(pairs)
    ])


def _runner(rc: int, stdout: str = "", stderr: str = ""):
    def run(cmd: list[str]) -> tuple[int, str, str]:
        return rc, stdout, stderr
    return run


# --------------------------------------------------------------------------
# The verdict itself
# --------------------------------------------------------------------------

def test_all_green_runs_are_green(tmp_path) -> None:
    from core.ci_verdict import fetch_ci_verdict

    v = fetch_ci_verdict(tmp_path, "abc1234",
                         runner=_runner(0, _runs(("gate-check", "success"))))
    assert v.status == "green"
    assert v.failed == []


def test_a_failed_job_makes_the_verdict_red_and_names_it(tmp_path) -> None:
    from core.ci_verdict import fetch_ci_verdict

    v = fetch_ci_verdict(tmp_path, "abc1234", runner=_runner(0, _runs(
        ("gate-check", "success"),
        ("CRG Architecture Gate (P3+)", "failure"),
    )))
    assert v.status == "red"
    assert "CRG Architecture Gate (P3+)" in v.failed
    assert "gate-check" not in v.failed


def test_no_run_for_this_sha_is_unavailable_not_green(tmp_path) -> None:
    from core.ci_verdict import fetch_ci_verdict

    v = fetch_ci_verdict(tmp_path, "abc1234", runner=_runner(0, "[]"))
    assert v.status == "unavailable"


def test_gh_failure_is_unavailable_not_green(tmp_path) -> None:
    from core.ci_verdict import fetch_ci_verdict

    v = fetch_ci_verdict(tmp_path, "abc1234",
                         runner=_runner(127, "", "gh: command not found"))
    assert v.status == "unavailable"
    assert "gh" in v.detail


def test_a_still_running_job_is_unavailable_not_green(tmp_path) -> None:
    """`conclusion` is empty while a run is in progress — the answer is not
    in yet, so it is not an answer."""
    from core.ci_verdict import fetch_ci_verdict

    v = fetch_ci_verdict(tmp_path, "abc1234", runner=_runner(0, _runs(
        ("gate-check", "success"),
        ("CRG Architecture Gate (P3+)", ""),
    )))
    assert v.status == "unavailable"


# --------------------------------------------------------------------------
# The command's exit codes
# --------------------------------------------------------------------------

def _args(tmp_path, payload: str) -> argparse.Namespace:
    """`runner` is a real injection point on the command, not a patched
    private name — the seam the private-patch ratchet asks for."""
    return argparse.Namespace(project=str(tmp_path), sha="abc1234",
                              runner=_runner(0, payload))


@pytest.mark.parametrize("payload,expected_name", [
    (_runs(("gate-check", "success")), "EX_OK"),
    (_runs(("gate-check", "failure")), "EX_CI_RED"),
    ("[]", "EX_CI_VERDICT_UNAVAILABLE"),
])
def test_verify_ci_exit_codes(tmp_path, payload, expected_name) -> None:
    from cli.check_cmds import cmd_verify_ci

    assert cmd_verify_ci(_args(tmp_path, payload)) == _ex(expected_name)


def test_red_ci_run_blocks_and_names_the_failing_job(tmp_path, capsys) -> None:
    from cli.check_cmds import cmd_verify_ci

    rc = cmd_verify_ci(_args(tmp_path, _runs(
        ("ASPICE Traceability Check (TH-01)", "failure"),
    )))
    out = capsys.readouterr().out

    assert rc == _ex("EX_CI_RED")
    assert "BLOCKED" in out
    assert "ASPICE Traceability Check (TH-01)" in out
