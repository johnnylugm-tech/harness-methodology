"""PR 1: scanner unification tests.

Confirms that `core.traceability.scanner` produces identical output to the
prior in-script implementations for the same input. The scripts
(`scripts/build_traceability.py`, `scripts/check_spec_trace.py`) now
re-export from scanner; this file pins their behavior to the canonical
implementation.
"""
from pathlib import Path

import pytest


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
    assert set(view["sad_frs"]) == {"FR-01", "FR-02", "FR-03"}
    assert "FR-01" in view["fr_to_code"]
    assert "FR-01" in view["fr_to_tests"]
    assert "core/foo.py" in view["fr_to_modules"]["FR-01"]
    assert view["ghost_frs"] == []  # nothing in code/tests outside SAD
