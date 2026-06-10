"""
Unit tests for harness/tool_runners.py — scorer functions.

Covers:
- _score_radon_cc  (Issue 1: None on parse error; JSON structure)
- _score_radon_mi  (Issue 1: None on parse error; JSON structure)
- _score_pyright   (JSON summary.errorCount path + text fallback)
- _score_bandit    (HIGH/MEDIUM/LOW severity accounting)
- _score_pytest_benchmark (NFR-01/06 latency-based performance scorer)
- compute_tool_score  (None propagation from radon scorers)
"""
import pytest


import json
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from harness.tool_runners import (
    compute_tool_score,
    run_tool,
    _score_radon_cc,
    _score_radon_mi,
    _score_pyright,
    _score_bandit,
    _score_pytest_benchmark,
    _score_assertion_quality,
    _score_error_handling_coverage,
    _score_docstring_coverage,
)


# ---------------------------------------------------------------------------
# _score_radon_cc
# ---------------------------------------------------------------------------

class TestScoreRadonCc:
    """radon cc -j: {"file.py": [{"complexity": N, ...}, ...]}"""

    def test_clean_project_returns_100(self):
        # All functions CC ≤ 10 → no penalty
        data = {
            "src/a.py": [
                {"name": "foo", "complexity": 5},
                {"name": "bar", "complexity": 10},
            ]
        }
        assert _score_radon_cc(json.dumps(data), 0) == 100.0

    def test_one_complex_function_costs_5(self):
        data = {
            "src/a.py": [{"name": "foo", "complexity": 11}]
        }
        assert _score_radon_cc(json.dumps(data), 0) == 95.0

    def test_multiple_complex_functions(self):
        data = {
            "src/a.py": [
                {"name": "foo", "complexity": 12},  # > 10
                {"name": "bar", "complexity": 9},   # ≤ 10, no penalty
            ],
            "src/b.py": [
                {"name": "baz", "complexity": 20},  # > 10
            ],
        }
        # 2 over threshold → 100 - 2×5 = 90
        assert _score_radon_cc(json.dumps(data), 0) == 90.0

    def test_empty_project_returns_100(self):
        assert _score_radon_cc(json.dumps({}), 0) == 100.0

    def test_score_floor_is_zero(self):
        # 21 complex functions → 100 - 21×5 = -5, clamped to 0
        data = {"src/a.py": [{"name": f"f{i}", "complexity": 15} for i in range(21)]}
        assert _score_radon_cc(json.dumps(data), 0) == 0.0

    def test_non_json_returns_none(self):
        """Issue 1: tool crash / non-JSON stderr must not award 100."""
        assert _score_radon_cc("radon: command not found", 127) is None

    def test_empty_string_returns_none(self):
        assert _score_radon_cc("", 0) is None

    def test_non_list_file_entry_ignored(self):
        # If a file entry is not a list (malformed), skip it gracefully.
        data = {"src/a.py": {"unexpected": "dict"}}
        assert _score_radon_cc(json.dumps(data), 0) == 100.0

    def test_entry_missing_complexity_key_ignored(self):
        data = {"src/a.py": [{"name": "foo"}]}  # no "complexity" key
        assert _score_radon_cc(json.dumps(data), 0) == 100.0


# ---------------------------------------------------------------------------
# _score_radon_mi
# ---------------------------------------------------------------------------

class TestScoreRadonMi:
    """radon mi -j: {"file.py": {"mi": 80.5, "rank": "A"}}"""

    def test_single_file(self):
        data = {"src/a.py": {"mi": 80.0, "rank": "A"}}
        assert _score_radon_mi(json.dumps(data), 0) == 80.0

    def test_average_of_multiple_files(self):
        data = {
            "src/a.py": {"mi": 60.0, "rank": "B"},
            "src/b.py": {"mi": 80.0, "rank": "A"},
        }
        assert _score_radon_mi(json.dumps(data), 0) == 70.0

    def test_empty_project_returns_none(self):
        # No analysable files → None (no longer a free 100); cross-validation blocks
        # a passing readability score that has nothing to verify.
        assert _score_radon_mi(json.dumps({}), 0) is None

    def test_non_dict_file_value_ignored(self):
        data = {"src/a.py": "bad", "src/b.py": {"mi": 50.0, "rank": "B"}}
        assert _score_radon_mi(json.dumps(data), 0) == 50.0

    def test_missing_mi_key_ignored(self):
        data = {"src/a.py": {"rank": "A"}}  # no "mi" key
        # Falls through to empty list → None (not a free 100)
        assert _score_radon_mi(json.dumps(data), 0) is None

    def test_non_json_returns_none(self):
        """Issue 1: tool crash must not award 100."""
        assert _score_radon_mi("radon: command not found", 127) is None

    def test_empty_string_returns_none(self):
        assert _score_radon_mi("", 0) is None


# ---------------------------------------------------------------------------
# _score_pyright
# ---------------------------------------------------------------------------

class TestScorePyright:

    def test_zero_errors_returns_100(self):
        data = {"summary": {"errorCount": 0, "warningCount": 2}}
        assert _score_pyright(json.dumps(data), 0) == 100.0

    def test_one_error_costs_5(self):
        data = {"summary": {"errorCount": 1}}
        assert _score_pyright(json.dumps(data), 1) == 95.0

    def test_many_errors(self):
        data = {"summary": {"errorCount": 10}}
        assert _score_pyright(json.dumps(data), 1) == 50.0

    def test_floor_at_zero(self):
        data = {"summary": {"errorCount": 25}}
        assert _score_pyright(json.dumps(data), 1) == 0.0

    def test_text_fallback_counts_error_lines(self):
        text = "src/foo.py:10:1: error: Cannot assign\nsrc/bar.py:5:2: error: Missing return"
        assert _score_pyright(text, 1) == 90.0  # 2 errors × 5

    def test_text_fallback_no_errors(self):
        assert _score_pyright("0 errors, 0 warnings", 0) == 100.0


# ---------------------------------------------------------------------------
# _score_bandit
# ---------------------------------------------------------------------------

class TestScoreBandit:

    def test_no_issues_returns_100(self):
        data = {"results": []}
        assert _score_bandit(json.dumps(data), 0) == 100.0

    def test_high_severity_costs_10(self):
        data = {"results": [{"issue_severity": "HIGH"}]}
        assert _score_bandit(json.dumps(data), 0) == 90.0

    def test_medium_severity_costs_3(self):
        data = {"results": [{"issue_severity": "MEDIUM"}]}
        assert _score_bandit(json.dumps(data), 0) == 97.0

    def test_low_severity_costs_1(self):
        data = {"results": [{"issue_severity": "LOW"}]}
        assert _score_bandit(json.dumps(data), 0) == 99.0

    def test_mixed_severities(self):
        results = [
            {"issue_severity": "HIGH"},   # -10
            {"issue_severity": "HIGH"},   # -10
            {"issue_severity": "MEDIUM"}, # -3
            {"issue_severity": "LOW"},    # -1
        ]
        data = {"results": results}
        assert _score_bandit(json.dumps(data), 0) == 76.0

    def test_floor_at_zero(self):
        data = {"results": [{"issue_severity": "HIGH"}] * 11}
        assert _score_bandit(json.dumps(data), 0) == 0.0

    def test_non_json_returns_zero(self):
        # Conservative: tool crash → 0, not 100
        assert _score_bandit("bandit: command not found", 127) == 0.0


# ---------------------------------------------------------------------------
# compute_tool_score — None propagation from radon scorers
# ---------------------------------------------------------------------------

class TestComputeToolScoreNonePropagation:
    """When a scorer returns None, compute_tool_score must also return None."""

    def test_radon_cc_parse_error_propagates_none(self):
        result = compute_tool_score("radon-cc", "not json", 0)
        assert result is None

    def test_radon_mi_parse_error_propagates_none(self):
        result = compute_tool_score("radon-mi", "not json", 0)
        assert result is None

    def test_radon_cc_valid_json_returns_score(self):
        data = {"src/a.py": [{"complexity": 5}]}
        result = compute_tool_score("radon-cc", json.dumps(data), 0)
        assert result == 100.0

    def test_negative_returncode_always_returns_none(self):
        # Harness-internal error codes: skip regardless of output
        assert compute_tool_score("radon-cc", "{}", -1) is None
        assert compute_tool_score("radon-cc", "{}", -3) is None

    def test_unknown_tool_returns_none(self):
        assert compute_tool_score("nonexistent-tool", "output", 0) is None

    def test_pytest_benchmark_no_tests_returns_none(self):
        # Exit code 5 = no tests collected → dimension skipped
        assert compute_tool_score("pytest-benchmark", "", 5) is None

    def test_pytest_benchmark_valid_returns_score(self):
        output = (
            "Name (time in ms)   Mean     Max\n"
            "test_pipeline       500.0    600.0\n"
        )
        result = compute_tool_score("pytest-benchmark", output, 0)
        assert result == 100.0


# ---------------------------------------------------------------------------
# _score_pytest_benchmark
# ---------------------------------------------------------------------------

class TestScorePytestBenchmark:

    # Benchmark table header formats
    _HEADER_MS = "Name (time in ms)   Mean     Max\n"
    _HEADER_US = "Name (time in us)   Mean     Max\n"
    _HEADER_NS = "Name (time in ns)   Mean     Max\n"
    _HEADER_S  = "Name (time in s)    Mean     Max\n"

    def test_exit_code_5_returns_none(self):
        """No benchmark tests collected → dimension not yet applicable."""
        assert _score_pytest_benchmark("no tests ran", 5) is None

    def test_all_fast_returns_100(self):
        output = self._HEADER_MS + "test_pipeline   200.0   250.0\n"
        assert _score_pytest_benchmark(output, 0) == 100.0

    def test_slow_above_1000ms_deducts_25(self):
        output = self._HEADER_MS + "test_pipeline   1500.0  1800.0\n"
        assert _score_pytest_benchmark(output, 0) == 75.0

    def test_very_slow_above_3000ms_deducts_50(self):
        output = self._HEADER_MS + "test_pipeline   4000.0  5000.0\n"
        assert _score_pytest_benchmark(output, 0) == 50.0

    def test_two_slow_benchmarks_stack_penalties(self):
        output = (
            self._HEADER_MS
            + "test_pipeline   1500.0  1800.0\n"
            + "test_health     1200.0  1500.0\n"
        )
        assert _score_pytest_benchmark(output, 0) == 50.0  # 100 - 25 - 25

    def test_score_floor_is_zero(self):
        lines = "".join(f"test_fn{i}   4000.0  5000.0\n" for i in range(5))
        output = self._HEADER_MS + lines
        assert _score_pytest_benchmark(output, 0) == 0.0

    def test_microseconds_unit_converted_correctly(self):
        """200,000 us = 200 ms → fast, score 100."""
        output = self._HEADER_US + "test_pipeline   200000.0   250000.0\n"
        assert _score_pytest_benchmark(output, 0) == 100.0

    def test_microseconds_slow_converted_correctly(self):
        """2,000,000 us = 2000 ms → > 1000 ms → -25."""
        output = self._HEADER_US + "test_pipeline   2000000.0  2500000.0\n"
        assert _score_pytest_benchmark(output, 0) == 75.0

    def test_nanoseconds_unit_fast_converted_correctly(self):
        """200,000,000 ns = 200 ms → fast, score 100."""
        output = self._HEADER_NS + "test_pipeline   200000000.0   250000000.0\n"
        assert _score_pytest_benchmark(output, 0) == 100.0

    def test_nanoseconds_unit_slow_converted_correctly(self):
        """2,000,000,000 ns = 2000 ms → > 1000 ms → -25."""
        output = self._HEADER_NS + "test_pipeline   2000000000.0   2500000000.0\n"
        assert _score_pytest_benchmark(output, 0) == 75.0

    def test_nanoseconds_very_slow_converted_correctly(self):
        """4,000,000,000 ns = 4000 ms → > 3000 ms → -50."""
        output = self._HEADER_NS + "test_pipeline   4000000000.0   5000000000.0\n"
        assert _score_pytest_benchmark(output, 0) == 50.0

    def test_seconds_unit_converted_correctly(self):
        """0.2 s = 200 ms → fast, score 100."""
        output = self._HEADER_S + "test_pipeline   0.2   0.25\n"
        assert _score_pytest_benchmark(output, 0) == 100.0

    def test_no_benchmark_rows_returns_100(self):
        """Output with header but no data rows → nothing to penalise."""
        assert _score_pytest_benchmark(self._HEADER_MS, 0) == 100.0

    def test_empty_output_returns_100(self):
        """Empty output (e.g. pytest ran but produced no table) → 100."""
        assert _score_pytest_benchmark("", 0) == 100.0


# ---------------------------------------------------------------------------
# _score_assertion_quality + ast-assertions (Layer 1a)
# ---------------------------------------------------------------------------

class TestScoreAssertionQuality:
    def test_all_asserted_returns_100(self):
        assert _score_assertion_quality('{"total": 4, "asserted": 4}', 0) == 100.0

    def test_partial_ratio(self):
        assert _score_assertion_quality('{"total": 4, "asserted": 3}', 0) == 75.0

    def test_zero_tests_returns_0(self):
        """A passing assertion-quality score with no tests at all is a fabrication."""
        assert _score_assertion_quality('{"total": 0, "asserted": 0}', 0) == 0.0

    def test_non_json_returns_none(self):
        assert _score_assertion_quality("not json", 0) is None


class TestRunAstAssertions:
    def test_detects_zero_assert_and_real_assertions(self, tmp_path):
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_x.py").write_text(
            "def test_real():\n    assert 1 == 1\n"
            "def test_empty():\n    pass\n"
            "def test_raises():\n    import pytest\n    with pytest.raises(ValueError):\n        raise ValueError()\n"
            "def test_unittest():\n    import unittest\n    unittest.TestCase().assertEqual(1, 1)\n",
            encoding="utf-8",
        )
        out, rc = run_tool("ast-assertions", str(tmp_path))
        assert rc == 0
        data = json.loads(out)
        assert data["total"] == 4
        assert data["asserted"] == 3  # test_empty is the only zero-assert
        assert any("test_empty" in z for z in data["zero_assert"])
        assert compute_tool_score("ast-assertions", out, rc) == 75.0


# ---------------------------------------------------------------------------
# _score_error_handling_coverage + ast-error-handling (Layer 3, error_handling)
# ---------------------------------------------------------------------------

class TestScoreErrorHandlingCoverage:
    def test_all_handled_returns_100(self):
        assert _score_error_handling_coverage('{"total": 3, "with_handler": 3}', 0) == 100.0

    def test_partial_ratio(self):
        assert _score_error_handling_coverage('{"total": 4, "with_handler": 2}', 0) == 50.0

    def test_no_source_returns_100(self):
        """No source files with code → nothing to handle, not a failure."""
        assert _score_error_handling_coverage('{"total": 0, "with_handler": 0}', 0) == 100.0

    def test_non_json_returns_none(self):
        assert _score_error_handling_coverage("not json", 0) is None


class TestRunAstErrorHandling:
    def test_file_level_coverage(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        # Clean handler (handles, doesn't swallow) — `except Exception: pass`
        # is now a broad_swallow anti-pattern deducting 5 (v2.9 A1).
        (src / "a.py").write_text(
            "def f():\n    try:\n        x = 1\n    except Exception as e:\n        print(e)\n", encoding="utf-8")
        (src / "b.py").write_text("def g():\n    return 1\n", encoding="utf-8")
        (src / "__init__.py").write_text("", encoding="utf-8")  # no code → skipped
        out, rc = run_tool("ast-error-handling", str(tmp_path))
        assert rc == 0
        data = json.loads(out)
        assert data["total"] == 2  # __init__ skipped
        assert data["with_handler"] == 1
        assert compute_tool_score("ast-error-handling", out, rc) == 50.0


# ---------------------------------------------------------------------------
# _score_docstring_coverage + ast-docstrings (Layer B1)
# ---------------------------------------------------------------------------

class TestScoreDocstringCoverage:
    def test_all_documented_returns_100(self):
        assert _score_docstring_coverage('{"total": 3, "with_doc": 3}', 0) == 100.0

    def test_partial_ratio(self):
        assert _score_docstring_coverage('{"total": 4, "with_doc": 1}', 0) == 25.0

    def test_no_public_api_returns_100(self):
        """No public def/class → nothing to document, not a failure."""
        assert _score_docstring_coverage('{"total": 0, "with_doc": 0}', 0) == 100.0

    def test_non_json_returns_none(self):
        assert _score_docstring_coverage("not json", 0) is None


class TestRunAstDocstrings:
    def test_public_only_coverage(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        (src / "a.py").write_text(
            'def public_fn():\n    """doc."""\n    return 1\n'
            "def undocumented():\n    return 2\n"
            "class _Private:\n    pass\n"          # _-prefixed → excluded
            "def _helper():\n    pass\n",          # _-prefixed → excluded
            encoding="utf-8")
        out, rc = run_tool("ast-docstrings", str(tmp_path))
        assert rc == 0
        data = json.loads(out)
        assert data["total"] == 2  # public_fn + undocumented (privates excluded)
        assert data["with_doc"] == 1
        assert any("undocumented" in m for m in data["missing"])
        assert compute_tool_score("ast-docstrings", out, rc) == 50.0


# ---------------------------------------------------------------------------
# pytest-cov-integration (Layer 1b)
# ---------------------------------------------------------------------------

class TestPytestCovIntegration:
    def test_score_uses_coverage_not_passrate(self):
        """integration tool is scored on the coverage TOTAL%, never pass-rate."""
        cov_output = "TOTAL      100     20    80%\n5 passed in 0.1s"
        assert compute_tool_score("pytest-cov-integration", cov_output, 0) == 80.0

    def test_missing_suite_scores_zero(self):
        """No integration suite → no TOTAL line, no passes → 0 (cross-validation blocks)."""
        assert compute_tool_score("pytest-cov-integration", "no tests ran", 0) == 0.0

pytestmark = pytest.mark.mutation_oracle
