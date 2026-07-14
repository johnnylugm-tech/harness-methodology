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


def strip_comments_and_strings(text: str) -> str:
    """Blank `//`, `/* */` comments and `'`/`"`/backtick string bodies,
    keeping newlines and every other byte in place so line numbers in
    the result still line up 1:1 with the input."""
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        two = text[i:i + 2]
        if two == "//":
            j = text.find("\n", i)
            j = n if j == -1 else j
            out.append(" " * (j - i))
            i = j
            continue
        if two == "/*":
            j = text.find("*/", i + 2)
            j = n if j == -1 else j + 2
            out.append("".join(ch if ch == "\n" else " " for ch in text[i:j]))
            i = j
            continue
        c = text[i]
        if c in ("'", '"', "`"):
            quote = c
            j = i + 1
            while j < n:
                if text[j] == "\\":
                    j += 2
                    continue
                if text[j] == quote:
                    j += 1
                    break
                j += 1
            out.append("".join(ch if ch == "\n" else " " for ch in text[i:j]))
            i = j
            continue
        out.append(c)
        i += 1
    return "".join(out)


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
