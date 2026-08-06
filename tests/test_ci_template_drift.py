"""The CI workflow a project runs must be the one the framework ships.

Round 40 站0. `init-project` writes `.github/workflows/harness_quality_gate.yml`
with `write_text(_harness_workflow_template())` — no substitution, no
templating, a byte-for-byte copy of `templates/harness_quality_gate.yml`. That
makes the deployed file and the template comparable by equality, and it is why
this check can exist at all.

What did not exist was anyone comparing them after that first write. The
template is edited by the framework; the copy in a consumer repo is edited by
nobody. `_harness_workflow_template`'s docstring said "both deploy the same
file, so there is no drift" — true of the moment of deployment and guaranteed
of no moment after it.

Measured on taskq-renew (2026-08-06), whose copy was two framework rounds
behind:

    -  run: pip install -r harness/requirements.txt
    +  run: pip install -r harness/requirements.txt || true       (R37 站4)
    -  crg-arch-check --project . $BASELINE
    +  crg-arch-check --project . --threshold 80 $BASELINE        (R38 站2)

Neither changed that project's verdict, which is the point: a drift that
changes nothing today is the one nobody notices before it changes something.
Round 38 removed `--threshold` from every call site the framework owns, and
`tests/test_crg_threshold_ssot.py` enforces that — over the framework's own
files. The consumer copy is outside every scan the framework runs, so the
number came back on the one file no test could see.

This is the same shape as Round 36 (verifying the generator is not verifying
what ships) one level further out: verifying the template is not verifying
what a project runs.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.ci_template import (
    ci_template_drift,
    deployed_ci_path,
    ci_template_path,
)

pytestmark = [pytest.mark.core]

REPO = Path(__file__).resolve().parent.parent


def _init_project(tmp_path: Path, workflow_text: "str | None") -> Path:
    """A project shaped enough for the check: .methodology/ plus the workflow."""
    (tmp_path / ".methodology").mkdir()
    if workflow_text is not None:
        wf = deployed_ci_path(tmp_path)
        wf.parent.mkdir(parents=True, exist_ok=True)
        wf.write_text(workflow_text, encoding="utf-8")
    return tmp_path


def test_the_template_this_check_compares_against_exists() -> None:
    """Positive control: an absent template would make every comparison
    vacuous, and a scan that cannot fail is not a scan (Round 19)."""
    assert ci_template_path().is_file(), (
        f"{ci_template_path()} is the file init-project deploys — without it "
        "there is nothing to compare a deployed copy to"
    )


def test_a_copy_of_the_current_template_is_not_drift(tmp_path: Path) -> None:
    project = _init_project(tmp_path, ci_template_path().read_text(encoding="utf-8"))
    assert ci_template_drift(project) is None


def test_one_changed_byte_is_drift(tmp_path: Path) -> None:
    """Negative control. The historical drifts were single-line edits."""
    text = ci_template_path().read_text(encoding="utf-8")
    project = _init_project(tmp_path, text.replace(
        "crg-arch-check --project . $BASELINE",
        "crg-arch-check --project . --threshold 80 $BASELINE",
    ))
    drift = ci_template_drift(project)
    assert drift is not None, (
        "re-introducing the exact --threshold drift Round 38 removed was not "
        "reported — this check would not have caught taskq-renew"
    )
    assert "--threshold 80" in drift, (
        f"the report must name the differing line, got: {drift}"
    )


def test_a_missing_workflow_is_drift(tmp_path: Path) -> None:
    """Absent is the degenerate case of out-of-date, not a separate world.

    A project whose phase deliverables already require this file (the P3+
    plan checks `.github/workflows/harness_quality_gate.yml exists`) does not
    get to be silently un-gated because the file was deleted rather than
    edited.
    """
    project = _init_project(tmp_path, None)
    drift = ci_template_drift(project)
    assert drift is not None and "not deployed" in drift


def test_the_report_names_the_command_that_repairs_it(tmp_path: Path) -> None:
    """Round 24: a block that does not say what to do is half a block."""
    project = _init_project(tmp_path, "# nothing like the template\n")
    drift = ci_template_drift(project)
    assert drift is not None
    assert "--ci-only" in drift and "--overwrite" in drift, (
        f"the report must carry the redeploy command, got: {drift}"
    )


def test_doctor_reports_ci_template_drift(tmp_path: Path) -> None:
    """The mechanism needs a consumer.

    Round 30's rule: a check nothing calls is half a mechanism. doctor is
    where at-rest cross-file reconciliation already lives (git-sync,
    gate1-evidence, dimension-scope), and it is the one command that runs in
    a consumer repo with the harness submodule beside it — so it can see both
    files at once.
    """
    from core.doctor import run_doctor

    project = _init_project(tmp_path, "# nothing like the template\n")
    (project / ".methodology" / "state.json").write_text(
        '{"state": "RUNNING", "current_phase": 3}', encoding="utf-8")

    checks = {f.check for f in run_doctor(project)}
    assert "ci-template" in checks, (
        f"doctor did not report the drift; checks reported: {sorted(checks)}"
    )


def test_doctor_is_silent_when_the_copy_is_current(tmp_path: Path) -> None:
    """A WARN every clean project sees is a WARN nobody reads."""
    from core.doctor import run_doctor

    project = _init_project(tmp_path, ci_template_path().read_text(encoding="utf-8"))
    (project / ".methodology" / "state.json").write_text(
        '{"state": "RUNNING", "current_phase": 3}', encoding="utf-8")

    assert not [f for f in run_doctor(project) if f.check == "ci-template"]
