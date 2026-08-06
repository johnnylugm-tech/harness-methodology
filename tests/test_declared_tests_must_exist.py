"""Round 42 站0 — a declared test that does not exist has a name.

`spec_coverage._run_spec_coverage_check` already knows exactly which declared
tests are missing. It prints them:

    [spec-coverage] 81/89 (91.0%)
      Missing (8):
        - test_nfr01_submit_status_p95_under_50ms  (type=..., deriv=nfr_pattern)
        ...

and then returns `(exit_code, pct)`. The list is a local; stdout is the only
place it ever goes.

Measured on taskq-renew (read-only, the framework's own checker): 81/89 =
91.011235955056...%, against Gate 4's threshold of 90.0
(`SPEC_COV_THRESHOLDS[4]`). It passed by one point. And that percentage is not
merely similar to the `traceability` score in its committed
`gate4_result.json` — it is that number, to the last digit:

    81 / 89 * 100      = 91.01123595505618
    gate4 traceability = 91.01123595505618

So the count travelled all the way into the verdict, the artifact and the
quality report. The eight names did not — and the eight are
`test_nfr01_*_p95_*` (performance budgets) and `test_nfr03_*_survives_mid_write_kill`
(atomicity under SIGKILL): the two hardest families in the spec.

taskq-plus's number for the same check is 92/93, missing one — and its
TEST_SPEC declares no `nfr_pattern` case at all, so it had none of these to
miss. Declaring more is what put renew's name list on the floor.

This round does not move the threshold: 40/60/80/90 is a stated ladder
(`gate_cmds.py:1610`, `spec_tracking_checker.SPEC_COV_THRESHOLDS`,
`phase_cmds.py:2447-2451`), not a derived one, and replacing a stated number
with a stricter stated number is not a fix. What changes is that the names
survive the function that computes them.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.quality_gate import spec_coverage


_TEST_SPEC = """\
# TEST_SPEC.md

## FR-01: task submission

| # | Test Function | Type | Derivation |
|---|---|---|---|
| 1 | `test_fr01_submit_returns_id` | happy | SPEC §8 #4 |
| 2 | `test_nfr01_submit_status_p95_under_50ms` | nfr_pattern | NP-01 |
| 3 | `test_nfr03_tasks_file_survives_mid_write_kill` | fault_injection | NP-03 |
"""

_IMPLEMENTED = """\
def test_fr01_submit_returns_id():
    assert True
"""


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    (tmp_path / "02-architecture").mkdir(parents=True)
    (tmp_path / "02-architecture" / "TEST_SPEC.md").write_text(
        _TEST_SPEC, encoding="utf-8"
    )
    (tmp_path / "03-development" / "tests").mkdir(parents=True)
    (tmp_path / "03-development" / "tests" / "test_fr01.py").write_text(
        _IMPLEMENTED, encoding="utf-8"
    )
    return tmp_path


def test_an_unimplemented_declared_test_is_named_not_just_counted(project: Path):
    """The report names the two absent tests, and agrees with the percentage.

    Both halves matter. A caller that gets only `pct` can publish 33.3% and a
    caller that gets only the names cannot reproduce the number the gate
    compared against its threshold.
    """
    report = spec_coverage.spec_coverage_report(project)

    assert [m["test_fn"] for m in report["missing"]] == [
        "test_nfr01_submit_status_p95_under_50ms",
        "test_nfr03_tasks_file_survives_mid_write_kill",
    ]
    assert report["declared"] == 3
    assert report["implemented"] == 1
    assert report["pct"] == pytest.approx(1 / 3 * 100)

    _, pct = spec_coverage._run_spec_coverage_check(project, 0.0, verbose=False)
    assert report["pct"] == pytest.approx(pct), (
        "the report and the gate's own percentage must come from one count — "
        "two counts is two chances to disagree"
    )


def test_a_missing_test_carries_the_derivation_that_asked_for_it(project: Path):
    """Each absent name keeps its type and derivation.

    Without them the list cannot answer "which requirement lost its evidence"
    — the question the eight taskq-renew names exist to answer (three
    `nfr_pattern`, four `fault_injection`, one static scan).
    """
    missing = {m["test_fn"]: m for m in spec_coverage.spec_coverage_report(project)["missing"]}
    assert missing["test_nfr01_submit_status_p95_under_50ms"]["type"] == "nfr_pattern"
    assert missing["test_nfr03_tasks_file_survives_mid_write_kill"]["derivation"] == "NP-03"
