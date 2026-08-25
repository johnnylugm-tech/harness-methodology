"""Oracle tests for harness/harness_bridge.py pure helper functions.

Gap: test_harness_bridge.py has 50% presence-only assertions ('in result', 'is not None').
     Pure helpers (_validate_tool_content, _check_tests_failed,
     _check_test_skip_ratio) had no exact-value assertions.

Design rule: expected values hard-coded.  Never re-derive from the mutated source.

Round 31 站2: the eight _extract_mutmut_kill_rate cases moved to
tests/test_mutmut_report.py with the parser they now test. That helper's only
production caller was S4's mutmut branch, which no longer parses free text at
all — the score comes from the framework's own artifact.
"""
import pytest

from harness.harness_bridge import (
    _validate_tool_content,
    _check_tests_failed,
    _check_test_skip_ratio,
    _TOOL_OUTPUT_MIN_BYTES,
    _extract_fr_section,
    _parse_spec_names_for_fr,
)


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


# ─── _check_tests_failed — Round 76 per-FR scope ──────────────────────────────

def test_check_tests_failed_per_fr_sibling_only_passes():
    """FR-08 with only sibling (FR-01/02) failures in FAILED paths → no block.
    Sibling failures belong to the owning FR's gate, not this one.
    Kills the 'block on any FAILED path' over-broad regression."""
    raw = {"breakdown": {"test_coverage": {"tool_evidence": (
        "FAILED tests/test_fr01.py::test_x - AssertionError\n"
        "FAILED tests/test_fr02.py::test_y - AssertionError\n"
        "20 failed, 59 passed in 6.17s"
    )}}}
    assert _check_tests_failed(raw, fr_id="FR-08") == []


def test_check_tests_failed_per_fr_only_scoped_fails_block():
    """FR-08 with one of its own tests failing → block on that count only.
    Kills the regression that ignores fr_id scoping."""
    raw = {"breakdown": {"test_coverage": {"tool_evidence": (
        "FAILED tests/test_fr08.py::test_z - AssertionError\n"
        "FAILED tests/test_fr01.py::test_x - AssertionError\n"
        "2 failed, 60 passed in 1.5s"
    )}}}
    violations = _check_tests_failed(raw, fr_id="FR-08")
    assert len(violations) == 1
    assert "test_fr08" in violations[0]
    assert "1" in violations[0]


def test_check_tests_failed_per_fr_legacy_fallback_when_no_paths():
    """fr_id given but evidence lacks parseable FAILED paths → legacy behavior.
    Kills regressions where fr_id alone disables the legacy summary-line parse."""
    raw = {"breakdown": {"test_coverage": {"tool_evidence": "5 failed, 100 passed in 2.0s"}}}
    violations = _check_tests_failed(raw, fr_id="FR-08")
    assert len(violations) == 1
    assert "5" in violations[0]


def test_check_tests_failed_per_fr_fr_number_padding():
    """FR-01 (single-digit) zero-pads to test_fr01.
    Kills fr_num regex group→format regressions (test_fr1 vs test_fr01)."""
    raw = {"breakdown": {"test_coverage": {"tool_evidence": (
        "FAILED tests/test_fr01.py::test_x - AssertionError\n"
        "1 failed, 5 passed in 0.5s"
    )}}}
    violations = _check_tests_failed(raw, fr_id="FR-01")
    assert len(violations) == 1
    assert "test_fr01" in violations[0]


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

# ─── _extract_fr_section ────────────────────────────────────────────────────────

def test_extract_fr_section_exact_match():
    """Extracts exactly the target section and stops at next H3.
    Kills Regex match group and boundary conditions."""
    text = "### FR-01: Login\nfoo\n### FR-02: Logout\nbar"
    assert _extract_fr_section(text, "FR-01") == "### FR-01: Login\nfoo"


def test_extract_fr_section_stops_at_h2():
    """Stops extracting when hitting an H2 heading."""
    text = "### FR-10\ncontent\n## Next Phase"
    assert _extract_fr_section(text, "FR-10") == "### FR-10\ncontent"


def test_extract_fr_section_stops_at_hr():
    """Stops extracting when hitting a horizontal rule (---)."""
    text = "### FR-99\nbody\n---\nfooter"
    assert _extract_fr_section(text, "FR-99") == "### FR-99\nbody"


def test_extract_fr_section_not_found_fallback():
    """If FR not found, returns up to 60K of original text.
    Kills the fallback `srs_text[:60_000]` literal."""
    text = "some text without headers" * 10000
    res = _extract_fr_section(text, "FR-01")
    assert len(res) == min(len(text), 60000)
    assert res.startswith("some text")


# ─── _parse_spec_names_for_fr ─────────────────────────────────────────────────

def test_parse_spec_names_bullet_format():
    """Parses old bullet format: - `test_foo`.
    Kills regex matching for bullet lists."""
    text = "### FR-01\n- `test_alpha`\n- test_beta"
    names = _parse_spec_names_for_fr(text, "FR-01")
    assert names == ["test_alpha", "test_beta"]


def test_parse_spec_names_table_format():
    """Parses markdown table format extracting from column 2.
    Kills table parsing index logic (cols[1])."""
    text = (
        "### FR-02\n"
        "| ID | Test Function | Desc |\n"
        "|---|---|---|\n"
        "| 1 | `test_gamma` | yes |\n"
        "| 2 | test_delta | no |\n"
        "| 3 | not_a_test | skip |"
    )
    names = _parse_spec_names_for_fr(text, "FR-02")
    assert names == ["test_gamma", "test_delta"]


def test_parse_spec_names_stops_at_h2_and_ignores_others():
    """Only parses tests belonging to the target FR.
    Stops parsing on new section headers."""
    text = (
        "### FR-01\n- test_a\n"
        "### FR-02\n- test_b\n"
        "## Cross-Cutting\n- test_c\n"
        "### FR-01\n- test_d\n"
    )
    names = _parse_spec_names_for_fr(text, "FR-01")
    assert names == ["test_a", "test_d"]

pytestmark = pytest.mark.mutation_oracle
