"""Oracle tests for harness/harness_bridge.py pure helper functions.

Gap: test_harness_bridge.py has 50% presence-only assertions ('in result', 'is not None').
     Pure helpers (_extract_mutmut_kill_rate, _validate_tool_content,
     _check_tests_failed, _check_test_skip_ratio) had no exact-value assertions.

Design rule: expected values hard-coded.  Never re-derive from the mutated source.
"""
import pytest
pytestmark = pytest.mark.mutation_oracle

from harness.harness_bridge import (
    _extract_mutmut_kill_rate,
    _validate_tool_content,
    _check_tests_failed,
    _check_test_skip_ratio,
    _TOOL_OUTPUT_MIN_BYTES,
)


# ─── _extract_mutmut_kill_rate ────────────────────────────────────────────────

def test_extract_kill_rate_format_a_exact():
    """Killed 70 Survived 30 → 70.0.  Kills division and addition mutations."""
    result = _extract_mutmut_kill_rate("Killed 70 Survived 30 mutation tests")
    assert result == 70.0


def test_extract_kill_rate_format_a_rounding():
    """Killed 1 Survived 2 → 33.33...  Verifies float division (not int)."""
    result = _extract_mutmut_kill_rate("Killed 1 Survived 2")
    assert result is not None
    assert abs(result - 33.333) < 0.01


def test_extract_kill_rate_format_b_percentage():
    """'mutation score: 75%' → 75.0.  Kills regex and float() conversion."""
    result = _extract_mutmut_kill_rate("Results: mutation score: 75%")
    assert result == 75.0


def test_extract_kill_rate_format_b_with_decimal():
    """'mutation score: 66.7%' → 66.7.  Kills decimal group in regex."""
    result = _extract_mutmut_kill_rate("mutation score 66.7%")
    assert result is not None
    assert abs(result - 66.7) < 0.01


def test_extract_kill_rate_format_a_prefers_over_b():
    """Both formats present → Format A (Killed/Survived) wins.  Kills fallthrough."""
    content = "Killed 80 Survived 20\nmutation score: 50%"
    result = _extract_mutmut_kill_rate(content)
    assert result == 80.0   # 80/(80+20), not 50


def test_extract_kill_rate_zero_total_returns_none():
    """Killed 0 Survived 0 → total=0 → None.  Kills total > 0 guard."""
    result = _extract_mutmut_kill_rate("Killed 0 Survived 0")
    assert result is None


def test_extract_kill_rate_no_match_returns_none():
    """No parseable data → None.  Kills None return path."""
    result = _extract_mutmut_kill_rate("no relevant content here")
    assert result is None


def test_extract_kill_rate_case_insensitive():
    """'killed 5 survived 5' (lowercase) → 50.0.  Kills IGNORECASE flag removal."""
    result = _extract_mutmut_kill_rate("killed 5 survived 5")
    assert result == 50.0


# ─── _validate_tool_content ───────────────────────────────────────────────────

def test_validate_too_small_file_blocked():
    """Content < _TOOL_OUTPUT_MIN_BYTES (5) → violation.  Kills size threshold."""
    content = "abc"   # 3 bytes < 5
    assert len(content.encode()) < _TOOL_OUTPUT_MIN_BYTES
    violations = _validate_tool_content(content, "ruff", "linting", inline=False)
    assert len(violations) == 1
    assert "too small" in violations[0]


def test_validate_min_bytes_exact_boundary_passes():
    """Content == _TOOL_OUTPUT_MIN_BYTES (5 bytes) → no size violation.
    Kills < → <= mutation."""
    content = "x" * _TOOL_OUTPUT_MIN_BYTES   # exactly 5 bytes
    violations = _validate_tool_content(content, None, "dim", inline=False)
    assert not any("too small" in v for v in violations)


def test_validate_inline_ignores_size():
    """inline=True skips size check even for 1-char content."""
    violations = _validate_tool_content("x", "ruff", "linting", inline=True)
    # Only pattern check may fire (short content may not match), not size
    assert not any("too small" in v for v in violations)


def test_validate_comment_stub_blocked_file():
    """Content starting with '#' → stub violation (file mode)."""
    content = "# this is a stub\nno real content"
    violations = _validate_tool_content(content, None, "linting", inline=False)
    assert any("stub" in v.lower() or "#" in v for v in violations)


def test_validate_comment_stub_blocked_inline():
    """Comment stub detected in inline mode too."""
    content = "# placeholder"
    violations = _validate_tool_content(content, "ruff", "linting", inline=True)
    assert any("stub" in v.lower() or "#" in v for v in violations)


def test_validate_ruff_clean_output_passes():
    """ruff 'All checks passed' → no violation.  Kills pattern string mutation."""
    content = "All checks passed.\nNo issues found."
    violations = _validate_tool_content(content, "ruff", "linting", inline=False)
    assert violations == []


def test_validate_ruff_violation_line_passes():
    """ruff file:line:col violation → matches pattern, no violation flag."""
    content = "src/foo.py:12:3: E501 line too long [E501]\nFound 1 error"
    violations = _validate_tool_content(content, "ruff", "linting", inline=False)
    assert violations == []


def test_validate_ruff_unrelated_content_blocked():
    """Content with no ruff patterns → violation reported."""
    content = "completely unrelated text with no ruff patterns here"
    violations = _validate_tool_content(content, "ruff", "linting", inline=True)
    assert any("ruff" in v for v in violations)


def test_validate_mypy_success_passes():
    """mypy 'Success: no issues found' → no violation."""
    content = "Success: no issues found\n"
    violations = _validate_tool_content(content, "mypy", "type_safety", inline=False)
    assert violations == []


def test_validate_pytest_passed_output_passes():
    """pytest '3 passed' → no violation."""
    content = "3 passed in 1.2s"
    violations = _validate_tool_content(content, "pytest", "test_coverage", inline=True)
    assert violations == []


def test_validate_gitleaks_no_leaks_passes():
    """gitleaks 'No leaks found' → no violation."""
    content = "No leaks found\n"
    violations = _validate_tool_content(content, "gitleaks", "secrets_scanning", inline=False)
    assert violations == []


def test_validate_unknown_tool_no_pattern_check():
    """Unknown tool → pattern check skipped, no pattern violation."""
    content = "anything at all here"
    violations = _validate_tool_content(content, "unknown-tool-xyz", "dim", inline=True)
    assert not any("pattern" in v.lower() for v in violations)


# ─── _check_tests_failed ──────────────────────────────────────────────────────

def test_check_tests_failed_zero_fails_passes():
    """'0 failed' is NOT > 0 → passes.  Kills > 0 → >= 0 mutation."""
    raw = {"breakdown": {"test_coverage": {"tool_evidence": "10 passed, 0 failed in 1.2s"}}}
    assert _check_tests_failed(raw) == []


def test_check_tests_failed_exact_count_in_message():
    """'3 failed' → violation message contains exact count '3'.
    Kills group(1) extraction and integer conversion mutations."""
    raw = {"breakdown": {"test_coverage": {"tool_evidence": "7 passed, 3 failed in 0.9s"}}}
    violations = _check_tests_failed(raw)
    assert len(violations) == 1
    assert "3" in violations[0]   # exact count appears in message


def test_check_tests_failed_one_fail_blocks():
    """'1 failed' → violation.  Confirms > 0 gate with boundary case."""
    raw = {"breakdown": {"test_coverage": {"tool_evidence": "99 passed, 1 failed"}}}
    violations = _check_tests_failed(raw)
    assert len(violations) == 1
    assert "1" in violations[0]


def test_check_tests_failed_no_evidence_passes():
    """Empty evidence → no violation (S3 handles missing evidence separately)."""
    assert _check_tests_failed({}) == []
    assert _check_tests_failed({"breakdown": {"test_coverage": {}}}) == []


# ─── _check_test_skip_ratio ───────────────────────────────────────────────────

def test_skip_ratio_exact_at_threshold_passes():
    """10 skipped / 100 total = 10% = threshold → passes (not > 0.10).
    Kills > → >= mutation."""
    raw = {"breakdown": {"test_coverage": {"tool_evidence": "90 passed, 10 skipped"}}}
    assert _check_test_skip_ratio(raw, threshold=0.10) is None


def test_skip_ratio_just_above_threshold_warns():
    """11 skipped / 100 total = 11% > 10% → warning.  Kills comparison logic."""
    raw = {"breakdown": {"test_coverage": {"tool_evidence": "89 passed, 11 skipped"}}}
    result = _check_test_skip_ratio(raw, threshold=0.10)
    assert result is not None
    assert "11" in result   # exact skip count appears in warning
    assert "89" in result or "100" in result  # total context


def test_skip_ratio_exact_counts_in_warning():
    """Warning message contains exact skip and total count.
    Kills skipped/total variable mutations."""
    raw = {"breakdown": {"test_coverage": {"tool_evidence": "80 passed, 20 skipped"}}}
    result = _check_test_skip_ratio(raw, threshold=0.10)   # 20/100 = 20% > 10%
    assert result is not None
    assert "20" in result   # skip count
    assert "100" in result  # total


def test_skip_ratio_zero_skips_no_warn():
    """0 skipped → ratio = 0 → no warning."""
    raw = {"breakdown": {"test_coverage": {"tool_evidence": "100 passed, 0 skipped"}}}
    assert _check_test_skip_ratio(raw) is None


def test_skip_ratio_no_evidence_returns_none():
    """Missing evidence → None (nothing to parse)."""
    assert _check_test_skip_ratio({}) is None


def test_skip_ratio_zero_total_guard():
    """passed=0, skipped=0 → total=0 → None (no ZeroDivisionError).
    Kills total == 0 guard."""
    raw = {"breakdown": {"test_coverage": {"tool_evidence": "0 passed, 0 skipped"}}}
    assert _check_test_skip_ratio(raw) is None
