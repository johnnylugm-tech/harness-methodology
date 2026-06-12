"""Regression: spec-coverage-check must not vacuously pass when TEST_SPEC.md
exists but contains 0 parseable test cases AND FRs are defined.

E2E framework bug #4 (integration-test run, 2026-06-12): the project shipped
a TEST_SPEC.md that was a 27KB prose "test strategy overview" with no
`derive_test_cases.md` table rows. The parser correctly found 0 test cases
in the wrong-shape file, but `_run_spec_coverage_check` returned `(0, 100.0)`
— a vacuous pass that masked the structural defect and let Gate 2 composite
reach 92.25 while the actual deliverable (named test-case catalog) was
missing.

Fix: when 0 items parsed AND any FR is declared (SAD.md or SPEC_TRACKING.md
contains `FR-NN` pattern), return `(1, 0.0)` — block the gate. Vacuous pass
is only allowed when no FRs are defined (genuinely empty project).
"""

from __future__ import annotations

from pathlib import Path


from harness_cli import _run_spec_coverage_check


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed_sad_with_frs(project: Path, frs: list[str]) -> None:
    """Create 02-architecture/SAD.md with `### FR-XX` rows."""
    (project / "02-architecture").mkdir(parents=True, exist_ok=True)
    lines = ["# SAD\n", "## Modules\n"]
    for fr in frs:
        lines.append(f"### {fr}: a module\n")
    (project / "02-architecture" / "SAD.md").write_text("\n".join(lines), encoding="utf-8")


def _seed_test_spec_prose(project: Path) -> None:
    """Create a TEST_SPEC.md that is prose (wrong shape — no table)."""
    (project / "02-architecture").mkdir(parents=True, exist_ok=True)
    (project / "02-architecture" / "TEST_SPEC.md").write_text(
        "# TEST_SPEC\n\n"
        "## §1 Test Strategy Overview\n"
        "This is a prose strategy document. No table rows.\n",
        encoding="utf-8",
    )


def _seed_test_spec_with_table(project: Path, fr_id: str, fn_name: str) -> None:
    """Create a TEST_SPEC.md that DOES have a valid table row for `fr_id`."""
    (project / "02-architecture").mkdir(parents=True, exist_ok=True)
    (project / "02-architecture" / "TEST_SPEC.md").write_text(
        "# TEST_SPEC\n\n"
        f"### {fr_id}: example\n"
        "| # | Test Function | Type | Derivation |\n"
        "|---|---|---|---|\n"
        f"| 1 | `{fn_name}` | happy_path | Q1 |\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# B.3 regression tests
# ---------------------------------------------------------------------------


class TestSpecCoverageVacuousFix:
    def test_prose_test_spec_with_frs_returns_blocked(self, tmp_path: Path):
        """B.3 core case: wrong-shape TEST_SPEC.md + FRs defined → fail (1, 0.0)."""
        _seed_sad_with_frs(tmp_path, ["FR-01", "FR-02"])
        _seed_test_spec_prose(tmp_path)

        code, pct = _run_spec_coverage_check(tmp_path, threshold=60.0, verbose=False)
        assert code == 1, "must block when 0 cases parsed but FRs defined"
        assert pct == 0.0

    def test_prose_test_spec_no_frs_vacuous_pass(self, tmp_path: Path):
        """0 cases + no FRs defined → vacuous pass OK (genuinely empty)."""
        # No SAD.md, no SPEC_TRACKING.md
        (tmp_path / "02-architecture").mkdir(parents=True, exist_ok=True)
        _seed_test_spec_prose(tmp_path)

        code, pct = _run_spec_coverage_check(tmp_path, threshold=60.0, verbose=False)
        assert code == 0
        assert pct == 100.0

    def test_prose_test_spec_with_spec_tracking_frs_blocked(self, tmp_path: Path):
        """SAD missing but SPEC_TRACKING.md has FR-XX → still blocked."""
        # No SAD.md, but SPEC_TRACKING.md has FR-01
        (tmp_path / "01-requirements").mkdir(parents=True)
        (tmp_path / "01-requirements" / "SPEC_TRACKING.md").write_text(
            "# Spec Tracking\n\n| FR ID | Status |\n| FR-01 | IN_PROGRESS |\n",
            encoding="utf-8",
        )
        _seed_test_spec_prose(tmp_path)

        code, pct = _run_spec_coverage_check(tmp_path, threshold=60.0, verbose=False)
        assert code == 1
        assert pct == 0.0

    def test_per_fr_query_with_no_cases_for_that_fr_blocked(self, tmp_path: Path):
        """--fr-id FR-01 query: TEST_SPEC.md has 0 cases for FR-01 → blocked."""
        # SAD declares FR-01, FR-02, FR-03
        _seed_sad_with_frs(tmp_path, ["FR-01", "FR-02", "FR-03"])
        # TEST_SPEC.md has only FR-02 cases (so FR-01 filter yields 0)
        _seed_test_spec_with_table(tmp_path, "FR-02", "test_fr02_happy")

        code, pct = _run_spec_coverage_check(
            tmp_path, threshold=60.0, fr_id="FR-01", verbose=False
        )
        assert code == 1
        assert pct == 0.0

    def test_missing_test_spec_with_sad_frs_blocked(self, tmp_path: Path):
        """TEST_SPEC.md absent but SAD has FRs → blocked (already worked pre-fix;
        this test guards against regression)."""
        _seed_sad_with_frs(tmp_path, ["FR-01"])

        code, pct = _run_spec_coverage_check(tmp_path, threshold=60.0, verbose=False)
        assert code == 1
        assert pct == 0.0

    def test_missing_test_spec_no_frs_vacuous_pass(self, tmp_path: Path):
        """TEST_SPEC.md absent, no FRs → vacuous pass (project just bootstrapped)."""
        # No SAD, no SPEC_TRACKING
        code, pct = _run_spec_coverage_check(tmp_path, threshold=60.0, verbose=False)
        assert code == 0
        assert pct == 100.0
