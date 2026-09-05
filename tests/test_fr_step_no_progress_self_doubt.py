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


def test_detects_a_realistic_passing_score_not_just_a_perfect_100(tmp_path):
    """Code-review follow-up (2026-08-23): the real Gate 1 pass bar is 80
    (cli/fr_prompts/gate.py's prompt text + harness_bridge.py's
    `_gt = ctx.config.get("score_gate", 80)`), not the 100.0 this function
    used to require. Gate 1's own dimension thresholds (100/100/80/100 at
    weight 0.25 each) mean any FR that passes every dimension scores AT
    LEAST 95 but need not score a perfect 100 — test_coverage alone can be
    anywhere 80-100. A score of 95 is a realistic, common passing FR."""
    from cli.fr_cmds import _detect_evaluator_passed_but_commit_uncommitted

    (tmp_path / ".sessi-work").mkdir()
    (tmp_path / ".sessi-work" / "gate1_result.json").write_text(json.dumps({
        "fr_id": "FR-01", "quality_complete": True, "overall_score": 95.0,
    }), encoding="utf-8")
    (tmp_path / ".methodology").mkdir()
    (tmp_path / ".methodology" / "quality_manifest.json").write_text(json.dumps({
        "gate_results": {"gate1": {"FR-01": {
            "quality_complete": False, "score": 95.0,
        }}},
    }), encoding="utf-8")

    diag = _detect_evaluator_passed_but_commit_uncommitted(tmp_path, "FR-01")
    assert diag is not None, (
        "a legitimately-passing FR scoring 95 (a realistic, common Gate 1 "
        "score — not a perfect 100) must still trigger the FR-99 recovery "
        "diagnostic; requiring >=100 silently missed most real recovery cases"
    )
    assert diag["score"] == 95.0


# ── Round 28: progress-signal helper regression suite ───────────────────
# The fix-round loop's no-progress detector fired RC=2 on a GREEN-pass idle
# round when the agent's tool output happened to be identical to the
# previous round (FR-01 actual). _progress_signal adds a file-diff hash
# alongside the tool snapshot so a round whose agent actually edited files
# is correctly classified as progress even when ruff+pytest output is
# unchanged. These tests pin all four signal-comparison cases so a future
# regression cannot roll back to "tool output only" without flipping them.


def test_progress_signal_empty_inputs_return_zero_delta():
    """Empty tool_snapshot + empty diff_sig -> delta=0, prev preserved.

    Avoids the false-positive no-progress signal that the trivial
    ``f"{''}|{''}"`` -> ``"|"`` would otherwise induce on real empty-signal
    rounds.
    """
    delta, new_prev = fr_cmds._progress_signal("", "", "")
    assert delta == 0
    assert new_prev == "", "empty inputs must preserve previous combined sig"


def test_progress_signal_identical_combined_returns_one():
    delta, new_prev = fr_cmds._progress_signal(
        "abc|ruff pass", "ruff pass", "abc"
    )
    assert delta == 1, "identical combined sig must register as no-progress"
    assert new_prev == "abc|ruff pass"


def test_progress_signal_different_combined_returns_zero():
    """Either side differing -> progress; combined sig updates for next round."""
    delta, new_prev = fr_cmds._progress_signal(
        "prev-snap", "different-tool-output", "abc"
    )
    assert delta == 0
    assert new_prev == "abc|different-tool-output"


def test_progress_signal_file_changed_same_tool_returns_zero():
    """FR-01 actual scenario: tool output identical BUT file diff differs.

    This is the root-cause fix: a round where the agent edited files but
    ruff+pytest output stayed identical (a GREEN-pass idle round) must be
    classified as progress, not no-progress.
    """
    delta, new_prev = fr_cmds._progress_signal(
        "abc|ruff pass + pytest 17 passed",  # previous combined
        "ruff pass + pytest 17 passed",       # tool sig: identical
        "def",                                 # diff_sig: changed
    )
    assert delta == 0, (
        "agent edited files but tool output stayed identical -> must be "
        "progress, not no-progress (the FR-01 root-cause fix)"
    )
    assert new_prev == "def|ruff pass + pytest 17 passed"


def test_capture_diff_sig_returns_hash_in_git_repo(tmp_path):
    """In a git repo with dirty working tree -> non-empty hash."""
    import subprocess

    subprocess.run(["git", "init", "-q"], cwd=str(tmp_path), check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.email", "t@t"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.name", "t"],
        check=True,
    )
    (tmp_path / "foo.py").write_text("x = 1\n", encoding="utf-8")
    sig = fr_cmds._capture_diff_sig(tmp_path)
    assert isinstance(sig, str) and len(sig) > 0, (
        f"expected non-empty diff sig in a dirty git repo, got {sig!r}"
    )


def test_capture_diff_sig_returns_empty_in_non_git_dir(tmp_path):
    """Outside a git repo -> "" (fail-soft, no signal)."""
    sig = fr_cmds._capture_diff_sig(tmp_path)
    assert sig == "", (
        f"non-git working dir must return empty sig (fail-soft), got {sig!r}"
    )
