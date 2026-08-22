"""The gate verdict must be the last thing said about the tree (Round 69 站1).

`verify-gate` records a PASS together with `delivered_tree_digest(project)`,
and `advance-phase` refuses a phase whose exit gate has no PASS *for that
tree* (`core/quality_gate/gate_verify.has_matching_pass`,
`cli/phase_cmds.py` → `EX_ADVANCE_GATE_VERDICT_MISSING`). So every step that
writes a delivered file after the verdict silently invalidates it.

Three of the eight phase workflows do exactly that:

  * **P6, since long before this round.** `phase6-quality.js` records the
    Gate 4 verdict, then `Release Docs` writes `RELEASE_NOTES.md` and
    `FINAL_SIGN_OFF.md` — both at the project root, both git-tracked — and
    that step's own SCOPE RULES say "DO NOT re-run Gate 4". Measured in
    taskq-cc's `.methodology/gate_verify.jsonl`: four gate-4 verdicts at one
    commit (`11673af2`) across three different tree digests.
  * **P3 and P4, since dc92fb5**, which put the `preview-next-phase` fixer —
    a step whose whole purpose is to edit files — between the gate loop and
    the advance loop.

The fix is not to move those steps earlier (P6's Release Docs cannot move
before the gate it quotes). It is that a phase with an exit gate re-records
that gate's verdict as the first step of its advance, against the tree it is
about to advance — which is verbatim what `advance-phase`'s own [BLOCKED]
message already tells the operator to do.

Re-verifying is safe to make a rule because `verify-gate` does not write the
tree it measures: taskq-cc ran it twice at `11673af2` (12:40 and 12:45) and
recorded the identical digest `f8e8638ae7bd`; taskq-api twice at `4ffeb3a0`
and recorded `83675e3dcbd4` both times. `pins_that_verify_gate_writes_nothing`
below holds that property.
"""
from __future__ import annotations

import re

import pytest

from core.phase_topology import EXIT_GATE_MAP
from scripts.workflowgen import js_blocks as B
from scripts.workflowgen import spec_shared as S
from scripts.workflowgen.generate_workflows import generate

_LABEL = re.compile(r"label:\s*'([^']*)'")

# The only agent dispatches allowed to sit after the exit gate's verdict.
# Every one of them commits, tags, pushes or reads state — none authors a
# deliverable. A ninth name appearing here is a decision, not a typo.
_POST_VERDICT_LABELS = frozenset({
    "advance-r", "advance-verify-r", "tag-advance-r", "cleanup-r",
    "sync-", "sync-handover-note", "record-block",
})


def _labels_after_last_verdict(phase: int) -> list[str]:
    text = generate(phase)
    cut = text.rfind("verify-gate --project")
    assert cut != -1, f"phase {phase} has an exit gate but never runs verify-gate"
    return _LABEL.findall(text[cut:])


@pytest.mark.parametrize("phase", sorted(EXIT_GATE_MAP))
def test_no_authoring_dispatch_survives_the_verdict(phase: int) -> None:
    """After the last `verify-gate`, nothing may still be writing the tree."""
    offenders = [lbl for lbl in _labels_after_last_verdict(phase)
                 if lbl not in _POST_VERDICT_LABELS]
    assert not offenders, (
        f"P{phase} (exit gate {EXIT_GATE_MAP[phase]}): {offenders} run after the "
        f"verdict that `advance-phase` will check the tree digest against"
    )


def test_p6_release_docs_does_not_outlive_gate4() -> None:
    """E1, named on its own because it predates every other finding here.

    `release-docs` writes RELEASE_NOTES.md and FINAL_SIGN_OFF.md at the project
    root and is told not to re-run Gate 4, so on the pre-fix tree the Gate 4
    PASS can never describe the tree P6 advances.
    """
    assert "release-docs" not in _labels_after_last_verdict(6)


def test_advance_loop_reverifies_when_the_phase_has_an_exit_gate() -> None:
    text = B.render_advance_loop(phase=3, next_phase=4)
    assert "verify-gate --project" in text
    assert "--gate 2" in text
    assert f"--spec-threshold {S.D4_THRESHOLDS[3]}" in text


def test_advance_loop_does_not_reverify_when_there_is_no_exit_gate() -> None:
    """P5 and P7 close no gate; asking them to re-verify one would be a
    dispatch bought for nothing."""
    assert 5 not in EXIT_GATE_MAP and 7 not in EXIT_GATE_MAP
    for phase, nxt in ((5, 6), (7, 8)):
        assert "verify-gate" not in B.render_advance_loop(phase=phase, next_phase=nxt)


def test_the_d4_threshold_has_one_source() -> None:
    """The re-verify step needs the same number the gate loop used.

    Before this round it was three hand-written constants whose comments
    pointed at each other (`See spec_phase4._D4_THRESHOLD_P4`); a fourth copy
    inside the advance loop is how that becomes a drift.

    Asserting the VALUES are equal would be a guard that reads as enforcement
    and is not (Round 64's shape): a hand-typed `60.0` that happens to match
    passes it. So the assertion is structural — each `_D4_THRESHOLD_PN` must
    be a subscript of the shared map, not a literal.
    """
    import ast
    from pathlib import Path

    assert sorted(S.D4_THRESHOLDS) == sorted(EXIT_GATE_MAP)
    for phase in sorted(EXIT_GATE_MAP):
        src = Path(f"scripts/workflowgen/spec_phase{phase}.py").read_text(
            encoding="utf-8")
        assigned = [
            node.value
            for node in ast.walk(ast.parse(src))
            if isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name)
                    and t.id == f"_D4_THRESHOLD_P{phase}" for t in node.targets)
        ]
        assert len(assigned) == 1, f"P{phase} assigns the constant {len(assigned)}x"
        assert isinstance(assigned[0], ast.Subscript), (
            f"spec_phase{phase}._D4_THRESHOLD_P{phase} is a literal again — "
            f"it must read spec_shared.D4_THRESHOLDS"
        )


def test_verify_gate_does_not_write_the_tree_it_measures(tmp_path) -> None:
    """The premise the whole rule rests on: re-verifying cannot invalidate
    itself. Two `record_verdict` calls on an untouched tree must agree."""
    from core.quality_gate.gate_verify import record_verdict

    project = tmp_path
    (project / ".methodology").mkdir(parents=True)
    (project / "mod.py").write_text("x = 1\n", encoding="utf-8")
    first = record_verdict(project, gate=2, phase=3, checks={}, verdict="PASS")
    second = record_verdict(project, gate=2, phase=3, checks={}, verdict="PASS")
    assert first["delivered_tree_sha256"] == second["delivered_tree_sha256"]
