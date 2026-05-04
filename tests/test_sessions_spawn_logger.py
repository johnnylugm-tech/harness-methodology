"""Tests for core/sessions_spawn_logger.py."""

import json
import pytest
from pathlib import Path

from core.sessions_spawn_logger import SessionsSpawnLogger, log_spawn_event


@pytest.fixture
def logger(tmp_path):
    return SessionsSpawnLogger(tmp_path)


class TestSessionsSpawnLoggerInit:
    def test_creates_log_directory(self, tmp_path):
        SessionsSpawnLogger(tmp_path)
        assert (tmp_path / ".methodology").exists()

    def test_log_path_set_correctly(self, logger, tmp_path):
        assert logger.log_path == tmp_path / ".methodology" / "sessions_spawn.log"


class TestLogSpawn:
    def test_log_spawn_returns_entry(self, logger):
        entry = logger.log_spawn("developer", "Implement FR-01", "sess-1")
        assert entry["role"] == "developer"
        assert entry["task"] == "Implement FR-01"
        assert entry["session_id"] == "sess-1"
        assert entry["status"] == "SPAWNED"

    def test_log_spawn_writes_to_file(self, logger):
        logger.log_spawn("developer", "task", "sess-1")
        content = logger.log_path.read_text().strip()
        assert len(content) > 0
        parsed = json.loads(content)
        assert parsed["role"] == "developer"

    def test_log_spawn_with_confidence(self, logger):
        entry = logger.log_spawn("qa", "test", "sess-2", confidence=8)
        assert entry["confidence"] == 8

    def test_log_spawn_with_extra_kwargs(self, logger):
        entry = logger.log_spawn("architect", "design", "sess-3", phase=2, fr_id="FR-01")
        assert entry["phase"] == 2
        assert entry["fr_id"] == "FR-01"


class TestLogUpdate:
    def test_log_update_finds_and_updates_entry(self, logger):
        logger.log_spawn("developer", "task", "sess-1")
        updated = logger.log_update("sess-1", status="COMPLETED", result="pass")
        assert updated is not None
        assert updated["status"] == "COMPLETED"
        assert updated["result"] == "pass"
        assert "_updated_at" in updated

    def test_log_update_returns_none_for_unknown_session(self, logger):
        logger.log_spawn("developer", "task", "sess-1")
        assert logger.log_update("nonexistent", status="COMPLETED") is None


class TestValidate:
    def test_validate_empty_log_returns_valid(self, logger):
        result = logger.validate()
        assert result["valid"] is True
        assert result["count"] == 0

    def test_validate_with_valid_entries(self, logger):
        logger.log_spawn("developer", "task", "sess-1")
        logger.log_spawn("reviewer", "review", "sess-2")
        result = logger.validate()
        assert result["valid"] is True
        assert result["count"] == 2

    def test_validate_detects_missing_fields(self, logger):
        logger.log_path.write_text('{"role": "dev"}\n')  # missing session_id
        result = logger.validate()
        assert result["valid"] is False
        assert len(result["errors"]) > 0

    def test_validate_detects_json_decode_error(self, logger):
        logger.log_path.write_text("NOT VALID JSON\n")
        result = logger.validate()
        assert result["valid"] is False
        assert len(result["errors"]) == 1

    def test_validate_empty_content_returns_valid(self, logger):
        logger.log_path.write_text("")
        result = logger.validate()
        assert result["valid"] is True
        assert result["count"] == 0


class TestGetSummary:
    def test_get_summary_returns_structure(self, logger):
        logger.log_spawn("developer", "FR-01 task", "sess-1")
        logger.log_spawn("reviewer", "FR-01 review", "sess-2", status="COMPLETED")
        summary = logger.get_summary()
        assert "total_entries" in summary
        assert "role_counts" in summary
        assert "fr_tasks" in summary
        assert "status_counts" in summary
        assert summary["total_entries"] == 2
        assert summary["role_counts"]["developer"] == 1
        assert summary["role_counts"]["reviewer"] == 1

    def test_get_summary_handles_empty_log(self, logger):
        summary = logger.get_summary()
        assert summary["total_entries"] == 0


class TestReadEntries:
    def test_read_entries_handles_leading_comma(self, logger):
        logger.log_path.write_text(',\n{"role":"dev","session_id":"s1","task":"t"}\n')
        entries = logger._read_entries()
        assert len(entries) == 1

    def test_read_entries_skips_empty_lines(self, logger):
        logger.log_path.write_text('\n\n{"role":"dev","session_id":"s1","task":"t"}\n\n')
        entries = logger._read_entries()
        assert len(entries) == 1


class TestLogSpawnEvent:
    def test_convenience_function(self, tmp_path):
        entry = log_spawn_event(tmp_path, "architect", "design", "sess-x")
        assert entry["role"] == "architect"
        assert entry["session_id"] == "sess-x"
