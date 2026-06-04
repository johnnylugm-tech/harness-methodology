"""Tests for SpecAssertionParser + integration with the consistency engine.

The fixture is a minimal TEST_SPEC.md in the milestone-4 schema, carrying the
two real bugs so we verify parse → engine end to end.
"""
from __future__ import annotations

from core.quality_gate.parsers import SpecAssertionParser
from core.quality_gate.red_assertion_check import check_test_spec_consistency

# Note FR-01 case 3 Inputs uses the TRUE expected "ㄌㄜˋ ㄙㄜˋ" (a real space),
# while the parametrize id uses the underscore form — proving Inputs ≠ id.
SCHEMA = """
### FR-01: Taiwan lexicon

| # | parametrize id | Inputs | Type |
|---|---|---|---|
| 3 | 垃圾→ㄌㄜˋ_ㄙㄜˋ | source="垃圾"; expected="ㄌㄜˋ ㄙㄜˋ" | boundary |
| 8 | 和→ㄏㄢˋ | source="和"; expected="ㄏㄢˋ" | boundary |

**Sub-assertions** (predicate over inputs):

| rule_id | predicate | applies_to |
|---|---|---|
| AC5-bopomofo-space | `" " in expected` | 3, 8 |

### FR-03: text splitter

| # | parametrize id | Inputs | Type |
|---|---|---|---|
| 10 | all_boundary_chars | text_input="。？！!?" | boundary |

**Sub-assertions** (predicate over result):

| rule_id | predicate | applies_to |
|---|---|---|
| fr03-c10-count | `len(result) == 4` | 10 |
| fr03-c10-each | `all(len(c) == 1 for c in result)` | 10 |
| fr03-c10-lossless | `"".join(result) == text_input` | 10 |

## Cross-Cutting
"""


def _errors(violations):
    return [v for v in violations if v.severity == "error"]


def test_inputs_hold_true_value_not_pytest_id():
    parsed = SpecAssertionParser.parse(SCHEMA)
    cases, _ = parsed["FR-01"]
    by_id = {c.case_id: c for c in cases}
    assert by_id[8].inputs["expected"] == "ㄏㄢˋ"
    # case 3 id is the underscore form but the parsed Inputs must be the real space.
    assert by_id[3].inputs["expected"] == "ㄌㄜˋ ㄙㄜˋ"


def test_applies_to_parses_multiple_ids():
    _, assertions = SpecAssertionParser.parse(SCHEMA)["FR-01"]
    assert assertions[0].rule_id == "AC5-bopomofo-space"
    assert assertions[0].predicate == '" " in expected'
    assert assertions[0].applies_to == [3, 8]


def test_case_a_detected_end_to_end():
    cases, assertions = SpecAssertionParser.parse(SCHEMA)["FR-01"]
    errs = _errors(check_test_spec_consistency(cases, assertions))
    assert any(v.check_type == "predicate_false" and v.extra["case_id"] == 8 for v in errs)


def test_case_b_detected_end_to_end():
    cases, assertions = SpecAssertionParser.parse(SCHEMA)["FR-03"]
    assert cases[0].inputs["text_input"] == "。？！!?"
    errs = _errors(check_test_spec_consistency(cases, assertions))
    assert any(v.check_type == "length_contradiction" for v in errs)


def test_section_boundary_respected():
    # FR-03 tables must not bleed into FR-01 and vice-versa.
    parsed = SpecAssertionParser.parse(SCHEMA)
    assert set(parsed) == {"FR-01", "FR-03"}
    assert len(parsed["FR-03"][0]) == 1  # only case 10


def test_cli_consistency_gate(tmp_path):
    import argparse

    from harness_cli import cmd_check_test_spec_consistency

    arch = tmp_path / "02-architecture"
    arch.mkdir()
    spec = arch / "TEST_SPEC.md"

    # SCHEMA carries BOTH bugs (applies_to 3,8 and len==4) → gate must FAIL.
    spec.write_text(SCHEMA, encoding="utf-8")
    rc = cmd_check_test_spec_consistency(argparse.Namespace(project=str(tmp_path), fr_id=None))
    assert rc == 1

    # Fix both: 和 dropped from AC5 group; chunk count 4 → 5.
    fixed = SCHEMA.replace("| 3, 8 |", "| 3 |").replace("len(result) == 4", "len(result) == 5")
    spec.write_text(fixed, encoding="utf-8")
    rc = cmd_check_test_spec_consistency(argparse.Namespace(project=str(tmp_path), fr_id=None))
    assert rc == 0
