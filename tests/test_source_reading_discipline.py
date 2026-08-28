"""`inspect.getsource` ratchet — reading the code can only decrease.

Round 78 站6. A test that asserts a string appears in
`inspect.getsource(some_function)` is checking how the code is SPELLED. Four
of them were written for Plan E and Plan F, and they are why Round 78 站1's
defect shipped green:

    assert "_audit_pragma_no_cover" in src
    assert src.count("PRAGMA_NO_COVER_ALLOWLIST") >= 2
    assert "_scope.is_phantom" in src
    assert "Plan F (Round 50+): early phantom module check" in src

The last one asserts a COMMENT. It stayed green through the entire period its
check was blocking all nine corpus projects, because the comment was there and
the behaviour was not. The second-to-last pins the only behaviour Plan F
changed and would survive any rewrite that kept the spelling. And the first
went RED on a rename that FIXED the code — a test that fires on the repair and
not on the break.

This repo has recorded the lesson twice already:

    tests/test_unscoreable_is_not_zero.py
      "A check that reads the code cannot see a change in what the code
       means."  — written after its own first draft was defeated exactly so.
    Round 64
      a guard was disabled by trimming the comment it read.

**When reading the source is legitimate.** Not all 47 remaining calls are the
defect above, and this is a ratchet rather than a ban because of that:

  * extracting a LIST the code owns and comparing it against another source
    of truth — `test_prompt_gate_parity.py` pulls the column names out of the
    prompt generator so the parser can be checked against them. The source is
    the data, not a proxy for behaviour.
  * asserting a string is ABSENT — that a removed mechanism has not come back
    (`test_unscoreable_is_not_zero.py`'s "Skip rather than falsely blocking
    the gate" check). Absence has no behavioural witness to call instead.
  * pinning a call-site ORDER or the existence of a call inside a function too
    expensive to drive — but read that off the AST (`ast.Call`, `ast.Name`),
    not the text. A comment cannot forge a call node. Round 78 站1 and 站5
    converted all four of the tests above to that form, which is why they
    stopped counting here.

**When it is not.** Using it as the only pin for a behaviour change that a
caller could have asserted instead. If the seam is too deep to call, make the
seam a function — that is what Round 78 站1 did with
`sab_amender.phantom_module_block` and Round 77 站2 with
`record_waived_test_failures`.

Counted on the AST, not the text: this module's own docstring names
`inspect.getsource` a dozen times, and so do several of the files it scans
now that they explain what they replaced. `tests/test_no_hardcoded_paths.py`
learned the same thing — "prose ABOUT the pattern read identically to the
pattern", and "a rule about code shape has to look at code shape".
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.core]

REPO = Path(__file__).resolve().parent.parent

# 2026-08-26 (Round 78 站6): 47, measured after 站1 and 站5 converted Plan E's
# and Plan F's four call-site tests to AST checks (51 before). DOWN ONLY. A
# 48th needs the ceiling raised in the same commit with the reason — and the
# reason has to be one of the three legitimate shapes in this module's
# docstring, not "the seam was awkward to call".
# 43 at Round 81 站6, from 47: the five `inspect.getsource(_advance_prechecks)`
# readers became `tests.support.pipeline.pipeline_source`, which reads the file
# rather than a cached module and — the reason they had to change at all —
# follows the extraction into the `_precheck_*` helpers. Four asked whether a
# constant or SSOT call appears in the precheck path; scoping them to the
# caller's own body after 站6 would have answered a question none of them meant.
_GETSOURCE_CEILING = 43


def _getsource_calls() -> "dict[str, int]":
    """`{relative path: count}` of real `*.getsource(...)` call nodes."""
    counts: dict[str, int] = {}
    for path in sorted(REPO.joinpath("tests").rglob("test_*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:  # pragma: no cover — not our files
            continue
        n = sum(
            1 for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "getsource"
        )
        if n:
            counts[path.relative_to(REPO).as_posix()] = n
    return counts


def test_source_reading_only_ratchets_down():
    counts = _getsource_calls()
    total = sum(counts.values())
    assert total <= _GETSOURCE_CEILING, (
        f"{total} inspect.getsource() call(s) in tests/ > ceiling "
        f"{_GETSOURCE_CEILING}. A test that reads how the code is SPELLED "
        f"cannot see a change in what it MEANS — that is how Plan F's phantom "
        f"audit shipped green while blocking all nine corpus projects, with "
        f"its call-site test asserting a comment. Assert on behaviour; if the "
        f"seam is too deep to call, make the seam a function. If the source "
        f"really is the data (a list the generator owns, or a string that "
        f"must be ABSENT), raise the ceiling in THIS commit and say which.\n"
        f"  per file: "
        + ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
    )


def test_the_ceiling_is_not_slack():
    """A ratchet with headroom authorises the growth it exists to surface —
    the defect Round 78 站3 found in the file-size table, which had 101 unused
    lines under a justification for a different number."""
    total = sum(_getsource_calls().values())
    assert total == _GETSOURCE_CEILING, (
        f"{total} calls against a ceiling of {_GETSOURCE_CEILING}: the debt "
        f"went down and the ceiling did not follow. Lower it in the same "
        f"commit that removed them.")


def test_the_scan_reads_call_nodes_not_prose():
    """Negative space. This module's own docstring says `inspect.getsource`
    a dozen times and several scanned files now explain what they replaced;
    a text scan would count every mention and the ratchet would be
    unsatisfiable. Same lesson as tests/test_no_hardcoded_paths.py."""
    prose_only = ast.parse(
        '"""A docstring about inspect.getsource(fn) and src.count(...)."""\n'
        '# inspect.getsource(other) in a comment\n'
        'MESSAGE = "call inspect.getsource(x) to see it"\n'
    )
    assert 0 == sum(
        1 for node in ast.walk(prose_only)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and node.func.attr == "getsource"
    )

    real = ast.parse("import inspect\nsrc = inspect.getsource(fn)\n")
    assert 1 == sum(
        1 for node in ast.walk(real)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and node.func.attr == "getsource"
    )
