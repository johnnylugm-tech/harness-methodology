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
docs/deferred_guards.yaml, keyed by `(round, item)` — NOT by line: line numbers
shift every time the ledger grows, and that file's header records the first
draft being withdrawn for exactly that reason.

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
_BULLET_LEAD = re.compile(r"^[-*] \*\*(.+?)\*\*")


def _bullet_item(text: str) -> str:
    """The bullet's own name for itself — a byte-exact slice, like every field.

    Round 110 站3. A bullet carried only `text`, so `(round, item)` — the key
    docs/deferred_guards.yaml is written against — could not address one, and
    136 of the ledger's 288 recorded decisions sat outside the reach of the
    guard that exists to notice an unanswered re-open condition. Not excluded
    on purpose: structurally invisible, which is worse, because the guard was
    green over a denominator it had chosen without saying so.

    126 of the 136 open with a bold lead (`- **X**: …`) — the ledger's own way
    of naming a decision — and the other 10 use their first line. Measured
    2026-09-09: all 136 are verbatim slices of the ledger, all 136 keys are
    unique within their round, and none collides with a table row's `item`.

    This does NOT extract a re-open condition. A bullet states its condition in
    prose ("若它哪天變成受版控的交付面再開"), and a regex fitted to those is
    the Round 55 shape this file's docstring refuses. Whether a bullet has a
    condition at all is a judgement, and judgements live in deferred_guards.yaml
    — `NO_CONDITION` is one of the five verdicts for exactly this.
    """
    lead = _BULLET_LEAD.match(text)
    if lead:
        return lead.group(1)
    return text.split("\n")[0].lstrip("-* ").strip()


def _cells(row: str) -> "list[str]":
    """The row's cells, each a byte-slice of the row with the pipes removed.

    `.strip()` only removes whitespace, so every result is still a substring of
    the line it came from — which is the property the guard checks.
    """
    return [c.strip() for c in row.strip().strip("|").split("|")]


def _columns(header: "list[str] | None") -> "dict[str, int]":
    """Which cell holds the item, the reason and the re-open condition.

    Round 109 站7. This used to be positional — cells 0/1/2 — and 23 of the
    ledger's 24 tables are `| 項目 | 理由 | re-open |`, so it was right 23
    times. Round 72's is `| # | 事項 | 理由 | re-open |`, and those four rows
    came out with an `item` of "A".."D", the real item filed as the reason, the
    real reason filed as the re-open condition, and the actual re-open
    condition — one column further right — dropped.

    Every one of those cells was still a byte-exact slice of the ledger, which
    is why the guard that exists to stop this file inventing text was green the
    whole time. Round 24's shape: the field is present, and its content is not
    what its name says.

    The header is what a markdown table has for saying which column is which,
    so it is what gets asked. With no header row (a table with no separator)
    there is nothing to ask and the positional reading stands.
    """
    if not header:
        return {"item": 0, "reason": 1, "reopen": 2}

    reopen = next((i for i, h in enumerate(header)
                   if "re-open" in h or "再開" in h), None)
    item = next((i for i, h in enumerate(header)
                 if h not in ("#", "＃") and i != reopen), 0)
    reason = next((i for i in range(item + 1, len(header)) if i != reopen), None)

    cols = {"item": item}
    if reason is not None:
        cols["reason"] = reason
    if reopen is not None:
        cols["reopen"] = reopen
    return cols


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
        cols = _columns(_cells(rows[0][1]) if has_sep else None)
        for k, row in (rows[1:] if has_sep else rows):
            cells = _cells(row)
            if len(cells) < 2:
                continue
            entry = {"line": k + 1, "kind": "table_row"}
            for key in ("item", "reason", "reopen"):
                col = cols.get(key)
                if col is not None and col < len(cells):
                    entry[key] = cells[col]
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
        text = "\n".join(lines[k:j])
        entries.append({"line": k + 1, "kind": "bullet",
                        "item": _bullet_item(text), "text": text})

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
