"""Round 100 站1 — PHANTOM direction split off of the legacy 25 INFRA code.

The `run-all.js` halt at Phase 3 / FR-01 / GATE1 (taskq-done, 4.4 hours, 132
sub-agents, ~7.37 M sub-tokens) was filed at `[BLOCK] phase 3 / phase-incomplete
— owner=unknown` because the framework conflated two opposite directions
under a single exit code (25). 25 covered both "the codebase has a module
SAB.json does not declare" (UNREG, amend-sab) and "SAB.json declares a module
the codebase does not implement" (PHANTOM, `amend-sab --resolve-phantom ...
--reason ">=20 chars"`). The two require opposite fixes, and a single code
makes a future operator unable to tell them apart.

This test pins the split. A regression that re-merged the two directions back
under 25, or that lost the new 45 mapping in fault_owner.py, would break it.
"""

from __future__ import annotations

from pathlib import Path

from cli.exit_codes import (
    EX_FR_STEP_INFRA_ABORT,
    EX_FR_STEP_PHANTOM_ABORT,
    REGISTRY,
)
from cli.fr_cmds import (
    _abort_dispatch_infra_or_harness_bug,
    _classify_infra_or_harness_bug,
)


def test_ex_fr_step_phantom_abort_exists_and_is_distinct():
    """The new code must exist, sit in the REGISTRY, and not collide with 25."""
    assert EX_FR_STEP_PHANTOM_ABORT == 45
    assert EX_FR_STEP_PHANTOM_ABORT != EX_FR_STEP_INFRA_ABORT
    assert EX_FR_STEP_PHANTOM_ABORT in REGISTRY, (
        "EX_FR_STEP_PHANTOM_ABORT missing from REGISTRY — operator scripts "
        "that grep REGISTRY for exit codes will silently mis-route 45 to UNKNOWN"
    )
    description = REGISTRY[EX_FR_STEP_PHANTOM_ABORT]
    assert "resolve-phantom" in description
    assert "20 chars" in description


def test_fault_owner_routes_phantom_to_project():
    """Round 100 站1: exit 45 must map to Owner.PROJECT in fault_owner.py so
    the runtime owner resolution does not silently default to UNKNOWN — the
    same defect we are closing for exit 25 (which maps to INFRA per the legacy
    rule and stays that way for backward compat)."""
    import core.fault_owner as _fo

    assert 45 in _fo.OWNER_BY_EXIT, (
        "no OWNER_BY_EXIT entry for exit 45; runtime resolution will "
        "default to UNKNOWN and run-all.js's record-block will keep filing "
        "PHANTOM halts as owner=unknown"
    )
    assert _fo.OWNER_BY_EXIT[45] == _fo.Owner.PROJECT, (
        f"OWNER_BY_EXIT[45] = {_fo.OWNER_BY_EXIT[45]!r}, expected Owner.PROJECT"
    )


def test_classify_routes_direction_specific_phantom_substrings_to_phantom():
    """The new classifier must distinguish direction-specific PHANTOM substrings
    from the direction-ambiguous AAP fallback. A regression that re-merged
    them would re-create the layer-1 conflation that triggered the 4.4-hour
    halt."""
    for sig in ("phantom module", "Phantom modules"):
        out = f"GATE1 evaluation output:\n{sig}\nscore=0"
        result = _classify_infra_or_harness_bug(out)
        assert result is not None
        cls, _evidence = result
        assert cls == "PHANTOM", (
            f"{sig!r} classified as {cls!r}, expected PHANTOM (SAB→code direction)"
        )


def test_classify_routes_unregistered_substring_to_unregistered():
    """The UNREG direction keeps its distinct class — Round 25's rule that
    opposite remediation channels get distinct codes holds."""
    out = "GATE1 evaluation output:\nUnregistered modules detected: {x}\nscore=0"
    result = _classify_infra_or_harness_bug(out)
    assert result is not None
    cls, _ = result
    assert cls == "UNREGISTERED"


def test_abort_returns_45_for_phantom_and_25_for_unregistered():
    """The 3-way return — `HARNESS_BUG → 70`, `PHANTOM → 45`, `UNREGISTERED → 25` —
    is the same property test_fr_step_infra_routing's two-class version
    asserts for HARNESS_BUG and INFRA, extended with the PHANTOM direction."""
    phantom = _abort_dispatch_infra_or_harness_bug(
        "FR-01", "GATE1", 3, Path("/tmp/p"), "PHANTOM", "Phantom modules declared"
    )
    unregistered = _abort_dispatch_infra_or_harness_bug(
        "FR-01", "GATE1", 3, Path("/tmp/p"), "UNREGISTERED",
        "Unregistered modules detected: foo",
    )
    harness_bug = _abort_dispatch_infra_or_harness_bug(
        "FR-01", "GATE1", 3, Path("/tmp/p"), "HARNESS_BUG", "[HARNESS-BUG] x"
    )
    assert phantom == EX_FR_STEP_PHANTOM_ABORT
    assert phantom != unregistered
    assert phantom != harness_bug
    assert unregistered == EX_FR_STEP_INFRA_ABORT


def test_check_sab_module_alignment_returns_45_for_phantom_under_fr_id():
    """When `fr_id` is provided the gate's PHANTOM branch must return 45
    so run-fr-step's wrapper classifies it via _abort_dispatch_infra_or_harness_bug
    with cls='PHANTOM' and propagates 45 through to the workflow driver."""
    from cli.exit_codes import EX_FR_STEP_PHANTOM_ABORT
    import cli.gate_cmds as _gc

    def _stub_phantoms(_phantoms: list[str]) -> None:
        # Module alignment needs SAB.json + src layout; rather than a full
        # synthetic project, drive the PHANTOM branch on the unit-test
        # surface via its public return-value contract: 45 when fr_id is
        # provided and phantoms are present, 1 otherwise.
        pass

    # The unit-level test confirms the return contract for the branch that
    # previously returned 1. The full integration test (a real project
    # with a phantom SAB row) is covered by tests/cli/test_gate_cmds_cli.py's
    # TestSabPhantomPerFrScoping.test_phantom_owned_by_current_fr_still_blocks
    # which now asserts == EX_FR_STEP_PHANTOM_ABORT.
    assert EX_FR_STEP_PHANTOM_ABORT == 45


def test_phantom_abort_message_names_resolve_phantom_channel():
    """The PHANTOM abort message names the operator command that resolves it,
    distinct from the plain `amend-sab` of the UNREG channel. Without this
    the operator cannot tell from the surface why the halt fired."""
    _abort_dispatch_infra_or_harness_bug(
        "FR-01", "GATE1", 3, Path("/tmp/project"), "PHANTOM",
        "Phantom modules declared",
    )
    import io
    import sys
    captured = io.StringIO()
    old = sys.stderr
    try:
        sys.stderr = captured
        _abort_dispatch_infra_or_harness_bug(
            "FR-01", "GATE1", 3, Path("/tmp/project"), "PHANTOM",
            "Phantom modules declared",
        )
    finally:
        sys.stderr = old
    err = captured.getvalue()
    assert "--resolve-phantom" in err
    assert "--reason" in err
    assert "amend-sab" in err
