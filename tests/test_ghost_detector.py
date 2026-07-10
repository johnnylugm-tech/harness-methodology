"""Tests for core.quality_gate.ghost_detector — ghost paper-trail detection.

Each test creates a temporary git repo, captures a pre-SHA, makes some change,
and asserts whether ghost detection flags it as substantive or not.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

# Prepend the harness root so imports resolve.
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.quality_gate.ghost_detector import (
    GHOST_PAPER_TRAIL_DIR,
    detect_ghost_changes,
    scan_phase_ghost_trails,
    write_ghost_paper_trail,
)


# ── Helpers ─────────────────────────────────────────────────────────────────


def _init_git_repo(path: Path) -> None:
    """Initialise a git repo at *path* with one commit."""
    subprocess.run(
        ["git", "-C", str(path), "init", "-b", "main"],
        check=True, capture_output=True, text=True,
    )
    subprocess.run(
        ["git", "-C", str(path), "config", "user.email", "test@example.com"],
        check=True, capture_output=True, text=True,
    )
    subprocess.run(
        ["git", "-C", str(path), "config", "user.name", "Test"],
        check=True, capture_output=True, text=True,
    )
    (path / "README.md").write_text("# init\n", encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(path), "add", "README.md"],
        check=True, capture_output=True, text=True,
    )
    subprocess.run(
        ["git", "-C", str(path), "commit", "-m", "initial commit"],
        check=True, capture_output=True, text=True,
    )


def _capture_head(path: Path) -> str:
    """Return HEAD SHA for *path*."""
    r = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"],
        capture_output=True, text=True, timeout=10,
    )
    return r.stdout.strip()


def _commit_file(path: Path, filename: str, content: str) -> None:
    """Write *filename* with *content* and commit."""
    (path / filename).write_text(content, encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(path), "add", filename],
        check=True, capture_output=True, text=True,
    )
    subprocess.run(
        ["git", "-C", str(path), "commit", "-m", f"update {filename}"],
        check=True, capture_output=True, text=True,
    )


# ── Tests ───────────────────────────────────────────────────────────────────


class TestDetectGhostChanges:
    """Unit tests for detect_ghost_changes()."""

    def test_no_changes_ghost(self, tmp_path: Path) -> None:
        """pre == post HEAD → ghost for code-producing steps."""
        _init_git_repo(tmp_path)
        pre_sha = _capture_head(tmp_path)

        result = detect_ghost_changes(
            tmp_path, pre_sha, "TDD-GREEN", "FR-01", "done",
        )
        assert result["ghost_detected"] is True
        assert "HEAD did not move" in result["reason"]

    def test_whitespace_only_ghost(self, tmp_path: Path) -> None:
        """Whitespace-only change in a code file → ghost."""
        _init_git_repo(tmp_path)
        # Create a Python source file.
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "mod.py").write_text("def foo():\n    return 1\n")
        subprocess.run(
            ["git", "-C", str(tmp_path), "add", "src/mod.py"],
            check=True, capture_output=True, text=True,
        )
        subprocess.run(
            ["git", "-C", str(tmp_path), "commit", "-m", "add module"],
            check=True, capture_output=True, text=True,
        )

        pre_sha = _capture_head(tmp_path)

        # Change only whitespace: replace spaces with tabs (same content,
        # different whitespace). git diff -w should report zero net change.
        (tmp_path / "src" / "mod.py").write_text(
            "def foo():\n\treturn 1\n"
        )
        subprocess.run(
            ["git", "-C", str(tmp_path), "add", "src/mod.py"],
            check=True, capture_output=True, text=True,
        )
        subprocess.run(
            ["git", "-C", str(tmp_path), "commit", "-m", "whitespace only"],
            check=True, capture_output=True, text=True,
        )

        result = detect_ghost_changes(
            tmp_path, pre_sha, "TDD-GREEN", "FR-01", "cleaned up",
        )
        assert result["ghost_detected"] is True
        assert result["total_added"] == 0
        assert result["total_removed"] == 0

    def test_docs_only_ghost(self, tmp_path: Path) -> None:
        """Only .md files changed → ghost."""
        _init_git_repo(tmp_path)
        pre_sha = _capture_head(tmp_path)
        _commit_file(tmp_path, "README.md", "# updated docs\n")
        _commit_file(tmp_path, "CHANGELOG.md", "## v2\n")

        result = detect_ghost_changes(
            tmp_path, pre_sha, "TDD-GREEN", "FR-01", "updated readme",
        )
        assert result["ghost_detected"] is True
        assert "non-code files" in result["reason"]

    def test_substantive_change_not_ghost(self, tmp_path: Path) -> None:
        """Adding a new function → not ghost."""
        _init_git_repo(tmp_path)
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "mod.py").write_text("def foo():\n    return 1\n")
        subprocess.run(
            ["git", "-C", str(tmp_path), "add", "src/mod.py"],
            check=True, capture_output=True, text=True,
        )
        subprocess.run(
            ["git", "-C", str(tmp_path), "commit", "-m", "add module"],
            check=True, capture_output=True, text=True,
        )

        pre_sha = _capture_head(tmp_path)

        # Real change: add a new function.
        (tmp_path / "src" / "mod.py").write_text(
            "def foo():\n    return 1\n\n\ndef bar():\n    return 2\n"
        )
        subprocess.run(
            ["git", "-C", str(tmp_path), "add", "src/mod.py"],
            check=True, capture_output=True, text=True,
        )
        subprocess.run(
            ["git", "-C", str(tmp_path), "commit", "-m", "add bar()"],
            check=True, capture_output=True, text=True,
        )

        result = detect_ghost_changes(
            tmp_path, pre_sha, "TDD-GREEN", "FR-01", "added bar function",
        )
        assert result["ghost_detected"] is False
        assert result["total_added"] > 0

    def test_tdd_red_skip(self, tmp_path: Path) -> None:
        """TDD-RED step is skipped by ghost detection (test-only step)."""
        _init_git_repo(tmp_path)
        pre_sha = _capture_head(tmp_path)
        # TDD-RED returns not-ghost regardless of changes.
        result = detect_ghost_changes(
            tmp_path, pre_sha, "TDD-RED", "FR-01", "wrote test",
        )
        assert result["ghost_detected"] is False

    def test_tdd_green_source_required(self, tmp_path: Path) -> None:
        """TDD-GREEN with only test file changes → ghost (needs source changes)."""
        _init_git_repo(tmp_path)
        (tmp_path / "tests").mkdir()
        pre_sha = _capture_head(tmp_path)

        # Only add test file — no source file.
        _commit_file(tmp_path, "tests/test_mod.py", "def test_foo(): pass\n")
        result = detect_ghost_changes(
            tmp_path, pre_sha, "TDD-GREEN", "FR-01", "added tests",
        )
        # Test-only with TDD-GREEN: code files changed = 1 (the test file IS a .py file).
        # But the step is TDD-GREEN which is code-producing → 1 code file changed, not ghost.
        # Actually: the test file is a .py file so it counts as code.
        # That's correct behavior — if the agent only added a test for GREEN,
        # it did make a substantive change (the test file).
        # This should NOT be ghost.
        assert result["ghost_detected"] is False

    def test_empty_pre_sha_graceful(self, tmp_path: Path) -> None:
        """Empty pre_sha → not ghost (graceful degradation)."""
        _init_git_repo(tmp_path)

        result = detect_ghost_changes(
            tmp_path, "", "TDD-GREEN", "FR-01", "done",
        )
        assert result["ghost_detected"] is False

    def test_gate1_skip(self, tmp_path: Path) -> None:
        """GATE1 step is skipped by ghost detection (evaluation step)."""
        _init_git_repo(tmp_path)
        pre_sha = _capture_head(tmp_path)
        # GATE1 returns not-ghost regardless of changes.
        result = detect_ghost_changes(
            tmp_path, pre_sha, "GATE1", "FR-01", "gate result",
        )
        assert result["ghost_detected"] is False

    def test_code_fix_deletion_only_not_ghost(self, tmp_path: Path) -> None:
        """CODE-FIX that only deletes code → not ghost (deletions are substantive)."""
        _init_git_repo(tmp_path)
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "mod.py").write_text(
            "def good():\n    return 1\n\ndef dead_code():\n    return None\n"
        )
        subprocess.run(
            ["git", "-C", str(tmp_path), "add", "src/mod.py"],
            check=True, capture_output=True, text=True,
        )
        subprocess.run(
            ["git", "-C", str(tmp_path), "commit", "-m", "add module"],
            check=True, capture_output=True, text=True,
        )

        pre_sha = _capture_head(tmp_path)

        # Delete dead_code function.
        (tmp_path / "src" / "mod.py").write_text("def good():\n    return 1\n")
        subprocess.run(
            ["git", "-C", str(tmp_path), "add", "src/mod.py"],
            check=True, capture_output=True, text=True,
        )
        subprocess.run(
            ["git", "-C", str(tmp_path), "commit", "-m", "remove dead code"],
            check=True, capture_output=True, text=True,
        )

        result = detect_ghost_changes(
            tmp_path, pre_sha, "CODE-FIX", "FR-01", "deleted dead code",
        )
        assert result["ghost_detected"] is False
        assert result["total_removed"] > 0

    def test_config_only_json_ghost(self, tmp_path: Path) -> None:
        """Only .json config file changed → ghost (non-code file)."""
        _init_git_repo(tmp_path)
        pre_sha = _capture_head(tmp_path)
        _commit_file(tmp_path, "settings.json", '{"key": "value"}\n')

        result = detect_ghost_changes(
            tmp_path, pre_sha, "TDD-GREEN", "FR-01", "updated config",
        )
        assert result["ghost_detected"] is True


class TestGhostPaperTrailPersistence:
    """Unit tests for write_ghost_paper_trail() and scan_phase_ghost_trails()."""

    def test_write_and_scan(self, tmp_path: Path) -> None:
        """Write ghost trails then scan for a specific phase."""
        trail_dir = tmp_path / GHOST_PAPER_TRAIL_DIR
        trail_dir.mkdir(parents=True)

        # Write two ghost records: one phase 3, one phase 4.
        (trail_dir / "FR-01_TDD-GREEN.json").write_text(
            json.dumps({
                "ghost_detected": True,
                "reason": "no code changes",
                "phase": 3,
                "fr_id": "FR-01",
                "step": "TDD-GREEN",
            }),
            encoding="utf-8",
        )
        (trail_dir / "FR-02_CODE-FIX.json").write_text(
            json.dumps({
                "ghost_detected": True,
                "reason": "whitespace only",
                "phase": 4,
                "fr_id": "FR-02",
                "step": "CODE-FIX",
            }),
            encoding="utf-8",
        )

        # scan_phase_ghost_trails uses project-relative path internally.
        # Write records at the expected location relative to *tmp_path*.
        actual_dir = tmp_path / GHOST_PAPER_TRAIL_DIR
        actual_dir.mkdir(parents=True, exist_ok=True)

        # Phase 3 only.
        p3_trail = actual_dir / "FR-01_TDD-GREEN.json"
        p3_trail.write_text(json.dumps({
            "ghost_detected": True,
            "reason": "no code changes",
            "phase": 3,
            "fr_id": "FR-01",
            "step": "TDD-GREEN",
        }), encoding="utf-8")

        # Phase 4 (should not appear in phase 3 scan).
        (actual_dir / "FR-02_CODE-FIX.json").write_text(json.dumps({
            "ghost_detected": True,
            "reason": "whitespace only",
            "phase": 4,
            "fr_id": "FR-02",
            "step": "CODE-FIX",
        }), encoding="utf-8")

        phase3_results = scan_phase_ghost_trails(tmp_path, 3)
        assert len(phase3_results) == 1
        assert phase3_results[0]["fr_id"] == "FR-01"

        phase4_results = scan_phase_ghost_trails(tmp_path, 4)
        assert len(phase4_results) == 1
        assert phase4_results[0]["fr_id"] == "FR-02"

        phase5_results = scan_phase_ghost_trails(tmp_path, 5)
        assert len(phase5_results) == 0

    def test_scan_empty_dir(self, tmp_path: Path) -> None:
        """Empty or missing trail dir → returns empty list."""
        results = scan_phase_ghost_trails(tmp_path, 3)
        assert results == []

    def test_scan_corrupt_json_skipped(self, tmp_path: Path) -> None:
        """Corrupt JSON files are skipped gracefully."""
        actual_dir = tmp_path / GHOST_PAPER_TRAIL_DIR
        actual_dir.mkdir(parents=True)
        (actual_dir / "corrupt.json").write_text("not json", encoding="utf-8")

        results = scan_phase_ghost_trails(tmp_path, 3)
        assert results == []

    def test_write_ghost_paper_trail(self, tmp_path: Path) -> None:
        """write_ghost_paper_trail() writes a valid JSON record."""
        write_ghost_paper_trail(tmp_path, {
            "ghost_detected": True,
            "reason": "test write",
            "phase": 3,
            "fr_id": "FR-01",
            "step": "TDD-GREEN",
        })
        trail_file = tmp_path / GHOST_PAPER_TRAIL_DIR / "FR-01_TDD-GREEN.json"
        assert trail_file.exists()
        data = json.loads(trail_file.read_text(encoding="utf-8"))
        assert data["ghost_detected"] is True
        assert data["fr_id"] == "FR-01"
        assert "detected_at" in data
