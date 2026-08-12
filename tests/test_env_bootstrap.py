"""The framework depends on a virtualenv it never builds.

Round 47 站0. Every generated workflow's first line is

    const PY = REPO + '/.venv/bin/python'          (js_blocks.py:110)

and every phase's first command runs through it. A whole-tree grep for
`python -m venv` finds exactly one hit: a string inside a BLOCKED message
(`cli/project_cmds.py:341`) telling the operator to run it themselves. No code
path creates that venv. When it is absent, Phase 1's preflight fails its first
command three times and reports "preflight did not PASS in 3 orchestrator
attempts" — a message about the retry loop, not about the missing interpreter.

The half that does exist checks the wrong interpreter. `init-project`'s step
[10b/11] (`cli/project_cmds.py:305-337`) decides whether pyyaml/jsonschema are
importable with `__import__(_p)` — which runs in the *calling* process — and
then installs into `project/.venv`. Run init-project from any interpreter that
already has pyyaml and it prints "OK — all runtime Python deps importable"
while the project's venv stays empty. It also only checks 2 of the 20 packages
requirements.txt pins.

Measured 2026-08-12 on a clean venv with only `pip install -r requirements.txt`
(PATH narrowed to /usr/bin:/bin plus node), `verify_all_gate_tools` still
reports four missing: code-review-graph, import-linter, scancode (all
pip-installable, all in the CI template's "Install gate hard dependencies"
step) and gitleaks (an external binary). requirements.txt alone does not
produce an environment that can pass Gate 3/4 — only the CI workflow knows the
rest of the sequence, and only CI can execute it.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

pytestmark = [pytest.mark.core]


def test_the_framework_can_create_the_venv_it_depends_on(tmp_path):
    """A project with no .venv gets one, and it is a working interpreter."""
    from scripts.bootstrap_env import ensure_venv

    project = tmp_path / "fresh"
    project.mkdir()
    assert not (project / ".venv").exists()

    py = ensure_venv(project)

    assert py.exists(), f"ensure_venv returned {py}, which does not exist"
    assert py == project / ".venv" / ("Scripts" if sys.platform == "win32" else "bin") / (
        "python.exe" if sys.platform == "win32" else "python"
    )
    proc = subprocess.run([str(py), "-c", "print(1 + 1)"], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "2"


def test_importability_is_judged_in_the_target_interpreter(tmp_path):
    """The interpreter asked is the one being prepared, not the one asking.

    This test's own interpreter imports yaml (the suite could not run
    otherwise). A freshly created venv does not. `init-project`'s current
    probe would answer "importable" here; the answer that matters is the
    target venv's.
    """
    from scripts.bootstrap_env import ensure_venv, missing_imports

    __import__("yaml")  # the calling interpreter definitely has it

    py = ensure_venv(tmp_path / "fresh")
    assert missing_imports(py, ("yaml",)) == ["yaml"]


def test_a_failed_pip_step_is_not_the_verdict(tmp_path):
    """The probe decides, in both directions.

    Measured on macOS 2026-08-12: the `gate-extras` round fails outright
    because `scancode-toolkit==32.4.1` needs pyicu, which needs ICU headers —
    on a host where `scancode --version` answers perfectly well from a system
    install. Treating pip's exit code as the verdict would block that host for
    a tool it already has, which is the same mistake as believing a green pip
    over an absent tool.
    """
    from scripts.bootstrap_env import bootstrap

    project = tmp_path / "proj"
    project.mkdir()
    (project / ".venv" / "bin").mkdir(parents=True)
    (project / ".venv" / "bin" / "python").write_text("#!/bin/sh\n")

    def failing_pip(argv, **kwargs):
        failed = "pip" in argv and "install" in argv
        return type(
            "P", (), {"returncode": 1 if failed else 0, "stdout": "", "stderr": "boom"}
        )()

    # Absent on the first look (so the install is attempted), resolvable on the
    # second (it came from somewhere pip is not, e.g. a system scancode).
    answers = [["scancode"], []]

    report = bootstrap(project, run=failing_pip, probe=lambda _p: answers.pop(0))

    assert report.steps_run, "a missing tool must trigger the install"
    assert report.failures, "a failed pip step must still be reported"
    assert report.still_missing_tools == []
    assert report.ok is True, "everything the step promised is present — that is the verdict"


def test_requirements_alone_is_not_the_whole_gate_toolchain():
    """The three packages requirements.txt cannot carry have a home in the SSOT.

    requirements.txt's own comment explains why code-review-graph cannot live
    in it (semgrep pins exceptiongroup~=1.2.0; fastmcp needs >=1.2.2, so a
    combined resolve is ResolutionImpossible). That is a real constraint, and
    it is exactly why a second pip round has to exist somewhere. Today it
    exists only in the CI yaml.
    """
    from harness.toolchains import bootstrap

    step_names = [s.name for s in bootstrap.PIP_STEPS]
    assert step_names == ["requirements", "gate-extras"]

    # The four tools a requirements.txt-only install leaves behind, measured
    # 2026-08-12 — three routed to the second pip round, one not pip at all.
    assert bootstrap.step_for_tool("scancode") == "gate-extras"
    assert bootstrap.step_for_tool("import-linter") == "gate-extras"
    assert bootstrap.step_for_tool("code-review-graph") == "gate-extras"
    assert bootstrap.step_for_tool("gitleaks") is None
    assert "gitleaks" in bootstrap.EXTERNAL_BINARIES

    # And the ordinary ones still come from requirements.txt.
    assert bootstrap.step_for_tool("ruff") == "requirements"
    assert bootstrap.step_for_tool("pytest-cov") == "requirements"
