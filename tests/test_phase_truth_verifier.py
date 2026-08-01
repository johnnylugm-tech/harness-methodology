"""
tests/test_phase_truth_verifier.py — Unit tests for PhaseTruthVerifier (crg-004).

Tests cover: get_manual_checklist, check_pytest, check_coverage, verify().
check_framework_block is excluded (requires full FrameworkEnforcer env).
"""

import pytest
from unittest.mock import patch
import unittest.mock

from core.quality_gate.phase_truth_verifier import PhaseTruthVerifier, InfraSkip
import json

class TestLoadThreshold:
    def test_default_threshold_when_no_config(self, tmp_path):
        v = PhaseTruthVerifier(str(tmp_path), 1)
        assert v.threshold == 90.0

    def test_threshold_from_config(self, tmp_path):
        cfg_dir = tmp_path / ".methodology"
        cfg_dir.mkdir(exist_ok=True)
        cfg_file = cfg_dir / "enforcement.json"
        cfg_file.write_text(json.dumps({"hr_overrides": {"HR-11_phase_truth_threshold": 95.5}}), encoding="utf-8")
        v = PhaseTruthVerifier(str(tmp_path), 1)
        assert v.threshold == 95.5

    def test_threshold_none_in_config_uses_default(self, tmp_path):
        cfg_dir = tmp_path / ".methodology"
        cfg_dir.mkdir(exist_ok=True)
        cfg_file = cfg_dir / "enforcement.json"
        cfg_file.write_text(json.dumps({"hr_overrides": {"HR-11_phase_truth_threshold": None}}), encoding="utf-8")
        v = PhaseTruthVerifier(str(tmp_path), 1)
        assert v.threshold == 90.0

    def test_override_threshold_via_init(self, tmp_path):
        v = PhaseTruthVerifier(str(tmp_path), 1, threshold=85.0)
        assert v.threshold == 85.0

    def test_pytest_timeout_config(self, tmp_path):
        cfg_dir = tmp_path / ".methodology"
        cfg_dir.mkdir(exist_ok=True)
        cfg_file = cfg_dir / "enforcement.json"
        cfg_file.write_text(json.dumps({"phase_truth": {"pytest_timeout_seconds": 60}}), encoding="utf-8")
        v = PhaseTruthVerifier(str(tmp_path), 1)
        assert v._get_pytest_timeout() == 60

    def test_pytest_timeout_default(self, tmp_path):
        v = PhaseTruthVerifier(str(tmp_path), 1)
        assert v._get_pytest_timeout() == 300

    def test_pytest_timeout_floor(self, tmp_path):
        cfg_dir = tmp_path / ".methodology"
        cfg_dir.mkdir(exist_ok=True)
        cfg_file = cfg_dir / "enforcement.json"
        cfg_file.write_text(json.dumps({"phase_truth": {"pytest_timeout_seconds": 10}}), encoding="utf-8")
        v = PhaseTruthVerifier(str(tmp_path), 1)
        # Should floor at 30
        assert v._get_pytest_timeout() == 30



# ---------------------------------------------------------------------------
# get_manual_checklist
# ---------------------------------------------------------------------------

class TestGetManualChecklist:
    @pytest.mark.parametrize("phase", range(1, 9))
    def test_all_phases_return_list(self, tmp_path, phase):
        checklist = PhaseTruthVerifier(str(tmp_path), phase).get_manual_checklist()
        assert isinstance(checklist, list)
        assert len(checklist) >= 1  # always includes sessions_spawn

    def test_always_includes_sessions_spawn(self, tmp_path):
        items = [c["item"] for c in PhaseTruthVerifier(str(tmp_path), 2).get_manual_checklist()]
        assert ".methodology/sessions_spawn.log" in items

    def test_missing_sessions_spawn_shows_missing(self, tmp_path):
        checklist = PhaseTruthVerifier(str(tmp_path), 1).get_manual_checklist()
        spawn = next(c for c in checklist if c["item"] == ".methodology/sessions_spawn.log")
        assert "missing" in spawn["status"]

    def test_each_item_has_required_keys(self, tmp_path):
        for item in PhaseTruthVerifier(str(tmp_path), 3).get_manual_checklist():
            assert "item" in item
            assert "status" in item
            assert "action" in item


# ---------------------------------------------------------------------------
# check_pytest / check_coverage
#
# Round 25: both read one shared suite execution
# (core.quality_gate.test_suite_run.run_suite) instead of each spawning their
# own pytest. These tests moved from mocking subprocess.run to supplying that
# measurement, which is the seam the methods actually depend on now.
# ---------------------------------------------------------------------------

def _suite(**kwargs):
    from core.quality_gate.test_suite_run import SuiteResult

    base = dict(
        passed=True, coverage=100.0, test_target="tests", cov_target="src",
        returncode=0, output="", ran=True,
    )
    base.update(kwargs)
    return SuiteResult(**base)  # type: ignore[arg-type]


def _with_suite(result):
    return patch("core.quality_gate.test_suite_run.run_suite", return_value=result)


class TestCheckPytest:
    def test_passes_when_suite_is_green(self, tmp_path):
        with _with_suite(_suite()):
            passed, score, _ = PhaseTruthVerifier(str(tmp_path), 3).check_pytest()
        assert passed is True
        assert score == 100.0

    def test_fails_when_suite_is_red(self, tmp_path):
        with _with_suite(_suite(passed=False, returncode=1, output="FAILED 2")):
            passed, score, _ = PhaseTruthVerifier(str(tmp_path), 3).check_pytest()
        assert passed is False
        assert score == 0.0

    def test_returncode_1_with_no_parsed_failures_still_passes(self, tmp_path):
        """Pre-Round-25 tolerance, kept verbatim: exit 1 with zero reported
        failures is a pytest-level complaint, not a failing test."""
        with _with_suite(_suite(passed=False, returncode=1, output="no failures here")):
            passed, _, _ = PhaseTruthVerifier(str(tmp_path), 3).check_pytest()
        assert passed is True

    def test_unmeasurable_suite_reports_why(self, tmp_path):
        with _with_suite(_suite(ran=False, passed=False, coverage=None,
                                reason="pytest not runnable: missing")):
            passed, score, details = PhaseTruthVerifier(str(tmp_path), 3).check_pytest()
        assert passed is False
        assert score == 0.0
        assert "not runnable" in details

    def test_timeout_is_named_as_a_timeout(self, tmp_path):
        with _with_suite(_suite(passed=False, coverage=None, returncode=124)):
            passed, _, details = PhaseTruthVerifier(str(tmp_path), 3).check_pytest()
        assert passed is False
        assert "timed out" in details


class TestCheckCoverage:
    def test_at_threshold_passes(self, tmp_path):
        with _with_suite(_suite(coverage=80.0)):
            passed, score, details = PhaseTruthVerifier(str(tmp_path), 3).check_coverage()
        assert passed is True
        assert score == 80.0
        assert "80.0%" in details

    def test_below_threshold_fails(self, tmp_path):
        with _with_suite(_suite(coverage=20.0)):
            passed, score, _ = PhaseTruthVerifier(str(tmp_path), 3).check_coverage()
        assert passed is False
        assert score == 20.0

    def test_exact_percentage_is_reported_not_a_truncated_integer(self, tmp_path):
        """Round 25: the number comes from coverage's JSON totals, so the
        details line says 85.9%, not the 85.0% the `TOTAL … n%` terminal line
        would have yielded. For the integer thresholds here the verdict is the
        same either way (floor(x) >= T ⟺ x >= T); the reported figure is not.
        """
        with _with_suite(_suite(coverage=85.94736842105263)):
            passed, score, details = PhaseTruthVerifier(str(tmp_path), 3).check_coverage()
        assert passed is True
        assert score == pytest.approx(85.947, abs=0.001)
        assert "85.9%" in details

    def test_unreadable_coverage_report_fails_and_says_so(self, tmp_path):
        with _with_suite(_suite(coverage=None)):
            passed, score, details = PhaseTruthVerifier(str(tmp_path), 3).check_coverage()
        assert passed is False
        assert score == 0.0
        assert "unreadable" in details

    def test_unmeasurable_suite_reports_why(self, tmp_path):
        with _with_suite(_suite(ran=False, coverage=None, passed=False,
                                reason="coverage target src is not a directory")):
            passed, score, details = PhaseTruthVerifier(str(tmp_path), 3).check_coverage()
        assert passed is False
        assert score == 0.0
        assert "not a directory" in details

    def test_verify_method(self, tmp_path):
        v = PhaseTruthVerifier(str(tmp_path), 3)
        with patch.object(v, "check_framework_block", return_value=(True, 100.0, "ok")):
            result = v.verify()
        assert isinstance(result, dict)
        assert "passed" in result
        assert "checks" in result
        assert "checklist" in result


# ---------------------------------------------------------------------------
# verify() integration (Phase 3+)
# ---------------------------------------------------------------------------

class TestVerifyIntegration:
    """Phase 3+ verify() composes framework/pytest/coverage/previous/cross_artifact."""

    def test_verify_renormalizes_after_infra_skip(self, tmp_path):
        """verify() should handle InfraSkip gracefully and renormalize weights."""
        v = PhaseTruthVerifier(str(tmp_path), 3)
        with unittest.mock.patch.multiple(
            v,
            check_framework_block=unittest.mock.DEFAULT,
            check_pytest=unittest.mock.DEFAULT,
            check_coverage=unittest.mock.DEFAULT,
            check_previous_phase_artifacts=unittest.mock.DEFAULT,
            check_cross_artifact=unittest.mock.DEFAULT,
        ) as mocks:
            mocks["check_framework_block"].return_value = (True, 100.0, "ok")
            mocks["check_pytest"].return_value = (True, 100.0, "ok")
            mocks["check_coverage"].return_value = (True, 100.0, "ok")
            mocks["check_previous_phase_artifacts"].return_value = (True, 100.0, "ok")
            mocks["check_cross_artifact"].return_value = (True, 100.0, "ok")
            result = v.verify()
        assert result["passed"] is True
        assert result["total_score"] >= 90.0

class TestCheckSessionLog:
    def test_missing_log(self, tmp_path):
        v = PhaseTruthVerifier(str(tmp_path), 1)
        passed, score, msg = v.check_session_log()
        assert not passed
        assert score == 0.0
        assert "missing" in msg

    def test_empty_log(self, tmp_path):
        (tmp_path / ".methodology").mkdir(exist_ok=True)
        (tmp_path / ".methodology" / "sessions_spawn.log").write_text("   \n")
        v = PhaseTruthVerifier(str(tmp_path), 1)
        passed, score, msg = v.check_session_log()
        assert not passed
        assert score == 0.0
        assert "empty" in msg

    def test_malformed_jsonl_exactly_half(self, tmp_path):
        (tmp_path / ".methodology").mkdir(exist_ok=True)
        (tmp_path / ".methodology" / "sessions_spawn.log").write_text('{"a": 1}\nnot json\n')
        v = PhaseTruthVerifier(str(tmp_path), 3) # phase 3 has no AB check
        passed, score, msg = v.check_session_log()
        assert not passed
        assert score == 0.0
        assert "malformed" in msg

    def test_malformed_jsonl_below_half_passes(self, tmp_path):
        (tmp_path / ".methodology").mkdir(exist_ok=True)
        # 2 valid, 1 invalid = 0.33 malformed
        (tmp_path / ".methodology" / "sessions_spawn.log").write_text('{"a": 1}\n{"b": 2}\nnot json\n')
        v = PhaseTruthVerifier(str(tmp_path), 3)
        passed, score, msg = v.check_session_log()
        assert passed is True
        assert score == 100.0

    def test_ab_reviewer_absence_is_no_longer_judged_from_this_log(self, tmp_path):
        """Round 21 站3: A/B coverage is not decided by a file the agent writes.

        This used to score 50.0 when no reviewer role appeared. Two reasons it
        is gone: the input is agent-writable (taskq's P6 hand-wrote six
        `role: architect` entries), and the scan collected roles across the
        whole log without filtering by phase, so one entry from any phase
        satisfied it everywhere. HR-01 is enforced by the deliverable review.
        """
        (tmp_path / ".methodology").mkdir(exist_ok=True)
        # 1 FR, only developer
        (tmp_path / ".methodology" / "sessions_spawn.log").write_text('{"fr_id": "FR-1", "role": "developer"}\n')
        v = PhaseTruthVerifier(str(tmp_path), 1)
        passed, score, msg = v.check_session_log()
        assert passed is True
        assert score == 100.0
        assert "A/B reviewer missing" not in msg

    def test_ab_reviewer_present_passes(self, tmp_path):
        (tmp_path / ".methodology").mkdir(exist_ok=True)
        # 1 FR, both dev and reviewer
        (tmp_path / ".methodology" / "sessions_spawn.log").write_text('{"fr_id": "FR-1", "role": "developer"}\n{"fr_id": "FR-1", "role": "reviewer"}\n')
        v = PhaseTruthVerifier(str(tmp_path), 1)
        passed, score, msg = v.check_session_log()
        assert passed is True
        assert score == 100.0

    def test_ab_reviewer_ignored_in_other_phases(self, tmp_path):
        (tmp_path / ".methodology").mkdir(exist_ok=True)
        # 1 FR, only developer, but phase 3 ignores AB
        (tmp_path / ".methodology" / "sessions_spawn.log").write_text('{"fr_id": "FR-1", "role": "developer"}\n')
        v = PhaseTruthVerifier(str(tmp_path), 3)
        passed, score, msg = v.check_session_log()
        assert passed is True
        assert score == 100.0

class TestCheckSrsMandatoryReconciliation:
    """Unit tests for check_srs_mandatory_reconciliation (Defect A2/B fix:
    reconciles SRS.md's hard boolean-flag / zero-skip ACs against live
    harness_config.json + pytest skip count — closes the gap where a
    continuous-percentage Gate dimension can't fail on a single violation).
    """

    def _write_srs(self, tmp_path, body: str) -> None:
        reqs = tmp_path / "01-requirements"
        reqs.mkdir(parents=True, exist_ok=True)
        (reqs / "SRS.md").write_text(body, encoding="utf-8")

    def _write_config(self, tmp_path, features: dict) -> None:
        import json
        meth = tmp_path / ".methodology"
        meth.mkdir(parents=True, exist_ok=True)
        (meth / "harness_config.json").write_text(
            json.dumps({"version": 1, "features": features}), encoding="utf-8"
        )

    def test_no_srs_raises_infra_skip(self, tmp_path):
        v = PhaseTruthVerifier(str(tmp_path), 3)
        with pytest.raises(InfraSkip):
            v.check_srs_mandatory_reconciliation()

    def test_passes_with_no_mandatory_acs(self, tmp_path):
        self._write_srs(tmp_path, "### NFR-01: Something\n\nNo hard ACs here.\n")
        v = PhaseTruthVerifier(str(tmp_path), 3)
        passed, score, _ = v.check_srs_mandatory_reconciliation()
        assert passed is True
        assert score == 100.0

    def test_fails_on_flag_mismatch(self, tmp_path):
        self._write_srs(tmp_path, (
            "### NFR-08: Mutation Testing\n\n"
            "- `harness_config.json` must set `features.mutation_testing: true`\n"
        ))
        self._write_config(tmp_path, {"mutation_testing": False})
        v = PhaseTruthVerifier(str(tmp_path), 3)
        passed, score, details = v.check_srs_mandatory_reconciliation()
        assert passed is False
        assert score == 0.0
        assert "NFR-08" in details and "mutation_testing" in details

    def test_passes_on_flag_match(self, tmp_path):
        self._write_srs(tmp_path, (
            "### NFR-08: Mutation Testing\n\n"
            "- `harness_config.json` must set `features.mutation_testing: true`\n"
        ))
        self._write_config(tmp_path, {"mutation_testing": True})
        v = PhaseTruthVerifier(str(tmp_path), 3)
        passed, _, _ = v.check_srs_mandatory_reconciliation()
        assert passed is True

    def test_waived_nfr_exempt_from_flag_check(self, tmp_path):
        self._write_srs(tmp_path, (
            "### NFR-08: Mutation Testing（已豁免）\n\n"
            "**Status**: WAIVED — intentionally disabled.\n\n"
            "- `harness_config.json` must set `features.mutation_testing: true`\n"
        ))
        self._write_config(tmp_path, {"mutation_testing": False})
        v = PhaseTruthVerifier(str(tmp_path), 3)
        passed, score, _ = v.check_srs_mandatory_reconciliation()
        assert passed is True
        assert score == 100.0

    def test_fails_on_skip_count_violation(self, tmp_path):
        self._write_srs(tmp_path, (
            "### NFR-09: Zero Skip\n\n"
            "- `pytest` output must report **0 skipped**\n"
        ))
        with _with_suite(_suite(skipped=5)):
            v = PhaseTruthVerifier(str(tmp_path), 3)
            passed, score, details = v.check_srs_mandatory_reconciliation()
        assert passed is False
        assert score == 0.0
        assert "NFR-09" in details and "5 skipped" in details

    def test_passes_when_skip_count_is_zero(self, tmp_path):
        self._write_srs(tmp_path, (
            "### NFR-09: Zero Skip\n\n"
            "- `pytest` output must report **0 skipped**\n"
        ))
        with _with_suite(_suite(skipped=0)):
            v = PhaseTruthVerifier(str(tmp_path), 3)
            passed, _, _ = v.check_srs_mandatory_reconciliation()
        assert passed is True

    def test_waived_nfr_exempt_from_skip_check(self, tmp_path):
        self._write_srs(tmp_path, (
            "### NFR-09: Zero Skip（已豁免）\n\n"
            "**Status**: WAIVED — intentionally relaxed.\n\n"
            "- `pytest` output must report **0 skipped**\n"
        ))
        with _with_suite(_suite(skipped=5)):
            v = PhaseTruthVerifier(str(tmp_path), 3)
            passed, score, _ = v.check_srs_mandatory_reconciliation()
        assert passed is True
        assert score == 100.0


pytestmark = pytest.mark.gate


class TestSkipsThatDidNotFireAreStillSkips:
    """Round 27 站7b — a zero-skip rule enforced as written, not as measured.

    The count check reads what pytest reported on THIS machine. A suite whose
    skips are conditional — `if not shutil.which(tool): pytest.skip(...)` —
    measures zero wherever the tooling is present and several everywhere else.
    One project declared the rule, measured 35 passed / 0 skipped, and had ten
    `pytest.skip(` calls in the very file that measurement came from.
    """

    def test_a_conditional_skip_is_found_even_when_it_did_not_fire(self, tmp_path):
        from core.quality_gate.phase_truth_verifier import _skip_sites

        tests = tmp_path / "tests"
        tests.mkdir()
        (tests / "test_nfr.py").write_text(
            "import pytest, shutil\n"
            "def test_layering():\n"
            "    if not shutil.which('lint-imports'):\n"
            "        pytest.skip('not installed in this environment')\n"
            "    assert True\n",
            encoding="utf-8",
        )
        sites = _skip_sites(tmp_path)
        assert len(sites) == 1
        assert "test_nfr.py:4" in sites[0]

    def test_marker_forms_are_found_too(self, tmp_path):
        from core.quality_gate.phase_truth_verifier import _skip_sites

        tests = tmp_path / "tests"
        tests.mkdir()
        (tests / "test_nfr.py").write_text(
            "import pytest, sys\n"
            "@pytest.mark.skipif(sys.platform != 'linux', reason='linux only')\n"
            "def test_rss():\n    assert True\n"
            "@pytest.mark.xfail\n"
            "def test_flaky():\n    assert True\n",
            encoding="utf-8",
        )
        assert len(_skip_sites(tmp_path)) == 2

    def test_the_word_in_prose_is_not_a_hit(self, tmp_path):
        """Parsed, not grepped — otherwise the comment explaining the rule
        would violate it."""
        from core.quality_gate.phase_truth_verifier import _skip_sites

        tests = tmp_path / "tests"
        tests.mkdir()
        (tests / "test_nfr.py").write_text(
            '"""No test here may pytest.skip — NFR-09 forbids it."""\n'
            "# a bare pytest.skip( in a comment is not a skip\n"
            "def test_real():\n    assert True\n",
            encoding="utf-8",
        )
        assert _skip_sites(tmp_path) == []

    def test_no_test_tree_is_silent(self, tmp_path):
        from core.quality_gate.phase_truth_verifier import _skip_sites
        assert _skip_sites(tmp_path) == []
