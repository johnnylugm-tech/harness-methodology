"""PREFLIGHT_CHECKS registry gates — the structural fix for un-wired checks.

History: preflight/gate checks used to be composed by a hand-written dict
literal inside _do_preflight_all. Refactors repeatedly dropped a call without
anything noticing until a per-check wiring guard was written after the fact —
REGRESSION_GUARDS.yaml carries 7 such guards, each pinning one incident
(spec_alignment, property_spec, artifact_consistency, lessons ×2,
traceability-regen, spec_tracking).

The registry inverts the burden: core/phase_hooks.py::PREFLIGHT_CHECKS is the
ONLY source _do_preflight_all runs, and the completeness test here fails when
a preflight_* method exists on PhaseHooks without being registered (or listed
in NON_PIPELINE_PREFLIGHTS with a reason). Forgetting to wire a new check is
now a test failure, not a silent hole.

Scope note: _advance_prechecks, finalize-gate cross-checks and
_run_fast_preflight are heterogeneous exit-code pipelines (data dependencies,
early returns, distinct exit codes) — deliberately NOT registry-driven; their
wiring stays pinned by the existing guards.
"""

from __future__ import annotations

import inspect

from core.phase_hooks import (
    _DELAYED_BLOCKING_PREFLIGHTS,
    NON_PIPELINE_PREFLIGHTS,
    PREFLIGHT_CHECKS,
    PhaseHooks,
)

# The 15 result keys external readers rely on (details["fsm"], details["sab"], …).
_EXPECTED_KEYS = (
    "manifest_integrity",
    "fsm",
    "bvs_phase_order",
    "kill_switch",
    "previous_phase_artifacts",
    "drift_detection",
    "sab",
    "tool_registry",
    "traceability",
    "fr_spec_consistency",
    "spec_alignment",
    "property_spec",
    "artifact_consistency",
    "reliability_lint",
    "config_liveness",
)


def _completeness_violations(cls: type) -> list[str]:
    """Return violations of 'every preflight_* method is registered or excluded'.

    Factored out so the negative test can prove the check fires on an
    unregistered method.
    """
    defined = {
        name for name, _ in inspect.getmembers(cls, callable)
        if name.startswith("preflight_")
    }
    registered = {method for _, method in PREFLIGHT_CHECKS}
    excluded = set(NON_PIPELINE_PREFLIGHTS)
    violations = []
    for name in sorted(defined - registered - excluded):
        violations.append(
            f"{cls.__name__}.{name} is neither in PREFLIGHT_CHECKS nor in "
            f"NON_PIPELINE_PREFLIGHTS — an un-wired check is a silent hole"
        )
    for name in sorted(registered - defined):
        violations.append(
            f"PREFLIGHT_CHECKS references {name!r} which does not exist on "
            f"{cls.__name__}"
        )
    for name in sorted(excluded - defined):
        violations.append(
            f"NON_PIPELINE_PREFLIGHTS lists {name!r} which does not exist on "
            f"{cls.__name__} — stale exclusion"
        )
    return violations


def test_registry_complete():
    assert not _completeness_violations(PhaseHooks), (
        "\n".join(_completeness_violations(PhaseHooks))
    )


def test_registry_complete_fires_on_unregistered_method():
    """Negative: an extra preflight_* method must be reported."""

    class Probe(PhaseHooks):
        def preflight_bogus(self):  # pragma: no cover - never called
            return {"passed": True}

    violations = _completeness_violations(Probe)
    assert any("preflight_bogus" in v for v in violations), (
        "completeness check failed to flag an unregistered preflight method"
    )


def test_registry_methods_resolve():
    for key, method in PREFLIGHT_CHECKS:
        assert callable(getattr(PhaseHooks, method, None)), (
            f"PREFLIGHT_CHECKS entry ({key!r}, {method!r}) does not resolve to "
            f"a callable on PhaseHooks"
        )


def test_do_preflight_all_driven_by_registry(tmp_path, monkeypatch):
    """_do_preflight_all must run exactly the registry, in registry order —
    a hand-written dict sneaking back in cannot satisfy this for new entries."""
    hooks = PhaseHooks(str(tmp_path), phase=1)
    for key, method in PREFLIGHT_CHECKS:
        monkeypatch.setattr(
            PhaseHooks, method,
            (lambda k: lambda _self: {"passed": True, "sentinel": k})(key),
        )
    result = hooks._do_preflight_all()
    details = result["details"]
    assert list(details) == [key for key, _ in PREFLIGHT_CHECKS]
    for key, payload in details.items():
        assert payload["sentinel"] == key
    assert result["all_passed"] is True


def test_registry_keys_pinned():
    """Renaming a result key breaks external readers (details['fsm'] etc.) —
    pin the exact key tuple; extend deliberately, never rename casually."""
    assert tuple(key for key, _ in PREFLIGHT_CHECKS) == _EXPECTED_KEYS


def test_delayed_blocking_set_reads_registry_keys():
    """The obligation filter selects by result KEY, so it must spell keys.

    Round 43 站1. `_DELAYED_BLOCKING_PREFLIGHTS` is read by
    `PhaseHooks.preview_next_phase_blocking` against `_do_preflight_all`'s
    result dict, which is keyed by the first element of each PREFLIGHT_CHECKS
    pair. It carried `"sab_check"` — the METHOD name — from Round 14 A until
    Round 43, so every SAB finding was filtered out before it could become an
    obligation, and `_obligations_from_preflight`'s branch for it never ran.

    The completeness test above pins PREFLIGHT_CHECKS against the methods on
    PhaseHooks. This pins the other direction: a second registry that reads
    those keys may not name one that does not exist.
    """
    unknown = _DELAYED_BLOCKING_PREFLIGHTS - {key for key, _ in PREFLIGHT_CHECKS}
    assert not unknown, (
        f"_DELAYED_BLOCKING_PREFLIGHTS names {sorted(unknown)}, which "
        f"_do_preflight_all never produces — findings from those checks can "
        f"never become carry-over obligations"
    )
