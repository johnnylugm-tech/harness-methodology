"""canonical_lint.py — flag non-canonical FR-IDs in source code.

Root cause (I of 5-meta-pattern convergence plan): FR-ID variants in source
code (e.g. `FR01`, `FR_01`, `FR(01)`) get normalized by 6 different sites in
`harness_cli.py`, but each site tolerates a slightly different variant set.
Source code itself never gets enforced — so a typo `FR01` slips through and
gets normalized downstream, hiding the bug.

This module is the source-code lint that closes the loop:
  - Flag occurrences of non-canonical FR-ID patterns
  - Suggest canonical form
  - Used by pre-commit hook (run on staged files)
  - Used by CI lint job

Patterns flagged (with the canonical form they would normalize to):
  - `FR01`       (no separator)  → `FR-01`
  - `FR_01`      (underscore)     → `FR-01`
  - `FR(01)`     (parens)         → `FR-01`
  - `FR 01`      (space)          → `FR-01`
  - `fr-01`      (lowercase)      → `FR-01`
  - Same for NFR/TASK prefixes

NOT flagged:
  - Canonical form `FR-01` (matches the canary regex)
  - Plain prose mentions (the regex requires TASK|FR|NFR followed by digits)
  - Comments referencing the canonical form

Commonality: framework-level. Used by pre-commit + CI + future editor plugins.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import NamedTuple

from core.canonical_form import canonical_form


# Patterns that look like FR-IDs but are NOT canonical
# Order matters: more specific patterns first (FR_01 before FR01, etc.)
# Each pattern captures (prefix, digits) so we can normalise.
_LINT_PATTERNS: tuple[re.Pattern[str], ...] = (
    # FR_01, FR_1, NFR_12, task_3 — underscore separator
    re.compile(r"\b(TASK|FR|NFR)_(\d+)\b", re.IGNORECASE),
    # FR(01), FR(1) — parens
    re.compile(r"\b(TASK|FR|NFR)\((\d+)\)"),
    # FR( 01 ) — parens with spaces inside
    re.compile(r"\b(TASK|FR|NFR)\(\s*(\d+)\s*\)"),
    # FR 01, fr 1, TASK 3 — whitespace separator
    re.compile(r"\b(TASK|FR|NFR)\s+(\d+)\b"),
    # FR01, fr1 — no separator (lowercase or uppercase prefix, immediately followed by digits)
    re.compile(r"\b(TASK|FR|NFR)(?=\d)", re.IGNORECASE),
    # fr-01, FR-01 — hyphen but lowercase prefix
    re.compile(r"\b(t|fr|nfr|task)-(\d+)\b"),
)


# A "canary" pattern for canonical forms that should NOT be flagged.
_CANONICAL_CANARY = re.compile(r"\b(?:TASK|FR|NFR)-\d{2,}\b")


class LintHit(NamedTuple):
    """A single non-canonical FR-ID occurrence."""

    file: Path
    line_no: int
    column: int
    matched_text: str
    suggested_canonical: str
    pattern_description: str


def lint_text(text: str, source_label: str | Path = "<text>") -> list[LintHit]:
    """Scan `text` for non-canonical FR-IDs; return list of LintHit.

    `source_label` is a Path or string identifying the source (for reporting).
    Used by lint_files() to label each hit with the source file.
    """
    hits: list[LintHit] = []
    source_path = Path(source_label) if not isinstance(source_label, str) else Path(source_label)
    seen_spans: set[tuple[int, int, int]] = set()  # (line_no, start, end)
    for line_no, line in enumerate(text.splitlines(), start=1):
        # Quick canary: skip lines containing canonical forms (likely correct)
        # but still scan for embedded non-canonical variants
        for pattern in _LINT_PATTERNS:
            for m in pattern.finditer(line):
                # Extract prefix + digits. Patterns with capture group for
                # digits (e.g. FR_(\d+)) have them in m.groups(). Patterns
                # using lookahead (e.g. FR(?=\d)) only capture the prefix;
                # for those, walk forward from m.end() to find the digit run.
                groups = [g for g in m.groups() if g]
                if len(groups) >= 2:
                    prefix, digits = groups[0], groups[1]
                    matched = m.group(0)
                else:
                    prefix = groups[0]
                    rest = line[m.end():]
                    digits_match = re.match(r"\d+", rest)
                    digits = digits_match.group(0) if digits_match else ""
                    # Extend matched to include the digit run so reported
                    # text shows the full non-canonical form (e.g. "FR01"
                    # not just "FR").
                    if digits:
                        matched = line[m.start():m.end() + len(digits)]
                    else:
                        matched = m.group(0)
                if not digits:
                    continue
                candidate = f"{prefix.upper()}-{int(digits):02d}"
                try:
                    canonical = canonical_form(candidate)
                except ValueError:
                    # The hit doesn't map to a canonical form — skip
                    continue
                # Only flag if the matched text is NOT already canonical
                if _CANONICAL_CANARY.match(matched):
                    continue
                # Dedupe: skip if a previous hit covers the same span
                if (line_no, m.start(), m.start() + len(matched)) in seen_spans:
                    continue
                seen_spans.add((line_no, m.start(), m.start() + len(matched)))
                hits.append(
                    LintHit(
                        file=source_path,
                        line_no=line_no,
                        column=m.start() + 1,
                        matched_text=matched,
                        suggested_canonical=canonical,
                        pattern_description=pattern.pattern,
                    )
                )
    return hits


def lint_files(paths: list[Path]) -> list[LintHit]:
    """Run lint_text() on each file in `paths`.

    Files that don't exist are silently skipped (callers should validate
    paths separately if they care).
    """
    all_hits: list[LintHit] = []
    for p in paths:
        if not p.is_file():
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        all_hits.extend(lint_text(text, source_label=p))
    return all_hits


def format_report(hits: list[LintHit]) -> str:
    """Format hits as a human-readable report."""
    if not hits:
        return "OK: no non-canonical FR-IDs found."
    lines = [f"Found {len(hits)} non-canonical FR-ID(s):"]
    for h in hits:
        rel = h.file
        try:
            rel = h.file.relative_to(Path.cwd())
        except ValueError:
            pass
        lines.append(
            f"  {rel}:{h.line_no}:{h.column}: "
            f"{h.matched_text!r} → {h.suggested_canonical!r} "
            f"(pattern: {h.pattern_description})"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _cli() -> int:
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        description="Flag non-canonical FR-IDs in source files."
    )
    parser.add_argument(
        "files",
        nargs="+",
        type=Path,
        help="Files to lint.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Only print on hits (no 'OK' message).",
    )
    args = parser.parse_args()

    hits = lint_files(args.files)
    if hits:
        print(format_report(hits), file=sys.stderr)
        return 1  # exit 1 = findings
    if not args.quiet:
        print(format_report(hits))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_cli())