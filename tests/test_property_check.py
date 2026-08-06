"""Tests for the lightweight property-declaration gate (Direction B).

Design (the "lightweight正解" the user approved):
  * property testing is NOT a new scored gate dimension — no scorer, no weight
    rebalancing, no per-FR mutation. It stays opt-in.
  * an FR may declare universal invariants in a TEST_SPEC `**Properties**` table
    (columns: property_id | invariant | applies_to) — structurally distinct from
    the example `Sub-assertions` table (predicate | applies_to).
  * declared invariants are run through the SAME decidable red_assertion engine
    (check_test_spec_consistency): an invariant that is false for a case it
    `applies_to` is a spec contradiction → error, before any test is written.
  * once an FR declares a property, its test must actually EXECUTE it with a
    property-based tool (hypothesis / fast-check) — declaring an invariant and
    never testing it is blocked from P4. Semantic strength of the property test
    is backed by the existing mutation_testing dimension, not re-scored here.
"""

from __future__ import annotations

from pathlib import Path

from core.quality_gate.property_check import check_property_spec
from core.utils.project_layout import ProjectLayout


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _spec(fr_body: str) -> str:
    return "# TEST_SPEC\n\n## Functional Requirement Test Cases\n\n" + fr_body


def _project(tmp_path: Path, spec_body: str, *, test_files: dict | None = None) -> Path:
    _write(ProjectLayout(tmp_path).test_spec_path, _spec(spec_body))
    for name, content in (test_files or {}).items():
        _write(tmp_path / "tests" / name, content)
    return tmp_path


_FR_WITH_PROP = """### FR-01: encode/decode roundtrip

| # | Test Function | Inputs | Type | Derivation |
|---|---|---|---|---|
| 1 | `test_fr01_roundtrip` | source="abc" | happy_path | Q1 |

**Properties** (universal invariants — verify with hypothesis; strength via mutation):
| property_id | invariant | applies_to |
|---|---|---|
| P1-len | `len(source) == 3` | 1 |
"""

_HYPOTHESIS_TEST = (
    "from hypothesis import given, strategies as st\n\n"
    "@given(st.text())\n"
    "def test_fr01_roundtrip(source):\n"
    "    assert decode(encode(source)) == source\n"
)


def test_self_consistent_property_without_test_blocks_execution(tmp_path: Path) -> None:
    # Invariant holds for the case (len("abc")==3) → no consistency error, but
    # no hypothesis test exists → execution-existence violation.
    proj = _project(tmp_path, _FR_WITH_PROP)
    vs = check_property_spec(proj, require_execution=True)
    assert [v.check_type for v in vs] == ["property_not_executed"]
    assert "FR-01" in vs[0].message


def test_property_with_hypothesis_test_passes(tmp_path: Path) -> None:
    proj = _project(tmp_path, _FR_WITH_PROP,
                    test_files={"test_fr01.py": _HYPOTHESIS_TEST})
    assert check_property_spec(proj, require_execution=True) == []


def test_contradictory_invariant_blocks(tmp_path: Path) -> None:
    # Invariant len(source)==5 is FALSE for source="abc" → spec contradiction,
    # caught by the reused red_assertion engine regardless of execution.
    body = _FR_WITH_PROP.replace("`len(source) == 3`", "`len(source) == 5`")
    proj = _project(tmp_path, body, test_files={"test_fr01.py": _HYPOTHESIS_TEST})
    vs = check_property_spec(proj, require_execution=True)
    errs = [v for v in vs if v.severity == "error"]
    assert errs and any("predicate" in v.check_type or "false" in v.check_type.lower()
                        for v in errs), [v.check_type for v in errs]


def test_no_properties_declared_is_noop(tmp_path: Path) -> None:
    body = (
        "### FR-01: plain\n\n"
        "| # | Test Function | Inputs | Type | Derivation |\n"
        "|---|---|---|---|---|\n"
        "| 1 | `test_fr01_x` | source=\"abc\" | happy_path | Q1 |\n"
    )
    proj = _project(tmp_path, body)
    assert check_property_spec(proj, require_execution=True) == []


def test_execution_not_required_before_p4(tmp_path: Path) -> None:
    # Same self-consistent property, but require_execution=False (pre-P4):
    # missing hypothesis test is not yet an error.
    proj = _project(tmp_path, _FR_WITH_PROP)
    assert check_property_spec(proj, require_execution=False) == []


def test_missing_test_spec_is_noop(tmp_path: Path) -> None:
    assert check_property_spec(tmp_path, require_execution=True) == []


def test_free_variable_invariant_needs_no_case_eval_but_requires_execution(
    tmp_path: Path,
) -> None:
    # Universal invariant referencing a free variable x (not a case input):
    # not evaluable against cases (engine does not guess) → no false error,
    # but declaring it still requires a hypothesis test.
    body = _FR_WITH_PROP.replace("`len(source) == 3`", "`f(x) == f(f(x))`")
    proj = _project(tmp_path, body)
    vs = check_property_spec(proj, require_execution=True)
    assert all(v.severity != "error" or v.check_type == "property_not_executed"
               for v in vs)
    assert any(v.check_type == "property_not_executed" for v in vs)


# ── preflight wiring (execution phase-gated + composition guard) ─────────────


def _hooks(project: Path, phase: int):
    from core.phase_hooks import PhaseHooks

    return PhaseHooks(str(project), phase=phase, enable_kill_switch=False)


def test_preflight_execution_required_only_from_p4(tmp_path: Path) -> None:
    proj = _project(tmp_path, _FR_WITH_PROP)  # property declared, no hypothesis test
    r3 = _hooks(proj, 3).preflight_property_spec()  # pre-P4: not required yet
    assert r3["passed"] is True
    r4 = _hooks(proj, 4).preflight_property_spec()  # P4: must be executed
    assert r4["passed"] is False and r4["errors"] == 1


def test_preflight_noop_without_declarations(tmp_path: Path) -> None:
    body = (
        "### FR-01: plain\n\n"
        "| # | Test Function | Inputs | Type | Derivation |\n"
        "|---|---|---|---|---|\n"
        "| 1 | `test_fr01_x` | source=\"abc\" | happy_path | Q1 |\n"
    )
    proj = _project(tmp_path, body)
    r = _hooks(proj, 4).preflight_property_spec()
    assert r["passed"] is True and r.get("skipped") is True


def test_property_spec_is_wired_into_preflight_all() -> None:
    """Composition guard: the gate must stay in the blocking aggregate
    (REGRESSION_GUARDS-pinned).

    Mechanism upgraded with the PREFLIGHT_CHECKS registry: membership in the
    registry IS composition — tests/test_preflight_registry.py proves
    _do_preflight_all runs exactly the registry."""
    from core.phase_hooks import PREFLIGHT_CHECKS

    assert ("property_spec", "preflight_property_spec") in PREFLIGHT_CHECKS, (
        "property_spec gate dropped from PREFLIGHT_CHECKS — declared but "
        "unexecuted / contradictory property invariants would stop blocking"
    )


def test_ast_check_avoids_false_positive(tmp_path: Path) -> None:
    # A file with an unrelated property test and a loose FR-01 comment 
    # should NOT be treated as a property test for FR-01.
    body = (
        "### FR-01: something\n\n"
        "| # | Test Function | Inputs | Type | Derivation |\n"
        "|---|---|---|---|---|\n"
        "| 1 | `test_fr01_x` | source=\"abc\" | happy_path | Q1 |\n"
        "\n"
        "**Properties**\n"
        "| property_id | invariant | applies_to |\n"
        "|---|---|---|\n"
        "| P1 | `len(source) == 3` | 1 |\n"
    )
    test_content = (
        "# FR-01: some notes\n\n"
        "@given(st.integers())\n"
        "def test_unrelated():\n"
        "    pass\n"
    )
    proj = _project(tmp_path, body, test_files={"test_fr01.py": test_content})
    vs = check_property_spec(proj, require_execution=True)
    assert any(v.check_type == "property_not_executed" for v in vs)


# ── Round 14 B: fulfill_phase column (back-compat + new column) ────────────


def test_fulfill_phase_missing_column_falls_back_to_p4(tmp_path: Path) -> None:
    """Back-compat: tables that omit `fulfill_phase` must keep the historical
    P4 trigger — preflight blocks at P4, informational at P3 (regression guard
    for the dynamic `_max_fulfill` default introduced in Round 14 B)."""
    proj = _project(tmp_path, _FR_WITH_PROP)  # no fulfill_phase column
    r3 = _hooks(proj, 3).preflight_property_spec()
    assert r3["passed"] is True
    assert r3.get("fulfill_phase") == 4
    r4 = _hooks(proj, 4).preflight_property_spec()
    assert r4["passed"] is False and r4["errors"] == 1
    assert r4.get("fulfill_phase") == 4


def _spec_with_fulfill_phase(fulfill_row: str) -> str:
    """Build a TEST_SPEC body whose FR-01 Properties table includes a
    `fulfill_phase` column matching the legacy `(property_id, invariant,
    applies_to)` shape plus a 4th column equal to `fulfill_row`."""
    return (
        "### FR-01: encode/decode roundtrip\n\n"
        "| # | Test Function | Inputs | Type | Derivation |\n"
        "|---|---|---|---|---|\n"
        "| 1 | `test_fr01_roundtrip` | source=\"abc\" | happy_path | Q1 |\n\n"
        "**Properties** (universal invariants — verify with hypothesis; "
        "strength via mutation):\n"
        "| property_id | invariant | applies_to | fulfill_phase |\n"
        "|---|---|---|---|\n"
        f"| {fulfill_row}\n"
    )


def test_fulfill_phase_column_blocks_according_to_value(tmp_path: Path) -> None:
    """When `fulfill_phase=5` is declared, the gate is informational at P4
    and blocking at P5 (dynamic — replaces the hard-coded P4 trigger)."""
    body = _spec_with_fulfill_phase(
        "P1-len | `len(source) == 3` | 1 | 5 |")
    proj = _project(tmp_path, body)

    r3 = _hooks(proj, 3).preflight_property_spec()
    assert r3["passed"] is True
    assert r3.get("fulfill_phase") == 5

    r4 = _hooks(proj, 4).preflight_property_spec()
    assert r4["passed"] is True  # P4 < fulfill_phase=5 → not blocking yet
    assert r4.get("fulfill_phase") == 5

    r5 = _hooks(proj, 5).preflight_property_spec()
    assert r5["passed"] is False and r5["errors"] == 1
    assert r5.get("fulfill_phase") == 5


def test_fulfill_phase_empty_or_invalid_cell_falls_back_to_p4(tmp_path: Path) -> None:
    """Empty cell or non-int cell in `fulfill_phase` → None in the parsed
    SubAssertion → default 4 (back-compat)."""
    body_empty = _spec_with_fulfill_phase(
        "P1-len | `len(source) == 3` | 1 |  |")
    proj = _project(tmp_path, body_empty)
    r4 = _hooks(proj, 4).preflight_property_spec()
    assert r4["passed"] is False and r4["errors"] == 1
    assert r4.get("fulfill_phase") == 4  # empty → default P4

    body_garbage = _spec_with_fulfill_phase(
        "P1-len | `len(source) == 3` | 1 | soon |")
    proj2 = _project(tmp_path, body_garbage)
    r4b = _hooks(proj2, 4).preflight_property_spec()
    assert r4b["passed"] is False and r4b["errors"] == 1
    assert r4b.get("fulfill_phase") == 4  # non-int → default P4


def test_fulfill_phase_uses_max_across_frs(tmp_path: Path) -> None:
    """When multiple FRs declare fulfill_phases, the gate uses the MAX
    (not min / average) — strict semantics: a single 'later' FR still
    fails open at P4 if the others are valid."""
    body = (
        "### FR-01: early\n\n"
        "| # | Test Function | Inputs | Type | Derivation |\n"
        "|---|---|---|---|---|\n"
        "| 1 | `test_fr01_x` | source=\"abc\" | happy_path | Q1 |\n\n"
        "**Properties**\n"
        "| property_id | invariant | applies_to | fulfill_phase |\n"
        "|---|---|---|---|\n"
        "| P1 | `len(source) == 3` | 1 | 4 |\n\n"
        "### FR-02: late\n\n"
        "| # | Test Function | Inputs | Type | Derivation |\n"
        "|---|---|---|---|---|\n"
        "| 1 | `test_fr02_x` | source=\"abc\" | happy_path | Q1 |\n\n"
        "**Properties**\n"
        "| property_id | invariant | applies_to | fulfill_phase |\n"
        "|---|---|---|---|\n"
        "| P2 | `len(source) == 3` | 1 | 6 |\n"
    )
    proj = _project(tmp_path, body)
    # P4 < max(4, 6) = 6 → execution not required → passes
    r4 = _hooks(proj, 4).preflight_property_spec()
    assert r4["passed"] is True
    assert r4.get("fulfill_phase") == 6  # max(4, 6) — the later one wins
    # P6 ≥ max(4, 6) → execution required → both FRs fail (no @given test)
    r6 = _hooks(proj, 6).preflight_property_spec()
    assert r6["passed"] is False and r6["errors"] == 2
    assert r6.get("fulfill_phase") == 6


# ══════════════════════════════════════════════════════════════════════════════
# Structural tautology detection (VAR == VAR, LITERAL == LITERAL, etc.)
# ══════════════════════════════════════════════════════════════════════════════

_TAUT_FR = """### FR-01: tautology round-trip

| # | Test Function | Inputs | Type | Derivation |
|---|---|---|---|---|
| 1 | `test_fr01_roundtrip` | source="abc" | happy_path | Q1 |

**Properties**
| property_id | invariant | applies_to |
|---|---|---|
| P1-id | `source == source` | 1 |
"""


def test_tautology_var_eq_var_skips_execution(tmp_path: Path) -> None:
    """An FR whose only invariant is VAR == VAR → no property_not_executed error."""
    proj = _project(tmp_path, _TAUT_FR)
    vs = check_property_spec(proj, require_execution=True)
    errors = [v for v in vs if v.severity == "error"]
    assert not errors, f"tautology-only FR should have 0 errors, got: {errors}"
    infos = [v for v in vs if v.check_type == "tautology_placeholder"]
    assert len(infos) == 1


def test_tautology_literal_eq_literal_skips_execution(tmp_path: Path) -> None:
    body = _TAUT_FR.replace("`source == source`", '`"hello" == "hello"`')
    proj = _project(tmp_path, body)
    vs = check_property_spec(proj, require_execution=True)
    errors = [v for v in vs if v.severity == "error"]
    assert not errors


def test_tautology_nonempty_literal_neq_empty_skips_execution(
    tmp_path: Path,
) -> None:
    body = _TAUT_FR.replace("`source == source`", '`"hello" != ""`')
    proj = _project(tmp_path, body)
    vs = check_property_spec(proj, require_execution=True)
    errors = [v for v in vs if v.severity == "error"]
    assert not errors


def test_mixed_tautology_and_real_still_requires_execution(
    tmp_path: Path,
) -> None:
    """A single real invariant keeps the FR in the execution path."""
    body = (
        "### FR-01: mixed\n\n"
        "| # | Test Function | Inputs | Type | Derivation |\n"
        "|---|---|---|---|---|\n"
        "| 1 | `test_fr01_x` | source=\"abc\" | happy_path | Q1 |\n\n"
        "**Properties**\n"
        "| property_id | invariant | applies_to |\n"
        "|---|---|---|\n"
        "| P1-id | `source == source` | 1 |\n"
        "| P1-real | `len(source) == 3` | 1 |\n"
    )
    proj = _project(tmp_path, body)
    vs = check_property_spec(proj, require_execution=True)
    errors = [v for v in vs if v.severity == "error"]
    not_executed = [v for v in errors if v.check_type == "property_not_executed"]
    assert len(not_executed) == 1, (
        f"mixed invariants should still require execution, got {len(not_executed)} errors"
    )
    # tautology should still be reported as info
    infos = [v for v in vs if v.check_type == "tautology_placeholder"]
    assert len(infos) == 1


def test_non_tautology_still_requires_execution(tmp_path: Path) -> None:
    """`len(source) > 0` is a real constraint, not a structural identity."""
    body = (
        "### FR-01: real\n\n"
        "| # | Test Function | Inputs | Type | Derivation |\n"
        "|---|---|---|---|---|\n"
        "| 1 | `test_fr01_x` | source=\"abc\" | happy_path | Q1 |\n\n"
        "**Properties**\n"
        "| property_id | invariant | applies_to |\n"
        "|---|---|---|\n"
        "| P1-real | `len(source) > 0` | 1 |\n"
    )
    proj = _project(tmp_path, body)
    vs = check_property_spec(proj, require_execution=True)
    errors = [v for v in vs if v.severity == "error"]
    assert any(v.check_type == "property_not_executed" for v in errors)


def test_tautology_with_output_var_still_requires_execution(
    tmp_path: Path,
) -> None:
    """A tautology referencing an output variable (Decider B path) is NOT
    a structural identity — it constrains the output relative to itself."""
    body = (
        "### FR-01: output tautology\n\n"
        "| # | Test Function | Inputs | Type | Derivation |\n"
        "|---|---|---|---|---|\n"
        "| 1 | `test_fr01_x` | source=\"abc\" | happy_path | Q1 |\n\n"
        "**Properties**\n"
        "| property_id | invariant | applies_to |\n"
        "|---|---|---|\n"
        "| P1-out | `result == result` | 1 |\n"
    )
    proj = _project(tmp_path, body)
    vs = check_property_spec(proj, require_execution=True)
    # result is NOT a case input → goes through Decider B
    # → not classified as structural tautology → execution required
    assert any(v.check_type == "property_not_executed" for v in vs), (
        "output-variable tautology should still require execution"
    )


def test_tautology_info_at_p3(tmp_path: Path) -> None:
    """At P3 (require_execution=False), tautology info still appears."""
    proj = _project(tmp_path, _TAUT_FR)
    vs = check_property_spec(proj, require_execution=False)
    assert not [v for v in vs if v.severity == "error"]
    assert any(v.check_type == "tautology_placeholder" for v in vs)
