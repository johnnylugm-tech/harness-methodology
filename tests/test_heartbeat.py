"""Round 24 站5a — liveness trail, and an explicit pin on what it cannot see.

The gap this closes: in the run-all-by-workflow P1-P8 validation run, Phase 6
reached `Gate4 PASS 97.4` and then made no progress for 1h18m. Nothing
noticed; it surfaced only because 老闆 asked, and the liveness judgement
available at that moment was "the journal has had no new entry for 3 minutes,
treat it as dead" — a number invented on the spot.

The gap it does NOT close is pinned by
`test_heartbeat_cannot_see_an_agent_that_never_calls_the_harness`. A partial
solution that reads as complete is worse than none: it would let the next
reader conclude "liveness is covered" when an agent stuck inside a sub-agent
dispatch is still entirely invisible.
"""

from __future__ import annotations

import argparse
import json

import pytest

from core.heartbeat import (
    HEARTBEAT_RELPATH,
    STALL_THRESHOLD_MINUTES,
    minutes_since,
    read_heartbeat,
    record_heartbeat,
)

pytestmark = [pytest.mark.core]


def _method(tmp_path):
    (tmp_path / ".methodology").mkdir(parents=True, exist_ok=True)
    return tmp_path


def test_record_then_read_round_trips(tmp_path):
    _method(tmp_path)
    record_heartbeat(tmp_path, "finalize-gate")
    beat = read_heartbeat(tmp_path)
    assert beat is not None
    assert beat["command"] == "finalize-gate"
    assert beat["utc"].endswith("+00:00"), "Round 24 站3 time base"


def test_record_is_a_noop_outside_a_methodology_project(tmp_path):
    record_heartbeat(tmp_path, "doctor")
    assert not (tmp_path / HEARTBEAT_RELPATH).exists()
    assert read_heartbeat(tmp_path) is None


def test_record_never_raises_on_an_unwritable_path(tmp_path):
    """A heartbeat failure must never turn a successful command into a failed
    one — it is wired into a `finally` that must not change the exit code."""
    method = _method(tmp_path)
    (method / ".methodology" / "heartbeat.json").mkdir()  # a directory, not a file
    record_heartbeat(tmp_path, "run-gate")  # must not raise


def test_read_tolerates_corrupt_json(tmp_path):
    _method(tmp_path)
    (tmp_path / HEARTBEAT_RELPATH).write_text("{trunc", encoding="utf-8")
    assert read_heartbeat(tmp_path) is None


def test_minutes_since_computes_across_offsets(tmp_path):
    beat = {"utc": "2026-07-28T13:42:00+00:00"}
    assert minutes_since(beat, "2026-07-28T15:00:00+00:00") == pytest.approx(78.0)
    # Same instant expressed in another offset — must agree.
    assert minutes_since(beat, "2026-07-28T23:00:00+08:00") == pytest.approx(78.0)


def test_minutes_since_refuses_a_naive_timestamp():
    """Pre-migration files carry an unknown zone. Refusing to subtract is
    correct — guessing is what produced this round's own eight-hour
    misreading of the run-all-by-workflow artifacts."""
    assert minutes_since({"utc": "2026-07-28T13:42:00"}, "2026-07-28T15:00:00+00:00") is None
    assert minutes_since({"utc": "2026-07-28T13:42:00+00:00"}, "2026-07-28T15:00:00") is None
    assert minutes_since({}, "2026-07-28T15:00:00+00:00") is None


# ── doctor wiring ───────────────────────────────────────────────────────

def _doctor_heartbeat_findings(project):
    from core.doctor import _check_heartbeat

    return _check_heartbeat(project)


def test_doctor_is_silent_on_a_fresh_heartbeat(tmp_path):
    _method(tmp_path)
    record_heartbeat(tmp_path, "advance-phase")
    assert _doctor_heartbeat_findings(tmp_path) == []


def test_doctor_is_silent_when_no_heartbeat_exists(tmp_path):
    """A project that has never run one, or predates this station, is not a
    finding — absence is not evidence of a stall."""
    _method(tmp_path)
    assert _doctor_heartbeat_findings(tmp_path) == []


def test_doctor_warns_past_the_threshold_and_names_the_last_command(tmp_path):
    _method(tmp_path)
    stale = "2020-01-01T00:00:00+00:00"
    (tmp_path / HEARTBEAT_RELPATH).write_text(
        json.dumps({"command": "run-gate", "utc": stale}), encoding="utf-8"
    )
    findings = _doctor_heartbeat_findings(tmp_path)
    assert len(findings) == 1
    f = findings[0]
    assert f.severity == "WARN", "a possible stall is not a state inconsistency"
    assert "run-gate" in f.message
    assert str(STALL_THRESHOLD_MINUTES) in f.message


def test_doctor_warning_states_what_it_cannot_see(tmp_path):
    """The message must not read as 'liveness is covered'."""
    _method(tmp_path)
    (tmp_path / HEARTBEAT_RELPATH).write_text(
        json.dumps({"command": "run-gate", "utc": "2020-01-01T00:00:00+00:00"}),
        encoding="utf-8",
    )
    msg = _doctor_heartbeat_findings(tmp_path)[0].message
    assert "HARNESS layer only" in msg
    assert "invisible" in msg


def test_heartbeat_cannot_see_an_agent_that_never_calls_the_harness(tmp_path):
    """BOUNDARY PIN — this is the documented limit, not an oversight.

    An agent can be alive and working (thinking, waiting on an LLM, running a
    sub-agent) for hours without invoking a single harness command. The
    workflow runtime exposes no heartbeat API, so nothing in this module can
    distinguish that from a dead run. If a future change makes the harness
    aware of agent-side liveness, this test is the one to revisit.
    """
    _method(tmp_path)
    record_heartbeat(tmp_path, "load-context")
    # Simulate: hours pass, the agent is busy but calls nothing.
    (tmp_path / HEARTBEAT_RELPATH).write_text(
        json.dumps({"command": "load-context", "utc": "2020-01-01T00:00:00+00:00"}),
        encoding="utf-8",
    )
    findings = _doctor_heartbeat_findings(tmp_path)
    assert len(findings) == 1, (
        "a busy agent and a dead run are indistinguishable here — that is the "
        "boundary, and the WARN text says so"
    )


# ── CLI wiring ──────────────────────────────────────────────────────────

def test_dispatch_records_a_heartbeat_on_success(tmp_path):
    import harness_cli

    _method(tmp_path)
    args = argparse.Namespace(
        command="doctor", project=str(tmp_path), func=lambda _a: 0
    )
    assert harness_cli._dispatch(args, ["doctor"]) == 0
    beat = read_heartbeat(tmp_path)
    assert beat is not None and beat["command"] == "doctor"


def test_dispatch_records_a_heartbeat_on_failure_too(tmp_path):
    """A failing command still proves the harness was running — a stalled run
    and a failing run must stay distinguishable."""
    import harness_cli

    _method(tmp_path)
    args = argparse.Namespace(
        command="run-gate", project=str(tmp_path), func=lambda _a: 1
    )
    assert harness_cli._dispatch(args, ["run-gate"]) == 1
    beat = read_heartbeat(tmp_path)
    assert beat is not None and beat["command"] == "run-gate"


def test_dispatch_records_a_heartbeat_when_the_handler_crashes(tmp_path):
    import harness_cli
    from cli.exit_codes import EX_HARNESS_BUG

    _method(tmp_path)

    def _boom(_a):
        raise RuntimeError("kaboom")

    args = argparse.Namespace(command="finalize-gate", project=str(tmp_path), func=_boom)
    assert harness_cli._dispatch(args, ["finalize-gate"]) == EX_HARNESS_BUG
    beat = read_heartbeat(tmp_path)
    assert beat is not None and beat["command"] == "finalize-gate"


def test_heartbeat_failure_does_not_change_the_exit_code(tmp_path, monkeypatch):
    import harness_cli
    import core.heartbeat as hb

    _method(tmp_path)
    monkeypatch.setattr(hb, "record_heartbeat", lambda *_a, **_k: None)
    args = argparse.Namespace(command="doctor", project=str(tmp_path), func=lambda _a: 7)
    assert harness_cli._dispatch(args, ["doctor"]) == 7
