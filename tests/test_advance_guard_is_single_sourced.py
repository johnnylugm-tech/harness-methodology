"""The "already advanced" verdict has one source, and tag existence is not it.

Round 95. Round 93 fixed a real bug: P6's Tag & Advance step-0 GUARD OR'd
`current_phase >= 7` with "a `harness-v4-*` tag exists", and a round that
created the tag and then died before advance-phase left the second half
permanently true — five retry rounds reported "ADVANCE: PASS (already
advanced)" without calling advance-phase (taskq-final, wf_569d50b0-c17 and
wf_b1c8e5c8-94c, tag `harness-v4-20260904-score98` on disk).

WHAT IT REGISTERED AS THE GUARD AGAINST THAT BUG COMING BACK

    tests/test_workflowgen_golden.py::test_composite_output_matches_golden[run-all]
    tests/test_workflowgen_golden.py::test_generated_output_matches_golden[6]

Both are `assert generate(...) == golden.read_text()`, and both rewrite the
golden when `REGEN_WORKFLOWS=1` — the command the fixing commit's own message
tells the next person to run. Re-add the OR clause, regenerate, and the two
registered guards stay green. Grepping tests/ and js_src/ for `already
advanced`, `ADVANCE: PASS` and `harness-v4-` found no non-golden assertion
anywhere: nothing in the repository could tell the difference.

A byte-equality snapshot pins that output did not change by accident. It
cannot pin that output is correct, because the accepted way to change it is
to accept whatever it now says. So this file asserts the property instead:
one source for the guard text, and no shipped instruction anywhere that reads
a tag as proof the phase advanced.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from scripts.workflowgen import js_blocks as B
from scripts.workflowgen.generate_workflows import GENERATORS, generate, generate_composite

pytestmark = [pytest.mark.core]

REPO = Path(__file__).resolve().parent.parent

#: Phase -> the phase its advance step is trying to reach.
_ADVANCE_PHASES = {3: 4, 4: 5, 5: 6, 6: 7, 7: 8}


@pytest.mark.parametrize("phase,next_phase", sorted(_ADVANCE_PHASES.items()))
def test_every_phase_renders_the_shared_guard_verbatim(phase, next_phase):
    """Including P6, which is the one that hand-rolled its own."""
    assert phase in GENERATORS, phase
    assert B.render_advance_guard_step(next_phase) in generate(phase), (
        f"phase{phase}'s advance step does not render "
        f"js_blocks.render_advance_guard_step({next_phase}) — it is stating "
        f"the stop condition in its own words again, which is the drift "
        f"Round 93 fixed one instance of"
    )


def test_the_composite_carries_the_same_guards():
    """run-all.js inlines all eight bodies; the shipped file is what runs."""
    composite = generate_composite("run-all")
    for _, next_phase in sorted(_ADVANCE_PHASES.items()):
        assert B.render_advance_guard_step(next_phase) in composite, next_phase


@pytest.mark.parametrize("phase,next_phase", sorted(_ADVANCE_PHASES.items()))
def test_no_advance_step_names_a_tag_in_its_stop_condition(phase, next_phase):
    """The narrow property, stated over the rendered text rather than a hash.

    `harness-v4-` may still appear in a phase body (P8 checks tags for a
    different reason). What may not appear is a tag inside the sentence that
    decides whether this round stops.
    """
    guard = B.render_advance_guard_step(next_phase)
    body = generate(phase)
    start = body.index(guard)
    # The stop condition is step 0. Step 1 begins at the next numbered line.
    tail = body[start:start + len(guard)]
    assert "tag" not in tail.lower(), (
        f"phase{phase}'s step-0 GUARD mentions a tag: {tail!r}"
    )


#: An instruction that treats a release tag as evidence the phase advanced.
#: Two shapes, because the defect used both: the OR inside the guard, and the
#: prose that described it to the operator afterwards.
_TAG_AS_ADVANCE_PROOF = re.compile(
    r"(?:already[ -]advanced/tagged)"
    r"|(?:tag already exists.{0,60}(?:ADVANCE: PASS|already advanced))"
    r"|(?:confirmed OR tag)"
    r"|(?:already advanced.{0,40}OR .{0,40}tag)",
    re.IGNORECASE,
)

def _instruction_texts() -> "list[tuple[str, str]]":
    """(name, text) for everything that reaches an agent as instructions.

    The GENERATED output, not the generator source: a generator's docstring
    naming the clause it deleted is a record, and Round 39 站4 already learned
    that a scan which reports its own explanatory prose reports the wrong
    thing. What ships is what the runtime reads, so the shipped files are
    scanned too (Round 36) — regenerating is a step, and a step can be missed.
    """
    out = [(f"generate({p})", generate(p)) for p in sorted(GENERATORS)]
    out.append(("generate_composite(run-all)", generate_composite("run-all")))
    shipped = REPO / ".claude" / "workflows"
    out.extend(
        (str(p.relative_to(REPO)), p.read_text(encoding="utf-8", errors="replace"))
        for p in sorted(shipped.glob("*.js"))
    )
    return out


def test_no_shipped_instruction_reads_a_tag_as_proof_the_phase_advanced():
    offenders: list[str] = []
    for name, text in _instruction_texts():
        for lineno, line in enumerate(text.splitlines(), 1):
            if _TAG_AS_ADVANCE_PROOF.search(line):
                offenders.append(f"{name}:{lineno}")
    assert not offenders, (
        "a harness-v4-* tag proves the git-tag step ran, not that "
        "advance-phase did — advance-phase is the only writer of "
        "state.json.current_phase, so that is the only thing the round-stop "
        "verdict may read:\n  " + "\n  ".join(offenders)
    )


def test_the_scan_covers_the_shipped_files_and_not_only_the_generator():
    names = [n for n, _ in _instruction_texts()]
    assert any(n.startswith("generate(") for n in names)
    assert any(n.endswith("run-all.js") for n in names), (
        "the shipped composite is not in the scan, so a stale .claude/ file "
        "would pass"
    )


def test_the_scan_would_see_the_defect_it_is_written_for():
    """Negative control: the exact texts Round 93 and Round 95 removed.

    Without this, a regex that matches nothing passes as loudly as a regex
    that matches nothing because the tree is clean.
    """
    removed_by_93 = (
        'and stop. Separately (for step 2 only): `git -C \' + REPO + \' tag -l '
        '"harness-v4-*" | head -1` — If Phase 7 is confirmed OR tag already '
        'exists, report "ADVANCE: PASS (already advanced)" and stop.'
    )
    removed_by_95 = (
        "message: 'Agent hit session/rate limit during Tag & Advance. Resume "
        "after quota reset — the GUARD step skips if already advanced/tagged.'"
    )
    assert _TAG_AS_ADVANCE_PROOF.search(removed_by_93)
    assert _TAG_AS_ADVANCE_PROOF.search(removed_by_95)
    # And it does not fire on P8's legitimate tag check, which asks whether a
    # tag was PUSHED and draws no conclusion about the phase.
    p8 = (
        '3. `git -C \' + REPO + \' tag -l \\"harness-v*\\" | head -3` — confirm '
        "any Phase 6 gate4 tag is pushed; if there is a P6 tag but `git push "
        "origin --tags` hasn't run yet, push tags."
    )
    assert not _TAG_AS_ADVANCE_PROOF.search(p8)


def test_the_session_block_tail_has_one_source():
    """The message that describes the guard travels with the guard.

    P6's copy said "skips if already advanced/tagged" for as long as the tag
    half existed, and for one commit after it did not.
    """
    tail = "the GUARD step skips if already advanced."
    for label in ("Advance", "Tag & Advance"):
        assert B.advance_session_block_message(label).endswith(tail)
    for phase in sorted(_ADVANCE_PHASES):
        body = generate(phase)
        assert tail in body, f"phase{phase} does not carry the shared tail"
