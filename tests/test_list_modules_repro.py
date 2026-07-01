"""Regression tests for list-modules — Bugs M13/M14.

M13 (line 34): _is_semver splits by '.' and rejects valid pre-release
   suffixes like "1.0.0-beta.1" because split gives 4 parts, not 3.
M14 (line 119): fm.get("name") raises AttributeError when yaml.safe_load
   returns None (empty frontmatter). The bare except wraps it as a
   generic "parse error" rather than a clear "empty frontmatter" msg.
"""

from __future__ import annotations

from pathlib import Path

import pytest


def _load_module():
    """Load scripts/list-modules.py — name has a hyphen so normal import
    doesn't work; use importlib.util to load by file path."""
    import importlib.util
    scripts_path = (
        Path(__file__).resolve().parents[1] / "scripts" / "list-modules.py"
    )
    spec = importlib.util.spec_from_file_location("list_modules", scripts_path)
    module = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


@pytest.fixture
def module():
    return _load_module()


# ---------------------------------------------------------------------------
# Bug M13: pre-release suffix in any segment should be accepted
# ---------------------------------------------------------------------------

class TestM13Semver:
    def test_pre_release_in_third_segment_accepted(self, module):
        """Bug M13 regression: '1.0.0-beta.1' is a valid pre-release semver.
        Previous code rejected it because split('.') gave 4 parts."""
        assert module._is_semver("1.0.0-beta.1") is True, (
            "M13: pre-release suffix in 3rd segment should be valid semver"
        )

    def test_plain_semver_works(self, module):
        """Sanity: plain '1.2.3' is still accepted."""
        assert module._is_semver("1.2.3") is True

    def test_invalid_still_rejected(self, module):
        """Sanity: clearly invalid versions are still rejected."""
        assert module._is_semver("1.2") is False
        assert module._is_semver("1.2.3.4") is False
        assert module._is_semver("abc") is False


# ---------------------------------------------------------------------------
# Bug M14: empty frontmatter must give clear diagnostic, not generic parse err
# ---------------------------------------------------------------------------

class TestM14EmptyFrontmatter:
    def test_empty_frontmatter_gives_clear_message(
        self, module, tmp_path, monkeypatch
    ):
        """Bug M14 regression: yaml.safe_load on empty string returns None.
        The previous fm.get('name') raised AttributeError caught as a
        generic 'parse error'. Should report 'empty frontmatter' clearly."""
        # Build a SKILL.md with empty frontmatter (--- ... ---)
        skill = tmp_path / "SKILL.md"
        skill.write_text("---\n---\n", encoding="utf-8")

        # Point REPO_ROOT to tmp_path so the function reads our SKILL.md
        monkeypatch.setattr(module, "REPO_ROOT", tmp_path)

        errors = module.validate_skill_md_frontmatter()
        joined = " ".join(errors).lower()
        assert "empty" in joined or "no" in joined or "name" in joined, (
            f"M14: empty frontmatter should produce clear diagnostic, "
            f"got errors={errors}"
        )
        # Should not raise AttributeError
        assert all("AttributeError" not in e for e in errors), (
            f"M14: AttributeError leaked into error message, errors={errors}"
        )

    def test_valid_frontmatter_passes(self, module, tmp_path, monkeypatch):
        """Sanity: valid frontmatter with correct name passes."""
        skill = tmp_path / "SKILL.md"
        skill.write_text(
            "---\nname: harness-methodology\nversion: 1.0.0\n---\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(module, "REPO_ROOT", tmp_path)
        errors = module.validate_skill_md_frontmatter()
        assert errors == [], f"M14: valid frontmatter should pass, got {errors}"
