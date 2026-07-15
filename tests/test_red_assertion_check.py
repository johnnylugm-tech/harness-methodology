"""Tests for the RED test self-consistency engine.

Fixtures are the two REAL bugs from the tts-new control-group experiment:
  * Case A (FR-01): sub-assertion `" " in expected` wrongly grouped 和→ㄏㄢˋ
    into the space-separated set (TEST_SPEC said "asserted in cases 3 and 8").
  * Case B (FR-03): len(result)==4 contradicts the 5-char all-boundary input
    "。？！!?" (the agent mis-counted "4 of the 5" boundary chars).
"""
from __future__ import annotations

import pytest

from core.quality_gate.red_assertion_check import (
    SpecCase,
    SubAssertion,
    UnsafePredicateError,
    check_test_spec_consistency,
    _safe_eval_predicate,
    _parse_length_fact,
)


def _errors(violations):
    return [v for v in violations if v.severity == "error"]


# Real FR-01 cases — expected values are the TRUE strings (real spaces, NOT the
# pytest-id underscore form): 垃圾→"ㄌㄜˋ ㄙㄜˋ" has a space; 和→"ㄏㄢˋ" has none.
FR01_CASE3 = SpecCase(3, {"source": "垃圾", "expected": "ㄌㄜˋ ㄙㄜˋ"})
FR01_CASE8 = SpecCase(8, {"source": "和", "expected": "ㄏㄢˋ"})


class TestCaseA_PredicateGrouping:
    def test_bad_grouping_flags_he(self):
        # applies_to includes case 8 (和) — the real RED bug.
        a = SubAssertion("AC5-bopomofo-space", '" " in expected', [3, 8])
        violations = check_test_spec_consistency([FR01_CASE3, FR01_CASE8], [a])
        errs = _errors(violations)
        assert len(errs) == 1
        assert errs[0].check_type == "predicate_false"
        assert errs[0].extra["case_id"] == 8

    def test_correct_grouping_passes(self):
        # applies_to only case 3 (垃圾) — the fix.
        a = SubAssertion("AC5-bopomofo-space", '" " in expected', [3])
        violations = check_test_spec_consistency([FR01_CASE3, FR01_CASE8], [a])
        assert _errors(violations) == []

    def test_real_expected_not_pytest_id(self):
        # Guard: engine uses the TRUE expected (space), not an id underscore.
        a = SubAssertion("AC5-bopomofo-space", '" " in expected', [3])
        assert _errors(check_test_spec_consistency([FR01_CASE3], [a])) == []


# Real FR-03 case 10 — all-boundary input of FIVE chars.
FR03_CASE10 = SpecCase(10, {"text_input": "。？！!?"})


class TestCaseB_LengthContradiction:
    @staticmethod
    def _preds(count):
        return [
            SubAssertion("fr03-c10-count", f"len(result) == {count}", [10]),
            SubAssertion("fr03-c10-each", "all(len(c) == 1 for c in result)", [10]),
            SubAssertion("fr03-c10-lossless", '"".join(result) == text_input', [10]),
        ]

    def test_wrong_count_flagged(self):
        # len==4 but input is 5 chars → 4≠5 contradiction (the real bug).
        violations = check_test_spec_consistency([FR03_CASE10], self._preds(4))
        errs = _errors(violations)
        assert len(errs) == 1
        assert errs[0].check_type == "length_contradiction"
        assert set(errs[0].extra["totals"].values()) == {4, 5}

    def test_correct_count_passes(self):
        # len==5 matches the 5-char input → satisfiable.
        violations = check_test_spec_consistency([FR03_CASE10], self._preds(5))
        assert _errors(violations) == []


class TestNeedsReview:
    def test_behavioural_assertion_is_needs_review(self):
        # Production-behaviour assertion, not a length pattern → info, not fail.
        a = SubAssertion("fr01-empty", 'apply_lexicon("") == ""', [8])
        violations = check_test_spec_consistency([FR01_CASE8], [a])
        assert _errors(violations) == []
        infos = [v for v in violations if v.severity == "info"]
        assert len(infos) == 1
        assert infos[0].check_type == "needs_review"


class TestSafety:
    @pytest.mark.parametrize("evil", [
        '__import__("os").system("echo pwned")',
        'expected.__class__',
        'expected.__class__.__bases__',
        '(lambda: 1)()',
        'open("/etc/passwd").read()',
        'expected.format()',          # .format not whitelisted
    ])
    def test_unsafe_predicate_rejected(self, evil):
        with pytest.raises(UnsafePredicateError):
            _safe_eval_predicate(evil, {"expected": "x"})

    @pytest.mark.parametrize("expr,ns,want", [
        ('" " in expected', {"expected": "a b"}, True),
        ('" " in expected', {"expected": "ab"}, False),
        ('len(expected.split()) == 2', {"expected": "a b"}, True),
        ('expected.startswith("ㄏ")', {"expected": "ㄏㄢˋ"}, True),
    ])
    def test_safe_predicate_evaluates(self, expr, ns, want):
        assert bool(_safe_eval_predicate(expr, ns)) == want

    def test_unsafe_predicate_in_spec_is_error_not_crash(self):
        a = SubAssertion("evil", 'expected.__class__', [8])
        errs = _errors(check_test_spec_consistency([FR01_CASE8], [a]))
        assert len(errs) == 1
        assert errs[0].check_type == "unsafe_predicate"

    def test_runtime_error_in_spec_is_error_not_crash(self):
        a = SubAssertion("type_error", 'len(42) == 2', [8])
        errs = _errors(check_test_spec_consistency([FR01_CASE8], [a]))
        assert len(errs) == 1
        assert errs[0].check_type == "malformed_predicate"
        assert "evaluation failed: TypeError" in errs[0].message


class TestLengthFactParser:
    def test_card(self):
        assert _parse_length_fact("len(result) == 4") == ("card", "result", 4)

    def test_elem(self):
        assert _parse_length_fact("all(len(c) == 1 for c in result)") == ("elem", "result", 1)

    def test_join(self):
        assert _parse_length_fact('"".join(result) == text_input') == ("join", "result", "text_input")

    def test_unrecognised(self):
        assert _parse_length_fact('apply_lexicon("") == ""') is None


# ── C1 guard: contradictory card (len(X)==N) declarations ────────────────────
from core.quality_gate.red_assertion_check import check_test_mirrors_spec  # noqa: E402


class TestC1_ContradictoryCardValues:
    def test_two_different_len_declarations_detected(self):
        # len(result)==4 AND len(result)==5: overwrite bug would miss this.
        cases = [SpecCase(1, {"text_input": "abc"})]
        assertions = [
            SubAssertion("r1", "len(result) == 4", [1]),
            SubAssertion("r2", "len(result) == 5", [1]),
        ]
        errs = _errors(check_test_spec_consistency(cases, assertions))
        assert any(v.check_type == "length_contradiction" for v in errs)
        v = next(v for v in errs if v.check_type == "length_contradiction")
        assert set(v.extra["totals"].values()) == {4, 5}

    def test_same_value_repeated_is_not_contradiction(self):
        cases = [SpecCase(1, {"text_input": "abc"})]
        assertions = [
            SubAssertion("r1", "len(result) == 3", [1]),
            SubAssertion("r2", "len(result) == 3", [1]),
        ]
        errs = _errors(check_test_spec_consistency(cases, assertions))
        assert not any(v.check_type == "length_contradiction" for v in errs)


# ── C2 guard: multi-function parametrize aggregation ─────────────────────────
class TestC2_MultiFunctionParametrize:
    _MULTI_FN = '''
import pytest
_CORE = [pytest.param("a", id="a")]
_EDGE = [pytest.param("b", id="b")]
@pytest.mark.parametrize("x", _CORE)
def test_core(x):
    if x == "a":
        assert True
@pytest.mark.parametrize("x", _EDGE)
def test_edge(x):
    if x == "b":
        assert True
'''

    def test_rows_from_both_functions_aggregated(self):
        # Spec has "a" and "b"; both must be found, no param_missing/extra.
        cases = [SpecCase(1, {"x": "a"}), SpecCase(2, {"x": "b"})]
        errs = _errors(check_test_mirrors_spec(
            self._MULTI_FN, cases,
            [SubAssertion("r", "True", [1, 2])]))
        param_errs = [e for e in errs if e.check_type in ("param_missing", "param_extra")]
        assert param_errs == [], f"unexpected: {param_errs}"

    def test_single_function_file_unaffected(self):
        src = '''
import pytest
@pytest.mark.parametrize("x", [pytest.param("a", id="a")])
def test_fn(x):
    pass
'''
        cases = [SpecCase(1, {"x": "a"})]
        assert _errors(check_test_mirrors_spec(src, cases, [])) == []


class TestCanonicalPredicate:
    """Bug #27 regression: sub-assertion predicate matching must be
    insensitive to whitespace, redundant parens, and other syntactic noise.
    Previously, `_normalize_predicate(ast.unparse(...))` produced different
    strings for semantically equivalent expressions, causing false-
    negative `assertion_missing` violations."""

    def test_canonical_strips_whitespace(self):
        from core.quality_gate.red_assertion_check import _canonical_predicate
        a = _canonical_predicate("result > 0")
        b = _canonical_predicate("result>0")
        assert a == b
        assert a is not None

    def test_canonical_strips_redundant_parens(self):
        from core.quality_gate.red_assertion_check import _canonical_predicate
        assert _canonical_predicate("(1 + 2) == 3") == _canonical_predicate("1+2==3")

    def test_canonical_normalises_not(self):
        from core.quality_gate.red_assertion_check import _canonical_predicate
        assert _canonical_predicate("not is_empty") == _canonical_predicate("(not is_empty)")

    def test_canonical_handles_complex_expression(self):
        from core.quality_gate.red_assertion_check import _canonical_predicate
        a = _canonical_predicate("x > 0 and y > 0")
        b = _canonical_predicate("(x>0)and(y>0)")
        assert a == b
        assert a is not None

    def test_canonical_returns_none_on_invalid(self):
        from core.quality_gate.red_assertion_check import _canonical_predicate
        assert _canonical_predicate("not valid python !!") is None

    def test_canonical_distinguishes_semantically_different(self):
        """Whitespace-stripped canonical form should NOT collapse
        semantically distinct expressions."""
        from core.quality_gate.red_assertion_check import _canonical_predicate
        a = _canonical_predicate("x > 0")
        b = _canonical_predicate("x < 0")
        assert a != b


class TestR5_BareAssertsExtracted:
    """Bug Fix R5 (2026-07-15): bare top-level asserts must be visible to MIRROR.

    Previously, `_collect_ifs` only walked `if`-trigger blocks, so a test
    that wrote `assert x == 3` at the top of a test function (valid TDD-RED)
    was silently dropped — `_extract_sub_assertions` returned [], every spec
    sub-assertion then fired `assertion_missing`, and the gate BLOCKED
    even though the test was correct. Fix: `_collect_bare_asserts` walker
    records bare asserts under trigger_var "<bare>".
    """

    def test_bare_asserts_are_collected(self):
        from core.quality_gate.red_assertion_check import _extract_sub_assertions
        import ast
        src = (
            "def test_foo():\n"
            "    assert x == 3\n"
            "    assert y == 'value'\n"
        )
        uses = _extract_sub_assertions(ast.parse(src))
        # Two bare asserts → two _SubUse records, both with var="<bare>"
        bare_uses = [u for u in uses if u.var == "<bare>"]
        assert len(bare_uses) == 2
        all_asserts = set()
        for u in bare_uses:
            all_asserts |= u.asserts
        # R6: literals are coerced to str() form so both sides align
        assert "x == '3'" in all_asserts
        assert "y == 'value'" in all_asserts

    def test_bare_and_if_wrapped_combined(self):
        """A test with both shapes should record both."""
        from core.quality_gate.red_assertion_check import _extract_sub_assertions
        import ast
        # trigger var must be an ast.Name (not Attribute) for _collect_ifs
        # to recognise it — that's a separate harness constraint, not R5.
        src = (
            "def test_mix():\n"
            "    assert top == 1\n"
            "    if failure_count == 3:\n"
            "        assert opened_at is not None\n"
        )
        uses = _extract_sub_assertions(ast.parse(src))
        # One bare ("top == 1") plus one if-wrapped (trigger=failure_count)
        bare = [u for u in uses if u.var == "<bare>"]
        triggered = [u for u in uses if u.var != "<bare>"]
        assert len(bare) == 1
        assert len(triggered) == 1
        assert "top == '1'" in next(iter(bare)).asserts

    def test_no_asserts_returns_empty(self):
        from core.quality_gate.red_assertion_check import _extract_sub_assertions
        import ast
        src = "def test_empty():\n    pass\n"
        uses = _extract_sub_assertions(ast.parse(src))
        assert uses == []


class TestR6_LiteralNormalization:
    """Bug Fix R6 (2026-07-15): TEST_SPEC string literals vs Python native
    literals must canonicalise to the SAME string for substring match.

    `_canonical_predicate` now coerces every non-string `ast.Constant.value`
    to its `str()` form BEFORE `ast.unparse`, so:
      - test-side `failure_count == 3`   → canonical `failure_count == '3'`
      - spec-side `failure_count == "3"` → canonical `failure_count == '3'`
    Both align; substring match works.
    """

    def test_int_vs_string_literal_align(self):
        from core.quality_gate.red_assertion_check import _canonical_predicate
        # Spec source: `failure_count == "3"` (quoted string)
        spec = _canonical_predicate('failure_count == "3"')
        # Test source: `failure_count == 3` (int literal)
        test = _canonical_predicate("failure_count == 3")
        assert spec == test
        assert spec == "failure_count == '3'"

    def test_float_vs_string_literal_align(self):
        from core.quality_gate.red_assertion_check import _canonical_predicate
        assert _canonical_predicate('x == "0.1"') == _canonical_predicate("x == 0.1")

    def test_string_literal_unchanged(self):
        """Already-string literals pass through unchanged."""
        from core.quality_gate.red_assertion_check import _canonical_predicate
        result = _canonical_predicate('name == "taskq"')
        assert result == "name == 'taskq'"

    def test_already_string_in_canonical_form_unchanged(self):
        """Idempotency: re-canonicalising an already-canonical form is no-op."""
        from core.quality_gate.red_assertion_check import _canonical_predicate
        once = _canonical_predicate('x == "3"')
        twice = _canonical_predicate(once)
        assert once == twice
