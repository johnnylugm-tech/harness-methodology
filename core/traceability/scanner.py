"""Traceability scanner — single source of regex + scan functions.

This module is intentionally pure: the scan functions parse strings and walk
the filesystem, but they DO NOT import `RequirementTraceability`. The model
constructor lives in `core.requirement_traceability` and is consumed by
`check_traceability` (a thin wrapper at the bottom of this file) and by the
caller scripts (`scripts/build_traceability.py`, `scripts/check_spec_trace.py`).

Design constraint: scanner.py must not import the model directly, to keep
imports one-way (`scanner` → `requirement_traceability` is fine, but the
reverse would create a cycle if `requirement_traceability.py` ever needed
regex constants).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple
from core.utils.lang_patterns import (
    SKIP_DIRS,
    iter_source_files,
    iter_test_files,
    project_language,
)
from core.utils.project_layout import ProjectLayout
from core.quality_gate.parsers.nfr_id_pattern import normalize_nfr_id


# ---------------------------------------------------------------------------
# Regex constants and normalization
# ---------------------------------------------------------------------------

FR_TAG_PATTERN = re.compile(r'\[FR-(\d+)\]', re.IGNORECASE)
FR_SAD_PATTERN = re.compile(r'\bFR-(\d+)\b', re.IGNORECASE)
# SAD module rows may reference source files in any supported language.
# (?<!N) keeps NFR table rows citing files (e.g. "| NFR-06 | ... `config.py` |")
# from becoming phantom FR→module mappings — same fix form as 90e35b2's
# DriftDetector.SAD_FR_PATTERN; parity-locked by tests/test_fr_token_parity.py.
SAD_ROW_PATTERN = re.compile(r'(?<!N)FR-(\d+)[^\n]*?`([^`]+\.(?:py|jsx?|tsx?|mjs|cjs))`')
TEST_FILENAME_PATTERN = re.compile(r'test_fr_?(\d+)', re.IGNORECASE)


def _norm_fr(num_str: str) -> str:
    """Normalize FR number to 2-digit zero-padded format."""
    return f"FR-{int(num_str):02d}"


def _skip_path(p: Path) -> bool:
    """Exclude virtualenvs, caches, and harness internals.

    SKIP_DIRS (lang_patterns) is the shared cross-language set; the extras
    below are Python-only tooling caches.
    """
    python_only = {"venv", "__pycache__", ".git", ".mypy_cache",
                   ".pytest_cache", ".ruff_cache", "harness"}
    parts = set(p.parts)
    if parts & (SKIP_DIRS | python_only):
        return True
    return any(part.endswith(".egg-info") for part in p.parts)


def _test_function_ranges(text: str) -> List[Tuple[str, int, int]]:
    """Return (qualified_name, start_line, end_line) for every `test_*`
    function, 1-indexed and inclusive. `qualified_name` is
    `"ClassName.method_name"` for a method inside a `class Test...:` block,
    or the bare function name at module level — matching how pytest's own
    JUnit XML `classname` distinguishes the two (a trailing class-name
    segment beyond the module path). A flat `ast.walk()` would lose this
    distinction (and would collide two same-named methods in two different
    classes), which is why this walks `.body` explicitly instead.

    The range starts at the `def` line itself (so a same-line trailing
    comment like `def test_x():  # NFR-08` is included) through the last
    line of the function body (so a requirement ID in the docstring is
    included too). Returns [] on a SyntaxError (e.g. a non-Python test
    file) — callers must treat that as "no functions found", never as a
    parse failure to propagate.
    """
    import ast

    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    ranges: List[Tuple[str, int, int]] = []

    def walk(body: List[ast.stmt], class_prefix: str) -> None:
        for node in body:
            if isinstance(node, ast.ClassDef):
                walk(node.body, class_prefix + node.name + ".")
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name.startswith("test_"):
                    end = getattr(node, "end_lineno", None) or node.lineno
                    ranges.append((class_prefix + node.name, node.lineno, end))
                # Deliberately not recursing into a function's own body —
                # a nested `def` inside a test function/fixture is not a
                # separately-collected test case.

    walk(tree.body, "")
    return ranges


def _function_has_any_passing_test(
    rel: str, qualified_name: str, test_outcomes: Dict[str, str]
) -> bool:
    """True if `qualified_name` (bare or "ClassName.method") passed at least
    once. Matches both the plain key (`"<rel>::<qualified_name>"`) and any
    `@pytest.mark.parametrize` variant (`"<rel>::<qualified_name>[...]"`,
    pytest's own bracket-suffixed id) — a bare function-name lookup would
    otherwise never match ANY parametrized test, since test_outcomes only
    ever holds the bracketed per-case ids, never the bare name.
    """
    key = f"{rel}::{qualified_name}"
    if test_outcomes.get(key) == "passed":
        return True
    prefix = key + "["
    return any(
        status == "passed"
        for outcome_key, status in test_outcomes.items()
        if outcome_key.startswith(prefix)
    )


def _file_has_any_passing_test(rel: str, test_outcomes: Dict[str, str]) -> bool:
    """True if at least one `"<rel>::<name>"` key in test_outcomes passed."""
    prefix = f"{rel}::"
    return any(
        status == "passed"
        for key, status in test_outcomes.items()
        if key.startswith(prefix)
    )


def _function_outcome(
    rel: str, qualified_name: str, test_outcomes: Dict[str, str]
) -> str:
    """The one outcome to report for a function that did not pass.

    Same key shapes as `_function_has_any_passing_test` — plain and
    parametrized. When a parametrized function has several non-passing cases
    the worst one is reported ("failed" over "error" over "skipped" is not a
    ranking anyone needs; alphabetical `min` is stable and the names are
    self-explanatory). "missing" means the function exists in the file but
    pytest reported no case for it at all — it was never collected.
    """
    key = f"{rel}::{qualified_name}"
    if key in test_outcomes:
        return test_outcomes[key]
    prefix = key + "["
    variants = sorted(
        status for k, status in test_outcomes.items() if k.startswith(prefix)
    )
    return variants[0] if variants else "missing"


def _absent_witnesses(
    tests_dir: Path,
    project: Path,
    language: Optional[str],
    test_outcomes: Dict[str, str],
    extract_ids,
) -> Dict[str, List[str]]:
    """`{req_id: ["<rel>::<func> (<outcome>)", ...]}` for every test function
    that names a requirement and did not pass.

    Round 46 站1. The two coverage scanners below already walk exactly these
    functions and already ask `_function_has_any_passing_test`; when the
    answer is no they `continue`, and the function is gone. Credit is granted
    per FILE, so one passing sibling makes the whole file the requirement's
    witness and the ones that did not run are never named by anything.

    taskq-advance shipped Gate 4 with NFR-07 VERIFIED because
    `test_licenses_in_allowlist` passed, while `test_sbom_license_field` and
    `test_license_file_exists` — in the same file, testing the parts of
    NFR-07 that were actually violated — skipped themselves with "SBOM.json
    not found" and "LICENSE missing".

    `extract_ids(rel, filename, segment) -> set[str]` is the only difference
    between the FR and NFR walks, so it is the only thing passed in.
    """
    absent: Dict[str, List[str]] = {}
    if not tests_dir or not tests_dir.is_dir():
        return absent
    for test_file in iter_test_files(tests_dir, language or project_language(project)):
        try:
            text = test_file.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            print(f"[WARN] traceability scanner: could not read {test_file}, "
                  f"skipping it: {exc}", file=sys.stderr)
            continue
        rel = str(test_file.relative_to(project))
        lines = text.splitlines()
        for func_name, start, end in _test_function_ranges(text):
            segment = "\n".join(lines[start - 1:end])
            found = extract_ids(rel, test_file.name, segment)
            if not found:
                continue
            if _function_has_any_passing_test(rel, func_name, test_outcomes):
                continue
            outcome = _function_outcome(rel, func_name, test_outcomes)
            entry = f"{rel}::{func_name} ({outcome})"
            for req_id in found:
                if entry not in absent.setdefault(req_id, []):
                    absent[req_id].append(entry)
    for lst in absent.values():
        lst.sort()
    return absent


def scan_test_fr_absent_witnesses(
    tests_dir: Path,
    test_outcomes: Dict[str, str],
    project_root: Path,
    language: Optional[str] = None,
) -> Dict[str, List[str]]:
    """FR-side companion to `scan_test_fr_coverage` — the functions it drops.

    An FR is claimed by a `[FR-XX]` reference inside the function *or* by the
    file's own `test_frNN.py` name, matching the two credit paths in
    `scan_test_fr_coverage`.
    """
    def _ids(_rel: str, filename: str, segment: str) -> Set[str]:
        ids: Set[str] = set()
        for m in re.finditer(r'\[\s*((?:FR-\d+(?:,\s*)?)+)\s*\]', segment, re.IGNORECASE):
            for inner in re.finditer(r'FR-(\d+)', m.group(1), re.IGNORECASE):
                ids.add(_norm_fr(inner.group(1)))
        name_match = TEST_FILENAME_PATTERN.match(filename)
        if name_match:
            ids.add(_norm_fr(name_match.group(1)))
        return ids

    return _absent_witnesses(
        tests_dir, project_root, language, test_outcomes, _ids)


def scan_test_nfr_absent_witnesses(
    tests_dir: Path,
    test_outcomes: Dict[str, str],
    project_root: Path,
) -> Dict[str, List[str]]:
    """NFR-side companion to `scan_test_nfr_coverage` — the functions it drops."""
    def _ids(_rel: str, _filename: str, segment: str) -> Set[str]:
        return {
            normalize_nfr_id(f"NFR-{m.group(1)}") or ""
            for m in NFR_PATTERN.finditer(segment)
        } - {""}

    return _absent_witnesses(
        tests_dir, project_root, None, test_outcomes, _ids)


def _find_sad(project: Path) -> Optional[Path]:
    """Locate SAD.md in canonical locations; returns None if absent."""
    layout = ProjectLayout(project)
    if layout.sad_path.exists():
        return layout.sad_path
    if (project / "SAD.md").exists():
        return project / "SAD.md"
    return None


# ---------------------------------------------------------------------------
# Scanners (pure functions; no model dependency)
# ---------------------------------------------------------------------------

def extract_fr_ids_from_sad(sad_path: Path) -> List[str]:
    """Extract all unique FR-XX IDs from SAD.md, zero-padded to 2 digits."""
    if not sad_path.exists():
        return []
    text = sad_path.read_text(encoding="utf-8", errors="replace")
    ids = {_norm_fr(m) for m in FR_SAD_PATTERN.findall(text)}
    return sorted(ids)


def scan_fr_annotations(
    project: Path, language: Optional[str] = None
) -> Dict[str, List[str]]:
    """Scan source files for [FR-XX] annotations. Returns {FR-XX: [file_path]}.

    The [FR-XX] tag is comment-style agnostic (# / // / /* */); only the file
    extension set varies per language (state.json `language`, default python).
    """
    language = language or project_language(project)
    fr_to_files: Dict[str, List[str]] = {}
    for src_file in iter_source_files(project, language):
        if _skip_path(src_file):
            continue
        try:
            text = src_file.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            print(f"[WARN] traceability scanner: could not read {src_file}, "
                  f"skipping it: {exc}", file=sys.stderr)
            continue
        found = set()
        for m in re.finditer(r'\[\s*((?:FR-\d+(?:,\s*)?)+)\s*\]', text, re.IGNORECASE):
            for inner_m in re.finditer(r'FR-(\d+)', m.group(1), re.IGNORECASE):
                found.add(_norm_fr(inner_m.group(1)))
        rel = str(src_file.relative_to(project))
        for fr_id in found:
            if rel not in fr_to_files.setdefault(fr_id, []):
                fr_to_files[fr_id].append(rel)
    for lst in fr_to_files.values():
        lst.sort()
    return fr_to_files


# Backward-compatible alias (pre-v2.8 name).
scan_python_fr_annotations = scan_fr_annotations


def scan_test_fr_coverage(
    tests_dir: Path,
    language: Optional[str] = None,
    test_outcomes: Optional[Dict[str, str]] = None,
    project_root: Optional[Path] = None,
) -> Dict[str, List[str]]:
    """Scan test files for FR references. Returns {FR-XX: [test_file]}.

    Project root defaults to tests_dir.parent so returned paths are
    relative to the project (not the tests/ directory) — correct for a
    flat `<root>/tests` layout, but WRONG for a nested layout like
    `<root>/03-development/tests` (tests_dir.parent is `03-development`,
    not `<root>`). Pass `project_root` explicitly whenever `test_outcomes`
    is also passed: the returned relative paths must exactly match the
    "file::name" keys in test_outcomes, which are always relative to the
    true project root (where run_suite's pytest subprocess actually ran) —
    a mismatch here silently empties every result instead of raising.

    `test_outcomes` (from `core.quality_gate.test_suite_run.run_suite(...)
    .test_outcomes`) makes this outcome-aware: a requirement mentioned only
    inside a test function that was skipped/failed does NOT count as
    coverage — only a mention inside a function whose own outcome is
    "passed" does (matching NFR-09's own rule: VERIFIED requires the test to
    have "actually ran and passed", not merely exist). `None` (no outcome
    data — e.g. a non-Python project, since run_suite only measures Python
    today) preserves the previous presence-only behavior, so callers
    without live run data are unaffected.

    Raises ValueError if `test_outcomes` is given without `project_root` —
    silently falling back to `tests_dir.parent` there produced empty
    results for every nested-layout project instead of a loud error.
    """
    if test_outcomes is not None and project_root is None:
        raise ValueError(
            "scan_test_fr_coverage: project_root is required when test_outcomes "
            "is provided (tests_dir.parent is only correct for a flat <root>/tests "
            "layout; a nested layout needs the true root to compute matching keys)."
        )
    fr_to_tests: Dict[str, List[str]] = {}
    if not tests_dir.is_dir():
        return fr_to_tests
    project = project_root if project_root is not None else tests_dir.parent
    language = language or project_language(project)
    for test_file in iter_test_files(tests_dir, language):
        rel = str(test_file.relative_to(project))
        try:
            text = test_file.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            print(f"[WARN] traceability scanner: could not read {test_file}, "
                  f"skipping it: {exc}", file=sys.stderr)
            text = ""

        name_match = TEST_FILENAME_PATTERN.match(test_file.name)
        if name_match:
            covered = (
                _file_has_any_passing_test(rel, test_outcomes)
                if test_outcomes is not None else True
            )
            if covered:
                fr_id = _norm_fr(name_match.group(1))
                if rel not in fr_to_tests.setdefault(fr_id, []):
                    fr_to_tests[fr_id].append(rel)

        if not text:
            continue

        if test_outcomes is None:
            for m in re.finditer(r'\[\s*((?:FR-\d+(?:,\s*)?)+)\s*\]', text, re.IGNORECASE):
                for inner_m in re.finditer(r'FR-(\d+)', m.group(1), re.IGNORECASE):
                    fr_id = _norm_fr(inner_m.group(1))
                    if rel not in fr_to_tests.setdefault(fr_id, []):
                        fr_to_tests[fr_id].append(rel)
            continue

        lines = text.splitlines()
        for func_name, start, end in _test_function_ranges(text):
            segment = "\n".join(lines[start - 1:end])
            found_ids: Set[str] = set()
            for m in re.finditer(r'\[\s*((?:FR-\d+(?:,\s*)?)+)\s*\]', segment, re.IGNORECASE):
                for inner_m in re.finditer(r'FR-(\d+)', m.group(1), re.IGNORECASE):
                    found_ids.add(_norm_fr(inner_m.group(1)))
            if not found_ids:
                continue
            if not _function_has_any_passing_test(rel, func_name, test_outcomes):
                continue
            for fr_id in found_ids:
                if rel not in fr_to_tests.setdefault(fr_id, []):
                    fr_to_tests[fr_id].append(rel)
    for lst in fr_to_tests.values():
        lst.sort()
    return fr_to_tests


def scan_sad_fr_modules(sad_path: Path) -> Dict[str, List[str]]:
    """Extract FR→module mappings from SAD.md component table rows.

    Matches patterns like:
      | `module.py` | FR-01 | ...
      FR-01 → `module.py`
    """
    fr_to_modules: Dict[str, List[str]] = {}
    if not sad_path.exists():
        return fr_to_modules
    text = sad_path.read_text(encoding="utf-8", errors="replace")
    for line in text.splitlines():
        if "|" not in line:
            continue
        for m in SAD_ROW_PATTERN.finditer(line):
            fr_id = _norm_fr(m.group(1))
            module = m.group(2)
            if module not in fr_to_modules.setdefault(fr_id, []):
                fr_to_modules[fr_id].append(module)
    for lst in fr_to_modules.values():
        lst.sort()
    return fr_to_modules


NFR_PATTERN = re.compile(r'\bNFR-(\d+)\b', re.IGNORECASE)


def extract_nfr_ids_from_srs(srs_path: Optional[Path]) -> Set[str]:
    """Return set of NFR-XX IDs found in SRS.md."""
    if not srs_path or not srs_path.exists():
        return set()
    text = srs_path.read_text(encoding="utf-8", errors="replace")
    ids = (normalize_nfr_id(f"NFR-{m.group(1)}") for m in NFR_PATTERN.finditer(text))
    return {i for i in ids if i}


def scan_test_nfr_coverage(
    tests_dir: Path,
    test_outcomes: Optional[Dict[str, str]] = None,
    project_root: Optional[Path] = None,
) -> Dict[str, List[str]]:
    """Return {NFR-XX: [relative_test_file, ...]} for NFR mentions in test files.

    Project root defaults to tests_dir.parent (correct for a flat
    `<root>/tests` layout, WRONG for a nested layout like
    `<root>/03-development/tests`). Pass `project_root` explicitly whenever
    `test_outcomes` is also passed — see `scan_test_fr_coverage`'s docstring
    for why a mismatch here silently empties every result.

    `test_outcomes` (from `core.quality_gate.test_suite_run.run_suite(...)
    .test_outcomes`) makes this outcome-aware: an NFR mentioned only inside
    a test function that was skipped/failed does NOT count as coverage —
    only a mention inside a function whose own outcome is "passed" does.
    This is the direct fix for the bug NFR-09 itself describes: a
    `pytest.skip()` stub whose docstring cites "NFR-08" used to count as
    full coverage regardless of whether it ever ran. `None` (no outcome
    data) preserves the previous presence-only behavior.

    Raises ValueError if `test_outcomes` is given without `project_root` —
    see `scan_test_fr_coverage`'s docstring for why.
    """
    if test_outcomes is not None and project_root is None:
        raise ValueError(
            "scan_test_nfr_coverage: project_root is required when test_outcomes "
            "is provided (tests_dir.parent is only correct for a flat <root>/tests "
            "layout; a nested layout needs the true root to compute matching keys)."
        )
    nfr_to_tests: Dict[str, List[str]] = {}
    if not tests_dir or not tests_dir.is_dir():
        return nfr_to_tests
    project = project_root if project_root is not None else tests_dir.parent
    for test_file in iter_test_files(tests_dir, project_language(project)):
        try:
            text = test_file.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            print(f"[WARN] traceability scanner: could not read {test_file}, "
                  f"skipping it: {exc}", file=sys.stderr)
            continue
        rel = str(test_file.relative_to(project))

        if test_outcomes is None:
            for m in NFR_PATTERN.finditer(text):
                nfr_id = f"NFR-{int(m.group(1)):02d}"
                if rel not in nfr_to_tests.setdefault(nfr_id, []):
                    nfr_to_tests[nfr_id].append(rel)
            continue

        lines = text.splitlines()
        for func_name, start, end in _test_function_ranges(text):
            segment = "\n".join(lines[start - 1:end])
            found_ids = {f"NFR-{int(m.group(1)):02d}" for m in NFR_PATTERN.finditer(segment)}
            if not found_ids:
                continue
            if not _function_has_any_passing_test(rel, func_name, test_outcomes):
                continue
            for nfr_id in found_ids:
                if rel not in nfr_to_tests.setdefault(nfr_id, []):
                    nfr_to_tests[nfr_id].append(rel)
    for lst in nfr_to_tests.values():
        lst.sort()
    return nfr_to_tests


# ---------------------------------------------------------------------------
# Combined scan: SAD + code + tests, return rich maps for the model layer.
# ---------------------------------------------------------------------------

def scan_all(
    project: Path,
    sad_path: Optional[Path] = None,
    test_outcomes: Optional[Dict[str, str]] = None,
) -> Dict[str, object]:
    """Run all four scanners and return a flat dict of FR-keyed maps.

    Returns dict with keys:
      - sad_frs: List[str] — unique FRs found in SAD.md
      - fr_to_code: Dict[str, List[str]] — FR → source files containing [FR-XX]
      - fr_to_tests: Dict[str, List[str]] — FR → test files referencing FR
      - fr_to_modules: Dict[str, List[str]] — FR → modules per SAD table rows
      - all_frs: List[str] — union, sorted
      - ghost_frs: List[str] — in code/tests but not in SAD.md

    `test_outcomes` (see `scan_test_fr_coverage`) makes the test-coverage
    scan outcome-aware; `None` preserves the previous presence-only
    behavior.
    """
    if sad_path is None:
        sad_path = _find_sad(project)

    language = project_language(project)
    sad_frs = extract_fr_ids_from_sad(sad_path) if sad_path else []
    fr_to_code = scan_fr_annotations(project, language)
    test_dir = ProjectLayout(project).active_test_dir
    fr_to_tests = scan_test_fr_coverage(
        test_dir, language, test_outcomes=test_outcomes, project_root=project
    )
    fr_to_modules = scan_sad_fr_modules(sad_path) if sad_path else {}

    coded = set(fr_to_code.keys())
    tested = set(fr_to_tests.keys())
    all_frs = sorted(set(sad_frs) | coded | tested)
    ghost_frs = sorted((coded | tested) - set(sad_frs))

    return {
        "sad_frs": sad_frs,
        "fr_to_code": fr_to_code,
        "fr_to_tests": fr_to_tests,
        "fr_to_modules": fr_to_modules,
        "all_frs": all_frs,
        "ghost_frs": ghost_frs,
    }


# ---------------------------------------------------------------------------
# check_traceability — thin wrapper that consumes scan_all() and the model.
# This is the only place scanner.py touches RequirementTraceability.
# ---------------------------------------------------------------------------

def check_traceability(
    project: Path,
    sad_path: Optional[Path] = None,
) -> Tuple[Any, Dict]:
    """Content-level FR → code → test check; returns (model, report).

    Imports are local so that scanner.py is importable without the model
    layer (and to keep the dependency arrow scanner → model, never
    reverse), and so a non-Python project (test_suite_run.run_suite only
    measures Python) never pays for an import it cannot use.
    """
    from core.requirement_traceability import (  # noqa: WPS433 (intentional late import)
        RequirementTraceability,
        TraceStatus,
    )
    from core.quality_gate.test_suite_run import run_suite  # noqa: WPS433

    # Defect A fix: outcome-aware coverage (see scan_test_fr_coverage's own
    # docstring). run_suite is memoized per-process (Round 25 SSOT), so this
    # reuses whatever measurement the current Gate evaluation already took.
    suite_result = run_suite(project)
    # _parse_junit_outcomes returns {} on parse failure OR when pytest's
    # collection phase aborted (its own classname is empty so the parser
    # skips it). Per its docstring callers must treat {} as "no outcome
    # data available", otherwise the outcome-aware scanner would report
    # 0% FR coverage on a project whose tests cannot even be collected,
    # masking the real failure behind a spurious traceability miss.
    test_outcomes = (
        suite_result.test_outcomes
        if (suite_result.ran and suite_result.test_outcomes)
        else None
    )

    scan = scan_all(project, sad_path=sad_path, test_outcomes=test_outcomes)
    sad_frs: List[str] = scan["sad_frs"]  # type: ignore[assignment]
    fr_to_code: Dict[str, List[str]] = scan["fr_to_code"]  # type: ignore[assignment]
    fr_to_tests: Dict[str, List[str]] = scan["fr_to_tests"]  # type: ignore[assignment]
    fr_to_modules: Dict[str, List[str]] = scan["fr_to_modules"]  # type: ignore[assignment]
    all_frs: List[str] = scan["all_frs"]  # type: ignore[assignment]
    ghost_frs: List[str] = scan["ghost_frs"]  # type: ignore[assignment]

    coded: Set[str] = set(fr_to_code.keys())
    tested: Set[str] = set(fr_to_tests.keys())
    untested = [fr for fr in sad_frs if fr not in tested]
    uncoded = [fr for fr in sad_frs if fr not in coded]
    fr_absent = (
        scan_test_fr_absent_witnesses(
            ProjectLayout(project).active_test_dir, test_outcomes, project)
        if test_outcomes is not None else {}
    )

    rt = RequirementTraceability(project_id=project.resolve().name)
    for fr_id in all_frs:
        has_code = fr_id in coded
        has_test = fr_id in tested
        # F-2.2 fix: align with build_traceability — `has_module` (SAD
        # table row mapping) is sufficient to mark an FR IN_PROGRESS.
        # Without this, an FR with only a module-table entry falls
        # through to PENDING here, but build_traceability marks it
        # IN_PROGRESS, causing the two reports to disagree on the
        # active FR denominator. The 4a pct computation (which uses
        # the scanner's status filter) then drops the FR from the
        # denominator entirely, masking the gap.
        has_module = fr_id in fr_to_modules
        # Round 46 站1: kept in step with build_traceability's copy above —
        # an FR whose claiming test did not run is IN_PROGRESS, not VERIFIED.
        if has_code and has_test and fr_id not in fr_absent:
            status = TraceStatus.VERIFIED
        elif has_code or has_module:
            status = TraceStatus.IN_PROGRESS
        elif fr_id in sad_frs:
            status = TraceStatus.PENDING
        else:
            status = TraceStatus.NOT_IMPLEMENTED

        srs_section = "SAD.md" if fr_id in sad_frs else None
        rt.add_requirement(
            req_id=fr_id,
            title=f"Requirement {fr_id}",
            srs_section=srs_section,
            description="",
            priority="HIGH",
            metadata={
                "code_files": fr_to_code.get(fr_id, []),
                "test_files": fr_to_tests.get(fr_id, []),
            },
        )
        rt.requirements[fr_id].status = status
        for fp in fr_to_code.get(fr_id, []):
            rt.add_code_component(file_path=fp, fr_id=fr_id)
        for tf in fr_to_tests.get(fr_id, []):
            rt.add_test_coverage(test_file=tf, fr_id=fr_id)

    report = {
        "total": len(all_frs),
        "sad_frs": len(sad_frs),
        "coded": len(coded),
        "tested": len(tested),
        "untested": untested,
        "uncoded": uncoded,
        "ghost_frs": ghost_frs,
        "complete": len(untested) == 0 and len(uncoded) == 0,
        "completeness": rt.verify_completeness(),
    }
    return rt, report
