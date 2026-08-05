"""Language file-pattern tables — single source for source/test file matching.

Lives in core/ (not harness/toolchains/) because core modules must not import
harness (dependency direction: core ← harness). harness.toolchains delegates
here, so the extension sets, test-file convention, and the state.json language
reader exist exactly once.

Conventions:
  python      source *.py;        tests tests/**/test_*.py
  javascript/ source *.js .jsx    tests tests/**/(*.test.*|*.spec.*|test_*.<ext>)
  typescript    .ts .tsx .mjs .cjs  (same test convention; harness requires
                it('test_*') / test('test_*') TITLES for D4 spec matching)
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterator, Union

from core.state_io import load_state

DEFAULT_LANGUAGE = "python"

SOURCE_EXTENSIONS: dict[str, tuple[str, ...]] = {
    "python": (".py",),
    "javascript": (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"),
    "typescript": (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"),
}

# Directories skipped when walking source trees of a project that is NOT a
# git repository. Inside a git repo the scope comes from
# core.utils.delivery_scope (Round 37) — git's own answer, which is the one
# CI checks out — and this denylist is not consulted. Kept as the non-git
# fallback: JS/TS build artifacts (dist, build, coverage, .next) and harness
# internals are excluded for every language. Paths whose name ends in
# .egg-info are also skipped (handled in scanner.py — Python-only suffix).
SKIP_DIRS: frozenset[str] = frozenset({
    "node_modules", "dist", "build", "coverage", ".next",
    ".sessi-work", ".methodology",
})

# vitest/jest convention (*.test.* / *.spec.*) plus the harness test_fr file
# naming used for traceability (test_fr01_x.test.ts also matches ^test_).
TEST_FILE_PATTERN = re.compile(r"(\.test\.|\.spec\.|^test_)", re.IGNORECASE)

# Extracts test titles that follow the harness naming convention from
# it('test_xxx', ...) / test("test_xxx", ...) / it.each(...)(`test_xxx`, ...).
JS_TEST_TITLE_PATTERN = re.compile(
    r"""\b(?:it|test)\b[\w.\s()\[\],]*?\(\s*['"`](test_\w+)""",
)


def source_extensions(language: str) -> tuple[str, ...]:
    return SOURCE_EXTENSIONS.get(language, SOURCE_EXTENSIONS[DEFAULT_LANGUAGE])


def is_test_file(path: Union[str, Path], language: str) -> bool:
    """True when *path* is a test file under the language's convention."""
    name = Path(path).name
    if language == "python":
        return name.startswith("test_") and name.endswith(".py")
    return (Path(path).suffix.lower() in source_extensions(language)
            and bool(TEST_FILE_PATTERN.search(name)))


def iter_test_files(tests_dir: Path, language: str) -> Iterator[Path]:
    """Yield test files under *tests_dir* per the language convention.

    Scoped to what the project delivers (Round 37) so a scratch copy of the
    test tree — an agent worktree, a stale build output — cannot be counted
    as coverage.
    """
    from core.utils.delivery_scope import iter_delivered_files

    if not tests_dir.is_dir():
        return
    for path in iter_delivered_files(tests_dir):
        if language == "python":
            if path.name.startswith("test_") and path.suffix == ".py":
                yield path
        elif is_test_file(path, language):
            yield path


def iter_source_files(root: Path, language: str) -> Iterator[Path]:
    """Yield source files (any depth) with the language's extensions.

    The file population comes from core.utils.delivery_scope — git's answer
    inside a git repo, the SKIP_DIRS denylist outside one (Round 37). This
    function only applies the language's extension filter on top.
    """
    from core.utils.delivery_scope import iter_delivered_files

    exts = source_extensions(language)
    for path in iter_delivered_files(root):
        if path.suffix.lower() in exts:
            yield path


def project_language(project_root: Union[str, Path]) -> str:
    """Persisted language from .methodology/state.json; pre-v2.8 → python."""
    lang = load_state(project_root, lenient=True).get("language")
    return lang if isinstance(lang, str) and lang else DEFAULT_LANGUAGE
