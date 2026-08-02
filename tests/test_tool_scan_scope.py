"""Round 31 站0/站6 — the harness must measure what the prompt told the agent to measure.

S4 cross-validation exists to stop score fabrication: the harness re-runs the
tool itself and blocks when the agent claims a pass the tool does not support.
That only works if both sides scan the same thing.

Measured on a live Gate 2 (a consumer project with a vendored harness and a
committed virtualenv):

    .py files under the project root : 4917
      of which .venv/                : 4344
      of which the harness submodule :  537
    the project's own source         :   21

`evaluate_dimension.md` tells the agent to run `pyright src/` and
`bandit -r src/`. The ToolSpecs ran `pyright {root}` and `bandit -r {root}`.
Two numbers over two different denominators were then compared for equality —
and in practice the harness side never even finished:

    .sessi-work/harness_verification/type_safety_harness.txt
      # returncode: -2
      TIMEOUT: pyright exceeded 60s

which S4 converts into `tool_score_fabrication`, telling the agent to "Install
'pyright'" — a tool that is installed. The harness's own inability to measure
became an accusation against the measured party.

`run_tool` already resolves the project's source and test targets through
`core.quality_gate.test_suite_run.resolve_targets` for `{cov_target}`, whose
docstring documents this exact class of denominator pollution. The same
resolution now feeds `{src_target}`.
"""
from __future__ import annotations

import subprocess

import pytest

import harness_cli  # noqa: F401  entry-first load order
import harness.tool_runners as tr  # noqa: E402

pytestmark = [pytest.mark.core]

# Dimension → tool for the source-scanning Python tools. gitleaks is
# deliberately absent: secrets must be scanned across the whole repository, so
# `{root}` is correct there and must stay.
SOURCE_SCANNING_TOOLS = ("pyright", "bandit", "ruff", "radon-cc")


@pytest.fixture()
def project(tmp_path):
    """A nested-layout project with the two decoys that blew the budget."""
    src = tmp_path / "03-development" / "src" / "app"
    src.mkdir(parents=True)
    (src / "__init__.py").write_text("", encoding="utf-8")
    (src / "core.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    (tmp_path / "03-development" / "tests").mkdir(parents=True)
    for decoy in (".venv/lib/python3.11/site-packages/dep",
                  "harness/core/quality_gate"):
        d = tmp_path / decoy
        d.mkdir(parents=True)
        (d / "mod.py").write_text("x = 1\n", encoding="utf-8")
    return tmp_path


def _captured_argv(monkeypatch, tool: str, root) -> list[str]:
    seen: dict = {}

    def _fake_run(cmd, **kwargs):
        seen["cmd"] = list(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="[]", stderr="")

    monkeypatch.setattr(tr.subprocess, "run", _fake_run)
    tr.run_tool(tool, str(root))
    assert "cmd" in seen, f"{tool}: run_tool never reached subprocess.run"
    return seen["cmd"]


@pytest.mark.parametrize("tool", SOURCE_SCANNING_TOOLS)
def test_source_scanners_are_pointed_at_the_source_not_the_project_root(
    tool, project, monkeypatch
):
    argv = _captured_argv(monkeypatch, tool, project)
    root = str(project)
    src = str(project / "03-development" / "src")

    assert root not in argv, (
        f"{tool} is aimed at the project root, which contains .venv/ and the "
        f"vendored harness. The agent scans src/ (evaluate_dimension.md); "
        f"comparing the two scores compares two different denominators, and "
        f"on a real project the harness side times out first.\n  argv: {argv}"
    )
    assert src in argv, (
        f"{tool} does not name the project's source dir {src!r}: {argv}"
    )


def test_secrets_scanning_still_covers_the_whole_repository(project, monkeypatch):
    """The counter-case, so a future 'point everything at src' sweep cannot
    quietly shrink the secrets denominator: a leaked key in a config file at
    the repo root is exactly what gitleaks is for."""
    argv = _captured_argv(monkeypatch, "gitleaks", project)
    assert str(project) in argv, argv


def test_the_prompt_and_the_toolspec_scan_the_same_thing():
    """The parity that was missing. evaluate_dimension.md is what the agent
    follows; the ToolSpec is what S4 re-runs. They were `src/` and `{root}`,
    and nothing compared them — so the drift could only surface as a timeout."""
    from pathlib import Path as _P

    prompt = (_P(__file__).resolve().parents[1]
              / "harness" / "ssi" / "prompts" / "evaluate_dimension.md"
              ).read_text(encoding="utf-8")
    for tool, invocation in (("pyright", "pyright src/"), ("bandit", "bandit -r src/")):
        assert invocation in prompt, (
            f"the prompt no longer tells the agent to run `{invocation}`; "
            f"update this pairing rather than deleting it"
        )
        spec = tr.get_tool_spec(tool)
        assert spec is not None and spec.cmd is not None
        assert "{src_target}" in spec.cmd, (
            f"the prompt scans src/ for {tool} and the ToolSpec scans "
            f"{spec.cmd} — two denominators, one comparison"
        )


def test_a_project_with_no_resolvable_source_records_a_degradation(
    tmp_path, monkeypatch
):
    """resolve_targets has no '.' fallback by design, and run_tool's existing
    cov_target path falls back to '.' when the resolved dir does not exist.
    Falling back silently is how Round 29's station 2 shipped a fix that could
    never fire: the code path existed, the value it produced named nothing.
    A project the harness cannot resolve must leave a ledger line."""
    (tmp_path / ".methodology").mkdir()
    _captured_argv(monkeypatch, "pyright", tmp_path)
    ledger = tmp_path / ".methodology" / "degradations.jsonl"
    assert ledger.is_file() and ledger.read_text(encoding="utf-8").strip(), (
        "no source dir resolved and nothing recorded — the scan silently fell "
        "back to the whole project root"
    )
    assert "scan-scope" in ledger.read_text(encoding="utf-8")
