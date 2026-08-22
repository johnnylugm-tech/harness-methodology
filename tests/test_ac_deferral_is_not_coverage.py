"""A criterion that was deferred is not a criterion that was covered
(Round 69 站5).

1547d71 added Step 1d to `harness/ssi/prompts/derive_test_cases.md`, and the
need behind it is real: an NFR verified by `pip-licenses` / `import-linter` /
`mutmut` / a docstring scanner trips none of the NP-01..NP-15 patterns, so
Steps 1/1b/1c give it no test case, and `check_ac_test_spec_coverage` — which
requires every declared `AC-` id to appear *somewhere* in TEST_SPEC.md — then
reads it as a dropped requirement. Before Step 1d there was no legal move.

But Step 1d's legal move is a sentence, and the check that reads it is a
substring search. So the prompt now teaches, in writing, how to satisfy the
coverage gate with one line of prose. `check_ac_test_spec_coverage`'s own
docstring cites `AC-N7.2` ("`08-config/SBOM.json` exists") as the criterion
that reached delivery unverified; after Step 1d, `Deferred: AC-N7.2 — SBOM
check` closes that gate legitimately.

The fix is at the layer that conflates cited with covered. Three states, not
two: covered, deferred (recorded, not free, and required to name its
verifier), uncited (still an error).
"""
from __future__ import annotations

import json
from pathlib import Path

from core.quality_gate.artifact_consistency import (
    ac_deferral_shape,
    check_ac_test_spec_coverage,
    record_ac_deferrals,
)

_SRS = (
    "# SRS\n\n### NFR-07: licence compliance\n\n**Acceptance criteria**\n\n"
    "- **AC-N7.1**: no GPL dependency ships.\n"
    "- **AC-N7.2**: `08-config/SBOM.json` exists.\n"
)


def _project(tmp_path: Path, test_spec: str) -> Path:
    (tmp_path / "01-requirements").mkdir(parents=True, exist_ok=True)
    (tmp_path / "01-requirements" / "SRS.md").write_text(_SRS, encoding="utf-8")
    (tmp_path / "02-architecture").mkdir(parents=True, exist_ok=True)
    (tmp_path / "02-architecture" / "TEST_SPEC.md").write_text(
        test_spec, encoding="utf-8")
    (tmp_path / ".methodology").mkdir(parents=True, exist_ok=True)
    return tmp_path


def test_a_deferral_line_is_recorded_not_counted_as_coverage(
    tmp_path: Path,
) -> None:
    spec = (
        "# TEST_SPEC.md\n\n### NFR-07: licence compliance\n\n"
        "| Case | Test Function | Derivation |\n|---|---|---|\n"
        "| 1 | `test_no_gpl_dependency` | AC-N7.1 |\n\n"
        "Deferred: AC-N7.2 — `pip-licenses` SBOM check (NFR-07), "
        "not a TEST_SPEC case.\n"
    )
    violations = check_ac_test_spec_coverage(_project(tmp_path, spec))
    kinds = {v.check_type for v in violations}
    assert "ac_no_test_case" not in kinds, (
        "a named deferral is not the same as an uncited criterion"
    )
    deferred = [v for v in violations if v.check_type == "ac_deferred"]
    assert len(deferred) == 1 and deferred[0].severity == "info", (
        "the deferral must be visible and non-blocking, not silent"
    )
    assert "AC-N7.2" in deferred[0].message


def test_a_deferral_is_written_to_the_ledger(tmp_path: Path) -> None:
    """Non-blocking must not mean free (Round 68 站1's rule)."""
    spec = (
        "# TEST_SPEC.md\n\n| 1 | `test_no_gpl_dependency` | AC-N7.1 |\n\n"
        "Deferred: AC-N7.2 — `pip-licenses` SBOM check (NFR-07), "
        "not a TEST_SPEC case.\n"
    )
    project = _project(tmp_path, spec)
    record_ac_deferrals(project)
    rows = [json.loads(line) for line in
            (project / ".methodology" / "degradations.jsonl")
            .read_text(encoding="utf-8").splitlines() if line.strip()]
    mine = [r for r in rows if r.get("component") == "gate:ac-deferred"]
    assert mine and mine[0].get("owner") == "project"


def test_a_deferral_that_names_no_tool_is_an_error(tmp_path: Path) -> None:
    """"all unit-layer; deferred to downstream phases" is the shape Step 1d's
    own `Why:` block records an agent inventing. A deferral with no verifier
    names nobody, so nobody can ever be asked whether it ran."""
    spec = (
        "# TEST_SPEC.md\n\n| 1 | `test_no_gpl_dependency` | AC-N7.1 |\n\n"
        "Deferred: AC-N7.2\n"
    )
    violations = check_ac_test_spec_coverage(_project(tmp_path, spec))
    unattributed = [v for v in violations
                    if v.check_type == "ac_deferral_unattributed"]
    assert len(unattributed) == 1 and unattributed[0].severity == "error"


def test_an_uncited_criterion_is_still_an_error(tmp_path: Path) -> None:
    spec = "# TEST_SPEC.md\n\n| 1 | `test_no_gpl_dependency` | AC-N7.1 |\n"
    violations = check_ac_test_spec_coverage(_project(tmp_path, spec))
    assert [v.check_type for v in violations] == ["ac_no_test_case"]


def test_the_prompt_quotes_the_shape_verbatim() -> None:
    """Same binding `spec_phase1.py` already has to `ac_label_shape()`: the
    prompt states the shape the checker matches, from the checker's own
    source, so the two cannot disagree."""
    prompt = Path("harness/ssi/prompts/derive_test_cases.md").read_text(
        encoding="utf-8")
    assert ac_deferral_shape() in prompt


def test_the_new_rows_are_documented_where_their_siblings_are() -> None:
    """`docs/OBSERVABILITY.md` is where `ac_no_test_case`,
    `ac_population_unread` and `ac_parse_gap` are explained to whoever reads
    an `error_details` payload. A fourth and fifth row that nobody documented
    is a row nobody can act on."""
    doc = Path("docs/OBSERVABILITY.md").read_text(encoding="utf-8")
    assert "ac_deferred" in doc
    assert "ac_deferral_unattributed" in doc
