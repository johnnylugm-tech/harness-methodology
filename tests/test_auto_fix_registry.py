"""Round 48 站0 — twenty-one repair strategies, one of them reachable.

Measured 2026-08-12. `core/auto_fix/` is 2076 lines. `STRATEGY_REGISTRY` maps
13 problem types onto fix functions, and `CLASSIFICATION_TABLE` routes 31
detector outcomes into them. Exactly ONE has a production caller:
`fix_missing_traceability`, reached from `core/phase_hooks.py:853` at P5+.
The other twelve are unreachable from any production path.

That alone would be Round 30's pattern (a half-built mechanism). Reading them
makes it sharper, and it is the reason this test declares rather than wires:

  fix_keyword_density        appends a dimension's keywords to a markdown file
                             to raise that dimension's constitution score
  fix_constitution_dimension the same, keyed on the failing dimension
  fix_section_headers        appends "## <missing section>\\n\\nTBD"
  fix_hollow_content         appends TBD blocks to files under 200 bytes —
                             to satisfy the hollow-content checker
  fix_missing_artifact       writes a TBD stub for a missing deliverable
  fix_missing_aspice_docs    the same, for ASPICE docs
  fix_gap_critical           writes "<gap>_stub.md" with TBD acceptance criteria
  fix_drift                  appends "<!-- AUTO-FIX: drift reconciliation stub -->"
  fix_low_coverage           writes `assert True` test stubs
  fix_pytest_failures        rewrites failing assertions to the observed value

Every one of those makes a checker quiet without making its subject true.
Wiring them would manufacture exactly the artifacts Rounds 27, 32, 42 and 46
exist to refuse. So the completeness rule this test enforces is deliberately
two-sided: a strategy is either LIVE — declared, reachable, and paired with
the check it re-runs to prove the repair worked — or RETIRED with a reason.
There is no third state, and "registered but unreachable" was the third state.
"""

from __future__ import annotations

import pytest


def test_every_reachable_problem_type_is_either_live_or_retired():
    """The denominator is what can be REACHED or DISPATCHED, not one of the two.

    Two problem types (`hardcoded_secrets`, `hard_rule_violation`) are routed to
    by CLASSIFICATION_TABLE but have no STRATEGY_REGISTRY entry at all — they
    classify HUMAN_REQUIRED and the engine escalates before it would look one
    up. Comparing against the strategy registry alone would leave those two
    undeclared, and comparing against the table alone would miss a registered
    strategy nothing routes to. The union is the honest set.
    """
    from core.auto_fix.classifier import CLASSIFICATION_TABLE
    from core.auto_fix.strategies import STRATEGY_REGISTRY
    from core.auto_fix.wiring import LIVE_STRATEGIES, RETIRED_STRATEGIES

    reachable = set(STRATEGY_REGISTRY) | {
        entry["problem_type"] for entry in CLASSIFICATION_TABLE.values()
    }
    declared = set(LIVE_STRATEGIES) | set(RETIRED_STRATEGIES)
    assert reachable == declared, (
        "every problem_type reachable from CLASSIFICATION_TABLE or dispatchable "
        "through STRATEGY_REGISTRY must be declared LIVE (with a production "
        "caller and a re-verify) or RETIRED (with a reason). "
        f"undeclared={sorted(reachable - declared)} "
        f"declared-but-unreachable={sorted(declared - reachable)}"
    )


def test_no_strategy_is_both_live_and_retired():
    from core.auto_fix.wiring import LIVE_STRATEGIES, RETIRED_STRATEGIES

    overlap = set(LIVE_STRATEGIES) & set(RETIRED_STRATEGIES)
    assert not overlap, f"declared both live and retired: {sorted(overlap)}"


def test_every_live_strategy_re_verifies_its_own_repair():
    """R47's shape, made a precondition rather than a habit: detect → repair →
    **re-detect** → block with the true cause. A repair that reports success
    without re-running the check that failed is asserting the fix worked,
    which is exactly the class Round 24 named (a field existing is not the
    field being true)."""
    from core.auto_fix.wiring import LIVE_STRATEGIES

    missing = [name for name, spec in LIVE_STRATEGIES.items() if spec.reverify is None]
    assert not missing, (
        "LIVE strategies with no re-verify — they would report success on the "
        f"strength of having written something: {sorted(missing)}"
    )


def test_every_live_strategy_names_its_production_caller():
    """A caller that is only a docstring is how the previous twenty became
    unreachable without anybody noticing."""
    from core.auto_fix.wiring import LIVE_STRATEGIES

    for name, spec in LIVE_STRATEGIES.items():
        assert spec.caller and "::" in spec.caller, (
            f"LIVE strategy {name!r} must name its production call site as "
            f"'path/to/file.py::function'; got {spec.caller!r}"
        )


def test_every_retirement_states_a_reason():
    from core.auto_fix.wiring import RETIRED_STRATEGIES

    for name, reason in RETIRED_STRATEGIES.items():
        assert isinstance(reason, str) and len(reason.strip()) >= 40, (
            f"retiring {name!r} needs a reason a later round can audit, not a "
            f"label; got {reason!r}"
        )


def test_the_classification_table_routes_only_to_declared_strategies():
    """`scripts/validate_cross_refs.py` already checks CLASSIFICATION_TABLE
    against STRATEGY_REGISTRY. It cannot see reachability, so a detector could
    route into a strategy that no caller ever reaches — which is the state all
    31 table entries but one are in today."""
    from core.auto_fix.classifier import CLASSIFICATION_TABLE
    from core.auto_fix.wiring import LIVE_STRATEGIES, RETIRED_STRATEGIES

    declared = set(LIVE_STRATEGIES) | set(RETIRED_STRATEGIES)
    undeclared = sorted(
        {entry["problem_type"] for entry in CLASSIFICATION_TABLE.values()} - declared
    )
    assert not undeclared, (
        f"CLASSIFICATION_TABLE routes to undeclared problem_type(s): {undeclared}"
    )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__]))
