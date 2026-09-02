"""The framework asked for a record and built nothing that reads it.

Round 87 站7. `cli/fr_prompts/tdd.py` has told every TDD-RED agent since it was
written:

    Add `# SPEC_AMBIGUITY: <one-line>` comment in the test … and note the
    deviation.

A full-tree search for that token found exactly one occurrence in this
repository: the line of prompt that asks for it. Round 43's
detected-with-no-executor, with the detection delegated to the agent.

Measured across ten corpus projects — three notes written, zero readers:

    taskq-cc       test_fr04.py:17   row 4 names `target="taskq_api.api.routes"`,
                                     a module that …
    taskq-advance  test_fr10.py:523  SPEC.md §7 maps 401 to
                                     `/errors/unauthenticated` and …
    taskq          test_fr01.py:286  TEST_SPEC.md lists the same function name …

The second is a live contradiction in a canonical spec, noticed at the only
moment in the pipeline when anyone holds the AC prose and the assertion open
at the same time, and it went nowhere.

THE OTHER HALF: THE LICENCE THAT PRODUCED `assert skip_count >= 0`

The same prompt says "If you truly cannot construct the scenario, write the
test against the SIMPLER invariant (>= 1 instead of == 3)". taskq-redo's
`test_pytest_skipped_count_zero` asserts `skip_count >= 0` — always true —
and its docstring explains that unconditional skips are what the SPEC forbids.
That is the licence generalised from scenarios to thresholds, which is not a
simpler test of the same thing: it is a test of a different thing that passes
while the criterion fails.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from core.quality_gate.red_assertion_check import spec_ambiguity_notes

pytestmark = [pytest.mark.core]

REPO = Path(__file__).resolve().parents[1]


def test_a_declared_ambiguity_is_collected(tmp_path: Path) -> None:
    tests = tmp_path / "03-development" / "tests"
    tests.mkdir(parents=True)
    (tests / "test_fr01.py").write_text(
        "def test_fr01_one():\n"
        "    # SPEC_AMBIGUITY: AC says 5 of which 3 done; Inputs lists 5 identical\n"
        "    assert True\n",
        encoding="utf-8")
    notes = spec_ambiguity_notes(tmp_path)
    assert len(notes) == 1
    assert notes[0]["line"] == 2
    assert "5 of which 3 done" in notes[0]["note"]
    assert notes[0]["file"].endswith("test_fr01.py")


def test_a_tree_with_no_ambiguities_reports_none(tmp_path: Path) -> None:
    tests = tmp_path / "03-development" / "tests"
    tests.mkdir(parents=True)
    (tests / "test_fr01.py").write_text(
        "def test_fr01_one():\n    assert True\n", encoding="utf-8")
    assert spec_ambiguity_notes(tmp_path) == []


def test_the_note_reaches_the_delivery_fingerprint(tmp_path: Path) -> None:
    """A record with no reader is the failure this station exists to close.

    The fingerprint is where every other product-side fact is kept for a later
    round to compare against (Round 52 站3), and it is written at every gate
    finalize — so this is a reader that already runs.
    """
    from core.quality_gate.delivery_fingerprint import build_fingerprint

    tests = tmp_path / "03-development" / "tests"
    tests.mkdir(parents=True)
    (tests / "test_fr01.py").write_text(
        "def test_fr01_one():\n"
        "    # SPEC_AMBIGUITY: prose and Inputs disagree about the count\n"
        "    assert True\n",
        encoding="utf-8")
    fingerprint = build_fingerprint(tmp_path, phase=3, gate=2)
    assert "spec_ambiguity" in fingerprint, (
        "the fingerprint no longer carries the field; the notes are back to "
        "having no reader at all"
    )
    assert len(fingerprint["spec_ambiguity"]) == 1


def test_the_prompt_that_asks_for_it_still_asks_for_it() -> None:
    """Producer and reader are one statement; neither half may leave alone.

    A collector for a token no prompt requests is dead code; a prompt
    requesting a token nothing collects is what this round found.
    """
    prompt = (REPO / "cli" / "fr_prompts" / "tdd.py").read_text(encoding="utf-8")
    assert "SPEC_AMBIGUITY" in prompt, (
        "the TDD-RED prompt no longer asks for the deviation note, but "
        "spec_ambiguity_notes still collects it"
    )


def test_the_weakening_licence_excludes_thresholds() -> None:
    """The sentence that produced `assert skip_count >= 0`, bounded.

    Pinned as a rule rather than as a snapshot of the wording: whatever the
    prompt says, it has to say that a stated number is not something the
    SIMPLER-invariant licence covers.
    """
    prompt = (REPO / "cli" / "fr_prompts" / "tdd.py").read_text(encoding="utf-8")
    assert "SIMPLER invariant" in prompt, "the licence itself is gone — check the golden"
    licence_start = prompt.index("SIMPLER invariant")
    window = prompt[licence_start: licence_start + 900]
    # The exclusion must be an EXCLUSION, not merely the word appearing
    # somewhere nearby. The first version of this guard asserted
    # `"THRESHOLD" in window.upper()`, and deleting the scope sentence left it
    # green because a later line still said "the declared threshold" — the
    # counter-proof found that, not review.
    assert "NEVER ABOUT THRESHOLD" in window.upper(), (
        "the SIMPLER-invariant licence no longer states that it does not "
        "cover thresholds. Without that scope line, `assert avg >= 78.0` "
        "against an AC stating 80 is something this prompt authorises."
    )
    assert "do not lower it" in window, (
        "the licence no longer says what to do instead of lowering a declared "
        "threshold; an exclusion with no alternative is an instruction to guess"
    )


def test_the_collector_is_not_collected_as_a_test() -> None:
    """A production helper named `test_*` becomes a pytest item on import.

    Round 87 站6 shipped `test_seam_findings` and pytest tried to run it,
    reporting `fixture 'project' not found`. This pins that neither new
    producer is named that way again.
    """
    for module in ("core/quality_gate/test_seam_in_production.py",
                   "core/quality_gate/red_assertion_check.py"):
        tree = ast.parse((REPO / module).read_text(encoding="utf-8"))
        public = [
            n.name for n in tree.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            and n.name.startswith("test_")
        ]
        assert not public, (
            f"{module} defines {public} — any test module importing that name "
            f"makes pytest collect it as a test case"
        )
