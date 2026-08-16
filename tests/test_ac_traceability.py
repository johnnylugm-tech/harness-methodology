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


# ---------------------------------------------------------------------------
# Round 55 — the label the prompt permits, the abstention that read as clean,
# and the two checks nobody ran.
# ---------------------------------------------------------------------------

# The shape five of the seven projects on this machine actually wrote. The P1
# prompt says "a bolded prefix on a bullet under a `**Acceptance criteria**`
# label"; qualifying the label with the requirement id is a reading of that
# sentence, and it is the reading the agent took every time.
_SRS_QUALIFIED_LABEL = """\
# SRS

### NFR-07: 依賴與授權合規

**Acceptance criteria (NFR-07)**

- **AC-N7.1** — `pip-licenses --format=json --with-system` reports every package.
- **AC-N7.2** — `08-config/SBOM.json` exists; every entry has `name`, `version`,
  `license`, and `direct|transitive`.
"""


def test_a_qualified_acceptance_criteria_label_is_read(tmp_path):
    """`**Acceptance criteria (NFR-07)**` is the live shape, and it was invisible.

    Measured over the seven projects: the literal-label regex attributed 0 of
    taskq-super's 133 `AC-` identifiers and 5 of taskq-renew's 41. With zero
    attributed, `check_ac_test_spec_coverage` has no population and returns a
    clean bill — which is how AC-N7.2 (`08-config/SBOM.json` must exist) went
    from the SRS to delivery without ever being cited by a TEST_SPEC case.
    """
    from core.quality_gate.artifact_consistency import _srs_acceptance_criteria

    criteria = _srs_acceptance_criteria(_project(tmp_path, _SRS_QUALIFIED_LABEL))
    assert "NFR-07" in criteria, (
        "the bolded label carried the requirement id and the parser read the "
        "section as having no acceptance criteria at all"
    )
    assert any("AC-N7.2" in line for line in criteria["NFR-07"])


def test_a_range_expression_is_two_identifiers_not_one():
    """`AC-1.1..AC-1.10` in a DERIVED note is a range, not an identifier.

    The id regex's character class spans `.` and `-`, so it swallowed the whole
    range as one token. Measured: 22 of taskq-super's 133 "identifiers" and 1
    of taskq-renew's 41 were range expressions — tokens no criterion will ever
    carry, and therefore permanently unattributable. Any check that treats
    unattributed ids as findings would report them forever.
    """
    from core.quality_gate.artifact_consistency import _AC_ID

    assert _AC_ID.findall("AC-1.1..AC-1.10") == ["AC-1.1", "AC-1.10"]
    # The spellings the corpus does use must survive unchanged.
    assert _AC_ID.findall("AC-N7.2") == ["AC-N7.2"]
    assert _AC_ID.findall("AC-01-1") == ["AC-01-1"]
    assert _AC_ID.findall("- **AC-9.5**: metrics") == ["AC-9.5"]


def test_a_population_this_check_could_not_build_is_not_coverage(tmp_path):
    """Round 46's rule, applied to the checker Round 51 built to enforce it.

    `check_ac_test_spec_coverage` returned `[]` whenever it attributed no
    criteria, and `[]` is what full coverage also looks like. taskq-super
    carried 133 identifiers, 0 attributed, 0 findings, through Gate 4 PASS.

    This is deliberately NOT a severity change on `ac_parse_gap`. That row
    says "some identifiers sit in a shape I cannot read", which is the
    framework's debt and stays `info` (Round 32 站4, and the test above that
    fixes it). This row says something else: "I built no population at all,
    so my silence is not a verdict."
    """
    from core.quality_gate.artifact_consistency import check_ac_test_spec_coverage

    srs = """\
# SRS

### FR-03: Circuit breaker

Criteria, in a dialect this parser does not read:

| id | criterion |
|----|-----------|
| AC-3.1 | Retry policy is honoured. |
| AC-3.2 | OPEN threshold is five failures. |
"""
    v = check_ac_test_spec_coverage(_project(tmp_path, srs, _TEST_SPEC_COVERING))
    assert v, "two identifiers, none attributed, and the check reported clean"
    assert all(x.severity == "error" for x in v)
    assert v[0].check_type == "ac_population_unread"
    assert "AC-3.1" in v[0].message


def test_the_prompt_describes_a_label_the_parser_accepts(tmp_path):
    """Prompt-gate parity (Round 17 站1) on the sentence that caused this round.

    Every example the Phase 1 prompt gives for the criteria-block label must be
    one `_AC_BLOCK` actually matches. The prompt and the parser used to state
    the shape independently, and they disagreed about a qualifier for as long
    as anyone had written an SRS.
    """
    import re

    from core.quality_gate.artifact_consistency import (
        _srs_acceptance_criteria,
        ac_label_shape,
    )

    shape = ac_label_shape()
    examples = re.findall(r"`(\*\*Acceptance [Cc]riteria[^`]*\*\*)`", shape)
    assert examples, "the sentence gives no example of the label it describes"

    for i, label in enumerate(examples):
        srs = f"# SRS\n\n### FR-01: Task submission\n\n{label}\n\n- **AC-1.1**: ok.\n"
        criteria = _srs_acceptance_criteria(_project(tmp_path / str(i), srs))
        assert "FR-01" in criteria, (
            f"the prompt tells the agent {label!r} is legal and the parser "
            f"reads that section as having no acceptance criteria"
        )

    # And the generated Phase 1 workflow must carry this exact sentence, not a
    # paraphrase that drifts from it.
    js = (Path(__file__).parent.parent
          / ".claude" / "workflows" / "phase1-requirements.js").read_text(encoding="utf-8")
    assert shape in js, (
        "phase1-requirements.js was regenerated from a different description "
        "of the label than the parser enforces"
    )


def test_preflight_artifact_consistency_runs_the_two_ac_checks(tmp_path):
    """The executor. Both checks existed; `build_fingerprint` only recorded them.

    taskq-advance carried 86 acceptance criteria that no TEST_SPEC case cites
    and blocked nothing, because the only consumer of the finding was a JSON
    field. Blocking starts at P3, matching `check_nfr_adr_coverage` — the
    citation cannot be demanded before the artifact that would carry it exists.
    """
    from core.phase_hooks import PhaseHooks

    project = _project(tmp_path, _SRS_IDENTIFIED, _TEST_SPEC_MISSING_ONE)
    (project / ".methodology").mkdir(exist_ok=True)
    hooks = PhaseHooks(project_path=str(project), phase=3)
    res = hooks.preflight_artifact_consistency()

    assert res.get("passed") is False, (
        "AC-9.5 is cited by no TEST_SPEC case and the preflight passed"
    )
    detail = " ".join(
        str(d.get("message", "")) for d in (res.get("error_details") or [])
    ) + str(res.get("error", ""))
    assert "AC-9.5" in detail, "the block must name the criterion, not the count"


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


# ── Round 55: AC-id regex must stop at the canonical AC-X.Y boundary ──
# Regression for the bug where `_AC_ID`'s `[\w\-]+` accepted a dash, so an
# AC-id emitted as `AC-1.1-status` was extracted as `AC-1.1-status` rather
# than `AC-1.1`. taskq-api's TEST_SPEC.md uses branch labels in that
# pattern (`AC-1.2-empty-cmd-422`, `AC-7.3-sample-rows`, ...); the bug
# inflated the SRS↔TEST_SPEC diff to 85 missing references when the real
# gap was 4. Spec_coverage check stayed accurate on coverage shape, but
# the wrong id set made it BLOCK on every phase advance.
def test_ac_id_regex_stops_at_canonical_boundary():
    """AC-1.1-status must extract as AC-1.1, not AC-1.1-status."""
    from core.quality_gate.artifact_consistency import _AC_ID

    # Branch-label suffixes (the case taskq-advance hit 85 false-positives on)
    assert _AC_ID.findall("AC-1.1-status") == ["AC-1.1"]
    assert _AC_ID.findall("AC-7.3-sample-rows") == ["AC-7.3"]
    assert _AC_ID.findall("AC-1.2-empty-cmd-422") == ["AC-1.2"]
    # Existing corpus must still parse correctly.
    assert _AC_ID.findall("AC-1.1") == ["AC-1.1"]
    assert _AC_ID.findall("AC-1.1..AC-1.10") == ["AC-1.1", "AC-1.10"]
    assert _AC_ID.findall("AC-N7.2") == ["AC-N7.2"]
    assert _AC_ID.findall("AC-01-1") == ["AC-01-1"]
    assert _AC_ID.findall("- **AC-9.5**: metrics") == ["AC-9.5"]
    # Word/dash boundary semantics: AC-1.1 followed by a non-word character
    # (space, punctuation, dash) IS a valid match (the canonical case).
    # Word-boundary semantics: AC-1.1 followed by another word char (e.g.
    # `AC-1.1z`) is left for the next match attempt — `AC-1z` is not a
    # canonical id so the engine falls back to capturing `AC-1` (the longest
    # backtracking-legal match before the boundary).
    assert _AC_ID.findall("AC-1.1 (FR-01)") == ["AC-1.1"]
    assert _AC_ID.findall("AC-1.1,") == ["AC-1.1"]
    assert _AC_ID.findall("AC-1.1\n") == ["AC-1.1"]
    assert _AC_ID.findall("AC-1.1-end") == ["AC-1.1"]
    # AC-1.1 followed by a digit (e.g. AC-1.11) extends the dot-sequence.
    assert _AC_ID.findall("AC-1.11") == ["AC-1.11"]
