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
import importlib
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def _lookup(module: str, name: str) -> "tuple[ast.FunctionDef, str]":
    """`name`'s def and the text of the file that NOW defines it.

    Round 82 站1. Everything below used to parse `REPO / module` and look for
    the name in that one tree — the same-file assumption. Round 82 moves the
    helpers out, so eleven suites that reach them through this module would
    have stopped finding them.

    Resolution follows the object, not the path: import the façade module the
    caller names, resolve the attribute (re-exports keep working), then read
    the body out of `__module__`'s file at the position `__qualname__` gives.
    This is `tests/test_god_file_split_safety.py::_source_of`'s answer, not a
    new one — "locating them is the test's job, and the point is that the
    location is allowed to change".

    A name that cannot be resolved raises `AssertionError`, deliberately:
    `tests/test_push_path_symmetry.py` builds a synthetic module with no file
    on disk and falls back on `(FileNotFoundError, AssertionError)`. Letting a
    `ModuleNotFoundError` out would walk past that fallback and change what
    that negative control tests without touching it.
    """
    dotted = module.removesuffix(".py").replace("/", ".")
    try:
        facade = importlib.import_module(dotted)
    except ImportError as exc:
        raise AssertionError(f"{dotted} cannot be imported: {exc}") from exc

    obj = getattr(facade, name, None)
    if obj is None:
        # A method: reached through a class, not the module. More than one
        # class in the namespace can carry the name and that is not ambiguity
        # — `HarnessBridge` and the `_FinalizeStages` mixin it inherits from
        # both answer to `_stage_*` with the SAME function object. What would
        # be ambiguous is two DIFFERENT functions, so that is what is checked.
        found = {
            getattr(value, name)
            for value in vars(facade).values()
            if isinstance(value, type) and getattr(value, name, None) is not None
        }
        assert len(found) == 1, (
            f"{name} is not reachable from {dotted} as a module attribute, and "
            f"the classes defined there answer to it with {len(found)} "
            f"different objects — say which one is meant"
        )
        obj = found.pop()

    home = importlib.import_module(obj.__module__)
    home_file = getattr(home, "__file__", None)
    assert home_file, f"{obj.__module__} has no file on disk"
    src = Path(home_file).read_text(encoding="utf-8")

    qualname = getattr(obj, "__qualname__", name)
    found = _by_qualname(ast.parse(src), qualname)
    assert found is not None, (
        f"{name} is importable from {dotted} but its source is not at "
        f"{obj.__module__}::{qualname}"
    )
    return found, src


def _by_qualname(tree: ast.Module, qualname: str) -> "ast.FunctionDef | None":
    """The def whose dotted position in `tree` is `qualname`.

    Matching the position rather than the bare name is what keeps a method
    apart from a module-level function of the same name — and after 站6 the
    stages live one class deep in a file that also has module-level defs.
    """
    def walk(node: ast.AST, prefix: str) -> "ast.FunctionDef | None":
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                hit = walk(child, f"{prefix}{child.name}.")
                if hit is not None:
                    return hit
            elif isinstance(child, ast.FunctionDef):
                if f"{prefix}{child.name}" == qualname:
                    return child
        return None

    return walk(tree, "")


def moved_statements(func: "ast.FunctionDef") -> "list[ast.stmt]":
    """A helper's body minus what the extraction generated rather than moved.

    That is the docstring and, when present, the trailing explicit
    `return None` mypy requires of an `int | None` fall-through. The runs never
    contain one: the extraction rule refuses a run that returns None, because
    the call site uses exactly that to mean "the helper did not return".
    """
    body = list(func.body)
    first = body[0]
    if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str) and len(body) > 1):
        body = body[1:]
    last = body[-1] if body else None
    if isinstance(last, ast.Return) and isinstance(last.value, ast.Constant) \
            and last.value.value is None:
        body = body[:-1]
    return body


def reconstructed(module: str, name: str, *, helper_prefix: str,
                  generated_tail: bool = False) -> "list[ast.stmt]":
    """The caller's body with the extraction undone — helpers AND scaffolding.

    `inlined` puts helper bodies back where they run but leaves the two-line
    `if _rc is not None: return _rc` behind, because the tests that use it are
    asking what the pipeline does. This one removes that too, so what comes
    back should be the original function's body statement for statement — the
    complete equivalence claim, ORDER included, which neither the byte-identity
    check nor the data-flow rule covers on its own.

    `generated_tail` drops the caller's own `return 0` contract fall-through
    where the extraction added one, for the two functions whose terminal return
    travelled into their last helper.
    """
    target, _ = _lookup(module, name)

    def helper_of(stmt: ast.stmt) -> "str | None":
        call: "ast.Call | None" = None
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
            call = stmt.value
        elif isinstance(stmt, ast.Assign) and isinstance(stmt.value, ast.Call):
            call = stmt.value
        if call is None:
            return None
        func = call.func
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) \
                and func.value.id == "self":
            return func.attr if func.attr.startswith(helper_prefix) else None
        if isinstance(func, ast.Name):
            return func.id if func.id.startswith(helper_prefix) else None
        return None

    body: "list[ast.stmt]" = []
    skip_next_propagate = False
    for stmt in target.body:
        if skip_next_propagate:
            skip_next_propagate = False
            if isinstance(stmt, ast.If) and all(
                isinstance(n, ast.Return) for n in stmt.body
            ):
                continue
        helper = helper_of(stmt)
        if helper is None:
            body.append(stmt)
            continue
        body.extend(moved_statements(_lookup(module, helper)[0]))
        skip_next_propagate = isinstance(stmt, ast.Assign)

    # A `Raise` as well as a `Return`: finalize_gate's generated fall-through
    # is `raise GateBlockedError(...)`, because fail-closed is the right default
    # for a gate where `return 0` is the right one for a CLI command.
    if generated_tail and body and isinstance(body[-1], (ast.Return, ast.Raise)):
        body = body[:-1]
    return body


def original_statements(before_file: str, name: str) -> "list[ast.stmt]":
    """The function's body as it stood before the extraction."""
    path = REPO / "tests" / "golden" / "extraction" / before_file
    return list(_function(ast.parse(path.read_text(encoding="utf-8")), name).body)


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
    def span(pair: "tuple[ast.FunctionDef, str]") -> str:
        func, text = pair
        lines = text.splitlines(keepends=True)
        return "".join(lines[func.lineno - 1:func.end_lineno])

    target, _ = (found := _lookup(module, name))
    out = [span(found)]
    seen: "set[str]" = set()
    for node in ast.walk(target):
        if not isinstance(node, ast.Call):
            continue
        # `helper(...)` and `self.helper(...)` both. `reconstructed` has read
        # the method shape since Round 81 站8 — `finalize_gate`'s stages are
        # methods — and this reader did not, so a question asked of the
        # finalize pipeline saw only the caller's own body.
        func = node.func
        called = (func.attr if isinstance(func, ast.Attribute)
                  and isinstance(func.value, ast.Name) and func.value.id == "self"
                  else getattr(func, "id", None) if isinstance(func, ast.Name)
                  else None)
        if called and called.startswith(helper_prefix) and called not in seen:
            seen.add(called)
            out.append(span(_lookup(module, called)))
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
    target, _ = _lookup(module, name)

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
        # drop the docstring
        body.extend(_lookup(module, helper)[0].body[1:])

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
