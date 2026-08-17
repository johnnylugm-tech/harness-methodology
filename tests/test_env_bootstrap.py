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

import os
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


# ── bootstrap_env.unsatisfied_tools: skip-inline tools are asked differently ──
# Regression for the bug where bootstrap-env reported scancode unresolvable on
# a host where `scancode --version` cannot run (macOS pyicu vs the system ICU)
# even after the distribution installed fine. Those tools are marked
# skip_inline=True precisely because their executable probe is not usable
# inline.
#
# Round 56 站3 keeps that exclusion and replaces the remedy. Skipping them
# outright made `unsatisfied_tools` return [] on a tree missing them, and its
# one production consumer reads [] as "nothing left to install" — so the pip
# round never ran. They are now asked the question bootstrap actually has:
# is the DISTRIBUTION present in the target interpreter.
def test_unsatisfied_tools_does_not_run_the_executable_probe_for_skip_inline(monkeypatch):
    """`scancode --version` must never be what decides this."""
    from scripts import bootstrap_env
    from harness import tool_checks
    from harness.toolchains.registry import TOOL_SPECS

    probed: list[str] = []

    def _spy_run_tool_check(cmd, **kwargs):
        # Map the cmd back to the tool_id by string match.
        for tool_id, spec in TOOL_SPECS.items():
            if spec.check_cmd and spec.check_cmd.split(" ", 1)[0] in cmd:
                probed.append(tool_id)
        return True  # pretend every probe passed

    monkeypatch.setattr(tool_checks, "run_tool_check", _spy_run_tool_check)
    bootstrap_env.unsatisfied_tools("/tmp/does-not-matter")

    for tool_id in ("scancode", "mutmut", "code-review-graph"):
        assert tool_id not in probed, (
            f"{tool_id} is skip_inline=True; its executable probe must not run"
        )


def test_every_skip_inline_pip_tool_resolves_to_a_distribution():
    """Otherwise the probe cannot answer, and says so rather than assuming.

    Measured 2026-08-17: scancode → scancode-toolkit and code-review-graph →
    code-review-graph come from `package_for_tool`; mutmut is named in
    requirements.txt as itself. stryker has no pip step, so this function
    never reaches it.
    """
    from scripts.bootstrap_env import _distribution_name
    import harness.toolchains.bootstrap as ssot
    from harness.toolchains.registry import TOOL_SPECS

    reached = {
        tool_id
        for step in ssot.PIP_STEPS
        for tool_id in ssot.tools_for_step(step.name)
        if TOOL_SPECS[tool_id].skip_inline
    }
    assert reached == {"scancode", "mutmut", "code-review-graph"}
    assert _distribution_name("scancode") == "scancode-toolkit"
    assert _distribution_name("code-review-graph") == "code-review-graph"
    assert _distribution_name("mutmut") == "mutmut"


def test_unsatisfied_tools_still_probes_active_tools(monkeypatch):
    """The skip-list exclusion must not drop active tools from the probe.

    ruff is not skip_inline; an inline probe must still run so a missing
    install gets reported at phase entry, not at gate time.
    """
    from scripts import bootstrap_env
    from harness import tool_checks
    from harness.toolchains.registry import TOOL_SPECS

    probed: list[str] = []

    def _spy_run_tool_check(cmd, **kwargs):
        for tool_id, spec in TOOL_SPECS.items():
            if spec.check_cmd and spec.check_cmd.split(" ", 1)[0] in cmd:
                probed.append(tool_id)
        return True

    monkeypatch.setattr(tool_checks, "run_tool_check", _spy_run_tool_check)
    bootstrap_env.unsatisfied_tools("/tmp/does-not-matter")

    assert "ruff" in probed, (
        "ruff is skip_inline=False; bootstrap-env must still probe it"
    )


# ── Round 56 站3: bootstrap asked "can it run", needed "is it installed" ──
# `unsatisfied_tools` has exactly one production consumer: `bootstrap()`'s
# `measure`, and its answer decides whether the pip round runs at all
# (`if report.ok: return report`). Skipping skip_inline tools therefore did not
# merely quieten a diagnostic — it made a host that is missing mutmut,
# scancode or code-review-graph look complete, so pip never ran and the tools
# were never installed and never reported.
#
# Measured 2026-08-17 on this repository: `unsatisfied_tools(".")` returned
# `[]` while `importlib.metadata.distribution` could find neither
# `code-review-graph` nor `scancode-toolkit`. code-review-graph is the tool
# `verify_all_gate_tools` calls a "hard dependency (no degradation)" — it
# scores the architecture dimension.
#
# The symptom the skip was reaching for is real: `scancode --version` fails
# forever on a host whose pyicu conflicts with the system ICU, so the pip round
# was re-run every time and never helped. But that is the wrong question.
# bootstrap needs to know whether pip still has work to do, which is answered
# by the distribution being present in the TARGET interpreter — not by the tool
# being executable today.
def _fake_venv(tmp_path, log):
    """A project whose .venv interpreter is a script we can reason about.

    It exits 1 for any command mentioning mutmut and 0 otherwise, and appends
    every invocation to *log*. Asking the target interpreter rather than the
    running one is Round 47 F2's whole point, so the test pins it.
    """
    bindir = tmp_path / ".venv" / ("Scripts" if os.name == "nt" else "bin")
    bindir.mkdir(parents=True)
    py = bindir / ("python.exe" if os.name == "nt" else "python")
    py.write_text(
        "#!/bin/sh\n"
        f'printf "%s\\n" "$*" >> "{log}"\n'
        'case "$*" in *mutmut*) exit 1 ;; esac\n'
        "exit 0\n",
        encoding="utf-8",
    )
    py.chmod(0o755)
    return py


def test_an_uninstalled_skip_inline_tool_keeps_the_pip_round_alive(tmp_path, monkeypatch):
    """mutmut absent from the target venv must surface as unsatisfied."""
    from scripts import bootstrap_env
    from harness import tool_checks

    log = tmp_path / "probe.log"
    _fake_venv(tmp_path, log)
    monkeypatch.setattr(tool_checks, "run_tool_check", lambda *_a, **_kw: True)

    result = bootstrap_env.unsatisfied_tools(tmp_path)

    assert any("mutmut" in r for r in result), (
        "mutmut is skip_inline, is not installed in the target venv, and was "
        f"reported satisfied — pip would never run. got: {result}"
    )
    assert log.exists() and "mutmut" in log.read_text(encoding="utf-8"), (
        "the probe never asked the target interpreter about the distribution"
    )
