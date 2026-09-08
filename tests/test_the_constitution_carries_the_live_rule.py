"""Round 109 站4 — the constitution agreed with a dead registry.

`constitution/CONSTITUTION.md` §8.1 declares itself a surface of one of the
framework's prompt rules and says so in the line above the text:

    - **Canonical rule text** (… R-CANONICAL-INTERP-001 — this doc is a
      declared surface; keep verbatim):
      > CANONICAL INTERPRETATION RULE (anti-over-specification …

A declared surface with nothing enforcing it drifts, and this one had. Three
copies of that rule existed on 2026-09-08:

    harness/prompts/rules/R-CANONICAL-INTERP-001.md   1,422 chars   the SSOT
    rules/manifest.yaml  →  text:                       860 chars   dead registry
    constitution/CONSTITUTION.md §8.1                              == the registry's

The constitution's copy was byte-for-byte the registry's, and the registry's
similarity to the live rule was 0.489. The SSOT is what an agent is actually
handed: `scripts/plangen/blocks.py::_load_rule` renders it into every phase-1
plan and `scripts/workflowgen/js_blocks.py::render_rule_prose` into every
generated workflow. So the highest-authority document in the repository stated
a version of the rule that no agent had received since `1facd4e7` changed it —
it still promised the "measurement / interpretation boundary is owned by the
test harness" template that the live rule now explicitly forbids as a phrase
that "names nobody and ships a false claim about who checked it".

`rules/manifest.yaml` was retired in the same commit. Its own header pointed
three times at `check_methodology_consistency.py (REMOVED)` — the tool that
was supposed to prove the surfaces agree — and it had no reader at all.

WHY THIS IS ONE GUARD AND NOT A SCANNER

The first plan for this station widened
`tests/test_prompt_rules.py::test_rule_prose_not_forked_into_python` from
`*.py` to `*.md` / `*.js` / `*.yaml`. Run against the tree it produced 26
hits, of which 24 were `.claude/workflows/*.js`, `tests/golden/**` and
`.methodology/phase1_plan.md` — RENDERED surfaces, already pinned byte-exact
by `generate_workflows.py --check` and the golden tests. A scanner reporting
24 false accusations to find 2 real ones is the shape Round 46 keeps closing,
so it was dropped. The useful distinction is not the file extension but
generated versus hand-written: once the registry is gone there is exactly one
hand-written surface, and one equality is the whole check.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = [pytest.mark.core]

REPO = Path(__file__).resolve().parents[1]
CONSTITUTION = REPO / "constitution" / "CONSTITUTION.md"
RULE = REPO / "harness" / "prompts" / "rules" / "R-CANONICAL-INTERP-001.md"

#: The line that declares the surface. Everything quoted under it is the copy.
_DECLARATION = re.compile(r"^-\s+\*\*Canonical rule text\*\*\s*\((?P<src>[^)]*)\)", re.M)


def _declared_copy() -> "tuple[str, str]":
    """(source named by the declaration, the quoted text beneath it)."""
    text = CONSTITUTION.read_text(encoding="utf-8")
    match = _DECLARATION.search(text)
    assert match, (
        "constitution/CONSTITUTION.md no longer declares itself a surface of "
        "the canonical-interpretation rule. If the declaration was removed on "
        "purpose the quoted text must go with it — a copy with no statement "
        "that it is a copy is how this one drifted for eight rounds")

    # `match.end()` sits just after the declaration's closing paren, mid-line.
    # Start at the line AFTER it, or the trailing `:` reads as the first
    # candidate and the block comes back empty.
    rest = text[text.index("\n", match.end()) + 1:]

    quoted = []
    for line in rest.splitlines():
        if not line.strip():
            if quoted:
                break
            continue
        stripped = line.lstrip()
        if not stripped.startswith(">"):
            break
        quoted.append(stripped[1:].strip())
    return match.group("src"), "\n".join(quoted)


def test_the_constitutions_copy_is_the_rule_agents_receive() -> None:
    """One equality, and it is the whole station.

    Not a similarity threshold: the declaration says "keep verbatim", and a
    tolerance would be a second, weaker rule about what counts as the same
    text. The drift this closes measured 0.489 similar and read as prose that
    said roughly the right thing.
    """
    _, quoted = _declared_copy()
    live = RULE.read_text(encoding="utf-8").strip()
    assert quoted == live, (
        "the constitution's declared-verbatim copy is not the rule the "
        "framework hands its agents.\n"
        f"  constitution: {len(quoted)} chars\n"
        f"  {RULE.relative_to(REPO)}: {len(live)} chars\n"
        "  first divergence at offset "
        f"{next((i for i, (a, b) in enumerate(zip(quoted, live)) if a != b), min(len(quoted), len(live)))}")


def test_the_declaration_names_the_source_that_exists() -> None:
    """A pointer at a deleted file is the drift's other half.

    `rules/manifest.yaml` is gone; a declaration still naming it would send
    the next reader to nothing and leave them with the copy in front of them
    as the only text they can see.
    """
    source, _ = _declared_copy()
    assert "rules/manifest.yaml" not in source, (
        f"the declaration still points at the retired registry: {source!r}")
    named = re.search(r"`([^`]+)`", source)
    assert named, f"the declaration names no source file: {source!r}"
    assert (REPO / named.group(1)).is_file(), (
        f"the declaration points at {named.group(1)!r}, which does not exist")


def test_the_retired_registry_is_gone() -> None:
    """It carried a third copy of this text and had no reader.

    Kept as its own assertion rather than folded into the equality above:
    re-adding the file would restore a copy that nothing renders from and
    nothing checks, which is the state this station ended.
    """
    assert not (REPO / "rules" / "manifest.yaml").exists(), (
        "rules/manifest.yaml is back. Its `text:` field was a copy of "
        "harness/prompts/rules/<id>.md with no reader and no enforcer — its "
        "own header named check_methodology_consistency.py (REMOVED) three "
        "times as the tool that would have kept them equal")
