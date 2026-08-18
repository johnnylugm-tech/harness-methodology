"""
Tests for core.harness_config feature flag loading and harness integration.
"""
import json
import pytest
from pathlib import Path

from core.harness_config import (
    STALL_TIMEOUTS,
    get_crg_settings,
    get_feature,
    get_timeout,
    load_harness_config,
)

pytestmark = [pytest.mark.mutation_oracle, pytest.mark.core]


# ---------------------------------------------------------------------------
# TestLoadHarnessConfig
# ---------------------------------------------------------------------------

class TestLoadHarnessConfig:
    def test_missing_file_returns_defaults(self, tmp_path):
        cfg = load_harness_config(tmp_path)
        assert cfg["security_design"] is True
        assert cfg["cross_artifact_live_cov"] is False

    def test_valid_file_overrides_defaults(self, tmp_path):
        (tmp_path / ".methodology").mkdir()
        (tmp_path / ".methodology" / "harness_config.json").write_text(
            json.dumps({"version": 1, "features": {"security_design": False}})
        )
        cfg = load_harness_config(tmp_path)
        assert cfg["security_design"] is False
        assert cfg["cross_artifact_live_cov"] is False  # default intact

    def test_malformed_json_returns_defaults(self, tmp_path):
        (tmp_path / ".methodology").mkdir()
        (tmp_path / ".methodology" / "harness_config.json").write_text("not json{{{")
        cfg = load_harness_config(tmp_path)
        assert cfg["security_design"] is True
        assert cfg["cross_artifact_live_cov"] is False

    def test_unknown_keys_ignored(self, tmp_path):
        (tmp_path / ".methodology").mkdir()
        (tmp_path / ".methodology" / "harness_config.json").write_text(
            json.dumps({"version": 1, "features": {"future_flag": True, "security_design": False}})
        )
        cfg = load_harness_config(tmp_path)
        assert cfg["cross_artifact_live_cov"] is False
        assert "future_flag" not in cfg


# ---------------------------------------------------------------------------
# TestGetCrgSettings
# ---------------------------------------------------------------------------

class TestGetCrgSettings:
    def _write(self, tmp_path, payload):
        (tmp_path / ".methodology").mkdir(exist_ok=True)
        (tmp_path / ".methodology" / "harness_config.json").write_text(
            payload if isinstance(payload, str) else json.dumps(payload)
        )

    def test_missing_file_returns_defaults(self, tmp_path):
        assert get_crg_settings(tmp_path) == {"cohesion_healthy": None, "excludes": []}

    def test_valid_values_parsed(self, tmp_path):
        self._write(tmp_path, {
            "crg_cohesion_healthy": 0.2,
            "crg_excludes": [".claude/*", "*.mjs"],
        })
        assert get_crg_settings(tmp_path) == {
            "cohesion_healthy": 0.2,
            "excludes": [".claude/*", "*.mjs"],
        }

    def test_out_of_range_cohesion_ignored(self, tmp_path):
        for bad in (0, -0.5, 1.5, "abc", True, None):
            self._write(tmp_path, {"crg_cohesion_healthy": bad})
            assert get_crg_settings(tmp_path)["cohesion_healthy"] is None, bad

    def test_boundary_one_accepted(self, tmp_path):
        self._write(tmp_path, {"crg_cohesion_healthy": 1.0})
        assert get_crg_settings(tmp_path)["cohesion_healthy"] == 1.0

    def test_non_list_excludes_ignored(self, tmp_path):
        self._write(tmp_path, {"crg_excludes": "not-a-list"})
        assert get_crg_settings(tmp_path)["excludes"] == []

    def test_mixed_type_excludes_sanitized(self, tmp_path):
        self._write(tmp_path, {"crg_excludes": [".claude/*", 42, None, "harness/*"]})
        assert get_crg_settings(tmp_path)["excludes"] == [".claude/*", "harness/*"]

    def test_malformed_json_returns_defaults(self, tmp_path):
        self._write(tmp_path, "not json{{{")
        assert get_crg_settings(tmp_path) == {"cohesion_healthy": None, "excludes": []}

    def test_load_harness_config_unaffected_by_crg_keys(self, tmp_path):
        """Boolean-consumer safety: crg_* value keys must not leak into the
        features dict returned by load_harness_config."""
        self._write(tmp_path, {
            "version": 1,
            "features": {"security_design": False},
            "crg_cohesion_healthy": 0.2,
            "crg_excludes": [".claude/*"],
        })
        cfg = load_harness_config(tmp_path)
        assert set(cfg) == {"cross_artifact_live_cov", "security_design"}
        assert cfg["security_design"] is False


# ---------------------------------------------------------------------------
# TestGetFeature
# ---------------------------------------------------------------------------

class TestGetFeature:
    def test_security_design_default_true(self, tmp_path):
        assert get_feature(tmp_path, "security_design") is True

    def test_cross_artifact_live_cov_default_false(self, tmp_path):
        assert get_feature(tmp_path, "cross_artifact_live_cov") is False

    def test_a_retired_flag_reads_as_nothing(self, tmp_path):
        """Round 60 站2: the name is a tombstone, not a flag with a default.

        `run-gate` refuses a config that still sets one to false
        (tests/test_dimension_cannot_be_disabled.py); what this pins is that
        no code path can read one back as True and act on it.
        """
        from core.harness_config import RETIRED_FEATURES

        for key in RETIRED_FEATURES:
            assert get_feature(tmp_path, key) is None

    def test_file_overrides_default(self, tmp_path):
        (tmp_path / ".methodology").mkdir()
        (tmp_path / ".methodology" / "harness_config.json").write_text(
            json.dumps({"version": 1, "features": {"security_design": False}})
        )
        assert get_feature(tmp_path, "security_design") is False

    def test_unknown_key_returns_none(self, tmp_path):
        assert get_feature(tmp_path, "nonexistent_flag") is None

    def test_security_design_file_disables(self, tmp_path):
        (tmp_path / ".methodology").mkdir()
        (tmp_path / ".methodology" / "harness_config.json").write_text(
            json.dumps({"version": 1, "features": {"security_design": False}})
        )
        assert get_feature(tmp_path, "security_design") is False


# ---------------------------------------------------------------------------
# TestMutmutAdvancePhaseRemoved
# ---------------------------------------------------------------------------

class TestMutmutAdvancePhaseRemoved:
    def test_run_mutation_precheck_not_imported_in_advance_phase(self):
        """advance-phase (cmd_advance_phase) must not call run_mutation_precheck."""
        import ast
        import harness_cli
        src = Path(harness_cli.__file__).read_text(encoding="utf-8")
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "cmd_advance_phase":
                for child in ast.walk(node):
                    if isinstance(child, ast.Name) and child.id == "run_mutation_precheck":
                        pytest.fail("run_mutation_precheck still referenced in cmd_advance_phase")
                return  # function found and clean
        # function not found — acceptable (might be renamed); pass


# ---------------------------------------------------------------------------
# TestPhase4LLMReview
# ---------------------------------------------------------------------------

class TestStallTimeouts:
    """STALL_TIMEOUTS is the single source of truth for stall/timeout
    thresholds. Previously hardcoded across 6+ sites in harness_cli.py and
    mutation_enforcer.py with values 300/600/1200/3600 — adding a new timeout
    required grepping every call site."""

    def test_stall_timeouts_dict_complete(self):
        """All keys expected by call sites must be present."""
        for key in ("subprocess", "env_check", "task_default", "task_dev",
                    "fr_step", "mutation", "state_alert_min"):
            assert key in STALL_TIMEOUTS, f"missing STALL_TIMEOUTS key: {key}"
            assert STALL_TIMEOUTS[key] > 0

    def test_get_timeout_returns_dict_value(self):
        assert get_timeout("subprocess") == 300
        assert get_timeout("env_check") == 900
        assert get_timeout("task_default") == 300
        assert get_timeout("task_dev") == 1200
        assert get_timeout("fr_step") == 600
        assert get_timeout("mutation") == 3600

    def test_get_timeout_unknown_key_raises(self):
        """Unknown keys raise KeyError so a typo can't silently 2x or 6x
        a subprocess's wallclock. See core.harness_config.get_timeout."""
        with pytest.raises(KeyError, match="nonexistent_key"):
            get_timeout("nonexistent_key")

    def test_stall_timeouts_values_are_int(self):
        for v in STALL_TIMEOUTS.values():
            assert isinstance(v, int)


# ---------------------------------------------------------------------------
# TestValuesSection (Round 9 站1) — every default IS the pre-Round-9
# hardcoded behavior, so an absent/empty config must change nothing.
# ---------------------------------------------------------------------------

def _write_cfg(tmp_path, payload):
    (tmp_path / ".methodology").mkdir(exist_ok=True)
    (tmp_path / ".methodology" / "harness_config.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )


class TestGetValue:
    def test_missing_file_returns_defaults(self, tmp_path):
        from core.harness_config import get_value
        assert get_value(tmp_path, "drift_threshold") == 85.0
        assert get_value(tmp_path, "max_fix_rounds") == 3
        assert get_value(tmp_path, "permission_mode") == "bypassPermissions"
        assert get_value(tmp_path, "timeouts") == {}
        assert get_value(tmp_path, "step_max_turns") == {}

    def test_file_value_wins(self, tmp_path):
        from core.harness_config import get_value
        _write_cfg(tmp_path, {"values": {"drift_threshold": 70,
                                         "max_fix_rounds": 5,
                                         "permission_mode": "acceptEdits"}})
        assert get_value(tmp_path, "drift_threshold") == 70
        assert get_value(tmp_path, "max_fix_rounds") == 5
        assert get_value(tmp_path, "permission_mode") == "acceptEdits"

    def test_unknown_consumer_key_raises(self, tmp_path):
        from core.harness_config import get_value
        with pytest.raises(KeyError, match="drift_threshold"):
            get_value(tmp_path, "drift_treshold")  # consumer typo = programming error

    @pytest.mark.parametrize("key,bad", [
        ("drift_threshold", "85"),      # string
        ("drift_threshold", 0),          # below range
        ("drift_threshold", 101),        # above range
        ("drift_threshold", True),       # bool masquerading as number
        ("max_fix_rounds", 0),
        ("max_fix_rounds", 2.5),
        ("permission_mode", ""),
        ("timeouts", {"mutation": "long"}),
        ("timeouts", [1, 2]),
        ("step_max_turns", {"GATE1": 0}),
    ])
    def test_invalid_value_warns_and_falls_back(self, tmp_path, capsys, key, bad):
        import core.harness_config as hc
        hc._warned_unknown.clear()
        _write_cfg(tmp_path, {"values": {key: bad}})
        assert hc.get_value(tmp_path, key) == hc._VALUE_DEFAULTS[key]
        assert "fails type/range validation" in capsys.readouterr().out

    def test_returned_dict_default_is_a_copy(self, tmp_path):
        from core.harness_config import get_value, _VALUE_DEFAULTS
        got = get_value(tmp_path, "timeouts")
        got["mutation"] = 1
        assert _VALUE_DEFAULTS["timeouts"] == {}  # registry not mutated


class TestUnknownKeyWarn:
    def test_typo_feature_key_warns_once(self, tmp_path, capsys):
        import core.harness_config as hc
        hc._warned_unknown.clear()
        _write_cfg(tmp_path, {"features": {"mutation_testng": True}})
        hc.load_harness_config(tmp_path)
        hc.load_harness_config(tmp_path)  # second call must not re-warn
        out = capsys.readouterr().out
        assert out.count("mutation_testng") == 1
        assert "unknown features key" in out

    def test_typo_values_key_warns(self, tmp_path, capsys):
        import core.harness_config as hc
        hc._warned_unknown.clear()
        _write_cfg(tmp_path, {"values": {"drift_treshold": 70}})
        assert hc.get_value(tmp_path, "drift_threshold") == 85.0
        assert "drift_treshold" in capsys.readouterr().out

    def test_unknown_top_level_key_warns(self, tmp_path, capsys):
        import core.harness_config as hc
        hc._warned_unknown.clear()
        _write_cfg(tmp_path, {"features": {}, "quality_gate_threshold": 60})
        hc.load_harness_config(tmp_path)
        assert "quality_gate_threshold" in capsys.readouterr().out

    def test_known_keys_stay_silent(self, tmp_path, capsys):
        import core.harness_config as hc
        hc._warned_unknown.clear()
        _write_cfg(tmp_path, {"version": 1,
                              "features": {"security_design": False},
                              "values": {"drift_threshold": 70.0},
                              "crg_cohesion_healthy": 0.2,
                              "crg_excludes": []})
        hc.load_harness_config(tmp_path)
        hc.get_value(tmp_path, "drift_threshold")
        assert capsys.readouterr().out == ""


class TestTimeoutOverlay:
    def test_no_project_keeps_builtin_verbatim(self):
        assert get_timeout("mutation") == STALL_TIMEOUTS["mutation"]

    def test_unconfigured_project_keeps_builtin(self, tmp_path):
        assert get_timeout("mutation", tmp_path) == STALL_TIMEOUTS["mutation"]

    def test_overlay_wins_for_named_key_only(self, tmp_path):
        _write_cfg(tmp_path, {"values": {"timeouts": {"mutation": 7200}}})
        assert get_timeout("mutation", tmp_path) == 7200
        assert get_timeout("fr_step", tmp_path) == STALL_TIMEOUTS["fr_step"]

    def test_overlay_typo_key_warns_and_is_ignored(self, tmp_path, capsys):
        import core.harness_config as hc
        hc._warned_unknown.clear()
        _write_cfg(tmp_path, {"values": {"timeouts": {"mutatoin": 7200}}})
        assert get_timeout("mutation", tmp_path) == STALL_TIMEOUTS["mutation"]
        assert "mutatoin" in capsys.readouterr().out

    def test_unknown_key_still_raises_with_project(self, tmp_path):
        _write_cfg(tmp_path, {"values": {"timeouts": {"mutation": 7200}}})
        with pytest.raises(KeyError):
            get_timeout("subproc", tmp_path)


class TestCrossArtifactLiveCovFlag:
    def test_defaults_to_false(self, tmp_path):
        assert get_feature(tmp_path, "cross_artifact_live_cov") is False

    def test_file_can_enable(self, tmp_path):
        _write_cfg(tmp_path, {"features": {"cross_artifact_live_cov": True}})
        assert get_feature(tmp_path, "cross_artifact_live_cov") is True


# ---------------------------------------------------------------------------
# TestPhaseTruthKeyMigration (Round 9 站3): the two live enforcement.json
# keys move home to values.*; the old location keeps working as a fallback
# with a migration nudge, so no project breaks mid-run.
# ---------------------------------------------------------------------------

class TestPhaseTruthKeyMigration:
    def _write_enforcement(self, tmp_path, payload):
        (tmp_path / ".methodology").mkdir(exist_ok=True)
        (tmp_path / ".methodology" / "enforcement.json").write_text(
            json.dumps(payload), encoding="utf-8"
        )

    def _verifier(self, tmp_path):
        from core.quality_gate.phase_truth_verifier import PhaseTruthVerifier
        return PhaseTruthVerifier(str(tmp_path), phase=1)

    def test_nothing_configured_keeps_90_and_300(self, tmp_path):
        v = self._verifier(tmp_path)
        assert v.threshold == 90.0
        assert v._get_pytest_timeout() == 300

    def test_values_keys_win(self, tmp_path):
        _write_cfg(tmp_path, {"values": {"phase_truth_threshold": 80.0,
                                         "phase_truth_pytest_timeout": 120}})
        v = self._verifier(tmp_path)
        assert v.threshold == 80.0
        assert v._get_pytest_timeout() == 120

    def test_legacy_enforcement_keys_still_work_with_nudge(self, tmp_path, capsys):
        self._write_enforcement(tmp_path, {
            "hr_overrides": {"HR-11_phase_truth_threshold": 75},
            "phase_truth": {"pytest_timeout_seconds": 60},
        })
        v = self._verifier(tmp_path)
        assert v.threshold == 75.0
        assert v._get_pytest_timeout() == 60
        out = capsys.readouterr().out
        assert "migrate to harness_config.json" in out

    def test_values_beat_legacy_when_both_present(self, tmp_path):
        _write_cfg(tmp_path, {"values": {"phase_truth_threshold": 80.0}})
        self._write_enforcement(tmp_path, {
            "hr_overrides": {"HR-11_phase_truth_threshold": 75}})
        assert self._verifier(tmp_path).threshold == 80.0

    def test_pytest_timeout_floor_still_30(self, tmp_path):
        _write_cfg(tmp_path, {"values": {"phase_truth_pytest_timeout": 5}})
        assert self._verifier(tmp_path)._get_pytest_timeout() == 30

    def test_explicit_ctor_threshold_still_wins_over_everything(self, tmp_path):
        from core.quality_gate.phase_truth_verifier import PhaseTruthVerifier
        _write_cfg(tmp_path, {"values": {"phase_truth_threshold": 80.0}})
        v = PhaseTruthVerifier(str(tmp_path), phase=1, threshold=95.0)
        assert v.threshold == 95.0
