"""A test that catches its own AssertionError is a test that cannot fail (Round 53 站0).

`test_assertion_quality` exists to catch the one thing a green pytest run
cannot: a test function that passes because it asserts nothing. Its scanner,
`harness/lang_scanners/python_ast.py::run_assertions`, answers that with
`_function_has_assertion`, which walks the function body looking for an
`ast.Assert` (or an `assertXxx` call, or `pytest.raises`). It never asks
whether the assertion it found is allowed to fail.

Round 46 站1 settled the neighbouring case — a guard that skips itself is not
a guard that passed, "an absent witness is not a failed testimony". This is the
next one along: the witness took the stand and was gagged.

    try:
        insert_task(store, {...})
        task = get_task(store, "test-1")
        assert task is not None
        assert task["name"] == "test"
    except Exception:
        pass

That is `test_task_repo_insert_get`, verbatim, from taskq-super's committed
integration suite. It counted toward `asserted`, and taskq-super's Gate 4
recorded `test_assertion_quality: 100.0`.

The narrow spelling reached the tree through a commit that says so:
`54f9b93 test(p5): swallow transient assertions in nfr_phase6_gap gap tests`
wrapped four assertions in `try: assert …; except AssertionError: pass`. Two of
them are the only executable checks behind **NFR-03** (a failed migration
leaves the previous revision) and **NFR-04** (`assert secret not in line`, log
redaction — a security requirement). `TRACEABILITY_MATRIX.md` still records both
as `VERIFIED`.

Two dimensions look straight at this code and both pass it. `run_assertions`
counts the `assert` node; `_handler_anti_pattern`, in the same file, exempts it
by design — "Narrow-typed except-pass (e.g. ``except FileNotFoundError: pass``)
is deliberate and NOT flagged" — and `except AssertionError` is narrow-typed.

Station 0's control group over the seven projects on this machine refuted the
plan's own estimate of 4. The AST rule finds **24 in taskq-super and 2 in
taskq-api**, five projects clean; the estimate came from grepping the narrow
form, and the common shape is the broad `except Exception: pass` above. Each
of the 26 was opened and confirmed. taskq-api's two convert the failure into
`pytest.skip(...)`, which is Round 46's case wearing a disguise — the verdict
is still discarded.

The bucket is separate from `zero_assert` on purpose: these functions *do*
assert, so calling them assertionless would misname the defect for whoever has
to fix it. They leave `asserted`, which is all the existing
`_score_assertion_quality` (`100 × asserted / total`) needs to stop rewarding
them.
"""

from __future__ import annotations

import json
from pathlib import Path

_SWALLOWED_PY = '''
def test_swallowed_broad():
    """Every assertion sits under `except Exception: pass`."""
    try:
        value = compute()
        assert value == 1
    except Exception:
        pass


def test_swallowed_narrow():
    """The spelling taskq-super committed."""
    try:
        assert compute() == 1
    except AssertionError:
        pass


def test_swallowed_into_skip():
    """taskq-api's shape — the failure becomes a skip (Round 46)."""
    try:
        assert compute() == 1
    except Exception:
        pytest.skip("needs a real DB")


def test_diagnostic_then_reraise():
    """NOT neutralised: the handler adds context and re-raises."""
    try:
        assert compute() == 1
    except AssertionError:
        print("compute() returned", compute())
        raise


def test_plain():
    assert compute() == 1


def test_shell():
    pass
'''

_SWALLOWED_JS = """
it('swallowed', () => {
  try {
    expect(compute()).toBe(1);
  } catch (e) {}
});

it('rethrown', () => {
  try {
    expect(compute()).toBe(1);
  } catch (e) { throw e; }
});

it('plain', () => {
  expect(compute()).toBe(1);
});

it('shell', () => {
  compute();
});
"""


def test_a_swallowed_assertion_does_not_count_as_an_assertion(tmp_path: Path) -> None:
    """`asserted` may only count assertions whose failure ends the test."""
    from harness.lang_scanners.python_ast import run_assertions

    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_probe.py").write_text(_SWALLOWED_PY, encoding="utf-8")

    summary = json.loads(run_assertions(str(tmp_path))[0])

    assert summary["total"] == 6
    assert sorted(n.split("::")[-1] for n in summary["neutralised"]) == [
        "test_swallowed_broad",
        "test_swallowed_into_skip",
        "test_swallowed_narrow",
    ], "the three gagged tests must be named, and only those three"
    assert summary["asserted"] == 2, (
        "only test_diagnostic_then_reraise and test_plain can still fail"
    )
    assert [n.split("::")[-1] for n in summary["zero_assert"]] == ["test_shell"], (
        "a gagged assertion is not the same defect as a missing one — "
        "reporting it as zero_assert would misname it for whoever fixes it"
    )


def test_the_js_scanner_answers_the_same_question(tmp_path: Path) -> None:
    """One schema across languages, or the JS half becomes the cheap way out.

    `harness_bridge`'s content-validation registry already shares one pattern
    list between `ast-assertions` and `js-assertions` because the two emit the
    same object. A rule that exists on only one side is a rule with a
    documented bypass.
    """
    from harness.lang_scanners.treesitter_js import run_assertions

    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "probe.test.js").write_text(_SWALLOWED_JS, encoding="utf-8")

    summary = json.loads(run_assertions(str(tmp_path))[0])

    assert summary["total"] == 4
    assert [n.split("::")[-1] for n in summary["neutralised"]] == ["swallowed"]
    assert summary["asserted"] == 2
    assert [n.split("::")[-1] for n in summary["zero_assert"]] == ["shell"]
