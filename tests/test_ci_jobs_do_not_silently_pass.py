"""A CI job whose every check was skipped must not report success in silence.

Round 80 站4, and Round 79 站5's rule at the layer it was not applied to. That
station's commit subject is the rule itself — "a check that did not run must
not read like one that passed" — and it closed four silent-pass paths inside
`scripts/hooks/pre-push`. Two jobs in this repo's own CI have the same defect
and were not swept.

MEASURED, on the most recent run at dff609e6 (`gh run view --json jobs`):

    Phase Quality Gate (C1/C2/C4/C5/C9 + TDD links + coverage)  success   9/13
    P8 Archive & HANDOVER Validation                            success   5/7

The four steps `Phase Quality Gate` did not run are its only four checks
(`audit-phase`, `pytest --cov-fail-under=100`, `spec-coverage-check`,
`verify-trace`); the other nine are checkout, setup, install and the phase
lookup. The two `P8 Archive` did not run are its only two validations. Both
jobs have reported success having executed none of the work their names claim,
on every run.

It is permanent and by construction. `.gitignore:79` excludes
`.methodology/state.json`, so a CI checkout never has one,
`scripts/ci_state_helper.py get current_phase --default 0` answers `0`, and
every `if: steps.phase.outputs.phase >= 1` is false forever. `p8-archive-check`
writes its version of the same thing down: "Returns 'false' on any read failure
(missing/corrupted/empty file) so CI continues."

WHAT THIS GUARD REQUIRES

Not that the checks run — harness-methodology is the framework, not an ASPICE
project, and keeping no phase state is deliberate. What it requires is that the
job SAY so: any job whose checks are gated on a value read at runtime must also
contain an unconditional step that states, in the log, whether those checks
applied and why. The rule is the one the boss set for this station — declare
not-applicable, do not lean on state to skip quietly.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

pytestmark = [pytest.mark.core]

REPO = Path(__file__).resolve().parents[1]
WORKFLOW = REPO / ".github" / "workflows" / "harness_ci.yml"

#: The phrase an applicability statement has to contain. One spelling, so the
#: log is greppable and this guard has something exact to look for.
_MARKER = "not applicable"


def _jobs() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]


def _conditional_steps(job: dict) -> list[dict]:
    """Steps gated on something resolved while the job is running.

    A job-level `if:` (event or branch) is a different thing — it decides
    whether the job appears at all, and a job that did not run does not report
    success. These are the step-level gates that let a job run and check
    nothing.
    """
    return [s for s in job.get("steps", []) if "if" in s and "steps." in str(s["if"])]


def _unconditional_run_steps(job: dict) -> list[dict]:
    return [s for s in job.get("steps", []) if "if" not in s and "run" in s]


def test_every_job_that_can_skip_all_its_checks_says_so():
    offenders: list[str] = []
    for name, job in _jobs().items():
        if not _conditional_steps(job):
            continue
        if not any(
            _MARKER in str(step.get("run", ""))
            for step in _unconditional_run_steps(job)
        ):
            gated = [str(s.get("name", "<unnamed>")) for s in _conditional_steps(job)]
            offenders.append(f"{name}: {len(gated)} runtime-gated steps, "
                             f"no unconditional statement — {gated}")
    assert not offenders, (
        "these jobs can skip every check they are named for and still report "
        "success, with nothing in the log saying so:\n  "
        + "\n  ".join(offenders)
        + f"\n\nAdd an unconditional step whose output contains '{_MARKER}' "
          f"when the checks do not apply, naming the reason. A check that did "
          f"not run must not read like one that passed (Round 79 站5)."
    )


def test_the_applicability_statement_names_why_not_just_that():
    """"not applicable" alone is a shrug; the reason is the whole content."""
    thin: list[str] = []
    for name, job in _jobs().items():
        for step in _unconditional_run_steps(job):
            body = str(step.get("run", ""))
            if _MARKER not in body:
                continue
            if "state.json" not in body:
                thin.append(f"{name}: says '{_MARKER}' without naming what is absent")
    assert not thin, (
        "an applicability statement has to carry its cause the way a [BLOCKED] "
        "carries its remediation (R24 站1):\n  " + "\n  ".join(thin)
    )


def test_the_guard_can_see_a_job_that_would_offend():
    """The detector's own witness — a scan that cannot fail is not a scan."""
    offending = yaml.safe_load(
        "steps:\n"
        "  - name: lookup\n"
        "    id: phase\n"
        "    run: echo phase=0\n"
        "  - name: the only check\n"
        "    if: steps.phase.outputs.phase >= 1\n"
        "    run: exit 1\n"
    )
    assert _conditional_steps(offending), "the scan cannot see a runtime-gated step"
    assert not any(
        _MARKER in str(s.get("run", "")) for s in _unconditional_run_steps(offending)
    ), "the fixture must have no applicability statement, or it proves nothing"
