"""A suite that did not run is not a suite where nothing passed.

Round 88 站0. Round 87 站1 taught `spec_coverage` to ask whether a declared
test actually ran and passed. It fetches the answer through
`_live_test_outcomes`, whose own docstring says:

    Never raises: an unmeasurable suite must degrade to presence-only, not
    stop the gate that was asking a different question.

The implementation covered one of the three ways a suite can be unmeasurable
— the one that raises. It did not cover the other two: `run_suite` returning
`ran=False` (no source or test directory, a non-Python project), and
`run_suite` returning `ran=True` with a non-zero exit and an EMPTY outcome
map, which is what pytest produces on a collection error. In both, the value
handed back is `{}`, and `delivery_outcome` reads `{}` as a measurement in
which every declared test failed to be collected.

`_parse_junit_outcomes`, which produces that `{}`, states the rule in its own
docstring:

    Returns {} on any parse failure (missing file, malformed XML) — callers
    must treat that the same as "no outcome data available", never as "zero
    tests ran".

Two consumers already obey it — `spec_tracking_checker.py`'s
`if suite_result.ran and suite_result.test_outcomes` and `scanner.py`'s
`suite_result.test_outcomes if (suite_result.ran and suite_result.test_outcomes)`.
Round 87 站1's fetch was the third, and it is the one that did not.

MEASURED CONSEQUENCE

Replaying `check_ac_deferral_targets` against taskq-cc-new's frozen P3 tree —
the commit its own `phase_completed["3"].sha` names, a tree that passed its P3
exit — returns 35 blocking violations, 33 of them reading `[not_collected]`.
The suite behind them never started: `run_suite` reports `ran=True rc=2`,
because an archived tree has no `.venv`. With the guard applied the same tree
returns 2, both `[absent]`, both real.

taskq-cc-new is the project Round 87 held up as the one that did the work
honestly (its MI test shells out to radon, its p95 test builds data and
measures). It is the one this defect punishes hardest, because a project that
answered every criterion with a correctly-named stub has nothing that can be
reported as uncollected.
"""
from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from core.quality_gate.spec_coverage import (
    _live_test_outcomes,
    delivery_outcome,
    spec_coverage_report,
)
from core.quality_gate.test_suite_run import run_suite

pytestmark = [pytest.mark.core]

CORPUS = Path("/Users/johnny/projects")

_TEST_SPEC = """\
# TEST_SPEC.md

### FR-01: Alpha

| # | Test Function | Inputs | Type | Derivation |
|---|---|---|---|---|
| 1 | `test_fr01_alpha` | n=1 | happy_path | Q1 |
"""


def _project(tmp_path: Path, test_body: str) -> Path:
    src = tmp_path / "03-development" / "src" / "pkg"
    src.mkdir(parents=True)
    (src / "__init__.py").write_text("", encoding="utf-8")
    arch = tmp_path / "02-architecture"
    arch.mkdir(parents=True)
    (arch / "TEST_SPEC.md").write_text(_TEST_SPEC, encoding="utf-8")
    tests = tmp_path / "03-development" / "tests"
    tests.mkdir(parents=True)
    (tests / "test_fr01.py").write_text(textwrap.dedent(test_body), encoding="utf-8")
    return tmp_path


_COLLECTION_ERROR = '''
    """[FR-01]"""
    import a_package_that_does_not_exist_anywhere  # noqa: F401


    def test_fr01_alpha():
        assert True
'''

_REALLY_SKIPPED = '''
    """[FR-01]"""
    import pytest


    @pytest.mark.skip(reason="declared but not implemented")
    def test_fr01_alpha():
        assert True
'''


def test_a_suite_that_could_not_run_reports_no_outcomes(tmp_path: Path) -> None:
    """The reproduction, with a real pytest run rather than a stubbed one.

    A stubbed `run_suite` would let the fixture and the rule share an author,
    which is how Round 19 got two dead rules that stayed green. This asks
    pytest itself for the shape.
    """
    project = _project(tmp_path, _COLLECTION_ERROR)
    result = run_suite(project)
    assert result.ran is True and result.returncode != 0, (
        f"fixture no longer reproduces a started-but-failed suite: "
        f"ran={result.ran} rc={result.returncode}"
    )
    assert result.test_outcomes == {}, result.test_outcomes

    assert _live_test_outcomes(project) is None, (
        "an empty outcome map from a suite that did not complete is "
        "'no measurement', not 'nothing passed'"
    )


def test_delivery_falls_back_to_presence_when_the_suite_did_not_run(tmp_path: Path) -> None:
    """The declared test exists; the run says nothing. It is not undelivered."""
    project = _project(tmp_path, _COLLECTION_ERROR)
    report = spec_coverage_report(project, test_outcomes=_live_test_outcomes(project))
    assert report["declared"] == 1
    assert report["missing"] == [], (
        f"a suite that never started must not make every declaration "
        f"undelivered: {report['missing']}"
    )


def test_a_test_that_really_skipped_is_still_undelivered(tmp_path: Path) -> None:
    """The guard must not switch Round 87 站1 off.

    This is the case the round exists for: the suite ran, the report names the
    test, and its outcome is `skipped`. That is a measurement, and it still
    counts against delivery.
    """
    project = _project(tmp_path, _REALLY_SKIPPED)
    outcomes = _live_test_outcomes(project)
    assert outcomes, "a suite that ran must report outcomes"
    assert any(v == "skipped" for v in outcomes.values()), outcomes
    assert delivery_outcome("test_fr01_alpha", {"test_fr01_alpha"}, outcomes) == "skipped"
    report = spec_coverage_report(project, test_outcomes=outcomes)
    assert [m["test_fn"] for m in report["missing"]] == ["test_fr01_alpha"]


def test_an_empty_map_from_a_completed_run_is_still_a_measurement() -> None:
    """`delivery_outcome`'s own contract is unchanged.

    The fix belongs in the fetch, not in `delivery_outcome`: a caller that
    genuinely measured a suite with no matching tests must still be able to
    say so. Moving the rule into `delivery_outcome` would make `{}` mean
    "unmeasured" for every caller, including one that meant it.
    """
    assert delivery_outcome("test_x", {"test_x"}, {}) == "not_collected"
    assert delivery_outcome("test_x", {"test_x"}, None) == "delivered"


def test_the_frozen_corpus_tree_is_not_charged_for_a_suite_that_never_started() -> None:
    """The measurement that found this, kept executable.

    taskq-cc-new's frozen P3 tree returned 35 blocking violations before the
    guard and 2 after; the 33 that went away all read `[not_collected]` from
    a suite reporting `ran=True rc=2`. Read-only: the tree comes out of
    `git archive`, which writes nothing to the corpus repo.
    """
    import io
    import subprocess
    import tarfile
    import tempfile

    project = CORPUS / "taskq-cc-new"
    state = project / ".methodology" / "state.json"
    if not state.is_file():
        pytest.skip("corpus projects not present on this machine")
    entry = (json.loads(state.read_text(encoding="utf-8")).get("phase_completed") or {}).get("3") or {}
    sha = entry.get("sha")
    if not (isinstance(sha, str) and len(sha) == 40):
        pytest.skip("taskq-cc-new has no P3 verdict commit recorded")
    archived = subprocess.run(
        ["git", "-C", str(project), "archive", sha], capture_output=True, check=False)
    if archived.returncode != 0:
        pytest.skip(f"git archive could not reach {sha[:12]} — could not measure")

    from core.quality_gate.artifact_consistency import check_ac_deferral_targets

    with tempfile.TemporaryDirectory() as td:
        tree = Path(td).resolve()
        with tarfile.open(fileobj=io.BytesIO(archived.stdout)) as tf:
            tf.extractall(tree)
        violations = check_ac_deferral_targets(tree)
    reasons = [str(getattr(v, "message", v)) for v in violations]
    uncollected = [r for r in reasons if "not_collected" in r]
    assert not uncollected, (
        f"{len(uncollected)} deferral(s) blocked because a suite that never "
        f"started reported no outcome for them:\n  " + "\n  ".join(uncollected[:3])
    )
    assert len(violations) == 2, (
        f"expected the two real [absent] deferrals, got {len(violations)}:\n  "
        + "\n  ".join(reasons[:4])
    )
