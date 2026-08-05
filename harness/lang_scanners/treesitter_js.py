"""JS/TS in-process scanners — assertions / error-handling / JSDoc / MI.

tree-sitter ports of the Python ast scanners (python_ast.py), emitting the
SAME output JSON schemas so the existing scorers apply unchanged:

  run_assertions      → ast-assertions scorer   (it()/test() titles)
  run_error_handling  → ast-error-handling scorer (try/catch or .catch())
  run_doc_coverage    → ast-docstrings scorer   (JSDoc on exported decls)
  run_mi              → radon-mi scorer          ({file: {"mi": x, "rank": r}})

Grammars are pinned in requirements.txt (a grammar bump can change node
shapes and therefore scores). tree_sitter imports are lazy: python-only
installs never pay for them — availability is guarded by the registry
check_cmd at S2 preflight.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import TYPE_CHECKING, Callable, Iterator

if TYPE_CHECKING:  # tree_sitter imports stay lazy at runtime
    from tree_sitter import Parser

from core.utils.delivery_scope import iter_delivered_files
from core.utils.lang_patterns import (
    SOURCE_EXTENSIONS as _LANG_EXTS,
    TEST_FILE_PATTERN as _TEST_FILE_RE,
)

# Same layout conventions as the Python scanners.
_SRC_DIRS: tuple[str, ...] = ("03-development/src", "src")
_TEST_DIRS: tuple[str, ...] = ("tests", "03-development/tests")

_SOURCE_EXTS: tuple[str, ...] = _LANG_EXTS["javascript"]

# Comment-style-agnostic pragma (Python scanners use `# pragma: ...`).
_PRAGMA_EXEMPT = "pragma: no error-handling"

_parsers: dict[str, "Parser"] = {}


def _parser_for(path: Path) -> "Parser":
    """Return a cached tree-sitter Parser for the file's dialect."""
    from tree_sitter import Language, Parser

    ext = path.suffix.lower()
    if ext == ".ts":
        key = "typescript"
    elif ext == ".tsx":
        key = "tsx"
    else:
        key = "javascript"

    if key not in _parsers:
        if key == "typescript":
            import tree_sitter_typescript as tst
            lang = Language(tst.language_typescript())
        elif key == "tsx":
            import tree_sitter_typescript as tst
            lang = Language(tst.language_tsx())
        else:
            import tree_sitter_javascript as tsj
            lang = Language(tsj.language())
        _parsers[key] = Parser(lang)
    return _parsers[key]


def _walk(node) -> Iterator:
    """Depth-first iterator over all named + anonymous nodes."""
    stack = [node]
    while stack:
        n = stack.pop()
        yield n
        stack.extend(reversed(n.children))


def _iter_files(
    project_root: str, rel_dirs: tuple[str, ...],
    keep: Callable[[Path], bool],
) -> Iterator[tuple[Path, Path]]:
    """Yield (path, relpath) for unique matching files under rel_dirs."""
    root = Path(project_root)
    seen: set[str] = set()
    for rel in rel_dirs:
        base = root / rel
        if not base.is_dir():
            continue
        # Round 37: the file population is what the project delivers, not
        # whatever is on disk under base/ — an agent worktree or a stale
        # build output is not JS/TS this project ships.
        for path in iter_delivered_files(base):
            if path.suffix.lower() not in _SOURCE_EXTS:
                continue
            key = str(path.resolve())
            if key in seen or not keep(path):
                continue
            seen.add(key)
            yield path, path.relative_to(root)


def _parse(path: Path):
    """Parse a file; returns (tree, source_bytes) or (None, b"") on failure."""
    try:
        source = path.read_bytes()
        return _parser_for(path).parse(source), source
    except (OSError, ValueError):
        return None, b""


def _node_text(node, source: bytes) -> str:
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


# ──────────────────────────────────────────────────────────────────────────────
# A. Assertions — it()/test() callbacks must contain expect()/assert
# ──────────────────────────────────────────────────────────────────────────────

def _callee_root(node, source: bytes) -> str:
    """Leftmost identifier of a call's callee.

    Handles member chains (it.skip → it) and curried calls
    (it.each([...])("title", fn) → it).
    """
    fn = node.child_by_field_name("function")
    while fn is not None:
        if fn.type == "member_expression":
            fn = fn.child_by_field_name("object")
        elif fn.type == "call_expression":
            fn = fn.child_by_field_name("function")
        else:
            break
    return _node_text(fn, source) if fn is not None and fn.type == "identifier" else ""


def _call_has_assertion(call_node, source: bytes) -> bool:
    """True if the test callback body contains expect(...) / assert usage."""
    for sub in _walk(call_node):
        if sub.type != "call_expression":
            continue
        fn = sub.child_by_field_name("function")
        if fn is None:
            continue
        text = _node_text(fn, source)
        root = text.split(".")[0].split("(")[0]
        if root in ("expect", "assert") or text.endswith(".should"):
            return True
    return False


def _test_title(call_node, source: bytes) -> str:
    """First string/template argument of it()/test() — the test title."""
    args = call_node.child_by_field_name("arguments")
    if args is None:
        return "<untitled>"
    for arg in args.named_children:
        if arg.type in ("string", "template_string"):
            return _node_text(arg, source).strip("'\"`")
    return "<untitled>"


def run_assertions(project_root: str) -> tuple[str, int]:
    """it()/test() cases without expect()/assert are zero_assert shells.

    Output schema matches python_ast.run_assertions:
    {total, asserted, zero_assert: ["relpath::title", ...]}.
    """
    total = 0
    asserted = 0
    zero_assert: list[str] = []

    for path, rel in _iter_files(
        project_root, _TEST_DIRS, lambda p: bool(_TEST_FILE_RE.search(p.name))
    ):
        tree, source = _parse(path)
        if tree is None:
            continue
        for node in _walk(tree.root_node):
            if node.type != "call_expression":
                continue
            if _callee_root(node, source) not in ("it", "test"):
                continue
            # Curried forms (it.each([...])("title", fn)): the inner
            # it.each([...]) is itself the callee of the outer call — skip it,
            # only the outer invocation is the test case.
            parent = node.parent
            if (parent is not None and parent.type == "call_expression"
                    and parent.child_by_field_name("function") == node):
                continue
            total += 1
            if _call_has_assertion(node, source):
                asserted += 1
            else:
                zero_assert.append(f"{rel}::{_test_title(node, source)}")

    summary = {"total": total, "asserted": asserted, "zero_assert": zero_assert[:50]}
    return json.dumps(summary), 0


# ──────────────────────────────────────────────────────────────────────────────
# B. Error handling — try/catch or promise .catch() per source file
# ──────────────────────────────────────────────────────────────────────────────

_CODE_NODE_TYPES = frozenset({
    "function_declaration", "function_expression", "arrow_function",
    "method_definition", "class_declaration", "generator_function_declaration",
})


def _file_has_code(tree) -> bool:
    return any(n.type in _CODE_NODE_TYPES for n in _walk(tree.root_node))


def _file_has_handler(tree, source: bytes) -> bool:
    for node in _walk(tree.root_node):
        if node.type == "try_statement":
            if any(c.type == "catch_clause" for c in node.children):
                return True
        elif node.type == "member_expression":
            prop = node.child_by_field_name("property")
            if prop is None or _node_text(prop, source) != "catch":
                continue
            # Only a promise rejection handler `x.catch(fn)` counts — a bare
            # property read `obj.catch` is not error handling.
            parent = node.parent
            if (parent is not None and parent.type == "call_expression"
                    and parent.child_by_field_name("function") == node):
                return True
    return False


def _catch_anti_patterns(tree, rel: "Path") -> list[str]:
    """Empty catch blocks — handlers that exist but swallow every error.

    A ``catch {}`` / ``catch (e) {}`` whose statement_block has no named
    children besides comments is the JS analogue of Python's broad_swallow.
    Returns "relpath:line::empty_catch" entries (python_ast parity format).
    """
    found: list[str] = []
    for node in _walk(tree.root_node):
        if node.type != "catch_clause":
            continue
        body = node.child_by_field_name("body")
        if body is None:
            continue
        statements = [c for c in body.named_children if c.type != "comment"]
        if not statements:
            found.append(f"{rel}:{node.start_point[0] + 1}::empty_catch")
    return found


def run_error_handling(project_root: str) -> tuple[str, int]:
    """File-level try/catch (or .catch()) coverage of source files.

    Output schema matches python_ast.run_error_handling, including
    anti_patterns (empty catch blocks — a handler that swallows everything is
    no longer a free positive signal). Files containing
    `// pragma: no error-handling` are exempt from the denominator.
    """
    total = 0
    with_handler = 0
    exempt_count = 0
    no_handler: list[str] = []
    exempt_files: list[str] = []
    anti_patterns: list[str] = []

    for path, rel in _iter_files(
        project_root, _SRC_DIRS, lambda p: not _TEST_FILE_RE.search(p.name)
    ):
        tree, source = _parse(path)
        if tree is None or not _file_has_code(tree):
            continue
        if _PRAGMA_EXEMPT in source.decode("utf-8", errors="replace"):
            exempt_count += 1
            exempt_files.append(str(rel))
            continue
        total += 1
        if _file_has_handler(tree, source):
            with_handler += 1
        else:
            no_handler.append(str(rel))
        anti_patterns.extend(_catch_anti_patterns(tree, rel))

    summary = {
        "total": total, "with_handler": with_handler, "no_handler": no_handler[:50],
        "exempt_count": exempt_count, "exempt_files": exempt_files[:50],
        "anti_patterns": anti_patterns[:50],
    }
    return json.dumps(summary), 0


# ──────────────────────────────────────────────────────────────────────────────
# C. Doc coverage — JSDoc (/** */) on exported declarations
# ──────────────────────────────────────────────────────────────────────────────

def _has_jsdoc(node, source: bytes) -> bool:
    """True when the node (or its export wrapper) is preceded by /** ... */."""
    target = node
    if target.parent is not None and target.parent.type == "export_statement":
        target = target.parent
    prev = target.prev_sibling
    if prev is not None and prev.type == "comment":
        return _node_text(prev, source).startswith("/**")
    return False


def _exported_name(decl, source: bytes) -> str:
    name = decl.child_by_field_name("name")
    if name is not None:
        return _node_text(name, source)
    # lexical_declaration: export const foo = () => {}
    for sub in decl.named_children:
        if sub.type == "variable_declarator":
            ident = sub.child_by_field_name("name")
            if ident is not None:
                return _node_text(ident, source)
    return "<anonymous>"


def _lexical_is_callable(decl) -> bool:
    """True if `export const x = ...` binds a function/arrow (not a data value).

    Parity with python_ast.run_docstrings, which counts public def/class but
    NOT module-level constants — `export const TABLE = {...}` is a value, not a
    documentable callable, and must not inflate the doc-coverage denominator.
    """
    for sub in decl.named_children:
        if sub.type == "variable_declarator":
            value = sub.child_by_field_name("value")
            if value is not None and value.type in (
                "arrow_function", "function_expression",
                "generator_function",
            ):
                return True
    return False


def run_doc_coverage(project_root: str) -> tuple[str, int]:
    """JSDoc coverage of the exported (public) surface.

    Counts: export function/class/const-arrow declarations, plus public
    method_definitions of exported classes (constructor and _-prefixed
    excluded — python_ast.run_docstrings parity). Output schema:
    {total, with_doc, missing} or {} when no code exists at all.
    """
    total = 0
    with_doc = 0
    missing: list[str] = []
    has_code = False

    for path, rel in _iter_files(
        project_root, _SRC_DIRS, lambda p: not _TEST_FILE_RE.search(p.name)
    ):
        tree, source = _parse(path)
        if tree is None:
            continue
        if not _file_has_code(tree):
            continue
        has_code = True

        for node in _walk(tree.root_node):
            if node.type != "export_statement":
                continue
            for decl in node.named_children:
                if decl.type in ("function_declaration", "class_declaration",
                                 "abstract_class_declaration",
                                 "generator_function_declaration",
                                 "lexical_declaration"):
                    # Value exports (export const X = 5) are not documentable
                    # callables — skip them for Python def/class parity.
                    if (decl.type == "lexical_declaration"
                            and not _lexical_is_callable(decl)):
                        continue
                    name = _exported_name(decl, source)
                    if name.startswith("_"):
                        continue
                    total += 1
                    documented = _has_jsdoc(decl, source)
                    if documented:
                        with_doc += 1
                    else:
                        missing.append(f"{rel}::{name}")
                    if decl.type in ("class_declaration", "abstract_class_declaration"):
                        body = decl.child_by_field_name("body")
                        for member in (body.named_children if body else []):
                            if member.type != "method_definition":
                                continue
                            mname_node = member.child_by_field_name("name")
                            mname = (_node_text(mname_node, source)
                                     if mname_node is not None else "")
                            if mname.startswith("_") or mname == "constructor":
                                continue
                            total += 1
                            prev = member.prev_sibling
                            if prev is not None and prev.type == "comment" and \
                                    _node_text(prev, source).startswith("/**"):
                                with_doc += 1
                            else:
                                missing.append(f"{rel}::{name}.{mname}")

    if not has_code:
        return json.dumps({}), 0

    summary = {"total": total, "with_doc": with_doc, "missing": missing[:50]}
    return json.dumps(summary), 0


# ──────────────────────────────────────────────────────────────────────────────
# D. Maintainability Index — radon-compatible normalized MI per file
# ──────────────────────────────────────────────────────────────────────────────

_DECISION_NODE_TYPES = frozenset({
    "if_statement", "for_statement", "for_in_statement", "while_statement",
    "do_statement", "switch_case", "catch_clause", "ternary_expression",
})

_OPERAND_NODE_TYPES = frozenset({
    "identifier", "property_identifier", "shorthand_property_identifier",
    "number", "string", "template_string", "true", "false", "null",
    "undefined", "regex",
})


def _file_metrics(tree, source: bytes) -> tuple[float, int, int]:
    """(halstead_volume, cyclomatic_complexity, sloc) for one file."""
    operators: dict[str, int] = {}
    operands: dict[str, int] = {}
    cc = 1

    for node in _walk(tree.root_node):
        if node.type in _DECISION_NODE_TYPES:
            cc += 1
        elif node.type == "binary_expression":
            op = node.child_by_field_name("operator")
            if op is not None and _node_text(op, source) in ("&&", "||", "??"):
                cc += 1
        if node.child_count == 0:  # leaf
            if node.type in _OPERAND_NODE_TYPES:
                operands[_node_text(node, source)] = (
                    operands.get(_node_text(node, source), 0) + 1
                )
            elif node.type not in ("comment",) and not node.is_named:
                # anonymous leaves: punctuation, operators, keywords
                operators[node.type] = operators.get(node.type, 0) + 1

    n1, n2 = len(operators), len(operands)
    big_n = sum(operators.values()) + sum(operands.values())
    vocab = n1 + n2
    volume = big_n * math.log2(vocab) if vocab > 0 else 0.0

    text = source.decode("utf-8", errors="replace")
    sloc = sum(
        1 for ln in text.splitlines()
        if ln.strip() and not ln.strip().startswith(("//", "/*", "*"))
    )
    return volume, cc, max(sloc, 1)


def _mi_rank(mi: float) -> str:
    if mi >= 20:
        return "A"
    if mi >= 10:
        return "B"
    return "C"


def run_mi(project_root: str) -> tuple[str, int]:
    """Per-file normalized Maintainability Index (radon mi -j compatible).

    MI = max(0, (171 − 5.2·ln(V) − 0.23·CC − 16.2·ln(SLOC)) × 100/171) —
    the same normalized formula radon uses, so the radon-mi scorer (average
    across files) applies unchanged.
    """
    results: dict[str, dict] = {}

    for path, rel in _iter_files(
        project_root, _SRC_DIRS, lambda p: not _TEST_FILE_RE.search(p.name)
    ):
        tree, source = _parse(path)
        if tree is None or not _file_has_code(tree):
            continue
        volume, cc, sloc = _file_metrics(tree, source)
        raw = (
            171.0
            - 5.2 * math.log(volume if volume > 0 else 1.0)
            - 0.23 * cc
            - 16.2 * math.log(sloc)
        )
        mi = max(0.0, raw * 100.0 / 171.0)
        results[str(rel)] = {"mi": round(mi, 2), "rank": _mi_rank(mi)}

    return json.dumps(results), 0


# Optional shared helper for callers that need a quick availability probe.
def grammars_available() -> bool:
    """True when the pinned tree-sitter grammars import cleanly."""
    try:
        import tree_sitter  # noqa: F401
        import tree_sitter_javascript  # noqa: F401
        import tree_sitter_typescript  # noqa: F401
        return True
    except ImportError:
        return False
