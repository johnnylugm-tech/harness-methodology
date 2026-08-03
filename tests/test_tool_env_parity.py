"""Round 32 站0/站3 — the harness must be able to run the tool it is judging by.

S4 re-runs each dimension's tool so an agent-claimed score can be checked
against a real measurement. When the harness cannot run the tool, the result
today is not "unverified" — it is an accusation:

    .methodology/last_block.md (a live P4 Gate 1, FR-01)
      1. tool_score_fabrication
         - architecture_constraints: fabrication detected — harness ran
           'import-linter' and scored 0.0 (below threshold 100.0), but agent
           reported 100.0 (above threshold).

Reproduced (a src-layout project whose package is not installed into the venv,
which is every project that relies on pytest's `pythonpath` setting):

    no PYTHONPATH  -> Could not find package 'probeapp'  rc=1  -> score 0.0
    with PYTHONPATH -> Contracts: 1 kept, 0 broken        rc=0  -> score 100.0

`run_tool` injects the project's source root into PYTHONPATH — but only for
`cmd[0] == "pytest"` (harness/tool_runners.py:166). The Round 16 comment above
that line describes exactly this failure for the pytest family ("`import
<package>` fails collection (test_coverage silently scores 0)") and fixes it
for pytest alone. import-linter is the same defect in the same function.

Premise P3, measured per tool on a src-layout fixture with a real intra-package
import, so the fix cannot loosen a score by accident:

    ruff / mypy / bandit / radon-cc / radon-mi / readability-v2 / gitleaks
        unchanged — none of them resolve the project's imports
    pyright         95.0 -> 100.0   (an unresolved-import diagnostic disappears)
    import-linter    0.0 -> 100.0
    pytest-cov       0.0 -> 100.0   (rc=2, a collection error)

Every score that moves, moves UP, and every rise is a false negative the
harness itself manufactured. Nothing is relaxed.
"""
from __future__ import annotations

import os
import subprocess

import pytest

import harness_cli  # noqa: F401  entry-first load order
import harness.tool_runners as tr  # noqa: E402

pytestmark = [pytest.mark.core]

# Tools that must resolve the project's own package to say anything true.
# gitleaks/ruff/radon are deliberately absent: they read files, not imports,
# and this test makes no claim about them either way.
IMPORT_RESOLVING_TOOLS = ("import-linter", "pyright", "pytest-cov", "mypy")


@pytest.fixture()
def src_layout_project(tmp_path):
    """A project whose package is importable only via its source root."""
    src = tmp_path / "03-development" / "src" / "probeapp"
    src.mkdir(parents=True)
    (src / "__init__.py").write_text("", encoding="utf-8")
    (src / "core.py").write_text(
        "from probeapp.helper import scale\n\n\n"
        "def add(a: int, b: int) -> int:\n    return scale(a + b)\n",
        encoding="utf-8",
    )
    (src / "helper.py").write_text(
        "def scale(n: int) -> int:\n    return n * 2\n", encoding="utf-8"
    )
    (tmp_path / "03-development" / "tests").mkdir(parents=True)
    (tmp_path / ".importlinter").write_text(
        "[importlinter]\nroot_package = probeapp\nname = probeapp\n\n"
        "[importlinter:contract:layers]\nname = layers\ntype = layers\n"
        "layers =\n    probeapp.core\n    probeapp.helper\n",
        encoding="utf-8",
    )
    return tmp_path


def _captured_env(monkeypatch, tool: str, root) -> dict:
    seen: dict = {}

    def _fake_run(cmd, **kwargs):
        seen["cmd"] = list(cmd)
        seen["env"] = kwargs.get("env")
        return subprocess.CompletedProcess(cmd, 0, stdout="[]", stderr="")

    monkeypatch.setattr(tr.subprocess, "run", _fake_run)
    tr.run_tool(tool, str(root))
    assert "cmd" in seen, f"{tool}: run_tool never reached subprocess.run"
    return seen


@pytest.mark.parametrize("tool", IMPORT_RESOLVING_TOOLS)
def test_a_tool_that_imports_the_project_is_given_the_projects_import_root(
    tool, src_layout_project, monkeypatch
):
    seen = _captured_env(monkeypatch, tool, src_layout_project)
    env = seen["env"]
    src = str(src_layout_project / "03-development" / "src")
    assert env is not None, (
        f"{tool} runs with env=None (the harness's own environment). On a "
        f"src-layout project it cannot import the package it is judging, and "
        f"S4 reports that as the agent fabricating the score."
    )
    assert src in (env.get("PYTHONPATH") or "").split(os.pathsep), (
        f"{tool} does not receive {src!r} on PYTHONPATH: "
        f"{env.get('PYTHONPATH')!r}"
    )


def test_an_existing_pythonpath_is_extended_not_replaced(
    src_layout_project, monkeypatch
):
    """The counter-case: a caller who set PYTHONPATH meant it. Round 16 got
    this right for pytest; widening the injection must not lose it."""
    monkeypatch.setenv("PYTHONPATH", "/caller/set/this")
    seen = _captured_env(monkeypatch, "import-linter", src_layout_project)
    parts = (seen["env"].get("PYTHONPATH") or "").split(os.pathsep)
    assert "/caller/set/this" in parts, parts
    assert str(src_layout_project / "03-development" / "src") in parts, parts


def test_a_project_with_no_source_root_still_runs_the_tool(tmp_path, monkeypatch):
    """No src dir to inject is not a reason to refuse to run. The degradation
    ledger already covers the scan-scope half of this (Round 31 站6)."""
    (tmp_path / ".methodology").mkdir()
    seen = _captured_env(monkeypatch, "import-linter", tmp_path)
    assert seen["cmd"], seen


def test_the_test_target_comes_from_the_one_resolver():
    """Round 25 named 'four call sites each hand-rolling the argv' as the
    defect and unified them behind resolve_targets. run_tool calls it — and
    throws away the test target it returns, re-deriving it from a hardcoded
    two-branch probe ten lines earlier (tool_runners.py:116-120)."""
    import inspect

    src = inspect.getsource(tr.run_tool)
    assert 'os.path.join(root, "03-development", "tests")' not in src, (
        "run_tool still hardcodes the test directory probe next to its own "
        "call to resolve_targets, whose first return value is that answer"
    )
