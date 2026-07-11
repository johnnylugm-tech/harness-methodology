"""Tests for cli/gate_cmds.py — env-check / finalize-gate / DA waivers / SAB alignment / FR-scoped overrides (split from tests/test_harness_cli.py, C1c)."""

from __future__ import annotations

import os
import shutil
import subprocess

import argparse
import json
from pathlib import Path

import harness_cli as _hc_entry  # noqa: F401  entry-first before cli imports
from cli.gate_cmds import _cmd_finalize_gate_impl, _fr_source_files_from_imports  # noqa: E402


class TestVerifyEnvCheckClaims:
    """A2: framework spot-check of env_check_result.json self-reported claims."""

    def _write(self, project: Path, payload: dict) -> None:
        work = project / ".sessi-work"
        work.mkdir(parents=True, exist_ok=True)
        (work / "env_check_result.json").write_text(json.dumps(payload))

    def test_claimed_cli_tool_missing_blocks(self, tmp_path):
        from cli.gate_cmds import _verify_env_check_claims
        self._write(tmp_path, {"cli_tools": {"required": [
            {"name": "definitely_not_a_real_tool_xyz", "present": True},
        ]}})
        findings = _verify_env_check_claims(tmp_path)
        assert any("definitely_not_a_real_tool_xyz" in f for f in findings)

    def test_claimed_absent_tool_not_flagged(self, tmp_path):
        """present:false tools are not force-verified."""
        from cli.gate_cmds import _verify_env_check_claims
        self._write(tmp_path, {"cli_tools": {"required": [
            {"name": "definitely_not_a_real_tool_xyz", "present": False},
        ]}})
        assert _verify_env_check_claims(tmp_path) == []

    def test_claimed_env_var_unset_blocks(self, tmp_path, monkeypatch):
        from cli.gate_cmds import _verify_env_check_claims
        monkeypatch.delenv("HARNESS_A2_PROBE", raising=False)
        self._write(tmp_path, {"env_vars": {"required": [
            {"name": "HARNESS_A2_PROBE", "present": True},
        ]}})
        findings = _verify_env_check_claims(tmp_path)
        assert any("HARNESS_A2_PROBE" in f for f in findings)

    def test_present_cli_tool_passes(self, tmp_path):
        """A real tool claimed present is not flagged."""
        from cli.gate_cmds import _verify_env_check_claims
        self._write(tmp_path, {"cli_tools": {"required": [
            {"name": "python3", "present": True},
        ]}})
        assert _verify_env_check_claims(tmp_path) == []

    def test_no_result_file_no_findings(self, tmp_path):
        from cli.gate_cmds import _verify_env_check_claims
        assert _verify_env_check_claims(tmp_path) == []

    def test_annotated_venv_name_not_flagged(self, tmp_path):
        """B1: 'python3 (.venv)' annotation must be stripped before PATH check.
        The base tool 'python3' exists on PATH, so no fabrication finding.
        """
        from cli.gate_cmds import _verify_env_check_claims
        self._write(tmp_path, {"cli_tools": {"required": [
            {"name": "python3 (.venv)", "present": True},
        ]}})
        assert _verify_env_check_claims(tmp_path) == [], (
            "Annotated name 'python3 (.venv)' should resolve to 'python3' "
            "which is on PATH — must not be flagged as fabrication"
        )

    def test_python_package_not_flagged_via_import(self, tmp_path):
        """B1: Python packages (e.g. 'json') not on PATH must pass via import fallback."""
        from cli.gate_cmds import _verify_env_check_claims
        # 'json' is stdlib — always importable, never a CLI binary.
        # The old code would flag it; the new code must not.
        self._write(tmp_path, {"cli_tools": {"required": [
            {"name": "json", "present": True},
        ]}})
        assert _verify_env_check_claims(tmp_path) == [], (
            "Python stdlib module 'json' must pass via import fallback, "
            "not be flagged as fabrication just because it's not on PATH"
        )

    def test_venv_python_fallback_active_venv(self, tmp_path, monkeypatch):
        """Bug #128: semantic venv-python passes if active venv is detected."""
        from cli.gate_cmds import _verify_env_check_claims
        import sys
        self._write(tmp_path, {"cli_tools": {"required": [{"name": "venv-python", "present": True}]}})
        monkeypatch.setattr(sys, "prefix", "/mock/venv")
        monkeypatch.setattr(sys, "base_prefix", "/mock/base")
        assert _verify_env_check_claims(tmp_path) == []

    def test_venv_python_fallback_inactive_unix(self, tmp_path, monkeypatch):
        """Bug #128: semantic venv-python passes if inactive venv exists (Unix)."""
        from cli.gate_cmds import _verify_env_check_claims
        import sys
        self._write(tmp_path, {"cli_tools": {"required": [{"name": "python-venv", "present": True}]}})
        monkeypatch.setattr(sys, "prefix", "/mock/base")
        monkeypatch.setattr(sys, "base_prefix", "/mock/base")
        monkeypatch.setattr(os, "name", "posix")
        (tmp_path / ".venv" / "bin").mkdir(parents=True)
        (tmp_path / ".venv" / "bin" / "python3").touch()
        assert _verify_env_check_claims(tmp_path) == []

    def test_venv_python_fallback_inactive_windows(self, tmp_path, monkeypatch):
        """Bug #128: semantic venv-python passes if inactive venv exists (Windows)."""
        from cli.gate_cmds import _verify_env_check_claims
        import sys
        self._write(tmp_path, {"cli_tools": {"required": [{"name": "venv-python3", "present": True}]}})
        monkeypatch.setattr(sys, "prefix", "/mock/base")
        monkeypatch.setattr(sys, "base_prefix", "/mock/base")
        (tmp_path / ".venv" / "Scripts").mkdir(parents=True)
        (tmp_path / ".venv" / "Scripts" / "python.exe").touch()
        with monkeypatch.context() as m:
            m.setattr(os, "name", "nt")
            assert _verify_env_check_claims(tmp_path) == []

    def test_venv_python_fallback_fails(self, tmp_path, monkeypatch):
        """Bug #128: semantic venv-python fails if no venv detected."""
        from cli.gate_cmds import _verify_env_check_claims
        import sys
        self._write(tmp_path, {"cli_tools": {"required": [{"name": "venv-python", "present": True}]}})
        monkeypatch.setattr(sys, "prefix", "/mock/base")
        monkeypatch.setattr(sys, "base_prefix", "/mock/base")
        monkeypatch.setattr(os, "name", "posix")
        findings = _verify_env_check_claims(tmp_path)
        assert len(findings) == 1
        assert "venv-python" in findings[0]

    def test_tool_in_project_venv_bin_without_virtual_env(self, tmp_path, monkeypatch):
        """Bug #129: tools installed only in project-local .venv/bin must pass
        even when $VIRTUAL_ENV is unset. Orchestrated runs invoke
        `.venv/bin/python harness_cli.py ...` directly, which never exports
        VIRTUAL_ENV — the old $VIRTUAL_ENV-only probe was dead code there.
        """
        from cli.gate_cmds import _verify_env_check_claims
        monkeypatch.delenv("VIRTUAL_ENV", raising=False)
        (tmp_path / ".venv" / "bin").mkdir(parents=True)
        (tmp_path / ".venv" / "bin" / "faketool_only_in_venv_xyz").touch()
        self._write(tmp_path, {"cli_tools": {"required": [
            {"name": "faketool_only_in_venv_xyz", "present": True},
        ]}})
        assert _verify_env_check_claims(tmp_path) == []

    def test_import_fallback_uses_project_venv_python(self, tmp_path, monkeypatch):
        """Bug #129: the import fallback must also try the project venv's
        python, not only sys.executable — whether a plugin-only package
        (e.g. pytest-cov) passes must not depend on which interpreter
        happens to run harness_cli.
        """
        from cli.gate_cmds import _verify_env_check_claims
        monkeypatch.delenv("VIRTUAL_ENV", raising=False)
        bindir = tmp_path / ".venv" / "bin"
        bindir.mkdir(parents=True)
        # Stand-in venv python: succeeds for any `-c "import ..."` probe.
        fake_py = bindir / "python"
        fake_py.write_text("#!/bin/sh\nexit 0\n")
        fake_py.chmod(0o755)
        self._write(tmp_path, {"cli_tools": {"required": [
            {"name": "pkg_only_in_project_venv_xyz", "present": True},
        ]}})
        assert _verify_env_check_claims(tmp_path) == []

    def test_python_version_semantic_name_resolves(self, tmp_path, monkeypatch):
        """Bug #129: 'python311' is a version-semantic name for the
        `python3.11` binary — honest when that binary actually exists.
        """
        from cli.gate_cmds import _verify_env_check_claims
        monkeypatch.delenv("VIRTUAL_ENV", raising=False)
        monkeypatch.setattr(shutil, "which", lambda *_a, **_k: None)
        (tmp_path / ".venv" / "bin").mkdir(parents=True)
        (tmp_path / ".venv" / "bin" / "python3.11").touch()
        self._write(tmp_path, {"cli_tools": {"required": [
            {"name": "python311", "present": True},
        ]}})
        assert _verify_env_check_claims(tmp_path) == []

    def test_absent_tool_still_flagged_with_venv_present(self, tmp_path, monkeypatch):
        """Anti-fraud must survive Bug #129 widening: a tool present nowhere
        (PATH, project venvs, import) is still flagged even when a project
        venv directory exists.
        """
        from cli.gate_cmds import _verify_env_check_claims
        monkeypatch.delenv("VIRTUAL_ENV", raising=False)
        (tmp_path / ".venv" / "bin").mkdir(parents=True)
        self._write(tmp_path, {"cli_tools": {"required": [
            {"name": "definitely_not_a_real_tool_xyz", "present": True},
        ]}})
        findings = _verify_env_check_claims(tmp_path)
        assert any("definitely_not_a_real_tool_xyz" in f for f in findings)

    def test_python_version_semantic_wrong_version_flagged(self, tmp_path, monkeypatch):
        """Anti-fraud: 'python312' claimed when only python3.11 exists must
        still be flagged — version-semantic normalization is not a blank pass.
        """
        from cli.gate_cmds import _verify_env_check_claims
        monkeypatch.delenv("VIRTUAL_ENV", raising=False)
        monkeypatch.setattr(shutil, "which", lambda *_a, **_k: None)
        (tmp_path / ".venv" / "bin").mkdir(parents=True)
        (tmp_path / ".venv" / "bin" / "python3.11").touch()
        self._write(tmp_path, {"cli_tools": {"required": [
            {"name": "python312", "present": True},
        ]}})
        findings = _verify_env_check_claims(tmp_path)
        assert any("python312" in f for f in findings)

    def test_optional_missing_not_flagged_by_verifier(self, tmp_path, monkeypatch):
        """env_vars.optional_missing is trusted by design — the verifier
        never inspects it. Vars with baked-in config defaults go here."""
        from cli.gate_cmds import _verify_env_check_claims
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.delenv("REDIS_HOST", raising=False)
        self._write(tmp_path, {
            "env_vars": {
                "required": [],
                "optional_missing": ["DATABASE_URL", "REDIS_HOST"],
            },
        })
        assert _verify_env_check_claims(tmp_path) == [], (
            "optional_missing entries must NOT be flagged — they are trusted "
            "by design (same trust model as infra_services)"
        )

    def test_mixed_scenario_only_fabricated_required_is_flagged(self, tmp_path, monkeypatch):
        """Only required[].present:true is verified. optional_missing is
        untouched — even when the same var name appears there too."""
        from cli.gate_cmds import _verify_env_check_claims
        monkeypatch.delenv("FAKE_MISSING_VAR_XYZ", raising=False)
        monkeypatch.delenv("DATABASE_URL", raising=False)
        self._write(tmp_path, {
            "env_vars": {
                "required": [
                    {"name": "FAKE_MISSING_VAR_XYZ", "present": True},
                ],
                "optional_missing": ["DATABASE_URL"],
            },
        })
        findings = _verify_env_check_claims(tmp_path)
        assert len(findings) == 1, f"expected 1 finding (FAKE_MISSING_VAR_XYZ only), got {findings}"
        assert any("FAKE_MISSING_VAR_XYZ" in f for f in findings)
        assert not any("DATABASE_URL" in f for f in findings), (
            "DATABASE_URL in optional_missing must NOT be flagged"
        )

    def test_real_exported_var_and_optional_missing_pass_together(self, tmp_path, monkeypatch):
        """A real exported var (PATH) in required + optional_missing entries
        — both pass; the real claim is verified, the optional entries trusted."""
        from cli.gate_cmds import _verify_env_check_claims
        monkeypatch.delenv("DATABASE_URL", raising=False)
        self._write(tmp_path, {
            "env_vars": {
                "required": [
                    {"name": "PATH", "present": True},
                ],
                "optional_missing": ["DATABASE_URL"],
            },
        })
        assert _verify_env_check_claims(tmp_path) == [], (
            "Real exported var must pass; optional_missing entry must not be flagged"
        )


class TestCmdRunEnvCheck:
    """Bug #127: cmd_run_env_check exit code reflects ready flag."""

    def _setup_mock_result(self, project, content: str, monkeypatch) -> None:
        import shutil
        work = project / ".sessi-work"
        work.mkdir(parents=True, exist_ok=True)
        (work / "env_check_result.json").write_text(content, encoding="utf-8")
        monkeypatch.setattr(shutil, "which", lambda _: "/fake/claude")
        class FakeProc:
            returncode = 0
            stderr = ""
        monkeypatch.setattr(subprocess, "run", lambda *_, **__: FakeProc())

    def test_ready_true_exits_0(self, tmp_path, monkeypatch):
        from harness_cli import cmd_run_env_check
        self._setup_mock_result(tmp_path, '{"ready": true}', monkeypatch)
        args = argparse.Namespace(project=str(tmp_path), phase=1, fr_id=None)
        monkeypatch.setattr("cli.gate_cmds._verify_env_check_claims", lambda _: [])
        assert cmd_run_env_check(args) == 0

    def test_ready_false_exits_1(self, tmp_path, monkeypatch, capsys):
        from harness_cli import cmd_run_env_check
        self._setup_mock_result(tmp_path, '{"ready": false}', monkeypatch)
        args = argparse.Namespace(project=str(tmp_path), phase=1, fr_id=None)
        monkeypatch.setattr("cli.gate_cmds._verify_env_check_claims", lambda _: [])
        assert cmd_run_env_check(args) == 1
        out, _ = capsys.readouterr()
        assert "[BLOCKED]" in out

    def test_missing_ready_key_exits_1(self, tmp_path, monkeypatch):
        from harness_cli import cmd_run_env_check
        self._setup_mock_result(tmp_path, '{"other": "data"}', monkeypatch)
        args = argparse.Namespace(project=str(tmp_path), phase=1, fr_id=None)
        monkeypatch.setattr("cli.gate_cmds._verify_env_check_claims", lambda _: [])
        assert cmd_run_env_check(args) == 1

    def test_missing_file_exits_1(self, tmp_path, monkeypatch):
        from harness_cli import cmd_run_env_check
        self._setup_mock_result(tmp_path, '{"ready": true}', monkeypatch)
        (tmp_path / ".sessi-work" / "env_check_result.json").unlink()
        args = argparse.Namespace(project=str(tmp_path), phase=1, fr_id=None)
        monkeypatch.setattr("cli.gate_cmds._verify_env_check_claims", lambda _: [])
        assert cmd_run_env_check(args) == 1

    def test_malformed_json_exits_1(self, tmp_path, monkeypatch):
        from harness_cli import cmd_run_env_check
        self._setup_mock_result(tmp_path, 'not valid json', monkeypatch)
        args = argparse.Namespace(project=str(tmp_path), phase=1, fr_id=None)
        monkeypatch.setattr("cli.gate_cmds._verify_env_check_claims", lambda _: [])
        assert cmd_run_env_check(args) == 1

    def test_json_not_a_dict_exits_1(self, tmp_path, monkeypatch):
        from harness_cli import cmd_run_env_check
        self._setup_mock_result(tmp_path, '["ready"]', monkeypatch)
        args = argparse.Namespace(project=str(tmp_path), phase=1, fr_id=None)
        monkeypatch.setattr("cli.gate_cmds._verify_env_check_claims", lambda _: [])
        assert cmd_run_env_check(args) == 1

    # -- Bug #138: timeout must use artifact-based success, not process exit --

    def _setup_timeout(self, tmp_path, monkeypatch, write_result: "str | None",
                       stderr_tail: str = "") -> None:
        """Mock claude CLI + subprocess.run that raises TimeoutExpired.
        When write_result is given, the fake sub-agent writes the artifact
        (with a future mtime, definitely >= sentinel) before 'timing out' —
        simulating a sub-agent killed during post-artifact wrap-up.
        """
        import shutil
        import time as _time
        monkeypatch.setattr(shutil, "which", lambda _: "/fake/claude")
        work = tmp_path / ".sessi-work"
        def _fake_run(*_a, **_k):
            if write_result is not None:
                work.mkdir(parents=True, exist_ok=True)
                rp = work / "env_check_result.json"
                rp.write_text(write_result, encoding="utf-8")
                fut = _time.time() + 60
                os.utime(rp, (fut, fut))
            raise subprocess.TimeoutExpired(
                cmd=["claude", "-p"], timeout=300,
                output=b"partial stdout", stderr=stderr_tail.encode() or None,
            )
        monkeypatch.setattr(subprocess, "run", _fake_run)

    def test_timeout_with_fresh_artifact_ready_true_exits_0(self, tmp_path, monkeypatch):
        """Sub-agent wrote a valid ready=true artifact, then got killed during
        wrap-up (finalize / final response). The check succeeded — rc must be 0.
        """
        from harness_cli import cmd_run_env_check
        self._setup_timeout(tmp_path, monkeypatch, write_result='{"ready": true}')
        monkeypatch.setattr("cli.gate_cmds._verify_env_check_claims", lambda _: [])
        args = argparse.Namespace(project=str(tmp_path), phase=1, fr_id=None)
        assert cmd_run_env_check(args) == 0

    def test_timeout_with_fresh_artifact_ready_false_exits_1(self, tmp_path, monkeypatch):
        """Artifact-based fallback must still honor ready=false."""
        from harness_cli import cmd_run_env_check
        self._setup_timeout(tmp_path, monkeypatch, write_result='{"ready": false}')
        monkeypatch.setattr("cli.gate_cmds._verify_env_check_claims", lambda _: [])
        args = argparse.Namespace(project=str(tmp_path), phase=1, fr_id=None)
        assert cmd_run_env_check(args) == 1

    def test_timeout_without_artifact_exits_1(self, tmp_path, monkeypatch):
        """No artifact written by this spawn → genuine failure, rc 1."""
        from harness_cli import cmd_run_env_check
        self._setup_timeout(tmp_path, monkeypatch, write_result=None)
        monkeypatch.setattr("cli.gate_cmds._verify_env_check_claims", lambda _: [])
        args = argparse.Namespace(project=str(tmp_path), phase=1, fr_id=None)
        assert cmd_run_env_check(args) == 1

    def test_timeout_with_stale_artifact_exits_1(self, tmp_path, monkeypatch):
        """A leftover artifact from a PREVIOUS run (mtime older than this
        run's sentinel) must not be accepted as this spawn's output.
        """
        import time as _time
        from harness_cli import cmd_run_env_check
        self._setup_timeout(tmp_path, monkeypatch, write_result=None)
        work = tmp_path / ".sessi-work"
        work.mkdir(parents=True, exist_ok=True)
        rp = work / "env_check_result.json"
        rp.write_text('{"ready": true}', encoding="utf-8")
        past = _time.time() - 3600
        os.utime(rp, (past, past))
        monkeypatch.setattr("cli.gate_cmds._verify_env_check_claims", lambda _: [])
        args = argparse.Namespace(project=str(tmp_path), phase=1, fr_id=None)
        assert cmd_run_env_check(args) == 1

    def test_timeout_prints_partial_stderr(self, tmp_path, monkeypatch, capsys):
        """Observability: TimeoutExpired partial output must be surfaced."""
        from harness_cli import cmd_run_env_check
        self._setup_timeout(tmp_path, monkeypatch, write_result=None,
                            stderr_tail="AUTH_HINT: token refresh loop")
        monkeypatch.setattr("cli.gate_cmds._verify_env_check_claims", lambda _: [])
        args = argparse.Namespace(project=str(tmp_path), phase=1, fr_id=None)
        assert cmd_run_env_check(args) == 1
        _, err = capsys.readouterr()
        assert "AUTH_HINT: token refresh loop" in err
        assert "partial stdout" in err


class TestFrSourceFilesFromImports:
    """Tests for _fr_source_files_from_imports — AST-based FR source file detection."""

    def test_from_import_matches_module_file(self, tmp_path):
        """from foo.bar import Baz → matches foo/bar.py."""

        src = tmp_path / "src"
        src.joinpath("foo").mkdir(parents=True)
        src.joinpath("foo", "bar.py").write_text("class Baz: pass", encoding="utf-8")
        test = tmp_path / "tests"
        test.mkdir()
        test_file = test / "test_fr01.py"
        test_file.write_text("from foo.bar import Baz\n", encoding="utf-8")

        result = _fr_source_files_from_imports(
            tmp_path, "tests/test_fr01.py", "src"
        )
        assert result == ["src/foo/bar.py"]

    def test_direct_import_matches_module_file(self, tmp_path):
        """import foo.bar → matches foo/bar.py."""

        src = tmp_path / "src"
        src.joinpath("foo").mkdir(parents=True)
        src.joinpath("foo", "bar.py").write_text("x = 1", encoding="utf-8")
        test = tmp_path / "tests"
        test.mkdir()
        test_file = test / "test_fr01.py"
        test_file.write_text("import foo.bar\n", encoding="utf-8")

        result = _fr_source_files_from_imports(
            tmp_path, "tests/test_fr01.py", "src"
        )
        assert result == ["src/foo/bar.py"]

    def test_stdlib_only_returns_empty(self, tmp_path):
        """Test file with only stdlib imports → returns [] (fallback)."""

        src = tmp_path / "src"
        src.mkdir()
        src.joinpath("dummy.py").write_text("x = 1", encoding="utf-8")
        test = tmp_path / "tests"
        test.mkdir()
        test_file = test / "test_fr01.py"
        test_file.write_text("import os\nimport sys\nfrom pathlib import Path\n", encoding="utf-8")

        result = _fr_source_files_from_imports(
            tmp_path, "tests/test_fr01.py", "src"
        )
        assert result == []

    def test_missing_test_file_returns_empty(self, tmp_path):
        """Test file doesn't exist → returns []."""

        src = tmp_path / "src"
        src.mkdir()
        src.joinpath("dummy.py").write_text("x = 1", encoding="utf-8")

        result = _fr_source_files_from_imports(
            tmp_path, "tests/nonexistent.py", "src"
        )
        assert result == []

    def test_syntax_error_returns_empty(self, tmp_path):
        """Unparseable test file → returns []."""

        src = tmp_path / "src"
        src.mkdir()
        src.joinpath("dummy.py").write_text("x = 1", encoding="utf-8")
        test = tmp_path / "tests"
        test.mkdir()
        test_file = test / "test_fr01.py"
        test_file.write_text("this is not valid python {{{{{\n", encoding="utf-8")

        result = _fr_source_files_from_imports(
            tmp_path, "tests/test_fr01.py", "src"
        )
        assert result == []

    def test_mixed_imports_match_multiple_files(self, tmp_path):
        """from foo.bar import Baz + import foo.baz → matches both source files."""

        src = tmp_path / "src"
        src.joinpath("foo").mkdir(parents=True)
        src.joinpath("foo", "bar.py").write_text("class Baz: pass", encoding="utf-8")
        src.joinpath("foo", "baz.py").write_text("x = 1", encoding="utf-8")
        test = tmp_path / "tests"
        test.mkdir()
        test_file = test / "test_fr01.py"
        test_file.write_text(
            "from foo.bar import Baz\nimport foo.baz\n", encoding="utf-8"
        )

        result = _fr_source_files_from_imports(
            tmp_path, "tests/test_fr01.py", "src"
        )
        assert sorted(result) == ["src/foo/bar.py", "src/foo/baz.py"]

    def test_init_py_excluded(self, tmp_path):
        """__init__.py files are excluded even when the package is imported."""

        src = tmp_path / "src"
        src.joinpath("foo").mkdir(parents=True)
        src.joinpath("foo", "__init__.py").write_text("x = 1", encoding="utf-8")
        src.joinpath("foo", "bar.py").write_text("class Baz: pass", encoding="utf-8")
        test = tmp_path / "tests"
        test.mkdir()
        test_file = test / "test_fr01.py"
        test_file.write_text("import foo\nfrom foo.bar import Baz\n", encoding="utf-8")

        result = _fr_source_files_from_imports(
            tmp_path, "tests/test_fr01.py", "src"
        )
        # foo/__init__.py should be excluded; only foo/bar.py should match
        assert result == ["src/foo/bar.py"]

    def test_missing_src_dir_returns_empty(self, tmp_path):
        """src_dir doesn't exist → returns []."""

        test = tmp_path / "tests"
        test.mkdir()
        test_file = test / "test_fr01.py"
        test_file.write_text("from foo.bar import Baz\n", encoding="utf-8")

        result = _fr_source_files_from_imports(
            tmp_path, "tests/test_fr01.py", "nonexistent_src"
        )
        assert result == []

    def test_from_import_with_alias_subpath_match(self, tmp_path):
        """from foo.bar import BazClass adds foo.bar.BazClass, which startswith-match
        module foo.bar → correctly finds foo/bar.py."""

        src = tmp_path / "src"
        src.joinpath("foo").mkdir(parents=True)
        src.joinpath("foo", "bar.py").write_text("class BazClass: pass", encoding="utf-8")
        test = tmp_path / "tests"
        test.mkdir()
        test_file = test / "test_fr01.py"
        test_file.write_text("from foo.bar import BazClass\n", encoding="utf-8")

        result = _fr_source_files_from_imports(
            tmp_path, "tests/test_fr01.py", "src"
        )
        assert result == ["src/foo/bar.py"]


# ---------------------------------------------------------------------------
# P0-A: _mark_plan_item
# ---------------------------------------------------------------------------
# P0-B: _append_dev_log_tdd_entry
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# P2-B: W2 coverage warning logic (harness_bridge.finalize_gate)
# ---------------------------------------------------------------------------

class TestW2CoverageWarning:
    """Tests for W2 sub-100% coverage advisory emitted by finalize_gate."""

    def _run_w2_logic(self, score: float, capsys) -> str:
        """Replicate the W2 branch from finalize_gate for unit testing."""
        try:
            _cov_pct = float(score)
        except (TypeError, ValueError):
            _cov_pct = 100.0
        if _cov_pct < 100.0:
            print(
                f"[W2] test_coverage {_cov_pct:.1f}% < 100 — "
                "advance-phase requires 100% on 03-development/src. "
                "Lines not exercisable in tests: add # pragma: no cover."
            )
        return capsys.readouterr().out

    def test_w2_emitted_when_coverage_below_100(self, capsys):
        out = self._run_w2_logic(95.0, capsys)
        assert "[W2]" in out
        assert "95.0%" in out
        assert "# pragma: no cover" in out

    def test_w2_emitted_at_80_percent(self, capsys):
        out = self._run_w2_logic(80.0, capsys)
        assert "[W2]" in out

    def test_w2_not_emitted_at_100_percent(self, capsys):
        out = self._run_w2_logic(100.0, capsys)
        assert "[W2]" not in out

    def test_w2_not_emitted_above_100(self, capsys):
        # edge case: score > 100 (invalid but shouldn't crash)
        out = self._run_w2_logic(100.1, capsys)
        assert "[W2]" not in out


# =============================================================================
# Bug 3 — _check_gate4_prerequisites: da_waiver skipped when tool_score ≥ threshold
# =============================================================================

class TestGate4DaWaiverThresholdCheck:
    """Bug 3: waiver must only enter da_waivers when tool_score < threshold.

    Previously the threshold check was absent — a waiver was accepted even when
    tool_score was already above threshold, which then set
    da_waiver_needs_human_review=True in quality_manifest.json as a false positive.
    """

    _LONG = "x" * 130  # > _DA_EVIDENCE_MIN_CHARS (120)

    def _make_g4(self, dim: str, tool_score: float, threshold: float | None) -> dict:
        """Minimal gate4_result.json satisfying all A3 checks for one waived dim."""
        from cli.gate_cmds import _TIER3_DIMS

        devil_advocate = {d: True for d in _TIER3_DIMS}
        evidence = {
            d: {"challenge": self._LONG, "response": self._LONG}
            for d in _TIER3_DIMS
        }
        bd: dict = {"tool_score": tool_score}
        if threshold is not None:
            bd["threshold"] = threshold
        return {
            "devil_advocate": devil_advocate,
            "devil_advocate_evidence": evidence,
            "da_waiver": {dim: True},
            "breakdown": {dim: bd},
        }

    def _run(self, tmp_path: Path, g4: dict) -> tuple[bool, set]:
        from cli.gate_cmds import _check_gate4_prerequisites

        sessi = tmp_path / ".sessi-work"
        sessi.mkdir(parents=True, exist_ok=True)
        (sessi / "gate4_result.json").write_text(
            json.dumps(g4), encoding="utf-8"
        )
        # B2 check: needs at least one score file in round_1/scores/
        scores_dir = sessi / "round_1" / "scores"
        scores_dir.mkdir(parents=True, exist_ok=True)
        (scores_dir / "architecture.json").write_text(
            json.dumps({"round": 1, "dim": "architecture", "score": 80.0}),
            encoding="utf-8",
        )
        return _check_gate4_prerequisites(tmp_path)

    def test_waiver_skipped_when_tool_score_above_threshold(self, tmp_path):
        """tool_score=100 >= threshold=80 → dimension already passes → waiver NOT applied."""
        blocked, da_waivers = self._run(
            tmp_path, self._make_g4("architecture", tool_score=100.0, threshold=80.0)
        )
        assert not blocked
        assert "architecture" not in da_waivers

    def test_waiver_skipped_at_exact_threshold(self, tmp_path):
        """tool_score == threshold → passes → waiver NOT applied."""
        blocked, da_waivers = self._run(
            tmp_path, self._make_g4("architecture", tool_score=80.0, threshold=80.0)
        )
        assert not blocked
        assert "architecture" not in da_waivers

    def test_waiver_applied_when_tool_score_below_threshold(self, tmp_path):
        """tool_score=50 < threshold=80 → dimension fails → waiver IS applied."""
        blocked, da_waivers = self._run(
            tmp_path, self._make_g4("architecture", tool_score=50.0, threshold=80.0)
        )
        assert not blocked
        assert "architecture" in da_waivers

    def test_waiver_applied_when_threshold_field_missing(self, tmp_path):
        """threshold absent → default float('inf') → waiver IS applied (conservative).

        The M1 fix: old default was 0.0, which made tool_score >= 0.0 always True
        and silently discarded every waiver.  float('inf') means 'unknown threshold
        → assume waiver is needed'.
        """
        blocked, da_waivers = self._run(
            tmp_path,
            self._make_g4("architecture", tool_score=100.0, threshold=None),
        )
        assert not blocked
        assert "architecture" in da_waivers


# =============================================================================
# Gate 3 DA waiver — _collect_da_waivers reads gate3_result.json
# =============================================================================

class TestGate3DaWaiverCollection:
    """Gate 3 honors the same artifact-backed DA waivers as Gate 4.

    Doc/code drift fix: phase4_plan.md and the phase4 workflow always claimed a
    Gate 3 architecture FAIL could be waived via da_waiver, but the CLI only
    read the waiver at gate==4. _collect_da_waivers is now gate-parametrized
    and called for gate 3 at the finalize-gate call site (waiver collection
    only — none of the Gate 4 A3-completeness/A5/B2/B3 prerequisites).
    """

    _LONG = "y" * 130  # > _DA_EVIDENCE_MIN_CHARS (120)

    def _make_g3(self, dim: str, tool_score: float, threshold: float | None,
                 evidence: bool = True, da_true: bool = True) -> dict:
        g3: dict = {
            "devil_advocate": {dim: da_true},
            "da_waiver": {dim: True},
            "breakdown": {dim: {"tool_score": tool_score}},
        }
        if threshold is not None:
            g3["breakdown"][dim]["threshold"] = threshold
        if evidence:
            g3["devil_advocate_evidence"] = {
                dim: {"challenge": self._LONG, "response": self._LONG}
            }
        return g3

    def _run(self, tmp_path: Path, g3: "dict | None") -> tuple[bool, set]:
        from cli.gate_cmds import _collect_da_waivers

        if g3 is not None:
            sessi = tmp_path / ".sessi-work"
            sessi.mkdir(parents=True, exist_ok=True)
            (sessi / "gate3_result.json").write_text(json.dumps(g3), encoding="utf-8")
        return _collect_da_waivers(tmp_path, 3)

    def test_waiver_applied_below_threshold(self, tmp_path):
        blocked, da_waivers = self._run(
            tmp_path, self._make_g3("architecture", tool_score=64.7, threshold=80.0))
        assert not blocked
        assert da_waivers == {"architecture"}

    def test_waiver_skipped_at_or_above_threshold(self, tmp_path):
        blocked, da_waivers = self._run(
            tmp_path, self._make_g3("architecture", tool_score=85.0, threshold=80.0))
        assert not blocked
        assert da_waivers == set()

    def test_blocked_when_evidence_missing(self, tmp_path):
        """Requested-but-unbacked waiver must fail loudly (fabrication guard)."""
        blocked, da_waivers = self._run(
            tmp_path, self._make_g3("architecture", tool_score=64.7, threshold=80.0,
                                    evidence=False))
        assert blocked
        assert da_waivers == set()

    def test_blocked_when_evidence_too_short(self, tmp_path):
        g3 = self._make_g3("architecture", tool_score=64.7, threshold=80.0)
        g3["devil_advocate_evidence"]["architecture"]["response"] = "too short"
        blocked, da_waivers = self._run(tmp_path, g3)
        assert blocked
        assert da_waivers == set()

    def test_no_waiver_when_devil_advocate_false(self, tmp_path):
        blocked, da_waivers = self._run(
            tmp_path, self._make_g3("architecture", tool_score=64.7, threshold=80.0,
                                    da_true=False))
        assert not blocked
        assert da_waivers == set()

    def test_no_file_returns_empty(self, tmp_path):
        blocked, da_waivers = self._run(tmp_path, None)
        assert not blocked
        assert da_waivers == set()

    def test_missing_threshold_defaults_to_waiver_applied(self, tmp_path):
        """threshold absent → float('inf') → conservative: waiver applied (M1 parity)."""
        blocked, da_waivers = self._run(
            tmp_path, self._make_g3("architecture", tool_score=100.0, threshold=None))
        assert not blocked
        assert da_waivers == {"architecture"}

    def test_gate4_reader_ignores_gate3_file(self, tmp_path):
        """_collect_da_waivers(project, 4) must not pick up gate3_result.json."""
        from cli.gate_cmds import _collect_da_waivers
        sessi = tmp_path / ".sessi-work"
        sessi.mkdir(parents=True, exist_ok=True)
        (sessi / "gate3_result.json").write_text(
            json.dumps(self._make_g3("architecture", 64.7, 80.0)), encoding="utf-8")
        blocked, da_waivers = _collect_da_waivers(tmp_path, 4)
        assert not blocked
        assert da_waivers == set()


# =============================================================================
# Bug 2 — finalize-gate persist: composite_score patched with harness score
# =============================================================================

class TestFinalizeGatePersistCompositeScore:
    """Bug 2: gate{N}_result.json persisted to .methodology/ must carry the
    harness-computed composite_score, not the agent's self-assessed raw value.

    Previously _cmd_finalize_gate_impl copied the file verbatim; the agent's
    raw composite_score was never updated with the weighted value computed by
    bridge.finalize_gate.
    """

    def _run_finalize(
        self,
        tmp_path: Path,
        monkeypatch,
        gate: int = 1,
        phase: int = 1,
        agent_score: float = 96.9,
        harness_score: float = 97.1288,
        src_json_text: str | None = None,
    ) -> int:
        from harness.harness_bridge import GateResult

        # Write the source gate result (agent-assessed) to .sessi-work/
        sessi = tmp_path / ".sessi-work"
        sessi.mkdir(parents=True, exist_ok=True)
        gate_src = sessi / f"gate{gate}_result.json"
        if src_json_text is not None:
            gate_src.write_text(src_json_text, encoding="utf-8")
        else:
            gate_src.write_text(
                json.dumps({"composite_score": agent_score, "breakdown": {}}),
                encoding="utf-8",
            )

        (tmp_path / ".methodology").mkdir(parents=True, exist_ok=True)

        # Stub out all heavyweight helpers
        monkeypatch.setattr("cli.gate_cmds._finalize_gate_preflight", lambda _a, _p: None)
        monkeypatch.setattr("cli.gate_cmds._finalize_gate_fr_checks", lambda _a, _p: None)
        monkeypatch.setattr("cli.gate_cmds._finalize_gate_cross_checks", lambda _a, _p: None)
        monkeypatch.setattr("cli.gate_cmds._update_state_checkpoint", lambda *_, **__: None)
        monkeypatch.setattr("core.claude_md.update_claude_md", lambda _p: None)
        from core.quality_gate import gate1_evidence as _ge
        monkeypatch.setattr(_ge, "record_gate_timestamp", lambda *_a: None)
        monkeypatch.setattr("cli._shared._generate_stage_pass", lambda *_a: None)

        _harness_score = harness_score

        class FakeGit:
            def ensure_gitignore(self): pass
            def commit_fr_gate1(self, *_a): return True
            def commit_and_push_gate(self, *_a): return True

        monkeypatch.setattr("cli._shared._make_git", lambda *_a: FakeGit())

        class FakeBridge:
            def prepare_gate(self, **_):
                return object()

            def finalize_gate(self, _ctx, **_):
                return GateResult(
                    gate_num=gate,
                    score=_harness_score,
                    dimensions=[],
                    open_critical=0,
                    open_high=0,
                    quality_complete=True,
                    rounds_used=1,
                )

        import harness.harness_bridge as hb
        monkeypatch.setattr(hb, "HarnessBridge", FakeBridge)

        args = argparse.Namespace(
            project=str(tmp_path),
            gate=gate,
            phase=phase,
            fr_id=None,
        )
        return _cmd_finalize_gate_impl(args)

    def test_composite_score_patched_with_harness_score(self, tmp_path, monkeypatch):
        """Persisted gate result must carry the harness-computed score, not agent's."""
        rc = self._run_finalize(
            tmp_path, monkeypatch, agent_score=96.9, harness_score=97.1288
        )
        assert rc == 0
        persisted = json.loads(
            (tmp_path / ".methodology" / "gate1_result.json").read_text()
        )
        assert persisted["composite_score"] == round(97.1288, 4), (
            f"expected {round(97.1288, 4)}, got {persisted['composite_score']}"
        )

    def test_malformed_source_json_falls_back_to_verbatim(self, tmp_path, monkeypatch):
        """Malformed gate result JSON → verbatim fallback, not a crash."""
        rc = self._run_finalize(
            tmp_path, monkeypatch, src_json_text="NOT_VALID_JSON{"
        )
        assert rc == 0
        # Verbatim text written as-is
        persisted_raw = (tmp_path / ".methodology" / "gate1_result.json").read_text()
        assert persisted_raw == "NOT_VALID_JSON{"


# =============================================================================
# Bug #118 — finalize-gate must patch quality_manifest.json gate_results
# =============================================================================

class TestFinalizeGateManifestPatch:
    """Bug #118: finalize-gate must keep quality_manifest.gate_results in sync.

    Before the fix, gate_results stayed at its prior value (often null).
    The next phase's entry_gate reads gate_results.gate{N} and would block
    on null even though finalize-gate had successfully written gate{N}_result.json.

    Two patching paths:
      Gate 1: per-FR dict under gate_results.gate1.{fr_id}
      Gate 2+: composite block at gate_results.gate{N}
    """

    def _run(
        self,
        tmp_path: Path,
        monkeypatch,
        gate: int,
        phase: int,
        fr_id: str | None,
        initial_manifest: dict,
        harness_score: float = 88.5,
    ) -> tuple[int, dict]:
        from harness.harness_bridge import GateResult

        sessi = tmp_path / ".sessi-work"
        sessi.mkdir(parents=True, exist_ok=True)
        (sessi / f"gate{gate}_result.json").write_text(
            json.dumps({"composite_score": harness_score}), encoding="utf-8"
        )
        meth = tmp_path / ".methodology"
        meth.mkdir(parents=True, exist_ok=True)
        manifest_path = meth / "quality_manifest.json"
        manifest_path.write_text(json.dumps(initial_manifest), encoding="utf-8")

        monkeypatch.setattr("cli.gate_cmds._finalize_gate_preflight", lambda _a, _p: None)
        monkeypatch.setattr("cli.gate_cmds._finalize_gate_fr_checks", lambda _a, _p: None)
        monkeypatch.setattr("cli.gate_cmds._finalize_gate_cross_checks", lambda _a, _p: None)
        monkeypatch.setattr("cli.gate_cmds._update_state_checkpoint", lambda *_, **__: None)
        monkeypatch.setattr("core.claude_md.update_claude_md", lambda _p: None)
        from core.quality_gate import gate1_evidence as _ge
        monkeypatch.setattr(_ge, "record_gate_timestamp", lambda *_a: None)
        monkeypatch.setattr("cli._shared._generate_stage_pass", lambda *_a: None)

        class FakeGit:
            def ensure_gitignore(self): pass
            def commit_fr_gate1(self, *_a): return True
            def commit_and_push_gate(self, *_a): return True

        monkeypatch.setattr("cli._shared._make_git", lambda *_a: FakeGit())

        # Stub PhaseHooks so gate≥2 post-flight structural checks pass
        import core.phase_hooks as ph_mod
        class _FakeHooks:
            def __init__(self, *_a, **_): pass
            def postflight_artifact_links(self): return {"passed": True}
            def postflight_drift_check(self): return {"passed": True}
        monkeypatch.setattr(ph_mod, "PhaseHooks", _FakeHooks)

        # Stub PhaseTruthVerifier so HR-11 check passes (last gate of phase)
        import core.quality_gate.phase_truth_verifier as ptv_mod
        class _FakePTV:
            def __init__(self, *_a, **_): pass
            def verify(self): return {"passed": True, "total_score": 100.0}
        monkeypatch.setattr(ptv_mod, "PhaseTruthVerifier", _FakePTV)

        _score = harness_score

        class FakeBridge:
            def prepare_gate(self, **_): return object()
            def finalize_gate(self, _ctx, **_):
                return GateResult(
                    gate_num=gate, score=_score, dimensions=[],
                    open_critical=0, open_high=0,
                    quality_complete=True, rounds_used=1,
                )

        import harness.harness_bridge as hb
        monkeypatch.setattr(hb, "HarnessBridge", FakeBridge)

        args = argparse.Namespace(
            project=str(tmp_path), gate=gate, phase=phase, fr_id=fr_id,
        )
        rc = _cmd_finalize_gate_impl(args)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        return rc, manifest

    def test_gate1_patches_per_fr_dict(self, tmp_path, monkeypatch):
        """Gate 1 finalize must write gate_results.gate1.{fr_id} with score."""
        initial = {"fr_ids": ["FR-01"], "gate_results": {"gate1": {}}}
        rc, manifest = self._run(
            tmp_path, monkeypatch, gate=1, phase=3, fr_id="FR-01",
            initial_manifest=initial, harness_score=91.0,
        )
        assert rc == 0
        g1 = manifest["gate_results"]["gate1"]
        assert "FR-01" in g1, f"FR-01 not patched into gate1: {g1}"
        assert g1["FR-01"]["score"] == 91.0
        assert g1["FR-01"]["quality_complete"] is True

    def test_gate2_patches_composite_block(self, tmp_path, monkeypatch):
        """Gate 2 finalize must write gate_results.gate2 composite block."""
        initial = {"fr_ids": ["FR-01"], "gate_results": {"gate1": {}, "gate2": None}}
        rc, manifest = self._run(
            tmp_path, monkeypatch, gate=2, phase=3, fr_id=None,
            initial_manifest=initial, harness_score=78.5,
        )
        assert rc == 0
        g2 = manifest["gate_results"]["gate2"]
        assert isinstance(g2, dict), f"gate2 not patched to dict: {g2}"
        assert g2["score"] == 78.5
        assert g2["quality_complete"] is True
        assert g2["gate"] == 2
        assert g2["phase"] == 3

    def test_gate1_increments_rounds_used(self, tmp_path, monkeypatch):
        """Re-finalizing Gate 1 increments rounds_used from the prior value."""
        initial = {
            "fr_ids": ["FR-01"],
            "gate_results": {"gate1": {"FR-01": {"score": 70.0, "rounds_used": 2}}},
        }
        _, manifest = self._run(
            tmp_path, monkeypatch, gate=1, phase=3, fr_id="FR-01",
            initial_manifest=initial, harness_score=88.0,
        )
        assert manifest["gate_results"]["gate1"]["FR-01"]["rounds_used"] == 3

    def test_manifest_write_is_atomic(self, tmp_path, monkeypatch):
        """quality_manifest.json must be written via atomic_write_json (F1 fix)."""
        import core.atomic_io as aio
        captured: list[Path] = []
        original = aio.atomic_write_json

        def spy(path, data, **kw):
            captured.append(Path(path))
            return original(path, data, **kw)

        monkeypatch.setattr(aio, "atomic_write_json", spy)
        monkeypatch.setattr("cli.gate_cmds.atomic_write_json", spy)

        initial = {"fr_ids": ["FR-01"], "gate_results": {"gate2": None}}
        self._run(
            tmp_path, monkeypatch, gate=2, phase=3, fr_id=None,
            initial_manifest=initial,
        )
        manifest_writes = [p for p in captured if p.name == "quality_manifest.json"]
        assert manifest_writes, (
            "quality_manifest.json was not written via atomic_write_json (F1 regression)"
        )


# =============================================================================
# finalize-gate optimistically patches quality_manifest.json's gate_results
# BEFORE the git commit is attempted. Every phase workflow (phase3..8-*.js)
# treats quality_complete==True as the SOLE authority a gate passed. If the
# git commit fails (e.g. prepare-commit-msg hook rejects a stale trace
# attestation) after that optimistic write, the manifest must be rolled back
# to quality_complete=False and the CLI must return a non-zero exit code —
# otherwise the manifest keeps lying and downstream workflow verify steps
# wrongly conclude the gate passed and was durably committed.
# =============================================================================

class TestFinalizeGateCommitFailureRollback:
    def _run(
        self,
        tmp_path: Path,
        monkeypatch,
        gate: int,
        phase: int,
        fr_id: str | None,
        initial_manifest: dict,
        commit_ok: bool,
        post_push_calls: list | None = None,
        harness_score: float = 96.6,
    ) -> tuple[int, dict]:
        from harness.harness_bridge import GateResult

        sessi = tmp_path / ".sessi-work"
        sessi.mkdir(parents=True, exist_ok=True)
        (sessi / f"gate{gate}_result.json").write_text(
            json.dumps({"composite_score": harness_score}), encoding="utf-8"
        )
        meth = tmp_path / ".methodology"
        meth.mkdir(parents=True, exist_ok=True)
        manifest_path = meth / "quality_manifest.json"
        manifest_path.write_text(json.dumps(initial_manifest), encoding="utf-8")

        monkeypatch.setattr("cli.gate_cmds._finalize_gate_preflight", lambda _a, _p: None)
        monkeypatch.setattr("cli.gate_cmds._finalize_gate_fr_checks", lambda _a, _p: None)
        monkeypatch.setattr("cli.gate_cmds._finalize_gate_cross_checks", lambda _a, _p: None)
        monkeypatch.setattr("cli.gate_cmds._update_state_checkpoint", lambda *_, **__: None)
        monkeypatch.setattr("core.claude_md.update_claude_md", lambda _p: None)
        from core.quality_gate import gate1_evidence as _ge
        monkeypatch.setattr(_ge, "record_gate_timestamp", lambda *_a: None)
        monkeypatch.setattr("cli._shared._generate_stage_pass", lambda *_a: None)
        monkeypatch.setattr("cli._shared._post_push_self_check",
            lambda _p: (post_push_calls.append(1) if post_push_calls is not None else None) or [],
        )

        class FakeGit:
            def ensure_gitignore(self): pass
            def commit_fr_gate1(self, *_a): return commit_ok
            def commit_and_push_gate(self, *_a): return commit_ok

        monkeypatch.setattr("cli._shared._make_git", lambda *_a: FakeGit())

        # Stub PhaseHooks so gate≥2 post-flight structural checks pass
        import core.phase_hooks as ph_mod
        class _FakeHooks:
            def __init__(self, *_a, **_): pass
            def postflight_artifact_links(self): return {"passed": True}
            def postflight_drift_check(self): return {"passed": True}
        monkeypatch.setattr(ph_mod, "PhaseHooks", _FakeHooks)

        # Stub PhaseTruthVerifier so HR-11 check passes (last gate of phase)
        import core.quality_gate.phase_truth_verifier as ptv_mod
        class _FakePTV:
            def __init__(self, *_a, **_): pass
            def verify(self): return {"passed": True, "total_score": 100.0}
        monkeypatch.setattr(ptv_mod, "PhaseTruthVerifier", _FakePTV)

        _score = harness_score

        class FakeBridge:
            def prepare_gate(self, **_): return object()
            def finalize_gate(self, _ctx, **_):
                return GateResult(
                    gate_num=gate, score=_score, dimensions=[],
                    open_critical=0, open_high=0,
                    quality_complete=True, rounds_used=2,
                )

        import harness.harness_bridge as hb
        monkeypatch.setattr(hb, "HarnessBridge", FakeBridge)

        args = argparse.Namespace(
            project=str(tmp_path), gate=gate, phase=phase, fr_id=fr_id,
        )
        rc = _cmd_finalize_gate_impl(args)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        return rc, manifest

    def test_gate2_commit_failure_rolls_back_quality_complete(self, tmp_path, monkeypatch):
        initial = {"fr_ids": ["FR-01"], "gate_results": {"gate1": {}, "gate2": None}}
        rc, manifest = self._run(
            tmp_path, monkeypatch, gate=2, phase=3, fr_id=None,
            initial_manifest=initial, commit_ok=False,
        )
        assert rc == 6
        g2 = manifest["gate_results"]["gate2"]
        assert g2["quality_complete"] is False, (
            f"gate2.quality_complete must roll back to False on commit failure: {g2}"
        )

    def test_gate1_commit_failure_rolls_back_quality_complete(self, tmp_path, monkeypatch):
        initial = {"fr_ids": ["FR-01"], "gate_results": {"gate1": {}}}
        rc, manifest = self._run(
            tmp_path, monkeypatch, gate=1, phase=3, fr_id="FR-01",
            initial_manifest=initial, commit_ok=False,
        )
        assert rc == 6
        g1 = manifest["gate_results"]["gate1"]["FR-01"]
        assert g1["quality_complete"] is False, (
            f"gate1.FR-01.quality_complete must roll back to False on commit failure: {g1}"
        )

    def test_commit_success_keeps_quality_complete_true(self, tmp_path, monkeypatch):
        initial = {"fr_ids": ["FR-01"], "gate_results": {"gate1": {}, "gate2": None}}
        rc, manifest = self._run(
            tmp_path, monkeypatch, gate=2, phase=3, fr_id=None,
            initial_manifest=initial, commit_ok=True,
        )
        assert rc == 0
        assert manifest["gate_results"]["gate2"]["quality_complete"] is True

    def test_post_push_dirty_check_skipped_when_commit_failed(self, tmp_path, monkeypatch):
        calls: list = []
        initial = {"fr_ids": ["FR-01"], "gate_results": {"gate1": {}, "gate2": None}}
        rc, _ = self._run(
            tmp_path, monkeypatch, gate=2, phase=3, fr_id=None,
            initial_manifest=initial, commit_ok=False, post_push_calls=calls,
        )
        assert rc == 6
        assert calls == [], "_post_push_self_check must not run when the commit itself failed"


# =============================================================================
# D2 variance check must tolerate a None-scored dimension
# =============================================================================

class TestFinalizeGateNoneDimVariance:
    """Regression: the D2 score-uniformity check must not crash on score=None.

    harness_bridge legitimately emits dimensions with score=None (a not-yet-
    applicable dim — e.g. the CRG architecture override or a benchmark-less
    perf dim). finalize-gate ran statistics.pstdev/sum over the raw scores;
    a None raised TypeError AFTER the manifest patch but before the gate was
    finalized — a split-write that left gate_results recorded while the
    finalized sentinel / fr_progress were never written.
    """

    def _run_with_dims(self, tmp_path, monkeypatch, dims):
        from harness.harness_bridge import GateResult

        sessi = tmp_path / ".sessi-work"
        sessi.mkdir(parents=True, exist_ok=True)
        (sessi / "gate1_result.json").write_text(
            json.dumps({"composite_score": 92.0}), encoding="utf-8"
        )
        meth = tmp_path / ".methodology"
        meth.mkdir(parents=True, exist_ok=True)
        (meth / "quality_manifest.json").write_text(
            json.dumps({"fr_ids": ["FR-01"], "gate_results": {"gate1": {}}}),
            encoding="utf-8",
        )

        monkeypatch.setattr("cli.gate_cmds._finalize_gate_preflight", lambda _a, _p: None)
        monkeypatch.setattr("cli.gate_cmds._finalize_gate_fr_checks", lambda _a, _p: None)
        monkeypatch.setattr("cli.gate_cmds._finalize_gate_cross_checks", lambda _a, _p: None)
        monkeypatch.setattr("cli.gate_cmds._update_state_checkpoint", lambda *_, **__: None)
        monkeypatch.setattr("core.claude_md.update_claude_md", lambda _p: None)
        from core.quality_gate import gate1_evidence as _ge
        monkeypatch.setattr(_ge, "record_gate_timestamp", lambda *_a: None)
        monkeypatch.setattr("cli._shared._generate_stage_pass", lambda *_a: None)

        class FakeGit:
            def ensure_gitignore(self): pass
            def commit_fr_gate1(self, *_a): return True
            def commit_and_push_gate(self, *_a): return True
        monkeypatch.setattr("cli._shared._make_git", lambda *_a: FakeGit())

        class FakeBridge:
            def prepare_gate(self, **_): return object()
            def finalize_gate(self, _ctx, **_):
                return GateResult(
                    gate_num=1, score=92.0, dimensions=dims,
                    open_critical=0, open_high=0,
                    quality_complete=True, rounds_used=1,
                )
        import harness.harness_bridge as hb
        monkeypatch.setattr(hb, "HarnessBridge", FakeBridge)

        args = argparse.Namespace(
            project=str(tmp_path), gate=1, phase=3, fr_id="FR-01",
        )
        return _cmd_finalize_gate_impl(args)

    def test_none_scored_dim_does_not_crash_finalize(self, tmp_path, monkeypatch):
        """A None-scored dim must be skipped, not crash pstdev/sum (split-write)."""
        from harness.harness_bridge import DimResult
        dims = [
            DimResult(name="linting", score=100.0, threshold=90.0),
            DimResult(name="type_safety", score=95.0, threshold=85.0),
            DimResult(name="test_coverage", score=90.0, threshold=80.0),
            DimResult(name="architecture", score=None, threshold=80.0),  # type: ignore[arg-type]
        ]
        rc = self._run_with_dims(tmp_path, monkeypatch, dims)
        assert rc == 0, f"finalize crashed/blocked on a None-scored dim: rc={rc}"
        # The gate must actually be finalized: gate_results.gate1 patched with FR-01.
        g1 = json.loads(
            (tmp_path / ".methodology" / "quality_manifest.json").read_text()
        )["gate_results"]["gate1"]
        assert "FR-01" in g1, f"gate not finalized (split-write): {g1}"


# =============================================================================
# _check_sab_module_alignment  (Amendment Protocol, commit 631782b)
# =============================================================================

class TestSabModuleAlignmentCheck:
    """Architecture Amendment Protocol: Gate 1 must block if any .py file in src/
    is absent from SAB.json modules list.  Prior to the fix, parser.modules() was
    called on a @property (TypeError), silently caught, so the check always passed."""

    def _make_sab(self, tmp_path: Path, modules: list) -> None:
        (tmp_path / ".methodology").mkdir(exist_ok=True)
        sab_data = {
            "layers": [{"name": "app", "modules": modules}],
            "allowed_dependencies": [],
        }
        (tmp_path / ".methodology" / "SAB.json").write_text(
            json.dumps(sab_data), encoding="utf-8"
        )

    def test_skips_when_gate_not_1(self, tmp_path):
        from cli.gate_cmds import _check_sab_module_alignment
        self._make_sab(tmp_path, [])
        src = tmp_path / "src"
        src.mkdir()
        (src / "app.py").write_text("x = 1")
        assert _check_sab_module_alignment(str(tmp_path), gate=2) is None

    def test_skips_when_no_sab_json(self, tmp_path):
        from cli.gate_cmds import _check_sab_module_alignment
        src = tmp_path / "src"
        src.mkdir()
        (src / "app.py").write_text("x = 1")
        assert _check_sab_module_alignment(str(tmp_path), gate=1) is None

    def test_skips_when_no_src_dir(self, tmp_path):
        from cli.gate_cmds import _check_sab_module_alignment
        self._make_sab(tmp_path, [])
        assert _check_sab_module_alignment(str(tmp_path), gate=1) is None

    def test_blocks_on_unregistered_module(self, tmp_path):
        from cli.gate_cmds import _check_sab_module_alignment
        self._make_sab(tmp_path, [])  # no modules registered
        src = tmp_path / "src"
        src.mkdir()
        (src / "app.py").write_text("x = 1")
        assert _check_sab_module_alignment(str(tmp_path), gate=1) == 1

    def test_passes_when_all_modules_registered(self, tmp_path):
        from cli.gate_cmds import _check_sab_module_alignment
        self._make_sab(tmp_path, ["app"])
        src = tmp_path / "src"
        src.mkdir()
        (src / "app.py").write_text("x = 1")
        assert _check_sab_module_alignment(str(tmp_path), gate=1) is None

    def test_skips_init_files(self, tmp_path):
        from cli.gate_cmds import _check_sab_module_alignment
        self._make_sab(tmp_path, [])  # no modules registered
        src = tmp_path / "src"
        src.mkdir()
        (src / "__init__.py").write_text("")  # must not count as a module
        assert _check_sab_module_alignment(str(tmp_path), gate=1) is None

    def test_prefers_03_development_src(self, tmp_path):
        from cli.gate_cmds import _check_sab_module_alignment
        self._make_sab(tmp_path, [])
        dev_src = tmp_path / "03-development" / "src"
        dev_src.mkdir(parents=True)
        (dev_src / "module.py").write_text("x = 1")
        fallback = tmp_path / "src"
        fallback.mkdir()  # fallback exists but should not be used
        # 03-development/src is preferred; "module" not in SAB → blocked
        assert _check_sab_module_alignment(str(tmp_path), gate=1) == 1

    def test_nested_module_path(self, tmp_path):
        from cli.gate_cmds import _check_sab_module_alignment
        self._make_sab(tmp_path, ["core.utils"])
        src = tmp_path / "src"
        (src / "core").mkdir(parents=True)
        (src / "core" / "utils.py").write_text("x = 1")
        assert _check_sab_module_alignment(str(tmp_path), gate=1) is None

    def test_sab_path_notation_matches_actual(self, tmp_path):
        """SAB entries written as ``03-development/src/<pkg>/<mod>.py`` (real
        project layout — paths written by ``scripts/generate_sab.py``) must
        match actual files even though the format differs from dotted
        notation. Regression: prior to the fix, every module was reported as
        unregistered because path-format SAB entries never intersected with
        dotted-format actual module names."""
        from cli.gate_cmds import _check_sab_module_alignment
        self._make_sab(
            tmp_path,
            [
                "03-development/src/taskq/cli.py",
                "03-development/src/taskq/store.py",
                "03-development/src/taskq/executor.py",
                "03-development/src/__main__.py",
            ],
        )
        src = tmp_path / "03-development" / "src" / "taskq"
        src.mkdir(parents=True)
        for name in ("cli.py", "store.py", "executor.py"):
            (src / name).write_text("x = 1")
        (tmp_path / "03-development" / "src" / "__main__.py").write_text("")
        assert _check_sab_module_alignment(str(tmp_path), gate=1) is None

    def test_sab_path_notation_detects_unregistered(self, tmp_path):
        """Path-notation SAB listing must still detect unregistered modules —
        mixing SAB paths with new actual files should block Gate 1."""
        from cli.gate_cmds import _check_sab_module_alignment
        self._make_sab(tmp_path, ["03-development/src/taskq/cli.py"])
        src = tmp_path / "03-development" / "src" / "taskq"
        src.mkdir(parents=True)
        (src / "cli.py").write_text("x = 1")
        (src / "store.py").write_text("x = 1")  # not in SAB
        assert _check_sab_module_alignment(str(tmp_path), gate=1) == 1

    def test_sab_mixed_dotted_and_path(self, tmp_path):
        """SAB entries may mix dotted and path notations in different layers;
        both must normalise to the same set."""
        from cli.gate_cmds import _check_sab_module_alignment
        (tmp_path / ".methodology").mkdir(exist_ok=True)
        sab_data = {
            "layers": [
                {"name": "core", "modules": ["core.utils"]},
                {"name": "app", "modules": ["03-development/src/app/main.py"]},
            ],
            "allowed_dependencies": [],
        }
        (tmp_path / ".methodology" / "SAB.json").write_text(
            json.dumps(sab_data), encoding="utf-8"
        )
        dev_src = tmp_path / "03-development" / "src"
        dev_src.mkdir(parents=True)
        (dev_src / "core").mkdir()
        (dev_src / "core" / "utils.py").write_text("x = 1")
        (dev_src / "app").mkdir()
        (dev_src / "app" / "main.py").write_text("x = 1")
        assert _check_sab_module_alignment(str(tmp_path), gate=1) is None

    def test_dict_shaped_modules_with_implemented_in_registers_correctly(self, tmp_path):
        """SAB modules may be dict-shaped {"name": ..., "implemented_in": ...}
        (the official schema form for a module whose logical name differs
        from its physical location). Regression: prior to the fix, dict
        entries silently normalised to None, making the registered set
        permanently empty."""
        from cli.gate_cmds import _check_sab_module_alignment
        self._make_sab(tmp_path, [
            {"name": "app.cli", "implemented_in": "app.interface.cli"},
        ])
        src = tmp_path / "03-development" / "src" / "app" / "interface"
        src.mkdir(parents=True)
        (src / "cli.py").write_text("x = 1")
        assert _check_sab_module_alignment(str(tmp_path), gate=1) is None

    def test_dict_shaped_modules_fall_back_to_name_when_no_implemented_in(self, tmp_path):
        from cli.gate_cmds import _check_sab_module_alignment
        self._make_sab(tmp_path, [{"name": "app.cli"}])
        src = tmp_path / "03-development" / "src" / "app"
        src.mkdir(parents=True)
        (src / "cli.py").write_text("x = 1")
        assert _check_sab_module_alignment(str(tmp_path), gate=1) is None

    def test_dict_shaped_modules_still_blocks_on_unregistered(self, tmp_path):
        """Fixing dict-entry normalization must not disable the unregistered
        check for genuinely unregistered files."""
        from cli.gate_cmds import _check_sab_module_alignment
        self._make_sab(tmp_path, [
            {"name": "app.cli", "implemented_in": "app.interface.cli"},
        ])
        src = tmp_path / "03-development" / "src" / "app" / "interface"
        src.mkdir(parents=True)
        (src / "cli.py").write_text("x = 1")
        (src / "store.py").write_text("x = 1")  # not in SAB → must still BLOCK
        assert _check_sab_module_alignment(str(tmp_path), gate=1) == 1

    def test_sab_mixed_dict_and_string_modules(self, tmp_path):
        from cli.gate_cmds import _check_sab_module_alignment
        self._make_sab(tmp_path, [
            "app.core",
            {"name": "app.cli", "implemented_in": "app.interface.cli"},
        ])
        src = tmp_path / "03-development" / "src" / "app"
        src.mkdir(parents=True)
        (src / "core.py").write_text("x = 1")
        (src / "interface").mkdir()
        (src / "interface" / "cli.py").write_text("x = 1")
        assert _check_sab_module_alignment(str(tmp_path), gate=1) is None


# =============================================================================
# _check_sab_module_alignment — phantom branch + per-FR scoping (2026-07-08 fix)
#
# Bug: phantom detection (SAB declares a module the codebase lacks) was
# project-wide, but Gate 1 runs per-FR in sequence (P3/P5/P7/P8). Gating an
# early FR tripped on later FRs' modules that legitimately don't exist yet.
# Fix narrows the phantom set via `fr_module_traceability` before blocking.
# =============================================================================

class TestSabPhantomPerFrScoping:
    def _make_sab(self, tmp_path: Path, modules: list) -> None:
        (tmp_path / ".methodology").mkdir(exist_ok=True)
        sab_data = {
            "layers": [{"name": "app", "modules": modules}],
            "allowed_dependencies": [],
        }
        (tmp_path / ".methodology" / "SAB.json").write_text(
            json.dumps(sab_data), encoding="utf-8"
        )

    def _make_manifest(self, tmp_path: Path, *, traceability: dict, gate1: dict) -> None:
        (tmp_path / ".methodology").mkdir(exist_ok=True)
        manifest = {
            "fr_module_traceability": traceability,
            "gate_results": {"gate1": gate1},
        }
        (tmp_path / ".methodology" / "quality_manifest.json").write_text(
            json.dumps(manifest), encoding="utf-8"
        )

    def _make_src(self, tmp_path: Path, *existing_modules: str) -> None:
        src = tmp_path / "src"
        src.mkdir(exist_ok=True)
        for mod in existing_modules:
            (src / f"{mod}.py").write_text("x = 1")

    def test_phantom_blocks_without_fr_id(self, tmp_path):
        """fr_id=None preserves original unscoped (global) behavior."""
        from cli.gate_cmds import _check_sab_module_alignment
        self._make_sab(tmp_path, ["taskq.cli", "taskq.cache"])
        self._make_src(tmp_path, "taskq.cli")  # taskq.cache missing
        assert _check_sab_module_alignment(str(tmp_path), gate=1) == 1

    def test_phantom_not_owned_by_current_fr_is_skipped(self, tmp_path):
        """Reproduces the P3 FR-01 false positive: FR-01 gated first, FR-04's
        module legitimately doesn't exist yet — must NOT block FR-01."""
        from cli.gate_cmds import _check_sab_module_alignment
        self._make_sab(tmp_path, ["taskq.cli", "taskq.cache"])
        self._make_src(tmp_path, "taskq.cli")  # taskq.cache (FR-04) not built yet
        self._make_manifest(
            tmp_path,
            traceability={"FR-01": "taskq.cli", "FR-04": "taskq.cache"},
            gate1={},  # no FR has passed gate1 yet
        )
        assert _check_sab_module_alignment(str(tmp_path), gate=1, fr_id="FR-01") is None

    def test_phantom_owned_by_current_fr_still_blocks(self, tmp_path):
        """A module the FR BEING GATED owns is still its own responsibility."""
        from cli.gate_cmds import _check_sab_module_alignment
        self._make_sab(tmp_path, ["taskq.cli"])
        self._make_src(tmp_path)  # taskq.cli itself missing
        self._make_manifest(
            tmp_path,
            traceability={"FR-01": "taskq.cli"},
            gate1={},
        )
        assert _check_sab_module_alignment(str(tmp_path), gate=1, fr_id="FR-01") == 1

    def test_phantom_owned_by_already_passed_fr_still_blocks(self, tmp_path):
        """A module owned by an FR that already passed Gate 1 going missing
        is a real regression, not a sequencing artifact — must still block."""
        from cli.gate_cmds import _check_sab_module_alignment
        self._make_sab(tmp_path, ["taskq.cli", "taskq.cache"])
        self._make_src(tmp_path, "taskq.cli")  # taskq.cache (FR-04, already PASS) missing
        self._make_manifest(
            tmp_path,
            traceability={"FR-01": "taskq.cli", "FR-04": "taskq.cache"},
            gate1={"FR-04": {"score": 97.0, "quality_complete": True}},
        )
        assert _check_sab_module_alignment(str(tmp_path), gate=1, fr_id="FR-01") == 1

    def test_phantom_unowned_module_is_skipped_at_fr_gate1(self, tmp_path):
        """A module with no FR owner in fr_module_traceability at all (e.g.
        shared/entry-layer scaffolding like config/models/__main__) is not any
        FR's TDD responsibility — blocking here only punishes whichever FR
        happens to gate first. The real enforcement point for a permanently-
        missing SAB module is preflight_sab_check (phase_hooks.py:341), which
        is unconditional and phase-gated at P4 entry, independent of
        fr_module_traceability."""
        from cli.gate_cmds import _check_sab_module_alignment
        self._make_sab(tmp_path, ["taskq.cli", "taskq.config"])
        self._make_src(tmp_path, "taskq.cli")  # taskq.config never traced to any FR
        self._make_manifest(
            tmp_path,
            traceability={"FR-01": "taskq.cli"},  # taskq.config absent from mapping
            gate1={},
        )
        assert _check_sab_module_alignment(str(tmp_path), gate=1, fr_id="FR-01") is None

    def test_phantom_manifest_unreadable_falls_back_to_unscoped(self, tmp_path):
        """No quality_manifest.json at all — can't determine ownership, stay
        conservative and block (same as fr_id=None)."""
        from cli.gate_cmds import _check_sab_module_alignment
        self._make_sab(tmp_path, ["taskq.cli", "taskq.cache"])
        self._make_src(tmp_path, "taskq.cli")
        # no manifest written at all
        assert _check_sab_module_alignment(str(tmp_path), gate=1, fr_id="FR-01") == 1

    def test_phantom_list_traceability_entry(self, tmp_path):
        """fr_module_traceability entries may be list[str] (an FR owning
        multiple modules), not just str — ownership lookup must handle both."""
        from cli.gate_cmds import _check_sab_module_alignment
        self._make_sab(tmp_path, ["taskq.cli", "taskq.store"])
        self._make_src(tmp_path)  # both missing
        self._make_manifest(
            tmp_path,
            traceability={"FR-01": ["taskq.cli", "taskq.store"]},
            gate1={},
        )
        assert _check_sab_module_alignment(str(tmp_path), gate=1, fr_id="FR-01") == 1


# =============================================================================
# _print_fr_scoped_overrides_py  (FR scope uses fr_module_traceability,
# not import-based detection — commit pending)
# =============================================================================

class TestPrintFrScopedOverridesPy:
    """When ``quality_manifest.json`` declares ``fr_module_traceability[fr_id]``,
    _print_fr_scoped_overrides_py must scope coverage to that module alone,
    not to every module imported by the test file. Prior to the fix, a test
    that imported helpers from other FRs' modules reported ~1/N coverage
    instead of the true per-module coverage, blocking legitimate GATE1-DELTA
    re-evaluations in carry-forward phases (P5/P7/P8)."""

    def _setup(self, tmp_path):
        (tmp_path / ".methodology").mkdir(exist_ok=True)
        # Project layout: 03-development/{src,tests}/<pkg>/<mod>.py
        (tmp_path / "03-development" / "src" / "taskq").mkdir(parents=True)
        (tmp_path / "03-development" / "tests").mkdir(parents=True)
        for mod in ("cache", "cli", "store"):
            (tmp_path / "03-development" / "src" / "taskq" / f"{mod}.py").write_text("x = 1")
        (tmp_path / "03-development" / "tests" / "test_fr04.py").write_text(
            "from taskq.cli import cmd\n"
            "from taskq.store import load_task\n"
            "from taskq.cache import lookup\n"
        )
        return tmp_path

    def test_uses_fr_module_traceability_when_present(self, tmp_path, capsys):
        from cli.gate_cmds import _print_fr_scoped_overrides_py
        self._setup(tmp_path)
        manifest = {
            "fr_module_traceability": {"FR-04": "taskq.cache"},
            "quality_targets": {"min_coverage": 80},
        }
        _print_fr_scoped_overrides_py(
            str(tmp_path), "FR-04",
            "03-development/tests/test_fr04.py", "03-development/src",
            manifest, non_code_frs=set(), cov_threshold=80,
        )
        out = capsys.readouterr().out
        # Only the owned module (cache) should appear in the include flag.
        assert "cache.py" in out
        assert "cli.py" not in out
        assert "store.py" not in out

    def test_falls_back_to_imports_when_no_traceability(self, tmp_path, capsys):
        """No ``fr_module_traceability`` → fall back to import-based detection
        (preserves backward compatibility for projects that never declared
        the trace)."""
        from cli.gate_cmds import _print_fr_scoped_overrides_py
        self._setup(tmp_path)
        manifest = {"quality_targets": {"min_coverage": 80}}
        _print_fr_scoped_overrides_py(
            str(tmp_path), "FR-04",
            "03-development/tests/test_fr04.py", "03-development/src",
            manifest, non_code_frs=set(), cov_threshold=80,
        )
        out = capsys.readouterr().out
        # All imported modules (cache, cli, store) appear because no
        # traceability declares the FR's owned module.
        for mod in ("cache.py", "cli.py", "store.py"):
            assert f"{mod}" in out, f"{mod} should appear in fallback scope"

    def test_skips_traceability_when_owned_path_missing(self, tmp_path, capsys):
        """If fr_module_traceability points to a file that does not exist
        (e.g. stale trace after refactor), fall back to imports rather than
        reporting an empty scope."""
        from cli.gate_cmds import _print_fr_scoped_overrides_py
        self._setup(tmp_path)
        manifest = {
            "fr_module_traceability": {"FR-04": "taskq.deleted_module"},
            "quality_targets": {"min_coverage": 80},
        }
        _print_fr_scoped_overrides_py(
            str(tmp_path), "FR-04",
            "03-development/tests/test_fr04.py", "03-development/src",
            manifest, non_code_frs=set(), cov_threshold=80,
        )
        out = capsys.readouterr().out
        # Imports still drive scope when owned path is missing.
        assert "cache.py" in out
        assert "cli.py" in out

    def test_does_not_crash_on_dot_trace(self, tmp_path, capsys, recwarn):
        """fr_trace='.' (or '..') previously raised ValueError from
        Path.with_suffix() and aborted the entire Gate 1 run. A malformed
        trace must now warn and fall back to import-based detection."""
        from cli.gate_cmds import _print_fr_scoped_overrides_py
        self._setup(tmp_path)
        manifest = {
            "fr_module_traceability": {"FR-04": "."},
            "quality_targets": {"min_coverage": 80},
        }
        # Must not raise.
        _print_fr_scoped_overrides_py(
            str(tmp_path), "FR-04",
            "03-development/tests/test_fr04.py", "03-development/src",
            manifest, non_code_frs=set(), cov_threshold=80,
        )
        out = capsys.readouterr().out
        # Falls back to imports (all 3 modules appear).
        for mod in ("cache.py", "cli.py", "store.py"):
            assert mod in out, f"{mod} should appear after malformed trace"
        # And a warning was emitted.
        assert any(
            "malformed" in str(w.message) for w in recwarn.list
        ), "expected a malformed-trace warning"

    def test_does_not_crash_on_double_dot_trace(self, tmp_path, capsys, recwarn):
        """Same protection for '..' trace."""
        from cli.gate_cmds import _print_fr_scoped_overrides_py
        self._setup(tmp_path)
        manifest = {
            "fr_module_traceability": {"FR-04": ".."},
            "quality_targets": {"min_coverage": 80},
        }
        _print_fr_scoped_overrides_py(
            str(tmp_path), "FR-04",
            "03-development/tests/test_fr04.py", "03-development/src",
            manifest, non_code_frs=set(), cov_threshold=80,
        )
        out = capsys.readouterr().out
        for mod in ("cache.py", "cli.py", "store.py"):
            assert mod in out
        assert any("malformed" in str(w.message) for w in recwarn.list)

    def test_does_not_crash_on_traversal_segment_trace(self, tmp_path, capsys, recwarn):
        """A trace containing '..' as a path segment (e.g. 'taskq/../sub')
        must be rejected before any path is constructed."""
        from cli.gate_cmds import _print_fr_scoped_overrides_py
        self._setup(tmp_path)
        manifest = {
            "fr_module_traceability": {"FR-04": "taskq/../outside"},
            "quality_targets": {"min_coverage": 80},
        }
        _print_fr_scoped_overrides_py(
            str(tmp_path), "FR-04",
            "03-development/tests/test_fr04.py", "03-development/src",
            manifest, non_code_frs=set(), cov_threshold=80,
        )
        out = capsys.readouterr().out
        for mod in ("cache.py", "cli.py", "store.py"):
            assert mod in out
        assert any("malformed" in str(w.message) for w in recwarn.list)

    def test_warns_on_non_string_trace(self, tmp_path, capsys, recwarn):
        """Non-string fr_trace (int, dict, etc.) must warn and fall back
        to imports rather than silently ignoring the schema violation."""
        from cli.gate_cmds import _print_fr_scoped_overrides_py
        self._setup(tmp_path)
        manifest = {
            "fr_module_traceability": {"FR-04": 42},
            "quality_targets": {"min_coverage": 80},
        }
        _print_fr_scoped_overrides_py(
            str(tmp_path), "FR-04",
            "03-development/tests/test_fr04.py", "03-development/src",
            manifest, non_code_frs=set(), cov_threshold=80,
        )
        out = capsys.readouterr().out
        for mod in ("cache.py", "cli.py", "store.py"):
            assert mod in out
        assert any(
            "expected str or list[str]" in str(w.message) for w in recwarn.list
        )

    def test_accepts_list_of_traces(self, tmp_path, capsys, recwarn):
        """fr_trace may also be list[str] (multiple owned modules)."""
        from cli.gate_cmds import _print_fr_scoped_overrides_py
        self._setup(tmp_path)
        manifest = {
            "fr_module_traceability": {"FR-04": ["taskq.cache", "taskq.cli"]},
            "quality_targets": {"min_coverage": 80},
        }
        _print_fr_scoped_overrides_py(
            str(tmp_path), "FR-04",
            "03-development/tests/test_fr04.py", "03-development/src",
            manifest, non_code_frs=set(), cov_threshold=80,
        )
        out = capsys.readouterr().out
        # Both owned modules appear; store.py (not owned, not imported by test)
        # should not appear since traceability claims all imports already.
        assert "cache.py" in out
        assert "cli.py" in out
        assert not any("malformed" in str(w.message) for w in recwarn.list)

    def test_list_with_non_string_emits_warning(self, tmp_path, capsys, recwarn):
        """A list containing non-string entries warns and processes only
        the valid strings."""
        from cli.gate_cmds import _print_fr_scoped_overrides_py
        self._setup(tmp_path)
        manifest = {
            "fr_module_traceability": {"FR-04": ["taskq.cache", 42, None]},
            "quality_targets": {"min_coverage": 80},
        }
        _print_fr_scoped_overrides_py(
            str(tmp_path), "FR-04",
            "03-development/tests/test_fr04.py", "03-development/src",
            manifest, non_code_frs=set(), cov_threshold=80,
        )
        out = capsys.readouterr().out
        assert "cache.py" in out
        assert any(
            "non-string entries" in str(w.message) for w in recwarn.list
        )


class TestFinalizeGate4StateJsonWriteBeforePush:
    """Site 3: _cmd_finalize_gate_impl gate-4 branch must write state.json
    BEFORE git.commit_and_push_gate() and use atomic_write_json (not raw write_text).
    """

    def _run_with_spy(self, tmp_path, monkeypatch, gate=4, phase=6, push_ok=True):
        from harness.harness_bridge import GateResult

        sessi = tmp_path / ".sessi-work"
        sessi.mkdir(parents=True, exist_ok=True)
        (sessi / f"gate{gate}_result.json").write_text(
            json.dumps({"composite_score": 90.0}), encoding="utf-8"
        )
        meth = tmp_path / ".methodology"
        meth.mkdir(parents=True, exist_ok=True)
        state_path = meth / "state.json"
        state_path.write_text(json.dumps({"existing": True}), encoding="utf-8")
        (meth / "quality_manifest.json").write_text(
            json.dumps({"gate_results": {}}), encoding="utf-8"
        )

        monkeypatch.setattr("cli.gate_cmds._finalize_gate_preflight", lambda *_a: None)
        monkeypatch.setattr("cli.gate_cmds._finalize_gate_fr_checks", lambda *_a: None)
        monkeypatch.setattr("cli.gate_cmds._finalize_gate_cross_checks", lambda *_a: None)
        monkeypatch.setattr("cli.gate_cmds._check_gate4_prerequisites", lambda *_a: (False, set()))
        monkeypatch.setattr("cli.gate_cmds._update_state_checkpoint", lambda *_, **__: None)
        monkeypatch.setattr("core.claude_md.update_claude_md", lambda _p: None)
        from core.quality_gate import gate1_evidence as _ge
        monkeypatch.setattr(_ge, "record_gate_timestamp", lambda *_a: None)
        monkeypatch.setattr("cli._shared._generate_stage_pass", lambda *_a: None)

        # Bypass structural postflight (artifact links + drift) — irrelevant to
        # the write-before-push assertion and would otherwise need a fully
        # populated phase artifact tree.
        class _FakePhaseHooks:
            def __init__(self, *_a, **_kw): pass
            def postflight_artifact_links(self): return {"passed": True}
            def postflight_drift_check(self): return {"passed": True}
        import core.phase_hooks as _ph_mod
        monkeypatch.setattr(_ph_mod, "PhaseHooks", _FakePhaseHooks)

        # Bypass PhaseTruthVerifier (HR-11) — also irrelevant; requires full
        # phase artifact tree and runs only for phase exit gates.
        class _FakePhaseTruthVerifier:
            def __init__(self, *_a, **_kw): pass
            def verify(self): return {"passed": True, "total_score": 100.0}
        import core.quality_gate.phase_truth_verifier as _ptv_mod
        monkeypatch.setattr(_ptv_mod, "PhaseTruthVerifier", _FakePhaseTruthVerifier)

        call_order: list[str] = []

        class FakeGit:
            def ensure_gitignore(self): pass
            def commit_fr_gate1(self, *_a): pass
            def commit_and_push_gate(self, *_a):
                call_order.append("commit_and_push_gate")
                return push_ok

        monkeypatch.setattr("cli._shared._make_git", lambda *_a: FakeGit())

        class FakeBridge:
            def prepare_gate(self, **_): return object()
            def finalize_gate(self, _ctx, **_):
                return GateResult(
                    gate_num=gate, score=90.0, dimensions=[],
                    open_critical=0, open_high=0,
                    quality_complete=True, rounds_used=1,
                )

        import harness.harness_bridge as hb
        monkeypatch.setattr(hb, "HarnessBridge", FakeBridge)

        from core.atomic_io import atomic_write_json as _orig_atomic

        def _spy(path, data, **_kw):
            if Path(path).name == "state.json":
                call_order.append("atomic_write_json(state.json)")
            _orig_atomic(path, data)

        monkeypatch.setattr("cli.gate_cmds.atomic_write_json", _spy)

        # capture raw write_text calls on state.json
        raw_writes: list[str] = []
        _orig_write_text = Path.write_text

        def _write_text_spy(self, *a, **kw):
            if self.name == "state.json":
                raw_writes.append("write_text(state.json)")
            return _orig_write_text(self, *a, **kw)

        monkeypatch.setattr(Path, "write_text", _write_text_spy)

        args = argparse.Namespace(
            project=str(tmp_path), gate=gate, phase=phase, fr_id=None,
        )
        rc = _cmd_finalize_gate_impl(args)
        return rc, call_order, raw_writes, state_path

    def test_gate4_state_json_written_before_commit_and_push_gate(self, tmp_path, monkeypatch):
        rc, call_order, raw_writes, state_path = self._run_with_spy(
            tmp_path, monkeypatch, gate=4, phase=6
        )
        assert rc == 0

        # 1. on-disk content
        sd = json.loads(state_path.read_text(encoding="utf-8"))
        assert sd["last_milestone_command"] == "finalize-gate --gate 4 --phase 6"
        assert sd["existing"] is True

        # 2. ordering
        idx_write = call_order.index("atomic_write_json(state.json)")
        idx_push = call_order.index("commit_and_push_gate")
        assert idx_write < idx_push, (
            f"state.json write must precede commit_and_push_gate; got: {call_order}"
        )

        # 3. must use atomic_write_json, NOT raw write_text
        assert not raw_writes, (
            f"gate-4 audit write must use atomic_write_json, not raw write_text; "
            f"raw write_text calls: {raw_writes}"
        )

    def test_reverted_on_push_failure(self, tmp_path, monkeypatch):
        """B3 (弱點強化): commit_and_push_gate failing must revert the
        optimistic last_milestone_command write — same class as the
        push-milestone/push-checkpoint reverts (dd9129b): downstream
        ci_state_helper trusts last_milestone_command alone, so a failed
        gate-4 push must not read as a completed finalize."""
        rc, _call_order, _raw_writes, state_path = self._run_with_spy(
            tmp_path, monkeypatch, gate=4, phase=6, push_ok=False
        )
        assert rc == 6
        sd = json.loads(state_path.read_text(encoding="utf-8"))
        assert "last_milestone_command" not in sd, (
            "failed gate-4 push left last_milestone_command="
            f"{sd.get('last_milestone_command')!r} in state.json — a failed "
            "finalize reads as pushed"
        )
        assert sd["existing"] is True

    def test_skip_when_state_json_missing(self, tmp_path, monkeypatch):
        from harness.harness_bridge import GateResult

        sessi = tmp_path / ".sessi-work"
        sessi.mkdir(parents=True, exist_ok=True)
        (sessi / "gate4_result.json").write_text(
            json.dumps({"composite_score": 90.0}), encoding="utf-8"
        )
        (tmp_path / ".methodology").mkdir(parents=True, exist_ok=True)
        (tmp_path / ".methodology" / "quality_manifest.json").write_text(
            json.dumps({"gate_results": {}}), encoding="utf-8"
        )
        # NOTE: state.json intentionally NOT created

        monkeypatch.setattr("cli.gate_cmds._finalize_gate_preflight", lambda *_a: None)
        monkeypatch.setattr("cli.gate_cmds._finalize_gate_fr_checks", lambda *_a: None)
        monkeypatch.setattr("cli.gate_cmds._finalize_gate_cross_checks", lambda *_a: None)
        monkeypatch.setattr("cli.gate_cmds._check_gate4_prerequisites", lambda *_a: (False, set()))
        monkeypatch.setattr("cli.gate_cmds._update_state_checkpoint", lambda *_, **__: None)
        monkeypatch.setattr("core.claude_md.update_claude_md", lambda _p: None)
        from core.quality_gate import gate1_evidence as _ge
        monkeypatch.setattr(_ge, "record_gate_timestamp", lambda *_a: None)
        monkeypatch.setattr("cli._shared._generate_stage_pass", lambda *_a: None)

        # Bypass structural postflight (artifact links + drift) — irrelevant to
        # the write-before-push assertion and would otherwise need a fully
        # populated phase artifact tree.
        class _FakePhaseHooks:
            def __init__(self, *_a, **_kw): pass
            def postflight_artifact_links(self): return {"passed": True}
            def postflight_drift_check(self): return {"passed": True}
        import core.phase_hooks as _ph_mod
        monkeypatch.setattr(_ph_mod, "PhaseHooks", _FakePhaseHooks)

        # Bypass PhaseTruthVerifier (HR-11) — also irrelevant; requires full
        # phase artifact tree and runs only for phase exit gates.
        class _FakePhaseTruthVerifier:
            def __init__(self, *_a, **_kw): pass
            def verify(self): return {"passed": True, "total_score": 100.0}
        import core.quality_gate.phase_truth_verifier as _ptv_mod
        monkeypatch.setattr(_ptv_mod, "PhaseTruthVerifier", _FakePhaseTruthVerifier)

        call_order: list[str] = []

        class FakeGit:
            def ensure_gitignore(self): pass
            def commit_and_push_gate(self, *_a):
                call_order.append("commit_and_push_gate")
                return True

        monkeypatch.setattr("cli._shared._make_git", lambda *_a: FakeGit())

        class FakeBridge:
            def prepare_gate(self, **_): return object()
            def finalize_gate(self, _ctx, **_):
                return GateResult(
                    gate_num=4, score=90.0, dimensions=[],
                    open_critical=0, open_high=0,
                    quality_complete=True, rounds_used=1,
                )

        import harness.harness_bridge as hb
        monkeypatch.setattr(hb, "HarnessBridge", FakeBridge)

        args = argparse.Namespace(
            project=str(tmp_path), gate=4, phase=6, fr_id=None,
        )
        rc = _cmd_finalize_gate_impl(args)
        assert rc == 0
        # push still happened; audit write was skipped (no state.json to write)
        assert "commit_and_push_gate" in call_order


class TestFinalizeGate4PostPushDirtyWarn:
    """Site 3: _cmd_finalize_gate_impl gate-4 should warn (NOT fail) when
    post-push tree is dirty."""

    def _setup(self, tmp_path, monkeypatch, dirty_paths):
        from harness.harness_bridge import GateResult

        sessi = tmp_path / ".sessi-work"
        sessi.mkdir(parents=True, exist_ok=True)
        (sessi / "gate4_result.json").write_text(
            json.dumps({"composite_score": 90.0}), encoding="utf-8",
        )
        meth = tmp_path / ".methodology"
        meth.mkdir(parents=True, exist_ok=True)
        (meth / "state.json").write_text(
            json.dumps({"existing": True}), encoding="utf-8",
        )
        (meth / "quality_manifest.json").write_text(
            json.dumps({"gate_results": {}}), encoding="utf-8",
        )

        monkeypatch.setattr("cli.gate_cmds._finalize_gate_preflight", lambda *_a: None)
        monkeypatch.setattr("cli.gate_cmds._finalize_gate_fr_checks", lambda *_a: None)
        monkeypatch.setattr("cli.gate_cmds._finalize_gate_cross_checks", lambda *_a: None)
        monkeypatch.setattr("cli.gate_cmds._check_gate4_prerequisites",
                            lambda *_a: (False, set()))
        monkeypatch.setattr("cli.gate_cmds._update_state_checkpoint", lambda *_, **__: None)
        monkeypatch.setattr("core.claude_md.update_claude_md", lambda _p: None)
        from core.quality_gate import gate1_evidence as _ge
        monkeypatch.setattr(_ge, "record_gate_timestamp", lambda *_a: None)
        monkeypatch.setattr("cli._shared._generate_stage_pass", lambda *_a: None)

        class _FakePhaseHooks:
            def __init__(self, *_a, **_kw): pass
            def postflight_artifact_links(self): return {"passed": True}
            def postflight_drift_check(self): return {"passed": True}
        import core.phase_hooks as _ph_mod
        monkeypatch.setattr(_ph_mod, "PhaseHooks", _FakePhaseHooks)

        class _FakePhaseTruthVerifier:
            def __init__(self, *_a, **_kw): pass
            def verify(self): return {"passed": True, "total_score": 100.0}
        import core.quality_gate.phase_truth_verifier as _ptv_mod
        monkeypatch.setattr(_ptv_mod, "PhaseTruthVerifier", _FakePhaseTruthVerifier)

        call_order: list[str] = []

        class FakeGit:
            def ensure_gitignore(self): pass
            def commit_and_push_gate(self, *_a):
                call_order.append("commit_and_push_gate")
                return True

        monkeypatch.setattr("cli._shared._make_git", lambda *_a: FakeGit())
        monkeypatch.setattr("cli._shared._post_push_self_check",
            lambda _p: list(dirty_paths),
        )

        class FakeBridge:
            def prepare_gate(self, **_): return object()
            def finalize_gate(self, _ctx, **_):
                return GateResult(
                    gate_num=4, score=90.0, dimensions=[],
                    open_critical=0, open_high=0,
                    quality_complete=True, rounds_used=1,
                )
        import harness.harness_bridge as hb
        monkeypatch.setattr(hb, "HarnessBridge", FakeBridge)

        return call_order

    def test_warns_on_post_push_dirty(self, tmp_path, monkeypatch, capsys):
        call_order = self._setup(
            tmp_path, monkeypatch,
            dirty_paths=[".methodology/quality_manifest.json"],
        )
        args = argparse.Namespace(
            project=str(tmp_path), gate=4, phase=6, fr_id=None,
        )
        rc = _cmd_finalize_gate_impl(args)
        out = capsys.readouterr().out
        assert rc == 0
        assert "[WARN] post-push dirty tree" in out
        assert "quality_manifest.json" in out
        assert "commit_and_push_gate" in call_order

    def test_silent_on_clean(self, tmp_path, monkeypatch, capsys):
        call_order = self._setup(tmp_path, monkeypatch, dirty_paths=[])
        args = argparse.Namespace(
            project=str(tmp_path), gate=4, phase=6, fr_id=None,
        )
        rc = _cmd_finalize_gate_impl(args)
        out = capsys.readouterr().out
        assert rc == 0
        assert "[WARN] post-push dirty tree" not in out
        assert "commit_and_push_gate" in call_order
