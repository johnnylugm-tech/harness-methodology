"""Read an extracted pipeline as the single function it used to be.

Round 81 站6. Seven tests assert wiring properties of `_advance_prechecks` —
"it must call `_audit_pragma_no_cover` BEFORE coverage/lint/type run", "it must
pin the mypy exclude args", "the milestone gate applies only to phase 3" — by
walking that function's AST. The extraction moved those calls into
`_precheck_*` helpers, so the scan stopped finding them.

Narrowing the question to "is the call in `_advance_prechecks`'s own body" would
be answering something none of those tests meant. Widening it to "is the call
anywhere in the file" would lose the ORDER half, which several of them assert.

So this inlines: the caller's body with each `_precheck_*(...)` call site
replaced, in place, by that helper's statements. The result is the function
those tests were written against — same statements, same order — which is
exactly the claim the extraction makes about itself.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def pipeline_source(module: str, name: str, *, helper_prefix: str) -> str:
    """`name`'s source text followed by that of every helper it calls.

    The string-matching half of the same question. Four tests assert that a
    particular constant or SSOT call appears in `_advance_prechecks` —
    `PRAGMA_NO_COVER_GUIDANCE`, `run_suite`, `_MYPY_EXCLUDE_ARGS`,
    `_validate_p3_post_gate2_precondition` — and each is now one helper down.
    Read from the file rather than through `inspect.getsource`, which resolves
    against a cached read and has given stale answers in this repo before
    (tests/test_god_file_split_safety.py says so at its `_source_of`).
    """
    text = (REPO / module).read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    tree = ast.parse(text)
    target = _function(tree, name)

    def span(func: "ast.FunctionDef") -> str:
        return "".join(lines[func.lineno - 1:func.end_lineno])

    out = [span(target)]
    for node in ast.walk(target):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id.startswith(helper_prefix):
            out.append(span(_function(tree, node.func.id)))
    return "".join(out)


def _function(tree: ast.Module, name: str) -> "ast.FunctionDef":
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} is not defined in this module any more")


def inlined(module: str, name: str, *, helper_prefix: str) -> "ast.FunctionDef":
    """`name`'s body with every `helper_prefix*` call expanded where it sits.

    Only statement-level call sites are expanded — a bare `_helper(...)` and the
    `x = _helper(...)` whose result the next statement checks. That is the whole
    shape the extraction emits, and anything else is left alone rather than
    guessed at.
    """
    tree = ast.parse((REPO / module).read_text(encoding="utf-8"))
    target = _function(tree, name)

    def called_helper(stmt: ast.stmt) -> "str | None":
        call: "ast.Call | None" = None
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
            call = stmt.value
        elif isinstance(stmt, ast.Assign) and isinstance(stmt.value, ast.Call):
            call = stmt.value
        if call is None or not isinstance(call.func, ast.Name):
            return None
        return call.func.id if call.func.id.startswith(helper_prefix) else None

    body: "list[ast.stmt]" = []
    for stmt in target.body:
        helper = called_helper(stmt)
        if helper is None:
            body.append(stmt)
            continue
        body.extend(_function(tree, helper).body[1:])  # drop the docstring

    # Renumber. Several of these tests compare `lineno`s to assert ORDER, and
    # the helpers are defined ABOVE the caller, so the original numbers would
    # say a call moved into a helper happens before everything still in the
    # caller — true here by luck and not by construction. Sequential numbering
    # in execution order is what those comparisons actually mean.
    cursor = target.lineno
    for stmt in body:
        base = stmt.lineno
        span = (stmt.end_lineno or base) - base
        for node in ast.walk(stmt):
            if hasattr(node, "lineno"):
                node.lineno = cursor + (node.lineno - base)
            if getattr(node, "end_lineno", None) is not None:
                node.end_lineno = cursor + (node.end_lineno - base)
        cursor += span + 1

    return ast.FunctionDef(
        name=target.name,
        args=target.args,
        body=body,
        decorator_list=[],
        returns=target.returns,
        type_comment=None,
        lineno=target.lineno,
        col_offset=target.col_offset,
    )
