"""Single source of truth for the phase/gate topology.

Every structural fact about the 9-phase pipeline lives here: phase numbers,
canonical names, on-disk directories, entry/exit gate mapping, per-FR Gate 1
membership, and the prerequisite chain. History shows why: these facts used
to be hand-copied across ~40 files, and every topology change shipped drift
incidents — the P8→9 handover crash (_VALID_PHASES missed one copy), the
"restore P5 to per-FR Gate 1 phases — revert incomplete removal" episode
(5e18f58), and the #109-#115 plan/CLI signature drift batch.

Rules:
- This module imports nothing from the framework (stdlib only), so scripts/,
  harness/, core/, and constitution/ can all import it without cycles.
- Consumers import the derived constants below instead of re-declaring
  literals. Mirrors that cannot import Python (JSON schemas, templates,
  display-variant payload maps) are pinned by tests/test_phase_topology_ssot.py,
  which names every stale copy when the topology changes.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class PhaseSpec:
    num: int
    name: str                 # canonical long display name
    dir: str                  # on-disk phase directory (e.g. "09-maintenance")
    entry_gate: Optional[int] # gate that must PASS before the phase may start
    exit_gate: Optional[int]  # composite gate that closes the phase
    per_fr_gate1: bool        # phase runs Gate 1 per-FR
    prerequisite: Optional[int]  # phase that must be complete first (BVS order)


PHASES: dict[int, PhaseSpec] = {
    1: PhaseSpec(1, "Requirements Specification", "01-requirements",
                 entry_gate=None, exit_gate=None, per_fr_gate1=False, prerequisite=None),
    2: PhaseSpec(2, "Architecture Design", "02-architecture",
                 entry_gate=None, exit_gate=None, per_fr_gate1=False, prerequisite=1),
    3: PhaseSpec(3, "Implementation", "03-development",
                 entry_gate=None, exit_gate=2, per_fr_gate1=True, prerequisite=2),
    4: PhaseSpec(4, "Testing", "04-testing",
                 entry_gate=2, exit_gate=3, per_fr_gate1=True, prerequisite=3),
    5: PhaseSpec(5, "Verification & Delivery", "05-verification",
                 entry_gate=3, exit_gate=None, per_fr_gate1=True, prerequisite=4),
    6: PhaseSpec(6, "Quality Assurance", "06-quality",
                 entry_gate=3, exit_gate=4, per_fr_gate1=False, prerequisite=5),
    7: PhaseSpec(7, "Risk Management", "07-risk",
                 entry_gate=4, exit_gate=None, per_fr_gate1=True, prerequisite=6),
    8: PhaseSpec(8, "Configuration Management", "08-config",
                 entry_gate=4, exit_gate=None, per_fr_gate1=True, prerequisite=7),
    9: PhaseSpec(9, "Maintenance", "09-maintenance",
                 entry_gate=4, exit_gate=None, per_fr_gate1=True, prerequisite=8),
}

MAX_PHASE = max(PHASES)
VALID_PHASES = range(1, MAX_PHASE + 1)

PER_FR_GATE1_PHASES: frozenset[int] = frozenset(
    p for p, spec in PHASES.items() if spec.per_fr_gate1
)

# advance-phase deliberately skips P9's Gate 1 records: --completed 9 is
# always BLOCKED (terminal steady state), so P9 FRs are checked per-CR by
# cr-close instead. Expressed as a derivation so it can never drift from
# PER_FR_GATE1_PHASES the way the old independent literals did.
ADVANCE_GATE1_CHECK_PHASES: frozenset[int] = PER_FR_GATE1_PHASES - frozenset({9})

ENTRY_GATE_MAP: dict[int, int] = {
    p: spec.entry_gate for p, spec in PHASES.items() if spec.entry_gate is not None
}
EXIT_GATE_MAP: dict[int, int] = {
    p: spec.exit_gate for p, spec in PHASES.items() if spec.exit_gate is not None
}
PHASE_PREREQUISITES: dict[int, int] = {
    p: spec.prerequisite for p, spec in PHASES.items() if spec.prerequisite is not None
}
PHASE_DIRS: dict[int, str] = {p: spec.dir for p, spec in PHASES.items()}


def gates_for_phase(num: int) -> set[int]:
    """Gate numbers a run at phase *num* can reach, derived from the table above.

    Round 56 站1. `cli/phase_cmds.PHASE_GATES` used to state this as a
    hand-written dict with keys 1..6, so `_phase_gate_tools` read
    `.get(phase, [])` at P7/P8/P9 and treated every gate as a future-phase
    concern — `critical` stayed empty and run-phase stopped blocking on
    missing tools at two of the four phases that run Gate 1 per-FR. The fix
    is not three more keys: the mapping already lives here, for all nine
    phases, and a second copy of it is the defect.

    Cumulative on purpose. A gate that closed an earlier phase is re-run as a
    DELTA check later (P4/P5/P7/P8 all re-run Gate 1, and advance-phase
    re-verifies earlier gates), so a tool that has since vanished must block
    rather than warn. Measured 2026-08-17: the cumulative derivation
    reproduces the hand-written table cell for cell at P1–P6.
    """
    gates: set[int] = set()
    for phase, spec in PHASES.items():
        if phase > num:
            continue
        if spec.entry_gate is not None:
            gates.add(spec.entry_gate)
        if spec.exit_gate is not None:
            gates.add(spec.exit_gate)
        if spec.per_fr_gate1:
            gates.add(1)
    return gates


def phase_name(num: int, default: Optional[str] = None) -> str:
    """Canonical long name for a phase; *default* (if given) for unknown nums."""
    spec = PHASES.get(num)
    if spec is None:
        if default is not None:
            return default
        raise KeyError(f"unknown phase {num}; valid: {list(VALID_PHASES)}")
    return spec.name
