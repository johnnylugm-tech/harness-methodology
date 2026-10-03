"""Round 113 站7 — the SSOT names two manifests, so the scaffold writes two.

taskq-sol's SPEC §5.3 declares `requirements.txt` (runtime, pinned) and
`requirements-dev.txt` (`import-linter` / `pip-licenses` / `mutmut` /
`pytest-benchmark` / `httpx`) as separate mandatory files. The scaffold parsed
that dev row on purpose — `_parse_spec_dev_deps_table` matches only it — and
then poured the result into `requirements.txt`. No code anywhere wrote
`requirements-dev.txt`, and env-repair never installed one a project wrote.

The row is an explicit declaration of what the dev file contains; everything
else the scaffold collects is a mention it inferred (SAD's `pip-licenses`
command, SPEC §0's intent table). An explicit declaration outranks an
inference, so a package the dev row names goes to the dev file only.
"""

from __future__ import annotations

import json
from pathlib import Path

from harness.ssot_manifest import (
    scaffold_project_manifest_from_ssot,
    unfinished_scaffolded_manifest,
)

_SPEC = (
    "## 0. Intent\n"
    "| 依賴樹淺 | fastapi / uvicorn | NFR-07 |\n"
    "\n"
    "### 5.3 Files\n"
    "| 檔案 | 用途 | 對應 |\n"
    "|------|------|------|\n"
    "| `requirements-dev.txt` | `import-linter` / `mutmut` / `httpx` | NFR-06 |\n"
)


def _project(tmp_path: Path, *, sad: str = "") -> Path:
    project = tmp_path / "proj"
    project.mkdir()
    (project / "SPEC.md").write_text(_SPEC, encoding="utf-8")
    if sad:
        arch = project / "02-architecture"
        arch.mkdir()
        (arch / "SAD.md").write_text(sad, encoding="utf-8")
    return project


def _deps(path: Path) -> "list[str]":
    return [ln for ln in path.read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.startswith("#")]


def test_runtime_and_dev_land_in_their_own_files(tmp_path):
    project = _project(tmp_path)
    outcome = scaffold_project_manifest_from_ssot(project)
    assert sorted(_deps(project / "requirements.txt")) == ["fastapi", "uvicorn"]
    assert sorted(_deps(project / "requirements-dev.txt")) == ["httpx", "import-linter", "mutmut"]
    assert outcome.dev_manifest_path == project / "requirements-dev.txt"


def test_the_declared_row_outranks_an_inferred_mention(tmp_path):
    # SAD mentions `httpx.AsyncClient` — an inference; the dev row is a declaration.
    project = _project(tmp_path, sad="Tests use `httpx.AsyncClient(app)`.\n")
    scaffold_project_manifest_from_ssot(project)
    assert "httpx" not in _deps(project / "requirements.txt")
    assert "httpx" in _deps(project / "requirements-dev.txt")


def test_an_existing_dev_manifest_is_not_overwritten(tmp_path):
    project = _project(tmp_path)
    (project / "requirements-dev.txt").write_text("mutmut==3.2.0\n", encoding="utf-8")
    outcome = scaffold_project_manifest_from_ssot(project)
    assert (project / "requirements-dev.txt").read_text() == "mutmut==3.2.0\n"
    assert outcome.dev_manifest_path is None


def test_an_unpinned_scaffolded_dev_manifest_is_unfinished(tmp_path):
    project = _project(tmp_path)
    (project / "requirements.txt").write_text("fastapi==0.115.0\n", encoding="utf-8")
    (project / "requirements-dev.txt").write_text("mutmut\n", encoding="utf-8")
    meth = project / ".methodology"
    meth.mkdir()
    (meth / "degradations.jsonl").write_text(json.dumps({
        "component": "gate:env-repair",
        "what": "SSOT scaffold wrote requirements-dev.txt (1 deps from 1 SSOT file(s))",
    }) + "\n", encoding="utf-8")
    reason = unfinished_scaffolded_manifest(project)
    assert reason and "requirements-dev.txt" in reason and "mutmut" in reason


def test_env_repair_installs_both_and_records_both(tmp_path):
    from harness.env_repair import install_project_dependencies

    project = _project(tmp_path)
    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        return type("P", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    outcome = install_project_dependencies(project, run=fake_run)
    assert outcome.ok and outcome.installed
    assert [c[-1] for c in calls] == [
        str(project / "requirements.txt"), str(project / "requirements-dev.txt")]
    ledger = (project / ".methodology" / "degradations.jsonl").read_text()
    assert "SSOT scaffold wrote requirements.txt" in ledger
    assert "SSOT scaffold wrote requirements-dev.txt" in ledger


def test_a_project_authored_dev_manifest_is_installed_too(tmp_path):
    from harness.env_repair import install_project_dependencies

    project = tmp_path / "authored"
    project.mkdir()
    (project / "requirements.txt").write_text("fastapi==0.115.0\n", encoding="utf-8")
    (project / "requirements-dev.txt").write_text("pytest==8.3.2\n", encoding="utf-8")
    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        return type("P", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    assert install_project_dependencies(project, run=fake_run).installed
    assert [c[-1] for c in calls][-1] == str(project / "requirements-dev.txt")


def test_a_failing_dev_install_blocks_and_names_the_file(tmp_path):
    from harness.env_repair import install_project_dependencies

    project = tmp_path / "authored"
    project.mkdir()
    (project / "requirements.txt").write_text("fastapi==0.115.0\n", encoding="utf-8")
    (project / "requirements-dev.txt").write_text("nope==0\n", encoding="utf-8")

    def fake_run(argv, **kwargs):
        rc = 1 if argv[-1].endswith("requirements-dev.txt") else 0
        return type("P", (), {"returncode": rc, "stdout": "", "stderr": "no such version"})()

    outcome = install_project_dependencies(project, run=fake_run)
    assert not outcome.ok and "requirements-dev.txt" in outcome.blocked_reason
