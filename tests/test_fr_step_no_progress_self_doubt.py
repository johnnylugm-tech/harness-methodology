"""Round 17 站2 (finding B) — no-progress fix-round abort: ledger + self-doubt.

The run-fr-step fix-round loop's `no_progress_count >= 2` guard used to just
print BLOCKED and return 2 — the inescapable-loop event was invisible to
run-report, and a same-error loop that is actually a harness gate bug (the
#20 spec-cap class) looked identical to a genuine code defect. This pins the
two honest additions at that already-terminal point (the plan's original
S4-vs-verdict contradiction signal is unavailable — S4 returns only violation
messages and _capture_tool_snapshot runs pytest without --cov).
"""

from __future__ import annotations

import json

import harness_cli as _hc_entry  # noqa: F401  entry-first before cli imports
from cli import fr_cmds  # noqa: E402


def test_no_progress_abort_records_degradation(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(fr_cmds, "record_degradation",
                        lambda *a, **k: calls.append((a, k)))
    rc = fr_cmds._abort_no_progress_with_self_doubt(
        "FR-01", "GATE1", 3, tmp_path, "LOW_COVERAGE", "AssertionError: x")
    assert rc == 2, "must preserve the loop's existing BLOCKED return code"
    assert len(calls) == 1, "the exhausted fix-round must reach the degradation ledger"
    # record_degradation(project, component, what, why=...) — component is arg[1]
    assert calls[0][0][1] == "fr-step-no-progress", (
        "run-report/R16 classification keys off this component label")


def test_no_progress_abort_emits_harness_bug_self_doubt(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(fr_cmds, "record_degradation", lambda *a, **k: None)
    fr_cmds._abort_no_progress_with_self_doubt(
        "FR-01", "COVERAGE-FIX", 3, tmp_path, "LOW_COVERAGE", "sig")
    out = capsys.readouterr().out
    assert "[HARNESS-BUG]" in out, (
        "the self-doubt channel must name [HARNESS-BUG] as the alternative to "
        "code-fixing a deterministic same-error loop forever (#20 class)")
    assert "SELF-CHECK" in out
    # the operator-facing BLOCKED line the loop always printed must remain,
    # in stdout, so workflow JS still reads it unchanged.
    assert "2 consecutive no-progress rounds" in out


def test_recovery_banner_prints_a_real_phase_number(monkeypatch, capsys, tmp_path):
    """Code-review follow-up (2026-08-23): the printed recovery command must
    use the actual --phase int, not the step-name string. `step='GATE1'`
    used to leak into `--phase GATE1` (argparse's `--phase` is `type=int`,
    so the copy-pasted command failed with 'invalid int value'). The
    function has no way to fabricate a phase on its own — it must receive
    the real one from its caller."""
    monkeypatch.setattr(fr_cmds, "record_degradation", lambda *a, **k: None)
    (tmp_path / ".sessi-work").mkdir()
    (tmp_path / ".sessi-work" / "gate1_result.json").write_text(json.dumps({
        "fr_id": "FR-01", "quality_complete": True, "overall_score": 100.0,
    }), encoding="utf-8")
    (tmp_path / ".methodology").mkdir()
    (tmp_path / ".methodology" / "quality_manifest.json").write_text(json.dumps({
        "gate_results": {"gate1": {"FR-01": {
            "quality_complete": False, "score": 100.0,
        }}},
    }), encoding="utf-8")

    fr_cmds._abort_no_progress_with_self_doubt(
        "FR-01", "GATE1", 3, tmp_path, "LOW_COVERAGE", "sig")
    out = capsys.readouterr().out
    assert "finalize-gate --gate 1 --phase 3 --fr-id FR-01" in out, (
        f"recovery command must carry the real phase int (3), not the step "
        f"name ('GATE1' is not a valid --phase int and argparse rejects "
        f"it): {out}"
    )
    assert "--phase GATE1" not in out
