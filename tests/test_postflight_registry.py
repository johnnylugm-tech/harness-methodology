"""postflight_* completeness gates (弱點強化 Round 2, Station E).

_do_postflight_all is NOT registry-driven like _do_preflight_all
(tests/test_preflight_registry.py) — it has real data dependencies
(postflight_update_state needs `success`, which is only known after
bvs/drift/artifact_links have run and been combined with inline FR-approval
accounting that isn't even a postflight_* method). Forcing a call-order
registry onto that would be the wrong abstraction.

What CAN be guaranteed cheaply: every postflight_* method on PhaseHooks is
either referenced by name inside _do_postflight_all's source, or explicitly
excluded in NON_PIPELINE_POSTFLIGHTS with a reason. This closes the same
"silently un-wired check" hole Station A closed for preflight, without
pretending the postflight pipeline is homogeneous when it isn't.
"""

from __future__ import annotations

import inspect

from core.phase_hooks import (
    NON_PIPELINE_POSTFLIGHTS,
    POSTFLIGHT_CHECK_METHODS,
    PhaseHooks,
)


def _completeness_violations(cls: type) -> list[str]:
    """Return violations of 'every postflight_* method is declared or excluded'.

    Factored out so the negative test can prove the check fires on an
    unregistered method (same shape as test_preflight_registry.py).
    """
    defined = {
        name for name, _ in inspect.getmembers(cls, callable)
        if name.startswith("postflight_")
    }
    declared = set(POSTFLIGHT_CHECK_METHODS)
    excluded = set(NON_PIPELINE_POSTFLIGHTS)
    violations = []
    for name in sorted(defined - declared - excluded):
        violations.append(
            f"{cls.__name__}.{name} is neither in POSTFLIGHT_CHECK_METHODS nor "
            f"in NON_PIPELINE_POSTFLIGHTS — an un-wired check is a silent hole"
        )
    for name in sorted(declared - defined):
        violations.append(
            f"POSTFLIGHT_CHECK_METHODS references {name!r} which does not "
            f"exist on {cls.__name__}"
        )
    for name in sorted(excluded - defined):
        violations.append(
            f"NON_PIPELINE_POSTFLIGHTS lists {name!r} which does not exist on "
            f"{cls.__name__} — stale exclusion"
        )
    return violations


def test_registry_complete():
    assert not _completeness_violations(PhaseHooks), (
        "\n".join(_completeness_violations(PhaseHooks))
    )


def test_registry_complete_fires_on_unregistered_method():
    """Negative: an extra postflight_* method must be reported."""

    class Probe(PhaseHooks):
        def postflight_bogus(self):  # pragma: no cover - never called
            return {"passed": True}

    violations = _completeness_violations(Probe)
    assert any("postflight_bogus" in v for v in violations), (
        "completeness check failed to flag an unregistered postflight method"
    )


def test_declared_checks_called_in_do_postflight_all():
    """Every declared check must actually appear in _do_postflight_all's
    source — catches a name being added to the declared set without also
    being wired into the (necessarily hand-written) call sequence, or
    removed from the calls without being un-declared."""
    src = inspect.getsource(PhaseHooks._do_postflight_all)
    missing = [m for m in POSTFLIGHT_CHECK_METHODS if m not in src]
    assert not missing, (
        f"declared but not called in _do_postflight_all: {missing}"
    )
