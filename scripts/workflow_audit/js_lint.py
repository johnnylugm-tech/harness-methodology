"""Playbook §4 runtime-constraint lint for generated workflow JS.

Claude Code Workflow scripts run in a sandboxed evaluator with no Node
host API access (docs/WORKFLOW_PLAYBOOK.md §4): `import()`/`require()`/
`fs.*`/`path.*`/`process.*` all throw at runtime, and `Date.now()`/
`Math.random()`/an argless `new Date()` break resume-from-cache. An
inline `schema:` object also breaks the script parser (§5.3) — the value
must be a top-level const identifier.

Comment/string-aware scan, not a bare substring search: the banned
tokens legitimately appear as PROSE inside this repo's own generated
files. `phase1-requirements.js` documents the constraint itself in a
header comment ("no fs.* / no process.* / no import() / no Date.now() /
no Math.random()."), `phase2-architecture.js` explains in a comment why
`process.env` can't be read, and several phases embed `os.path.getsize`
Python source inside template-literal bash commands passed to `agent()`.
All of that is correct, shipped content — a naive substring scan either
false-positives on it or (guarded by ad-hoc exclusions) misses the next
real regression. `strip_comments_and_strings` blanks `//` and `/* */`
comments plus `'`/`"`/backtick string bodies (escape-aware) before the
patterns are matched, preserving line numbers so callers can report
where a real violation sits.

Known limitation: a template literal's `${...}` interpolation is treated
as opaque string content, same as the rest of the backtick body — a
banned call written directly inside an interpolation would not be
caught. None of this repo's generated files do that (interpolations here
are always plain identifier/property reads like `${REPO}`), and a full
JS tokenizer is more machinery than this regression lint needs.
"""
from __future__ import annotations

import re
from collections.abc import Iterator

_BANNED_PATTERNS: dict[str, re.Pattern[str]] = {
    "import()": re.compile(r"\bimport\s*\("),
    "require()": re.compile(r"\brequire\s*\("),
    "fs.*": re.compile(r"\bfs\.\w"),
    "path.*": re.compile(r"\bpath\.\w"),
    "process.*": re.compile(r"\bprocess\.\w"),
    "Date.now()": re.compile(r"\bDate\.now\s*\("),
    "Math.random()": re.compile(r"\bMath\.random\s*\("),
    "new Date()": re.compile(r"\bnew\s+Date\s*\(\s*\)"),
    "inline schema object": re.compile(r"\bschema\s*:\s*\{"),
}


def _segments(text: str) -> "Iterator[tuple[int, int, str]]":
    """Yield (start, end, kind) spans that tile `text` exactly once.

    kind is "comment" (`//` or `/* */`), "string" (a `'`/`"`/backtick body
    including its delimiters) or "code". Both public helpers below consume
    this one scanner — a second hand-rolled copy of the state machine is
    exactly the same-shaped-sibling pattern this repo keeps finding drifted
    (Round 6 站3, Round 8 站1, Round 20 站2).
    """
    i, n = 0, len(text)
    code_start = 0
    while i < n:
        two = text[i:i + 2]
        if two in ("//", "/*"):
            if code_start < i:
                yield (code_start, i, "code")
            if two == "//":
                j = text.find("\n", i)
                j = n if j == -1 else j
            else:
                j = text.find("*/", i + 2)
                j = n if j == -1 else j + 2
            yield (i, j, "comment")
            i = code_start = j
            continue
        quote = text[i]
        if quote in ("'", '"', "`"):
            if code_start < i:
                yield (code_start, i, "code")
            j = i + 1
            while j < n:
                if text[j] == "\\":
                    j += 2
                    continue
                if text[j] == quote:
                    j += 1
                    break
                j += 1
            yield (i, j, "string")
            i = code_start = j
            continue
        i += 1
    if code_start < n:
        yield (code_start, n, "code")


def strip_comments_and_strings(text: str) -> str:
    """Blank `//`, `/* */` comments and `'`/`"`/backtick string bodies,
    keeping newlines and every other byte in place so line numbers in
    the result still line up 1:1 with the input."""
    out: list[str] = []
    for start, end, kind in _segments(text):
        span = text[start:end]
        out.append(span if kind == "code"
                   else "".join(ch if ch == "\n" else " " for ch in span))
    return "".join(out)


def comment_line_numbers(text: str) -> set[int]:
    """1-indexed line numbers whose entire content is comment.

    A line qualifies when it carries at least one non-whitespace comment
    byte and no non-whitespace byte belonging to code or a string literal.

    Deliberately NOT "the line's first non-space characters are `//`":
    the generated workflow files embed prompt text inside template
    literals, and a line of that STRING content may legitimately begin
    with `//`. Dropping such a line would silently corrupt an agent
    prompt. Round 23 站2 uses this to strip pure-comment lines out of the
    combined run-all.js, whose eight inlined phase bodies would otherwise
    carry 110 KB of duplicated commentary into a file the runtime refuses
    to parse past 512 KB.
    """
    has_comment: set[int] = set()
    has_other: set[int] = set()
    line = 1
    for start, end, kind in _segments(text):
        for ch in text[start:end]:
            if ch == "\n":
                line += 1
            elif not ch.isspace():
                (has_comment if kind == "comment" else has_other).add(line)
    return has_comment - has_other


def find_banned_constructs(js_text: str) -> dict[str, list[int]]:
    """label -> 1-indexed line numbers where a banned construct appears
    in actual code (comments and string contents excluded)."""
    code = strip_comments_and_strings(js_text)
    violations: dict[str, list[int]] = {}
    for label, pattern in _BANNED_PATTERNS.items():
        lines = [code[:m.start()].count("\n") + 1 for m in pattern.finditer(code)]
        if lines:
            violations[label] = lines
    return violations
