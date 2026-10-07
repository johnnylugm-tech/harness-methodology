"""Round 116 站3 — every statement of "where the tests are" derives from the measured root.

Besides the layout bug (站1), the root `tests/` was still stated by:

  * `pytest-cov-integration`, which hard-coded `03-development/tests/integration`
    and `--cov=03-development/src` while its JS siblings use `{test_target}` —
    taskq-final built a symlink mirror to satisfy it;
  * the P3 workflow, which told the implementer to annotate
    `tests/test_fr<NN>.py` and to run `check-test-mirrors-spec --test-file
    tests/test_fr<NN>.py` (a path that CLI resolved against its cwd);
  * plangen ("`tests/` - Unit tests", "Create `tests/test_perf.py`", a repro
    "path in `tests/`") and the fix / TDD prompts (`tests/conftest.py`).

Each now names the root `ProjectLayout` measures.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from core.utils.project_layout import ProjectLayout

REPO = Path(__file__).resolve().parents[1]
_ROOT_TESTS = re.compile(r"(?<![\w./-])tests/")


def test_the_integration_tool_runs_the_measured_root():
    from harness.toolchains.registry import TOOL_SPECS

    cmd = TOOL_SPECS["pytest-cov-integration"].cmd
    assert "{test_target}/integration" in cmd and "--cov={cov_target}" in cmd
    assert not any("03-development" in part for part in cmd)


def test_an_frs_test_file_has_one_definition(tmp_path):
    (tmp_path / "01-requirements").mkdir()
    layout = ProjectLayout(tmp_path)
    assert layout.get_relative_str(layout.fr_test_file("FR-07")) == "03-development/tests/test_fr07.py"


def test_the_mirror_check_defaults_to_the_frs_test_file_under_the_project(tmp_path):
    (tmp_path / "01-requirements").mkdir()
    (tmp_path / "02-architecture").mkdir()
    (tmp_path / "02-architecture" / "TEST_SPEC.md").write_text("# TEST_SPEC\n", encoding="utf-8")
    out = subprocess.run([sys.executable, str(REPO / "harness_cli.py"), "check-test-mirrors-spec",
                          "--fr-id", "FR-01", "--project", str(tmp_path)],
                         capture_output=True, text=True, cwd=str(REPO))
    assert out.returncode != 2, out.stderr  # argparse accepts a call without --test-file
    assert "unrecognized" not in out.stderr and "required" not in out.stderr


def test_no_workflow_names_a_root_tests_directory():
    from scripts.workflowgen.generate_workflows import generate

    for phase in range(1, 9):
        js = generate(phase)
        hits = [m.group(0) for m in _ROOT_TESTS.finditer(js)
                if "REGRESSION_GUARDS" not in js[m.start():m.start() + 40]]
        assert not hits, (phase, hits[:3])


def test_no_fr_prompt_names_a_root_tests_directory(tmp_path):
    from cli.fr_prompts import _build_fr_step_prompt

    (tmp_path / "01-requirements").mkdir()
    for step in ("TDD-RED", "TDD-GREEN", "LINT-FIX", "INFRA-FIX", "TEST-FIX"):
        prompt = _build_fr_step_prompt(step, "FR-01", 3, tmp_path, None)
        assert not _ROOT_TESTS.search(prompt), (step, _ROOT_TESTS.findall(prompt)[:3])


def test_no_phase_plan_names_a_root_tests_directory(tmp_path):
    from scripts.generate_full_plan import generate_full_plan
    from tests.test_plangen_golden import _fixture_project

    proj = _fixture_project(tmp_path)
    for phase in range(1, 10):
        text = generate_full_plan(phase, proj, None, dynamic=False) or ""
        hits = [text[max(0, m.start() - 30):m.end() + 20] for m in _ROOT_TESTS.finditer(text)]
        assert not hits, (phase, hits[:3])
