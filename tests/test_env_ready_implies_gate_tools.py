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


# ── Bug #131/sym: a scorer with no binary of its own name ──
# Regression for the P3 env-check failure mode where `probe_cli_tools` looked
# for an `ast_docstrings` (underscore) binary on PATH while the registry
# canonicalises on `ast-docstrings` (dash), and reported a false BLOCKED on
# every fresh project whose contract and the registry used different
# conventions.
#
# Round 56 站2 kept the symptom fixed and changed the remedy. These two tests
# used to assert `_is_in_process_tool(...) is True`, i.e. that env-check
# classifies the name and reports the tool present without measuring — which
# is how `radon-mi` went green on a host with no `radon`. The intent survives;
# the criterion is now the registry's own `check_cmd`, both spellings.
def test_a_scorer_with_no_binary_of_its_own_name_resolves_by_check_cmd():
    """ast_docstrings (contract) -> ast-docstrings (registry) -> its check_cmd."""
    from core.quality_gate.env_verify import _registry_check_cmd

    assert _registry_check_cmd("ast-docstrings")
    assert _registry_check_cmd("ast_docstrings") == _registry_check_cmd("ast-docstrings")
    # A name the registry does not carry has no declared probe; it falls
    # through to the generic import probe as before.
    assert _registry_check_cmd("definitely-not-a-registry-tool") is None


def test_readability_v2_and_radon_mi_resolve_through_radon(monkeypatch):
    """The scorer is `harness/toolchains/readability_v2.py`; `radon` is data.

    No binary called `readability-v2` or `radon-mi` exists, so a bare PATH
    probe must not decide. The registry says what to ask — `radon --version`
    — and both spellings of the contract name reach it.
    """
    from core.quality_gate import env_verify
    from harness import tool_checks

    for name in ("readability-v2", "readability_v2", "radon-mi", "radon_mi"):
        assert env_verify._registry_check_cmd(name) == "radon --version 2>&1", name

    monkeypatch.setattr(tool_checks, "run_tool_check", lambda *_a, **_kw: True)
    found = env_verify.probe_cli_tools(["readability-v2", "radon_mi"],
                                       Path("/tmp/does-not-matter"))
    assert found == {"readability-v2": True, "radon_mi": True}


# ── Round 56 站2: env-check answered a different question ──
# `probe_cli_tools` classified names into "PATH tool" vs "in-process tool" and
# reported the second kind present without measuring anything. Two families
# went green on a host that cannot run them:
#
#   radon-mi / readability-v2  — dispatched as `python -m harness.toolchains.*`,
#     but both modules shell out (`radon_mi_ast_stripped.py:17`,
#     `readability_v2.py:14` → subprocess(["radon", ...])). Their registry
#     check_cmd is literally `radon --version`.
#   js-assertions / js-error-handling / js-doc-coverage / js-mi — check_cmd is
#     `import tree_sitter, tree_sitter_javascript, tree_sitter_typescript`,
#     a real probe that was skipped whole.
#
# `ToolSpec.check_cmd` is the registry's own answer to "can this tool run".
# docs/PROPOSAL_ADJUDICATIONS.md:2555 already adjudicated this: those tool_ids
# "各自帶 `check_cmd` 正是為此". The classifier does not need to exist.
def test_a_scorer_whose_data_source_is_missing_is_not_present(monkeypatch):
    """radon-mi with no `radon` on the host must probe False, not True."""
    from core.quality_gate import env_verify
    from harness import tool_checks

    asked: list[str] = []

    def _probe(check_cmd, cwd=None, env=None):
        asked.append(check_cmd)
        return "radon" not in check_cmd  # this host has everything but radon

    monkeypatch.setattr(tool_checks, "run_tool_check", _probe)
    result = env_verify.probe_cli_tools(["radon-mi", "readability-v2"],
                                        Path("/tmp/does-not-matter"))
    assert any("radon" in c for c in asked), (
        "the registry's own check_cmd was never consulted — the probe decided "
        "from the tool's name instead of from what the tool needs"
    )
    assert result["radon-mi"] is False
    assert result["readability-v2"] is False


def test_a_tree_sitter_scanner_without_tree_sitter_is_not_present(monkeypatch):
    """The js-* check_cmd is a real import probe; it must actually run."""
    from core.quality_gate import env_verify
    from harness import tool_checks

    asked: list[str] = []

    def _probe(check_cmd, cwd=None, env=None):
        asked.append(check_cmd)
        return "tree_sitter" not in check_cmd

    monkeypatch.setattr(tool_checks, "run_tool_check", _probe)
    result = env_verify.probe_cli_tools(["js-assertions", "js-mi"],
                                        Path("/tmp/does-not-matter"))
    assert any("tree_sitter" in c for c in asked), (
        "the tree-sitter import probe the registry declares was skipped"
    )
    assert result["js-assertions"] is False
    assert result["js-mi"] is False
