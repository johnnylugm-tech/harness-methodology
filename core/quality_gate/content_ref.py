"""A reference to a record, resolved by the record's content (Round 114 站3).

Two checks named their evidence as `path:line` — `decision_issues[*]
.resolution_ref` (Round 113 站9) and a property's `review_ref` (91fd4ba9) —
and then read that one line for the id and a closing word. The line number
was the fragile half: an edit above the record moved it out from under the
reference, taskq-open re-pinned four refs by hand (55415df), and 78f79c15 had
to teach one writer to rebase them while every other edit still broke them.

The record already has a shape the template and the P2 prompt prescribe:
`<id>: <word> — <why>`. So the reference names the file and this finds the
line. A trailing `:N` is accepted and ignored — the identity was always the
id and the word, never the position.
"""

from __future__ import annotations

import re
from pathlib import Path

__all__ = ["record_line", "ref_file"]

_LINE_HINT = re.compile(r"^(?P<path>.+?):\d+$")


def ref_file(project: "str | Path", ref: str) -> "Path | None":
    """The project file *ref* names (`path` or `path:N`), or None.

    A reference outside the project — absolute, or climbing out with `..` —
    does not resolve: evidence has to be part of what is delivered.
    """
    text = (ref or "").strip()
    match = _LINE_HINT.match(text)
    rel = match.group("path") if match else text
    if not rel or Path(rel).is_absolute():
        return None
    root = Path(project).resolve()
    path = (root / rel).resolve()
    if root not in path.parents or not path.is_file():
        return None
    return path


def record_line(project: "str | Path", ref: str, ident: str,
                words: "tuple[str, ...]") -> "str | None":
    """A real, unambiguous disposition record, never an example or comment.

    Records start with the exact id (optional Markdown list/emphasis), outside
    code and HTML comments. All records for that id must agree on disposition;
    a closing record beside an open/reopened/rejected record closes nothing.
    """
    path = ref_file(project, ref)
    if path is None or not ident:
        return None
    pattern = re.compile(
        r"^\s*(?:[-*+]\s+|\d+\.\s+)?[*`]*" + re.escape(ident)
        + r"(?![\w.-])[*`]*\s*:\s*[*`]*(?P<word>[\w-]+)\b", re.IGNORECASE)
    text = re.sub(r"<!--.*?(?:-->|\Z)", "", path.read_text(
        encoding="utf-8", errors="replace"), flags=re.DOTALL)
    records: list[tuple[str, str]] = []
    fence = ""
    for line in text.splitlines():
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
        if fence:
            if marker and marker[1][0] == fence[0] and len(marker[1]) >= len(fence) \
                    and not line[marker.end():].strip():
                fence = ""
            continue
        if marker:
            fence = marker[1]
            continue
        if line.startswith(("    ", "\t")):
            continue  # Markdown indented code, not a disposition
        match = pattern.match(line)
        if match and not re.search(r"<(?:decision|why|reason)>", line, re.IGNORECASE):
            records.append((match["word"].lower(), line))
    dispositions = {word for word, _line in records}
    return records[0][1] if len(dispositions) == 1 and dispositions <= {
        word.lower() for word in words} else None
