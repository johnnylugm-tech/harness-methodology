"""Docstring-aware (and optionally comment-aware) source-code grep.

The naive `grep -E 'shell=True'` over Python source matches both the
forbidden call site AND docstrings/comments that mention the API as a
warning or example. Audits need to exclude those false positives.

This module provides:
  - strip_docstrings(text) → text with all triple-quoted strings removed
  - strip_comments(text)   → text with all '#' line comments removed
  - audit_grep(src_dir, pattern, ...) → list of Hit(path, line, text)

Pattern is a compiled regex; audit_grep matches line-by-line after
stripping the requested categories. Line numbers refer to the ORIGINAL
text so reports can cite real source positions.

Planned consumers (not yet wired up):
  - scripts/shell_audit.py (NFR-02 'no shell=True') — TODO
  - future audits for os.system / eval / pickle.loads / etc.
"""
from __future__ import annotations

import ast
import functools
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Pattern


# Line comment: '#' to end of line. We do NOT touch the leading '#!' of
# a shebang — that would corrupt the source on line 0.
_LINE_COMMENT = re.compile(r"#[^\n]*")


@dataclass(frozen=True)
class Hit:
    """A single match: source file path (relative to src_dir parent),
    1-based line number in the ORIGINAL text, and the matched line."""
    path: str
    line_no: int
    line_text: str


def _preserve_newlines(match: re.Match) -> str:
    """Replace every char of a match with a space, but keep '\\n' as '\\n'.
    This makes the strip lossless for line-number accounting: the resulting
    string has the same number of lines as the original, so `splitlines()`
    indices stay aligned with the source."""
    return "".join(c if c == "\n" else " " for c in match.group(0))


def _blank_spans(text: str, spans: list[tuple[int, int]]) -> str:
    """Blank out all [start, end) character offsets in *text* with spaces,
    preserving line structure so splitlines() indices stay aligned.

    Processes spans in descending order so earlier deletions don't shift
    later offsets in the string.
    """
    for start, end in sorted(spans, reverse=True):
        middle = text[start:end]
        blanked = "".join(c if c == "\n" else " " for c in middle)
        text = text[:start] + blanked + text[end:]
    return text


@functools.lru_cache(maxsize=256)
def strip_docstrings(text: str) -> str:
    """Return text with all docstrings removed (newlines preserved,
    non-newline chars replaced with spaces so line numbers stay aligned
    with the original source).

    Uses the AST to find docstrings, which correctly handles:
    - Multi-line triple-quoted strings containing " or '
    - Strings with embedded triple-quote sequences as literal content
    - Module, class, and function docstrings

    Unlike a regex heuristic, AST parsing respects Python's tokenisation
    rules and will never misidentify a closing delimiter.
    """
    if not text.strip():
        return text
    try:
        tree = ast.parse(text)
    except SyntaxError:
        # Syntactically invalid source (mid-edit) — fall back to no stripping
        # so we don't corrupt an incomplete file.
        return text

    spans: list[tuple[int, int]] = []

    class DocstringCollector(ast.NodeVisitor):
        def _record_docstring(
            self,
            node: ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef,
        ) -> None:
            doc = ast.get_docstring(node)
            # Only trust get_docstring — it correctly returns None for:
            #   - modules whose first statement is not a string literal
            #   - classes/functions whose body is empty or first stmt is not a string
            # Never re-detect based on raw Constant checks; that would produce false
            # positives on strings that happen to be the first body element.
            if doc is None or not node.body:
                return
            first = node.body[0]
            if not isinstance(first, ast.Expr):
                return
            # Convert line/column positions to absolute character offsets.
            # lineno/end_lineno are 1-based; splitlines(keepends=True) is
            # 0-indexed, so subtract 1 from lineno to get the right slice.
            lines = text.splitlines(keepends=True)
            first_lineno = first.lineno or 0
            first_end_lineno = first.end_lineno or 0
            first_end_col_offset = first.end_col_offset or 0
            start = (
                sum(len(ln) for ln in lines[: first_lineno - 1])
            ) + first.col_offset
            # end of the closing delimiter line + 1 to include its newline.
            end = (
                sum(len(ln) for ln in lines[: first_end_lineno - 1])
            ) + first_end_col_offset
            spans.append((start, end))

        def visit_Module(self, node: ast.Module) -> None:
            self._record_docstring(node)
            self.generic_visit(node)

        def visit_ClassDef(self, node: ast.ClassDef) -> None:
            self._record_docstring(node)
            self.generic_visit(node)

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self._record_docstring(node)
            self.generic_visit(node)

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            self._record_docstring(node)
            self.generic_visit(node)

    DocstringCollector().visit(tree)
    if not spans:
        return text
    return _blank_spans(text, spans)


def strip_comments(text: str) -> str:
    """Return text with all '#' line comments removed (newlines
    preserved, non-newline chars replaced with spaces so line numbers
    stay aligned with the original source)."""
    return _LINE_COMMENT.sub(_preserve_newlines, text)


def audit_grep(
    src_dir: Path,
    pattern: Pattern[str],
    *,
    exclude_docstrings: bool = True,
    exclude_comments: bool = False,
) -> list[Hit]:
    """Scan `src_dir` recursively for ``*.py`` files; return every Hit
    matching ``pattern``.

    ``pattern`` MUST be a compiled regex (use ``re.compile(...)``). Audits
    that need raw string patterns should compile them once at module
    level — never recompile per call.

    Line numbers refer to the ORIGINAL text so reports cite the
    actual offending source line, even when docstrings/comments are
    stripped for matching.

    Optimisation: the original text is scanned first; stripping (AST parsing)
    is only triggered when a hit is confirmed in the original text, avoiding
    costly AST work on clean files.
    """
    src_dir = Path(src_dir)
    if not src_dir.is_dir():
        return []

    hits: list[Hit] = []
    for py_file in sorted(src_dir.rglob("*.py")):
        if "__pycache__" in py_file.parts:
            continue
        try:
            text = py_file.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        # Pre-split once for original-line lookups (shared by all hits).
        text_lines = text.splitlines()
        needs_strip = exclude_docstrings or exclude_comments

        # Fast path: scan the original text first.  Only invoke the expensive
        # strip (AST parse + blank) when there is at least one confirmed hit.
        if needs_strip:
            hit_lines: set[int] = set()
            for line_no, line in enumerate(text_lines, 1):
                if pattern.search(line):
                    hit_lines.add(line_no)
            if not hit_lines:
                continue  # Clean file — skip stripping entirely.

            # Build stripped scan_text only for the files that have hits.
            scan_text = text
            if exclude_docstrings:
                scan_text = strip_docstrings(scan_text)
            if exclude_comments:
                scan_text = strip_comments(scan_text)
            # Re-scan stripped text against confirmed hit lines.
            scan_lines = scan_text.splitlines()
            for line_no in hit_lines:
                if line_no <= len(scan_lines) and pattern.search(scan_lines[line_no - 1]):
                    hits.append(Hit(
                        path=str(py_file),
                        line_no=line_no,
                        line_text=text_lines[line_no - 1].rstrip(),
                    ))
        else:
            # No stripping needed — scan original text directly.
            for line_no, line in enumerate(text_lines, 1):
                if pattern.search(line):
                    hits.append(Hit(
                        path=str(py_file),
                        line_no=line_no,
                        line_text=line.rstrip(),
                    ))
    return hits