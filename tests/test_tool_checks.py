"""harness/tool_checks.py::verify_gate_tools — corrupt-config fail-closed.

Round 7 (TDD mechanism audit) station 1: the exception-swallow ratchet was
extended to recognise `tuple[bool, ...]` fail-open shapes and immediately
flagged this function's YAML-parse except block, which silently returned
``(True, [])`` (all tools present, nothing missing) for a gate config file
that exists but fails to parse. See test_exception_swallow_ratchet.py.
"""
from __future__ import annotations

import pytest

from harness import tool_checks

pytestmark = [pytest.mark.core]


def _write_gate1_config(project, content: str) -> None:
    cfg_dir = project / "harness" / "gate_configs"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "gate1_per_fr.yaml").write_text(content, encoding="utf-8")


class TestVerifyGateToolsCorruptConfig:
    def test_corrupt_yaml_fails_closed_not_open(self, tmp_path):
        """A gate config that exists but fails to parse must BLOCK (False,
        [diagnostic]) — not silently report every tool present."""
        _write_gate1_config(tmp_path, "dimensions: [{name: linting\n")  # unclosed flow seq
        ok, missing = tool_checks.verify_gate_tools(1, str(tmp_path))
        assert ok is False
        assert len(missing) == 1
        assert "gate1_per_fr.yaml" in missing[0]

    def test_missing_config_is_a_legitimate_pass(self, tmp_path):
        """Distinguish the corrupt case from the genuinely-no-config case:
        no gate_configs/ directory at all is a real 'nothing to check' —
        must stay a pass, not be swept up by the corrupt-config fix."""
        ok, missing = tool_checks.verify_gate_tools(1, str(tmp_path))
        assert ok is True
        assert missing == []

    def test_valid_yaml_still_evaluates_dimensions(self, tmp_path, monkeypatch):
        """Sanity check: a well-formed config with a tool that's actually
        available still reports a clean pass (the fix didn't flip the
        success path to always-fail)."""
        _write_gate1_config(
            tmp_path,
            "dimensions:\n"
            "  - { name: linting, tool: ruff, requires_tool_execution: true }\n",
        )
        monkeypatch.setattr(tool_checks, "check_tool_for_dim", lambda *_a, **_k: (True, ""))
        ok, missing = tool_checks.verify_gate_tools(1, str(tmp_path))
        assert ok is True
        assert missing == []
