"""Unit tests for scripts/ensure_project_init.py and harness_cli ensure-init subcommand.

Atomic TDD Protocol:
  test_id_01_check_init_status_all_present: All required components exist -> returns True, []
  test_id_02_check_init_status_missing_state: Missing state.json -> detected
  test_id_03_check_init_status_missing_venv: Missing .venv/bin/python -> detected
  test_id_04_check_init_status_missing_ci: Missing CI workflow -> detected
  test_id_05_ensure_project_init_skips_when_clean: Fully initialized project exits 0 without subcommands
  test_id_06_ensure_project_init_executes_bootstrap_and_init: Incomplete project triggers bootstrap & init
  test_id_07_ensure_project_init_commits_changes_when_dirty: Untracked files trigger git commit & push
  test_id_08_cli_ensure_init_check_only_flag: --check-only flag behaves correctly
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from scripts.ensure_project_init import check_project_init, ensure_project_init, main


@pytest.fixture
def mock_project(tmp_path: Path) -> Path:
    """Create a mock initialized project tree."""
    proj = tmp_path / "mock_target"
    proj.mkdir(parents=True)

    # .methodology/state.json
    meth = proj / ".methodology"
    meth.mkdir(parents=True)
    (meth / "state.json").write_text(json.dumps({"state": "RUNNING", "current_phase": 1}), encoding="utf-8")

    # .methodology/trace/attestation.json
    trace = meth / "trace"
    trace.mkdir(parents=True)
    (trace / "attestation.json").write_text("{}", encoding="utf-8")

    # .github/workflows/harness_quality_gate.yml
    ci_dir = proj / ".github" / "workflows"
    ci_dir.mkdir(parents=True)
    (ci_dir / "harness_quality_gate.yml").write_text("name: CI\n", encoding="utf-8")

    # .gitleaks.toml
    (proj / ".gitleaks.toml").write_text("[extend]\n", encoding="utf-8")

    # .venv/bin/python
    venv_bin = proj / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    py = venv_bin / "python"
    py.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    py.chmod(0o755)

    # .git
    git_dir = proj / ".git" / "hooks"
    git_dir.mkdir(parents=True)
    (git_dir / "prepare-commit-msg").write_text("#!/bin/sh\n", encoding="utf-8")

    return proj


def test_id_01_check_init_status_all_present(mock_project: Path):
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        ok, missing = check_project_init(mock_project)
        assert ok is True
        assert missing == []


def test_id_02_check_init_status_missing_state(mock_project: Path):
    (mock_project / ".methodology" / "state.json").unlink()
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        ok, missing = check_project_init(mock_project)
        assert ok is False
        assert any(".methodology/state.json" in m for m in missing)


def test_id_03_check_init_status_missing_venv(mock_project: Path):
    (mock_project / ".venv" / "bin" / "python").unlink()
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        ok, missing = check_project_init(mock_project)
        assert ok is False
        assert any(".venv/bin/python" in m for m in missing)


def test_id_04_check_init_status_missing_ci(mock_project: Path):
    (mock_project / ".github" / "workflows" / "harness_quality_gate.yml").unlink()
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        ok, missing = check_project_init(mock_project)
        assert ok is False
        assert any("harness_quality_gate.yml" in m for m in missing)


def test_id_05_ensure_project_init_skips_when_clean(mock_project: Path):
    with patch("scripts.ensure_project_init.check_project_init", return_value=(True, [])):
        with patch("subprocess.run") as mock_sub:
            rc = ensure_project_init(mock_project)
            assert rc == 0
            mock_sub.assert_not_called()


def test_id_06_ensure_project_init_executes_bootstrap_and_init(mock_project: Path):
    # Missing venv
    (mock_project / ".venv" / "bin" / "python").unlink()

    calls: list[list[str]] = []

    def fake_run(cmd, *args, **kwargs):
        calls.append(list(cmd))
        # After running, re-create the missing file so final check passes
        (mock_project / ".venv" / "bin" / "python").write_text("#!/bin/sh\n", encoding="utf-8")
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("subprocess.run", side_effect=fake_run):
        rc = ensure_project_init(mock_project, skip_push=True)
        assert rc == 0
        # Verify bootstrap_env was invoked
        assert any("bootstrap_env.py" in str(c) for c in calls)
        # Verify init-project was invoked
        assert any("init-project" in c for c in calls)


def test_id_07_ensure_project_init_commits_changes_when_dirty(mock_project: Path):
    (mock_project / ".methodology" / "state.json").unlink()

    calls: list[list[str]] = []

    def fake_run(cmd, *args, **kwargs):
        cmd_list = list(cmd)
        calls.append(cmd_list)
        # Fake git status reporting dirty
        if "git" in cmd_list and "status" in cmd_list:
            return MagicMock(returncode=0, stdout="?? new_file.txt\n")
        if "git" in cmd_list and "remote" in cmd_list:
            return MagicMock(returncode=0, stdout="origin\n")
        # Ensure final check passes
        (mock_project / ".methodology" / "state.json").write_text('{"current_phase": 1}', encoding="utf-8")
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("subprocess.run", side_effect=fake_run):
        rc = ensure_project_init(mock_project, skip_push=False)
        assert rc == 0
        # Verify git commit and push were executed
        assert any("commit" in c for c in calls)
        assert any("push" in c for c in calls)


def test_id_08_cli_ensure_init_check_only_flag(mock_project: Path):
    with patch("scripts.ensure_project_init.check_project_init", return_value=(True, [])):
        assert main(["--project", str(mock_project), "--check-only"]) == 0

    with patch("scripts.ensure_project_init.check_project_init", return_value=(False, ["missing_xyz"])):
        assert main(["--project", str(mock_project), "--check-only"]) == 1
