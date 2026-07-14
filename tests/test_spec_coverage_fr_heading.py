"""Regression: `_parse_test_spec` FR-header detection must tolerate
TOC-numbered subsections, not just the canonical `### FR-XX:` form.

Same bug class documented in spec_alignment.py / artifact_parsers.py /
core.phase_hooks.preflight_fr_spec_consistency (2026-07-14 phase1-requirements
E2E): a heading regex anchored on `### FR-XX` misses the natural SRS/TEST_SPEC
authoring form `### 2.1 FR-01` (§2 Functional Test Cases / §2.1 FR-01 TOC
numbering). Without SRS_SUBSECTION_PREFIX every per-FR test-case table row
falls through to the "not an FR header" fallback slug and D4 spec-coverage
sees zero properly-attributed FR test cases.
"""

from __future__ import annotations

from pathlib import Path

from core.quality_gate.spec_coverage import _parse_test_spec


def _write_test_spec(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_canonical_heading_still_works(tmp_path: Path) -> None:
    spec = _write_test_spec(
        tmp_path / "TEST_SPEC.md",
        "# TEST_SPEC\n\n"
        "### FR-01: alpha\n"
        "| # | Test Function | Type | Derivation |\n"
        "|---|---|---|---|\n"
        "| 1 | `test_fr01_alpha` | happy_path | Q1 |\n",
    )
    results = _parse_test_spec(spec)
    assert len(results) == 1
    assert results[0]["fr_id"] == "FR-01"


def test_subsection_numbered_heading_attributed_correctly(tmp_path: Path) -> None:
    spec = _write_test_spec(
        tmp_path / "TEST_SPEC.md",
        "# TEST_SPEC\n\n"
        "### 2.1 FR-01 alpha\n"
        "| # | Test Function | Type | Derivation |\n"
        "|---|---|---|---|\n"
        "| 1 | `test_fr01_alpha` | happy_path | Q1 |\n\n"
        "### 2.2 FR-02 beta\n"
        "| # | Test Function | Type | Derivation |\n"
        "|---|---|---|---|\n"
        "| 1 | `test_fr02_beta` | happy_path | Q1 |\n",
    )
    results = _parse_test_spec(spec)
    fr_ids = {r["fr_id"] for r in results}
    assert fr_ids == {"FR-01", "FR-02"}, (
        f"expected rows attributed to FR-01/FR-02, got {fr_ids} "
        "(subsection-numbered heading fell through to fallback slug)"
    )


def test_subsection_numbered_three_level_heading(tmp_path: Path) -> None:
    spec = _write_test_spec(
        tmp_path / "TEST_SPEC.md",
        "# TEST_SPEC\n\n"
        "### 2.1.1 FR-01 alpha\n"
        "| # | Test Function | Type | Derivation |\n"
        "|---|---|---|---|\n"
        "| 1 | `test_fr01_alpha` | happy_path | Q1 |\n",
    )
    results = _parse_test_spec(spec)
    assert len(results) == 1
    assert results[0]["fr_id"] == "FR-01"


def test_non_fr_section_still_falls_through_to_slug(tmp_path: Path) -> None:
    """Negative control: a genuinely non-FR heading must still be tagged
    with the normalised-slug fallback, not misread as an FR."""
    spec = _write_test_spec(
        tmp_path / "TEST_SPEC.md",
        "# TEST_SPEC\n\n"
        "### 1.1 Test Strategy Overview\n"
        "| # | Test Function | Type | Derivation |\n"
        "|---|---|---|---|\n"
        "| 1 | `test_setup` | happy_path | Q1 |\n",
    )
    results = _parse_test_spec(spec)
    assert len(results) == 1
    assert results[0]["fr_id"] != ""
    assert not results[0]["fr_id"].startswith("FR-")
