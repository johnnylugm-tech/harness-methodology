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
