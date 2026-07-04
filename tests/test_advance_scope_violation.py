"""Tests for _scope_violation_scripts — advance-phase repo-root orphan-script guard.

WRITE_SCOPE convention: agent-generated diagnostic/debug artifacts belong under
.sessi-work/tmp/ (gitignored), never the source tree. A workflow advance agent once
left _diag_constitution.py stranded at the repo root while diagnosing a constitution
BLOCK. This guard is the mechanism (not the agent-self-clean prompt) that catches it:
BLOCK on an untracked, root-level script whose name signals a diagnostic.
"""
import subprocess

from harness_cli import _scope_violation_scripts


def _git_init(path):
    subprocess.run(["git", "init", "-q"], cwd=str(path), check=True)


def test_detects_root_diagnostic_script(tmp_path):
    _git_init(tmp_path)
    (tmp_path / "_diag_constitution.py").write_text("print(1)\n")
    assert _scope_violation_scripts(tmp_path) == ["_diag_constitution.py"]


def test_ignores_root_script_without_debug_keyword(tmp_path):
    # Narrow pattern: an oddly-named legit script is NOT flagged (寧漏不誤殺).
    _git_init(tmp_path)
    (tmp_path / "helper.py").write_text("x = 1\n")
    assert _scope_violation_scripts(tmp_path) == []


def test_ignores_subdirectory_scripts(tmp_path):
    # Recursing would false-positive on legitimate new module files mid-phase.
    _git_init(tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "debug_helper.py").write_text("x = 1\n")
    assert _scope_violation_scripts(tmp_path) == []


def test_ignores_gitignored_sessi_work(tmp_path):
    _git_init(tmp_path)
    (tmp_path / ".gitignore").write_text(".sessi-work/\n")
    work = tmp_path / ".sessi-work" / "tmp"
    work.mkdir(parents=True)
    (work / "debug_scan.py").write_text("x = 1\n")
    assert _scope_violation_scripts(tmp_path) == []


def test_ignores_tracked_diag_script(tmp_path):
    # Only *untracked* orphans are flagged; a committed diag_ tool is intentional.
    _git_init(tmp_path)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "t"], check=True)
    (tmp_path / "diag_tool.py").write_text("x = 1\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "diag_tool.py"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-qm", "add"], check=True)
    assert _scope_violation_scripts(tmp_path) == []


def test_flags_multiple_script_extensions(tmp_path):
    _git_init(tmp_path)
    (tmp_path / "scratch.js").write_text("1\n")
    (tmp_path / "probe_x.sh").write_text("echo\n")
    (tmp_path / "explore.ts").write_text("1\n")
    assert set(_scope_violation_scripts(tmp_path)) == {"scratch.js", "probe_x.sh", "explore.ts"}
