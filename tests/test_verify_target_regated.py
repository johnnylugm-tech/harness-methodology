"""The project's own end-to-end verification target, checked at every exit.

Round 46 站0. `execute_verification_target` runs `make verify-system` and
scores exit 0 → 100, anything else → 0. It appears in exactly one gate config:
`gate2_p3_exit.yaml`. `core/quality_gate/sab_parser.py:179` says so in prose
("execute_verification_target only in gate 2"), and Round 27 站2 had to widen
`_ALL_GATE_DIMENSION_STANDARD` past gate 4 alone precisely because of it.

The consequence is that a project declares what "the system works" means at
P3, the framework confirms it once, and from P4 to P8 the declaration is never
executed again. taskq-advance's `Makefile` at Phase 9 has a `verify-system`
that chains `test lint coverage` — none of the four steps its own SPEC §NFR-12
requires (alembic upgrade → suite → service smoke → downgrade/upgrade
round-trip) — and no gate after Gate 2 ever ran it.

Station 0's premise 3 dry-ran the target across the live projects: taskq,
taskq-plus, taskq-renew and taskq-advance all define `verify-system`;
taskq-api has no Makefile and no `.methodology/state.json` either (it is not a
live harness project). So re-gating asks nothing of the four that they did not
already satisfy at Gate 2.

Weight is 0.00 and the threshold is 100 — the `traceability` shape: it blocks
by threshold and contributes nothing to the composite, so no existing
dimension's weight moves.
"""

from __future__ import annotations

import pytest

from core.quality_gate.gate_thresholds import load_gate_dimensions

pytestmark = [pytest.mark.core]

_DIM = "execute_verification_target"


@pytest.mark.parametrize("gate", [2, 3, 4])
def test_every_phase_exit_gate_runs_the_projects_verify_target(gate: int):
    names = {d.get("name") for d in load_gate_dimensions(gate)}
    assert _DIM in names, (
        f"gate {gate} never executes the project's own verify-system target; "
        f"a Makefile that regressed after P3 would be invisible to it"
    )


@pytest.mark.parametrize("gate", [2, 3, 4])
def test_the_threshold_is_the_same_everywhere(gate: int):
    dim = next(d for d in load_gate_dimensions(gate) if d.get("name") == _DIM)
    assert float(dim.get("threshold", 0)) == 100.0, dim


@pytest.mark.parametrize("gate", [3, 4])
def test_the_new_entries_carry_no_weight(gate: int):
    """Gate 2 keeps its 0.10 — that weight is already in its composite and
    changing it would re-score a shipped gate. Gates 3 and 4 gain the check
    without moving any other dimension's share."""
    dim = next(d for d in load_gate_dimensions(gate) if d.get("name") == _DIM)
    assert float(dim.get("weight", -1)) == 0.0, dim


def test_the_prompt_no_longer_calls_it_gate_2_only():
    """`evaluate_dimension.md` is what the scoring agent reads. A heading that
    says "Gate 2 only" while gates 3 and 4 score it is the R17 drift shape."""
    from pathlib import Path
    text = Path("harness/ssi/prompts/evaluate_dimension.md").read_text(encoding="utf-8")
    heading = next(ln for ln in text.splitlines() if ln.startswith(f"### {_DIM}"))
    assert "Gate 2 only" not in heading, heading
