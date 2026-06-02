"""Mutation testing regression tests for scanner.py.

Targeted tests for surviving mutants identified in the mutation
testing sprint. Each test kills a specific class of mutation:

  test_ghost_frs_is_list_not_none — kills mutants that make
    `report["ghost_frs"]` NoneType (e.g. mutating dict access to
    literal None). Without this test, a regression in `scan_all`
    that returns `{"ghost_frs": None}` would not be caught.

  test_srs_section_assigned_from_sad — kills mutants that invert
    the `fr_id in sad_frs` condition (Mutant 130: `not in`).
    The model gets `srs_section="SAD.md"` only when fr_id is in
    the SAD set; tests must assert both branches.

  test_complete_inverted_is_false — kills mutants that flip
    the `==` to `!=` in the `complete` field (Mutant 146). When
    both untested and uncoded are empty, complete must be True;
    when either is non-empty, complete must be False.

  test_all_frs_uses_union_not_intersection — kills mutants that
    change `set(a) | b` to `set(a) & b` (Mutant 84). Tests assert
    that an FR present in only one of the three sources is still
    included in all_frs.
"""
from pathlib import Path

import pytest


# Playbook §6: dynamic mutation-oracle marker
pytestmark = pytest.mark.mutation_oracle


@pytest.fixture
def fixture_repo(tmp_path: Path) -> Path:
    """Repo with one FR in SAD + code, one in code only, one ghost
    (in code but not SAD). Designed to exercise every branch in
    check_traceability."""
    arch = tmp_path / "02-architecture"
    arch.mkdir()
    (arch / "SAD.md").write_text("FR-01: alpha\n")
    (tmp_path / "core").mkdir()
    (tmp_path / "core" / "a.py").write_text('"""[FR-01] Foo."""\n')
    # FR-99: in code + test but NOT in SAD → "ghost_frs"
    (tmp_path / "core" / "b.py").write_text('"""[FR-99] Ghost."""\n')
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_a.py").write_text('"""[FR-01]"""\n')
    (tmp_path / "tests" / "test_b.py").write_text('"""[FR-99]"""\n')
    return tmp_path


def test_ghost_frs_is_list_not_none(fixture_repo):
    """Kills mutants that make `report["ghost_frs"]` NoneType.

    Mutant 108 mutated `scan["ghost_frs"]` to literal `None`. The
    scanner should always return a list, and the report wraps it.
    """
    sys_path = str(Path(__file__).resolve().parent.parent)
    if sys_path not in __import__("sys").path:
        __import__("sys").path.insert(0, sys_path)
    from core.traceability.scanner import check_traceability
    _rt, report = check_traceability(fixture_repo)
    # ghost_frs MUST be a list (not None, not dict, not str)
    assert isinstance(report["ghost_frs"], list), (
        f"ghost_frs must be a list, got {type(report['ghost_frs']).__name__}"
    )
    # FR-99 is in code/tests but not in SAD → must appear in ghost_frs
    assert "FR-99" in report["ghost_frs"]


def test_srs_section_assigned_from_sad(fixture_repo):
    """Kills mutants that invert `if fr_id in sad_frs` (Mutant 130).

    FR-01 is in SAD → srs_section="SAD.md".
    FR-99 is NOT in SAD → srs_section=None.
    """
    sys_path = str(Path(__file__).resolve().parent.parent)
    if sys_path not in __import__("sys").path:
        __import__("sys").path.insert(0, sys_path)
    from core.traceability.scanner import check_traceability
    rt, _report = check_traceability(fixture_repo)
    assert rt.requirements["FR-01"].srs_section == "SAD.md"
    assert rt.requirements["FR-99"].srs_section is None


def test_complete_inverted_is_false(fixture_repo):
    """Kills mutants that flip `==` to `!=` in the `complete` field.

    The fixture has FR-99 with no SAD entry → it's a ghost, but
    FR-01 has code+test. To make the `complete` field deterministically
    False, we need a fixture with an untested/uncoded FR.
    """
    sys_path = str(Path(__file__).resolve().parent.parent)
    if sys_path not in __import__("sys").path:
        __import__("sys").path.insert(0, sys_path)
    from core.traceability.scanner import check_traceability
    # Use a fresh repo where FR-01 has no test → not complete
    no_test_repo = fixture_repo
    (no_test_repo / "tests" / "test_a.py").unlink()
    _rt, report = check_traceability(no_test_repo)
    # untested has FR-01 → complete must be False
    assert "FR-01" in report["untested"]
    assert report["complete"] is False
    # And the inverse: when no gaps, complete is True
    full_repo = no_test_repo.parent
    arch = full_repo / "full"
    arch.mkdir()
    (arch / "SAD.md").write_text("FR-01: alpha\n")
    (full_repo / "core").mkdir()
    (full_repo / "core" / "a.py").write_text('"""[FR-01]"""\n')
    (full_repo / "tests").mkdir()
    (full_repo / "tests" / "test_a.py").write_text('"""[FR-01]"""\n')
    _rt2, report2 = check_traceability(full_repo)
    assert report2["complete"] is True


def test_all_frs_uses_union_not_intersection(fixture_repo):
    """Kills mutants that change `|` to `&` in all_frs (Mutant 84).

    FR-99 is in code but NOT in SAD. With union (|), all_frs
    includes FR-99. With intersection (&), all_frs would NOT
    include FR-99 (which is the wrong behaviour).
    """
    sys_path = str(Path(__file__).resolve().parent.parent)
    if sys_path not in __import__("sys").path:
        __import__("sys").path.insert(0, sys_path)
    from core.traceability.scanner import check_traceability
    _rt, _report = check_traceability(fixture_repo)
    # Both FR-01 (in SAD) and FR-99 (only in code) must be in all_frs
    # When we get the atomic dict via atomic_to_dict, the requirements
    # dict should have BOTH keys.
    from core.traceability.overlay import atomic_to_dict
    atomic = atomic_to_dict(_rt)
    assert "FR-01" in atomic["requirements"]
    assert "FR-99" in atomic["requirements"]


def test_scan_all_does_not_return_none_values(fixture_repo):
    """Regression: scan_all must return all required keys with non-None values.

    Kills mutants that make any field NoneType (e.g., mutating
    dict access to literal None). All 6 required keys must be
    present and non-None.
    """
    sys_path = str(Path(__file__).resolve().parent.parent)
    if sys_path not in __import__("sys").path:
        __import__("sys").path.insert(0, sys_path)
    from core.traceability.scanner import scan_all
    view = scan_all(fixture_repo)
    for key in ("sad_frs", "fr_to_code", "fr_to_tests",
                "fr_to_modules", "all_frs", "ghost_frs"):
        assert key in view, f"scan_all missing required key: {key}"
        assert view[key] is not None, (
            f"scan_all returned None for {key} (mutant regression)"
        )


def test_check_traceability_typed_bools_not_none(fixture_repo):
    """Kills mutants that make `has_code` / `has_test` NoneType.

    Mutant 119 mutated `has_test = fr_id in tested` to
    `has_test = None`. Subsequent `if has_code and has_test` would
    short-circuit on None. Without this test, the regression is
    silent.
    """
    sys_path = str(Path(__file__).resolve().parent.parent)
    if sys_path not in __import__("sys").path:
        __import__("sys").path.insert(0, sys_path)
    from core.traceability.scanner import check_traceability
    _rt, report = check_traceability(fixture_repo)
    # untested / uncoded must be lists (not None)
    assert isinstance(report["untested"], list)
    assert isinstance(report["uncoded"], list)
    # ghost_frs also a list
    assert isinstance(report["ghost_frs"], list)
