"""State/manifest read-convergence lint (Round 14 站2d).

Round 14 站2 converged ~70 raw `state.json`/`quality_manifest.json` read
sites across cli/core/harness/scripts/detection onto core/state_io.py's
`load_state()`/`load_quality_manifest()` — the two functions that draw a
single line between "missing" (silently returns {}) and "corrupt" (raises
StateCorruptError, or degrades to {} with a degradation-ledger entry under
lenient=True). Before convergence, three uncoordinated shapes disagreed on
that line: a bare read that let json.JSONDecodeError raise uncaught (which
the Round 13 crash boundary then misclassifies as [HARNESS-BUG] — wrong,
since a corrupt PROJECT file is not a bug in harness's own code), a narrow
`except: pass`, and a broad `except Exception` fail-open — see
core/state_io.py's own module docstring for the fuller history.

This is a regression guard against a NEW site reintroducing any of those
three shapes instead of calling the shared helpers. AST-based (like
tests/test_exception_swallow_ratchet.py), not a text/regex scan: Python's
own parser already discards comments and normalizes every string form, so
a docstring or comment that mentions "state.json" in prose can never
masquerade as one of the Call nodes this scanner inspects.

The scanner tracks two things per function body, in textual (not control-
flow) order — a heuristic, the same tradeoff test_exception_swallow_
ratchet.py documents for its own shape list:

  1. Which local variables are "tainted" by a state/manifest path — either
     assigned an expression that mentions `state_json_path`/
     `quality_manifest_path`/the literal filename, or assigned by reading
     (`.read_text()`/`.read()`/`open()`) a variable already tainted.
     Round 14 站2c's own discovered-late gaps (this station's own first
     scan found 15 more sites across cli/core/harness that the original
     grep-based migration missed — variable names like `_mp`/`_mf_path`/
     `_mfst_path` didn't match the grep's `state_path`/`manifest_path`
     patterns, and core/quality_gate/phase_truth_verifier.py's
     `_js_runner_argv` used a `ProjectLayout(...).state_json_path` chain
     the grep's literal-filename pattern couldn't see either) are exactly
     this shape — hence taint tracking by AST structure rather than by
     variable name or literal-substring grep.
  2. Every `json.loads(...)`/`json.load(...)` call whose argument is
     tainted (by either rule above) is a violation: it's re-implementing
     core/state_io.py instead of calling it.

Exemption mechanism: an inline `# state-io-exempt: <reason>` comment,
either trailing the flagged call or in the contiguous comment block
directly above it, suppresses that one call site — not the whole file.
Two files are exempted wholesale (see _EXEMPT_FILES) because EVERY read
in them is architecturally incompatible with core/state_io.py's contract,
not just one call site.

Known limitation (documented, same spirit as the exception-swallow
ratchet's tuple-shape carve-outs): taint is tracked per top-level
statement list in source order, not real control-flow — an if/else
branch that clears taint on one arm but not the other is treated as
"still tainted after the if", which can only produce a false positive
(fixable with a `# state-io-exempt` comment), never a false negative
silently let through.
"""
from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

_SCAN_DIRS = ("cli", "core", "harness", "scripts", "detection")

_MARKERS = ("state_json_path", "quality_manifest_path", "state.json", "quality_manifest.json")

_EXEMPT_MARKER = "state-io-exempt"

# Round 14 站2d: whole files where EVERY state.json/quality_manifest.json
# read is architecturally incompatible with core/state_io.py's project-
# rooted, Path-based contract — not a single call site, so an inline
# `# state-io-exempt` comment per-site would be noise. Adding an entry
# here needs the same scrutiny as any other allowlist bump in this repo:
# dated reasoning, checked against the file's actual current behavior.
_EXEMPT_FILES = {
    # The converged implementation itself.
    "core/state_io.py",
    # _get_completed_phases(state_path: Path) takes an arbitrary path, not
    # necessarily <project>/.methodology/state.json — its own test suite
    # constructs flat tmp_path/state.json fixtures to test in isolation.
    # Round 14 站2c tried the state_io.py swap here and reverted it after 4
    # tests failed (assert [] == [1, 2, 3]); confirmed via empty git diff.
    "core/quality_gate/constitution/runner.py",
    # Standalone subprocess script: invoked as a bare `python
    # verify_gate1_qc.py` by a Bash sub-agent inside generated workflow JS,
    # against a VENDORED copy in a target project. No bootstrap sets up
    # sys.path for this invocation — importing core.state_io would break
    # its actual documented invocation (its own docstring specifies this
    # exact call form).
    "scripts/verify_gate1_qc.py",
    # Same standalone-subprocess shape as verify_gate1_qc.py, one directory
    # over: harness/ssi/scripts/ is a documented "embedded assets" family
    # (tests/test_ssi_scripts.py) vendored into target projects and run as
    # `python3 harness/ssi/scripts/verify_tools.py --core --project .` —
    # confirmed via repo-wide grep that zero files under harness/ssi/
    # scripts/ import core.* (a consistent, deliberate convention, not an
    # oversight).
    "harness/ssi/scripts/verify_tools.py",
    "scripts/phase_auditor.py",
    # PhaseAuditor reads through a GitHubFetcher|LocalFetcher abstraction
    # (self.gh.get_file_content(...)) that returns already-decoded text
    # from either the GitHub contents API or the local filesystem —
    # core/state_io.py's API can only ever read a local Path, so it can't
    # serve the GitHub-mode half of this abstraction. Separately, this
    # file's C9 check deliberately reports "manifest missing" and
    # "manifest not valid JSON" as two DISTINCT audit Findings (not a
    # collapse-to-one-fallback like every other site converged this
    # round) — state_io.py's binary strict/lenient contract would lose
    # that distinction.
}


def _is_tainted(expr: ast.expr, risky: dict[str, str]) -> bool:
    """Does this expression's value derive from a state/manifest path?"""
    if isinstance(expr, ast.Name):
        return expr.id in risky
    if isinstance(expr, ast.Call):
        if isinstance(expr.func, ast.Attribute) and _is_tainted(expr.func.value, risky):
            return True
        if isinstance(expr.func, ast.Name) and expr.func.id == "open" and expr.args:
            if _is_tainted(expr.args[0], risky):
                return True
    src = ast.unparse(expr)
    return any(marker in src for marker in _MARKERS)


def _flatten(stmts: list[ast.stmt]):
    """Yield statements in textual order, descending into nested blocks —
    but not into nested function/class bodies, which get their own fresh
    scan (see _scan_source). Not real control-flow — see module
    docstring's documented limitation."""
    for stmt in stmts:
        yield stmt
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        for field in ("body", "orelse", "finalbody"):
            block = getattr(stmt, field, None)
            if block:
                yield from _flatten(block)
        for handler in getattr(stmt, "handlers", ()):
            yield from _flatten(handler.body)


def _is_json_parse_call(node: ast.Call) -> ast.expr | None:
    """If node is `json.loads(X)` or `json.load(X)`, return X, else None."""
    if (
        isinstance(node.func, ast.Attribute)
        and node.func.attr in ("loads", "load")
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "json"
        and node.args
    ):
        return node.args[0]
    return None


def _is_exempted(node: ast.expr, lines: list[str]) -> bool:
    """`# state-io-exempt` on the call's own line(s), or anywhere in the
    contiguous comment/blank block immediately above it."""
    start = node.lineno - 1
    end = node.end_lineno or node.lineno
    if any(_EXEMPT_MARKER in lines[i] for i in range(start, min(end, len(lines)))):
        return True
    i = start - 1
    while i >= 0 and (lines[i].strip() == "" or lines[i].strip().startswith("#")):
        if _EXEMPT_MARKER in lines[i]:
            return True
        i -= 1
    return False


def _scan_function(func: "ast.Module | ast.FunctionDef | ast.AsyncFunctionDef", lines: list[str]) -> list[int]:
    hits: list[int] = []
    risky: dict[str, str] = {}
    for stmt in _flatten(func.body):
        if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1 and isinstance(stmt.targets[0], ast.Name):
            var = stmt.targets[0].id
            if _is_tainted(stmt.value, risky):
                risky[var] = var
            else:
                risky.pop(var, None)
        for item in getattr(stmt, "items", ()):
            if isinstance(item.optional_vars, ast.Name) and _is_tainted(item.context_expr, risky):
                risky[item.optional_vars.id] = item.optional_vars.id
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue  # nested scope — scanned independently, see _scan_source
        for node in ast.walk(stmt):
            if not isinstance(node, ast.Call):
                continue
            arg = _is_json_parse_call(node)
            if arg is not None and _is_tainted(arg, risky) and not _is_exempted(node, lines):
                hits.append(node.lineno)
    return hits


def _scan_source(source: str, filename: str = "<probe>") -> list[int]:
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError:
        return []
    lines = source.splitlines()
    hits: list[int] = []
    hits.extend(_scan_function(tree, lines))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            hits.extend(_scan_function(node, lines))
    return sorted(set(hits))


def _scan_file(path: Path) -> list[int]:
    try:
        source = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return _scan_source(source, filename=str(path))


def test_no_raw_state_or_manifest_json_parse():
    violations = []
    for d in _SCAN_DIRS:
        for path in sorted((REPO / d).rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            rel = path.relative_to(REPO).as_posix()
            if rel in _EXEMPT_FILES:
                continue
            for lineno in _scan_file(path):
                violations.append(f"{rel}:{lineno}")
    assert not violations, (
        "raw json.loads/json.load on a state.json/quality_manifest.json-"
        "derived path bypasses core/state_io.py's load_state()/"
        "load_quality_manifest() — call those instead (lenient=True for "
        "best-effort reads, strict for sites that must distinguish "
        "corrupt-vs-missing), or add a `# state-io-exempt: <reason>` "
        "comment if this site is genuinely incompatible (arbitrary path, "
        "non-filesystem content source, etc.):\n  " + "\n  ".join(violations)
    )


def test_scanner_flags_direct_one_liner():
    probe = (
        "def f(project):\n"
        "    return json.loads(\n"
        "        (ProjectLayout(project).state_json_path).read_text(encoding='utf-8')\n"
        "    )\n"
    )
    assert _scan_source(probe) == [2]


def test_scanner_flags_literal_path_one_liner():
    probe = (
        "def f(project):\n"
        "    p = project / '.methodology' / 'quality_manifest.json'\n"
        "    return json.loads(p.read_text(encoding='utf-8'))\n"
    )
    assert _scan_source(probe) == [3]


def test_scanner_flags_two_statement_chain():
    """The exact shape station 2c's own late discoveries were: a path
    assigned to a variable in one statement, read+parsed in a later one."""
    probe = (
        "def f(project):\n"
        "    manifest_path = project / '.methodology' / 'quality_manifest.json'\n"
        "    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))\n"
        "    return manifest\n"
    )
    assert _scan_source(probe) == [3]


def test_scanner_flags_with_open_chain():
    probe = (
        "def f(project):\n"
        "    state_path = project / '.methodology' / 'state.json'\n"
        "    with open(state_path) as fh:\n"
        "        return json.load(fh)\n"
    )
    assert _scan_source(probe) == [4]


def test_scanner_ignores_unrelated_json_parse():
    """Negative: json.loads on a path with no state/manifest marker at all."""
    probe = (
        "def f(project):\n"
        "    p = project / '.methodology' / 'enforcement.json'\n"
        "    return json.loads(p.read_text(encoding='utf-8'))\n"
    )
    assert _scan_source(probe) == []


def test_scanner_ignores_prose_mention_in_docstring():
    """Negative: 'state.json' appearing only in a comment/docstring, never
    inside a real Call node, can never trip the scanner — this is the
    whole reason it's AST-based rather than a text/regex scan."""
    probe = (
        "def f():\n"
        "    '''Reads state.json — see core/state_io.py for how.'''\n"
        "    # json.loads(state_path.read_text()) is the OLD, wrong way\n"
        "    return {}\n"
    )
    assert _scan_source(probe) == []


def test_scanner_respects_trailing_exempt_comment():
    probe = (
        "def f(project):\n"
        "    p = project / '.methodology' / 'state.json'\n"
        "    return json.loads(p.read_text(encoding='utf-8'))  # state-io-exempt: arbitrary path, see docstring\n"
    )
    assert _scan_source(probe) == []


def test_scanner_respects_leading_exempt_comment_block():
    probe = (
        "def f(project):\n"
        "    p = project / '.methodology' / 'state.json'\n"
        "    # state-io-exempt: arbitrary path, not project-rooted here\n"
        "    return json.loads(p.read_text(encoding='utf-8'))\n"
    )
    assert _scan_source(probe) == []


def test_scanner_exempt_comment_does_not_suppress_other_sites():
    """Negative: an exemption comment above one call must not accidentally
    suppress an unrelated violation elsewhere in the same function."""
    probe = (
        "def f(project):\n"
        "    p = project / '.methodology' / 'state.json'\n"
        "    # state-io-exempt: this one is fine\n"
        "    a = json.loads(p.read_text(encoding='utf-8'))\n"
        "    q = project / '.methodology' / 'quality_manifest.json'\n"
        "    b = json.loads(q.read_text(encoding='utf-8'))\n"
        "    return a, b\n"
    )
    assert _scan_source(probe) == [6]


def test_scanner_ignores_variable_reassigned_away_from_taint():
    """Negative: a variable tainted by a path assignment, then reassigned
    to something unrelated before being parsed, must not still be flagged."""
    probe = (
        "def f(project):\n"
        "    p = project / '.methodology' / 'state.json'\n"
        "    p = compute_something_else()\n"
        "    return json.loads(p.read_text(encoding='utf-8'))\n"
    )
    assert _scan_source(probe) == []


def test_scanner_ignores_nested_function_scope_leak():
    """Negative: taint inside an outer function must not leak into a
    nested function's own independent scope."""
    probe = (
        "def outer(project):\n"
        "    p = project / '.methodology' / 'state.json'\n"
        "    def inner(p):\n"
        "        return json.loads(p.read_text(encoding='utf-8'))\n"
        "    return inner\n"
    )
    assert _scan_source(probe) == []
