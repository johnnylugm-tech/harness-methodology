"""sessions_spawn.log is observation, not evidence (Round 21 站3).

PhaseTruthVerifier scored this file at weight 0.20 (0.10 for phases 3-4). The
file is written by the agent whose work the score judges, it is gitignored so
no review or CI ever sees it, and appending a line to it costs one Bash call.

SKILL.md:317 had already recorded the conclusion when HR-10 was withdrawn — "log
is agent-writable, not tamper-evident" — but the code kept scoring it and SAD.md
still called it a MUST, so all three said different things and nobody noticed
the check was still live.

taskq's Phase 6 then wrote six entries by hand, with `role: architect` and
`phase: 6` — exactly what the (then-scored) A/B branch searched for — 45 seconds
before the first `release(P6): Gate4 PASS` commit.

Two properties are pinned here:
  1. forged entries change no score (the file left the verdict)
  2. forged entries are still reported (doctor names them, as a WARN)

The second must never become the first again. A forger who can write the entries
can write whatever the authenticity heuristic reads; catching this after the
fact is all it is for.
"""

import json

import pytest

from core.doctor import run_doctor
from core.quality_gate.phase_truth_verifier import PhaseTruthVerifier

_FORGED = [
    {
        "timestamp": "2026-07-27T12:25:00.000000",
        "role": "architect",
        "task": "Gate 4 Round 3 — Tier-3 architect review of FR-01: confirmed.",
        "session_id": "round3-da-architect-fr01",
        "status": "complete",
        "phase": 6,
        "fr_id": "FR-01",
        "duration_seconds": 0,
        "dispatch_attempt": 1,
    },
    {
        "timestamp": "2026-07-27T12:25:01.000000",
        "role": "architect",
        "task": "Gate 4 Round 3 — Tier-3 architect review of FR-02: verified.",
        "session_id": "round3-da-architect-fr02",
        "status": "complete",
        "phase": 6,
        "fr_id": "FR-02",
        "duration_seconds": 0,
        "dispatch_attempt": 1,
    },
]

_REAL = {
    "timestamp": "2026-07-27T14:28:27.572744",
    "role": "developer",
    "task": "You are a Gate 1 evaluator. Your task: run Gate 1 evaluation for FR-01.",
    "session_id": "b9bd7582-509e-488d-aa30-0fac939d8905",
    "status": "complete",
    "phase": 7,
    "fr_id": "FR-01",
    "duration_seconds": 31.07,
    "dispatch_attempt": 1,
    "total_cost_usd": 0.2543675,
    "num_turns": 13,
    "duration_api_ms": 25394,
    "usage": {"input_tokens": 28053, "output_tokens": 1779},
}


def _project(tmp_path, entries):
    method = tmp_path / ".methodology"
    method.mkdir(parents=True, exist_ok=True)
    (method / "state.json").write_text(
        json.dumps({"state": "ACTIVE", "current_phase": 6}), encoding="utf-8"
    )
    if entries is not None:
        (method / "sessions_spawn.log").write_text(
            "\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8"
        )
    return tmp_path


class TestSpawnLogIsNotScored:
    @pytest.mark.parametrize("phase", [1, 2, 3, 6, 8])
    def test_no_phase_scores_the_spawn_log(self, tmp_path, phase):
        """The check must not appear in any phase's weighted check list."""
        v = PhaseTruthVerifier(str(_project(tmp_path, _FORGED)), phase)
        result = v.verify()
        names = [c["name"] for c in result["checks"]]
        assert "Session Log Validation" not in names, (
            f"phase {phase} still scores the spawn log: {names}"
        )

    def test_a_fully_forged_log_scores_the_same_as_no_log_at_all(self, tmp_path):
        """The decisive property: forging entries buys nothing.

        Same project, same everything, only the log differs — identical score.
        """
        forged = PhaseTruthVerifier(str(_project(tmp_path / "a", _FORGED)), 6).verify()
        absent = PhaseTruthVerifier(str(_project(tmp_path / "b", None)), 6).verify()
        assert forged["total_score"] == absent["total_score"]

    def test_weights_still_sum_to_one(self, tmp_path):
        """Redistribution must not leave the scale silently renormalised."""
        for phase in (1, 3, 6):
            v = PhaseTruthVerifier(str(_project(tmp_path / f"p{phase}", None)), phase)
            result = v.verify()
            total = sum(c["weight"] for c in result["checks"])
            assert abs(total - 1.0) < 1e-9, f"phase {phase} weights sum to {total}"


class TestDoctorStillReportsForgery:
    def test_forged_entries_are_named(self, tmp_path):
        findings = run_doctor(_project(tmp_path, _FORGED))
        spawn = [f for f in findings if f.check == "spawn-log"]
        assert spawn, f"no spawn-log finding; got {[f.check for f in findings]}"
        assert spawn[0].severity == "WARN"
        assert "round3-da-architect-fr01" in spawn[0].message

    def test_real_dispatch_entries_are_not_flagged(self, tmp_path):
        findings = run_doctor(_project(tmp_path, [_REAL]))
        assert not [f for f in findings if f.check == "spawn-log"]

    def test_a_pre_envelope_entry_with_a_uuid_is_not_flagged(self, tmp_path):
        """Lines written before Round 14 站0 have no envelope — and are genuine."""
        legacy = {k: v for k, v in _REAL.items()
                  if k not in ("total_cost_usd", "num_turns", "duration_api_ms", "usage")}
        findings = run_doctor(_project(tmp_path, [legacy]))
        assert not [f for f in findings if f.check == "spawn-log"]

    def test_finding_carries_remediation(self, tmp_path):
        findings = run_doctor(_project(tmp_path, _FORGED))
        spawn = [f for f in findings if f.check == "spawn-log"][0]
        assert "Fix:" in spawn.message
