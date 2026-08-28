"""Which parts of a long function can be extracted without threading state.

Round 81 站6-9. Round 80 declined to decompose this repo's four largest
functions and gave a reason that was about the wrong measurement:

    區塊抽取會改動函式自身文字,byte-equal 規則不適用;實測 harness_bridge.py
    全套件行為覆蓋 81%,不足以證明抽取等價

Coverage is not what decides whether an extraction is equivalent. Data flow is.

THE RULE

A contiguous run of statements may be extracted when **no name it binds is read
after it**. The call site is then fixed and mechanical:

    r = _helper(...)          # inputs: names it reads that were bound earlier
    if r is not None:         # an early return is propagated explicitly
        return r              # a raise propagates on its own

Under that condition the helper's entire effect on its caller is (a) the side
effects it performs itself — the same statements, in the same order, in the
same place — and (b) that one return value. Nothing is threaded, so nothing can
be threaded wrongly, which is the failure mode Round 80 was right to refuse.

WHAT THE RULE DOES NOT COVER, AND WHAT DOES

It does not prove the INPUT set was computed correctly. A missed input is a
`NameError` rather than a wrong answer — loud, not silent — but only on a path
something executes. So extraction is additionally gated on the run being
covered by the suite: see tests/test_extraction_moved_not_rewrote.py, which
also pins each extracted body by `ast.dump` against the pre-extraction source.
`ast.dump` rather than a source hash because extraction changes indentation and
nothing else; it is the byte-equality rule of Round 49-B, restated for a
transformation that must reindent.

CONSERVATIVE ON PURPOSE

`_bound` and `_loaded` walk nested scopes too, so a comprehension variable or a
name used inside a nested `def` counts. Both directions over-report, which
costs extractable lines and cannot cost correctness.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass

__all__ = ["Run", "extractable_runs", "live_out", "rejection_reason"]


@dataclass(frozen=True)
class Run:
    """A contiguous statement run that satisfies the rule above."""

    start: int          #: index of the first statement in the function body
    stop: int           #: index of the last statement, inclusive
    first_line: int
    last_line: int
    inputs: "frozenset[str]"
    returns: bool       #: whether an early return has to be propagated

    @property
    def lines(self) -> int:
        return self.last_line - self.first_line + 1


def _bound(nodes: "list[ast.stmt]") -> "set[str]":
    names: set[str] = set()
    for node in nodes:
        for child in ast.walk(node):
            if isinstance(child, ast.Name) and isinstance(child.ctx, (ast.Store, ast.Del)):
                names.add(child.id)
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(child.name)
            elif isinstance(child, (ast.Import, ast.ImportFrom)):
                for alias in child.names:
                    names.add((alias.asname or alias.name).split(".")[0])
            elif isinstance(child, ast.ExceptHandler) and child.name:
                names.add(child.name)
    return names


def _loaded(nodes: "list[ast.stmt]") -> "set[str]":
    return {
        child.id
        for node in nodes
        for child in ast.walk(node)
        if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Load)
    }


def live_out(segment: "list[ast.stmt]", after: "list[ast.stmt]") -> "set[str]":
    """Names the segment binds that something after it reads."""
    return _bound(segment) & _loaded(after)


def rejection_reason(segment: "list[ast.stmt]") -> "str | None":
    """Why this run may not be extracted even with an empty live-out set."""
    for node in segment:
        for child in ast.walk(node):
            if isinstance(child, ast.Return) and (
                child.value is None
                or (isinstance(child.value, ast.Constant) and child.value.value is None)
            ):
                # `return` and `return None` are what the call site uses to mean
                # "the helper did not return"; a run containing one could not be
                # told apart from falling through. None of the four functions
                # this was built for contains one — checked, not assumed — and
                # this rule is what stops the next edit introducing one quietly.
                return f"line {child.lineno}: a return of None cannot be propagated"
            if isinstance(child, (ast.Yield, ast.YieldFrom)):
                return f"line {child.lineno}: yield"
            if isinstance(child, ast.Nonlocal):
                # A helper is not nested in its caller, so `nonlocal` would bind
                # somewhere else or fail to compile. `global` is unaffected.
                return f"line {child.lineno}: nonlocal"
    return None


def _docstring_offset(body: "list[ast.stmt]") -> int:
    """1 when the function opens with a docstring, else 0.

    A run starting at index 0 would otherwise carry the docstring into the
    helper, which is not a move of behaviour and leaves the caller undocumented.
    """
    if body and isinstance(body[0], ast.Expr) and isinstance(
        getattr(body[0], "value", None), ast.Constant
    ) and isinstance(body[0].value.value, str):  # type: ignore[attr-defined]
        return 1
    return 0


def extractable_runs(
    func: "ast.FunctionDef | ast.AsyncFunctionDef",
    *,
    min_lines: int = 8,
    max_lines: "int | None" = None,
) -> "list[Run]":
    """Runs satisfying the rule, greedily from the top of the body.

    Greedy because a partition has to be chosen somehow and this one is
    deterministic: re-running it on unchanged source gives the same answer,
    which is what lets a guard re-derive it.

    `max_lines` cuts a run early at the last point that is still safe. Every
    prefix of a safe run is not automatically safe — it may bind something the
    rest of the run reads — so the cut point is searched for rather than
    computed, and a run with no safe cut under the cap is kept whole rather
    than dropped. Without this, `_advance_prechecks` offers one 318-line run,
    and a 318-line helper is not a decomposition.
    """
    body = list(func.body)
    params = {a.arg for a in func.args.args + func.args.kwonlyargs}
    runs: list[Run] = []
    i = _docstring_offset(body)
    while i < len(body):
        best: int | None = None
        best_capped: int | None = None
        j = i
        while j < len(body):
            segment = body[i:j + 1]
            if live_out(segment, body[j + 1:]) or rejection_reason(segment):
                break
            best = j
            end = segment[-1].end_lineno or segment[-1].lineno
            if max_lines is None or end - segment[0].lineno + 1 <= max_lines:
                best_capped = j
            j += 1
        if best_capped is not None:
            capped = body[i:best_capped + 1]
            end = capped[-1].end_lineno or capped[-1].lineno
            if end - capped[0].lineno + 1 >= min_lines:
                best = best_capped
        if best is None:
            i += 1
            continue
        segment = body[i:best + 1]
        last_line = segment[-1].end_lineno or segment[-1].lineno
        if last_line - segment[0].lineno + 1 >= min_lines:
            before = _bound(body[:i]) | params
            runs.append(Run(
                start=i,
                stop=best,
                first_line=segment[0].lineno,
                last_line=last_line,
                inputs=frozenset(_loaded(segment) & before),
                returns=any(isinstance(c, ast.Return)
                            for n in segment for c in ast.walk(n)),
            ))
        i = best + 1
    return runs
