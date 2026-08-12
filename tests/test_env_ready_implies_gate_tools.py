"""env-check says the environment is ready. It never asked about the gate's tools.

Round 47 站0. `env_contract.cli_tools` is the sub-agent's reading of the
project's own documents. The tools the framework itself needs to score a gate
come from the gate YAMLs resolved through `harness/toolchains/registry.py`.
Neither list has ever seen the other.

Measured 2026-08-12 — "tools the registry requires for this project's language,
absent from its stored env_contract.json":

    taskq                 16 of 16 missing   (contract declares 4 tools)
    run-all-by-workflow   16 of 16 missing   (contract declares 4 tools)
    taskq-plus            14 of 16 missing
    taskq-renew           12 of 16 missing
    taskq-advance         11 of 16 missing   (contract declares 20 tools, and
                                              names pip-licenses, which the
                                              framework never runs)

Those runs all reported ready=true. They were not wrong about what they
measured; they were answering a different question from the one the phase
needs answered.

The fix is NOT to merge the two lists. `cli_tools` entries are probed by
`probe_cli_tools`, which resolves PATH binaries and importable packages;
registry keys are tool_ids, several of which are neither (`ast-assertions`,
`readability-v2`, `pytest-cov`, `system-verification` — each carries its own
`check_cmd` precisely because a bare `which` cannot answer for it). Merging
would push `ast-assertions` into a binary probe that must fail, blocking every
project. Each list keeps its own prober; the VERDICT takes both.

`_finalize_env_result` is where readiness becomes an exit code — Round 20 站1
made it the only such place — so that is where the conjunction belongs.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

pytestmark = [pytest.mark.core]


def _project_with_contract(tmp_path: Path) -> Path:
    """A project whose env_contract asks for nothing and is therefore ready."""
    project = tmp_path / "proj"
    (project / ".methodology").mkdir(parents=True)
    (project / ".sessi-work").mkdir(parents=True)
    (project / ".methodology" / "state.json").write_text(
        json.dumps({"state": "RUNNING", "current_phase": 3, "language": "python"}),
        encoding="utf-8",
    )
    (project / ".methodology" / "env_contract.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "source_sha256": "x",
                "env_vars": {"mandatory": [], "has_default": [], "dev_opt_in": []},
                "cli_tools": [],
                "infra_services": {},
            }
        ),
        encoding="utf-8",
    )
    return project


def test_a_contract_with_nothing_in_it_is_ready_on_its_own_terms(tmp_path):
    """Baseline: evaluate_contract keeps answering only its own question."""
    from core.quality_gate.env_contract import evaluate_contract, load_contract

    project = _project_with_contract(tmp_path)
    contract = load_contract(project)
    assert contract is not None
    assert evaluate_contract(contract, project)["ready"] is True


def test_env_check_ready_requires_the_frameworks_own_tools(tmp_path, monkeypatch):
    """With a gate tool missing, env-check must not report the environment ready."""
    from cli import gate_cmds

    project = _project_with_contract(tmp_path)

    monkeypatch.setattr(
        gate_cmds.tool_checks,
        "verify_all_gate_tools",
        lambda _project: (False, ["license_compliance: scancode-toolkit (scancode) not found"]),
    )

    from core.quality_gate.env_contract import evaluate_contract, load_contract

    contract = load_contract(project)
    assert contract is not None
    rc = gate_cmds._finalize_env_result(str(project), evaluate_contract(contract, project))

    assert rc != 0, "env-check reported ready while a Tier-1 gate tool was absent"
    written = json.loads(
        (project / ".sessi-work" / "env_check_result.json").read_text(encoding="utf-8")
    )
    assert written["ready"] is False
    assert "scancode" in json.dumps(written), (
        "the result file must name the tool that made it not-ready"
    )


# ── Bug #131 (2026-08-12): underscore <-> dash tool-name normalization ──


def test_underscore_name_resolves_to_dashed_binary_in_venv(tmp_path) -> None:
    """Regression for the `pip-licenses` install trap.

    Contract names the tool with the import-style spelling
    `pip_licenses`; the package ships a console-script `pip-licenses`
    (dash, no underscore). The probe MUST accept either spelling —
    the contract is what the user wrote, the binary is what the
    installer created, and asking the user to rename the binary is
    not a fix.
    """
    from core.quality_gate.env_verify import _found_on_path_or_venv

    bindir = "Scripts" if os.name == "nt" else "bin"
    fake_bin = tmp_path / ".venv" / bindir
    fake_bin.mkdir(parents=True)
    dashed = fake_bin / "pip-licenses"
    dashed.write_text("#!/bin/sh\necho ok\n", encoding="utf-8")
    dashed.chmod(0o755)

    assert _found_on_path_or_venv("pip_licenses", tmp_path) is True, (
        "underscore claim must resolve to dash binary in project venv"
    )
    assert _found_on_path_or_venv("pip-licenses", tmp_path) is True, (
        "dash claim must still resolve to dash binary (no regression)"
    )


def test_underscore_name_does_not_match_unrelated_binary(tmp_path) -> None:
    """Negative case: a name that has no underscore/dash variant on disk
    must probe False. Guards the Bug #131 fix against a too-eager
    implementation that invents unrelated candidates — a binary literally
    named `pip-licenses` in venv bin must not satisfy a probe for
    `nonexistent_tool_xyz` just because we now also probe
    `nonexistent-tool-xyz` (which is also absent, so the result is False
    either way; the assertion is that the dash variant does not make a
    probe of `bandit` accidentally match `pip-licenses` because of some
    prefix-stripping logic)."""
    from core.quality_gate.env_verify import _found_on_path_or_venv

    bindir = "Scripts" if os.name == "nt" else "bin"
    fake_bin = tmp_path / ".venv" / bindir
    fake_bin.mkdir(parents=True)
    # venv has `pip-licenses` but not `nonexistent_tool_xyz`:
    dashed = fake_bin / "pip-licenses"
    dashed.write_text("#!/bin/sh\necho ok\n", encoding="utf-8")
    dashed.chmod(0o755)

    # Must not return True just because an unrelated binary exists in
    # the venv — a probe for a name absent from PATH and absent from
    # the venv must probe False. (We use a clearly-synthetic name
    # to avoid host-PATH cross-talk with real tools.)
    assert _found_on_path_or_venv("nonexistent_tool_xyz", tmp_path) is False, (
        "underscore/dash variant must not produce false positives on unrelated names"
    )
