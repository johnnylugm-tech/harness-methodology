"""Round 78 站2 — a number that grew a cause without growing a sentence.

`_advance_prechecks` returned 9 for one thing: the delivered source failed the
coverage gate. Plan E (`d5549c3a`) added a `return 9` for a non-allowlist
`# pragma: no cover`; Plan F (`da8e70fd`) added a third for a SAB module that
is not on disk. Neither touched a single sentence about the number, and there
were four of them:

    cli/exit_codes.py:37   the constant, named EX_COVERAGE_100_REQUIRED
    cli/exit_codes.py:81   the REGISTRY description, "100% coverage required
                           on 03-development/src not met" — which the module's
                           own header says renders into harness_cli.py's
                           docstring AND into docs/ERROR_HANDLING.md's table
    harness_cli.py:47      that docstring line
    docs/ERROR_HANDLING.md §"Exit 9 has two causes, and the message says which"

Three of the four described a third of the number, and the fourth counted to
two. `tests/test_exit_code_registry.py` could not see it: it checks that every
returned code IS in the REGISTRY, which 9 always was — a code acquiring a new,
unrelated cause looks identical to one that did not.

The number is deliberately NOT split. `core/fault_owner.py:108` gives all
three the same owner (`PROJECT`), and the remediation channel is the same —
the project changes its own tree. That is the condition Round 25 set for
sharing a code ("Same exit code, because the remediation channel is the same
… different first line, because the two are not the same problem"). What was
wrong was the sentences, so the sentences are what gets bound here.

Scope: this guard is about a doc that makes a COUNTABLE claim. It is not a
general "every overloaded code must be enumerated" rule — measured, sixteen
codes have more than one `return` site in `cli/` and most of them (exit 2 has
fourteen) mean the same thing at every one, so a site count is not evidence of
overloading. What it binds is the other direction: where the documentation
enumerates the causes, the enumeration has to keep up with the code.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from cli.exit_codes import EX_ADVANCE_SOURCE_GATE, REGISTRY

pytestmark = [pytest.mark.core]

REPO = Path(__file__).resolve().parents[1]
ERROR_HANDLING = REPO / "docs" / "ERROR_HANDLING.md"
_MARKER = "<!-- exit-9-causes:"


def _return_sites(code: int) -> list[str]:
    """Every `return <code>` in cli/, as "file:line".

    Resolves `return EX_NAME` through cli/exit_codes.py as well as the bare
    literal — all three of exit 9's sites are literals today, and a later one
    written as the constant must still be counted.
    """
    names = {k: v for k, v in vars(__import__(
        "cli.exit_codes", fromlist=["_"])).items()
        if k.startswith("EX_") and isinstance(v, int)}
    sites: list[str] = []
    for path in sorted((REPO / "cli").glob("*.py")):
        if path.name == "exit_codes.py":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Return) or node.value is None:
                continue
            value = node.value
            found = None
            if (isinstance(value, ast.Constant)
                    and isinstance(value.value, int)
                    and not isinstance(value.value, bool)):
                found = value.value
            elif isinstance(value, ast.Name) and value.id in names:
                found = names[value.id]
            if found == code:
                sites.append(f"{path.name}:{node.lineno}")
    return sites


def _documented_causes() -> list[str]:
    """The `[BLOCKED]` first lines the doc enumerates for exit 9."""
    text = ERROR_HANDLING.read_text(encoding="utf-8")
    start = text.index(_MARKER)
    block = re.search(r"```\n(.*?)```", text[start:], re.DOTALL)
    assert block, "the exit-9 marker is no longer followed by a fenced block"
    return [ln.strip() for ln in block.group(1).splitlines() if ln.strip()]


def test_the_documented_causes_are_as_many_as_the_return_sites():
    sites = _return_sites(EX_ADVANCE_SOURCE_GATE)
    causes = _documented_causes()
    assert len(causes) == len(sites), (
        f"exit {EX_ADVANCE_SOURCE_GATE} is returned from {len(sites)} places "
        f"({', '.join(sites)}) and docs/ERROR_HANDLING.md enumerates "
        f"{len(causes)} cause(s). A fourth `return 9` needs its [BLOCKED] "
        f"first line added to that block in the SAME commit — Plan E and "
        f"Plan F each added one and left every sentence about the number "
        f"describing a third of it."
    )


def test_each_documented_cause_is_a_message_the_code_actually_prints():
    """Enumerating causes is worth nothing if the strings are invented.

    Each documented first line must appear as a literal in the file that
    returns the code, so the doc cannot drift into describing a message no
    operator will ever see.
    """
    # Read the literals off the AST rather than the raw text: these messages
    # are built from f-strings split over three source lines, and Python's
    # parser is what joins adjacent segments back into one constant. Grepping
    # the file would only find the fragment before the line break.
    tree = ast.parse((REPO / "cli" / "phase_cmds.py").read_text(encoding="utf-8"))
    literals = [
        node.value for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]
    for cause in _documented_causes():
        assert any(cause in text for text in literals), (
            f"docs/ERROR_HANDLING.md lists an exit-9 cause whose message "
            f"cli/phase_cmds.py never prints: {cause!r}")


def test_the_registry_description_no_longer_names_only_coverage():
    """The description renders into harness_cli.py's docstring and the doc's
    exit-code table (cli/exit_codes.py's own header says so), so a sentence
    that names one of three causes is wrong in three places at once."""
    description = REGISTRY[EX_ADVANCE_SOURCE_GATE]
    for cause_word in ("coverage", "pragma", "Phantom"):
        assert cause_word in description, (
            f"the REGISTRY description for exit {EX_ADVANCE_SOURCE_GATE} does "
            f"not mention {cause_word!r}: {description!r}")


def test_the_constant_is_not_named_after_one_of_its_three_causes():
    """`EX_COVERAGE_100_REQUIRED` was the name until Round 78. A name is a
    statement; this one had been false since Plan E."""
    import cli.exit_codes as ec

    assert not hasattr(ec, "EX_COVERAGE_100_REQUIRED"), (
        "the old name is back — it describes one of three causes")
    assert ec.EX_ADVANCE_SOURCE_GATE == 9


def test_the_overloaded_list_in_the_doc_includes_this_code():
    """docs/ERROR_HANDLING.md's exit-code section lists which numbers carry
    more than one meaning. 9 became one and the list still said four."""
    text = ERROR_HANDLING.read_text(encoding="utf-8")
    m = re.search(r"numbers are deliberately overloaded[^\n]*\n[^\n]*", text)
    assert m, "the overloaded-numbers sentence is gone"
    assert "`9`" in m.group(0), (
        f"exit 9 carries three unrelated preconditions and is not in the "
        f"overloaded list: {m.group(0)!r}")
