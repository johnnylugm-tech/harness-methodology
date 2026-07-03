"""canonical_form.py — single source of truth for FR/NFR/TASK IDs.

Root cause (I of 5-meta-pattern convergence plan): FR-ID variants proliferated
across `harness_cli.py`. The pattern `re.match(r"FR-(\\d+)", fr_id)` plus
`re.sub(r"[^a-z0-9]", "_", fr_id.lower()).strip("_")` appeared in 6 places
(lines 2500, 6442, 6451, 6791, 7465, plus 4557 for FR- prefix). Each site
is a slight variation, and each was patched independently when a new variant
surfaced (e.g. `[FR-01]`, `FR(01)`, `FR_01`, `fr01`, `[FR(01-05)]`).

This module consolidates the 6 normalization sites into ONE function:

    canonical_form(s: str) -> "FR-NN"
    is_canonical(s: str) -> bool
    assert_canonical(s: str) -> None

The function is permissive in INPUT (accepts variants) but STRICT in OUTPUT
(returns the canonical `FR-NN` form only). This preserves backward-compat
for legacy callers while forcing a single canonical form going forward.

Workflow (I integration):
  - 6 normalization sites in `harness_cli.py` → call `canonical_form()`
  - `_check_content_quality` drops the 4-variant regex tolerance
  - pre-commit hook (via `canonical_lint.py`) flags non-canonical FR-IDs
    written into source code
"""

from __future__ import annotations

import re
from typing import Literal

# Canonical pattern: TASK-XX, FR-XX, NFR-XX, CR-XXX where XX is zero-padded.
# CR = Change Request (Phase 9 maintenance tickets, CR-BUG/CR-FEAT typed).
_CANONICAL_RE = re.compile(r"^(TASK|FR|NFR|CR)-(\d{2,})$")
_DIGIT_RE = re.compile(r"\d+")


# Public type aliases for downstream code
CanonicalPrefix = Literal["TASK", "FR", "NFR", "CR"]


def canonical_form(s: object) -> str:
    """Convert any FR-ID variant to canonical 'FR-NN' (zero-padded, uppercase).

    Accepts (permissive input):
      - "FR-01"      → "FR-01"   (already canonical)
      - "fr01"       → "FR-01"   (case + no separator)
      - "FR_01"      → "FR-01"   (underscore separator)
      - "FR(01)"     → "FR-01"   (parens)
      - "[FR-01]"    → "FR-01"   (brackets)
      - "fr-1"       → "FR-01"   (not zero-padded)
      - "FR 05"      → "FR-05"   (space separator)
      - "nfr-12"     → "NFR-12"
      - "task-3"     → "TASK-03"

    Raises ValueError on:
      - Empty string
      - No digit found
      - Unknown prefix (not TASK/FR/NFR)

    Note: This is INPUT-permissive, OUTPUT-strict. Always returns canonical
    'TASK-NN' / 'FR-NN' / 'NFR-NN'. The number is always at least 2 digits
    (zero-padded if needed).
    """
    if s is None:
        raise ValueError("canonical_form: input is None")
    s = str(s).strip()
    if not s:
        raise ValueError("canonical_form: input is empty")

    # Strip leading non-alphanumeric (handles `[FR-01]`, `(FR-01)` etc.)
    stripped = s
    while stripped and not stripped[0].isalnum():
        stripped = stripped[1:]

    # Find the prefix (TASK / FR / NFR / CR, case-insensitive) at position 0.
    # CR must come after FR/NFR in the alternation? No — alternation is
    # left-to-right on the SAME start position, and none of these prefixes
    # is a prefix of another at position 0 (CR vs FR differ at char 0), so
    # order is safe.
    prefix_match = re.match(r"^(TASK|FR|NFR|CR)", stripped, re.IGNORECASE)
    if not prefix_match:
        raise ValueError(
            f"Non-canonical FR-ID: {s!r} — must start with TASK/FR/NFR/CR"
        )
    prefix = prefix_match.group(1).upper()

    # Find the first digit run after the prefix
    after_prefix = stripped[prefix_match.end():]
    digit_match = _DIGIT_RE.search(after_prefix)
    if not digit_match:
        raise ValueError(
            f"Non-canonical FR-ID: {s!r} — no digits found after prefix"
        )
    num = int(digit_match.group())
    if num < 0:
        raise ValueError(f"Non-canonical FR-ID: {s!r} — negative number")
    # Zero-pad to at least 2 digits (3+ digits allowed for legacy data)
    return f"{prefix}-{num:02d}"


def is_canonical(s: object) -> bool:
    """Return True if s is already in canonical form (no transformation needed).

    A canonical ID matches `^(TASK|FR|NFR)-\\d{2,}$` exactly.
    """
    if not isinstance(s, str):
        return False
    return bool(_CANONICAL_RE.match(s.strip()))


def assert_canonical(s: str) -> None:
    """Raise ValueError if s is not in canonical form.

    Use this when downstream code REQUIRES canonical form (e.g. writing to
    state.json, emitting commit messages).
    """
    if not is_canonical(s):
        raise ValueError(f"Expected canonical FR-ID, got {s!r}")


# ---------------------------------------------------------------------------
# Filename helpers (test_frNN.py, sentinels/g1_frNN.flag)
# ---------------------------------------------------------------------------


def fr_id_to_test_filename(fr_id: str, test_dir: str = "tests") -> str:
    """Return test file path for an FR.

    Examples:
      fr_id_to_test_filename("FR-01") -> "tests/test_fr01.py"
      fr_id_to_test_filename("fr01") -> "tests/test_fr01.py"  # canonicalised
      fr_id_to_test_filename("FR-100") -> "tests/test_fr100.py"  # 3+ digits OK
    """
    canonical = canonical_form(fr_id)
    # Extract zero-padded digits (matches existing convention `test_frNN.py`)
    _, num_str = canonical.split("-")
    return f"{test_dir}/test_fr{num_str}.py"


def fr_id_to_sentinel_filename(fr_id: str, gate: int, suffix: str = "flag") -> str:
    """Return sentinel filename for an FR + gate.

    Examples:
      fr_id_to_sentinel_filename("FR-01", 1) -> "g1_fr01.flag"
      fr_id_to_sentinel_filename("FR-12", 4) -> "g4_fr12.flag"
    """
    canonical = canonical_form(fr_id)
    _, num_str = canonical.split("-")
    return f"g{gate}_fr{num_str}.{suffix}"


# ---------------------------------------------------------------------------
# Self-test (run `python3 core/canonical_form.py` to sanity-check)
# ---------------------------------------------------------------------------


def _selftest() -> int:
    cases = [
        # (input, expected, description)
        ("FR-01", "FR-01", "already canonical"),
        ("fr-01", "FR-01", "lowercase"),
        ("fr01", "FR-01", "no separator"),
        ("FR_01", "FR-01", "underscore separator"),
        ("FR(01)", "FR-01", "parens"),
        ("[FR-01]", "FR-01", "brackets"),
        ("fr-1", "FR-01", "not zero-padded"),
        ("FR 05", "FR-05", "space separator"),
        ("nfr-12", "NFR-12", "NFR prefix"),
        ("task-3", "TASK-03", "TASK prefix"),
        ("FR-100", "FR-100", "3+ digits allowed"),
        ("  FR-07  ", "FR-07", "whitespace stripped"),
    ]
    failures = []
    for inp, expected, desc in cases:
        try:
            got = canonical_form(inp)
            if got != expected:
                failures.append(f"  FAIL [{desc}]: canonical_form({inp!r}) = {got!r}, expected {expected!r}")
        except ValueError as e:
            failures.append(f"  FAIL [{desc}]: canonical_form({inp!r}) raised: {e}")

    # Error cases
    error_cases = [
        ("", "empty"),
        ("XY-01", "wrong prefix"),
        ("FR", "no digits"),
        ("FR-", "no digits after dash"),
        ("FR-0", "zero digits"),  # actually, "0" → "00" so this is valid
        # Actually "FR-0" should produce "FR-00" — not an error.
    ]
    for inp, desc in error_cases:
        if inp == "FR-0":
            try:
                got = canonical_form(inp)
                if got != "FR-00":
                    failures.append(f"  FAIL [zero digit]: canonical_form({inp!r}) = {got!r}, expected 'FR-00'")
            except ValueError as e:
                failures.append(f"  FAIL [zero digit]: canonical_form({inp!r}) raised: {e}")
            continue
        try:
            got = canonical_form(inp)
            failures.append(f"  FAIL [{desc}]: canonical_form({inp!r}) = {got!r}, expected ValueError")
        except ValueError:
            pass

    # Filename helpers
    if fr_id_to_test_filename("FR-01") != "tests/test_fr01.py":
        failures.append("  FAIL: fr_id_to_test_filename(FR-01)")
    if fr_id_to_test_filename("fr01", test_dir="03-development/tests") != "03-development/tests/test_fr01.py":
        failures.append("  FAIL: fr_id_to_test_filename(fr01, custom dir)")
    if fr_id_to_sentinel_filename("FR-12", 4) != "g4_fr12.flag":
        failures.append("  FAIL: fr_id_to_sentinel_filename(FR-12, 4)")

    if failures:
        print("canonical_form self-test FAILED:")
        for f in failures:
            print(f)
        return 1
    print(f"canonical_form self-test PASSED ({len(cases)} normal cases + {len(error_cases)} error cases + 3 filename cases)")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_selftest())