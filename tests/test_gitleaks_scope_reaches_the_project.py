"""A fix that only ships at `init-project` never reaches a project past init.

Round 96. Round 92 diagnosed the secrets-scanning defect correctly and shipped
`templates/.gitleaks.toml`: the framework writes free-text agent excerpts into
`.methodology/*.json` and commits them (Round 90), and `generic-api-key`'s
entropy rule fires on names like `test_invalid_api_key_returns_401`. The
config scopes that ONE rule to that ONE directory.

`init-project` writes it. `init-project` runs once, when the project is created.

Measured on taskq-final, which bumped its harness submodule to Round 92's fix
at 09-03 23:36 (`b4224a4`) and ran through Phases 5 to 8 afterwards:

    .gitleaks.toml                       does not exist
    .gitleaksignore fingerprints         6
    added AFTER the bump                 2   (`268ec46`, 09-04 19:06)

Round 92's own ledger judged that fingerprint route a workaround — a
fingerprint is `commit:file:rule:line` and `.methodology/gate1_result.json` is
rewritten every FR, so the fingerprints can never catch up. The project took it
anyway, because nothing told it there was another option.

Round 40 站1 built the answer to this exact shape for the CI workflow
(`core.ci_template.ci_template_drift`: a predicate, a named repair command, and
two readers — doctor and the pre-push hook). Round 92 listed a drift check for
`.gitleaks.toml` under "not doing" with the reason "no evidence it is missing
yet". This file is that evidence.

The predicate differs from the CI one and the difference is the point: the CI
workflow must be byte-identical to the template, while `.gitleaks.toml` is
project-owned — Round 92 established SKIP-never-overwrite because three corpus
projects hand-author their own. So the question is not "does it match" but
"is the project paying for not having one".
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = [pytest.mark.core]

REPO = Path(__file__).resolve().parent.parent

_HAS_GITLEAKS = shutil.which("gitleaks") is not None


def _project_with_framework_findings(root: Path) -> Path:
    """A project whose `.methodology/` trips generic-api-key, as they all do.

    The bait is assembled at runtime: a literal here would be caught by the
    framework's own scan of its own tree (Round 92 站3).
    """
    meth = root / ".methodology"
    meth.mkdir(parents=True, exist_ok=True)
    (meth / "gate1_result.json").write_text(
        '{"breakdown": {"test_coverage": {"tool_evidence": '
        '"' + "test_invalid_api_key" + "_returns_401 PASSED\\n"
        '" + "sk-" + "abcdef1234567890zz" + '"}}}\n",
        encoding="utf-8",
    )
    (meth / "notes.txt").write_text(
        'api_key = "' + "sk-" + "abcdef1234567890zz" + '"\n', encoding="utf-8",
    )
    return root


def test_the_template_exists_and_init_is_its_only_writer():
    """The premise, stated so a later round cannot mistake it for a guess."""
    from core.ci_template import gitleaks_template_path

    assert gitleaks_template_path().is_file()
    init_src = (REPO / "cli" / "project_cmds.py").read_text(encoding="utf-8")
    assert ".gitleaks.toml" in init_src


@pytest.mark.skipif(not _HAS_GITLEAKS, reason="gitleaks not installed")
def test_a_project_paying_the_fingerprint_tax_is_told_there_is_a_config(tmp_path):
    from core.ci_template import gitleaks_scope_missing

    project = _project_with_framework_findings(tmp_path)
    report = gitleaks_scope_missing(project)
    assert report, (
        "a project with no .gitleaks.toml whose .methodology/ trips the default "
        "ruleset is told nothing — which is how taskq-final ended up adding "
        "fingerprints twice after adopting the fix that would have stopped it"
    )
    assert ".gitleaks.toml" in report
    assert "--gitleaks-only" in report, (
        "the report must name a command that repairs it — Round 24: a block "
        f"carries its remediation. Got: {report}"
    )


@pytest.mark.skipif(not _HAS_GITLEAKS, reason="gitleaks not installed")
def test_a_project_with_its_own_config_is_left_alone(tmp_path):
    """Round 92 established that this file is project-owned and never
    overwritten. A check that nags a project which already answered the
    question is the R42 defect — charging the compliant party."""
    from core.ci_template import gitleaks_scope_missing

    project = _project_with_framework_findings(tmp_path)
    (project / ".gitleaks.toml").write_text(
        "[extend]\nuseDefault = true\n", encoding="utf-8",
    )
    assert gitleaks_scope_missing(project) is None


@pytest.mark.skipif(not _HAS_GITLEAKS, reason="gitleaks not installed")
def test_a_project_whose_methodology_is_clean_is_left_alone(tmp_path):
    """The config earns its place by silencing something real. No findings, no
    report — otherwise every project gets a permanent nag for a file it has no
    use for."""
    from core.ci_template import gitleaks_scope_missing

    (tmp_path / ".methodology").mkdir()
    (tmp_path / ".methodology" / "state.json").write_text(
        '{"current_phase": 3}\n', encoding="utf-8",
    )
    assert gitleaks_scope_missing(tmp_path) is None


def test_a_project_with_no_methodology_dir_is_not_accused(tmp_path):
    """Jurisdiction, the same one `ci_template_drift` draws: the harness is
    usable without init-project, and this says nothing about a project that
    never ran it."""
    from core.ci_template import gitleaks_scope_missing

    assert gitleaks_scope_missing(tmp_path) is None


def test_gitleaks_absent_is_inconclusive_not_a_finding(tmp_path, monkeypatch):
    """Round 32: a measurement which could not be taken is not a failing
    measurement. Without this the check would accuse every machine that simply
    has not installed the scanner."""
    from core import ci_template

    _project_with_framework_findings(tmp_path)
    monkeypatch.setattr(ci_template.shutil, "which", lambda _name: None)
    assert ci_template.gitleaks_scope_missing(tmp_path) is None


@pytest.mark.skipif(not _HAS_GITLEAKS, reason="gitleaks not installed")
def test_doctor_reports_it(tmp_path):
    """The predicate has to reach a reader, or it is the shape this round is
    about. Same reader `ci_template_drift` uses."""
    from core.doctor_checks.git_state import _check_gitleaks_scope

    project = _project_with_framework_findings(tmp_path)
    findings = _check_gitleaks_scope(project)
    assert findings, "doctor says nothing"
    assert findings[0].severity == "WARN", findings[0]
    assert "--gitleaks-only" in findings[0].message


def test_init_project_can_deploy_only_the_gitleaks_config(tmp_path):
    """The command the report names has to exist and do only that.

    `--ci-only` is the precedent: a flag that writes one file so the repair is
    safe to paste at a project mid-pipeline.
    """
    src = (REPO / "cli" / "project_cmds.py").read_text(encoding="utf-8")
    assert "--gitleaks-only" in src, "the report names a flag that does not exist"

    result = subprocess.run(
        ["python3", str(REPO / "harness_cli.py"), "init-project",
         "--project", str(tmp_path), "--gitleaks-only"],
        capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp_path / ".gitleaks.toml").is_file(), result.stdout
    # ONLY that: the flag is a repair, not a re-init of a live project.
    assert not (tmp_path / ".github").exists(), result.stdout
    assert not (tmp_path / ".methodology" / "state.json").exists(), result.stdout


def test_gitleaks_only_never_overwrites_a_project_owned_config(tmp_path):
    """Round 92's rule survives the new entry point."""
    (tmp_path / ".gitleaks.toml").write_text("# mine\n", encoding="utf-8")
    subprocess.run(
        ["python3", str(REPO / "harness_cli.py"), "init-project",
         "--project", str(tmp_path), "--gitleaks-only"],
        capture_output=True, text=True, timeout=120,
    )
    assert (tmp_path / ".gitleaks.toml").read_text(encoding="utf-8") == "# mine\n"
