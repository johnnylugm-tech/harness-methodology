"""Round 105 站1b — the block existed, and nothing called it at P2's exit.

`arch_constraints.unconfigured_blocking_reason` has one caller,
`harness/harness_bridge.py:2117`, inside `finalize_gate`. Phase 2 has no gate,
and `phase_artifact_enforcer.PHASE_ARTIFACTS[Phase.PLAN]` requires exactly one
artifact: `SAD.md`. So a project can declare an architecture constraint whose
executor this framework runs, ship Phase 2 without the config that executor
reads, and hear about it in Phase 3.

taskq-sn did. Its SAB at the Phase 2 completion commit (`a6bf87f`) declares
`no_circular_dependencies` and its tree carries no `.importlinter`. Run
against that exact state, the framework's own function already produced the
whole answer:

    status:   unconfigured
    evidence: import-linter decides this, and this project has no contract
              of type 'layers'
    remedy:   add an [importlinter:contract:…] section with `type = layers`
              listing this project's layers, top to bottom

`.importlinter` first appears in `368f9db`, a Phase 3 "address Gate1 failing
dims" commit. Round 43's shape: the detection was complete, the message was
actionable, and no executor stood at that boundary.

Phase 2 only. Phase 3 onward is `finalize_gate`'s, and asking the same
question of the same source at two layers is Round 20.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

pytestmark = [pytest.mark.core]

REPO = Path(__file__).resolve().parents[1]

_LAYERS = """\
[importlinter]
root_package = taskq_api

[importlinter:contract:layers]
name = layered
type = layers
layers =
    taskq_api.api
    taskq_api.repository
"""


def _project(tmp_path: Path, constraints: list, importlinter: str = "") -> Path:
    root = tmp_path / "proj"
    (root / ".methodology").mkdir(parents=True)
    (root / ".methodology" / "SAB.json").write_text(
        json.dumps({"sab": {"architecture_constraints": constraints}}),
        encoding="utf-8")
    if importlinter:
        (root / ".importlinter").write_text(importlinter, encoding="utf-8")
    return root


def _check(project: Path, phase: int):
    from cli.advance_prechecks import _precheck_declared_constraints_are_configured

    return _precheck_declared_constraints_are_configured(phase, project)


# ── the block ───────────────────────────────────────────────────────────────

def test_a_declared_constraint_with_no_config_blocks_p2_exit(tmp_path, capsys) -> None:
    """taskq-sn's exact Phase 2 state. RED before this round."""
    from cli.exit_codes import EX_ADVANCE_CONSTRAINT_UNCONFIGURED

    project = _project(tmp_path, ["no_circular_dependencies"])
    assert _check(project, 2) == EX_ADVANCE_CONSTRAINT_UNCONFIGURED

    out = capsys.readouterr().out
    assert "no_circular_dependencies" in out, out
    assert "importlinter:contract" in out, (
        "the block did not name the config to write — the remedy is what "
        f"makes an earlier block cheaper than a later one:\n{out}")


def test_a_configured_constraint_does_not_block(tmp_path) -> None:
    """The whole point is that the project can satisfy it. With the contract
    present the same constraint is `enforced` and Phase 2 closes."""
    project = _project(tmp_path, ["no_circular_dependencies"], _LAYERS)
    assert _check(project, 2) is None


def test_a_constraint_nothing_can_check_does_not_block(tmp_path) -> None:
    """Round 54's rule, unchanged: `declared_only` is never blocked, because
    the only way to satisfy a block on a constraint nothing can decide is to
    delete a true statement from the SAB."""
    project = _project(tmp_path, ["fr07_round_trip_must_preserve_data"])
    assert _check(project, 2) is None


def test_no_declaration_is_not_a_violation(tmp_path) -> None:
    """Reverse control. A project that declares no architecture constraint has
    nothing to configure; Round 46's rule is that an absent claim is absent,
    not failed."""
    assert _check(_project(tmp_path, []), 2) is None
    empty = tmp_path / "bare"
    empty.mkdir()
    assert _check(empty, 2) is None


# ── the boundary it is asked at ─────────────────────────────────────────────

def test_it_is_asked_only_at_the_phase_that_delivers_the_sab(tmp_path) -> None:
    """Phase 3 onward already answers this inside `finalize_gate`. A second
    layer asking the same question of the same source is Round 20's mother,
    and the earlier one would be the one with less evidence."""
    project = _project(tmp_path, ["no_circular_dependencies"])
    for phase in (1, 3, 4, 5, 6, 7, 8):
        assert _check(project, phase) is None, f"phase {phase} answered"


def test_advance_phase_runs_the_check(tmp_path) -> None:
    """The wiring, read off the AST rather than the text.

    Round 30's mother is a mechanism with no live effect. The behaviour above
    proves the check decides correctly; this proves `advance-phase` asks it.
    """
    from tests.support.pipeline import inlined

    # `inlined` splices each `_precheck_*` body in, so the pipeline is read as
    # one function and the assertion is on what advance-phase actually does
    # rather than on a helper name a rename could move.
    fn = inlined("cli/phase_cmds.py", "_advance_prechecks",
                 helper_prefix="_precheck_")
    called = {
        n.func.attr if isinstance(n.func, ast.Attribute) else n.func.id
        for n in ast.walk(fn)
        if isinstance(n, ast.Call)
        and isinstance(n.func, (ast.Name, ast.Attribute))
    }
    assert "unconfigured_blocking_reason" in called, (
        "_advance_prechecks never asks whether a declared constraint's checker "
        "is configured — a project can close Phase 2 having declared one whose "
        "tool it never switched on, which is the state this round removes")


def test_the_verdict_comes_from_the_gate_s_own_functions(tmp_path, monkeypatch) -> None:
    """Counter-proof CP-4's shape.

    The rule for what counts as `unconfigured` is Round 51/54's and lives in
    `arch_constraints`. A second copy of it here would pass every test above
    and put one question on two definitions — so replacing the definition has
    to change what this check says.
    """
    import core.quality_gate.arch_constraints as ac

    monkeypatch.setattr(ac, "unconfigured_blocking_reason", lambda _rows: "")
    project = _project(tmp_path, ["no_circular_dependencies"])
    assert _check(project, 2) is None, (
        "the Phase 2 check did not go through "
        "core.quality_gate.arch_constraints.unconfigured_blocking_reason — it "
        "is deciding `unconfigured` with a rule of its own")


def test_the_exit_code_is_registered_with_a_description() -> None:
    """`cli/exit_codes.py` is the registry; a number with no entry is a code
    the operator meets with no way to look it up."""
    from cli.exit_codes import REGISTRY, EX_ADVANCE_CONSTRAINT_UNCONFIGURED

    assert EX_ADVANCE_CONSTRAINT_UNCONFIGURED in REGISTRY
    description = REGISTRY[EX_ADVANCE_CONSTRAINT_UNCONFIGURED]
    assert "importlinter" in description or "import-linter" in description
