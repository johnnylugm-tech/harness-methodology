"""
tests/test_ci_state_helper.py — Unit tests for scripts/ci_state_helper.py.

Covers: _read_state, cmd_get, cmd_is_p8, build_parser / main.
"""
from __future__ import annotations

import json

import pytest

from scripts.ci_state_helper import _read_state, build_parser, cmd_get, cmd_is_p8, main



# ---------------------------------------------------------------------------
# _read_state
# ---------------------------------------------------------------------------

class TestReadState:
    def test_returns_none_for_missing_file(self, tmp_path):
        assert _read_state(tmp_path / "state.json") is None

    def test_returns_none_for_empty_file(self, tmp_path, capsys):
        p = tmp_path / "state.json"
        p.write_text("", encoding="utf-8")
        result = _read_state(p)
        assert result is None
        assert "[WARN]" in capsys.readouterr().err

    def test_returns_none_for_invalid_json(self, tmp_path, capsys):
        p = tmp_path / "state.json"
        p.write_text("{not valid json}", encoding="utf-8")
        result = _read_state(p)
        assert result is None
        assert "[WARN]" in capsys.readouterr().err

    def test_returns_dict_for_valid_json(self, tmp_path):
        p = tmp_path / "state.json"
        p.write_text(json.dumps({"current_phase": 3}), encoding="utf-8")
        result = _read_state(p)
        assert result == {"current_phase": 3}

    def test_returns_none_for_truncated_json(self, tmp_path, capsys):
        p = tmp_path / "state.json"
        p.write_text('{"current_phase":', encoding="utf-8")
        result = _read_state(p)
        assert result is None
        assert "[WARN]" in capsys.readouterr().err

    def test_whitespace_only_is_empty(self, tmp_path, capsys):
        p = tmp_path / "state.json"
        p.write_text("   \n\t  ", encoding="utf-8")
        result = _read_state(p)
        assert result is None
        assert "[WARN]" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# cmd_get
# ---------------------------------------------------------------------------

class TestCmdGet:
    def _args(self, field, default, state_file):
        p = build_parser()
        return p.parse_args(["get", field, "--default", str(default),
                             "--state-file", str(state_file)])

    def test_prints_field_value(self, tmp_path, capsys):
        sf = tmp_path / "state.json"
        sf.write_text(json.dumps({"current_phase": 5}), encoding="utf-8")
        args = self._args("current_phase", "0", sf)
        rc = cmd_get(args)
        assert rc == 0
        assert capsys.readouterr().out.strip() == "5"

    def test_prints_default_when_field_missing(self, tmp_path, capsys):
        sf = tmp_path / "state.json"
        sf.write_text(json.dumps({}), encoding="utf-8")
        args = self._args("current_phase", "0", sf)
        rc = cmd_get(args)
        assert rc == 0
        assert capsys.readouterr().out.strip() == "0"

    def test_prints_default_when_file_missing(self, tmp_path, capsys):
        args = self._args("current_phase", "99", tmp_path / "missing.json")
        rc = cmd_get(args)
        assert rc == 0
        assert capsys.readouterr().out.strip() == "99"

    def test_prints_default_when_file_malformed(self, tmp_path, capsys):
        sf = tmp_path / "state.json"
        sf.write_text("BAD JSON", encoding="utf-8")
        args = self._args("current_phase", "42", sf)
        rc = cmd_get(args)
        assert rc == 0
        assert capsys.readouterr().out.strip() == "42"

    def test_prints_json_for_dict_value(self, tmp_path, capsys):
        sf = tmp_path / "state.json"
        sf.write_text(json.dumps({"nested": {"a": 1}}), encoding="utf-8")
        args = self._args("nested", "{}", sf)
        cmd_get(args)
        out = capsys.readouterr().out.strip()
        assert json.loads(out) == {"a": 1}

    def test_prints_default_when_field_is_none(self, tmp_path, capsys):
        sf = tmp_path / "state.json"
        sf.write_text(json.dumps({"current_phase": None}), encoding="utf-8")
        args = self._args("current_phase", "0", sf)
        cmd_get(args)
        assert capsys.readouterr().out.strip() == "0"

    def test_string_field(self, tmp_path, capsys):
        sf = tmp_path / "state.json"
        sf.write_text(json.dumps({"last_milestone_command": "push-milestone --type p3-pre-gate2"}),
                      encoding="utf-8")
        args = self._args("last_milestone_command", "", sf)
        cmd_get(args)
        assert "push-milestone" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# cmd_is_p8
# ---------------------------------------------------------------------------

class TestCmdIsP8:
    def _args(self, state_file):
        p = build_parser()
        return p.parse_args(["is-p8", "--state-file", str(state_file)])

    def test_false_when_file_missing(self, tmp_path, capsys):
        args = self._args(tmp_path / "missing.json")
        cmd_is_p8(args)
        assert capsys.readouterr().out.strip() == "false"

    def test_false_when_phase_below_8(self, tmp_path, capsys):
        sf = tmp_path / "state.json"
        sf.write_text(json.dumps({"current_phase": 7}), encoding="utf-8")
        args = self._args(sf)
        cmd_is_p8(args)
        assert capsys.readouterr().out.strip() == "false"

    def test_true_when_phase_9(self, tmp_path, capsys):
        sf = tmp_path / "state.json"
        sf.write_text(json.dumps({"current_phase": 9}), encoding="utf-8")
        args = self._args(sf)
        cmd_is_p8(args)
        assert capsys.readouterr().out.strip() == "true"

    def test_true_when_phase_8_and_p8_milestone(self, tmp_path, capsys):
        sf = tmp_path / "state.json"
        sf.write_text(json.dumps({
            "current_phase": 8,
            "last_milestone_command": "push-milestone --type p8-complete",
        }), encoding="utf-8")
        args = self._args(sf)
        cmd_is_p8(args)
        assert capsys.readouterr().out.strip() == "true"

    def test_false_when_phase_8_no_p8_milestone(self, tmp_path, capsys):
        sf = tmp_path / "state.json"
        sf.write_text(json.dumps({
            "current_phase": 8,
            "last_milestone_command": "push-milestone --type p7-risk",
        }), encoding="utf-8")
        args = self._args(sf)
        cmd_is_p8(args)
        assert capsys.readouterr().out.strip() == "false"

    def test_false_when_phase_non_numeric(self, tmp_path, capsys):
        sf = tmp_path / "state.json"
        sf.write_text(json.dumps({"current_phase": "invalid"}), encoding="utf-8")
        args = self._args(sf)
        cmd_is_p8(args)
        assert capsys.readouterr().out.strip() == "false"

    def test_false_when_file_malformed(self, tmp_path, capsys):
        sf = tmp_path / "state.json"
        sf.write_text("{broken", encoding="utf-8")
        args = self._args(sf)
        cmd_is_p8(args)
        assert capsys.readouterr().out.strip() == "false"


# ---------------------------------------------------------------------------
# main (integration)
# ---------------------------------------------------------------------------

class TestMain:
    def test_get_via_main(self, tmp_path, capsys):
        sf = tmp_path / "state.json"
        sf.write_text(json.dumps({"current_phase": 4}), encoding="utf-8")
        rc = main(["get", "current_phase", "--state-file", str(sf), "--default", "0"])
        assert rc == 0
        assert capsys.readouterr().out.strip() == "4"

    def test_is_p8_via_main(self, tmp_path, capsys):
        sf = tmp_path / "state.json"
        sf.write_text(json.dumps({"current_phase": 9}), encoding="utf-8")
        rc = main(["is-p8", "--state-file", str(sf)])
        assert rc == 0
        assert capsys.readouterr().out.strip() == "true"

    def test_invalid_command_exits_nonzero(self):
        with pytest.raises(SystemExit):
            main(["bad-command"])

pytestmark = pytest.mark.gate
