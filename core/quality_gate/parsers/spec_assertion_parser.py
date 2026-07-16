"""SpecAssertionParser — assertion-level parser for TEST_SPEC.md.

Parses the milestone-4 schema additions only:
  * a per-FR case table with an `Inputs` column holding concrete values, e.g.
      | # | parametrize id | Inputs                          | Type | ... |
      | 8 | 和→ㄏㄢˋ        | source="和"; expected="ㄏㄢˋ"   | ...  | ... |
  * a per-FR Sub-assertion table:
      | rule_id            | predicate         | applies_to |
      | AC5-bopomofo-space | `" " in expected` | 3          |

This is deliberately separate from the two name-level parsers
(`harness_cli._parse_test_spec`, `harness_bridge._parse_spec_names_for_fr`),
which keep handling test-function-name coverage. This parser only feeds the
assertion-level self-consistency engine; it never reads any requirements source.

Returns plain `SpecCase` / `SubAssertion` objects (the engine's input models).
"""
from __future__ import annotations

import re

from core.quality_gate.red_assertion_check import SpecCase, SubAssertion

__all__ = ["SpecAssertionParser", "MalformedTableRowError"]


class MalformedTableRowError(ValueError):
    """A markdown table row started with '|' but did not end with '|'.

    Bug B fix (2026-07-07): `_rows_after_header` used to treat this the same
    as a genuine end-of-table line (anything not starting+ending with '|'),
    silently truncating every row after the malformed one. A single missing
    trailing '|' (e.g. from a truncated cell value) would then cascade into
    dropping the rest of the table, surfacing downstream as a wall of
    unrelated `unknown_case` violations instead of the actual formatting bug.
    """

_FR_HEADER = re.compile(r"^###\s+((?:N?FR)-\d+)\b")
_INPUT_KV = re.compile(r'(\w+)\s*=\s*"((?:[^"\\]|\\.)*)"')

_ESCAPED_PIPE = "\x00ESC_PIPE\x00"


def _split_row_cells(line: str) -> list:
    """Split a markdown table row on '|', honoring the '\\|' escape for a
    literal pipe inside a cell (standard markdown table syntax). Without
    this, a cell like `command="echo hi \\| wc"` splits into two cells at
    the escaped pipe, shifting every later column in the row."""
    protected = line.replace("\\|", _ESCAPED_PIPE)
    return [c.strip().replace(_ESCAPED_PIPE, "|") for c in protected.strip("|").split("|")]


def _is_separator(cells: list) -> bool:
    return bool(cells) and all(set(c) <= set("-: ") for c in cells if c != "")


def _find_col(header: list, *substrings: str) -> int | None:
    """Index of the first header cell whose lowercase contains all substrings."""
    for idx, col in enumerate(header):
        low = col.lower()
        if all(s in low for s in substrings):
            return idx
    return None


def _to_int(text: str) -> int | None:
    m = re.search(r"\d+", text)
    return int(m.group()) if m else None


def _parse_applies(text: str) -> list:
    return [int(m) for m in re.findall(r"\d+", text)]


class SpecAssertionParser:
    """Stateless parser for the assertion-level TEST_SPEC.md schema."""

    @staticmethod
    def parse(content: str) -> dict:
        """Return {fr_id: (cases, assertions)} for every FR with new-schema tables."""
        out: dict = {}
        for fr_id, body in SpecAssertionParser._split_fr_sections(content).items():
            cases = SpecAssertionParser._parse_cases(body)
            assertions = SpecAssertionParser._parse_subassertions(body)
            if cases or assertions:
                out[fr_id] = (cases, assertions)
        return out

    # ── section splitting ────────────────────────────────────────────────────
    @staticmethod
    def _split_fr_sections(content: str) -> dict:
        sections: dict = {}
        current: str | None = None
        buf: list = []
        for line in content.splitlines():
            m = _FR_HEADER.match(line.strip())
            if m:
                if current is not None:
                    sections[current] = "\n".join(buf)
                current = m.group(1)
                buf = []
                continue
            # A new H2 closes the current FR section.
            if line.strip().startswith("## ") and current is not None:
                sections[current] = "\n".join(buf)
                current = None
                buf = []
                continue
            if current is not None:
                buf.append(line)
        if current is not None:
            sections[current] = "\n".join(buf)
        return sections

    # ── generic table reader ─────────────────────────────────────────────────
    @staticmethod
    def _rows_after_header(body: str, *header_substrings: str):
        """Return (header_cells, [data_row_cells…]) for the first table whose
        header row contains every substring; ([], []) if none found."""
        lines = body.splitlines()
        for idx, line in enumerate(lines):
            s = line.strip()
            if not (s.startswith("|") and s.endswith("|")):
                continue
            cells = _split_row_cells(s)
            low = s.lower()
            if all(sub in low for sub in header_substrings):
                rows = []
                for j in range(idx + 1, len(lines)):
                    t = lines[j].strip()
                    if not t.startswith("|"):
                        break  # genuine end of table (blank line, next heading, prose)
                    if not t.endswith("|"):
                        raise MalformedTableRowError(
                            f"line {j + 1}: table row starts with '|' but does not "
                            f"end with '|' (truncated cell?): {t[:80]!r}"
                        )
                    rc = _split_row_cells(t)
                    if _is_separator(rc):
                        continue
                    rows.append(rc)
                return cells, rows
        return [], []

    # ── case table (needs an Inputs column) ──────────────────────────────────
    @staticmethod
    def _parse_cases(body: str) -> list:
        header, rows = SpecAssertionParser._rows_after_header(body, "inputs")
        if not header:
            return []
        idx_num = _find_col(header, "#")
        idx_in = _find_col(header, "inputs")
        if idx_num is None or idx_in is None:
            return []
        cases = []
        for cells in rows:
            if idx_num >= len(cells) or idx_in >= len(cells):
                continue
            num = _to_int(cells[idx_num])
            if num is None:
                continue
            inputs = dict(_INPUT_KV.findall(cells[idx_in]))
            cases.append(SpecCase(num, inputs))
        return cases

    # ── sub-assertion table ──────────────────────────────────────────────────
    @staticmethod
    def _parse_subassertions(body: str) -> list:
        header, rows = SpecAssertionParser._rows_after_header(
            body, "predicate", "applies_to")
        if not header:
            return []
        idx_rule = _find_col(header, "rule")
        idx_pred = _find_col(header, "predicate")
        idx_app = _find_col(header, "applies")
        if idx_pred is None or idx_app is None:
            return []
        assertions = []
        for cells in rows:
            if idx_pred >= len(cells) or idx_app >= len(cells):
                continue
            rule = cells[idx_rule].strip().strip("`") if idx_rule is not None and idx_rule < len(cells) else ""
            pred = cells[idx_pred].strip().strip("`").strip()
            if not pred:
                continue
            assertions.append(SubAssertion(rule, pred, _parse_applies(cells[idx_app])))
        return assertions
