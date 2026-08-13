"""The acceptance criterion is the only wire, and nobody counts the strands
(Round 51 站0).

An FR reaches an implementation along exactly one path in this framework:
SPEC section → SRS section → **acceptance criteria** → TEST_SPEC case →
test function → code. Every check that exists operates on the two ends. D4
spec-coverage compares TEST_SPEC's test names to the names in the test file.
`TRACEABILITY_MATRIX.md` links an FR id to a file. Nothing counts acceptance
criteria, and nothing asks whether each one produced a case.

Measured 2026-08-14 on the two trees built from the byte-identical SPEC.md:

    SRS `AC-<n>.<m>` identifiers   taskq-advance 95     taskq-api 0
    AC bullets under the ten FRs   taskq-advance 46     taskq-api 33
    TEST_SPEC citing an AC id      taskq-advance 6      taskq-api 0

FR-09 is the whole chain visible at once. SPEC.md L158 is a table row —
``| `GET /v1/metrics` | `admin` | 任務計數(按狀態)、執行延遲分位數、
rate-limit 拒絕數 |``. taskq-api's SRS transcribes that row verbatim, and then
lists three acceptance criteria, none about `/v1/metrics`. TEST_SPEC has no
metrics case; the test file has no metrics-403 test; and `app.py:295` mounts
`/v1/metrics` with no auth dependency, returning only a redacted DB URL. Every
downstream check agreed with every other, because they were all reading the
same silence.

The framework's own AC parser has never seen an AC. `scripts/canonical_diff.py`
names its output `total_ac` / `per_ac` and its heading regex requires an
`AC`-prefixed heading. Run over both SRS files it returns 23 and 22 clauses —
which are the FR and NFR section headings, one each. Every "AC clause" the
harness has scored in five projects was a section.

What these tests can and cannot reach is stated in
docs/PROPOSAL_ADJUDICATIONS.md: an AC that exists and produced no case is
catchable; a SPEC table row that produced no AC is not, and Round 51 did not
try to guess at it with a ratio.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# taskq-api's FR-09 section, verbatim (SRS.md). The `/v1/metrics` row is
# present; the acceptance criteria below it are the three that shipped.
_SRS_UNIDENTIFIED = """\
# SRS

### FR-09: 健康檢查與可觀測性

| 端點 | 認證 | 行為 |
|------|------|------|
| `GET /healthz` | 無 | 進程存活 → 200 |
| `GET /v1/metrics` | `admin` | 任務計數(按狀態)、執行延遲分位數、rate-limit 拒絕數 |

**Acceptance Criteria** (canonical SPEC §8 #10, #11):
- 停掉 DB 後 `GET /readyz` → 503,detail 指明 DB 不可用 (SPEC §8 #10).
- `alembic downgrade -1` 後 `GET /readyz` → 503 (SPEC §8 #11).
- `/healthz` 不要求認證(SPEC §3 FR-09 / FR-03 exception).
"""

# The same section in the shape taskq-advance used: every criterion numbered,
# so a downstream artifact can cite one and a checker can count them.
_SRS_IDENTIFIED = """\
# SRS

### FR-09: 健康檢查與可觀測性

**Acceptance Criteria**

- **AC-9.1**: 停掉 DB 後 `GET /readyz` → 503,detail 指明 DB 不可用.
- **AC-9.2**: `alembic downgrade -1` 後 `GET /readyz` → 503.
- **AC-9.3**: `/healthz` 不要求認證.
- **AC-9.5**: `/v1/metrics` 需要 `admin` scope;`read` key → 403.
"""

_TEST_SPEC_COVERING = """\
# TEST_SPEC.md

### FR-09: 健康檢查與可觀測性

| Case | Test Function | Derivation |
|---|---|---|
| 1 | `test_readyz_503_when_db_unreachable` | AC-9.1 |
| 2 | `test_readyz_503_when_migration_lag` | AC-9.2 |
| 3 | `test_healthz_readyz_no_auth` | AC-9.3 |
| 4 | `test_metrics_requires_admin` | AC-9.5 |
"""

_TEST_SPEC_MISSING_ONE = """\
# TEST_SPEC.md

### FR-09: 健康檢查與可觀測性

| Case | Test Function | Derivation |
|---|---|---|
| 1 | `test_readyz_503_when_db_unreachable` | AC-9.1 |
| 2 | `test_readyz_503_when_migration_lag` | AC-9.2 |
| 3 | `test_healthz_readyz_no_auth` | AC-9.3 |
"""


def _project(tmp_path: Path, srs: str, test_spec: str | None = None) -> Path:
    (tmp_path / "01-requirements").mkdir(parents=True)
    (tmp_path / "01-requirements" / "SRS.md").write_text(srs, encoding="utf-8")
    if test_spec is not None:
        (tmp_path / "02-architecture").mkdir(parents=True, exist_ok=True)
        (tmp_path / "02-architecture" / "TEST_SPEC.md").write_text(
            test_spec, encoding="utf-8")
    return tmp_path


def test_an_unnumbered_acceptance_criterion_is_a_violation(tmp_path):
    """Without an identifier no later artifact can cite it and no checker can count it."""
    from core.quality_gate.artifact_consistency import check_ac_identifiers

    v = check_ac_identifiers(_project(tmp_path, _SRS_UNIDENTIFIED))
    assert v, (
        "taskq-api's SRS carries 33 acceptance criteria and zero identifiers; "
        "the framework accepted it and every downstream traceability number "
        "read 100 %"
    )
    assert any("FR-09" in x.message for x in v)


def test_numbered_criteria_pass(tmp_path):
    """The control — taskq-advance's shape must not be a finding."""
    from core.quality_gate.artifact_consistency import check_ac_identifiers

    assert check_ac_identifiers(_project(tmp_path, _SRS_IDENTIFIED)) == []


def test_an_acceptance_criterion_with_no_test_case_is_named(tmp_path):
    """AC-9.5 is the one that was dropped; the check has to say so by id."""
    from core.quality_gate.artifact_consistency import check_ac_test_spec_coverage

    project = _project(tmp_path, _SRS_IDENTIFIED, _TEST_SPEC_MISSING_ONE)
    v = check_ac_test_spec_coverage(project)
    messages = " ".join(x.message for x in v)
    assert "AC-9.5" in messages, (
        "the acceptance criterion for `/v1/metrics` admin scope produced no "
        "TEST_SPEC case, and that is the last point at which the requirement "
        "could still have been caught"
    )
    assert "AC-9.1" not in messages, (
        "criteria that did produce a case must not be reported — a check that "
        "names everything names nothing"
    )


def test_full_coverage_is_silent(tmp_path):
    from core.quality_gate.artifact_consistency import check_ac_test_spec_coverage

    project = _project(tmp_path, _SRS_IDENTIFIED, _TEST_SPEC_COVERING)
    assert check_ac_test_spec_coverage(project) == []


def test_the_checks_are_exported():
    """Both belong to `artifact_consistency`'s public surface, like its siblings.

    `check-artifact-consistency` iterates `__all__`; a check that is not there
    is a check preflight never runs (Round 30's half-built mechanism).
    """
    from core.quality_gate import artifact_consistency

    assert "check_ac_identifiers" in artifact_consistency.__all__
    assert "check_ac_test_spec_coverage" in artifact_consistency.__all__


@pytest.mark.parametrize("missing", ["srs", "test_spec"])
def test_a_missing_artifact_is_not_a_pass(tmp_path, missing):
    """Round 46: an absent witness is not a clean bill of health.

    With no SRS there are no criteria to check and the honest answer is an
    empty result — but with an SRS and no TEST_SPEC, every criterion is
    uncovered and the check must say so rather than returning [].
    """
    from core.quality_gate.artifact_consistency import check_ac_test_spec_coverage

    if missing == "srs":
        project = _project(tmp_path, "# SRS\n", _TEST_SPEC_COVERING)
        assert check_ac_test_spec_coverage(project) == []
    else:
        project = _project(tmp_path, _SRS_IDENTIFIED)
        v = check_ac_test_spec_coverage(project)
        assert v, "four criteria and no TEST_SPEC at all reported nothing"


def test_criteria_written_as_headings_are_read_too(tmp_path):
    """taskq and taskq-renew write `#### AC-1.1`; advance writes bullets.

    A parser that reads one shape returns nothing for the other and reports
    zero findings, which is Round 46's defect committed by the checker itself.
    """
    from core.quality_gate.artifact_consistency import check_ac_identifiers

    srs = """\
# SRS

### FR-01: Task submission

#### AC-1.1
`submit "echo hi"` exits 0 and prints an 8-hex id.

#### AC-1.2
`submit ""` exits 2.
"""
    assert check_ac_identifiers(_project(tmp_path, srs)) == []


def test_the_lowercase_label_is_read(tmp_path):
    """taskq-renew writes `**Acceptance criteria**`; advance writes `Criteria`."""
    from core.quality_gate.artifact_consistency import check_ac_identifiers

    srs = """\
# SRS

### FR-01: Task submission

**Acceptance criteria** (each machine-decidable)

- a criterion with no identifier at all
"""
    v = check_ac_identifiers(_project(tmp_path, srs))
    assert any(x.check_type == "ac_unnumbered" for x in v), (
        "the label differed only in one letter's case and the whole section "
        "was invisible"
    )


def test_identifiers_outside_a_readable_shape_are_reported_as_unread(tmp_path):
    """Not clean — unchecked. The distinction Round 46 站1 exists to keep."""
    from core.quality_gate.artifact_consistency import check_ac_identifiers

    srs = """\
# SRS

### FR-01: Task submission

Some prose that mentions **AC-01-9:** in a shape this parser does not read.

**Acceptance Criteria**

- **AC-01-1**: a criterion in a shape it does read.
"""
    v = check_ac_identifiers(_project(tmp_path, srs))
    gaps = [x for x in v if x.check_type == "ac_parse_gap"]
    assert gaps, (
        "an `AC-` identifier the parser could not attribute to a requirement "
        "was silently dropped, and the check reported clean"
    )
    assert "AC-01-9" in gaps[0].message
    assert gaps[0].severity == "info", (
        "the framework not being able to read a shape is the framework's "
        "debt, not the project's failure (Round 32 站4)"
    )
