"""The Phase 2 step that declares a constraint also delivers its checker config.

Round 105 站1b made `advance-phase --completed 2` refuse (exit 48) a SAB whose
typed `architecture_constraints` name an executor this framework runs but whose
config the project never wrote. The workflow's SAB Generation step is the one
told to declare typed import-linter mappings, and its SCOPE RULES allowed it to
edit SAD.md §5 only. No other P2 step writes `.importlinter`, so the declaring
agent could not satisfy the rule it was declaring.

Every project that reached P2's exit after Round 105 halted there: taskq-sol
(2026-09-09, cleared by an ad-hoc `fix(gate)` commit) and taskq-open (advance
agent refused to act outside its scope, run-all halted).

`check-constraint-config` asks exactly the question advance-phase asks, through
the same function, so the SAB step can verify its own output before Peer Review.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.core]

REPO = Path(__file__).resolve().parents[1]

_TYPED = {
    "id": "NFR-06-layers",
    "executor": "import-linter",
    "contract_type": "layers",
    "contract_name": "taskq layers",
    "source_modules": ["taskq_api.api", "taskq_api.service"],
}

_MATCHING = """\
[importlinter]
root_package = taskq_api

[importlinter:contract:layers]
name = taskq layers
type = layers
layers =
    taskq_api.api
    taskq_api.service
"""


def _project(tmp_path: Path, constraints: list, importlinter: str = "") -> Path:
    root = tmp_path / "proj"
    (root / ".methodology").mkdir(parents=True)
    (root / ".methodology" / "SAB.json").write_text(
        json.dumps({"sab": {"architecture_constraints": constraints}}),
        encoding="utf-8")
    if importlinter:
        (root / ".importlinter").write_text(importlinter, encoding="utf-8")
    return root


def _cli(project: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(REPO / "harness_cli.py"),
         "check-constraint-config", "--project", str(project)],
        capture_output=True, text=True, cwd=str(REPO),
    )


# ── the command ─────────────────────────────────────────────────────────────

def test_a_typed_constraint_with_no_config_exits_48(tmp_path) -> None:
    from cli.exit_codes import EX_ADVANCE_CONSTRAINT_UNCONFIGURED

    proc = _cli(_project(tmp_path, [_TYPED]))
    assert proc.returncode == EX_ADVANCE_CONSTRAINT_UNCONFIGURED, proc.stdout + proc.stderr
    assert "NFR-06-layers" in proc.stdout


def test_a_config_that_matches_the_declaration_exits_0(tmp_path) -> None:
    proc = _cli(_project(tmp_path, [_TYPED], _MATCHING))
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_a_config_that_drifts_from_the_declaration_exits_48(tmp_path) -> None:
    """Counterexample: the contract exists but names a different module set,
    so it does not decide the declared constraint."""
    drifted = _MATCHING.replace("    taskq_api.service\n", "    taskq_api.models\n")
    proc = _cli(_project(tmp_path, [_TYPED], drifted))
    assert proc.returncode == 48, proc.stdout + proc.stderr


def test_no_declaration_exits_0(tmp_path) -> None:
    assert _cli(_project(tmp_path, [])).returncode == 0


def test_the_command_asks_advance_phase_s_own_question(tmp_path, monkeypatch) -> None:
    """One definition, two entry points: replacing the rule the advance
    precheck uses must change what the command says."""
    import core.quality_gate.arch_constraints as ac
    from cli.checks.gates import cmd_check_constraint_config

    monkeypatch.setattr(ac, "unconfigured_blocking_reason", lambda _rows: "")
    args = type("A", (), {"project": str(_project(tmp_path, [_TYPED]))})()
    assert cmd_check_constraint_config(args) == 0


# ── the workflow step that uses it ──────────────────────────────────────────

def _sab_step(js_path: Path) -> str:
    text = js_path.read_text(encoding="utf-8")
    # run-all.js prefixes inlined phase titles with "P2 · ".
    start = text.index("SAB Generation')")
    end = text.index("label: 'sab-generation'", start)
    return text[start:end]


@pytest.mark.parametrize("js", ["phase2-architecture.js", "run-all.js"])
def test_the_sab_step_writes_and_verifies_the_config(js) -> None:
    step = _sab_step(REPO / ".claude" / "workflows" / js)
    assert "check-constraint-config" in step, (
        "the SAB step declares typed import-linter constraints but never "
        "verifies their config — advance-phase exit 48 is the first to notice")
    assert ".importlinter" in step, (
        "the SAB step's SCOPE RULES still forbid writing the config the "
        "constraints it declares require")
