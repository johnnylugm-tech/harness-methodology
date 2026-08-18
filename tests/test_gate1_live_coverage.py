"""Unit tests for ``_check_gate1_live_coverage`` + ``_validate_fr_coverage_immediate``.

These tests cover the LIVE pytest --cov verification path introduced to
replace the gate_timestamps.jsonl-only check. The previous implementation
trusted a timestamp file written by the agent, which could pass even if no
actual test ran. The new path runs pytest on the whole project and BLOCKS
on real coverage gaps.
"""
import json
from pathlib import Path
from unittest import mock

import pytest


# ---------------------------------------------------------------------------
# Fixtures: a minimal project tree that mirrors the harness layout
# ---------------------------------------------------------------------------


@pytest.fixture
def project_with_fr(tmp_path: Path) -> Path:
    """Create 03-development/src + tests/ + manifest with one FR (FR-07)."""
    src = tmp_path / "03-development" / "src" / "infrastructure"
    tests = tmp_path / "03-development" / "tests"
    src.mkdir(parents=True)
    tests.mkdir(parents=True)
    (src / "audio_converter.py").write_text(
        "def convert(x: str) -> str:\n"
        "    \"\"\"[FR-07] Convert audio.\"\"\"\n"
        "    return x.upper()\n",
        encoding="utf-8",
    )
    (tests / "test_fr07.py").write_text(
        "from infrastructure.audio_converter import convert\n"
        "def test_convert():\n"
        "    assert convert('a') == 'A'\n",
        encoding="utf-8",
    )
    methodology = tmp_path / ".methodology"
    methodology.mkdir()
    (methodology / "quality_manifest.json").write_text(
        json.dumps({
            "fr_ids": ["FR-07"],
            "quality_targets": {"min_coverage": 80.0},
            "gate_results": {"gate1": {"FR-07": {"score": 95.0, "quality_complete": True}}},
        }),
        encoding="utf-8",
    )
    return tmp_path


# ---------------------------------------------------------------------------
# _validate_fr_coverage_immediate
# ---------------------------------------------------------------------------


def _suite(**kwargs):
    """A SuiteResult with sensible defaults (Round 25 shared suite run)."""
    from core.quality_gate.test_suite_run import SuiteResult

    base = dict(
        passed=True, coverage=100.0, test_target="03-development/tests",
        cov_target="03-development/src", returncode=0, output="", ran=True,
    )
    base.update(kwargs)
    return SuiteResult(**base)  # type: ignore[arg-type]


def test_validate_fr_coverage_immediate_returns_the_measured_percentage(project_with_fr):
    """The measured coverage is passed through unchanged."""
    from core.quality_gate.gate1_evidence import validate_fr_coverage_immediate
    with mock.patch("core.quality_gate.test_suite_run.run_suite",
                    return_value=_suite(coverage=100.0)):
        cov = validate_fr_coverage_immediate(project_with_fr)
    assert cov == 100.0


def test_validate_fr_coverage_immediate_no_coverage_returns_zero_on_success(project_with_fr):
    """Green suite but no readable coverage report → 0.0 (coverage tool inactive)."""
    from core.quality_gate.gate1_evidence import validate_fr_coverage_immediate
    with mock.patch("core.quality_gate.test_suite_run.run_suite",
                    return_value=_suite(coverage=None, passed=True)):
        cov = validate_fr_coverage_immediate(project_with_fr)
    assert cov == 0.0


def test_validate_fr_coverage_immediate_test_failure_returns_none(project_with_fr):
    """Red suite with no coverage number → None (caller BLOCKS)."""
    from core.quality_gate.gate1_evidence import validate_fr_coverage_immediate
    with mock.patch("core.quality_gate.test_suite_run.run_suite",
                    return_value=_suite(coverage=None, passed=False, returncode=1,
                                        output="1 failed\n")):
        cov = validate_fr_coverage_immediate(project_with_fr)
    assert cov is None


def test_validate_fr_coverage_immediate_no_src_returns_none(tmp_path):
    """No 03-development/src/ → None (no point running pytest)."""
    from core.quality_gate.gate1_evidence import validate_fr_coverage_immediate
    (tmp_path / "03-development" / "tests").mkdir(parents=True)
    assert validate_fr_coverage_immediate(tmp_path) is None


def test_validate_fr_coverage_immediate_no_tests_returns_none(tmp_path):
    """No 03-development/tests/ → None."""
    from core.quality_gate.gate1_evidence import validate_fr_coverage_immediate
    (tmp_path / "03-development" / "src").mkdir(parents=True)
    assert validate_fr_coverage_immediate(tmp_path) is None


def test_validate_fr_coverage_immediate_timeout_returns_none(project_with_fr):
    """Suite timeout → None (don't block forever, don't invent a 0%)."""
    from core.quality_gate.gate1_evidence import validate_fr_coverage_immediate
    with mock.patch("core.quality_gate.test_suite_run.run_suite",
                    return_value=_suite(coverage=None, passed=False, returncode=124,
                                        reason="test suite timed out after 300s")):
        cov = validate_fr_coverage_immediate(project_with_fr)
    assert cov is None


# ---------------------------------------------------------------------------
# _check_gate1_live_coverage
# ---------------------------------------------------------------------------


def test_check_gate1_live_coverage_passes_at_threshold(project_with_fr):
    """Coverage ≥ min_coverage → return 0."""
    from cli.phase_cmds import _check_gate1_live_coverage
    with mock.patch(
        "core.quality_gate.gate1_evidence.validate_fr_coverage_immediate", return_value=100.0
    ):
        rc = _check_gate1_live_coverage(project_with_fr, completed_phase=3)
    assert rc == 0


def test_check_gate1_live_coverage_blocks_below_threshold(project_with_fr):
    """Coverage < min_coverage → BLOCKED 14."""
    from cli.phase_cmds import _check_gate1_live_coverage
    with mock.patch(
        "core.quality_gate.gate1_evidence.validate_fr_coverage_immediate", return_value=50.0
    ):
        rc = _check_gate1_live_coverage(project_with_fr, completed_phase=3)
    assert rc == 14


def test_check_gate1_live_coverage_blocks_pytest_failure(project_with_fr):
    """pytest errored (None) → BLOCKED 14."""
    from cli.phase_cmds import _check_gate1_live_coverage
    with mock.patch(
        "core.quality_gate.gate1_evidence.validate_fr_coverage_immediate", return_value=None
    ):
        rc = _check_gate1_live_coverage(project_with_fr, completed_phase=3)
    assert rc == 14


def test_check_gate1_live_coverage_delta_auto_skip(tmp_path):
    """P5 + no code changed since last Gate 1 → skip live pytest, return 0."""
    from cli.phase_cmds import _check_gate1_live_coverage
    methodology = tmp_path / ".methodology"
    methodology.mkdir()
    (methodology / "quality_manifest.json").write_text(
        json.dumps({"fr_ids": ["FR-07"]}),
        encoding="utf-8",
    )
    with mock.patch(
        "core.quality_gate.gate1_evidence.fr_code_changed_since_last_gate1", return_value=False
    ):
        with mock.patch("core.quality_gate.gate1_evidence.validate_fr_coverage_immediate") as mock_cov:
            rc = _check_gate1_live_coverage(tmp_path, completed_phase=5)
            mock_cov.assert_not_called()  # live pytest was skipped
    assert rc == 0


def test_check_gate1_live_coverage_no_manifest_returns_zero(tmp_path):
    """Project without quality_manifest → skip (non-FR project)."""
    from cli.phase_cmds import _check_gate1_live_coverage
    rc = _check_gate1_live_coverage(tmp_path, completed_phase=3)
    assert rc == 0


def test_check_gate1_live_coverage_reads_min_coverage_from_manifest(tmp_path):
    """A min_coverage=100 in manifest is respected (not hardcoded 80)."""
    from cli.phase_cmds import _check_gate1_live_coverage
    methodology = tmp_path / ".methodology"
    methodology.mkdir()
    (methodology / "quality_manifest.json").write_text(
        json.dumps({
            "fr_ids": ["FR-07"],
            "quality_targets": {"min_coverage": 100.0},
        }),
        encoding="utf-8",
    )
    # 99.5% < 100% threshold → BLOCKED even though 99.5 > 80 default
    with mock.patch(
        "core.quality_gate.gate1_evidence.validate_fr_coverage_immediate", return_value=99.5
    ):
        rc = _check_gate1_live_coverage(tmp_path, completed_phase=3)
    assert rc == 14


# ── Bug #132: per-FR coverage scope under P3 TDD ──
# Regression for the false-positive LOW_COVERAGE loop in taskq-cc's P3
# FR-01 GATE1. Whole-project coverage was 8.5% (95% on FR-01's own
# modules, 0% on the 10 phantom modules SAB declared for other FRs),
# the cycle shipped 3 fix rounds with no progress, and the run went
# out to [HARNESS-BUG]. The fix: when the caller names an FR, restrict
# coverage to that FR's fr_module_traceability modules so the gate sees
# what this FR actually owns.
#
# Round 57 站1 removed the `phase == 3` half of the condition (and with it
# the state.json this test used to write). Only a per-FR gate passes fr_id,
# Gate 1 is that gate, and Gate 1 declares `scope: single_fr` at every phase
# it runs — the phase was never the question.
def test_validate_fr_coverage_immediate_per_fr_scope_returns_fr_only(project_with_fr):
    """Named an FR, FR-01's coverage reads 95%+ — not 8.5% whole-project.

    Whole-project score is 8.5% on the evidence project because 10 SAB
    modules belong to other FRs and are empty stubs. Per-FR scope must
    skip those and report the modules FR-01 actually owns.
    """
    # Build a SAB.json with FR-01 owning the only measured module.
    (project_with_fr / ".methodology" / "SAB.json").write_text(
        json.dumps({
            "sab": {
                "fr_module_traceability": {
                    "FR-07": ["infrastructure.audio_converter"],
                },
            },
        }),
        encoding="utf-8",
    )
    from core.quality_gate.gate1_evidence import validate_fr_coverage_immediate
    with mock.patch("core.quality_gate.test_suite_run.run_suite",
                    return_value=_suite(coverage=8.5)):
        # Without fr_id: whole-project (mocked, 8.5).
        whole = validate_fr_coverage_immediate(project_with_fr)
        assert whole == 8.5
        # With fr_id: per-FR scope. The reading is computed from the
        # .coverage data file, not the mocked number — in this fixture
        # suite_run is still mocked, so the reading falls back to 8.5
        # when no in-scope measured lines are found. The fixture is
        # deliberately too minimal to exercise the per-FR scoring
        # branch in isolation; the integration test below exercises it
        # against the real .coverage written by the project's pytest.
        scoped = validate_fr_coverage_immediate(project_with_fr, fr_id="FR-07")
        # When no in-scope lines are measured, we fall back to whole-project.
        assert scoped == 8.5


def test_validate_fr_coverage_immediate_falls_back_when_no_data_file(project_with_fr):
    """A missing .coverage file → fall back to whole-project number."""
    from core.quality_gate.gate1_evidence import validate_fr_coverage_immediate
    # No .coverage file written → coverage.Coverage(data_file=...) will
    # raise on load(). The helper should swallow the exception and
    # return the whole-project figure from run_suite.
    with mock.patch("core.quality_gate.test_suite_run.run_suite",
                    return_value=_suite(coverage=42.0)):
        cov = validate_fr_coverage_immediate(project_with_fr, fr_id="FR-07")
    assert cov == 42.0


# ── Round 56 站6: the per-FR window judged by a whole-project number ──
# Round 56's earlier commit diagnosed this correctly — at P3 the gate asks a
# per-FR question ("is THIS FR done?") and read the answer across the whole
# project, so ten phantom modules other FRs will activate later dragged
# FR-01's real 95% down to 8.5% and BLOCKED. But `fr_id` was only threaded
# into run-fr-step's COVERAGE-FIX inline fallback. `_check_gate1_live_coverage`
# — the check that actually returns 14 and stops advance-phase — still calls
# `validate_fr_coverage_immediate(project)` with no fr_id, and says so:
# "whole-project coverage {cov:.1f}%".
#
# One suite execution, then one recompute per FR off the same `.coverage`
# (Round 25 站1's invariant: the suite runs once per process).
def test_phase_three_judges_each_fr_on_its_own_modules(project_with_fr, capsys):
    """Whole-project 8.5% with every FR at 95% on its own modules → PASS."""
    from cli.phase_cmds import _check_gate1_live_coverage

    with mock.patch(
        "core.quality_gate.gate1_evidence.validate_fr_coverage_immediate",
        return_value=8.5,
    ), mock.patch(
        "core.quality_gate.gate1_evidence.fr_coverage_from_last_run",
        return_value=95.0,
    ):
        rc = _check_gate1_live_coverage(project_with_fr, completed_phase=3)
    assert rc == 0, (
        "FR-07 covers 95% of what it owns; the empty modules other FRs will "
        "fill are not FR-07's defect"
    )
    assert "FR-07" in capsys.readouterr().out


def test_phase_three_names_the_fr_that_actually_fails(project_with_fr, capsys):
    """A real per-FR shortfall must block, and say which FR."""
    from cli.phase_cmds import _check_gate1_live_coverage

    with mock.patch(
        "core.quality_gate.gate1_evidence.validate_fr_coverage_immediate",
        return_value=99.0,
    ), mock.patch(
        "core.quality_gate.gate1_evidence.fr_coverage_from_last_run",
        return_value=50.0,
    ):
        rc = _check_gate1_live_coverage(project_with_fr, completed_phase=3)
    assert rc == 14
    out = capsys.readouterr().out
    assert "FR-07" in out and "50" in out, (
        "a whole-project percentage does not tell the operator which FR to fix"
    )


def test_the_per_fr_recompute_runs_no_extra_pytest(project_with_fr):
    """`fr_coverage_from_last_run` reads `.coverage`; it never runs the suite."""
    from core.quality_gate import gate1_evidence

    with mock.patch("core.quality_gate.test_suite_run.run_suite") as ran:
        gate1_evidence.fr_coverage_from_last_run(project_with_fr, "FR-07")
    ran.assert_not_called()


# ── Bug Round 56 站6+#1: helper must accept str OR Path ──
# Regression for the production crash on FR-07 GATE1: the per-FR coverage
# fix landed, the S4 cross-validation visitor was wired up, and the
# orchestrator tore the run down on `TypeError: unsupported operand
# type(s) for /: 'str' and 'str'`. GateContext.project_root is `str`
# (the dataclass declaration is the source of truth), and the helper
# signature said `Path` — the mismatch surfaced only at run-time. The
# helper now coerces at the entry and the signature says `str | Path`.
def test_fr_coverage_from_last_run_accepts_str_path(project_with_fr):
    """str OR Path both land at the same per-FR number.

    `GateContext.project_root: str` — the contract is the bare string.
    The helper must not raise TypeError when handed one.
    """
    from core.quality_gate import gate1_evidence

    # Case 1: no SAB -> both return None without TypeError
    assert gate1_evidence.fr_coverage_from_last_run(str(project_with_fr), "FR-07") is None
    assert gate1_evidence.fr_coverage_from_last_run(project_with_fr, "FR-07") is None

    # Case 2: with SAB.json
    (project_with_fr / ".methodology" / "SAB.json").write_text(
        json.dumps({
            "sab": {
                "fr_module_traceability": {
                    "FR-07": ["infrastructure.audio_converter"],
                },
            },
        }),
        encoding="utf-8",
    )
    # No .coverage file on disk -> both fall back to None without TypeError
    as_str = gate1_evidence.fr_coverage_from_last_run(str(project_with_fr), "FR-07")
    as_path = gate1_evidence.fr_coverage_from_last_run(project_with_fr, "FR-07")
    assert as_str is None
    assert as_path is None


# ── Round 57 站0: Gate 1 is `single_fr` at every phase it runs ──
# Round 56 站6 made Phase 3 judge each FR on its own modules and left P4+
# reading the whole-project number, so the same Gate 1 was judged by two
# different rules depending on which phase re-ran it — and S4, which has no
# phase condition at all, used the per-FR number at every phase. Three
# statements of one question (harness_bridge S4 / validate_fr_coverage_immediate
# / here), and on a Phase 7 tree they disagreed.
#
# 老闆's adjudication: P4+ judges per FR as well, AND the whole-project floor
# stays as a separately-named condition, so nothing gets looser. The two
# conditions are reported apart because they have different remedies.
def test_phase_seven_judges_each_fr_on_its_own_modules(project_with_fr, capsys):
    """A whole-project pass hiding a per-FR shortfall must block at P4+.

    This is what "P4+ also judges per-FR" buys: today the run advances,
    because 90% ≥ 80% is the only question asked, while FR-07 covers 40% of
    the code it owns.
    """
    from cli.phase_cmds import _check_gate1_live_coverage

    with mock.patch(
        "core.quality_gate.gate1_evidence.fr_code_changed_since_last_gate1",
        return_value=True,
    ), mock.patch(
        "core.quality_gate.gate1_evidence.validate_fr_coverage_immediate",
        return_value=90.0,
    ), mock.patch(
        "core.quality_gate.gate1_evidence.fr_coverage_from_last_run",
        return_value=40.0,
    ):
        rc = _check_gate1_live_coverage(project_with_fr, completed_phase=7)

    assert rc == 14
    out = capsys.readouterr().out
    assert "FR-07" in out and "40" in out, (
        "a whole-project 90% says nothing about the FR that owns 40% of "
        "what it was given"
    )


def test_phase_seven_still_blocks_on_the_whole_project_floor(project_with_fr, capsys):
    """The floor 老闆 kept: every FR green does not excuse the tree.

    P5/P7/P8 have no full-phase gate at their exit, so this check is the only
    whole-project coverage judgement those transitions get. It survives the
    move to per-FR, and it says which of the two conditions failed.
    """
    from cli.phase_cmds import _check_gate1_live_coverage

    with mock.patch(
        "core.quality_gate.gate1_evidence.fr_code_changed_since_last_gate1",
        return_value=True,
    ), mock.patch(
        "core.quality_gate.gate1_evidence.validate_fr_coverage_immediate",
        return_value=62.0,
    ), mock.patch(
        "core.quality_gate.gate1_evidence.fr_coverage_from_last_run",
        return_value=100.0,
    ):
        rc = _check_gate1_live_coverage(project_with_fr, completed_phase=7)

    assert rc == 14
    out = capsys.readouterr().out
    assert "FR-07" in out, (
        "the per-FR verdict was reached and passed; a message that omits it "
        "sends the operator to look for a per-FR defect that is not there"
    )
    assert "whole-project" in out and "62" in out
