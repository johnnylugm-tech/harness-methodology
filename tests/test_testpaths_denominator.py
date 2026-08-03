"""Round 32 站0/站5 — the framework may judge a test set the project did not declare,
but not without recording that it did.

Measured on a live P4 project. `setup.cfg` declares nine entries:

    [tool:pytest]
    testpaths = .../test_fr05.py .../test_fr06.py .../integration .../test_fr01.py
                .../test_fr03.py .../test_fr04.py .../test_fr08.py
                .../test_coverage_p100.py .../test_nfr_patterns.py

`03-development/tests/` holds seven more that no entry covers:

    test_fr02.py  test_fr07.py  test_security_threats.py
    test_property_invariants.py  test_perf_benchmarks.py
    test_main_module.py  test_bug_hunt_run_all_breaker.py

Two of them are the FR test files for FR-02 and FR-07. A bare `pytest` — what
the agent runs, and what evaluate_dimension.md tells it to run — collects the
nine. The framework runs `pytest <active_test_dir>` (an explicit path, which
overrides `testpaths`) and collects all sixteen. Two denominators, one
comparison, and `setup.cfg`'s pytest section is not in
`DIMENSION_EXCLUSION_FILES`, so neither number is fingerprinted into the
verdict.

Narrowing the default test set is the project's decision to make — Round 27 站7
and Round 30 站6 established the rule for exactly this shape: an exclusion that
can move a score travels with the verdict. So this is a reconciliation, not a
prohibition. The same shape as Round 31 站4's `scope_drift`: report, never
rewrite.
"""
from __future__ import annotations

import pytest

import harness_cli  # noqa: F401  entry-first load order

pytestmark = [pytest.mark.core]


@pytest.fixture()
def project(tmp_path):
    tests = tmp_path / "03-development" / "tests"
    tests.mkdir(parents=True)
    (tmp_path / "03-development" / "src").mkdir(parents=True)
    for name in ("test_fr01.py", "test_fr02.py", "test_extra.py"):
        (tests / name).write_text("def test_x():\n    assert True\n", encoding="utf-8")
    return tmp_path


def _write_setup_cfg(project, testpaths: str) -> None:
    (project / "setup.cfg").write_text(
        f"[tool:pytest]\ntestpaths = {testpaths}\n", encoding="utf-8"
    )


def test_a_project_that_declares_no_testpaths_reports_no_drift(project):
    """No declaration is not a narrow declaration. A checker that reads the
    absence as an empty set would report every file as excluded."""
    from core.quality_gate import testpaths_scope

    assert testpaths_scope.declared_testpaths(project) is None
    assert testpaths_scope.testpaths_drift(project) is None


def test_the_files_the_project_left_out_of_its_own_default_run_are_named(project):
    from core.quality_gate import testpaths_scope

    _write_setup_cfg(project, "03-development/tests/test_fr01.py")
    drift = testpaths_scope.testpaths_drift(project)
    assert drift is not None
    assert set(drift["not_in_declared"]) == {
        "03-development/tests/test_fr02.py",
        "03-development/tests/test_extra.py",
    }, drift
    assert drift["declared_source"].endswith("setup.cfg"), drift


def test_a_declaration_that_covers_everything_reports_an_empty_difference(project):
    """The counter-case: a project that declares its whole test directory is
    measuring exactly what the framework measures, and must not be flagged."""
    from core.quality_gate import testpaths_scope

    _write_setup_cfg(project, "03-development/tests")
    drift = testpaths_scope.testpaths_drift(project)
    assert drift is not None and drift["not_in_declared"] == [], drift


def test_pyproject_and_pytest_ini_are_read_too(project):
    """Three files can carry `testpaths`. Reading only one of them means a
    project that used either of the other two looks like it declared nothing —
    the difference silently becomes empty."""
    from core.quality_gate import testpaths_scope

    (project / "pyproject.toml").write_text(
        "[tool.pytest.ini_options]\n"
        'testpaths = ["03-development/tests/test_fr01.py"]\n',
        encoding="utf-8",
    )
    drift = testpaths_scope.testpaths_drift(project)
    assert drift is not None and drift["declared_source"].endswith("pyproject.toml")

    (project / "pyproject.toml").unlink()
    (project / "pytest.ini").write_text(
        "[pytest]\ntestpaths = 03-development/tests/test_fr01.py\n", encoding="utf-8"
    )
    drift = testpaths_scope.testpaths_drift(project)
    assert drift is not None and drift["declared_source"].endswith("pytest.ini")


def test_a_config_file_it_cannot_parse_reads_as_no_declaration(project):
    """Round 31's rule again: unparseable means 'this file carries no such
    information', never 'the information is that there is none' — which here
    would invent a difference containing every test file in the project."""
    from core.quality_gate import testpaths_scope

    (project / "setup.cfg").write_text("[tool:pytest\nnot ini at all", encoding="utf-8")
    assert testpaths_scope.declared_testpaths(project) is None


def test_doctor_names_the_difference_without_forbidding_it(project):
    """WARN, not ERROR. Narrowing the default test set is the project's call;
    what it may not do is be invisible."""
    from core import doctor

    _write_setup_cfg(project, "03-development/tests/test_fr01.py")
    findings = doctor._check_testpaths_drift(project)
    assert len(findings) == 1, findings
    assert findings[0].severity == "WARN", findings[0]
    assert "test_fr02.py" in findings[0].message, findings[0].message

    _write_setup_cfg(project, "03-development/tests")
    assert doctor._check_testpaths_drift(project) == []


def test_the_declaring_file_travels_with_the_verdict():
    """Round 27 站3 / Round 30 站6: a file whose contents can move a score is
    fingerprinted into the gate verdict. setup.cfg is already registered for
    mutation_testing (its [mutmut] section); its [tool:pytest] section moves
    test_coverage the same way."""
    from harness.harness_bridge import DIMENSION_EXCLUSION_FILES

    assert "test_coverage" in DIMENSION_EXCLUSION_FILES, (
        "the file that declares which tests count is not registered as a "
        "score-altering input for test_coverage"
    )
