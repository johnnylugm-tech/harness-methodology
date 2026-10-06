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
    """The first line of *ref*'s file reading `<ident>: <word>`, or None.

    The id must stand alone (`NFR-99.1` is not answered by `NFR-99.10`) and
    the word must follow the colon — "NFR-99.1 will be resolved later" is a
    sentence about a decision, not the record of one.
    """
    path = ref_file(project, ref)
    if path is None or not ident:
        return None
    pattern = re.compile(
        r"(?<![\w.-])" + re.escape(ident) + r"(?![\w.-])`?\s*:\s*`?(?:"
        + "|".join(re.escape(w) for w in words) + r")\b",
        re.IGNORECASE,
    )
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if pattern.search(line):
            return line
    return None
