"""Six places detect a missing tool. None of them can install one.

Round 47 站0. The framework's detection points, each ending in BLOCKED plus a
sentence of prose telling the operator what to type:

    cli/project_cmds.py:348   init-project [11/11]
    cli/phase_cmds.py:2090    run-phase (every phase entry, P1 included)
    cli/gate_cmds.py:1081     run-gate
    cli/gate_cmds.py:1553     finalize-gate
    cli/fr_cmds.py:1594       fr-step Gate 1 preflight
    cli/gate_cmds.py:270      _finalize_env_result (env-check)

`ToolSpec` has `check_cmd` — how to ask whether a tool is there. It has no
field for how to put one there. That half is prose, in seven places that
disagree: code-review-graph is installed three different ways
(`pipx install` in harness/ssi/scripts/verify_tools.py, unpinned
`pip install` in cli/project_cmds.py:386, `pip install …==2.3.6` in the CI
template and in js_blocks' crg_verify_cmd). requirements.txt's header says the
pins exist so the same code scores the same everywhere; the unpinned paths
quietly opt out of that.

Two properties this file pins, both of which the repair executor must have and
neither of which follows from "run pip":

1. An installer's exit code is not evidence the tool is there. Round 24's
   pattern — a field existing is not the field being true — applies verbatim:
   re-probe with the tool's own check_cmd, and believe that.
2. A tool that pip cannot install must not have pip run at it. gitleaks is a
   Go binary; `pip install gitleaks` would fail slowly and report the wrong
   cause. 老闆's boundary for this round is pip-into-.venv only, so an
   external binary is reported, never attempted.
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.core]


def test_repair_reprobes_instead_of_trusting_the_installer(tmp_path):
    """pip exits 0, the tool is still absent — that is not a repair."""
    from harness.env_repair import repair_missing_tools

    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        return type("P", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    # The probe answers "still missing" no matter what the installer said.
    outcome = repair_missing_tools(
        tmp_path, ["scancode"], run=fake_run, reprobe=lambda ids, project: list(ids)
    )

    assert calls, "repair never invoked the installer"
    assert outcome.ok is False
    assert "scancode" in outcome.still_missing


def test_a_project_that_declares_nothing_is_named_not_guessed(tmp_path):
    """The installer states its precondition; it does not invent a manifest.

    taskq-advance is the live case: its SPEC's NFR-07 requires
    requirements.txt, requirements.lock and 08-config/SBOM.json, and `ls` /
    `find` show none of the three exist. 老闆's Round 47 ruling for that shape
    is 阻擋，且不猜測依賴 — reconstructing the list from imports or from
    `pip freeze` would make the framework the author of one of the project's
    own deliverables.
    """
    from harness.env_repair import install_project_dependencies

    project = tmp_path / "declares-nothing"
    (project / "src").mkdir(parents=True)
    (project / "src" / "app.py").write_text("import fastapi\n", encoding="utf-8")

    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):  # pragma: no cover - must not be reached
        calls.append(list(argv))
        return type("P", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    outcome = install_project_dependencies(project, run=fake_run)

    assert calls == [], f"the framework guessed a dependency: {calls}"
    assert outcome.ok is False
    assert outcome.installed is False
    assert "requirements.txt" in outcome.blocked_reason
    assert "pip freeze" in outcome.blocked_reason, (
        "the message must say what it refused to do, not only that it stopped"
    )


def test_a_declared_manifest_is_installed_from(tmp_path):
    """With a manifest present, install from THAT and nothing else."""
    from harness.env_repair import install_project_dependencies

    project = tmp_path / "declares-something"
    project.mkdir()
    manifest = project / "requirements.txt"
    manifest.write_text("fastapi==0.1.0\n", encoding="utf-8")

    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        return type("P", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    outcome = install_project_dependencies(project, run=fake_run)

    assert outcome.ok and outcome.installed
    assert outcome.manifest == manifest
    assert len(calls) == 1 and calls[0][-2:] == ["-r", str(manifest)]


def test_a_non_python_project_blocks_rather_than_pretending(tmp_path):
    """Round 47 implements the Python bootstrap only, and says so out loud."""
    from harness.env_repair import install_project_dependencies

    project = tmp_path / "js"
    project.mkdir()
    (project / "package.json").write_text("{}", encoding="utf-8")

    outcome = install_project_dependencies(project, language="typescript")

    assert outcome.ok is False
    assert outcome.installed is False
    assert "typescript" in outcome.blocked_reason


def test_an_external_binary_is_never_pip_installed(tmp_path):
    """gitleaks has no pip step; repair reports it and does not shell out."""
    from harness.env_repair import repair_missing_tools

    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):  # pragma: no cover - must not be reached
        calls.append(list(argv))
        return type("P", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    outcome = repair_missing_tools(
        tmp_path, ["gitleaks"], run=fake_run, reprobe=lambda ids, project: list(ids)
    )

    assert calls == [], f"pip was run for an external binary: {calls}"
    assert outcome.ok is False
    assert "gitleaks" in outcome.unfixable
    assert "brew install gitleaks" in outcome.advice_for("gitleaks")


def test_ssot_scaffold_writes_a_requirements_skeleton(tmp_path):
    """When a project lacks a manifest, transcribe declared deps from SSOT."""
    from harness.env_repair import install_project_dependencies

    project = tmp_path / "ssot-project"
    project.mkdir()
    (project / "SPEC.md").write_text(
        "## 0. Intent\n"
        "| 依賴樹淺 | fastapi / sqlalchemy / alembic / uvicorn + transitive deps | NFR-07 |\n",
        encoding="utf-8",
    )

    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        return type("P", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    outcome = install_project_dependencies(project, run=fake_run)

    assert outcome.ok and outcome.installed
    assert outcome.manifest is not None
    assert outcome.manifest.is_file()
    content = outcome.manifest.read_text(encoding="utf-8")
    assert "fastapi" in content
    assert "sqlalchemy" in content
    assert "alembic" in content
    assert "uvicorn" in content
    assert len(calls) == 1
    assert calls[0][-2:] == ["-r", str(outcome.manifest)]


def test_ssot_scaffold_does_not_overwrite_user_manifest(tmp_path):
    """If requirements.txt already exists, SSOT scaffold must NOT overwrite it."""
    from harness.ssot_manifest import scaffold_project_manifest_from_ssot

    project = tmp_path / "existing-manifest-project"
    project.mkdir()
    manifest = project / "requirements.txt"
    manifest.write_text("custom-pkg==1.2.3\n", encoding="utf-8")

    (project / "SPEC.md").write_text(
        "## 0. Intent\n"
        "| 依賴樹淺 | fastapi / uvicorn | NFR-07 |\n",
        encoding="utf-8",
    )

    outcome = scaffold_project_manifest_from_ssot(project)

    assert outcome.manifest_path is None
    assert any("already exists; not overwriting" in w for w in outcome.warnings)
    assert manifest.read_text(encoding="utf-8") == "custom-pkg==1.2.3\n"


def test_ssot_scaffold_does_not_infer_versions(tmp_path):
    """Scaffolded manifest must NOT pin versions — version pinning is author's responsibility."""
    from harness.ssot_manifest import scaffold_project_manifest_from_ssot

    project = tmp_path / "unpinned-project"
    project.mkdir()
    (project / "SPEC.md").write_text(
        "## 0. Intent\n"
        "| 依賴樹淺 | fastapi / sqlalchemy / alembic | NFR-07 |\n",
        encoding="utf-8",
    )

    outcome = scaffold_project_manifest_from_ssot(project)

    assert outcome.manifest_path is not None
    content = outcome.manifest_path.read_text(encoding="utf-8")
    lines = [
        line.strip()
        for line in content.splitlines()
        if line.strip() and not line.startswith("#")
    ]
    assert "fastapi" in lines
    assert "sqlalchemy" in lines
    assert "alembic" in lines
    for line in lines:
        assert "==" not in line
        assert ">=" not in line
        assert "<=" not in line
        assert "~=" not in line
    assert "pip-compile" in content


def test_ssot_scaffold_records_to_degradation_ledger(tmp_path):
    """Auto-installing from scaffolded manifest writes an entry with owner='ssot_scaffold'."""
    import json
    from harness.env_repair import install_project_dependencies

    project = tmp_path / "ledger-project"
    project.mkdir()
    (project / "SPEC.md").write_text(
        "## 0. Intent\n"
        "| 依賴樹淺 | fastapi / uvicorn | NFR-07 |\n",
        encoding="utf-8",
    )

    def fake_run(argv, **kwargs):
        return type("P", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    outcome = install_project_dependencies(project, run=fake_run)
    assert outcome.ok and outcome.installed

    ledger = project / ".methodology" / "degradations.jsonl"
    assert ledger.is_file(), "degradation ledger not created"
    entries = [
        json.loads(line)
        for line in ledger.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert any(
        e.get("component") == "gate:env-repair" and e.get("owner") == "ssot_scaffold"
        for e in entries
    )


def test_ssot_scaffold_falls_back_to_block_when_ssot_missing(tmp_path):
    """When both manifest AND SSOT are absent, BLOCK and name itself."""
    from harness.env_repair import install_project_dependencies

    project = tmp_path / "no-ssot-project"
    project.mkdir()

    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        return type("P", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    outcome = install_project_dependencies(project, run=fake_run)

    assert calls == []
    assert outcome.ok is False
    assert outcome.installed is False
    assert (
        "no dependency manifest AND no SSOT to scaffold from"
        in outcome.blocked_reason
    )
    assert "pip freeze" in outcome.blocked_reason

