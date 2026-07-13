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


class TestMultiSignatureParametrize:
    """Bug E: a TEST_SPEC case set may declare cases with different "first
    input" shapes (command / command_batch / task_id) — every shape must be
    checked, not just whichever @pytest.mark.parametrize signature the AST
    walk happens to see first."""

    CASES = [
        SpecCase(1, {"command": "echo hi"}),
        SpecCase(2, {"command_batch": "echo a; echo b"}),
        SpecCase(3, {"task_id": "deadbeef"}),
    ]

    def test_all_three_signatures_checked_when_correct(self):
        src = '''
import pytest

@pytest.mark.parametrize("command", ["echo hi"])
def test_fr05_01(command):
    pass

@pytest.mark.parametrize("command_batch", ["echo a; echo b"])
def test_fr05_02(command_batch):
    pass

@pytest.mark.parametrize("task_id", ["deadbeef"])
def test_fr05_03(task_id):
    pass
'''
        errs = _errors(check_test_mirrors_spec(src, self.CASES, [], fr_id="FR-05"))
        assert errs == []

    def test_second_signature_drift_is_flagged_not_silently_dropped(self):
        # Pre-fix: _extract_parametrize only kept the FIRST signature it saw
        # ("command"); the command_batch row below would be silently ignored
        # instead of being checked against its own spec case.
        src = '''
import pytest

@pytest.mark.parametrize("command", ["echo hi"])
def test_fr05_01(command):
    pass

@pytest.mark.parametrize("command_batch", ["WRONG VALUE"])
def test_fr05_02(command_batch):
    pass

@pytest.mark.parametrize("task_id", ["deadbeef"])
def test_fr05_03(task_id):
    pass
'''
        errs = _errors(check_test_mirrors_spec(src, self.CASES, [], fr_id="FR-05"))
        kinds = {v.check_type for v in errs}
        assert "param_missing" in kinds and "param_extra" in kinds

    def test_fr_scoping_excludes_other_functions_sharing_a_signature(self):
        # A Cross-Cutting test (test_cli_*) reuses "command" as its parametrize
        # variable name with a value that is NOT one of FR-05's own cases —
        # without fr_id scoping this would wrongly surface as param_extra for
        # FR-05, even though it belongs to a different TEST_SPEC section.
        src = '''
import pytest

@pytest.mark.parametrize("command", ["echo hi"])
def test_fr05_01(command):
    pass

@pytest.mark.parametrize("command_batch", ["echo a; echo b"])
def test_fr05_02(command_batch):
    pass

@pytest.mark.parametrize("task_id", ["deadbeef"])
def test_fr05_03(task_id):
    pass

@pytest.mark.parametrize("command", ["echo cross-cutting"])
def test_cli_pipeline(command):
    pass
'''
        errs = _errors(check_test_mirrors_spec(src, self.CASES, [], fr_id="FR-05"))
        assert errs == []

    def test_case_with_no_matching_signature_is_case_uncovered(self):
        src = '''
import pytest

@pytest.mark.parametrize("command", ["echo hi"])
def test_fr05_01(command):
    pass
'''
        errs = _errors(check_test_mirrors_spec(src, self.CASES, [], fr_id="FR-05"))
        uncovered = {v.check_type for v in errs}
        assert "case_uncovered" in uncovered
        assert sum(1 for v in errs if v.check_type == "case_uncovered") == 2  # cases 2 and 3

    def test_unscoped_call_keeps_legacy_single_signature_behavior(self):
        # fr_id=None (the default) must not break existing callers that
        # never scoped by function name — legacy behavior preserved.
        src = '''
import pytest

@pytest.mark.parametrize("command", ["echo hi"])
def test_fr05_01(command):
    pass
'''
        errs = _errors(check_test_mirrors_spec(src, [self.CASES[0]], []))
        assert errs == []


class TestUnresolvableConstantDegradesGracefully:
    """Bug E-3: a parametrize arg referencing a module-level constant this
    AST-only checker cannot statically evaluate (e.g. a str.join(...) result)
    must degrade to 'row not verified' — not crash the whole MIRROR check."""

    def test_name_reference_to_computed_constant_does_not_crash(self):
        src = '''
import pytest
_BATCH = ";".join(f"echo {i}" for i in range(1, 4))

@pytest.mark.parametrize("command_batch", [_BATCH])
def test_fr05_02(command_batch):
    pass
'''
        cases = [SpecCase(1, {"command_batch": "echo 1;echo 2;echo 3"})]
        errs = _errors(check_test_mirrors_spec(src, cases, [], fr_id="FR-05"))
        # Row could not be statically verified -> reported as missing, not a crash.
        assert any(v.check_type == "param_missing" for v in errs)


class TestRegressionExistingSingleSignatureFiles:
    """Bug E must not regress a canonically-named single-signature file
    (FR-01/FR-03 style: one parametrize signature for the whole file) when
    checked through the new fr_id-scoped path."""

    def test_single_signature_file_still_passes_with_fr_id_scoping(self):
        cases = [SpecCase(1, {"source": "和", "expected": "ㄏㄢˋ"})]
        src = '''
import pytest

@pytest.mark.parametrize("source,expected", [("和", "ㄏㄢˋ")])
def test_fr01_01_lexicon(source, expected):
    if source == "和":
        assert expected == "ㄏㄢˋ"
'''
        assert _errors(check_test_mirrors_spec(src, cases, [], fr_id="FR-01")) == []


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
