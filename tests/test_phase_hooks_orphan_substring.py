"""Tests for phase_hooks orphan detection — substring vs exact match."""

import sys
import os
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.phase_hooks import PhaseHooks


class TestOrphanSubstringBug:
    """Regression test: substring matching must not fool orphan detection.

    Bug: 'KOKORO_BACKEND_URL' in decl_text was True when the declared key was
    LEGACY_KOKORO_BACKEND_URL_V1 (substring match, not exact match).
    """

    @pytest.fixture
    def fake_project(self, tmp_path):
        """Create a minimal project that mimics a project with env declarations."""
        # Create 03-development/src so the scanner finds it
        src_dir = tmp_path / "03-development" / "src"
        src_dir.mkdir(parents=True)

        # .env.example declares LEGACY_KOKORO_BACKEND_URL_V1 only
        env_example = tmp_path / ".env.example"
        env_example.write_text("LEGACY_KOKORO_BACKEND_URL_V1=https://legacy.example.com\n")

        # Python source reads KOKORO_BACKEND_URL (a substring of the declared key)
        py_file = src_dir / "main.py"
        py_file.write_text('import os\nos.environ["KOKORO_BACKEND_URL"]\n')

        return tmp_path

    def test_substring_key_not_mistaken_for_exact_match(self, fake_project):
        """KOKORO_BACKEND_URL must be flagged as orphaned when only LEGACY_KOKORO_BACKEND_URL_V1 is declared.

        This is a regression test for the substring-matching bug where
        'KOKORO_BACKEND_URL' in decl_text was True when decl_text contained
        'LEGACY_KOKORO_BACKEND_URL_V1'.
        """
        hooks = PhaseHooks(str(fake_project), phase=4)
        result = hooks.preflight_config_liveness()

        # KOKORO_BACKEND_URL is NOT declared in .env.example — only LEGACY_KOKORO_BACKEND_URL_V1 is
        # So it MUST appear in the orphans list
        assert "KOKORO_BACKEND_URL" in result["orphans"], (
            f"KOKORO_BACKEND_URL should be flagged as orphaned but wasn't. "
            f"Orphans: {result['orphans']}"
        )
        assert result["passed"] is False

    @pytest.fixture
    def project_with_exact_declared_key(self, tmp_path):
        """Project where the exact env key is declared."""
        src_dir = tmp_path / "03-development" / "src"
        src_dir.mkdir(parents=True)

        # Exact match: KOKORO_BACKEND_URL is declared
        env_example = tmp_path / ".env.example"
        env_example.write_text("KOKORO_BACKEND_URL=https://example.com\n")

        py_file = src_dir / "main.py"
        py_file.write_text('import os\nos.environ["KOKORO_BACKEND_URL"]\n')

        return tmp_path

    def test_exact_key_match_is_not_orphaned(self, project_with_exact_declared_key):
        """A key that exactly matches a declaration must NOT be flagged as orphaned."""
        hooks = PhaseHooks(str(project_with_exact_declared_key), phase=4)
        result = hooks.preflight_config_liveness()

        assert "KOKORO_BACKEND_URL" not in result["orphans"], (
            f"KOKORO_BACKEND_URL should NOT be orphaned when exactly declared. "
            f"Orphans: {result['orphans']}"
        )
        assert result["passed"] is True

    @pytest.fixture
    def project_with_multiple_declared_keys(self, tmp_path):
        """Project with multiple env keys, one substring of another."""
        src_dir = tmp_path / "03-development" / "src"
        src_dir.mkdir(parents=True)

        env_example = tmp_path / ".env.example"
        env_example.write_text(
            "KOKORO_BACKEND_URL=https://example.com\n"
            "LEGACY_KOKORO_BACKEND_URL_V1=https://legacy.example.com\n"
        )

        py_file = src_dir / "main.py"
        py_file.write_text(
            'import os\n'
            'url1 = os.environ["KOKORO_BACKEND_URL"]\n'
            'url2 = os.environ["LEGACY_KOKORO_BACKEND_URL_V1"]\n'
        )

        return tmp_path

    def test_both_exact_keys_declared(self, project_with_multiple_declared_keys):
        """Both keys declared exactly — neither should be orphaned."""
        hooks = PhaseHooks(str(project_with_multiple_declared_keys), phase=4)
        result = hooks.preflight_config_liveness()

        assert len(result["orphans"]) == 0, (
            f"Neither key is orphaned when both are declared. Orphans: {result['orphans']}"
        )
        assert result["passed"] is True
