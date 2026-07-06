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


@dataclass(frozen=True)
class PhaseSpec:
    num: int
    name: str                 # canonical long display name
    dir: str                  # on-disk phase directory (e.g. "09-maintenance")
    entry_gate: int | None    # gate that must PASS before the phase may start
    exit_gate: int | None     # composite gate that closes the phase
    per_fr_gate1: bool        # phase runs Gate 1 per-FR
    prerequisite: int | None  # phase that must be complete first (BVS order)


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


def phase_name(num: int, default: str | None = None) -> str:
    """Canonical long name for a phase; *default* (if given) for unknown nums."""
    spec = PHASES.get(num)
    if spec is None:
        if default is not None:
            return default
        raise KeyError(f"unknown phase {num}; valid: {list(VALID_PHASES)}")
    return spec.name
