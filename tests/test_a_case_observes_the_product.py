"""Round 113 站5 — every TEST_SPEC case states something about the product.

taskq-sol's `test_fr01_list_accepts_limit_200` has Inputs `limit="200";
expected_status="200"` and exactly one sub-assertion: `limit == "200"`. The
P2 engine routes a predicate whose names are all Inputs to Decider A, which
evaluates it against those Inputs, finds it true, and stops. The case states
nothing the product must do — and the P3 mirror then requires the test to
assert that predicate, a test of its own input. 188 of taskq-sol's 251
sub-assertions read Inputs only; across the corpus, every project but
taskq-api has cases built entirely of them. The template taught it: its only
sub-assertion example was `" " in expected`, an Input.

What was measured before choosing the rule: corpus tests that mirror an
Inputs-only predicate do both things — taskq-final asserts 11 of them on bare
parameters (tautologies), taskq-super re-binds all 4 to product output before
asserting (legitimate). So the mirror is NOT relaxed; the spec is required to
say what the product does: at least one predicate per case naming a production
output. And the P2 consistency check, which only an agent prompt ever ran,
now runs at the P2 exit.
"""

from __future__ import annotations

import ast
from pathlib import Path

from core.quality_gate.red_assertion_check import (
    SpecCase,
    SubAssertion,
    cases_observing_nothing,
)

_TEMPLATE = Path(__file__).resolve().parent.parent / "templates" / "TEST_SPEC.md"


def _kinds(cases, assertions):
    return [v.check_type for v in cases_observing_nothing(cases, assertions)]


def test_a_case_whose_predicates_read_only_its_inputs_observes_nothing():
    cases = [SpecCase(8, {"limit": "200", "expected_status": "200"})]
    sa = SubAssertion("AC1-4c-accept-200", 'limit == "200"', [8])
    violations = cases_observing_nothing(cases, [sa])
    found = [v for v in violations if v.check_type == "no_product_assertion"]
    assert found and found[0].severity == "error"


def test_a_case_with_no_sub_assertion_observes_nothing():
    assert "no_product_assertion" in _kinds([SpecCase(3, {"x": "1"})], [])


def test_a_predicate_over_the_result_observes_the_product():
    cases = [SpecCase(8, {"limit": "200", "expected_status": "200"})]
    sas = [SubAssertion("a", 'limit == "200"', [8]),
           SubAssertion("b", "result_status_code == expected_status", [8])]
    assert "no_product_assertion" not in _kinds(cases, sas)


def test_a_conversion_builtin_is_not_a_production_output():
    # `float` was missing from the builtins, so this read as naming a
    # production output called `float`.
    cases = [SpecCase(2, {"timeout": "10.0"})]
    assert "no_product_assertion" in _kinds(cases, [SubAssertion("t", "float(timeout) > 0", [2])])


def test_the_template_example_observes_the_product():
    text = _TEMPLATE.read_text(encoding="utf-8")
    lines = text[text.index("| rule_id | predicate"):].splitlines()[2:]
    rows = []
    for ln in lines:  # the sub-assertion table only: it ends at the first non-row
        if not ln.startswith("|"):
            break
        rows.append(ln)
    assert rows and any("result" in r.split("|")[2] for r in rows), rows


def test_the_p2_exit_runs_the_consistency_check():
    src = (Path(__file__).resolve().parent.parent / "cli" / "p2_transition.py").read_text()
    called = {n.func.id for n in ast.walk(ast.parse(src))
              if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert "spec_consistency_findings" in called


def test_the_findings_helper_reports_what_the_command_blocks_on(tmp_path):
    from cli.checks.specs import spec_consistency_findings

    arch = tmp_path / "02-architecture"
    arch.mkdir()
    (arch / "TEST_SPEC.md").write_text(
        "# TEST_SPEC.md\n\n### FR-01: Tasks\n\n"
        "| # | Test Function | Inputs | Type | Q |\n|---|---|---|---|---|\n"
        '| 8 | `test_fr01_limit_200` | limit="200"; expected_status="200" | boundary | Q3 |\n\n'
        "| rule_id | predicate | applies_to |\n|---|---|---|\n"
        '| a | `limit == "200"` | 8 |\n', encoding="utf-8")
    found = spec_consistency_findings(tmp_path)
    assert len(found) == 1 and "no_product_assertion" in found[0]
