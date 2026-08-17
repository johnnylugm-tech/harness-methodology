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

import os
import subprocess

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


# ---------------------------------------------------------------------------
# 2026-08-11 — venv-scoped env for S2 tool-availability probes (FR-09
# infra_fail class). check_tool_for_dim / run_tool_check used to run every
# check_cmd with the harness's fully-inherited ambient env (not even
# PYTHONPATH, let alone PATH) — a stray same-named tool ahead of the target
# project's .venv/bin on PATH could make S2 wrongly report the wrong
# version's availability. See test_tool_env_parity.py for the parallel fix
# in tool_runners.run_tool (S4's independent tool re-run).
# ---------------------------------------------------------------------------


def test_run_tool_check_passes_env_through_to_subprocess(monkeypatch):
    seen = {}

    def _fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        seen["env"] = kwargs.get("env")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(tool_checks.subprocess, "run", _fake_run)
    marker_env = {"PATH": "/marker/bin", "VIRTUAL_ENV": "/marker"}
    tool_checks.run_tool_check("true", cwd="/tmp", env=marker_env)
    assert seen["env"] == marker_env, (
        "run_tool_check must pass its env argument through to subprocess.run "
        "unchanged, so a caller-supplied venv-scoped env actually takes effect"
    )


def test_run_tool_check_defaults_to_none_env_unchanged_behavior(monkeypatch):
    """Backward compatibility: a caller that doesn't pass env= must see the
    same fully-inherited-ambient-env behavior as before this change."""
    seen = {}

    def _fake_run(cmd, **kwargs):
        seen["env"] = kwargs.get("env")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(tool_checks.subprocess, "run", _fake_run)
    tool_checks.run_tool_check("true", cwd="/tmp")
    assert seen["env"] is None


def test_check_tool_for_dim_uses_venv_scoped_env_when_project_root_given(
    tmp_path, monkeypatch
):
    """check_tool_for_dim must resolve check_cmd's bare tool name against
    the target project's own .venv/bin, not the harness's ambient PATH —
    the S2 analog of the S4 fix in tool_runners.run_tool."""
    bin_dir = tmp_path / ".venv" / "bin"
    bin_dir.mkdir(parents=True)

    seen = {}

    def _fake_run(cmd, **kwargs):
        seen["env"] = kwargs.get("env")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(tool_checks.subprocess, "run", _fake_run)
    ok, diag = tool_checks.check_tool_for_dim(
        "linting", "ruff", "python", project_root=str(tmp_path)
    )
    assert ok is True, diag
    path_parts = (seen["env"].get("PATH") or "").split(os.pathsep)
    assert path_parts[0] == str(bin_dir), (
        f"expected the project's .venv/bin first on PATH: {path_parts[:3]}"
    )
    assert seen["env"].get("VIRTUAL_ENV") == str(tmp_path / ".venv")


def test_check_tool_for_dim_without_project_root_uses_no_env_override(
    monkeypatch,
):
    """No project_root means no venv to resolve against — env stays None,
    same as calling run_tool_check directly with no env (backward
    compatible for any caller that doesn't pass project_root)."""
    seen = {}

    def _fake_run(cmd, **kwargs):
        seen["env"] = kwargs.get("env")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(tool_checks.subprocess, "run", _fake_run)
    tool_checks.check_tool_for_dim("linting", "ruff", "python", project_root=None)
    assert seen["env"] is None



# ── Phase-aware gate tool check (cli/phase_cmds._phase_gate_tools) ──
# Regression for the bug where run-phase at P1/P2 treated `scancode
# --version` as a hard dependency even though license_compliance (the only
# tool-scored dimension that uses scancode) only runs at Gate 4 / P6. The
# fix splits verify_all_gate_tools' one BLOCKED verdict into (critical,
# anticipated) so tools needed by a future-phase gate degrade to a warning.
def test_phase_gate_tools_phase1_treats_all_gates_as_anticipated(monkeypatch):
    """Phase 1 does not run any gate — every missing tool is 'anticipated'."""
    from cli.phase_cmds import PHASE_GATES, _phase_gate_tools

    assert PHASE_GATES[1] == [], "Phase 1 must not require any gate tool"
    assert PHASE_GATES[2] == [], "Phase 2 must not require any gate tool"

    monkeypatch.setattr(
        "harness.tool_checks.verify_gate_tools",
        lambda _gate_num, _project: (False, ["license_compliance: scancode-toolkit (scancode) not found"]),
    )
    ok, critical, anticipated = _phase_gate_tools(1, "/tmp/none")
    assert ok is True, "Phase 1 must not block on missing future-phase gate tools"
    assert critical == [], "Phase 1 must produce no critical entries"
    assert len(anticipated) == 4, "All 4 gates' missing tools are anticipated at P1"
    assert all(e.startswith("gate") for e in anticipated)


def test_phase_gate_tools_phase3_is_critical_for_gates_1_and_2(monkeypatch):
    """Phase 3 runs Gate 1 + Gate 2 — missing tools there must block."""
    from cli.phase_cmds import PHASE_GATES, _phase_gate_tools

    assert PHASE_GATES[3] == [1, 2], "Phase 3 must require Gate 1 + Gate 2"

    # stub: gate 1+2 missing critical, gate 3+4 missing but future-phase
    def _fake_verify(gate_num, _project):
        if gate_num in (1, 2):
            return (False, ["linting: ruff not found"])
        return (False, ["license_compliance: scancode-toolkit (scancode) not found"])

    monkeypatch.setattr("harness.tool_checks.verify_gate_tools", _fake_verify)
    ok, critical, anticipated = _phase_gate_tools(3, "/tmp/none")
    assert ok is False, "Phase 3 must block when gate 1/2 tools are missing"
    assert any("gate1" in e for e in critical)
    assert any("gate2" in e for e in critical)
    assert any("gate3" in e for e in anticipated)
    assert any("gate4" in e for e in anticipated)


def test_phase_gate_tools_phase6_all_gates_critical(monkeypatch):
    """Phase 6 runs Gate 4 — every missing tool is critical."""
    from cli.phase_cmds import PHASE_GATES, _phase_gate_tools

    assert PHASE_GATES[6] == [1, 2, 3, 4], "Phase 6 must require every gate"

    monkeypatch.setattr(
        "harness.tool_checks.verify_gate_tools",
        lambda _gate_num, _project: (False, ["x: missing"]),
    )
    ok, critical, anticipated = _phase_gate_tools(6, "/tmp/none")
    assert ok is False
    assert len(anticipated) == 0
    assert len(critical) == 4


# ── Round 56 站1: the phase→gate map is a fourth statement ──
# `PHASE_GATES` (cli/phase_cmds.py) was hand-written with keys 1..6 only, so
# `_phase_gate_tools` at P7/P8/P9 read `.get(phase, [])` → every gate landed in
# `anticipated`, `critical` stayed empty and `_tools_ok` was unconditionally
# True. P7 and P8 are two of the four phases that run Gate 1 per-FR
# (core/phase_topology: per_fr_gate1=True), so the check stopped blocking at
# exactly the phases it exists for.
#
# The defect is not three missing keys. `core/phase_topology.py` opens with
# "Single source of truth for the phase/gate topology … entry/exit gate
# mapping, per-FR Gate 1" and carries all nine phases. A second table was
# written next to it.
def test_gate_set_per_phase_is_derived_from_the_topology():
    """The contract, stated here independently of how the code derives it.

    Measured 2026-08-17 against the hand-written table: P1–P6 agree cell for
    cell, and P7/P8/P9 had no entry at all. The expectation below is written
    out rather than compared against `PHASE_GATES` on purpose — a table
    checked against itself agrees with itself (Round 19 站1).

    Cumulative, because a gate that ran at an earlier phase is re-run as a
    DELTA check later (P4/P5/P7/P8 all re-run Gate 1): if its tool vanished
    since, that must block, not warn.
    """
    from core.phase_topology import gates_for_phase

    expected = {
        1: set(),            # Requirements — no gate runs
        2: set(),            # Architecture — no gate runs
        3: {1, 2},           # Gate 1 per-FR during P3, Gate 2 closes it
        4: {1, 2, 3},        # entry Gate 2, exit Gate 3, Gate 1 per-FR
        5: {1, 2, 3},        # entry Gate 3, Gate 1 per-FR
        6: {1, 2, 3, 4},     # exit Gate 4
        7: {1, 2, 3, 4},     # entry Gate 4, Gate 1 per-FR
        8: {1, 2, 3, 4},     # entry Gate 4, Gate 1 per-FR
        9: {1, 2, 3, 4},     # entry Gate 4, Gate 1 per-FR (maintenance)
    }
    for phase, gates in expected.items():
        assert gates_for_phase(phase) == gates, f"phase {phase}"


def test_phase_seven_blocks_on_a_missing_gate_one_tool(monkeypatch):
    """P7 runs Gate 1 per-FR. A missing Gate 1 tool there is not 'anticipated'."""
    from cli.phase_cmds import _phase_gate_tools

    monkeypatch.setattr(
        "harness.tool_checks.verify_gate_tools",
        lambda _gate_num, _project: (False, ["linting: ruff not found"]),
    )
    ok, critical, anticipated = _phase_gate_tools(7, "/tmp/none")
    assert ok is False, "P7 runs Gate 1 per-FR — a missing tool must block"
    assert any("gate1" in e for e in critical)
    assert anticipated == [], "no gate is in P7's future"


# ── Round 56 站1: an unreadable gate config is not a future-phase need ──
# `_walk_gate_tools` fails CLOSED on a gate config that is missing or does not
# parse, and says so: "Expected framework-owned asset — is the harness checkout
# intact?". `_phase_gate_tools` dropped that diagnostic into the same bucket as
# "the tool is not installed yet", so at any phase that does not run the gate
# it printed a WARN and continued. A corrupt framework asset is a harness/infra
# fault (docs/ERROR_HANDLING.md owner taxonomy), not something a later phase
# will get around to.
def test_a_corrupt_gate_config_is_critical_at_every_phase(tmp_path, monkeypatch):
    """Even at P1, which runs no gate at all."""
    import core.quality_gate.gate_thresholds as _gt
    from cli.phase_cmds import _phase_gate_tools

    cfg = tmp_path / "gate4_final.yaml"
    cfg.write_text("dimensions: [{name: linting\n", encoding="utf-8")  # unclosed
    monkeypatch.setattr(_gt, "gate_config_path", lambda _g: cfg)

    ok, critical, anticipated = _phase_gate_tools(1, str(tmp_path))
    assert ok is False, (
        "a gate config the framework cannot read is a broken checkout — "
        "it does not become readable when the phase that needs it arrives"
    )
    assert any("gate4_final.yaml" in e for e in critical)
    assert anticipated == [], "a config error is never merely anticipated"
