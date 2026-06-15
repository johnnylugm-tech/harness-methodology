"""Tests for the P3 mirror gate — test must faithfully implement TEST_SPEC.

The gate does NOT re-decide correctness (that is locked in P2); it proves the
test mirrors the already-consistent TEST_SPEC. Bugs surface as the test
*diverging* from spec: Case A as a trigger mismatch, Case B as a missing
assertion.
"""
from __future__ import annotations

from core.quality_gate.red_assertion_check import (
    SpecCase,
    SubAssertion,
    check_test_mirrors_spec,
)


def _errors(violations):
    return [v for v in violations if v.severity == "error"]


# ── Case A: FR-01 (source,expected) with `if source …: assert " " in expected` ─
FR01_CASES = [
    SpecCase(3, {"source": "垃圾", "expected": "ㄌㄜˋ ㄙㄜˋ"}),
    SpecCase(8, {"source": "和", "expected": "ㄏㄢˋ"}),
]
FR01_ASSERTIONS = [SubAssertion("AC5-bopomofo-space", '" " in expected', [3])]

_FR01_BAD = '''
import pytest
_ARGS = [
    pytest.param("垃圾", "ㄌㄜˋ ㄙㄜˋ", id="lese"),
    pytest.param("和", "ㄏㄢˋ", id="he"),
]
@pytest.mark.parametrize("source,expected", _ARGS)
def test_fr01(source, expected):
    if source in ("垃圾", "和"):
        assert " " in expected
'''

_FR01_GOOD = '''
import pytest
_ARGS = [
    pytest.param("垃圾", "ㄌㄜˋ ㄙㄜˋ", id="lese"),
    pytest.param("和", "ㄏㄢˋ", id="he"),
]
@pytest.mark.parametrize("source,expected", _ARGS)
def test_fr01(source, expected):
    if source == "垃圾":
        assert " " in expected
'''


class TestCaseA_TriggerMismatch:
    def test_bad_grouping_is_trigger_mismatch(self):
        errs = _errors(check_test_mirrors_spec(_FR01_BAD, FR01_CASES, FR01_ASSERTIONS))
        assert any(v.check_type == "trigger_mismatch" for v in errs)
        v = next(v for v in errs if v.check_type == "trigger_mismatch")
        assert v.extra["test_trigger"] == ["和", "垃圾"]
        assert v.extra["spec_trigger"] == ["垃圾"]

    def test_correct_grouping_passes(self):
        assert _errors(check_test_mirrors_spec(_FR01_GOOD, FR01_CASES, FR01_ASSERTIONS)) == []


# ── Case B: FR-03 single-value parametrize, `elif text_input == …` ─────────────
FR03_CASES = [SpecCase(10, {"text_input": "。？！!?"})]
FR03_ASSERTIONS = [SubAssertion("fr03-c10-count", "len(result) == 5", [10])]

_FR03_BAD = '''
import pytest
_EDGE = [pytest.param("。？！!?", id="all_boundary")]
@pytest.mark.parametrize("text_input", _EDGE)
def test_fr03(text_input):
    if text_input == "。？！!?":
        assert len(result) == 4
        assert "".join(result) == text_input
'''

_FR03_GOOD = '''
import pytest
_EDGE = [pytest.param("。？！!?", id="all_boundary")]
@pytest.mark.parametrize("text_input", _EDGE)
def test_fr03(text_input):
    if text_input == "。？！!?":
        assert len(result) == 5
        assert "".join(result) == text_input
'''


class TestCaseB_AssertionMissing:
    def test_wrong_count_is_assertion_missing(self):
        errs = _errors(check_test_mirrors_spec(_FR03_BAD, FR03_CASES, FR03_ASSERTIONS))
        assert any(v.check_type == "assertion_missing" for v in errs)

    def test_correct_count_passes(self):
        assert _errors(check_test_mirrors_spec(_FR03_GOOD, FR03_CASES, FR03_ASSERTIONS)) == []


class TestParametrizeAlignment:
    def test_input_drift_flagged(self):
        # P3 changed the locked input → param mismatch (3-char vs 5-char).
        drift = _FR03_GOOD.replace('"。？！!?"', '"。？！"')
        errs = _errors(check_test_mirrors_spec(drift, FR03_CASES, FR03_ASSERTIONS))
        kinds = {v.check_type for v in errs}
        assert "param_missing" in kinds and "param_extra" in kinds

    def test_complex_trigger_is_skipped_not_crashed(self):
        # `.startswith` triggers are behavioural — skipped, no false positive.
        src = '''
import pytest
@pytest.mark.parametrize("text_input", [pytest.param("。？！!?", id="b")])
def test_fr03(text_input):
    if text_input.startswith("。"):
        assert len(result) >= 1
'''
        # No declared assertion to mirror → only assertion_missing if any spec
        # assertion is unmatched. With no matching predicate, count assertion is missing.
        errs = _errors(check_test_mirrors_spec(src, FR03_CASES, FR03_ASSERTIONS))
        # The engine must not crash; the count assertion is reported missing.
        assert all(v.check_type in {"assertion_missing"} for v in errs)


_SPEC_MD = """
### FR-01: lexicon

| # | parametrize id | Inputs | Type |
|---|---|---|---|
| 3 | 垃圾 | source="垃圾"; expected="ㄌㄜˋ ㄙㄜˋ" | boundary |
| 8 | 和 | source="和"; expected="ㄏㄢˋ" | boundary |

**Sub-assertions**:

| rule_id | predicate | applies_to |
|---|---|---|
| AC5 | `" " in expected` | 3 |

## End
"""


def test_cli_mirror_gate(tmp_path):
    import argparse

    from harness_cli import cmd_check_test_mirrors_spec

    arch = tmp_path / "02-architecture"
    arch.mkdir()
    (arch / "TEST_SPEC.md").write_text(_SPEC_MD, encoding="utf-8")
    tf = tmp_path / "test_fr01.py"

    tf.write_text(_FR01_BAD, encoding="utf-8")
    rc = cmd_check_test_mirrors_spec(
        argparse.Namespace(project=str(tmp_path), fr_id="FR-01", test_files=[str(tf)]))
    assert rc == 1

    tf.write_text(_FR01_GOOD, encoding="utf-8")
    rc = cmd_check_test_mirrors_spec(
        argparse.Namespace(project=str(tmp_path), fr_id="FR-01", test_files=[str(tf)]))
    assert rc == 0
