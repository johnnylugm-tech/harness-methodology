#!/usr/bin/env python3
"""RED test self-consistency engine.

Decides whether a TEST_SPEC.md case set is *provably unsatisfiable* — i.e. no
implementation could ever make the declared sub-assertions all hold — using two
decidable sub-checkers. This is the shared engine behind the P2 self-consistency
gate (correctness is locked in TEST_SPEC.md) and is reused, in structure-only
form, by the P3 mirror gate.

Design contract (see docs/proposals / plan):
  * The engine NEVER reads any requirements source (SRS.md / SAD.md / SPEC.md)
    and NEVER hard-codes any application rule (Bopomofo, splitter semantics…).
    It only evaluates the *predicates the author wrote in TEST_SPEC.md* against
    the *concrete inputs declared in the same TEST_SPEC.md case*, plus generic
    length/count arithmetic. Hence it is project- and FR-agnostic.

  * Decider A (predicate evaluation): a sub-assertion whose free variables are
    all concrete case inputs is evaluated under a hardened AST whitelist.
    `" " in "ㄏㄢˋ"` -> False  ==>  TEST_SPEC self-inconsistent (Case A).

  * Decider B (length/count linear consistency): sub-assertions that reference a
    production output symbol (e.g. `result`) are matched against a small set of
    length identities. `len(result)==4` ∧ `all(len(c)==1 …)` ∧
    `"".join(result)==text_input` with `len(text_input)==5` -> 4≠5 (Case B).

  * Anything else -> `needs_review` (info severity): handed to the P2 human
    reviewer; the engine does not guess.
"""

from __future__ import annotations

import ast
import re
from collections import defaultdict
from dataclasses import dataclass, field

from core.quality_gate import Violation

__all__ = [
    "SpecCase",
    "SubAssertion",
    "UnsafePredicateError",
    "check_test_spec_consistency",
    "check_test_mirrors_spec",
    "check_test_mirrors_spec_js",
]


# ─────────────────────────────────────────────────────────────────────────────
# Input models (built directly in tests; produced by the parser in milestone 2)
# ─────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class SpecCase:
    """A single parametrize case declared in TEST_SPEC.md.

    `inputs` maps variable name -> concrete declared value, e.g.
    {"source": "和", "expected": "ㄏㄢˋ"} or {"text_input": "。？！!?"}.
    """

    case_id: int
    inputs: dict[str, str]


@dataclass(frozen=True)
class SubAssertion:
    """A sub-assertion rule and the case ids it is declared to apply to."""

    rule_id: str
    predicate: str
    applies_to: list[int] = field(default_factory=list)


class UnsafePredicateError(ValueError):
    """Raised when a predicate contains a construct outside the AST whitelist."""


# ─────────────────────────────────────────────────────────────────────────────
# Hardened predicate evaluation (Decider A)
# ─────────────────────────────────────────────────────────────────────────────
_ALLOWED_NODE_TYPES: tuple = (
    ast.Expression,
    ast.BoolOp, ast.And, ast.Or,
    ast.UnaryOp, ast.Not, ast.USub, ast.UAdd,
    ast.BinOp, ast.Add, ast.Sub, ast.Mult, ast.Mod, ast.FloorDiv,
    ast.Compare, ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE,
    ast.In, ast.NotIn,
    # ast.Call and ast.Attribute are handled by explicit isinstance checks before
    # the catch-all; kept out of this tuple to avoid giving the false impression
    # that they are whitelisted without validation.
    ast.Name, ast.Load, ast.Constant,
    ast.List, ast.Tuple, ast.Set, ast.Subscript,
    ast.GeneratorExp, ast.ListComp, ast.SetComp, ast.comprehension,
)

# Builtins exposed to predicates (also the eval namespace — __builtins__ is empty).
_ALLOWED_BUILTINS = {
    "len": len, "all": all, "any": any, "sorted": sorted,
    "set": set, "abs": abs, "min": min, "max": max, "sum": sum,
}

# String/sequence methods permitted on values.
_ALLOWED_METHODS = frozenset({
    "split", "rsplit", "count", "startswith", "endswith",
    "strip", "lstrip", "rstrip", "join", "lower", "upper",
    "replace", "find", "rfind", "index",
})


def _validate_predicate_ast(tree: ast.AST) -> None:
    """Raise UnsafePredicateError unless every node is on the whitelist."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                if func.id not in _ALLOWED_BUILTINS:
                    raise UnsafePredicateError(f"call to {func.id!r} not allowed")
            elif isinstance(func, ast.Attribute):
                if func.attr not in _ALLOWED_METHODS:
                    raise UnsafePredicateError(f"method .{func.attr}() not allowed")
            else:
                raise UnsafePredicateError("dynamic call target not allowed")
        elif isinstance(node, ast.Attribute):
            # Blocks dunder access (e.g. expected.__class__) and any non-method attr.
            if node.attr not in _ALLOWED_METHODS:
                raise UnsafePredicateError(f"attribute .{node.attr} not allowed")
        elif not isinstance(node, _ALLOWED_NODE_TYPES):
            raise UnsafePredicateError(f"{type(node).__name__} not allowed in predicate")


def _safe_eval_predicate(predicate: str, namespace: dict):
    """Evaluate a predicate string under the hardened whitelist."""
    tree = ast.parse(predicate, mode="eval")
    _validate_predicate_ast(tree)
    code = compile(tree, "<predicate>", "eval")
    return eval(code, {"__builtins__": {}}, {**_ALLOWED_BUILTINS, **namespace})  # noqa: S307


def _free_variables(predicate: str) -> set:
    """Return Load-context names that are not builtins or comprehension targets."""
    tree = ast.parse(predicate, mode="eval")
    bound: set = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.comprehension):
            for t in ast.walk(node.target):
                if isinstance(t, ast.Name):
                    bound.add(t.id)
    names: set = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            names.add(node.id)
    return names - bound - set(_ALLOWED_BUILTINS)


# ─────────────────────────────────────────────────────────────────────────────
# Length / count linear consistency (Decider B)
# ─────────────────────────────────────────────────────────────────────────────
def _as_len_call(node: ast.AST) -> str | None:
    """If node is `len(NAME)`, return NAME; else None."""
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == "len" and len(node.args) == 1
            and isinstance(node.args[0], ast.Name)):
        return node.args[0].id
    return None


def _const_int(node: ast.AST) -> int | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, int):
        return node.value
    return None


def _join_target(node: ast.AST) -> str | None:
    """If node is `"".join(NAME)`, return NAME; else None."""
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "join" and len(node.args) == 1
            and isinstance(node.args[0], ast.Name)):
        return node.args[0].id
    return None


def _parse_length_fact(predicate: str) -> tuple | None:
    """Recognise the three supported length identities, else None.

      ("card", X, N)  for  len(X) == N
      ("elem", X, M)  for  all(len(c) == M for c in X)
      ("join", X, Y)  for  "".join(X) == Y   (or the symmetric form)
    """
    try:
        tree = ast.parse(predicate, mode="eval").body
    except SyntaxError:
        return None

    # all(len(c) == M for c in X)
    if (isinstance(tree, ast.Call) and isinstance(tree.func, ast.Name)
            and tree.func.id == "all" and len(tree.args) == 1
            and isinstance(tree.args[0], ast.GeneratorExp)):
        gen = tree.args[0]
        if len(gen.generators) == 1 and isinstance(gen.generators[0].iter, ast.Name):
            iter_name = gen.generators[0].iter.id
            cmp = gen.elt
            if (isinstance(cmp, ast.Compare) and len(cmp.ops) == 1
                    and isinstance(cmp.ops[0], ast.Eq)):
                inner = _as_len_call(cmp.left)
                m = _const_int(cmp.comparators[0])
                if inner is not None and m is not None:
                    return ("elem", iter_name, m)
        return None

    if not (isinstance(tree, ast.Compare) and len(tree.ops) == 1
            and isinstance(tree.ops[0], ast.Eq)):
        return None
    left, right = tree.left, tree.comparators[0]

    # len(X) == N   (either orientation)
    for a_node, b_node in ((left, right), (right, left)):
        x = _as_len_call(a_node)
        n = _const_int(b_node)
        if x is not None and n is not None:
            return ("card", x, n)

    # "".join(X) == Y   (either orientation)
    for a_node, b_node in ((left, right), (right, left)):
        x = _join_target(a_node)
        if x is not None and isinstance(b_node, ast.Name):
            return ("join", x, b_node.id)

    return None


def _check_length_consistency(case_id: int, predicates: list, inputs: dict) -> list:
    """Detect contradictory total-length constraints for a single case."""
    # cardinality accumulates all len(sym)==N values (list, not dict) so that
    # contradictory declarations like len(X)==4 and len(X)==5 are detected (C1).
    cardinality: dict[str, list[int]] = defaultdict(list)
    elem_len: dict[str, int] = {}
    join_eq: dict[str, str] = {}
    unresolved: list = []

    for pred in predicates:
        fact = _parse_length_fact(pred)
        if fact is None:
            unresolved.append(pred)
            continue
        kind, sym, val = fact
        if kind == "card":
            cardinality[sym].append(val)
        elif kind == "elem":
            elem_len[sym] = val
        elif kind == "join":
            join_eq[sym] = val

    violations: list = []
    for sym in set(cardinality) | set(elem_len) | set(join_eq):
        card_vals = cardinality.get(sym, [])

        # Direct contradiction: two different len(sym)==N predicates.
        if len(set(card_vals)) >= 2:
            violations.append(Violation(
                check_type="length_contradiction",
                rule_id=f"case{case_id}-length",
                severity="error",
                message=(
                    f"case {case_id}: contradictory len({sym}) declarations "
                    f"{sorted(set(card_vals))} — TEST_SPEC self-inconsistent"),
                extra={"case_id": case_id, "symbol": sym,
                       "totals": {f"len({sym})=={v}": v for v in sorted(set(card_vals))}},
            ))
            continue

        totals: dict = {}
        card = card_vals[0] if card_vals else None
        if card is not None and sym in elem_len:
            totals[f"len({sym})*elem"] = card * elem_len[sym]
        if sym in join_eq:
            rhs = join_eq[sym]
            if rhs in inputs:
                totals[f'len("".join)==len({rhs})'] = len(str(inputs[rhs]))
        if len(set(totals.values())) >= 2:
            violations.append(Violation(
                check_type="length_contradiction",
                rule_id=f"case{case_id}-length",
                severity="error",
                message=(
                    f"case {case_id}: contradictory total length for {sym!r}: "
                    f"{totals} — no implementation can satisfy all sub-assertions "
                    f"(TEST_SPEC self-inconsistent)"),
                extra={"case_id": case_id, "symbol": sym, "totals": totals},
            ))

    for pred in unresolved:
        violations.append(Violation(
            check_type="needs_review",
            rule_id=f"case{case_id}-review",
            severity="info",
            message=(
                f"case {case_id}: predicate {pred!r} references a production "
                f"output but is not a recognised length/count pattern — "
                f"needs P2 Agent B review"),
            extra={"case_id": case_id, "predicate": pred},
        ))
    return violations


# ─────────────────────────────────────────────────────────────────────────────
# Top-level: route each sub-assertion to Decider A or B
# ─────────────────────────────────────────────────────────────────────────────
def check_test_spec_consistency(
    cases: list, assertions: list
) -> list:
    """Return Violations proving TEST_SPEC is self-inconsistent (or needs review).

    error severity == provably unsatisfiable -> gate FAIL.
    info  severity == needs_review          -> surfaced, non-blocking.
    """
    violations: list = []
    cases_by_id = {c.case_id: c for c in cases}
    b_bucket: dict = defaultdict(list)

    for a in assertions:
        try:
            free = _free_variables(a.predicate)
        except SyntaxError:
            violations.append(Violation(
                check_type="malformed_predicate", rule_id=a.rule_id, severity="error",
                message=f"sub-assertion {a.rule_id!r} predicate {a.predicate!r} is not parseable"))
            continue

        for cid in a.applies_to:
            case = cases_by_id.get(cid)
            if case is None:
                violations.append(Violation(
                    check_type="unknown_case", rule_id=a.rule_id, severity="error",
                    message=f"sub-assertion {a.rule_id!r} applies_to case {cid} which is not declared"))
                continue

            if free <= set(case.inputs):
                # Decider A — evaluate against concrete inputs.
                try:
                    ok = _safe_eval_predicate(a.predicate, dict(case.inputs))
                except UnsafePredicateError as exc:
                    violations.append(Violation(
                        check_type="unsafe_predicate", rule_id=a.rule_id, severity="error",
                        message=f"sub-assertion {a.rule_id!r} predicate {a.predicate!r} rejected: {exc}"))
                    continue
                except Exception as exc:
                    violations.append(Violation(
                        check_type="malformed_predicate", rule_id=a.rule_id, severity="error",
                        message=f"sub-assertion {a.rule_id!r} predicate {a.predicate!r} evaluation failed: {exc.__class__.__name__}: {exc}"))
                    continue
                if not bool(ok):
                    violations.append(Violation(
                        check_type="predicate_false", rule_id=a.rule_id, severity="error",
                        message=(
                            f"sub-assertion {a.rule_id!r} predicate {a.predicate!r} is False "
                            f"for case {cid} (inputs={case.inputs}) — TEST_SPEC self-inconsistent"),
                        extra={"case_id": cid, "predicate": a.predicate}))
            else:
                # References a production-output symbol -> Decider B.
                b_bucket[cid].append(a.predicate)

    for cid, preds in b_bucket.items():
        violations.extend(_check_length_consistency(cid, preds, cases_by_id[cid].inputs))

    return violations


# ─────────────────────────────────────────────────────────────────────────────
# P3 mirror gate — verify a test file faithfully implements TEST_SPEC.
# Pure structural comparison: no satisfiability, no eval of the test's own
# logic. TEST_SPEC was already locked self-consistent in P2; P3 only proves the
# test mirrors it (so P3 "only implements", it does not re-decide correctness).
# Only simple triggers (`VAR == c` / `VAR in (…)`) are compared; richer triggers
# (e.g. `.startswith`) are skipped — they carry behavioural assertions, not the
# declared sub-assertion grouping.
# ─────────────────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class _SubUse:
    var: str
    trigger: frozenset
    asserts: frozenset  # normalised predicate strings asserted under the trigger


def _normalize_predicate(text: str) -> str | None:
    try:
        return ast.unparse(ast.parse(text.strip(), mode="eval").body)
    except SyntaxError:
        return None


def _canonical_predicate(text: str) -> str | None:
    """Canonical form of an expression for set-membership comparison (Bug #27 fix).

    `_normalize_predicate` returns `ast.unparse(...)` which preserves all
    syntax differences (whitespace, redundant parens, operator associativity).
    For predicate comparison, two semantically equivalent expressions can
    produce different normalized forms:
      - `1+2`     vs `1 + 2`     vs `(1+2)`         (all ≡ 3)
      - `not a`   vs `(not a)`                     (both ≡ ¬a)
      - `a > 0`   vs `a>0`                         (same)

    Bug Fix R6 (2026-07-15): TEST_SPEC predicates use STRING literals
    (e.g. `failure_count == "3"`) while Python tests use native literals
    (e.g. `failure_count == 3`). `ast.unparse(ast.Constant("3"))` returns
    `"'3'"` (quoted) but `ast.unparse(ast.Constant(3))` returns `"3"`
    (unquoted) — the canonical forms differ, causing substring match to
    fail even when the predicates are semantically equivalent.

    Fix: normalise every `ast.Constant` to its str() value BEFORE
    `ast.unparse` (str is the canonical common type — Python source form
    of `"3"` and source form of `3` both unparse to `"'3'"` once values
    are coerced to str). Concretely:
      - `ast.Constant(3)`  → `ast.Constant("3")` → unparse → `"'3'"`
      - `ast.Constant("3")` → unchanged          → unparse → `"'3'"`
    Both sides now produce `failure_count == '3'` after canonicalisation.

    Returns None on syntax error (callers should fall back to
    `_normalize_predicate` or skip).
    """
    try:
        tree = ast.parse(text.strip(), mode="eval").body
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and not isinstance(node.value, str):
                # Coerce non-string literals to their str() form so both sides
                # of the comparison produce the SAME canonical string. This is
                # safe because the comparison is symbolic (sub-string match),
                # not semantic — we only need both sides to render the same.
                node.value = str(node.value)
        return ast.unparse(tree)
    except SyntaxError:
        return None


def _as_str(value) -> str:
    return value if isinstance(value, str) else repr(value)


def _literal_value(node: ast.AST):
    """Concrete value for a parametrize arg / trigger constant.

    Constant -> its value; a constants-only expression (e.g. "x"*11) -> safe
    eval; otherwise (None, False). NameError is caught alongside the other
    eval failures because a parametrize arg may reference a module-level
    constant this AST-only checker was never meant to resolve (e.g.
    `_BATCH = ";".join(...)`; `@parametrize("x", [_BATCH])`) — that must
    degrade to "can't verify this row", not crash the whole MIRROR check."""
    if isinstance(node, ast.Constant):
        return node.value, True
    try:
        return _safe_eval_predicate(ast.unparse(node), {}), True
    except (SyntaxError, TypeError, ValueError, ArithmeticError, MemoryError, NameError):
        return None, False


def _param_row_values(elt: ast.expr, n: int):
    if isinstance(elt, ast.Call) and (
        (isinstance(elt.func, ast.Attribute) and elt.func.attr == "param")
        or (isinstance(elt.func, ast.Name) and elt.func.id == "param")
    ):
        arg_nodes = elt.args
    elif isinstance(elt, (ast.Tuple, ast.List)):
        arg_nodes = elt.elts
    else:
        arg_nodes = [elt]  # single-value parametrize
    vals = []
    for a in arg_nodes[:n]:
        v, ok = _literal_value(a)
        if not ok:
            return None
        vals.append(v)
    return tuple(vals) if len(vals) == n else None


def _extract_parametrize(tree: ast.AST, fr_id: str | None = None):
    """Return [(var_names, rows), ...] — one entry per distinct variable-name
    signature found across all @pytest.mark.parametrize decorators (C2 fix:
    multi-function test files; Bug-E fix: multiple distinct signatures no
    longer silently drop each other's rows — a TEST_SPEC case set is free to
    declare cases with different "first input" shapes, e.g. `command` vs
    `command_batch` vs `task_id`, and every shape must be checked).

    When `fr_id` is given (e.g. "FR-05"), only functions named
    `test_fr{NN}_*` are considered — the project's canonical-file convention
    puts Cross-Cutting/NFR/Deployment-smoke tests for OTHER TEST_SPEC
    sections in the same file, and those rows are out of scope for this
    FR's own case-table alignment check.

    Iterates function definitions in AST order; for each function processes only
    its decorator_list (not the body) to avoid false matches.

    Supports both an inline args list and a module-level list of pytest.param/tuples.
    """
    fr_prefix: str | None = None
    if fr_id:
        m = re.match(r"N?FR-(\d+)", fr_id)
        if m:
            fr_prefix = f"test_fr{m.group(1).zfill(2)}_"

    list_vars: dict[str, ast.expr] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, (ast.List, ast.Tuple)):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    list_vars[t.id] = node.value

    groups: dict[tuple, list] = {}

    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if fr_prefix and not fn.name.startswith(fr_prefix):
            continue
        for dec in fn.decorator_list:
            if not (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)
                    and dec.func.attr == "parametrize" and len(dec.args) >= 2):
                continue
            var_node, args_node = dec.args[0], dec.args[1]
            if not (isinstance(var_node, ast.Constant) and isinstance(var_node.value, str)):
                continue
            var_names = tuple(v.strip() for v in var_node.value.split(","))
            args_seq: ast.expr | None = args_node
            if isinstance(args_node, ast.Name):
                args_seq = list_vars.get(args_node.id)
            if not isinstance(args_seq, (ast.List, ast.Tuple)):
                continue
            rows = groups.setdefault(var_names, [])
            for elt in args_seq.elts:
                values = _param_row_values(elt, len(var_names))
                if values is not None:
                    rows.append(values)

    return [(list(var_names), rows) for var_names, rows in groups.items()]


# Sentinel returned by _parse_trigger when a Compare node with an ast.Name
# on either side + a single comparison operator cannot be fully parsed but
# is structurally recognizable as a trigger attempt. _collect_ifs collects
# assertions inside the block but marks them with an empty trigger set so
# the MIRROR check surfaces a trigger_mismatch violation instead of silently
# dropping the assertions.
_UNHANDLED_TRIGGER = object()


def _parse_trigger(test_node: ast.AST):
    """Return (var, {values}) for trigger patterns, else None or sentinel.

    Handled forms (var on either side of the operator):
      - var == literal  /  literal == var       (Eq)
      - var != literal  /  literal != var       (NotEq)
      - var in (lit, ...)  /  "lit" in var      (In, collection or scalar)
      - var not in (lit, ...)                   (NotIn, collection)

    Returns ``_UNHANDLED_TRIGGER`` (sentinel) for structured Compare nodes
    with exactly 1 op + an ast.Name on either side that we cannot fully
    parse (e.g. ``var < literal``). Callers collect the assertions but mark
    the trigger set as empty — the assertion is visible to the MIRROR check
    rather than silently dropped.

    Returns ``None`` for non-Compare nodes, multi-op conditions, or nodes
    with no ast.Name on either side — those are not trigger patterns at all.
    """
    if not (isinstance(test_node, ast.Compare) and len(test_node.ops) == 1):
        return None

    left = test_node.left
    op = test_node.ops[0]
    comp = test_node.comparators[0]

    # Determine which side has the ast.Name (the "variable" side).
    var: str | None = None
    value_expr: ast.AST | None = None

    if isinstance(left, ast.Name):
        var = left.id
        value_expr = comp
    elif isinstance(comp, ast.Name):
        var = comp.id
        value_expr = left

    if var is None or value_expr is None:
        return None  # no Name on either side — not a trigger pattern

    # --- Handled cases (return (var, {values})) ---

    # Eq: var == literal  /  literal == var
    if isinstance(op, ast.Eq):
        v, ok = _literal_value(value_expr)
        return (var, {v}) if ok else None

    # NotEq: var != literal  /  literal != var
    if isinstance(op, ast.NotEq):
        v, ok = _literal_value(value_expr)
        return (var, {v}) if ok else None

    # In with collection literal: var in (a, b, c)  /  ("a","b") in var
    if isinstance(op, ast.In) and isinstance(value_expr, (ast.Tuple, ast.List, ast.Set)):
        vals: set = set()
        for e in value_expr.elts:
            v, ok = _literal_value(e)
            if not ok:
                return None
            vals.add(v)
        return (var, vals)

    # NotIn with collection literal: var not in (a, b, c)
    if isinstance(op, ast.NotIn) and isinstance(value_expr, (ast.Tuple, ast.List, ast.Set)):
        vals = set()
        for e in value_expr.elts:
            v, ok = _literal_value(e)
            if not ok:
                return None
            vals.add(v)
        return (var, vals)

    # In with scalar: "constant" in var  /  var in "constant"
    if isinstance(op, ast.In):
        v, ok = _literal_value(value_expr)
        return (var, {v}) if ok else None

    # --- Unhandled but structured: return sentinel ---
    # Covers Lt, LtE, Gt, GtE, Is, IsNot, NotIn with non-collection right,
    # and any other comparison operator we haven't explicitly handled.
    # The caller collects assertions but marks the trigger set as empty.
    return _UNHANDLED_TRIGGER


def _collect_ifs(stmts: list, uses: list) -> None:
    for st in stmts:
        if isinstance(st, ast.If):
            trig = _parse_trigger(st.test)
            if trig is _UNHANDLED_TRIGGER:
                # Structured trigger we cannot fully parse (e.g. var < 5).
                # Collect the assertions so they are visible to the MIRROR
                # check, but use an empty trigger set — the comparison at
                # line 648 (test_trigger != spec_trigger) will produce a
                # trigger_mismatch violation instead of a silent drop.
                _var = _trigger_var_name(st.test)
                if _var is not None:
                    _asserts = frozenset(
                        p for bs in st.body for s in ast.walk(bs)
                        if isinstance(s, ast.Assert)
                        for p in [_canonical_predicate(ast.unparse(s.test))] if p)
                    uses.append(_SubUse(_var, frozenset(), _asserts))
            elif trig is not None:
                var, values = trig
                # Bug #27 fix: use _canonical_predicate for both sides so
                # semantically equivalent predicates (different whitespace,
                # redundant parens) match. Falls back to None for invalid
                # syntax — the assertion is then ignored.
                asserts = frozenset(
                    p for bs in st.body for s in ast.walk(bs)
                    if isinstance(s, ast.Assert)
                    for p in [_canonical_predicate(ast.unparse(s.test))] if p)
                uses.append(_SubUse(var, frozenset(values), asserts))
            _collect_ifs(st.orelse, uses)  # elif chain


def _collect_bare_asserts(stmts: list, uses: list) -> None:
    """Bug Fix R5 (2026-07-15): collect assertions OUTSIDE any if-trigger.

    The MIRROR gate historically required every assertion to live under an
    `if`-trigger block (the canonical harness TDD shape). But Python tests
    written in a more direct style (`assert x == 3` at the top of a test
    function) are equally valid TDD and are commonly written by hand before
    GREEN arrives. The previous behaviour silently dropped those assertions,
    returning zero sub-uses for the FR — every spec sub-assertion then fired
    `assertion_missing`, BLOCKING the gate even though the test was correct.

    Fix: walk the test function body for `ast.Assert` nodes that are NOT
    inside an `ast.If` (those are recorded by `_collect_ifs` with the
    proper trigger_var, so we must not double-count). Recurse into
    loop/with/try bodies to catch bare asserts nested there, but skip
    `ast.If` blocks entirely (their contents are owned by `_collect_ifs`).

    Each assertion is recorded once. Bare assertions get trigger_var
    `"<bare>"` and an empty trigger set so the downstream MIRROR comparison
    surfaces a `trigger_mismatch` (visible to humans) instead of an
    `assertion_missing` (silently-blocking false positive).
    """
    for st in stmts:
        if isinstance(st, ast.Assert):
            pred = _canonical_predicate(ast.unparse(st.test))
            if pred is not None:
                uses.append(_SubUse("<bare>", frozenset(), frozenset({pred})))
        elif isinstance(st, ast.If):
            # Owned by `_collect_ifs` — do NOT recurse to avoid double-count.
            pass
        elif isinstance(st, (ast.For, ast.While, ast.With, ast.Try)):
            # Recurse into loop/with/try bodies for nested bare asserts.
            for field in ("body", "orelse", "finalbody", "handlers"):
                block = getattr(st, field, None)
                if block:
                    _collect_bare_asserts(block, uses)


def _trigger_var_name(test_node: ast.AST) -> str | None:
    """Extract the variable name from a Compare node, regardless of side."""
    if isinstance(test_node, ast.Compare):
        if isinstance(test_node.left, ast.Name):
            return test_node.left.id
        if (len(test_node.comparators) == 1
                and isinstance(test_node.comparators[0], ast.Name)):
            return test_node.comparators[0].id
    return None


def _extract_sub_assertions(tree: ast.AST) -> list:
    uses: list = []
    for fn in ast.walk(tree):
        if isinstance(fn, ast.FunctionDef) and fn.name.startswith("test_"):
            _collect_ifs(fn.body, uses)
            # Bug Fix R5 (2026-07-15): also walk for bare top-level asserts
            # that don't sit under an if-trigger. These are equally valid
            # TDD-RED shape; previous behaviour silently dropped them.
            _collect_bare_asserts(fn.body, uses)
    return uses


def check_test_mirrors_spec_js(
    test_source: str, spec_cases: list, spec_assertions: list,
    dialect: str = "typescript",
) -> list:
    """Structure-only P3 mirror gate for JS/TS test files.

    Honest scope (engine contract: "the engine does not guess"):
      * the file must parse (tree-sitter) and contain at least one it()/test()
        case — structural divergence is an error;
      * Python-syntax spec predicates cannot be mechanically aligned with JS
        assertion expressions, so each declared sub-assertion is surfaced as a
        needs_review INFO violation for the human reviewer — never silently
        passed, never guessed.

    Semantic predicate/parametrize alignment stays a Python-only capability;
    docs/ADDING_LANGUAGE_SUPPORT_SOP.md tracks this as a known limitation.
    """
    from tree_sitter import Language, Parser

    if dialect == "typescript":
        import tree_sitter_typescript as tst
        lang = Language(tst.language_typescript())
    elif dialect == "tsx":
        import tree_sitter_typescript as tst
        lang = Language(tst.language_tsx())
    else:
        import tree_sitter_javascript as tsj
        lang = Language(tsj.language())

    tree = Parser(lang).parse(test_source.encode("utf-8"))
    if tree.root_node.has_error:
        return [Violation(check_type="test_unparseable", rule_id="P3", severity="error",
                          message=f"test file does not parse as {dialect}")]

    if re.search(r"\b(?:it|test)\s*[.(]", test_source) is None:
        return [Violation(
            check_type="no_test_cases", rule_id="P3", severity="error",
            message="no it()/test() cases found — TEST_SPEC cases are not implemented")]

    violations: list = []
    for sa in spec_assertions:
        violations.append(Violation(
            check_type="js_predicate_review", rule_id=sa.rule_id, severity="info",
            message=(f"sub-assertion {sa.rule_id!r} predicate {sa.predicate!r}: "
                     f"JS/TS predicate alignment is structure-only — needs_review "
                     f"by the P3 reviewer")))
    return violations


def check_test_mirrors_spec(
    test_source: str, spec_cases: list, spec_assertions: list, fr_id: str | None = None
) -> list:
    """Return Violations where the test diverges from the (P2-locked) TEST_SPEC.

    `fr_id` scopes the parametrize-alignment check (section 1) to this FR's
    own test functions (`test_fr{NN}_*`) — the canonical test file also
    holds Cross-Cutting/NFR/Deployment-smoke tests for OTHER TEST_SPEC
    sections, whose parametrize rows must not be compared against THIS FR's
    case table. Optional (defaults to None = unscoped, legacy behavior) so
    existing callers that check a single-FR-only file keep working."""
    try:
        tree = ast.parse(test_source)
    except SyntaxError as exc:
        return [Violation(check_type="test_unparseable", rule_id="P3", severity="error",
                          message=f"test file does not parse: {exc}")]

    groups = _extract_parametrize(tree, fr_id=fr_id)
    subs = _extract_sub_assertions(tree)
    cases_by_id = {c.case_id: c for c in spec_cases}
    violations: list = []

    # 1. Parametrize alignment (projected onto each captured signature group).
    covered_case_ids: set = set()
    for var_names, rows in groups:
        relevant_cases = [c for c in spec_cases if all(v in c.inputs for v in var_names)]
        covered_case_ids.update(c.case_id for c in relevant_cases)
        spec_proj = {tuple(_as_str(c.inputs.get(v)) for v in var_names) for c in relevant_cases}
        test_proj = {tuple(_as_str(x) for x in row) for row in rows}
        for miss in sorted(spec_proj - test_proj):
            violations.append(Violation(
                check_type="param_missing", rule_id="P3", severity="error",
                message=f"TEST_SPEC declares case {dict(zip(var_names, miss))} but the test has no such parametrize row"))
        for extra in sorted(test_proj - spec_proj):
            violations.append(Violation(
                check_type="param_extra", rule_id="P3", severity="error",
                message=f"test parametrize row {dict(zip(var_names, extra))} is not declared in TEST_SPEC"))
    if groups:
        for c in spec_cases:
            if c.case_id not in covered_case_ids:
                violations.append(Violation(
                    check_type="case_uncovered", rule_id="P3", severity="error",
                    message=f"TEST_SPEC case {c.case_id} (inputs={c.inputs}) has no matching "
                            f"parametrize signature in the test file"))

    # 2. Sub-assertion predicate + trigger alignment.
    for sa in spec_assertions:
        # Bug #27 fix: use _canonical_predicate for set-membership
        # comparison. Both spec and test sides go through the same
        # canonical form (whitespace-stripped, paren-stripped), so
        # semantically equivalent predicates match.
        norm = _canonical_predicate(sa.predicate) or _normalize_predicate(sa.predicate)
        if norm is None:
            continue
        matches = [s for s in subs if norm in s.asserts]
        if not matches:
            violations.append(Violation(
                check_type="assertion_missing", rule_id=sa.rule_id, severity="error",
                message=(f"TEST_SPEC sub-assertion {sa.rule_id!r} predicate {sa.predicate!r} "
                         f"is not implemented by any test assertion")))
            continue
        for m in matches:
            spec_trigger = {_as_str(cases_by_id[cid].inputs.get(m.var))
                            for cid in sa.applies_to if cid in cases_by_id}
            test_trigger = {_as_str(v) for v in m.trigger}
            if test_trigger != spec_trigger:
                violations.append(Violation(
                    check_type="trigger_mismatch", rule_id=sa.rule_id, severity="error",
                    message=(f"sub-assertion {sa.rule_id!r} predicate {sa.predicate!r}: test applies it to "
                             f"{sorted(test_trigger)} but TEST_SPEC applies_to maps to {sorted(spec_trigger)}"),
                    extra={"test_trigger": sorted(test_trigger), "spec_trigger": sorted(spec_trigger)}))
    return violations
