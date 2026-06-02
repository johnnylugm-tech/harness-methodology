"""Mutation testing regression tests for auto_fix_propose.py.

Targeted tests for surviving mutants identified in the mutation
testing sprint on auto_fix_propose.py. Each test kills a specific
class of mutation that current tests miss:

  test_splitlines_returns_list_not_none — kills Mutant 87
    (`after_lines = None`). Without this test, mutating
    `after_text.splitlines(keepends=True)` to None would silently
    crash any subsequent line-end check.

  test_off_by_one_in_splitlines_index — kills Mutant 91
    (`[-2]` instead of `[-1]`). The last-line newline check must
    use `[-1]`, not `[-2]`.

  test_overlap_uses_strict_greater_than — kills Mutant 22
    (`>=` vs `>`). The closest_module tie-breaking must be strict
    greater so the FIRST encountered candidate is preferred.

  test_zero_overlap_is_rejected — kills Mutant 27 (`== 1` vs
    `== 0`). A module with zero token overlap must not be
    selected as the closest.

  test_double_negation_is_false — kills Mutant 11 (`if x:` vs
    `if not x:`). Empty-candidate path must return None, not
    the first candidate.
"""
from pathlib import Path

import pytest


# Playbook §6: dynamic mutation-oracle marker
pytestmark = pytest.mark.mutation_oracle


# ---------------------------------------------------------------------------
# _diff_append_to_existing: splitlines must return list, not None
# ---------------------------------------------------------------------------

def test_splitlines_returns_list_not_none():
    """Kills Mutant 87 (`after_lines = None`).

    The function calls `after_text.splitlines(keepends=True)` and
    later does `not after_lines[-1].endswith(...)`. If after_lines
    is None, the code crashes silently under tests that don't
    exercise the empty-input branch.
    """
    sys_path = str(Path(__file__).resolve().parent.parent)
    if sys_path not in __import__("sys").path:
        __import__("sys").path.insert(0, sys_path)
    from core.traceability.auto_fix_propose import _diff_append_to_existing
    # Pass a Path that doesn't exist → text is "". splitlines returns
    # []. The mutated code sets after_lines = None which would crash
    # on `not after_lines[-1].endswith(...)`.
    result = _diff_append_to_existing("/definitely/does/not/exist.py", "hello")
    # The original code returns a diff header + the appended text.
    # We don't assert exact content (that's covered elsewhere) — we
    # just need the call not to raise.
    assert isinstance(result, str)


# ---------------------------------------------------------------------------
# _diff_append_to_existing: off-by-one index
# ---------------------------------------------------------------------------

def test_off_by_one_in_splitlines_index(tmp_path):
    """Kills Mutant 91 (`[-2]` instead of `[-1]`).

    If the last line of an existing file has no trailing newline,
    the function must detect this and append one. The check is
    `before_lines[-1].endswith("\n")`. Using `[-2]` would miss
    the actual last line and append an extra newline to the
    second-to-last line.
    """
    sys_path = str(Path(__file__).resolve().parent.parent)
    if sys_path not in __import__("sys").path:
        __import__("sys").path.insert(0, sys_path)
    from core.traceability.auto_fix_propose import _diff_append_to_existing
    # File with NO trailing newline
    p = tmp_path / "no_nl.py"
    p.write_text("line1\nline2")  # last line has no \n
    diff = _diff_append_to_existing(str(p), "appended")
    # The diff should have @@ -1,2 +1,3 @@ (1 deleted + 2 added),
    # not @@ -1,3 +1,4 @@ (which would happen if [-1] were replaced
    # by [-2] — the function would treat "line2" as the second-to-last
    # line and add a newline to it).
    # The original code does:
    #   before_text = "line1\nline2"
    #   after_text  = "line1\nline2\nappended"  (auto-appends \n)
    #   before_lines = ["line1\n", "line2"]   (splitlines keepends)
    #   after_lines  = ["line1\n", "line2\n", "appended\n"]
    # The diff has 2 → 3 lines. The mutated code (with [-2]) would
    # think "line2" already has \n and add an extra \n to it, giving
    # "line1\nline2\n\nappended\n" (3 → 4 lines, with a blank line).
    assert "line2\n\nappended" not in diff, (
        f"off-by-one bug: extra newline added before appended text. "
        f"Diff:\n{diff}"
    )
    # The correct behavior: the appended line follows the original
    # last line, with exactly one newline.
    assert "line2\nappended" in diff or "+appended" in diff


# ---------------------------------------------------------------------------
# _closest_module: tie-breaking uses strict >
# ---------------------------------------------------------------------------

def test_overlap_uses_strict_greater_than():
    """Kills Mutant 22 (`>=` vs `>`). Strict greater means the FIRST
    candidate with the maximum overlap wins, not any of them.

    Note: the regex `[a-z_][a-z0-9_]+` treats `_` as part of a
    token (not a separator), so the section text must produce a
    token that EXACTLY matches the candidate's stem token to give
    non-zero overlap. We use two files with the SAME stem (in
    different subdirs) so both have overlap=1.
    """
    sys_path = str(Path(__file__).resolve().parent.parent)
    if sys_path not in __import__("sys").path:
        __import__("sys").path.insert(0, sys_path)
    from core.traceability.auto_fix_propose import _closest_module
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        (tmp / "core" / "sub1").mkdir(parents=True)
        (tmp / "core" / "sub2").mkdir(parents=True)
        # Both files have stem "fr01_a" — both produce tokens={"fr01_a"}
        # and overlap=1 with section text "fr01_a". A third file with
        # an unrelated stem has overlap=0.
        (tmp / "core" / "sub1" / "fr01_a.py").write_text("")
        (tmp / "core" / "sub2" / "fr01_a.py").write_text("")
        (tmp / "core" / "fr99.py").write_text("")
        result = _closest_module("FR-01", "fr01_a", tmp)
        # With strict >, the FIRST candidate encountered wins.
        # rglob is sorted (alphabetical), so sub1/fr01_a.py is visited
        # before sub2/fr01_a.py. The mutated code (>=) would UPDATE
        # best on the tie, returning sub2's file instead. We assert
        # on the parent dir to distinguish the two paths (both have
        # the same filename fr01_a.py).
        assert result is not None
        assert result.parent.name == "sub1", (
            f"strict > must keep the FIRST candidate; got {result}, "
            f"expected sub1/fr01_a.py"
        )


def test_zero_overlap_is_rejected():
    """Kills Mutant 27 (`== 1` vs `== 0`). Zero overlap → None."""
    sys_path = str(Path(__file__).resolve().parent.parent)
    if sys_path not in __import__("sys").path:
        __import__("sys").path.insert(0, sys_path)
    from core.traceability.auto_fix_propose import _closest_module
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        (tmp / "core").mkdir()
        (tmp / "core" / "fr99.py").write_text("")
        # FR-99 / "completely_different" / no token overlap
        result = _closest_module("FR-99", "completely_different_words", tmp)
        assert result is None, (
            f"zero-overlap module should be rejected, got {result}"
        )


def test_double_negation_is_false():
    """Kills Mutant 11 (`if x:` vs `if not x:`).

    When candidates list is empty, _closest_module must return None.
    If the condition is inverted (mutant removes the `not`), it
    would return `candidates[0]` from an empty list and crash.
    """
    sys_path = str(Path(__file__).resolve().parent.parent)
    if sys_path not in __import__("sys").path:
        __import__("sys").path.insert(0, sys_path)
    from core.traceability.auto_fix_propose import _closest_module
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        # No core/ directory → candidates list is empty
        result = _closest_module("FR-01", "fr01", tmp)
        assert result is None, (
            f"empty-candidate list must return None, got {result}"
        )
