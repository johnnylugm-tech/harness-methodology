"""The NFR declarations the framework itself made uncheckable.

Round 87 站3. `scripts/workflowgen/spec_phase2.py` dictates TEST_SPEC.md's
tables in one sentence, and until this round that sentence said two things at
once:

    For NFRs where TEST_SPEC.md is the verifier (Integration-level), you MUST
    define concrete `Inputs` … and `Sub-assertions`. For all other NFRs
    (Unit/Static), isolate them in a `Deferred to Downstream Phases` table
    with columns: #, NFR, Test Function, Layer, Title.

Integration NFRs got thresholds. Unit/static NFRs got a prose title. So the 58
unit/static declarations taskq-redo wrote had no machine-checkable content but
their own names — and a name is exactly what `spec_coverage` scored, and what
Round 83 站3's `check_ac_deferral_targets` blocked on.

What that bought, in taskq-redo's own delivered tree:

    def test_project_mi_at_least_80():
        ...
        assert avg >= 78.0, f"project MI avg {avg:.1f} < 78.0 (SPEC floor 80.0; …)"

The name states the criterion. The assertion states a different number. The
failure message names the gap in prose. Nothing read any of it.

SCOPE, AND WHY IT IS NARROW

Measured over the FR tables, which have carried `Inputs` all along: 772
declared values across eight corpus projects, 107 absent from their tests.
Restricting to values that are wholly numeric (dropping `key_id="k-revoked-1"`
and 64-hex digests) gives 569 checked / 64 absent — 88.8% parity. That 11% is
real signal mixed with fixture data written before any rule asked for this, so
blocking the FR tables would charge projects for old rows (Round 42). This
check is scoped to the Deferred table, where the column is new and has no
legacy content, and the FR-table measurement is recorded in the ledger with a
re-open condition instead.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from cli.checks.specs import deferred_inputs_violations

pytestmark = [pytest.mark.core]

REPO = Path(__file__).resolve().parents[1]

_WITH_INPUTS = """\
## Deferred to Downstream Phases (Unit/Static NFRs)

| # | NFR | Test Function | Layer | Inputs | Title |
|---|---|---|---|---|---|
| 1 | NFR-11 | `test_project_mi_at_least_80` | static | mi_floor="80" | project MI >= 80 (AC-N11.1) |
| 2 | NFR-01 | `test_get_by_id_p95_under_30ms` | unit | p95_budget_ms="30"; rows="10000" | GET p95 (AC-N1.1) |
"""

_WITHOUT_INPUTS = """\
## Deferred to Downstream Phases (Unit/Static NFRs)

| # | NFR | Test Function | Layer | Title |
|---|---|---|---|---|
| 1 | NFR-11 | `test_project_mi_at_least_80` | static | project MI >= 80 (AC-N11.1) |
"""


def _project(tmp_path: Path, spec: str, tests: str) -> Path:
    (tmp_path / "02-architecture").mkdir(parents=True)
    (tmp_path / "02-architecture" / "TEST_SPEC.md").write_text(spec, encoding="utf-8")
    (tmp_path / "03-development" / "tests").mkdir(parents=True)
    (tmp_path / "03-development" / "tests" / "test_nfr.py").write_text(
        tests, encoding="utf-8")
    return tmp_path


def test_the_shipped_drift_is_reported(tmp_path: Path) -> None:
    """taskq-redo's exact shape: declares 80, asserts 78."""
    project = _project(tmp_path, _WITH_INPUTS, (
        "def test_project_mi_at_least_80():\n"
        "    avg = 79.0\n"
        "    assert avg >= 78.0\n\n"
        "def test_get_by_id_p95_under_30ms():\n"
        "    budget = 30\n"
        "    rows = 10000\n"
        "    assert budget and rows\n"
    ))
    not_checked, violations = deferred_inputs_violations(project)
    assert not not_checked, "the table declares Inputs, so the check must run"
    assert len(violations) == 1, violations
    assert "test_project_mi_at_least_80" in violations[0]
    assert "mi_floor=80" in violations[0]


def test_a_threshold_read_from_a_framework_constant_is_not_a_violation(
    tmp_path: Path,
) -> None:
    """The better implementation must not be the one that gets blocked.

    taskq-cc-new's real p95 test reads `cfg["performance"]["p95_budget_ms"]`
    rather than retyping 30. Whatever form the number reaches the test in, the
    value it asserts against is the declared one — which is the whole property
    this check is about, so a constant-fed test passes on its own terms.
    """
    project = _project(tmp_path, _WITH_INPUTS, (
        "BUDGET_MS = 30\nROWS = 10000\nMI_FLOOR = 80\n\n"
        "def test_project_mi_at_least_80():\n"
        "    assert 99.0 >= MI_FLOOR\n\n"
        "def test_get_by_id_p95_under_30ms():\n"
        "    assert BUDGET_MS and ROWS\n"
    ))
    not_checked, violations = deferred_inputs_violations(project)
    assert not not_checked
    assert violations == [], violations


def test_a_row_with_no_threshold_is_a_name_again(tmp_path: Path) -> None:
    """An Inputs cell that declares nothing checkable is the defect restated."""
    spec = _WITH_INPUTS.replace('mi_floor="80"', "(none)")
    project = _project(tmp_path, spec, (
        "def test_project_mi_at_least_80():\n    assert True\n\n"
        "def test_get_by_id_p95_under_30ms():\n"
        "    assert 30 and 10000\n"
    ))
    _not_checked, violations = deferred_inputs_violations(project)
    assert len(violations) == 1
    assert "declares no `key=<number>` threshold" in violations[0]


def test_a_table_without_the_column_says_so_instead_of_passing(
    tmp_path: Path,
) -> None:
    """Forward-only migration, and a check that cannot run must not read clean.

    Every TEST_SPEC.md written before this round has the five-column table.
    Round 74 站4's rule: "nothing to check" and "nothing found" are different
    sentences, and a half-built mechanism that silently passes is Round 30's.
    """
    project = _project(tmp_path, _WITHOUT_INPUTS, (
        "def test_project_mi_at_least_80():\n    assert 1 >= 78.0\n"
    ))
    not_checked, violations = deferred_inputs_violations(project)
    assert not_checked, "a table with no Inputs column must report not-checked"
    assert "Inputs" in not_checked
    assert violations == []


def test_an_absent_test_is_not_this_checks_finding(tmp_path: Path) -> None:
    """spec-coverage owns "nobody wrote it"; this one owns "it asserts wrong".

    Two checks reporting the same defect is how a repair gets applied twice
    and measured once.
    """
    project = _project(tmp_path, _WITH_INPUTS, (
        "def test_get_by_id_p95_under_30ms():\n"
        "    assert 30 and 10000\n"
    ))
    _not_checked, violations = deferred_inputs_violations(project)
    assert violations == [], (
        "test_project_mi_at_least_80 does not exist; that is spec-coverage's "
        f"finding, not a threshold contradiction: {violations}"
    )


def test_the_generator_asks_for_the_column_it_enforces() -> None:
    """The rule and the instruction that produces its input are one statement.

    `spec_phase2.py` is the only place the Deferred table's columns are named.
    An enforcer for a column no prompt asks for would block every project
    forever; a prompt asking for a column no enforcer reads is Round 30's
    half-built mechanism. This pins that both exist.
    """
    src = (REPO / "scripts" / "workflowgen" / "spec_phase2.py").read_text(
        encoding="utf-8")
    assert "Test Function, Layer, Inputs, Title" in src, (
        "spec_phase2.py no longer asks for the Inputs column in the Deferred "
        "table, but deferred_inputs_violations still enforces it"
    )


def test_no_corpus_project_is_retroactively_blocked() -> None:
    """Forward-only, measured rather than asserted.

    All eight corpus TEST_SPEC.md files predate the column. Each must report
    not-checked with zero violations — if one starts failing, the migration
    was not forward-only and Round 42's rule was broken.
    """
    checked_any = False
    for name in ("taskq-redo", "taskq-cc", "taskq-cc-new", "taskq-new",
                 "taskq-super", "taskq-api", "taskq-advance", "taskq-renew"):
        project = Path("/Users/johnny/projects") / name
        if not (project / "02-architecture" / "TEST_SPEC.md").exists():
            continue
        checked_any = True
        not_checked, violations = deferred_inputs_violations(project)
        assert violations == [], (
            f"{name} is blocked by a rule written after its TEST_SPEC.md: {violations}"
        )
        assert not_checked, f"{name} unexpectedly has the new column"
    if not checked_any:
        pytest.skip("corpus projects not present on this machine")
