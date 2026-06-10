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

import json
import re
from pathlib import Path
from typing import Iterator, Union

DEFAULT_LANGUAGE = "python"

SOURCE_EXTENSIONS: dict[str, tuple[str, ...]] = {
    "python": (".py",),
    "javascript": (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"),
    "typescript": (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"),
}

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
    """Yield test files under *tests_dir* per the language convention."""
    if not tests_dir.is_dir():
        return
    if language == "python":
        yield from sorted(tests_dir.rglob("test_*.py"))
        return
    for path in sorted(tests_dir.rglob("*")):
        if path.is_file() and is_test_file(path, language):
            yield path


def iter_source_files(root: Path, language: str) -> Iterator[Path]:
    """Yield source files (any depth) with the language's extensions."""
    exts = source_extensions(language)
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.suffix.lower() in exts \
                and "node_modules" not in path.parts:
            yield path


def project_language(project_root: Union[str, Path]) -> str:
    """Persisted language from .methodology/state.json; pre-v2.8 → python."""
    state_path = Path(project_root) / ".methodology" / "state.json"
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
        lang = data.get("language") if isinstance(data, dict) else None
        return lang if isinstance(lang, str) and lang else DEFAULT_LANGUAGE
    except (OSError, json.JSONDecodeError):
        return DEFAULT_LANGUAGE
