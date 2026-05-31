"""Tests for constitution/execution_logger.py — execution log collection."""

import json
from constitution.execution_logger import (
    ExecutionLogger,
    ExecutionLogEntry,
)


class TestExecutionLogEntry:
    def test_fields(self):
        entry = ExecutionLogEntry(
            timestamp="2025-01-01T00:00:00Z", phase=3, role="developer",
            task="FR-01", session_id="s1", session_key="k1",
            status="success", confidence=8, citations=["FR-01"],
            summary="done", duration_seconds=120.5,
        )
        assert entry.phase == 3
        assert entry.role == "developer"
        assert entry.confidence == 8
        assert entry.duration_seconds == 120.5

    def test_optional_defaults(self):
        entry = ExecutionLogEntry(
            timestamp="t", phase=1, role="r", task="t",
            session_id="s", session_key="k", status="s",
            confidence=5, citations=[], summary="s",
            duration_seconds=0,
        )
        assert entry.error is None
        assert entry.artifacts_read == []
        assert entry.artifacts_produced == []


class TestInferPhaseFromTask:
    def test_explicit_phase(self):
        assert ExecutionLogger._infer_phase_from_task("phase 3 implementation") == 3
        assert ExecutionLogger._infer_phase_from_task("P5 testing") == 5
        assert ExecutionLogger._infer_phase_from_task("p8 config") == 8

    def test_no_phase_match(self):
        assert ExecutionLogger._infer_phase_from_task("implementation") == 1
        assert ExecutionLogger._infer_phase_from_task("") == 1


class TestCollectFromSessionsSpawnLog:
    def test_no_log_file(self, tmp_path):
        logger = ExecutionLogger(str(tmp_path))
        result = logger.collect_from_sessions_spawn_log()
        assert result == []

    def test_empty_log_file(self, tmp_path):
        log_dir = tmp_path / ".methodology"
        log_dir.mkdir()
        (log_dir / "sessions_spawn.log").write_text("")
        logger = ExecutionLogger(str(tmp_path))
        result = logger.collect_from_sessions_spawn_log()
        assert result == []

    def test_valid_entries(self, tmp_path):
        log_dir = tmp_path / ".methodology"
        log_dir.mkdir()
        (log_dir / "sessions_spawn.log").write_text(
            json.dumps({
                "timestamp": "2025-01-01T00:00:00Z", "role": "developer",
                "task": "FR-01", "session_id": "s1", "status": "success",
                "confidence": 8, "citations": ["FR-01"], "summary": "done",
                "duration_seconds": 120,
            }) + "\n"
        )
        logger = ExecutionLogger(str(tmp_path))
        result = logger.collect_from_sessions_spawn_log()
        assert len(result) == 1
        assert result[0]["role"] == "developer"
        assert result[0]["task"] == "FR-01"

    def test_skip_comments_and_blank(self, tmp_path):
        log_dir = tmp_path / ".methodology"
        log_dir.mkdir()
        (log_dir / "sessions_spawn.log").write_text(
            "# header comment\n\n"
            + json.dumps({"timestamp": "t", "role": "r", "task": "t",
                           "session_id": "s", "status": "ok"}) + "\n"
            + "\n"
        )
        logger = ExecutionLogger(str(tmp_path))
        result = logger.collect_from_sessions_spawn_log()
        assert len(result) == 1

    def test_invalid_json_line(self, tmp_path):
        log_dir = tmp_path / ".methodology"
        log_dir.mkdir()
        (log_dir / "sessions_spawn.log").write_text(
            json.dumps({"timestamp": "t", "role": "r", "task": "t",
                         "session_id": "s1"}) + "\n"
            + "not valid json\n"
            + json.dumps({"timestamp": "t2", "role": "r2", "task": "t2",
                           "session_id": "s2"}) + "\n"
        )
        logger = ExecutionLogger(str(tmp_path))
        result = logger.collect_from_sessions_spawn_log()
        assert len(result) == 2


class TestGetPhaseContext:
    def test_phase_context_structure(self, tmp_path):
        logger = ExecutionLogger(str(tmp_path))
        ctx = logger.get_phase_context(1)
        assert ctx["phase"] == 1
        assert "max_allowed_phase" in ctx
        assert "parent_session_id" in ctx
        assert "review_iterations" in ctx
        assert "artifact_contents" in ctx

    def test_phase_context_has_artifact_contents(self, tmp_path):
        logger = ExecutionLogger(str(tmp_path))
        ctx = logger.get_phase_context(3)
        assert isinstance(ctx["artifact_contents"], dict)


class TestLoadArtifactsForPhase:
    def test_phase1_loads_srs(self, tmp_path):
        (tmp_path / "01-requirements").mkdir()
        (tmp_path / "01-requirements" / "SRS.md").write_text("# SRS content")
        logger = ExecutionLogger(str(tmp_path))
        artifacts = logger._load_artifacts_for_phase(1)
        # Should find 01-requirements/SRS.md
        assert any("SRS.md" in k for k in artifacts)

    def test_phase2_loads_sad(self, tmp_path):
        (tmp_path / "02-architecture").mkdir()
        (tmp_path / "02-architecture" / "SAD.md").write_text("# SAD content")
        logger = ExecutionLogger(str(tmp_path))
        artifacts = logger._load_artifacts_for_phase(2)
        assert any("SAD.md" in k for k in artifacts)

    def test_phase3_loads_all(self, tmp_path):
        (tmp_path / "01-requirements").mkdir()
        (tmp_path / "01-requirements" / "SRS.md").write_text("# SRS")
        logger = ExecutionLogger(str(tmp_path))
        artifacts = logger._load_artifacts_for_phase(3)
        assert len(artifacts) >= 1  # at least SRS found

    def test_missing_artifacts(self, tmp_path):
        logger = ExecutionLogger(str(tmp_path))
        artifacts = logger._load_artifacts_for_phase(1)
        assert artifacts == {}
