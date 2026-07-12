"""Round 9 站2 — consumers actually read the `values` section, precedence locked.

Every default in _VALUE_DEFAULTS equals the old hardcoded constant, so each
wiring gets a pair: "unconfigured → behavior byte-identical to pre-Round-9"
and "configured → the value flows to the spawn/construction site". The
precedence chain locked here is the PRE-EXISTING one:

    per-FR fr_config  >  explicit CLI flag  >  values (project)  >  built-in

(fr_config outranking an explicit CLI flag predates Round 9; the run-fr-step
comment used to claim the opposite — the comment was fixed, not the code.)

Two completeness lints mirror the push-path-symmetry pattern: every
get_timeout() call site must pass a project (a bare call silently ignores
the project's values.timeouts overlay), and every production PhaseHooks
construction must pass drift_threshold (a bare construction silently ignores
values.drift_threshold — the 4 sites wired in this round would otherwise
grow a fifth unswept sibling).
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
import types
from pathlib import Path

import pytest

pytestmark = [pytest.mark.core]

REPO = Path(__file__).resolve().parent.parent


def _write_values(project: Path, values: dict, features: dict | None = None) -> None:
    meth = project / ".methodology"
    meth.mkdir(exist_ok=True)
    payload: dict = {"values": values}
    if features:
        payload["features"] = features
    (meth / "harness_config.json").write_text(json.dumps(payload), encoding="utf-8")


# ---------------------------------------------------------------------------
# run-fr-step spawn kwargs (permission_mode / step_max_turns / fr_step timeout)
# ---------------------------------------------------------------------------

class TestRunFrStepWiring:
    def _run(self, tmp_path, monkeypatch, *, values=None, fr_config=None, **arg_overrides):
        """Drive cmd_run_fr_step through TDD-RED with a fake spawner; return
        the spawn kwargs it received."""
        import harness_cli
        from tests.cli.test_fr_cmds_cli import _setup_preflight_fixtures

        _setup_preflight_fixtures(tmp_path, step="TDD-RED")
        if values is not None:
            _write_values(tmp_path, values)
        if fr_config is not None:
            mf = tmp_path / ".methodology" / "quality_manifest.json"
            manifest = json.loads(mf.read_text(encoding="utf-8"))
            manifest["fr_config"] = {"FR-01": fr_config}
            mf.write_text(json.dumps(manifest), encoding="utf-8")

        # No sentinel/finalize marker exists in the fresh fixture repo, so the
        # TDD-RED idempotency check naturally reports "not done" — real public
        # behavior, no private-name patch (see tests/test_patch_discipline.py).
        dispatched: dict = {}

        class _FakeSpawner:
            def __init__(self, project_path=None):
                pass

            def spawn(self, **kwargs):
                dispatched.update(kwargs)
                return {"status": "complete", "output": "{}"}

        fake_mod = types.ModuleType("core.agent_spawner")
        fake_mod.AgentSpawner = _FakeSpawner  # type: ignore[reportAttributeAccessIssue]
        monkeypatch.setitem(sys.modules, "core.agent_spawner", fake_mod)

        import subprocess as _sp

        class _FakeResult:
            returncode = 0
            stdout = ""
            stderr = ""

        monkeypatch.setattr(_sp, "run", lambda cmd, **kw: _FakeResult())

        args = argparse.Namespace(
            phase=3, fr_id="FR-01", step="TDD-RED", project=str(tmp_path),
            srs=None, timeout=None, max_turns=None, max_fix_rounds=None,
        )
        for k, v in arg_overrides.items():
            setattr(args, k, v)
        harness_cli.cmd_run_fr_step(args)
        return dispatched

    # -- permission_mode ----------------------------------------------------
    def test_unconfigured_permission_mode_unchanged(self, tmp_path, monkeypatch):
        got = self._run(tmp_path, monkeypatch)
        assert got["permission_mode"] == "bypassPermissions"

    def test_values_permission_mode_flows_to_spawn(self, tmp_path, monkeypatch):
        got = self._run(tmp_path, monkeypatch,
                        values={"permission_mode": "acceptEdits"})
        assert got["permission_mode"] == "acceptEdits"

    def test_explicit_flag_beats_values_permission_mode(self, tmp_path, monkeypatch):
        got = self._run(tmp_path, monkeypatch,
                        values={"permission_mode": "acceptEdits"},
                        permission_mode="plan")
        assert got["permission_mode"] == "plan"

    # -- step_max_turns -----------------------------------------------------
    def test_unconfigured_step_max_turns_unchanged(self, tmp_path, monkeypatch):
        from cli.fr_cmds import _STEP_MAX_TURNS
        got = self._run(tmp_path, monkeypatch)
        assert got["max_turns"] == _STEP_MAX_TURNS["TDD-RED"]

    def test_values_step_max_turns_overlay_flows_to_spawn(self, tmp_path, monkeypatch):
        got = self._run(tmp_path, monkeypatch,
                        values={"step_max_turns": {"TDD-RED": 55}})
        assert got["max_turns"] == 55

    def test_explicit_max_turns_beats_overlay(self, tmp_path, monkeypatch):
        got = self._run(tmp_path, monkeypatch,
                        values={"step_max_turns": {"TDD-RED": 55}},
                        max_turns=7)
        assert got["max_turns"] == 7

    def test_overlay_typo_step_warns_and_changes_nothing(self, tmp_path, monkeypatch, capsys):
        from cli.fr_cmds import _STEP_MAX_TURNS
        got = self._run(tmp_path, monkeypatch,
                        values={"step_max_turns": {"TDD-REDD": 55}})
        assert got["max_turns"] == _STEP_MAX_TURNS["TDD-RED"]
        assert "TDD-REDD" in capsys.readouterr().out

    # -- fr_step timeout ----------------------------------------------------
    def test_unconfigured_timeout_unchanged(self, tmp_path, monkeypatch):
        from core.harness_config import STALL_TIMEOUTS
        got = self._run(tmp_path, monkeypatch)
        assert got["task_timeout"] == STALL_TIMEOUTS["fr_step"]  # 600, the old argparse default

    def test_values_timeouts_fr_step_flows_to_spawn(self, tmp_path, monkeypatch):
        got = self._run(tmp_path, monkeypatch,
                        values={"timeouts": {"fr_step": 123}})
        assert got["task_timeout"] == 123

    def test_cli_timeout_beats_values(self, tmp_path, monkeypatch):
        got = self._run(tmp_path, monkeypatch,
                        values={"timeouts": {"fr_step": 123}}, timeout=50)
        assert got["task_timeout"] == 50

    def test_fr_config_beats_cli_and_values(self, tmp_path, monkeypatch):
        """Pre-existing precedence, kept verbatim and now locked: per-FR
        fr_config outranks even an explicit CLI flag."""
        got = self._run(tmp_path, monkeypatch,
                        values={"timeouts": {"fr_step": 123}},
                        fr_config={"timeout": 999}, timeout=50)
        assert got["task_timeout"] == 999

    # -- max_fix_rounds (argparse default now defers to values) --------------
    def test_max_fix_rounds_flag_default_is_none(self):
        """The old default=3 made it impossible for values.max_fix_rounds to
        ever apply (args always carried 3). None = "not given"."""
        src = (REPO / "cli" / "fr_cmds.py").read_text(encoding="utf-8")
        assert '"--max-fix-rounds", type=int, default=None' in src
        assert '"--timeout", type=int, default=None' in src.split("run-fr-step", 1)[1]
        assert 'get_value(project, "max_fix_rounds")' in src


# ---------------------------------------------------------------------------
# drift_threshold: adapter behavior + construction-site completeness lint
# ---------------------------------------------------------------------------

class TestDriftThresholdWiring:
    def test_adapter_unconfigured_keeps_85(self, tmp_path):
        from core.adapters.phase_hooks_adapter import PhaseHooksAdapter
        (tmp_path / ".methodology").mkdir()
        hooks = PhaseHooksAdapter(str(tmp_path), phase=1)._get_hooks()
        assert hooks.drift_threshold == 85.0

    def test_adapter_reads_values_drift_threshold(self, tmp_path):
        from core.adapters.phase_hooks_adapter import PhaseHooksAdapter
        _write_values(tmp_path, {"drift_threshold": 70.0})
        hooks = PhaseHooksAdapter(str(tmp_path), phase=1)._get_hooks()
        assert hooks.drift_threshold == 70.0

    def test_every_production_phasehooks_construction_passes_drift_threshold(self):
        """Completeness lint: a bare PhaseHooks(...) construction silently
        pins drift_threshold to the constructor default and ignores the
        project's config — the exact hole this round closed at 4 sites."""
        offenders = []
        for base in ("cli", "core", "harness", "scripts"):
            for path in sorted((REPO / base).rglob("*.py")):
                if "__pycache__" in path.parts or path.name == "phase_hooks.py":
                    continue
                src = path.read_text(encoding="utf-8")
                if "PhaseHooks(" not in src:
                    continue
                for node in ast.walk(ast.parse(src)):
                    if (isinstance(node, ast.Call)
                            and isinstance(node.func, ast.Name)
                            and node.func.id == "PhaseHooks"
                            and not any(kw.arg == "drift_threshold" for kw in node.keywords)):
                        offenders.append(
                            f"{path.relative_to(REPO).as_posix()}:{node.lineno}")
        assert not offenders, (
            "PhaseHooks constructed without drift_threshold= (ignores "
            "values.drift_threshold): " + ", ".join(offenders)
        )


# ---------------------------------------------------------------------------
# get_timeout call-site completeness lint
# ---------------------------------------------------------------------------

def test_every_get_timeout_call_passes_project():
    """A bare get_timeout(key) can never see values.timeouts — every call
    site wired this round passes its project; a new bare call is the next
    unswept sibling."""
    offenders = []
    for base in ("cli", "core", "harness", "scripts", "detection"):
        root = REPO / base
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.py")):
            if "__pycache__" in path.parts or path.name == "harness_config.py":
                continue
            src = path.read_text(encoding="utf-8")
            if "get_timeout(" not in src:
                continue
            for node in ast.walk(ast.parse(src)):
                if (isinstance(node, ast.Call)
                        and isinstance(node.func, ast.Name)
                        and node.func.id == "get_timeout"
                        and len(node.args) + len(node.keywords) < 2):
                    offenders.append(
                        f"{path.relative_to(REPO).as_posix()}:{node.lineno}")
    assert not offenders, (
        "get_timeout() called without a project (values.timeouts overlay "
        "silently ignored): " + ", ".join(offenders)
    )


# ---------------------------------------------------------------------------
# cross_artifact live-cov toggle: env wins, then the feature flag
# ---------------------------------------------------------------------------

def test_cross_artifact_reads_env_then_feature():
    src = (REPO / "core" / "quality_gate" / "cross_artifact.py").read_text(encoding="utf-8")
    assert 'get_feature(project_root, "cross_artifact_live_cov")' in src
    assert 'environ.get("HARNESS_CROSS_ARTIFACT_COV")' in src
    # env must be consulted before the feature flag (per-invocation override)
    assert src.index('environ.get("HARNESS_CROSS_ARTIFACT_COV")') < src.index(
        'get_feature(project_root, "cross_artifact_live_cov")')
