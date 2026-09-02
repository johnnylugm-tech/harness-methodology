"""The CI workflow the framework ships has to be able to run the framework's
own preflight.

Round 91. taskq-cc's `gate-check` job (run 32511120510, 2026-08-21) failed like
this, with the remediation printed inside the failure:

    submodule_pin_ci: harness pin d922cf6c: CI verdict unavailable —
    gh run list failed (rc=4): gh: To use GitHub CLI in a GitHub Actions
    workflow, set the GH_TOKEN environment variable.
    PRE-FLIGHT: FAIL

THE CHAIN THIS FILE PINS

    templates/harness_quality_gate.yml    the only workflow the framework ships;
                                          `init-project` (cli/project_cmds.py:167)
                                          and `scripts/harness-init.sh:130` both
                                          copy THIS file byte-for-byte
      -> `run-phase`                      the only step in it that runs a gate
      -> `cmd_run_phase`                  the only caller of `preflight_all()`
                                          in the repository
      -> `PREFLIGHT_CHECKS`               carries `submodule_pin_ci`
      -> `submodule_pin_verdict`          -> `fetch_ci_verdict` -> `gh run list`

`gh` inside Actions has no credentials of its own. Every link above is
load-bearing: remove any one and the conclusion "this step needs GH_TOKEN"
stops following, so each is asserted separately. A guard that only compared
one string would keep passing after the reason for it had been deleted, and
nobody reading it later could re-derive why the line was there (Round 56: fix
at the source, not at the statement).

WHY THE FIX IS A LINE IN THE TEMPLATE AND NOT IN A PROJECT

The operator hit this on 2026-09-01 and repaired taskq-redo by hand —
`b9276d3 ci: pass GH_TOKEN to Phase Preflight so submodule pin CI verdict can
resolve` — and that copy then went green (`harness pin cf8cc0e9: all runs
green`). The template still had nothing, so the next project deployed from it
would fail the same way, and `ci_template_drift` reported the repaired copy as
DRIFTED: the official repair command (`init-project --ci-only --overwrite`)
would have overwritten the fix. The template now carries the same line
verbatim, which is why that copy reads as clean.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest
import yaml

pytestmark = [pytest.mark.core]

REPO = Path(__file__).resolve().parents[1]
#: The credential `gh` reads. Not configurable: it is gh's own variable name.
TOKEN_KEY = "GH_TOKEN"


def _gate_check_steps() -> list[dict]:
    from core.ci_template import ci_template_path

    doc = yaml.safe_load(ci_template_path().read_text(encoding="utf-8"))
    return doc["jobs"]["gate-check"]["steps"]


def _steps_running(command: str) -> list[dict]:
    """Steps whose `run:` invokes *command*. Parsed from YAML, not grepped:
    a step's env is a mapping, and asking a mapping for a key is the question
    this file has — `GH_TOKEN` appearing anywhere in the file would not."""
    return [s for s in _gate_check_steps() if command in str(s.get("run", ""))]


def _direct_calls(fn: ast.FunctionDef) -> set[str]:
    names = set()
    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Name):
                names.add(f.id)
            elif isinstance(f, ast.Attribute):
                names.add(f.attr)
    return names


def _calls_in(path: Path, func_name: str) -> set[str]:
    """Every name reachable by calls from `func_name`, following same-file
    callees to a fixed point.

    One level was not enough and finding that out is the point: `cmd_run_phase`
    is a tracing wrapper around `_cmd_run_phase_impl`, so a direct-calls-only
    version of this guard reported that run-phase does not run the preflight —
    which would have been read as "the token line is dead weight". A guard that
    stops at the first wrapper measures the wrapper.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    defs = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    assert func_name in defs, f"{func_name} is gone from {path.name}; re-derive this guard"

    seen: set[str] = set()
    names: set[str] = set()
    frontier = [func_name]
    while frontier:
        current = frontier.pop()
        if current in seen:
            continue
        seen.add(current)
        called = _direct_calls(defs[current])
        names |= called
        frontier.extend(c for c in called if c in defs and c not in seen)
    return names


# ── the fix ──────────────────────────────────────────────────────────────


def test_the_run_phase_step_carries_the_token_preflight_needs() -> None:
    """The reproduction. Before Round 91 this step had only PHASE."""
    steps = _steps_running("run-phase")
    assert steps, (
        "no step in the shipped template runs `run-phase` — either the template "
        "no longer runs a gate, or this guard is looking at the wrong job"
    )
    for step in steps:
        env = step.get("env") or {}
        assert TOKEN_KEY in env, (
            f"the template's `{step.get('name')}` step runs run-phase, whose "
            f"preflight asks GitHub for the pinned harness commit's CI verdict, "
            f"and passes no {TOKEN_KEY}. Inside Actions `gh` exits rc=4 and the "
            f"whole preflight fails — taskq-cc run 32511120510"
        )
        assert "secrets.GITHUB_TOKEN" in env[TOKEN_KEY] or "github.token" in env[TOKEN_KEY], (
            f"{TOKEN_KEY} is set to {env[TOKEN_KEY]!r}, which is neither of the "
            f"two tokens Actions provides"
        )


def test_the_template_is_the_single_deployed_source() -> None:
    """Both installers copy one file. If a second copy appears, fixing one is
    fixing half — and the drift check `core/ci_template.py` performs, which
    compares a project against exactly this path, would be comparing against
    the wrong one."""
    from core.ci_template import ci_template_path

    template = ci_template_path()
    assert template.is_file()
    init_sh = (REPO / "scripts" / "harness-init.sh").read_text(encoding="utf-8")
    assert "templates/harness_quality_gate.yml" in init_sh, (
        "harness-init.sh no longer deploys from templates/ — it may carry its "
        "own copy, in which case this round's fix reached only one of two"
    )


# ── the chain that makes the line necessary ──────────────────────────────


def test_run_phase_is_what_runs_the_preflight_pipeline() -> None:
    """Link 2: `cmd_run_phase` is the only caller of `preflight_all()`."""
    calls = _calls_in(REPO / "cli" / "phase_cmds.py", "cmd_run_phase")
    assert "preflight_all" in calls, (
        "cmd_run_phase no longer calls preflight_all — the template's run-phase "
        "step may no longer need any credential; re-derive this guard"
    )


def test_the_preflight_pipeline_asks_github_about_the_pin() -> None:
    """Link 3: `submodule_pin_ci` is registered, so every phase runs it."""
    from core.phase_hooks import PREFLIGHT_CHECKS

    assert "submodule_pin_ci" in dict(PREFLIGHT_CHECKS), (
        "submodule_pin_ci left PREFLIGHT_CHECKS — nothing in the preflight "
        "reaches the network any more, and the token line may be dead weight"
    )


def test_the_pin_check_reaches_gh() -> None:
    """Link 4: the check resolves its verdict through `gh`, which is what needs
    the credential. Asserted on the source rather than by running it: calling
    it here would issue a real network request from the suite."""
    calls = _calls_in(REPO / "core" / "quality_gate" / "submodule_pin.py",
                      "submodule_pin_verdict")
    assert "fetch_ci_verdict" in calls, (
        "submodule_pin_verdict no longer goes through fetch_ci_verdict — "
        "re-derive whether the preflight still needs GH_TOKEN"
    )
    verdict_src = (REPO / "core" / "ci_verdict.py").read_text(encoding="utf-8")
    assert '"gh", "run", "list"' in verdict_src, (
        "core/ci_verdict no longer shells out to `gh run list` — the reason "
        "the template carries GH_TOKEN may be gone"
    )


def test_an_unobtainable_verdict_still_fails_the_preflight() -> None:
    """Link 5, and the reason the missing token was fatal rather than noisy:
    Round 37's rule. A verdict that could not be fetched is reported as a
    FAILURE, not skipped — so no-token turned into PRE-FLIGHT: FAIL."""
    from core.quality_gate.submodule_pin import submodule_pin_verdict

    def _refuse(_cmd: list[str]) -> tuple[int, str, str]:
        return (4, "", "gh: To use GitHub CLI in a GitHub Actions workflow, "
                       "set the GH_TOKEN environment variable.")

    res = submodule_pin_verdict(REPO, pinned_sha="0" * 40, runner=_refuse)
    assert res["passed"] is False, (
        "an unobtainable CI verdict now passes the preflight — if that is "
        "deliberate, the template no longer needs GH_TOKEN and this whole file "
        "should go with it"
    )
    assert res.get("infra") is True, "the failure is INFRA-owned, not the project's"
