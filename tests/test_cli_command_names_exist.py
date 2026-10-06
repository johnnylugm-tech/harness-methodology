"""A remedy that names a subcommand must name one that exists.

Round 111 站F2. `harness_cli.py` registers 68 subcommands. Searched every
string in the tree that puts a token in command position after
`harness_cli.py` and compared it against `build_parser()`:

    resume-fr-step   does not exist — 14 sites, including two SHIPPED
                     workflow JS files and a .mjs test asserting the name
    sync-trace       does not exist — the traceability dimension's remedy
    stage-pass       does not exist — and the DeprecationWarning fifteen
                     lines below its Usage line already says so

Each is printed at the moment a run has stopped and is being told what to do
next, so the instruction that arrives is one that cannot be carried out. The
`resume-fr-step` sites are the sharpest: three of them route an agent out of
an infra failure or a repeated-failure abort, which is exactly when nobody
has spare attempts to spend discovering the command is not real.

`resume-fr-step` is `run-fr-step` misspelled — the argparse for it takes
`--phase/--fr-id/--step/--project`, a 1:1 match for what every site already
interpolates. `resume-fr-phase` DOES exist and is the wrong answer: read it
(cli/fr_cmds.py::cmd_resume_fr_phase) and all it does is print the
`run-fr-step` command the caller should have been given directly.

SCOPE

`docs/superpowers/plans/` is excluded: those are archived plans, records of
what was once proposed, and editing a record to make a guard green is Round
44. Nothing there is printed to anybody at runtime.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.core]

REPO = Path(__file__).resolve().parents[1]

#: Where a string can end up in front of somebody. Tests are deliberately in
#: scope: an assertion that pins the wrong name is how a wrong name survives
#: (Round 64), and three of them did.
_SCAN_ROOTS = (
    "core", "cli", "harness", "scripts", ".claude", "templates", "docs",
    "tests", "workflows",
)
_SCAN_FILES = ("SKILL.md", "harness_cli.py")
_SCAN_SUFFIXES = {".py", ".js", ".mjs", ".md", ".json", ".yaml", ".yml", ".sh"}
#: Round 114 站1: git hooks carry no suffix, and `prepare-commit-msg` prints
#: an advance-phase command to the operator whose commit it just refused.
_SCAN_GLOBS = ("scripts/hooks/*",)

#: Archived plans — records of what was proposed, not instructions anyone
#: reads at runtime. Round 44.
_EXCLUDED = (REPO / "docs" / "superpowers" / "plans",)

#: `harness_cli.py <token>`. A token is only read as a subcommand when it
#: contains a hyphen or is followed by a flag. Measured without that
#: qualifier: 30-odd prose false positives ("harness_cli.py does not …",
#: "harness_cli.py remains …") — a rule that accuses the sentence explaining
#: the entry point. 66 of the 68 registered subcommands are hyphenated, and
#: the five that are not (`dispatch`, `doctor`, `effort`, `manifest`,
#: `status`) are reached by the flag half.
#:
#: The flag half is `--[a-z]` and not `--`, because
#: `echo "--- 3. harness_cli.py reachable ---"` in docs/USER_MANUAL.md is a
#: shell banner, and `---` satisfies the looser form.
_INVOCATION = re.compile(r"harness_cli\.py\s+([a-z][a-z0-9-]*)(\s+--[a-z])?")


def _is_invocation(match: "re.Match[str]") -> bool:
    return "-" in match.group(1) or bool(match.group(2))


def _subcommands() -> set[str]:
    from harness_cli import build_parser

    for action in build_parser()._actions:
        if isinstance(action, argparse._SubParsersAction):
            return set(action.choices)
    raise AssertionError("harness_cli.build_parser() registers no subparsers")


def _files() -> list[Path]:
    out: list[Path] = []
    for rel in _SCAN_ROOTS:
        root = REPO / rel
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            if path.suffix not in _SCAN_SUFFIXES or not path.is_file():
                continue
            if any(ex in path.parents for ex in _EXCLUDED):
                continue
            out.append(path)
    out.extend(REPO / name for name in _SCAN_FILES if (REPO / name).exists())
    for pattern in _SCAN_GLOBS:
        out.extend(p for p in sorted(REPO.glob(pattern)) if p.is_file())
    return out


def test_every_named_subcommand_is_registered():
    """Every `harness_cli.py <cmd>` in the tree resolves to a real subcommand."""
    known = _subcommands()
    unknown: list[str] = []
    for path in _files():
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for match in _INVOCATION.finditer(text):
            token = match.group(1)
            if token in known or not _is_invocation(match):
                continue
            line = text[: match.start()].count("\n") + 1
            unknown.append(f"{path.relative_to(REPO)}:{line}: {token}")

    assert not unknown, (
        "these strings tell somebody to run a subcommand harness_cli.py does "
        "not register — printed at the moment a run has stopped and is asking "
        "what to do next:\n    " + "\n    ".join(sorted(unknown))
    )


def test_the_scan_can_still_see_a_command_name():
    """Reverse control: the scan passes trivially if the regex stops matching.

    Without this, a rule narrowed until it matches nothing looks exactly like
    a tree with no wrong names in it.
    """
    known = _subcommands()
    seen = {
        m.group(1)
        for path in _files()
        for m in _INVOCATION.finditer(path.read_text(encoding="utf-8", errors="replace"))
        if _is_invocation(m)
    }
    assert len(seen & known) >= 20, (
        f"the scan found only {len(seen & known)} real subcommand names in the "
        f"whole tree — it has stopped reading invocations, not stopped finding "
        f"wrong ones"
    )


# ---------------------------------------------------------------------------
# Round 114 站1 — the flags, not only the name.
#
# The test above proved every `harness_cli.py <cmd>` names a registered
# subcommand and stopped there. argparse refuses an unknown flag with exit 2
# exactly as it refuses an unknown command, so a remedy with the right name
# and a wrong flag fails just the same, at the same moment. Measured against
# build_parser():
#
#   cli/advance_prechecks.py:328,417  advance-phase --completed-phase N  (the flag is --completed)
#   cli/push_cmds.py:164              advance-phase --phase {next}       (and the phase it names is the wrong one)
#   scripts/harness-init.sh:149       advance-phase --phase N
#   scripts/hooks/prepare-commit-msg  advance-phase --phase <next_phase>
#   harness_cli.py:25                 advance-phase --completed-phase 3  (the usage text)
#   .claude/workflows/standalone-mutmut.js  mutation-test-score --paths-to-mutate … --timeout …
#                                     (accepts --project only — every run exited 2)
#
# Two of those the old scan could not have seen: `:417` splits the command
# across two implicitly concatenated f-strings, and the hook has no suffix.
# For .py files the strings are therefore read from the AST, where the parser
# has already joined adjacent literals into one node.
# ---------------------------------------------------------------------------

import ast  # noqa: E402

#: Where one invocation's own arguments end: a closing backtick, a shell
#: connective, a literal `\n` inside a JS/py string, a parenthesis closing
#: the sentence it sits in (`(harness_cli.py run-report --project X), then
#: either record-block with --owner` — that flag is record-block's), or the
#: next invocation.
_SEGMENT_END = re.compile(r"`|&&|\|\||\||;\s|\\n|\)[,.;:]?\s|harness_cli\.py")
_FLAG = re.compile(r"(?<![\w-])(--[a-z][a-z0-9-]*)")


def _subcommand_flags() -> dict[str, set[str]]:
    from harness_cli import build_parser

    for action in build_parser()._actions:
        if isinstance(action, argparse._SubParsersAction):
            return {
                name: {o for a in sub._actions for o in a.option_strings}
                for name, sub in action.choices.items()
            }
    raise AssertionError("harness_cli.build_parser() registers no subparsers")


def _py_strings(text: str) -> list[str]:
    """Every string a .py file can print, adjacent literals already joined.

    An f-string's interpolations become `{}` — a flag is never interpolated
    in this tree, and the placeholder keeps the surrounding words apart.
    """
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return [text]
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            out.append(node.value)
        elif isinstance(node, ast.JoinedStr):
            out.append("".join(
                str(v.value) if isinstance(v, ast.Constant) else "{}" for v in node.values))
    return out


def _flag_findings(path: Path, text: str, flags: dict[str, set[str]]) -> list[str]:
    bodies = _py_strings(text) if path.suffix == ".py" else [text]
    found: list[str] = []
    for body in bodies:
        for match in _INVOCATION.finditer(body):
            sub = match.group(1)
            if sub not in flags or not _is_invocation(match):
                continue
            rest = body[match.end(1):].split("\n", 1)[0]
            end = _SEGMENT_END.search(rest)
            segment = rest[: end.start()] if end else rest
            bad = sorted({f for f in _FLAG.findall(segment)
                          if f not in flags[sub] and f != "--help"})
            if bad:
                found.append(f"{path.relative_to(REPO)}: {sub} {' '.join(bad)}")
    return found


def test_every_flag_an_invocation_passes_is_accepted():
    """Every `harness_cli.py <cmd> --flag` names a flag that command takes."""
    flags = _subcommand_flags()
    wrong: list[str] = []
    for path in _files():
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        wrong.extend(_flag_findings(path, text, flags))
    assert not wrong, (
        "these invocations pass a flag the named subcommand does not accept — "
        "argparse exits 2 on them exactly as on an unknown command:\n    "
        + "\n    ".join(sorted(set(wrong)))
    )


def test_the_flag_scan_reads_split_literals_and_can_still_see_flags():
    """Reverse controls: the AST read joins `:417`'s shape, and the scan sees
    real flags — a scan that stopped matching would pass every tree."""
    flags = _subcommand_flags()
    split = (
        'print(\n'
        '    f"Then re-run: python3 harness_cli.py advance-phase"\n'
        '    f" --completed-phase {n} --project ."\n'
        ')\n'
    )
    assert _flag_findings(REPO / "x.py", split, flags), "split f-string not joined"
    seen = set()
    for path in _files():
        text = path.read_text(encoding="utf-8", errors="replace")
        bodies = _py_strings(text) if path.suffix == ".py" else [text]
        for body in bodies:
            for m in _INVOCATION.finditer(body):
                if m.group(1) in flags:
                    seen.update((m.group(1), f) for f in _FLAG.findall(
                        body[m.end(1):].split("\n", 1)[0]) if f in flags[m.group(1)])
    assert len(seen) >= 30, f"the scan read only {len(seen)} accepted (command, flag) pairs"
