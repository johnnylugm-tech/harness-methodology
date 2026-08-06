"""Round 42 站0 — the seven test types nobody counts.

`harness/ssi/prompts/derive_test_cases.md` and `templates/TEST_SPEC.md` require
each FR's derived test set to cover seven kinds — failure, boundary, negative,
integration, state_transition, fault_injection, nfr_pattern — and
`derive_test_cases.md:190` states that YAML names alone "do not satisfy" them.

`spec_coverage._parse_test_spec` already reads a `type` and a `derivation`
column per row and carries both through to its Missing report. What no code
anywhere does is ask whether the declared set covers the seven. Grepped across
`core/`, `cli/`, `harness/*.py` and `scripts/`, the strings `nfr_pattern`,
`fault_injection` and `state_transition` appear only inside the prompt and the
template — never in a comparison.

Measured: taskq-plus's TEST_SPEC declares **zero** `nfr_pattern` cases across
64 tests and passed every gate at 98.7 composite. taskq-renew declared nine and
delivered one, which is how it ended up on a defect list. The contract has
existed since `a3ef99f` (Round 10 站4) — six rounds with no enforcer, and in
that time the only project it ever touched was the one that tried to satisfy
it.

The registry has to be the same list the prompt renders, for the reason Round
17 站1 gave: a rule the prompt states and a gate enforces belongs in one place.
"""

from __future__ import annotations

from pathlib import Path

from core.quality_gate import spec_coverage, test_types


def _project(tmp_path: Path, rows: str) -> Path:
    (tmp_path / "02-architecture").mkdir(parents=True)
    (tmp_path / "02-architecture" / "TEST_SPEC.md").write_text(
        "# TEST_SPEC.md\n\n## FR-01: task submission\n\n"
        "| # | Test Function | Type | Derivation |\n|---|---|---|---|\n" + rows,
        encoding="utf-8",
    )
    (tmp_path / "03-development" / "tests").mkdir(parents=True)
    return tmp_path


def test_the_required_types_are_one_list():
    """The seven names, in one importable place."""
    assert test_types.REQUIRED_TEST_TYPES == (
        "failure",
        "boundary",
        "negative",
        "integration",
        "state_transition",
        "fault_injection",
        "nfr_pattern",
    )


def test_a_missing_nfr_pattern_derivation_is_reported(tmp_path: Path):
    """taskq-plus's shape: every other type present, `nfr_pattern` absent.

    The gap must name the type and the FR — "coverage is 100%" was true of
    every name in that file, and the derivation it never wrote was the point.
    """
    rows = "".join(
        f"| {i} | `test_fr01_case_{i}` | {t} | Q1 |\n"
        for i, t in enumerate(test_types.REQUIRED_TEST_TYPES[:-1], start=1)
    )
    report = spec_coverage.spec_coverage_report(_project(tmp_path, rows))
    assert report["missing_types"] == {"FR-01": ["nfr_pattern"]}


def test_all_seven_types_declared_reports_no_gap(tmp_path: Path):
    """Positive control — a complete derivation must not be flagged."""
    rows = "".join(
        f"| {i} | `test_fr01_case_{i}` | {t} | Q1 |\n"
        for i, t in enumerate(test_types.REQUIRED_TEST_TYPES, start=1)
    )
    report = spec_coverage.spec_coverage_report(_project(tmp_path, rows))
    assert report["missing_types"] == {}
