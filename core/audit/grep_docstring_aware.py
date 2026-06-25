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

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Pattern


# Triple-quoted string literals: """...""" or '''...''' with lazy
# matching so the first closing fence terminates the strip.
_TRIPLE_DOUBLE = re.compile(r'"""[\s\S]*?"""', re.MULTILINE)
_TRIPLE_SINGLE = re.compile(r"'''[\s\S]*?'''", re.MULTILINE)

# Line comment: '#' to end of line. We do NOT touch the leading '#!' of
# a shebang — that's not Python-comment syntax anyway (regex would still
# strip it, but a shebang on line 0 has no semantic effect on audits).
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


def strip_docstrings(text: str) -> str:
    """Return text with all triple-quoted strings removed (newlines
    preserved, non-newline chars replaced with spaces so line numbers
    stay aligned with the original source)."""
    out = _TRIPLE_DOUBLE.sub(_preserve_newlines, text)
    out = _TRIPLE_SINGLE.sub(_preserve_newlines, out)
    return out


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
    """Scan `src_dir` recursively for `*.py` files; return every Hit
    matching `pattern`.

    `pattern` MUST be a compiled regex (use `re.compile(...)`). Audits
    that need raw string patterns should compile them once at module
    level — never recompile per call.

    Line numbers refer to the ORIGINAL text so reports cite the
    actual offending source line, even when docstrings/comments are
    stripped for matching.
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

        # Strip for matching; preserve original text for line reporting.
        scan_text = text
        if exclude_docstrings:
            scan_text = strip_docstrings(scan_text)
        if exclude_comments:
            scan_text = strip_comments(scan_text)

        for line_no, line in enumerate(scan_text.splitlines(), 1):
            if pattern.search(line):
                # Report the original line (preserves line numbers).
                original_line = text.splitlines()[line_no - 1] \
                    if line_no - 1 < len(text.splitlines()) else line
                hits.append(Hit(
                    path=str(py_file),
                    line_no=line_no,
                    line_text=original_line.rstrip(),
                ))
    return hits