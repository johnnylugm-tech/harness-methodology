"""Which baselines get written and which get compared were declared separately.

Round 111 站F4. `cli/gate_cmds.py` snapshots a baseline at every exit gate —
`args.gate in EXIT_GATE_MAP.values()`, so p3, p4 and p6. The reader was a
local dict inside `harness_bridge.prepare_gate`, `{6: 4}`, with no shared
source; `cli/checks/gates.py` names `crg_baseline_p4.json` as a literal.

The comment above that local dict asserted "there is no p3 baseline (Gate 2
has no architecture dim)". Nine corpus projects have one on disk.

WHAT THIS GUARD DOES AND DOES NOT CATCH, because the first version of it
claimed more than it can do. 236a187b removed a `{4: 3}` entry with the
reason "pointing at crg_baseline_p3, which is never generated". That entry
was NOT dangling in the sense checked here — phase 3 is an exit-gate phase
and `cmd_finalize_gate` has always written a baseline for it when
`.sessi-work/crg_metrics.json` exists. It was dangling for a runtime reason:
the belief that no CRG metrics exist at the P3 exit. The corpus says that
belief is now wrong nine times over, which is why the comment repeating it
had to go.

What is checkable statically is the other way the same silence arrives: a
source phase that no exit gate writes at all. `{4: 3}` reads naturally as
"gate 4 versus gate 3" and this map is keyed by PHASE, so a value that is
really a gate number (`{6: 5}`, `{4: 2}`) aims the reader at a file nothing
ever produces — and a missing baseline is a legitimate "no reference" state,
so nothing complains.

Why the p3/p6 copies are not deleted instead: `snapshot_baseline` runs
`should_write_baseline` on every call, and a score below the gate-4 floor is
refused with a degradation row (Round 37). Measured 2026-09-09 — omnibot's p3
(22.2) and taskq-renew's p6 (77.8) are both refused today. Stopping those
writes removes a witness, not a broken link.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from core.phase_topology import EXIT_GATE_MAP
from core.quality_gate.crg_baseline import BASELINE_COMPARISONS

pytestmark = [pytest.mark.core]

REPO = Path(__file__).resolve().parents[1]


def test_every_baseline_comparison_points_at_a_written_phase():
    """A comparison source nothing writes reads as "no baseline" forever."""
    written = set(EXIT_GATE_MAP)
    dangling = {
        phase: source for phase, source in BASELINE_COMPARISONS.items()
        if source not in written
    }
    assert not dangling, (
        f"these comparisons name a baseline phase no exit gate writes: "
        f"{dangling}. Exit-gate phases are {sorted(written)}; a value that is "
        f"really a gate number aims the reader at a file nothing produces, and "
        f"an absent baseline is a legitimate 'no reference' state, so nothing "
        f"complains"
    )


def test_the_bridge_does_not_keep_its_own_copy_of_the_map():
    """`prepare_gate` reads the owner's table rather than restating it.

    Two declarations of the same mapping is how the writer set and the reader
    set drifted apart in the first place.
    """
    tree = ast.parse((REPO / "harness" / "harness_bridge.py").read_text(encoding="utf-8"))
    literals = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and "baseline_phase_map" in t.id.lower()
                for t in node.targets)
        and isinstance(node.value, ast.Dict)
    ]
    assert not literals, (
        "harness_bridge declares its own baseline comparison dict; "
        "core.quality_gate.crg_baseline.BASELINE_COMPARISONS is the owner"
    )
