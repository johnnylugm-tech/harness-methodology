"""v2.9 A2+A3 — reliability-lint and config-liveness preflight checks.

Phase semantics: P3 informational (passed=True with findings), P4+ blocking.
Real semgrep runs in two tests (ruleset smoke); the rest are pure-Python.
"""

import json
import shutil
from pathlib import Path

import pytest

from core.phase_hooks import PhaseHooks


def _project(tmp_path: Path, language: str = "python") -> Path:
    meth = tmp_path / ".methodology"
    meth.mkdir()
    (meth / "state.json").write_text(
        json.dumps({"state": "RUNNING", "current_phase": 4, "language": language}),
        encoding="utf-8",
    )
    (tmp_path / "src").mkdir()
    return tmp_path


BAD_RELIABILITY = """\
import subprocess, time

def convert(src):
    subprocess.run(["ffmpeg", "-i", src])

async def backoff():
    time.sleep(1)
"""

CLEAN_RELIABILITY = """\
import subprocess

def convert(src):
    subprocess.run(["ffmpeg", "-i", src], timeout=30)
"""


@pytest.mark.skipif(shutil.which("semgrep") is None, reason="semgrep not installed")
class TestReliabilityLint:
    def test_findings_block_at_p4(self, tmp_path):
        project = _project(tmp_path)
        (project / "src" / "bad.py").write_text(BAD_RELIABILITY, encoding="utf-8")
        result = PhaseHooks(str(project), phase=4).preflight_reliability_lint()
        assert result["passed"] is False
        assert result["blocking"] is True
        rules = {f["rule"] for f in result["findings"]}
        assert "py-subprocess-no-timeout" in rules
        assert "py-time-sleep-in-async" in rules

    def test_findings_informational_at_p3(self, tmp_path):
        project = _project(tmp_path)
        (project / "src" / "bad.py").write_text(BAD_RELIABILITY, encoding="utf-8")
        result = PhaseHooks(str(project), phase=3).preflight_reliability_lint()
        assert result["passed"] is True
        assert result["blocking"] is False
        assert result["finding_count"] >= 2

    def test_clean_project_passes_at_p4(self, tmp_path):
        project = _project(tmp_path)
        (project / "src" / "ok.py").write_text(CLEAN_RELIABILITY, encoding="utf-8")
        result = PhaseHooks(str(project), phase=4).preflight_reliability_lint()
        assert result["passed"] is True
        assert result["finding_count"] == 0


class TestReliabilityLintSkips:
    def test_non_python_language_skips(self, tmp_path):
        project = _project(tmp_path, language="typescript")
        result = PhaseHooks(str(project), phase=4).preflight_reliability_lint()
        assert result["passed"] is True and result["skipped"] is True

    def test_no_src_dirs_skips(self, tmp_path):
        project = _project(tmp_path)
        (project / "src").rmdir()
        result = PhaseHooks(str(project), phase=4).preflight_reliability_lint()
        assert result["passed"] is True and result["skipped"] is True


class TestConfigLiveness:
    def test_orphan_env_key_blocks_at_p4(self, tmp_path):
        # The tts-new bug class: typo'd env name → default always used.
        project = _project(tmp_path)
        (project / ".env.example").write_text(
            "KOKORO_BACKEND_URL=http://localhost:8880\n", encoding="utf-8"
        )
        (project / "src" / "config.py").write_text(
            'import os\n'
            'URL = os.getenv("XXKOKORO_BACKEND_URLXX", "http://localhost:8880")\n',
            encoding="utf-8",
        )
        result = PhaseHooks(str(project), phase=4).preflight_config_liveness()
        assert result["passed"] is False
        assert "XXKOKORO_BACKEND_URLXX" in result["orphans"]
        assert "src/config.py:2" in result["orphans"]["XXKOKORO_BACKEND_URLXX"]

    def test_multiline_env_read_is_scanned(self, tmp_path):
        # The real tts-new typo lived in a multi-line os.environ.get( call —
        # the key sits on the line AFTER `.get(`. A per-line scan misses it
        # entirely (backtest-discovered regression guard, v2.9 PR-7).
        project = _project(tmp_path)
        (project / ".env.example").write_text("KOKORO_BACKEND_URL=x\n", encoding="utf-8")
        (project / "src" / "config.py").write_text(
            'import os\n'
            'URL = os.environ.get(\n'
            '    "XXKOKORO_BACKEND_URLXX", "http://localhost:8880"\n'
            ')\n',
            encoding="utf-8",
        )
        result = PhaseHooks(str(project), phase=4).preflight_config_liveness()
        assert result["passed"] is False
        assert "XXKOKORO_BACKEND_URLXX" in result["orphans"]
        # Line number derives from the .get( offset, not the key's own line
        assert result["orphans"]["XXKOKORO_BACKEND_URLXX"] == "src/config.py:2"

    def test_declared_keys_pass(self, tmp_path):
        project = _project(tmp_path)
        (project / ".env.example").write_text("API_KEY=\nREDIS_URL=\n", encoding="utf-8")
        (project / "src" / "config.py").write_text(
            'import os\n'
            'A = os.getenv("API_KEY")\n'
            'B = os.environ["REDIS_URL"]\n'
            'C = os.environ.get("API_KEY", "x")\n',
            encoding="utf-8",
        )
        result = PhaseHooks(str(project), phase=4).preflight_config_liveness()
        assert result["passed"] is True
        assert result["orphans"] == {}
        assert result["used_count"] == 2

    def test_orphans_informational_at_p3(self, tmp_path):
        project = _project(tmp_path)
        (project / ".env.example").write_text("X=\n", encoding="utf-8")
        (project / "src" / "c.py").write_text(
            'import os\nY = os.getenv("NOT_DECLARED")\n', encoding="utf-8"
        )
        result = PhaseHooks(str(project), phase=3).preflight_config_liveness()
        assert result["passed"] is True and result["blocking"] is False
        assert "NOT_DECLARED" in result["orphans"]

    def test_no_declaration_sources_skips(self, tmp_path):
        project = _project(tmp_path)
        (project / "src" / "c.py").write_text(
            'import os\nY = os.getenv("WHATEVER")\n', encoding="utf-8"
        )
        result = PhaseHooks(str(project), phase=4).preflight_config_liveness()
        assert result["passed"] is True and result["skipped"] is True

    def test_system_vars_excluded(self, tmp_path):
        project = _project(tmp_path)
        (project / ".env.example").write_text("APP_KEY=\n", encoding="utf-8")
        (project / "src" / "c.py").write_text(
            'import os\n'
            'P = os.getenv("PATH")\n'
            'H = os.environ["HOME"]\n'
            'A = os.getenv("APP_KEY")\n',
            encoding="utf-8",
        )
        result = PhaseHooks(str(project), phase=4).preflight_config_liveness()
        assert result["passed"] is True
        assert result["used_count"] == 1  # only APP_KEY

    def test_js_process_env_scan(self, tmp_path):
        project = _project(tmp_path, language="typescript")
        (project / ".env.example").write_text("GOOD_KEY=\n", encoding="utf-8")
        (project / "src" / "config.ts").write_text(
            'const a = process.env.GOOD_KEY;\n'
            'const b = process.env.TYPO_KEY;\n'
            'const c = process.env["BRACKET_KEY"];\n',
            encoding="utf-8",
        )
        result = PhaseHooks(str(project), phase=4).preflight_config_liveness()
        assert result["passed"] is False
        assert set(result["orphans"]) == {"TYPO_KEY", "BRACKET_KEY"}
