"""PR 1: scanner unification tests.

Confirms that `core.traceability.scanner` produces identical output to the
prior in-script implementations for the same input. The scripts
(`scripts/build_traceability.py`, `scripts/check_spec_trace.py`) now
re-export from scanner; this file pins their behavior to the canonical
implementation.
"""
from pathlib import Path

import pytest


# Playbook §6: dynamic mutation-oracle marker
pytestmark = pytest.mark.mutation_oracle


@pytest.fixture
def fixture_repo(tmp_path) -> Path:
    """Minimal repo with SAD.md, code [FR-XX] annotations, and a test file."""
    arch = tmp_path / "02-architecture"
    arch.mkdir()
    sad = arch / "SAD.md"
    sad.write_text(
        "# SAD\n\n"
        "| FR | Component |\n"
        "|---|---|\n"
        "| FR-01 | `core/foo.py` |\n"
        "| FR-02 | `core/bar.py` |\n"
        "\n"
        "FR-01: feature one\n"
        "FR-02: feature two\n"
        "FR-03: feature three (no code yet)\n"
    )
    foo = tmp_path / "core" / "foo.py"
    foo.parent.mkdir()
    foo.write_text('""" Implements FR-01. [FR-01] """\ndef f(): return 1\n')
    bar = tmp_path / "core" / "bar.py"
    bar.write_text('""" Implements FR-02. [FR-02] """\ndef g(): return 2\n')
    tests = tmp_path / "tests"
    tests.mkdir()
    test = tests / "test_fr_01.py"
    test.write_text('"""[FR-01]""" def test_x(): assert True\n')
    return tmp_path


def test_scanner_extracts_sad_frs(fixture_repo):
    from core.traceability.scanner import extract_fr_ids_from_sad
    frs = extract_fr_ids_from_sad(fixture_repo / "02-architecture" / "SAD.md")
    assert frs == ["FR-01", "FR-02", "FR-03"]


def test_scanner_scan_python_fr_annotations(fixture_repo):
    from core.traceability.scanner import scan_python_fr_annotations
    fr_to_files = scan_python_fr_annotations(fixture_repo)
    assert "FR-01" in fr_to_files
    assert "FR-02" in fr_to_files
    assert "FR-03" not in fr_to_files
    assert any("foo.py" in p for p in fr_to_files["FR-01"])


def test_scanner_scan_test_fr_coverage(fixture_repo):
    from core.traceability.scanner import scan_test_fr_coverage
    fr_to_tests = scan_test_fr_coverage(fixture_repo / "tests")
    assert "FR-01" in fr_to_tests
    assert any("test_fr_01.py" in p for p in fr_to_tests["FR-01"])


def test_scanner_scan_sad_fr_modules(fixture_repo):
    from core.traceability.scanner import scan_sad_fr_modules
    fr_to_modules = scan_sad_fr_modules(fixture_repo / "02-architecture" / "SAD.md")
    assert "FR-01" in fr_to_modules
    assert "core/foo.py" in fr_to_modules["FR-01"]


# ---------------------------------------------------------------------------
# Defect A fix: outcome-aware scanning. A requirement mentioned inside a
# skipped/failed test must NOT count as coverage — only a mention inside a
# test whose own outcome is "passed" does. `test_outcomes=None` (the
# existing tests above) preserves the old presence-only behavior; these
# tests exercise the new branch directly.
# ---------------------------------------------------------------------------

@pytest.fixture
def outcome_fixture_repo(tmp_path) -> Path:
    """A tests/ dir with one file holding two functions: one that mentions
    NFR-08/[FR-05] and one that mentions NFR-09/[FR-06] — real, parseable
    Python, unlike fixture_repo's single-line docstring shortcut above."""
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_mixed.py").write_text(
        "def test_a():  # NFR-08\n"
        "    \"\"\"[FR-05] AC-NFR-08.a\"\"\"\n"
        "    assert True\n"
        "\n"
        "def test_b():  # NFR-09\n"
        "    \"\"\"[FR-06] AC-NFR-09.a\"\"\"\n"
        "    assert True\n",
        encoding="utf-8",
    )
    return tmp_path


def test_nfr_coverage_skipped_test_does_not_count(outcome_fixture_repo):
    from core.traceability.scanner import scan_test_nfr_coverage
    outcomes = {
        "tests/test_mixed.py::test_a": "skipped",
        "tests/test_mixed.py::test_b": "passed",
    }
    result = scan_test_nfr_coverage(
        outcome_fixture_repo / "tests", test_outcomes=outcomes, project_root=outcome_fixture_repo
    )
    assert "NFR-08" not in result, "a skipped test must not count as coverage"
    assert "NFR-09" in result
    assert "tests/test_mixed.py" in result["NFR-09"]


def test_nfr_coverage_failed_test_does_not_count(outcome_fixture_repo):
    from core.traceability.scanner import scan_test_nfr_coverage
    outcomes = {
        "tests/test_mixed.py::test_a": "failed",
        "tests/test_mixed.py::test_b": "passed",
    }
    result = scan_test_nfr_coverage(
        outcome_fixture_repo / "tests", test_outcomes=outcomes, project_root=outcome_fixture_repo
    )
    assert "NFR-08" not in result

def test_nfr_coverage_missing_from_outcomes_does_not_count(outcome_fixture_repo):
    """A function absent from test_outcomes (e.g. collection never reached
    it) must not count either — only an explicit "passed" counts."""
    from core.traceability.scanner import scan_test_nfr_coverage
    result = scan_test_nfr_coverage(
        outcome_fixture_repo / "tests",
        test_outcomes={"tests/test_mixed.py::test_b": "passed"},
        project_root=outcome_fixture_repo,
    )
    assert "NFR-08" not in result
    assert "NFR-09" in result


def test_nfr_coverage_none_outcomes_preserves_presence_only(outcome_fixture_repo):
    """test_outcomes=None (default) must behave exactly like before this
    fix — both NFRs count, regardless of pass/fail, matching the pre-Defect-A
    contract for callers with no live run data (e.g. non-Python projects)."""
    from core.traceability.scanner import scan_test_nfr_coverage
    result = scan_test_nfr_coverage(outcome_fixture_repo / "tests")
    assert "NFR-08" in result
    assert "NFR-09" in result


def test_fr_coverage_skipped_test_does_not_count(outcome_fixture_repo):
    from core.traceability.scanner import scan_test_fr_coverage
    outcomes = {
        "tests/test_mixed.py::test_a": "skipped",
        "tests/test_mixed.py::test_b": "passed",
    }
    result = scan_test_fr_coverage(
        outcome_fixture_repo / "tests", test_outcomes=outcomes, project_root=outcome_fixture_repo
    )
    assert "FR-05" not in result
    assert "FR-06" in result


def test_fr_coverage_filename_pattern_requires_a_pass_in_file(tmp_path):
    """The test_frNN.py filename-based signal must also become
    outcome-aware: if every test in that file was skipped, the file must
    not count as coverage just because of its name."""
    from core.traceability.scanner import scan_test_fr_coverage
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_fr07.py").write_text(
        "def test_only():\n    import pytest\n    pytest.skip('env')\n",
        encoding="utf-8",
    )
    result_all_skipped = scan_test_fr_coverage(
        tests, test_outcomes={"tests/test_fr07.py::test_only": "skipped"}, project_root=tmp_path
    )
    assert "FR-07" not in result_all_skipped

    result_one_passing = scan_test_fr_coverage(
        tests, test_outcomes={"tests/test_fr07.py::test_only": "passed"}, project_root=tmp_path
    )
    assert "FR-07" in result_one_passing


def test_check_traceability_unified(fixture_repo):
    """The unified check_traceability reports the expected gaps."""
    from core.traceability.scanner import check_traceability
    rt, report = check_traceability(fixture_repo)
    # FR-01 has code+test; FR-02 has code only; FR-03 has neither
    assert "FR-01" not in report["untested"]
    assert "FR-01" not in report["uncoded"]
    assert "FR-02" in report["untested"]  # no test
    assert "FR-02" not in report["uncoded"]
    assert "FR-03" in report["untested"]
    assert "FR-03" in report["uncoded"]
    assert not report["complete"]


def test_legacy_re_exports_still_work(fixture_repo):
    """`scripts.check_spec_trace.check_traceability` and `build_traceability`
    scan functions must remain importable for backward compatibility."""
    # The scripts add the project root to sys.path on import
    import sys
    sys.path.insert(0, str(fixture_repo))
    sys.path.insert(0, str(fixture_repo.parent))

    from scripts.check_spec_trace import check_traceability as legacy_check
    from scripts.build_traceability import (
        extract_fr_ids_from_sad as legacy_extract,
        scan_python_fr_annotations as legacy_scan_py,
        scan_test_fr_coverage as legacy_scan_tests,
        scan_sad_fr_modules as legacy_scan_modules,
    )

    rt, report = legacy_check(fixture_repo)
    assert "FR-02" in report["untested"]
    sad = fixture_repo / "02-architecture" / "SAD.md"
    assert legacy_extract(sad) == ["FR-01", "FR-02", "FR-03"]
    assert "FR-01" in legacy_scan_py(fixture_repo)
    assert "FR-01" in legacy_scan_tests(fixture_repo / "tests")
    assert "FR-01" in legacy_scan_modules(sad)


def test_scan_all_returns_combined_view(fixture_repo):
    from core.traceability.scanner import scan_all
    view = scan_all(fixture_repo)
    assert set(view["sad_frs"]) == {"FR-01", "FR-02", "FR-03"}  # type: ignore[reportArgumentType]
    assert "FR-01" in view["fr_to_code"]  # type: ignore[reportOperatorIssue]
    assert "FR-01" in view["fr_to_tests"]  # type: ignore[reportOperatorIssue]
    assert "core/foo.py" in view["fr_to_modules"]["FR-01"]  # type: ignore[reportIndexIssue]
    assert view["ghost_frs"] == []  # nothing in code/tests outside SAD


# ---------------------------------------------------------------------------
# Defect A fix, follow-up bug found during live validation against
# taskq-plus: both scan_test_*_coverage functions defaulted `project =
# tests_dir.parent`, which is WRONG for a nested layout (e.g.
# `<root>/03-development/tests` — tests_dir.parent is `03-development`, not
# `<root>`). Harmless under presence-only scanning (rel was cosmetic), but
# fatal once rel must exactly match run_suite's "file::name" outcome keys
# (always relative to the true root, since that's pytest's actual cwd) — a
# mismatch there silently emptied every result instead of raising anything.
# `project_root` must be passed explicitly to get correct paths for a
# nested layout.
# ---------------------------------------------------------------------------

@pytest.fixture
def nested_layout_repo(tmp_path) -> Path:
    """tests/ lives under <root>/03-development/tests, not <root>/tests —
    tests_dir.parent ("03-development") differs from the true root."""
    tests = tmp_path / "03-development" / "tests"
    tests.mkdir(parents=True)
    (tests / "test_mixed.py").write_text(
        "def test_a():  # NFR-08\n"
        "    \"\"\"[FR-05]\"\"\"\n"
        "    assert True\n",
        encoding="utf-8",
    )
    return tmp_path


def test_nfr_coverage_nested_layout_needs_project_root(nested_layout_repo):
    from core.traceability.scanner import scan_test_nfr_coverage
    tests_dir = nested_layout_repo / "03-development" / "tests"
    outcomes = {"03-development/tests/test_mixed.py::test_a": "passed"}

    # Without project_root: rel would be computed relative to
    # "03-development" (tests_dir.parent), which can never match a key
    # that starts with "03-development/tests/..." — silently losing all
    # coverage was the original bug found during live validation; this is
    # now a loud ValueError instead.
    with pytest.raises(ValueError, match="project_root is required"):
        scan_test_nfr_coverage(tests_dir, test_outcomes=outcomes)

    # With project_root: rel matches, coverage is found.
    result = scan_test_nfr_coverage(
        tests_dir, test_outcomes=outcomes, project_root=nested_layout_repo
    )
    assert "NFR-08" in result
    assert result["NFR-08"] == ["03-development/tests/test_mixed.py"]


def test_fr_coverage_nested_layout_needs_project_root(nested_layout_repo):
    from core.traceability.scanner import scan_test_fr_coverage
    tests_dir = nested_layout_repo / "03-development" / "tests"
    outcomes = {"03-development/tests/test_mixed.py::test_a": "passed"}

    with pytest.raises(ValueError, match="project_root is required"):
        scan_test_fr_coverage(tests_dir, test_outcomes=outcomes)

    result = scan_test_fr_coverage(
        tests_dir, test_outcomes=outcomes, project_root=nested_layout_repo
    )
    assert "FR-05" in result
    assert result["FR-05"] == ["03-development/tests/test_mixed.py"]


# ---------------------------------------------------------------------------
# Defect A fix, second follow-up bug found during live validation against
# taskq-plus: _test_function_ranges/outcome matching used the bare function
# name as the key, which (a) collides when two different classes both have
# a same-named method, and more importantly (b) never matches ANY
# @pytest.mark.parametrize case, since test_outcomes only ever holds the
# bracket-suffixed per-case id pytest itself reports (e.g.
# "test_x[case-a]"), never a bare "test_x" key. Confirmed live: taskq-plus's
# test_fr01.py alone has 19 test classes and several parametrized cases
# (test_fr01_d[...]) that this silently failed to match.
# ---------------------------------------------------------------------------

@pytest.fixture
def class_and_param_repo(tmp_path) -> Path:
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_class.py").write_text(
        "class TestFoo:\n"
        "    def test_a(self):  # NFR-20\n"
        "        \"\"\"[FR-20]\"\"\"\n"
        "        assert True\n"
        "\n"
        "class TestBar:\n"
        "    def test_a(self):  # NFR-21\n"
        "        \"\"\"[FR-21]\"\"\"\n"
        "        assert True\n"
        "\n"
        "def test_param():  # NFR-22\n"
        "    \"\"\"[FR-22]\"\"\"\n"
        "    assert True\n",
        encoding="utf-8",
    )
    return tmp_path


def test_class_methods_with_same_name_do_not_collide(class_and_param_repo):
    """TestFoo.test_a and TestBar.test_a must be tracked independently —
    one passing must not paper over the other's failure."""
    from core.traceability.scanner import scan_test_nfr_coverage
    outcomes = {
        "tests/test_class.py::TestFoo.test_a": "passed",
        "tests/test_class.py::TestBar.test_a": "skipped",
    }
    result = scan_test_nfr_coverage(
        class_and_param_repo / "tests", test_outcomes=outcomes, project_root=class_and_param_repo
    )
    assert "NFR-20" in result   # TestFoo.test_a passed
    assert "NFR-21" not in result  # TestBar.test_a skipped, same bare name


def test_parametrized_case_is_matched_via_bracket_suffix(class_and_param_repo):
    """test_outcomes only ever holds pytest's own bracketed per-case id
    (e.g. "test_param[case-a]"); a bare "test_param" lookup must still find
    it, matching if ANY parametrized variant passed."""
    from core.traceability.scanner import scan_test_nfr_coverage
    outcomes = {
        "tests/test_class.py::test_param[case-a]": "skipped",
        "tests/test_class.py::test_param[case-b]": "passed",
    }
    result = scan_test_nfr_coverage(
        class_and_param_repo / "tests", test_outcomes=outcomes, project_root=class_and_param_repo
    )
    assert "NFR-22" in result


def test_parametrized_case_all_skipped_does_not_count(class_and_param_repo):
    from core.traceability.scanner import scan_test_nfr_coverage
    outcomes = {
        "tests/test_class.py::test_param[case-a]": "skipped",
        "tests/test_class.py::test_param[case-b]": "skipped",
    }
    result = scan_test_nfr_coverage(
        class_and_param_repo / "tests", test_outcomes=outcomes, project_root=class_and_param_repo
    )
    assert "NFR-22" not in result
