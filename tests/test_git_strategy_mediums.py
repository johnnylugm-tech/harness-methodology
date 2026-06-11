"""
Regression tests for 2 MEDIUM bugs in git_strategy:

  1. _manifest_fr_ids (line 654) — JSON.load returns raw 'fr_ids'
     without type/element validation. Malformed manifest causes
     TypeError downstream at str.join on non-strings and silent
     coercion of non-string FR IDs into commit messages.

  2. commit_fr_gate1 (line 107) — `fr_id: str` is f-string
     interpolated into commit subject with no pattern validation.
     Embedded newlines split subject/body, breaking the
     `message[:72]` display and downstream tooling that assumes
     a single subject line.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from harness.git_strategy import GitStrategy


@pytest.fixture
def gs(tmp_path: Path) -> GitStrategy:
    return GitStrategy(project=tmp_path, enabled=True, push=False)


# ── Bug 1: unvalidated fr_ids from manifest ──────────────────────────────────

class TestManifestFrIdsValidation:
    def test_manifest_with_non_string_fr_ids_rejected(
        self, gs: GitStrategy,
    ):
        """A manifest with non-string fr_ids (e.g. integers, nulls)
        must surface as ValueError, not silently coerce to broken
        commit messages via str() conversion."""
        manifest_path = gs.project / ".methodology" / "quality_manifest.json"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(
            '{"fr_ids": [123, null, {"not": "a string"}, "valid-FR-1"]}',
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="[Ff]R"):
            gs._manifest_fr_ids()

    def test_manifest_with_invalid_fr_id_format_rejected(
        self, gs: GitStrategy,
    ):
        """A manifest with fr_ids that don't match the FR-NN pattern
        (e.g. typos, injection attempts) must be rejected."""
        manifest_path = gs.project / ".methodology" / "quality_manifest.json"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        # Includes one valid and one with embedded newlines.
        manifest_path.write_text(
            '{"fr_ids": ["FR-001", "FR-002;rm -rf /", "FR-003\\nbad"]}',
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="[Ff]R"):
            gs._manifest_fr_ids()

    def test_manifest_with_valid_fr_ids_passes(
        self, gs: GitStrategy,
    ):
        """Sanity guard: a clean manifest with valid FR IDs must
        return the list unchanged."""
        manifest_path = gs.project / ".methodology" / "quality_manifest.json"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(
            '{"fr_ids": ["FR-001", "FR-002", "FR-003"]}',
            encoding="utf-8",
        )
        result = gs._manifest_fr_ids()
        assert result == ["FR-001", "FR-002", "FR-003"]


# ── Bug 2: fr_id in commit message ──────────────────────────────────────────

class TestFrIdCommitMessageValidation:
    def test_fr_id_with_newline_rejected(self, gs: GitStrategy):
        """An fr_id with embedded newlines would split the commit
        subject. Must be rejected at the API boundary."""
        with pytest.raises(ValueError, match="[Ff]R"):
            gs.commit_fr_gate1(fr_id="FR-001\nbad", score=85.0, phase=3)

    def test_fr_id_with_semicolon_rejected(self, gs: GitStrategy):
        """An fr_id with shell metachars (semicolon) would enable
        command-injection if the commit subject is ever eval'd."""
        with pytest.raises(ValueError, match="[Ff]R"):
            gs.commit_fr_gate1(fr_id="FR-001;rm -rf /", score=85.0, phase=3)

    def test_fr_id_with_non_fr_format_rejected(self, gs: GitStrategy):
        """An fr_id not matching FR-NN must be rejected (consistency
        with the manifest validator and the _auto_fr_ids regex)."""
        with pytest.raises(ValueError, match="[Ff]R"):
            gs.commit_fr_gate1(fr_id="not-a-fr-id", score=85.0, phase=3)

    def test_valid_fr_id_accepted(self, gs: GitStrategy):
        """Sanity guard: a well-formed fr_id must not be rejected
        by validation (the test ends at validation; the actual
        git commit is a no-op in tmp_path)."""
        gs._commit = MagicMock(return_value=True)  # type: ignore[method-assign]
        gs.commit_fr_gate1(fr_id="FR-001", score=85.0, phase=3)
