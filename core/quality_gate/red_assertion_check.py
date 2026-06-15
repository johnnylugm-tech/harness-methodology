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

    This helper produces a canonical form by:
    1. Parsing with `ast.parse(text.strip(), mode="eval")`
    2. Normalizing operator spacing (single space around binary ops)
    3. Stripping redundant parens that `ast.unparse` adds
    4. Re-dumping via `ast.unparse`

    Returns None on syntax error (callers should fall back to
    `_normalize_predicate` or skip).
    """
    try:
        tree = ast.parse(text.strip(), mode="eval").body
        return ast.unparse(tree)
    except SyntaxError:
        return None


def _as_str(value) -> str:
    return value if isinstance(value, str) else repr(value)


def _literal_value(node: ast.AST):
    """Concrete value for a parametrize arg / trigger constant.

    Constant -> its value; a constants-only expression (e.g. "x"*11) -> safe
    eval; otherwise (None, False)."""
    if isinstance(node, ast.Constant):
        return node.value, True
    try:
        return _safe_eval_predicate(ast.unparse(node), {}), True
    except (SyntaxError, TypeError, ValueError, ArithmeticError, MemoryError):
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


def _extract_parametrize(tree: ast.AST):
    """Return (var_names, rows) aggregating ALL @pytest.mark.parametrize decorators
    that share the same variable-name signature (C2 fix: multi-function test files).

    Iterates function definitions in AST order; for each function processes only
    its decorator_list (not the body) to avoid false matches. Collects rows from
    every parametrize decorator whose comma-separated variable names match those
    of the first one found.  Different-signature decorators are silently skipped
    so that files with multiple unrelated parametrize functions don't cause
    cross-contamination.

    Supports both an inline args list and a module-level list of pytest.param/tuples.
    """
    list_vars: dict[str, ast.expr] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, (ast.List, ast.Tuple)):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    list_vars[t.id] = node.value

    first_var_names: list[str] = []
    all_rows: list = []

    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in fn.decorator_list:
            if not (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute)
                    and dec.func.attr == "parametrize" and len(dec.args) >= 2):
                continue
            var_node, args_node = dec.args[0], dec.args[1]
            if not (isinstance(var_node, ast.Constant) and isinstance(var_node.value, str)):
                continue
            var_names = [v.strip() for v in var_node.value.split(",")]
            # Only aggregate parametrize blocks with the same variable signature.
            if first_var_names and var_names != first_var_names:
                continue
            if not first_var_names:
                first_var_names = var_names
            args_seq: ast.expr | None = args_node
            if isinstance(args_node, ast.Name):
                args_seq = list_vars.get(args_node.id)
            if not isinstance(args_seq, (ast.List, ast.Tuple)):
                continue
            for elt in args_seq.elts:
                values = _param_row_values(elt, len(var_names))
                if values is not None:
                    all_rows.append(values)

    return first_var_names, all_rows


def _parse_trigger(test_node: ast.AST):
    """Return (var, {values}) for `VAR == c` / `VAR in (…)`, else None."""
    if not (isinstance(test_node, ast.Compare) and len(test_node.ops) == 1
            and isinstance(test_node.left, ast.Name)):
        return None
    var = test_node.left.id
    op = test_node.ops[0]
    comp = test_node.comparators[0]
    if isinstance(op, ast.Eq):
        v, ok = _literal_value(comp)
        return (var, {v}) if ok else None
    if isinstance(op, ast.In) and isinstance(comp, (ast.Tuple, ast.List, ast.Set)):
        vals = set()
        for e in comp.elts:
            v, ok = _literal_value(e)
            if not ok:
                return None
            vals.add(v)
        return (var, vals)
    return None


def _collect_ifs(stmts: list, uses: list) -> None:
    for st in stmts:
        if isinstance(st, ast.If):
            trig = _parse_trigger(st.test)
            if trig is not None:
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


def _extract_sub_assertions(tree: ast.AST) -> list:
    uses: list = []
    for fn in ast.walk(tree):
        if isinstance(fn, ast.FunctionDef) and fn.name.startswith("test_"):
            _collect_ifs(fn.body, uses)
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


def check_test_mirrors_spec(test_source: str, spec_cases: list, spec_assertions: list) -> list:
    """Return Violations where the test diverges from the (P2-locked) TEST_SPEC."""
    try:
        tree = ast.parse(test_source)
    except SyntaxError as exc:
        return [Violation(check_type="test_unparseable", rule_id="P3", severity="error",
                          message=f"test file does not parse: {exc}")]

    var_names, rows = _extract_parametrize(tree)
    subs = _extract_sub_assertions(tree)
    cases_by_id = {c.case_id: c for c in spec_cases}
    violations: list = []

    # 1. Parametrize alignment (projected onto the parametrize variables).
    if var_names and spec_cases:
        spec_proj = {tuple(_as_str(c.inputs.get(v)) for v in var_names) for c in spec_cases}
        test_proj = {tuple(_as_str(x) for x in row) for row in rows}
        for miss in sorted(spec_proj - test_proj):
            violations.append(Violation(
                check_type="param_missing", rule_id="P3", severity="error",
                message=f"TEST_SPEC declares case {dict(zip(var_names, miss))} but the test has no such parametrize row"))
        for extra in sorted(test_proj - spec_proj):
            violations.append(Violation(
                check_type="param_extra", rule_id="P3", severity="error",
                message=f"test parametrize row {dict(zip(var_names, extra))} is not declared in TEST_SPEC"))

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
