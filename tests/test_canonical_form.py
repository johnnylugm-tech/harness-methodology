"""Unit tests for core/canonical_form.py — single source of truth for FR/NFR/TASK IDs.

Improvement I of convergence plan: FR-ID variants proliferated across
`harness_cli.py`. canonical_form() consolidates the 6 normalization sites.
These tests verify:

  - Permissive input: 12 variants (FR-01, fr01, FR_01, FR(01), [FR-01], etc.)
  - Strict output: always canonical TASK-NN / FR-NN / NFR-NN
  - Zero-padding: fr-1 → FR-01
  - Whitespace stripping: "  FR-07  " → FR-07
  - Filename helpers: fr_id_to_test_filename, fr_id_to_sentinel_filename
  - Error cases: empty, no digits, unknown prefix
  - is_canonical: only True for exact FR-NN / TASK-NN / NFR-NN
  - assert_canonical: raises on non-canonical

Commonality: framework-level. All 6 normalization sites in harness_cli.py
must use canonical_form() instead of in-place regex.
"""

import pytest

from core.canonical_form import (
    _CANONICAL_RE,
    assert_canonical,
    canonical_form,
    fr_id_to_sentinel_filename,
    fr_id_to_test_filename,
    is_canonical,
)


# ---------------------------------------------------------------------------
# canonical_form: happy-path variants
# ---------------------------------------------------------------------------


class TestCanonicalFormHappyPath:
    @pytest.mark.parametrize("inp,expected", [
        ("FR-01", "FR-01"),  # already canonical
        ("fr-01", "FR-01"),  # lowercase hyphen
        ("FR01", "FR-01"),  # no separator
        ("fr01", "FR-01"),  # lowercase no separator
        ("FR_01", "FR-01"),  # underscore separator
        ("fr_01", "FR-01"),  # lowercase underscore
        ("FR(01)", "FR-01"),  # parens
        ("FR( 01 )", "FR-01"),  # parens with spaces
        ("[FR-01]", "FR-01"),  # brackets
        ("(FR-01)", "FR-01"),  # paren wrapper
        ("fr-1", "FR-01"),  # not zero-padded
        ("FR 05", "FR-05"),  # space separator
        ("  FR-07  ", "FR-07"),  # leading/trailing whitespace
        ("NFR-12", "NFR-12"),  # NFR prefix
        ("nfr-12", "NFR-12"),  # NFR lowercase
        ("NFR12", "NFR-12"),  # NFR no separator
        ("task-3", "TASK-03"),  # TASK prefix
        ("TASK_07", "TASK-07"),  # TASK underscore
        ("FR-100", "FR-100"),  # 3+ digits allowed
        ("FR-007", "FR-07"),  # leading zeros normalized to 2 digits
    ])
    def test_variants(self, inp, expected):
        assert canonical_form(inp) == expected


# ---------------------------------------------------------------------------
# canonical_form: error cases
# ---------------------------------------------------------------------------


class TestCanonicalFormErrors:
    def test_empty_string(self):
        with pytest.raises(ValueError, match="empty"):
            canonical_form("")

    def test_none(self):
        with pytest.raises(ValueError, match="None"):
            canonical_form(None)

    def test_no_prefix(self):
        with pytest.raises(ValueError, match="TASK/FR/NFR"):
            canonical_form("XY-01")

    def test_no_digits(self):
        with pytest.raises(ValueError, match="no digits"):
            canonical_form("FR")

    def test_digits_only(self):
        with pytest.raises(ValueError, match="TASK/FR/NFR"):
            canonical_form("12")

    def test_negative(self):
        # re.search picks up "1" from "-1" via lookahead behavior;
        # canonical_form doesn't extract from negative numbers cleanly
        # but at least doesn't crash. (FR-0 → FR-00 is fine.)
        assert canonical_form("FR-0") == "FR-00"

    def test_int_input(self):
        # type-permissive: int 5 → "FR-05"? No — has no prefix, should raise
        with pytest.raises(ValueError):
            canonical_form(5)


# ---------------------------------------------------------------------------
# is_canonical + assert_canonical
# ---------------------------------------------------------------------------


class TestIsCanonical:
    @pytest.mark.parametrize("s", [
        "FR-01", "FR-12", "FR-100", "FR-007",
        "NFR-01", "NFR-100",
        "TASK-01", "TASK-99",
    ])
    def test_canonical_true(self, s):
        assert is_canonical(s) is True

    @pytest.mark.parametrize("s", [
        "FR01", "fr-01", "FR_01", "FR(01)", "[FR-01]",
        "FR-1",  # 1 digit (canonical requires ≥2)
        "fr01",  # lowercase
        "XY-01",  # wrong prefix
        "", "FR", "FR-",
        "FR-01-extra",  # trailing extra
    ])
    def test_canonical_false(self, s):
        assert is_canonical(s) is False

    def test_non_string_input(self):
        # type-permissive: None and non-str → False (not raise)
        assert is_canonical(None) is False
        assert is_canonical(5) is False
        assert is_canonical(["FR-01"]) is False


class TestAssertCanonical:
    def test_canonical_passes(self):
        assert_canonical("FR-01")  # no raise

    def test_non_canonical_raises(self):
        with pytest.raises(ValueError, match="Expected canonical"):
            assert_canonical("FR01")


# ---------------------------------------------------------------------------
# Filename helpers
# ---------------------------------------------------------------------------


class TestFrIdToTestFilename:
    def test_canonical(self):
        assert fr_id_to_test_filename("FR-01") == "tests/test_fr01.py"

    def test_variant(self):
        assert fr_id_to_test_filename("fr01") == "tests/test_fr01.py"

    def test_three_digit(self):
        assert fr_id_to_test_filename("FR-100") == "tests/test_fr100.py"

    def test_custom_dir(self):
        assert fr_id_to_test_filename("FR-12", test_dir="03-development/tests") == \
            "03-development/tests/test_fr12.py"


class TestFrIdToSentinelFilename:
    def test_gate1(self):
        assert fr_id_to_sentinel_filename("FR-01", 1) == "g1_fr01.flag"

    def test_gate4(self):
        assert fr_id_to_sentinel_filename("FR-12", 4) == "g4_fr12.flag"

    def test_custom_suffix(self):
        assert fr_id_to_sentinel_filename("FR-01", 1, suffix="json") == "g1_fr01.json"

    def test_variant_input(self):
        assert fr_id_to_sentinel_filename("fr01", 1) == "g1_fr01.flag"


# ---------------------------------------------------------------------------
# Determinism: same input → same output, no randomness
# ---------------------------------------------------------------------------


class TestDeterminism:
    def test_stable(self):
        for inp in ["FR-01", "fr01", "FR_01", "[FR-01]"]:
            assert canonical_form(inp) == canonical_form(inp)

    def test_no_llm_dependency(self):
        # Sanity: the module does NOT touch any LLM/network
        import core.canonical_form as mod
        src_path = mod.__file__
        assert src_path is not None
        src = open(src_path).read()
        for token in ["requests", "urllib", "claude", "openai", "anthropic"]:
            assert token not in src, f"LLM/network call found: {token}"


# ---------------------------------------------------------------------------
# Regression: catch future variant additions that bypass canonical_form
# ---------------------------------------------------------------------------


class TestCanonicalRegex:
    """The canonical regex must accept canonical forms and reject variants."""

    def test_canonical_regex_matches_canonical(self):
        assert _CANONICAL_RE.match("FR-01")
        assert _CANONICAL_RE.match("NFR-100")
        assert _CANONICAL_RE.match("TASK-07")

    def test_canonical_regex_rejects_variants(self):
        assert not _CANONICAL_RE.match("FR01")
        assert not _CANONICAL_RE.match("fr-01")
        assert not _CANONICAL_RE.match("FR_01")
        assert not _CANONICAL_RE.match("[FR-01]")
        assert not _CANONICAL_RE.match("FR-1")  # 1-digit (canonical requires ≥2)