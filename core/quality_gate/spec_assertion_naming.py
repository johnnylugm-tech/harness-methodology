"""Sub-assertion predicate naming checks (v2.13.0).

Background (FR-05 P3 2026-07-16 lesson): a TEST_SPEC sub-assertion row
like ``FR05-json-flag-set | json == "true" | [2]`` is mirrored verbatim
by the TDD-RED agent as ``json = "true"`` inside a test function. If
the test file also imports the stdlib ``json`` module, the local
binding shadows the module and a later ``json.loads(...)`` raises
``AttributeError: 'str' object has no attribute 'loads'``.

check-test-spec-consistency now rejects such predicates at P2 so P3
TDD-RED never produces a silently-broken test file.

This module is the SSOT for the naming rule. The list is intentionally
small (top-level stdlib modules + a handful of common builtins that
LLMs frequently use as variable names); it is NOT an exhaustive list
of all stdlib/builtin names. Additions belong here with a comment
explaining the empirical trigger.
"""
from __future__ import annotations

# Top-level stdlib modules most likely to collide with predicate LHS.
# Keep alphabetical; add new entries with a one-line empirical trigger.
STDLIB_MODULE_NAMES: frozenset[str] = frozenset({
    "asyncio",
    "json",
    "logging",
    "os",
    "pathlib",
    "subprocess",
    "sys",
    "time",
    "typing",
})

# Builtins that LLM-written test code frequently uses as variable
# names. ``dict/list/set/tuple/str/int/bool/bytes`` are constructors,
# not variables — but their NAME colliding with a local still causes
# the same shadow failure mode. ``type`` is also a builtin.
BUILTIN_NAMES: frozenset[str] = frozenset({
    "bool",
    "bytes",
    "dict",
    "file",   # commonly shadowed; original was `file` builtin pre-py3
    "id",
    "int",
    "list",
    "path",   # pathlib attribute-style shadow
    "set",
    "str",
    "tuple",
    "type",
})

# Combined, exported as the canonical collision set.
RESERVED_NAMES: frozenset[str] = STDLIB_MODULE_NAMES | BUILTIN_NAMES


def _suggest_rename(name: str) -> str:
    """Produce a domain-specific synonym suggestion for a colliding LHS.

    Heuristic: append ``_flag`` / ``_name`` / ``_str`` / ``_value`` based
    on shape. Conservative — never claims to perfectly capture the
    predicate's intent; the spec author owns the final rename. The goal
    is just to give the consistency checker output something actionable.
    """
    if name in {"json", "time", "logging", "asyncio", "typing"}:
        return f"{name}_flag"
    if name == "type":
        return "type_name"
    if name in {"os", "sys", "subprocess", "pathlib"}:
        return f"{name}_name"
    if name in {"path", "file", "id"}:
        return f"{name}_str"
    if name in {"bool", "bytes", "dict", "int", "list", "set", "str", "tuple"}:
        return f"{name}_val"
    return f"{name}_value"


def extract_predicate_lhs(predicate: str) -> str | None:
    """Return the LHS identifier of a predicate expression, or None.

    Accepts the canonical TEST_SPEC predicate shape: ``LHS == RHS`` or
    ``LHS != RHS`` (whitespace-tolerant). ``len(LHS) > N`` style
    predicates return ``LHS`` as the identifier (the function/method
    call is the LHS in Python AST terms). Returns None when no
    identifier can be extracted (e.g. compound boolean expressions,
    ``a in [..]``, etc. — those are out of scope for this check).
    """
    text = (predicate or "").strip()
    if not text:
        return None
    for op in ("==", "!="):
        idx = text.find(op)
        if idx == -1:
            continue
        lhs = text[:idx].strip()
        # Strip surrounding parens; keep only the leading identifier or
        # function-call LHS like ``len(x)`` → ``len``.
        if "(" in lhs:
            # Keep just the function name (e.g. "len(command)" → "len").
            # ``len`` itself is NOT in RESERVED_NAMES so this is fine.
            func_name = lhs.split("(", 1)[0].strip()
            return func_name or None
        # Bare identifier (allow trailing attribute access like ``foo.bar``).
        head = lhs.split(".", 1)[0].strip()
        return head or None
    # No == / != found. Scan for trailing `<ident> <op> <rhs>` patterns
    # such as `len(command) > 0` (operator ">") so we can extract the
    # call target. Returns the LHS function name when the predicate is
    # a single comparison; returns None for compound / membership tests.
    for op in (">=", "<=", ">", "<"):
        idx = text.find(op)
        if idx == -1:
            continue
        lhs = text[:idx].strip()
        if "(" in lhs:
            return lhs.split("(", 1)[0].strip() or None
        return lhs.split(".", 1)[0].strip() or None
    return None


def scan_stdlib_name_collisions(
    parsed: dict,
) -> list[tuple[str, str, str, str]]:
    """Yield ``(fr_id, rule_id, predicate, suggested_rename)`` for every
    sub-assertion whose predicate LHS shadows a Python stdlib/builtin.

    ``parsed`` is the dict returned by
    ``core.quality_gate.parsers.SpecAssertionParser.parse``:
    ``{fr_id: (cases, assertions)}`` where ``assertions`` is a list of
    named tuples with ``rule_id`` and ``predicate`` attributes (see
    ``red_assertion_check.SubAssertion``).
    """
    out: list[tuple[str, str, str, str]] = []
    for fr_id, (_cases, assertions) in sorted(parsed.items()):  # noqa: PERF203  (cases not used here)
        for a in assertions or ():
            pred_raw = getattr(a, "predicate", None)
            pred: str = pred_raw if isinstance(pred_raw, str) else ""
            rule_id_raw = getattr(a, "rule_id", "<unknown>")
            rule_id: str = rule_id_raw if isinstance(rule_id_raw, str) else "<unknown>"
            lhs = extract_predicate_lhs(pred)
            if lhs and lhs in RESERVED_NAMES:
                out.append((fr_id, rule_id, pred, _suggest_rename(lhs)))
    return out