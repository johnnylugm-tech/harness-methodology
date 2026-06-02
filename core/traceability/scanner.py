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
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple


# ---------------------------------------------------------------------------
# Regex constants and normalization
# ---------------------------------------------------------------------------

FR_TAG_PATTERN = re.compile(r'\[FR-(\d+)\]', re.IGNORECASE)
FR_SAD_PATTERN = re.compile(r'\bFR-(\d+)\b', re.IGNORECASE)
SAD_ROW_PATTERN = re.compile(r'FR-(\d+)[^\n]*?`([^`]+\.py)`')
TEST_FILENAME_PATTERN = re.compile(r'test_fr_?(\d+)', re.IGNORECASE)


def _norm_fr(num_str: str) -> str:
    """Normalize FR number to 2-digit zero-padded format."""
    return f"FR-{int(num_str):02d}"


def _skip_path(p: Path) -> bool:
    """Exclude virtualenvs, caches, and harness internals."""
    skip_tokens = {"venv", "__pycache__", ".sessi-work", ".methodology",
                   ".git", "node_modules", ".mypy_cache", ".pytest_cache",
                   ".ruff_cache", "dist", "build", "harness"}
    parts = set(p.parts)
    if parts & skip_tokens:
        return True
    return any(part.endswith(".egg-info") for part in p.parts)


def _find_sad(project: Path) -> Optional[Path]:
    """Locate SAD.md in canonical locations; returns None if absent."""
    for candidate in ("02-architecture/SAD.md", "SAD.md"):
        p = project / candidate
        if p.exists():
            return p
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


def scan_python_fr_annotations(project: Path) -> Dict[str, List[str]]:
    """Scan all .py files for [FR-XX] annotations. Returns {FR-XX: [file_path]}."""
    fr_to_files: Dict[str, List[str]] = {}
    for py_file in project.rglob("*.py"):
        if _skip_path(py_file):
            continue
        try:
            text = py_file.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        found = {_norm_fr(m) for m in FR_TAG_PATTERN.findall(text)}
        rel = str(py_file.relative_to(project))
        for fr_id in found:
            fr_to_files.setdefault(fr_id, []).append(rel)
    return fr_to_files


def scan_test_fr_coverage(tests_dir: Path) -> Dict[str, List[str]]:
    """Scan test files for FR references. Returns {FR-XX: [test_file]}.

    Project root is inferred as tests_dir.parent so returned paths are
    relative to the project (not the tests/ directory).
    """
    fr_to_tests: Dict[str, List[str]] = {}
    if not tests_dir.is_dir():
        return fr_to_tests
    project = tests_dir.parent
    for test_file in tests_dir.rglob("test_*.py"):
        name_match = TEST_FILENAME_PATTERN.match(test_file.name)
        if name_match:
            fr_id = _norm_fr(name_match.group(1))
            rel = str(test_file.relative_to(project))
            fr_to_tests.setdefault(fr_id, []).append(rel)
        try:
            text = test_file.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for m in FR_TAG_PATTERN.finditer(text):
            fr_id = _norm_fr(m.group(1))
            rel = str(test_file.relative_to(project))
            if rel not in fr_to_tests.get(fr_id, []):
                fr_to_tests.setdefault(fr_id, []).append(rel)
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
            if module not in fr_to_modules.get(fr_id, []):
                fr_to_modules.setdefault(fr_id, []).append(module)
    return fr_to_modules


# ---------------------------------------------------------------------------
# Combined scan: SAD + code + tests, return rich maps for the model layer.
# ---------------------------------------------------------------------------

def scan_all(
    project: Path,
    sad_path: Optional[Path] = None,
) -> Dict[str, object]:
    """Run all four scanners and return a flat dict of FR-keyed maps.

    Returns dict with keys:
      - sad_frs: List[str] — unique FRs found in SAD.md
      - fr_to_code: Dict[str, List[str]] — FR → source files containing [FR-XX]
      - fr_to_tests: Dict[str, List[str]] — FR → test files referencing FR
      - fr_to_modules: Dict[str, List[str]] — FR → modules per SAD table rows
      - all_frs: List[str] — union, sorted
      - ghost_frs: List[str] — in code/tests but not in SAD.md
    """
    if sad_path is None:
        sad_path = _find_sad(project)

    sad_frs = extract_fr_ids_from_sad(sad_path) if sad_path else []
    fr_to_code = scan_python_fr_annotations(project)
    fr_to_tests = scan_test_fr_coverage(project / "tests")
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

    Import is local so that scanner.py is importable without the model layer
    (and to keep the dependency arrow scanner → model, never reverse).
    """
    from core.requirement_traceability import (  # noqa: WPS433 (intentional late import)
        RequirementTraceability,
        TraceStatus,
    )

    scan = scan_all(project, sad_path=sad_path)
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
        if has_code and has_test:
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
