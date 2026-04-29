# tests/test_decision_log.py
# Phase B deliverable B15 — M4: framework self-tests for DecisionLogWriter.
# Updated for DecisionContext refactor (quality-improvement-round-3).
import pytest
from pathlib import Path
from harness.decision_log import DecisionLogWriter, DecisionLogEntry, DecisionContext


@pytest.fixture
def log_dir(tmp_path):
    return tmp_path / "decision_logs"


@pytest.fixture
def writer(log_dir):
    return DecisionLogWriter(log_root=str(log_dir))


def _entry(agent_id="GATE", phase=3, decision="GATE_PASS", reasoning="ok",
           fr_id=None, gate_score=None, uaf_score=None):
    scores = {}
    if gate_score is not None:
        scores["gate_score"] = gate_score
    if uaf_score is not None:
        scores["uaf_score"] = uaf_score
    return DecisionLogEntry(
        ctx=DecisionContext(agent_id=agent_id, phase=phase, fr_id=fr_id),
        decision=decision,
        reasoning=reasoning,
        scores=scores,
    )


class TestDecisionLogEntry:
    def test_defaults_set(self):
        entry = _entry(agent_id="GATE", phase=3, decision="GATE_PASS", reasoning="score=87")
        assert len(entry.ctx.trace_id) == 8
        assert entry.ctx.fr_id is None
        assert entry.scores.get("gate_score") is None
        assert entry.scores.get("uaf_score", 0.0) == 0.0
        assert "T" in entry.ctx.timestamp   # ISO 8601

    def test_custom_fields(self):
        entry = _entry(
            agent_id="Developer", phase=3, decision="APPROVE", reasoning="lgtm",
            fr_id="FR-001", gate_score=88.5, uaf_score=0.7
        )
        assert entry.ctx.fr_id == "FR-001"
        assert entry.scores["gate_score"] == 88.5

    def test_agent_id_phase_properties(self):
        """agent_id and phase are accessible as properties via ctx."""
        entry = _entry(agent_id="Reviewer", phase=4)
        assert entry.agent_id == "Reviewer"
        assert entry.phase == 4


class TestDecisionLogWriter:
    def test_write_creates_file(self, writer, log_dir):
        path = writer.write(_entry())
        assert path.exists()
        assert path.suffix == ".yaml"

    def test_filename_pattern(self, writer):
        path = writer.write(_entry(agent_id="Reviewer", phase=4, decision="APPROVE"))
        assert "Reviewer_4_" in path.name
        assert path.name.endswith(".yaml")

    def test_sequence_increments(self, writer):
        entry = _entry(agent_id="GATE", phase=2, decision="GATE_BLOCK", reasoning="low")
        p1 = writer.write(entry)
        p2 = writer.write(entry)
        assert p1 != p2
        assert "001" in p1.name
        assert "002" in p2.name

    def test_content_readable(self, writer):
        path = writer.write(_entry(gate_score=90.0, fr_id="FR-001"))
        content = path.read_text()
        assert "GATE_PASS" in content
        assert "FR-001" in content

    def test_read_phase_returns_entries(self, writer):
        for _ in range(3):
            writer.write(_entry(phase=3))
        entries = writer.read_phase(phase=3)
        assert len(entries) == 3

    def test_read_phase_filters_correctly(self, writer):
        writer.write(_entry(phase=3, decision="GATE_PASS", reasoning="p3"))
        writer.write(_entry(phase=4, decision="GATE_BLOCK", reasoning="p4"))
        p3_entries = writer.read_phase(phase=3)
        assert len(p3_entries) == 1
        assert p3_entries[0]["ctx"]["phase"] == 3
