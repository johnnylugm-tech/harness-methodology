"""Round 114 站2 — advance-phase judges linting and type safety with the gate's tools.

`_precheck_p3_security_and_quality` ran `ruff check .` and `mypy .` at every
advance from P3 on, each only when `shutil.which` happened to find it. Gate 1
scores the same two dimensions with the tool its YAML names — ruff and
pyright for Python — over `{src_target}`. Two judges, two tools, two trees:

  taskq-open, the tree before 6df7c21:  Gate 2 type_safety (pyright, src) 0
  errors over 47 files; advance-phase `mypy .` 7 errors, blocked exit 19. The
  project's own SPEC.md:421 names pyright for type_safety.

  `ruff check .` reached the framework-written harness_cli.py shim
  (`import subprocess, sys, pathlib`, E401), and the agent hand-edited a file
  marked "do not edit manually" to get past it (f19cd01).

Now the advance asks the Gate 1 question: the tool `resolve_tool_id` gives
for the project's language, run by `run_tool` on the same target, scored by
`compute_tool_score`, against Gate 1's threshold.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from cli.advance_prechecks import _lint_and_type_verdict

pytestmark = [
    pytest.mark.core,
    pytest.mark.skipif(not (shutil.which("pyright") and shutil.which("ruff")),
                       reason="needs the gate tools on PATH"),
]

#: mypy rejects the reassignment (list[int] then list[str]); pyright narrows.
_MYPY_ONLY = (
    "def f() -> list[str]:\n"
    "    items = [1, 2]\n"
    "    items = [str(i) for i in items]\n"
    "    return items\n"
)


def _project(tmp_path: Path, body: str) -> Path:
    (tmp_path / ".methodology").mkdir()
    (tmp_path / ".methodology" / "state.json").write_text(
        json.dumps({"current_phase": 3, "language": "python"}), encoding="utf-8")
    pkg = tmp_path / "03-development" / "src" / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text('"""pkg."""\n', encoding="utf-8")
    (pkg / "mod.py").write_text('"""mod."""\n\n\n' + body, encoding="utf-8")
    (tmp_path / "03-development" / "tests").mkdir()
    # The framework's own shim, as init-project used to write it.
    (tmp_path / "harness_cli.py").write_text(
        '"""shim."""\nimport subprocess, sys, pathlib\n', encoding="utf-8")
    return tmp_path


def test_a_tree_the_gate_passes_is_not_blocked_by_another_type_checker(tmp_path):
    assert _lint_and_type_verdict(_project(tmp_path, _MYPY_ONLY)) is None


def test_a_type_error_the_gate_tool_sees_still_blocks(tmp_path, capsys):
    proj = _project(tmp_path, 'def g() -> int:\n    return "x"\n')
    assert _lint_and_type_verdict(proj) == 19
    assert "Type Safety (pyright) failure" in capsys.readouterr().out


def test_a_lint_finding_in_the_source_blocks(tmp_path, capsys):
    proj = _project(tmp_path, "import os, sys\n\n\ndef h() -> int:\n    return 1\n")
    assert _lint_and_type_verdict(proj) == 18
    assert "Linting (ruff) failure" in capsys.readouterr().out


def test_the_owner_classifier_reads_the_new_message():
    from core.fault_owner import Owner, classify_fault

    assert classify_fault(exit_code=19, text="[BLOCKED] Type Safety (pyright) failure.").owner == Owner.PROJECT
    assert classify_fault(exit_code=18, text="[BLOCKED] Linting (eslint) failure.").owner == Owner.PROJECT


def test_the_shim_init_project_writes_passes_lint():
    """A file the framework writes into the delivered tree passes the
    project's own lint: one import per line."""
    from cli import project_cmds

    src = Path(project_cmds.__file__).read_text(encoding="utf-8")
    assert "import subprocess, sys, pathlib" not in src
