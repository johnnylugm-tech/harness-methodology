"""harness/tool_checks.py::verify_gate_tools — corrupt-config fail-closed.

Round 7 (TDD mechanism audit) station 1: the exception-swallow ratchet was
extended to recognise `tuple[bool, ...]` fail-open shapes and immediately
flagged this function's YAML-parse except block, which silently returned
``(True, [])`` (all tools present, nothing missing) for a gate config file
that exists but fails to parse. See test_exception_swallow_ratchet.py.

Round 29 Station 1: verify_gate_tools no longer reads configs from
project/harness/gate_configs/; it delegates to
core.quality_gate.gate_thresholds.gate_config_path(gate_num).  Fixtures
were migrated to write directly under tmp_path and monkeypatch that
resolver instead of staging a harness/gate_configs/ directory tree.
"""
from __future__ import annotations

import pytest

import core.quality_gate.gate_thresholds as _gt
from harness import tool_checks

pytestmark = [pytest.mark.core]


class TestVerifyGateToolsCorruptConfig:
    def test_corrupt_yaml_fails_closed_not_open(self, tmp_path, monkeypatch):
        """A gate config that exists but fails to parse must BLOCK (False,
        [diagnostic]) — not silently report every tool present."""
        cfg_path = tmp_path / "gate1_per_fr.yaml"
        cfg_path.write_text("dimensions: [{name: linting\n", encoding="utf-8")  # unclosed flow seq
        monkeypatch.setattr(_gt, "gate_config_path", lambda g: cfg_path)
        ok, missing = tool_checks.verify_gate_tools(1, str(tmp_path))
        assert ok is False
        assert len(missing) == 1
        assert "gate1_per_fr.yaml" in missing[0]

    def test_missing_config_is_a_legitimate_pass(self, tmp_path, monkeypatch):
        """Distinguish the corrupt case from the genuinely-no-config case:
        no gate config defined at all is a real 'nothing to check' —
        must stay a pass, not be swept up by the corrupt-config fix."""
        monkeypatch.setattr(
            _gt, "gate_config_path",
            lambda g: (_ for _ in ()).throw(ValueError("no config for gate {}".format(g))),
        )
        ok, missing = tool_checks.verify_gate_tools(1, str(tmp_path))
        assert ok is True
        assert missing == []

    def test_valid_yaml_still_evaluates_dimensions(self, tmp_path, monkeypatch):
        """Sanity check: a well-formed config with a tool that's actually
        available still reports a clean pass (the fix didn't flip the
        success path to always-fail)."""
        cfg_path = tmp_path / "gate1_per_fr.yaml"
        cfg_path.write_text(
            "dimensions:\n"
            "  - { name: linting, tool: ruff, requires_tool_execution: true }\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(_gt, "gate_config_path", lambda g: cfg_path)
        monkeypatch.setattr(tool_checks, "check_tool_for_dim", lambda *_a, **_k: (True, ""))
        ok, missing = tool_checks.verify_gate_tools(1, str(tmp_path))
        assert ok is True
        assert missing == []
