"""Unit tests for harness/effort_tracker.py — EffortTracker SQLite backend."""
import pytest
import sqlite3
from pathlib import Path
from unittest.mock import patch

from harness.effort_tracker import EffortTracker, EffortRecord


# ── Schema / init ────────────────────────────────────────────────────────

def test_db_created_on_init(tmp_path: Path):
    db = tmp_path / "test.db"
    EffortTracker(str(db))
    assert db.exists()


def test_schema_creates_effort_table(tmp_path: Path):
    db = tmp_path / "test.db"
    EffortTracker(str(db))
    with sqlite3.connect(db) as c:
        cols = {row[1] for row in c.execute("PRAGMA table_info('effort')")}
    expected = {"id", "phase", "gate_num", "agent_id", "operation",
                "duration_s", "token_in", "token_out", "fr_id", "created_at"}
    assert expected.issubset(cols)


def test_mkdir_creates_methodology_dir(tmp_path: Path):
    db = tmp_path / ".methodology" / "metrics.db"
    EffortTracker(str(db))
    assert db.parent.exists()


# ── record ────────────────────────────────────────────────────────────────

def test_record_persists_entry(tmp_path: Path):
    db = tmp_path / "test.db"
    tracker = EffortTracker(str(db))
    rec = EffortRecord(phase=3, agent_id="dev-1", operation="gate_run",
                       duration_s=12.5, gate_num=1, token_in=100, token_out=50,
                       fr_id="FR-01")
    tracker.record(rec)

    with sqlite3.connect(db) as c:
        row = c.execute("SELECT phase, operation, duration_s, token_in, token_out, fr_id "
                         "FROM effort").fetchone()
    assert row == (3, "gate_run", 12.5, 100, 50, "FR-01")


def test_record_optional_fields_null(tmp_path: Path):
    db = tmp_path / "test.db"
    tracker = EffortTracker(str(db))
    rec = EffortRecord(phase=4, agent_id="qa-1", operation="review",
                       duration_s=3.0)
    tracker.record(rec)

    with sqlite3.connect(db) as c:
        row = c.execute("SELECT phase, gate_num, fr_id FROM effort").fetchone()
    assert row == (4, None, None)


def test_record_multiple_rows(tmp_path: Path):
    db = tmp_path / "test.db"
    tracker = EffortTracker(str(db))
    tracker.record(EffortRecord(phase=3, agent_id="a", operation="fix_round",
                                duration_s=1.0))
    tracker.record(EffortRecord(phase=3, agent_id="b", operation="fix_round",
                                duration_s=2.0))

    with sqlite3.connect(db) as c:
        count = c.execute("SELECT COUNT(*) FROM effort").fetchone()[0]
    assert count == 2


# ── summary (global) ──────────────────────────────────────────────────────

def test_summary_empty_db(tmp_path: Path):
    tracker = EffortTracker(str(tmp_path / "test.db"))
    s = tracker.summary()
    assert s == {"total_operations": 0, "total_duration_s": 0.0, "total_tokens": 0}


def test_summary_aggregates_all(tmp_path: Path):
    tracker = EffortTracker(str(tmp_path / "test.db"))
    tracker.record(EffortRecord(phase=3, agent_id="a", operation="gate_run",
                                duration_s=5.0, token_in=10, token_out=20))
    tracker.record(EffortRecord(phase=4, agent_id="b", operation="review",
                                duration_s=3.0, token_in=0, token_out=50))
    s = tracker.summary()
    assert s["total_operations"] == 2
    assert s["total_duration_s"] == 8.0
    assert s["total_tokens"] == 80


# ── summary (phase-filtered) ──────────────────────────────────────────────

def test_summary_with_phase_filter(tmp_path: Path):
    tracker = EffortTracker(str(tmp_path / "test.db"))
    tracker.record(EffortRecord(phase=3, agent_id="a", operation="gate_run",
                                duration_s=5.0, token_in=10, token_out=20))
    tracker.record(EffortRecord(phase=4, agent_id="b", operation="review",
                                duration_s=3.0, token_in=0, token_out=50))
    s = tracker.summary(phase=3)
    assert "gate_run" in s
    assert s["gate_run"]["duration_s"] == 5.0
    assert s["gate_run"]["total_tokens"] == 30


def test_summary_phase_no_data(tmp_path: Path):
    tracker = EffortTracker(str(tmp_path / "test.db"))
    s = tracker.summary(phase=99)
    assert s == {}


# ── query_phase_summary ───────────────────────────────────────────────────

def test_query_phase_summary_groups_by_operation(tmp_path: Path):
    tracker = EffortTracker(str(tmp_path / "test.db"))
    tracker.record(EffortRecord(phase=3, agent_id="a", operation="gate_run",
                                duration_s=2.0, token_in=10, token_out=10))
    tracker.record(EffortRecord(phase=3, agent_id="b", operation="gate_run",
                                duration_s=4.0, token_in=20, token_out=10))
    tracker.record(EffortRecord(phase=3, agent_id="c", operation="review",
                                duration_s=1.0, token_in=5, token_out=5))
    s = tracker.query_phase_summary(3)
    assert s["gate_run"]["duration_s"] == 6.0
    assert s["gate_run"]["total_tokens"] == 50
    assert s["review"]["duration_s"] == 1.0


# ── query_gate_summary ────────────────────────────────────────────────────

def test_query_gate_summary_empty(tmp_path: Path):
    tracker = EffortTracker(str(tmp_path / "test.db"))
    s = tracker.query_gate_summary(1)
    assert s == {"runs": 0, "total_duration_s": 0.0, "total_tokens": 0}


def test_query_gate_summary_aggregates(tmp_path: Path):
    tracker = EffortTracker(str(tmp_path / "test.db"))
    tracker.record(EffortRecord(phase=3, agent_id="a", operation="gate_run",
                                duration_s=3.0, gate_num=1, token_in=40, token_out=10))
    tracker.record(EffortRecord(phase=3, agent_id="b", operation="review",
                                duration_s=2.0, gate_num=1, token_in=20, token_out=30))
    s = tracker.query_gate_summary(1)
    assert s["runs"] == 2
    assert s["total_duration_s"] == 5.0
    assert s["total_tokens"] == 100


def test_query_gate_summary_filters_gate_num(tmp_path: Path):
    tracker = EffortTracker(str(tmp_path / "test.db"))
    tracker.record(EffortRecord(phase=3, agent_id="a", operation="gate_run",
                                duration_s=1.0, gate_num=1))
    tracker.record(EffortRecord(phase=4, agent_id="b", operation="gate_run",
                                duration_s=2.0, gate_num=2))
    assert tracker.query_gate_summary(1)["runs"] == 1
    assert tracker.query_gate_summary(2)["runs"] == 1


# ── Error handling ────────────────────────────────────────────────────────

def test_record_raises_on_db_write_failure(tmp_path: Path):
    """record() propagates sqlite3.Error when DB is locked/corrupt."""
    tracker = EffortTracker(str(tmp_path / "test.db"))
    rec = EffortRecord(phase=3, agent_id="a", operation="gate_run",
                       duration_s=1.0)
    with patch("sqlite3.connect") as mock_connect:
        mock_connect.side_effect = sqlite3.OperationalError("database is locked")
        with pytest.raises(sqlite3.OperationalError):
            tracker.record(rec)
