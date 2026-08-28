"""The extraction rule, and the cases it must refuse.

Round 81 站6-9. Everything the four mega-function extractions claim about their
own safety rests on `tests/support/dataflow.py` computing live-out correctly.
An analyser nobody checks is the same shape as the coverage number Round 80
reached for and the hunk counts Round 80 站6 argued from — a measurement trusted
because it was quoted, not because it was tested.

So this file is mostly negative controls: source that MUST be refused. If the
rule stops refusing them, the extractions built on it stop meaning anything.
"""

from __future__ import annotations

import ast
import textwrap

import pytest

from tests.support.dataflow import extractable_runs, live_out, rejection_reason

pytestmark = [pytest.mark.core]


def _func(source: str) -> ast.FunctionDef:
    tree = ast.parse(textwrap.dedent(source))
    node = tree.body[0]
    assert isinstance(node, ast.FunctionDef)
    return node


def _runs(source: str, **kw):
    return extractable_runs(_func(source), **kw)


# ── the rule refuses what it must ────────────────────────────────────────────

def test_a_binding_read_afterwards_is_refused():
    """The whole rule in one case: `total` escapes, so the block cannot go."""
    func = _func("""
        def f(items):
            total = 0
            for i in items:
                total += i
            print("counted")
            return total
    """)
    body = func.body
    assert live_out(body[:2], body[2:]) == {"total"}
    assert _runs("""
        def f(items):
            total = 0
            for i in items:
                total += i
            print("counted")
            return total
    """, min_lines=1) != [], "sanity: some run should still be found"

    leaking = [r for r in _runs("""
        def f(items):
            total = 0
            for i in items:
                total += i
            print("counted")
            return total
    """, min_lines=1) if r.start == 0 and r.stop >= 1]
    assert not leaking, (
        "a run that binds `total` and stops before the line reading it was "
        "accepted — the analyser is not computing live-out, and every "
        "extraction built on it is unproven"
    )


def test_a_return_of_none_is_refused():
    """`return None` is what the call site uses to mean "did not return".

    A run containing one could not be told apart from falling through, so the
    caller would return None from a function whose contract is an int.
    """
    segment = _func("""
        def f(x):
            if x:
                return None
            print("no")
    """).body
    assert rejection_reason(segment) is not None
    assert "return of None" in rejection_reason(segment)

    bare = _func("""
        def f(x):
            if x:
                return
            print("no")
    """).body
    assert rejection_reason(bare) is not None, (
        "a BARE return is the same ambiguity as `return None` and must be "
        "refused for the same reason"
    )


def test_nonlocal_is_refused():
    """A helper is not nested in its caller, so `nonlocal` cannot follow it."""
    segment = _func("""
        def f():
            nonlocal counter
            counter += 1
    """).body
    assert "nonlocal" in (rejection_reason(segment) or "")


def test_a_name_read_only_inside_a_nested_def_still_counts():
    """Conservative on purpose: `ast.walk` descends into nested scopes.

    Over-reporting a read costs extractable lines. Under-reporting one costs
    correctness, silently.
    """
    func = _func("""
        def f():
            token = compute()
            def later():
                return token
            return later
    """)
    assert live_out(func.body[:1], func.body[1:]) == {"token"}


def test_an_exception_alias_counts_as_a_binding():
    func = _func("""
        def f():
            try:
                go()
            except ValueError as exc:
                pass
            print(exc)
    """)
    assert live_out(func.body[:1], func.body[1:]) == {"exc"}


def test_a_comprehension_variable_is_not_bound_in_the_enclosing_function():
    """Found by ruff on the first generated call site, not by reading.

    `_bound` originally walked straight through comprehensions, so an `m` in an
    earlier statement's comprehension counted as "the caller has an `m`" and the
    extraction passed it as a parameter that does not exist — `F821 Undefined
    name 'm'`. A `for` target is deliberately still counted: `for` is not a
    scope and its target does leak.
    """
    from tests.support.dataflow import _bound

    comp = _func("""
        def f(rows):
            hits = [m for m in rows if m]
    """).body
    assert "m" not in _bound(comp), (
        "a comprehension variable was reported as bound in the enclosing "
        "function; extraction will pass it as a parameter that does not exist"
    )
    assert "hits" in _bound(comp)

    loop = _func("""
        def f(rows):
            for row in rows:
                pass
    """).body
    assert "row" in _bound(loop), (
        "a `for` target DOES leak into the function scope and must keep counting"
    )


def test_an_import_inside_the_run_counts_as_a_binding():
    func = _func("""
        def f():
            import json
            print("x")
            return json.dumps({})
    """)
    assert "json" in live_out(func.body[:1], func.body[1:])


# ── the rule accepts what it should ──────────────────────────────────────────

def test_a_self_contained_guard_block_is_accepted_with_its_inputs():
    """The shape the four functions are full of: check, raise or return, move on."""
    runs = _runs("""
        def f(ctx, raw):
            if not raw:
                print("empty")
                return 3
            if ctx.broken:
                print("broken")
                return 4
            result = compute(raw)
            return result
    """, min_lines=1)

    first = runs[0]
    # Both guard blocks merge into one run: neither binds anything read later,
    # and the partition is maximal. `result` is what stops it at the third
    # statement.
    assert (first.first_line, first.last_line) == (3, 8)
    assert first.returns is True
    assert first.inputs == frozenset({"ctx", "raw"}), (
        f"the helper's parameters are exactly the names it reads that were "
        f"already bound — both parameters are read here; got {set(first.inputs)}"
    )


def test_a_run_shorter_than_the_floor_is_not_offered():
    """Extracting three lines buys indirection and nothing else."""
    assert _runs("""
        def f(x):
            print(x)
            print(x)
    """, min_lines=8) == []


def test_the_partition_is_deterministic():
    """A guard has to be able to re-derive the same runs from the same source."""
    source = """
        def f(a, b):
            if a:
                print(1)
                print(2)
                print(3)
                print(4)
                print(5)
                print(6)
                print(7)
                return 1
            c = b + 1
            return c
    """
    assert [(r.first_line, r.last_line) for r in _runs(source, min_lines=1)] == \
           [(r.first_line, r.last_line) for r in _runs(source, min_lines=1)]
