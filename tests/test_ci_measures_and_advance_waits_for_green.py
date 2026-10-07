"""CI measures the suite the project ships; advance waits for a green build.

taskq-open's GitHub Actions went red at its P3 exit (`ab19fb6`, D4 job: every
project test `not_collected`) and from P5 on (`c1711bb`, traceability: Test
0.0%, attestation mismatch). The template's `gate-check`, D4 and ASPICE jobs
run the project's pytest suite but only ever installed `harness/requirements.txt`
-- the project's own dependencies (fastapi, sqlalchemy) were never installed,
although the same `gate-check` job runs `npm ci` for JS/TS projects and
`harness/env_repair.py` documents the premise that CI installs everything
first. Replayed: a harness-only venv reproduces CI's attestation digest
`50d910be...` exactly; installing the declared manifests turns it `matches`.

push-milestone already asks GitHub after it pushes and records
`state.json.last_milestone_head[<type>]` only on a green (or no-CI) build; its
exit 31 had no reader. advance-phase closed P3 and P5 on red builds, and the
workflow judged "pushed" as "passed". On taskq-open the record discriminates
6/6 milestone commits exactly as their CI verdicts do.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest
import yaml

pytestmark = [pytest.mark.core]

REPO = Path(__file__).resolve().parents[1]


class _Proc:
    def __init__(self, returncode: int = 0, stderr: str = "") -> None:
        self.returncode = returncode
        self.stderr = stderr
        self.stdout = ""


def _recorder(calls: list, returncode: int = 0):
    def run(cmd, **_kw):
        calls.append(list(cmd))
        return _Proc(returncode, "boom" if returncode else "")
    return run


# -- the installer installs what the project declares, and nothing else ------

def test_requirements_and_dev_requirements_are_both_installed(tmp_path) -> None:
    from harness.env_repair import install_project_dependencies

    (tmp_path / "requirements.txt").write_text("fastapi==0.1\n", encoding="utf-8")
    (tmp_path / "requirements-dev.txt").write_text("pytest==8\n", encoding="utf-8")
    calls: list = []

    out = install_project_dependencies(tmp_path, scaffold_missing=False, run=_recorder(calls))

    assert out.installed and out.ok
    assert [c[-1] for c in calls] == [str(tmp_path / "requirements.txt"),
                                      str(tmp_path / "requirements-dev.txt")]


def test_a_tool_only_pyproject_declares_nothing(tmp_path) -> None:
    """run-all-by-workflow: [tool.mypy]/[tool.pytest] only -- `pip install .` of it fails."""
    from harness.env_repair import install_project_dependencies, project_manifest

    (tmp_path / "pyproject.toml").write_text("[tool.mypy]\nstrict = true\n", encoding="utf-8")
    calls: list = []

    out = install_project_dependencies(tmp_path, scaffold_missing=False, run=_recorder(calls))

    assert project_manifest(tmp_path) is None
    assert out.ok and not out.installed and calls == []


def test_a_project_table_pyproject_is_the_manifest(tmp_path) -> None:
    from harness.env_repair import install_project_dependencies

    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\nversion = "0"\ndependencies = ["fastapi"]\n', encoding="utf-8")
    calls: list = []

    out = install_project_dependencies(tmp_path, scaffold_missing=False, run=_recorder(calls))

    assert out.installed and calls and calls[0][-1] == str(tmp_path)


def test_no_manifest_is_not_scaffolded_when_scaffolding_is_off(tmp_path) -> None:
    """CI must not author a deliverable: the SSOT scaffold is for run-env-check."""
    from harness.env_repair import install_project_dependencies

    (tmp_path / "SPEC.md").write_text("# SPEC\n\nRuntime: fastapi, sqlalchemy\n", encoding="utf-8")
    calls: list = []

    out = install_project_dependencies(tmp_path, scaffold_missing=False, run=_recorder(calls))

    assert out.ok and not out.installed and calls == []
    assert sorted(p.name for p in tmp_path.iterdir()) == ["SPEC.md"]


def _cli(project: Path) -> int:
    from cli.checks.gates import cmd_install_project_deps
    return cmd_install_project_deps(argparse.Namespace(project=str(project)))


def test_the_cli_is_not_applicable_to_a_non_python_project(tmp_path) -> None:
    (tmp_path / ".methodology").mkdir()
    (tmp_path / ".methodology" / "state.json").write_text(
        json.dumps({"language": "typescript"}), encoding="utf-8")
    (tmp_path / "requirements.txt").write_text("fastapi\n", encoding="utf-8")
    assert _cli(tmp_path) == 0


def test_the_cli_passes_when_nothing_is_declared(tmp_path) -> None:
    assert _cli(tmp_path) == 0


def test_the_cli_fails_when_the_install_fails(tmp_path, monkeypatch) -> None:
    import harness.env_repair as er

    def failing(*_a, **_k):
        return er.ProjectDepsOutcome(blocked_reason="installing from requirements.txt failed")

    monkeypatch.setattr(er, "install_project_dependencies", failing)
    assert _cli(tmp_path) == 1


# -- the CI template installs them before any step that runs the suite -------

@pytest.mark.parametrize("job", ["gate-check", "d4-spec-coverage-check", "aspice-trace-check"])
def test_the_template_installs_project_deps_before_running_the_suite(job) -> None:
    template = yaml.safe_load((REPO / "templates" / "harness_quality_gate.yml").read_text(encoding="utf-8"))
    steps = [s.get("run") or "" for s in template["jobs"][job]["steps"]]
    installs = [i for i, run in enumerate(steps) if "install-project-deps" in run]
    suite = [i for i, run in enumerate(steps)
             if any(k in run for k in ("run-phase", "spec-coverage-check", "verify-trace"))]
    assert installs and suite
    assert installs[0] < min(suite)


# -- advance-phase does not close a phase on a red build ---------------------

def _project(tmp_path: Path, recorded: dict) -> Path:
    (tmp_path / ".methodology").mkdir()
    (tmp_path / ".methodology" / "state.json").write_text(
        json.dumps({"current_phase": 5, "last_milestone_head": recorded}), encoding="utf-8")
    return tmp_path


@pytest.mark.parametrize("phase, milestone", [
    (3, "p3-post-gate2"), (4, "p4-pre-gate3"), (5, "p5-baseline"), (7, "p7"), (8, "p8")])
def test_advance_blocks_when_the_milestone_did_not_land_green(tmp_path, capsys, phase, milestone) -> None:
    """RED before this round: taskq-open closed P3 and P5 on red builds."""
    from cli.advance_checks import _precheck_exit_milestone_landed_green
    from cli.exit_codes import EX_ADVANCE_MILESTONE_NOT_GREEN

    project = _project(tmp_path, {"p3-mid": "a" * 40})

    assert _precheck_exit_milestone_landed_green(phase, project) == EX_ADVANCE_MILESTONE_NOT_GREEN == 51
    assert f"push-milestone --type {milestone}" in capsys.readouterr().out


def test_advance_passes_once_the_milestone_landed_green(tmp_path) -> None:
    from cli.advance_checks import _precheck_exit_milestone_landed_green

    project = _project(tmp_path, {"p5-baseline": "b" * 40})
    assert _precheck_exit_milestone_landed_green(5, project) is None


@pytest.mark.parametrize("phase", [1, 2, 6])
def test_phases_without_a_milestone_are_not_asked(tmp_path, phase) -> None:
    from cli.advance_checks import _precheck_exit_milestone_landed_green

    assert _precheck_exit_milestone_landed_green(phase, _project(tmp_path, {})) is None


# -- the workflow skips a push only when the push landed green ---------------

def test_the_workflow_guards_read_the_milestone_record() -> None:
    text = (REPO / ".claude" / "workflows" / "run-all.js").read_text(encoding="utf-8")
    for milestone in ("p3-post-gate2", "p4-pre-gate3", "p5-baseline", "p7", "p8"):
        assert f"--arg t {milestone} \\'.last_milestone_head[$t] // empty\\'" in text, milestone
    # Round 115 站4: P8's own p8VerifyCmd went with its hand-rolled Final
    # Push; the milestone is judged where every other phase's is — by
    # advance-phase (exit 51), which P8 now reaches through the shared loop.
    assert "advance-phase --completed 8" in text
    assert "log --oneline --grep=\\\"P8\\\" -1`. If exists" not in text
