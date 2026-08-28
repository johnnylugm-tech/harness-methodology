#!/usr/bin/env python3
"""Lift the ledger's "明列不做" decisions into something a program can read.

Round 81 站5. `docs/PROPOSAL_ADJUDICATIONS.md` opens by stating its own
protocol: consult it before re-opening a question, and reject a claim by entry
number when the re-open condition is unmet. Round 80 is that protocol's own
counterexample — three of its opening findings had been adjudicated by R74 站3
and R77 站4, were re-derived from scratch, and were withdrawn — and it recorded
the reason it did not build this index:

    23+ 條散在 5500 行散文裡,提取有把話塞進前幾輪嘴裡的實際風險
    re-open: 有人願意逐條與原文對照地做一次提取,或賬本改為結構化寫入

That risk belongs to a REWRITING extraction. This one copies. Every field it
emits is a byte-exact slice of the ledger, and tests/test_deferred_index.py
asserts exactly that — so the index cannot state anything the ledger does not,
not because the author was careful but because a rewrite makes the guard red.

WHAT IT DOES NOT DO

It does not classify, summarise, translate, or decide whether an entry is still
open. A section whose shape it does not recognise is emitted as
`kind: unstructured` carrying its line range and NO extracted fields — an
explicit hole rather than a guess (Round 46: an absent witness is not a failed
testimony). Fitting the parser to the corpus until every section yields
something is the Round 55 shape and is the failure mode this file most has to
avoid.

The `guard:` mapping is deliberately NOT here. It is a judgement — which test,
if any, already measures a given re-open condition — and judgements do not
belong in a generated file: the first hand edit would either be erased by the
next run or turn the byte-identity assertion red. It lives in
docs/deferred_guards.yaml, keyed by `round:line`.

    python3 scripts/extract_deferred_index.py            # write the index
    python3 scripts/extract_deferred_index.py --check    # fail if it is stale
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
LEDGER = REPO / "docs" / "PROPOSAL_ADJUDICATIONS.md"
INDEX = REPO / "docs" / "deferred_index.yaml"

#: A heading that introduces this-round-is-not-doing decisions. Matched on the
#: heading text, because the ledger spells it eight different ways across 80
#: rounds ("明列不做", "本輪明確不做", "不做,與 re-open 條件", ...) and
#: enumerating the spellings would mean fitting the reader to today's corpus.
_DEFER_HEADING = re.compile(r"^#{2,4} .*不做")
_ROUND_HEADING = re.compile(r"^#{1,3} Round (\d+)")
_ANY_HEADING = re.compile(r"^#{1,4} ")
_TABLE_SEP = re.compile(r"^\|[\s\-:|]+\|\s*$")
_BULLET = re.compile(r"^[-*] ")


def _cells(row: str) -> "list[str]":
    """The row's cells, each a byte-slice of the row with the pipes removed.

    `.strip()` only removes whitespace, so every result is still a substring of
    the line it came from — which is the property the guard checks.
    """
    return [c.strip() for c in row.strip().strip("|").split("|")]


def _sections(lines: "list[str]") -> "list[tuple[int, int, int]]":
    """(round, start line index, end line index) for each 不做 section."""
    out: list[tuple[int, int, int]] = []
    current_round: int | None = None
    for i, line in enumerate(lines):
        m = _ROUND_HEADING.match(line)
        if m:
            current_round = int(m.group(1))
            continue
        if not _DEFER_HEADING.match(line):
            continue
        j = i + 1
        while j < len(lines) and not _ANY_HEADING.match(lines[j]):
            j += 1
        if current_round is not None:
            out.append((current_round, i, j))
    return out


def _entries_in(lines: "list[str]", start: int, end: int) -> "list[dict]":
    """Table rows and bullets, verbatim. Empty when the shape is unrecognised."""
    entries: list[dict] = []

    rows = [(k, lines[k]) for k in range(start + 1, end)
            if lines[k].startswith("|") and not _TABLE_SEP.match(lines[k])]
    # The first row of a markdown table is its header; drop exactly one, and
    # only when a separator follows it, so a table-less section is not silently
    # decapitated.
    if rows:
        first_idx = rows[0][0]
        has_sep = (first_idx + 1 < end and _TABLE_SEP.match(lines[first_idx + 1]))
        for k, row in (rows[1:] if has_sep else rows):
            cells = _cells(row)
            if len(cells) < 2:
                continue
            entry = {"line": k + 1, "kind": "table_row", "item": cells[0],
                     "reason": cells[1]}
            if len(cells) >= 3:
                entry["reopen"] = cells[2]
            entries.append(entry)

    for k in range(start + 1, end):
        if not _BULLET.match(lines[k]):
            continue
        # A bullet owns its continuation lines: everything up to the next
        # bullet, the next blank-line-then-non-indented run, or the section end.
        j = k + 1
        while j < end and not _BULLET.match(lines[j]) and (
            lines[j].startswith(("  ", "\t")) or lines[j].strip() == ""
        ):
            j += 1
        while j > k + 1 and lines[j - 1].strip() == "":
            j -= 1
        entries.append({"line": k + 1, "kind": "bullet",
                        "text": "\n".join(lines[k:j])})

    return entries


def build() -> str:
    lines = LEDGER.read_text(encoding="utf-8").splitlines()
    out: list[str] = [
        "# GENERATED by scripts/extract_deferred_index.py — do not hand-edit.",
        "#",
        "# Every `item`/`reason`/`reopen`/`text` value below is a byte-exact slice of",
        "# docs/PROPOSAL_ADJUDICATIONS.md. tests/test_deferred_index.py asserts it, so",
        "# this file cannot say anything the ledger does not say. Round 80 declined to",
        "# build it because a REWRITING extraction risks putting words in past rounds'",
        "# mouths; this one copies, and the guard is what makes that a property rather",
        "# than an intention.",
        "#",
        "# `kind: unstructured` means the extractor did not recognise the section's",
        "# shape and declined to guess. That is a hole this file admits to, not one it",
        "# hides — see Round 46.",
        "#",
        "# The guard mapping lives in docs/deferred_guards.yaml, because which test",
        "# measures a re-open condition is a judgement and this file is a copy.",
        "entries:",
    ]
    for rnd, start, end in _sections(lines):
        entries = _entries_in(lines, start, end)
        if not entries:
            out.append(f"  - round: {rnd}")
            out.append(f"    line: {start + 1}")
            out.append("    kind: unstructured")
            out.append(f"    heading: {_yaml(lines[start])}")
            out.append(f"    spans_lines: [{start + 1}, {end}]")
            continue
        for entry in entries:
            out.append(f"  - round: {rnd}")
            out.append(f"    line: {entry['line']}")
            out.append(f"    kind: {entry['kind']}")
            for key in ("item", "reason", "reopen", "text"):
                if key in entry:
                    out.append(f"    {key}: {_yaml(entry[key])}")
    return "\n".join(out) + "\n"


def _yaml(value: str) -> str:
    """A YAML scalar that round-trips this string exactly.

    Always block-or-quoted, never bare: the ledger is full of `:`, `#`, `|`,
    leading `-` and CJK punctuation, and a bare scalar would quietly change the
    bytes this whole file exists to preserve.
    """
    if "\n" in value:
        body = "\n".join(f"      {line}" if line else "" for line in value.split("\n"))
        return "|-\n" + body
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="exit 1 if the committed index is not what this produces")
    args = parser.parse_args(argv)

    generated = build()
    if args.check:
        current = INDEX.read_text(encoding="utf-8") if INDEX.exists() else ""
        if current != generated:
            print(f"{INDEX.relative_to(REPO)} is stale — regenerate with "
                  f"`python3 scripts/extract_deferred_index.py`", file=sys.stderr)
            return 1
        print(f"{INDEX.relative_to(REPO)} is current")
        return 0

    INDEX.write_text(generated, encoding="utf-8")
    print(f"wrote {INDEX.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
