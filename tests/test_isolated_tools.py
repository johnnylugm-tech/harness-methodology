"""A tool whose dependencies contradict the project's runs from a venv of its own.

Measured on taskq-open (CI, harness 9bee0f77): `reliability_lint` failed with
"Expecting value: line 1 column 1" once CI installed the project's lock.
fastapi 0.142.2 requires opentelemetry-api>=1.44.0; semgrep 1.165.0 and 1.166.0
both require ~=1.37.0, so no version of either satisfies both and semgrep died
on `import opentelemetry...`. It only ever worked on a developer machine
because a separate pipx semgrep happened to sit on PATH.

Installing semgrep into its own venv is not enough — semgrep's launcher finds
`pysemgrep` through PATH, so an isolated `semgrep` called by absolute path
still ran the project's broken copy whenever the project's bin/ came first
(measured). The scoped environment is half of the fix and is tested as such.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

import harness_cli  # noqa: F401  entry-first before cli imports
from core.utils import isolated_tools
from harness.toolchains import bootstrap
from harness.toolchains.registry import TOOL_SPECS

PIN = bootstrap.pinned_spec("semgrep")


@pytest.fixture
def tools_dir(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "tools"
    monkeypatch.setenv("HARNESS_TOOLS_DIR", str(root))
    return root


def _script(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n" + body + "\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def _install_fake(root: Path, name: str = "semgrep", *, spec: str = PIN, body: str = "echo 1.165.0") -> Path:
    """What a finished `ensure` leaves behind, with a stand-in executable."""
    env = root / name
    _script(env / "bin" / name, body)
    (env / isolated_tools._MARKER).write_text(json.dumps({"spec": spec}), encoding="utf-8")
    return env / "bin"


# ── the pin and the registry agree ──────────────────────────────────────────

def test_semgrep_is_pinned_once_and_not_in_the_shared_requirements():
    assert "semgrep" not in bootstrap.requirements_packages()
    assert bootstrap.PINS["semgrep"] == PIN.split("==")[1]
    assert bootstrap.isolated_packages() == ("semgrep",)


def test_every_isolated_tool_has_a_package_and_every_package_a_tool():
    isolated_ids = {i for i, s in TOOL_SPECS.items() if s.install_step == "isolated"}
    mapped = {i for i in TOOL_SPECS if bootstrap.isolated_package_for_tool(i)}
    assert isolated_ids == mapped == {"semgrep", "semgrep-js"}


def test_an_isolated_tool_is_not_a_pip_round_of_the_shared_interpreter():
    assert bootstrap.step_for_tool("semgrep-js") is None
    assert "semgrep-js" in bootstrap.tools_for_step("isolated")
    advice = bootstrap.install_advice("semgrep-js")
    assert advice and "install-isolated-tools" in advice


# ── the mechanism ───────────────────────────────────────────────────────────

class _Fake:
    """Stands in for subprocess.run: creates the venv dir, records argv."""

    def __init__(self, fail_on: int | None = None):
        self.calls: list[list[str]] = []
        self.fail_on = fail_on

    def __call__(self, argv, **_kw):
        self.calls.append(list(argv))
        n = len(self.calls)
        if self.fail_on == n:
            return subprocess.CompletedProcess(argv, 1, "", "ERROR: no matching distribution")
        if argv[1:3] == ["-m", "venv"]:
            (Path(argv[-1]) / "bin").mkdir(parents=True, exist_ok=True)
            (Path(argv[-1]) / "bin" / "python").write_text("")
        return subprocess.CompletedProcess(argv, 0, "", "")


def test_ensure_builds_once_and_is_a_no_op_for_the_same_spec(tools_dir):
    fake = _Fake()
    isolated_tools.ensure("semgrep", PIN, run=fake)
    assert isolated_tools.installed_spec("semgrep") == PIN
    assert [c[1:3] for c in fake.calls] == [["-m", "venv"], ["-m", "pip"]]
    isolated_tools.ensure("semgrep", PIN, run=fake)
    assert len(fake.calls) == 2, "an environment built from the same spec must be left alone"


def test_ensure_rebuilds_when_the_pin_moves(tools_dir):
    fake = _Fake()
    isolated_tools.ensure("semgrep", "semgrep==1.0.0", run=fake)
    isolated_tools.ensure("semgrep", PIN, run=fake)
    assert isolated_tools.installed_spec("semgrep") == PIN
    assert len(fake.calls) == 4


@pytest.mark.parametrize("fail_on", [1, 2])
def test_a_failed_build_leaves_nothing_that_looks_installed(tools_dir, fail_on):
    with pytest.raises(isolated_tools.IsolatedInstallError, match="no matching distribution|exited"):
        isolated_tools.ensure("semgrep", PIN, run=_Fake(fail_on=fail_on))
    assert isolated_tools.installed_spec("semgrep") is None
    assert isolated_tools.executable("semgrep") is None
    assert not isolated_tools.env_dir("semgrep").exists()


def test_a_venv_step_that_builds_no_interpreter_is_a_failure(tools_dir):
    def run(argv, **_kw):
        return subprocess.CompletedProcess(argv, 0, "", "")  # claims success, builds nothing
    with pytest.raises(isolated_tools.IsolatedInstallError, match="no interpreter"):
        isolated_tools.ensure("semgrep", PIN, run=run)
    assert isolated_tools.installed_spec("semgrep") is None


def test_an_unfinished_directory_is_not_an_installed_environment(tools_dir):
    _script(tools_dir / "semgrep" / "bin" / "semgrep", "echo hi")  # no marker
    assert isolated_tools.executable("semgrep") is None


def test_executable_never_falls_back_to_path(tools_dir, tmp_path, monkeypatch):
    on_path = _script(tmp_path / "elsewhere" / "semgrep", "echo from-path")
    monkeypatch.setenv("PATH", str(on_path.parent) + os.pathsep + os.environ["PATH"])
    assert isolated_tools.executable("semgrep") is None


def test_scoped_env_puts_the_tool_first_and_drops_the_project_import_path(tools_dir):
    env = isolated_tools.scoped_env("semgrep", {
        "PATH": "/project/.venv/bin:/usr/bin", "PYTHONPATH": "/project/src",
        "VIRTUAL_ENV": "/project/.venv", "KEEP": "1"})
    assert env["PATH"].split(os.pathsep)[0] == str(isolated_tools.bin_dir("semgrep"))
    assert env["PATH"].endswith("/project/.venv/bin:/usr/bin")
    assert "PYTHONPATH" not in env and "VIRTUAL_ENV" not in env and env["KEEP"] == "1"


def test_env_for_spec_scopes_only_isolated_tools(tools_dir):
    base = {"PATH": "/usr/bin"}
    assert bootstrap.env_for_spec(TOOL_SPECS["semgrep-js"], base)["PATH"].startswith(
        str(isolated_tools.bin_dir("semgrep")))
    assert bootstrap.env_for_spec(TOOL_SPECS["ruff"], base) is base
    assert bootstrap.env_for_spec(TOOL_SPECS["ruff"], None) is None


# ── the consumers ───────────────────────────────────────────────────────────

def _project(tmp_path: Path) -> Path:
    meth = tmp_path / "proj" / ".methodology"
    meth.mkdir(parents=True)
    (meth / "state.json").write_text(json.dumps(
        {"state": "RUNNING", "current_phase": 4, "language": "python"}), encoding="utf-8")
    (tmp_path / "proj" / "src").mkdir()
    return tmp_path / "proj"


def _lint(project: Path, phase: int = 4) -> dict:
    from core.phase_hooks import PhaseHooks
    return PhaseHooks(str(project), phase=phase).preflight_reliability_lint()


def test_the_lint_blocks_with_the_fix_when_the_environment_is_missing(tools_dir, tmp_path):
    result = _lint(_project(tmp_path))
    assert result["passed"] is False and result["blocking"] is True
    assert "install-isolated-tools" in result["error"]


def test_the_lint_does_not_use_a_semgrep_found_on_path(tools_dir, tmp_path, monkeypatch):
    """The copy on PATH is whatever the project's dependencies left behind."""
    on_path = _script(tmp_path / "bin" / "semgrep", 'echo \'{"results": []}\'')
    monkeypatch.setenv("PATH", str(on_path.parent) + os.pathsep + os.environ["PATH"])
    result = _lint(_project(tmp_path))
    assert result["passed"] is False
    assert "install-isolated-tools" in result["error"]


def test_a_semgrep_that_prints_no_json_is_reported_with_its_own_words(tools_dir, tmp_path):
    _install_fake(tools_dir, body="echo 'ImportError: cannot import name _ExtendedAttributes' >&2; exit 2")
    result = _lint(_project(tmp_path))
    assert result["passed"] is False
    assert "ImportError: cannot import name _ExtendedAttributes" in result["error"]
    assert "Expecting value" not in result["error"]


def test_the_lint_runs_the_isolated_semgrep_under_the_scoped_environment(tools_dir, tmp_path, monkeypatch):
    _install_fake(tools_dir, body=(
        'printf "%s\\n%s\\n" "$PATH" "${PYTHONPATH-unset}" > "$(dirname "$0")/seen"; '
        "echo '{\"results\": []}'"))
    monkeypatch.setenv("PYTHONPATH", "/project/src")
    result = _lint(_project(tmp_path))
    assert result["passed"] is True, result
    seen_path, seen_pythonpath = (isolated_tools.bin_dir("semgrep") / "seen").read_text().splitlines()
    assert seen_path.split(os.pathsep)[0] == str(isolated_tools.bin_dir("semgrep"))
    assert seen_pythonpath == "unset", "the project's import path must not reach the tool"


def test_install_command_builds_probes_and_reports(tools_dir, tmp_path, capsys):
    _install_fake(tools_dir)  # already built from the pinned spec: ensure() is a no-op
    from cli.checks.gates import cmd_install_isolated_tools
    import argparse
    assert cmd_install_isolated_tools(argparse.Namespace(project=str(_project(tmp_path)))) == 0
    assert "ready at" in capsys.readouterr().out


def test_install_command_fails_when_the_installed_tool_does_not_run(tools_dir, tmp_path, capsys):
    _install_fake(tools_dir, body="exit 3")
    from cli.checks.gates import cmd_install_isolated_tools
    import argparse
    assert cmd_install_isolated_tools(argparse.Namespace(project=str(_project(tmp_path)))) == 1
    assert "does not run" in capsys.readouterr().out


def test_run_tool_says_how_to_fix_a_missing_environment(tools_dir, tmp_path):
    from harness.tool_runners import run_tool
    out, rc = run_tool("semgrep-js", str(_project(tmp_path)))
    assert rc == -3 and "install-isolated-tools" in out


def test_run_tool_runs_the_isolated_copy_not_the_one_on_path(tools_dir, tmp_path, monkeypatch):
    from harness.tool_runners import run_tool
    _install_fake(tools_dir, body="echo isolated-copy")
    on_path = _script(tmp_path / "bin" / "semgrep", "echo path-copy")
    monkeypatch.setenv("PATH", str(on_path.parent) + os.pathsep + os.environ["PATH"])
    out, rc = run_tool("semgrep-js", str(_project(tmp_path)))
    assert rc == 0 and out.strip() == "isolated-copy"


def test_an_env_contract_naming_semgrep_is_not_called_fabricated(tools_dir, tmp_path):
    from core.quality_gate.env_verify import _found_on_path_or_venv
    project = _project(tmp_path)
    assert _found_on_path_or_venv("pysemgrep-not-installed-anywhere", project) is False
    _install_fake(tools_dir, "semgrep")
    assert _found_on_path_or_venv("semgrep", project) is True


def test_env_repair_builds_an_isolated_tool_instead_of_calling_it_unfixable(tools_dir, tmp_path):
    from harness.env_repair import repair_missing_tools
    fake = _Fake()
    outcome = repair_missing_tools(
        _project(tmp_path), ["semgrep-js"], run=fake, reprobe=lambda _ids, _root: [])
    assert "isolated:semgrep" in outcome.attempted_steps
    assert outcome.unfixable == [] and outcome.pip_failures == []
    assert isolated_tools.installed_spec("semgrep") == PIN


# ── the property this exists for, against the real semgrep ──────────────────

@pytest.fixture
def real_semgrep(monkeypatch):
    monkeypatch.setenv("HARNESS_TOOLS_DIR", str(isolated_tools.default_tools_root()))
    try:
        isolated_tools.ensure("semgrep", PIN)
    except isolated_tools.IsolatedInstallError as exc:
        pytest.skip(f"isolated semgrep could not be built: {exc}")


def test_a_poisoned_pysemgrep_first_on_path_cannot_reach_the_lint(real_semgrep, tmp_path, monkeypatch):
    """The failure CI had: another environment's `pysemgrep` earlier on PATH.
    Called by absolute path, the isolated launcher still ran it. Fails if the
    lint stops going through scoped_env."""
    poison = _script(tmp_path / "poison" / "pysemgrep", "echo 'poisoned pysemgrep ran' >&2; exit 1")
    monkeypatch.setenv("PATH", str(poison.parent) + os.pathsep + os.environ["PATH"])
    project = _project(tmp_path)
    (project / "src" / "ok.py").write_text("x = 1\n", encoding="utf-8")
    result = _lint(project)
    assert result["passed"] is True, result
    assert result["finding_count"] == 0


# ── run-phase / env-check repair can see the preflight's semgrep ────────────

def test_a_python_project_without_the_lint_semgrep_is_told_and_repaired(tools_dir, tmp_path):
    """semgrep is no python dimension's tool, so the gate walk never asked for
    it; without this the blocking reliability lint was never auto-repaired."""
    from harness import tool_checks
    project = _project(tmp_path)
    assert tool_checks.missing_preflight_tool_ids(str(project)) == ["semgrep"]
    assert "semgrep" in tool_checks.all_missing_gate_tool_ids(str(project))
    _install_fake(tools_dir)
    assert tool_checks.missing_preflight_tool_ids(str(project)) == []


def test_a_project_in_another_language_is_not_asked_for_the_python_lint(tools_dir, tmp_path):
    from harness import tool_checks
    project = _project(tmp_path)
    state = project / ".methodology" / "state.json"
    state.write_text(json.dumps({"state": "RUNNING", "current_phase": 4, "language": "typescript"}))
    assert tool_checks.missing_preflight_tool_ids(str(project)) == []


def test_repairing_the_lint_semgrep_builds_the_isolated_environment(tools_dir, tmp_path):
    from harness.env_repair import repair_missing_tools
    outcome = repair_missing_tools(
        _project(tmp_path), ["semgrep"], run=_Fake(), reprobe=lambda _ids, _root: [])
    assert outcome.attempted_steps == ["isolated:semgrep"] and outcome.unfixable == []


def test_a_semgrep_on_path_does_not_make_the_isolated_tool_present(tools_dir, tmp_path, monkeypatch):
    """env-check said 'present' (another semgrep answered --version) while the
    run said 'not found' — the probe and the run must ask the same question."""
    from harness.tool_checks import run_spec_check
    on_path = _script(tmp_path / "bin" / "semgrep", "echo 9.9.9")
    monkeypatch.setenv("PATH", str(on_path.parent) + os.pathsep + os.environ["PATH"])
    for tool_id in ("semgrep", "semgrep-js"):
        assert run_spec_check(TOOL_SPECS[tool_id], cwd=str(tmp_path)) is False
    _install_fake(tools_dir)
    for tool_id in ("semgrep", "semgrep-js"):
        assert run_spec_check(TOOL_SPECS[tool_id], cwd=str(tmp_path)) is True


def test_two_builders_at_once_do_not_delete_each_other(tools_dir):
    """Measured before the lock: one of two concurrent ensure() calls failed
    every time ('venv vanished'), and its cleanup could delete the other's
    finished environment, leaving nothing installed."""
    import threading
    import time

    def make_run(tag):
        def run(argv, **_kw):
            if argv[1:3] == ["-m", "venv"]:
                d = Path(argv[-1])
                (d / "bin").mkdir(parents=True, exist_ok=True)
                (d / "bin" / "python").write_text("")
                (d / "owner").write_text(tag)
                time.sleep(0.2)
                return subprocess.CompletedProcess(argv, 0, "", "")
            owner_file = Path(argv[0]).parent.parent / "owner"
            owner = owner_file.read_text() if owner_file.exists() else None
            ok = owner == tag  # real pip fails when its venv was deleted under it
            return subprocess.CompletedProcess(argv, 0 if ok else 1, "", f"venv vanished ({owner})")
        return run

    outcome: dict[str, str] = {}
    barrier = threading.Barrier(2)

    def worker(tag):
        barrier.wait()
        try:
            isolated_tools.ensure("semgrep", PIN, run=make_run(tag))
            outcome[tag] = "ok"
        except isolated_tools.IsolatedInstallError as exc:
            outcome[tag] = str(exc)

    threads = [threading.Thread(target=worker, args=(t,)) for t in ("A", "B")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert outcome == {"A": "ok", "B": "ok"}
    assert isolated_tools.installed_spec("semgrep") == PIN
