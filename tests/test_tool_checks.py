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

    def test_unknown_gate_num_raises_rather_than_passing(self, tmp_path, monkeypatch):
        """Round 30 站3 — a caller-contract violation is not "nothing to check".

        `gate_config_path` raises ValueError only for a gate_num outside 1-4,
        and gate_num comes from the framework's own call sites, never from user
        input. Round 29 caught that ValueError and returned `(True, [])`, so a
        typo'd gate number reported "every required tool is installed".

        Letting it propagate routes it to the Round 28 crash boundary, which
        writes a crash bundle naming the caller — where a programming error
        belongs. The genuinely-no-config case is a different branch entirely
        (`cfg_path.exists()` is False → BLOCK with a diagnostic), because the
        four gate YAMLs are framework-owned assets tracked by git ls-files:
        their absence means a broken checkout, not an empty configuration.
        """
        monkeypatch.setattr(
            _gt, "gate_config_path",
            lambda g: (_ for _ in ()).throw(ValueError(f"no config for gate {g}")),
        )
        with pytest.raises(ValueError):
            tool_checks.verify_gate_tools(1, str(tmp_path))

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


def test_verify_gate_tools_still_checks_inside_ci(tmp_path, monkeypatch):
    """Round 30 站3 — an environment variable must not be able to switch a
    check off.

    Round 29 station 1 made this function fail closed. That broke harness's own
    CI (which has no ruff/pyright/mutmut on the runner), and 63b9399 unbroke it
    by adding `if os.environ.get("CI") or os.environ.get("GITHUB_ACTIONS"):
    return True, []` — reintroducing, one commit later, the exact "abstain reads
    as pass" shape station 1 existed to remove. It cited phase_cmds.py's
    substrate-probe CI skip (e92d089) as precedent, but that justification does
    not transfer: the probe checks for an interactive `claude` CLI, which GitHub
    Actions genuinely cannot have and the CI workflow's own comments say is
    "always local". Tool availability is different — harness's own CI installs
    ruff and mypy, and a consumer's CI-run gate scoring without its tools
    verified is precisely the fabrication S2 exists to prevent.

    The trigger was harness's own test suite, so the skip belongs in the test
    layer. 877c1bb already demonstrated the technique in the same round
    (tests/cli/test_phase_cmds_cli.py monkeypatches verify_all_gate_tools);
    this test pins that the production path keeps checking.
    """
    monkeypatch.setenv("CI", "true")
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    cfg_path = tmp_path / "gate1_per_fr.yaml"
    cfg_path.write_text(
        "dimensions:\n"
        "  - name: linting\n"
        "    tool: ruff\n"
        "    requires_tool_execution: true\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(_gt, "gate_config_path", lambda _n: cfg_path)
    monkeypatch.setattr(
        tool_checks, "check_tool_for_dim",
        lambda *_a, **_k: (False, "ruff: not installed"),
    )
    ok, missing = tool_checks.verify_gate_tools(1, str(tmp_path))
    assert ok is False, "a CI env var must not turn a missing tool into a pass"
    assert any("ruff" in m for m in missing)

