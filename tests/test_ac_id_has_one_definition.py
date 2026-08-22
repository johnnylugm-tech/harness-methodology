"""One acceptance-criterion identifier, one definition (Round 69 站3).

b128efb solved a real problem — TEST_SPEC sub-assertion rule_ids are routinely
written without the dash (`AC1.1-status-201`), and `_AC_ID` read zero tokens
out of them, so on taskq-new 59 of 100 declared criteria were reported as
cited by nothing. It solved it by adding a SECOND regex (`_AC_ID_BROAD`) plus
a normaliser, and the second regex re-opened two closed questions and opened a
third:

  * Round 56 removed a trailing `\\b` because the engine could satisfy it by
    backtracking to a PREFIX of the token: `AC-1.1a` came back as `AC-1`, so
    one TEST_SPEC citation covered two different criteria. `_AC_ID_BROAD` ends
    in `\\b` and does it again — measured, `_AC_ID` finds nothing in `AC-1.1a`
    and `_AC_ID_BROAD` finds `AC-1`.
  * `_normalise_ac_token` returns the raw token unchanged when the leading
    letter is not `N`, so a declared `AC-P1.1` never equals the cited
    `AC-P1.1-latency-p95` and is reported uncited.
  * `_AC_ID` admits a numeric branch suffix (`AC-9.1-2`) that the normaliser's
    own pattern rejects, so it returns `None` and the criterion is reported
    uncited unconditionally. **taskq-renew is written entirely in that shape:
    its violation count went 11 → 40, and the 29 new ones are all false.**

The fix keeps one body string and spells it twice, dash required on the
canonical side and optional on the citation side. Measured against the corpus,
that recovers b128efb's win (taskq-new 59 → 0) without its regression
(taskq-renew back to 11), and the zero-padding half of the normaliser is
deleted because it changes nothing anywhere: across taskq-advance (37
zero-padded SRS ids), taskq-super and taskq-renew, the number of criteria that
would match only after zero-padding is 0.
"""
from __future__ import annotations

from pathlib import Path

from core.quality_gate.artifact_consistency import (
    _AC_ID,
    _AC_ID_CITED,
    check_ac_test_spec_coverage,
)


def _project(tmp_path: Path, srs: str, test_spec: str) -> Path:
    (tmp_path / "01-requirements").mkdir(parents=True, exist_ok=True)
    (tmp_path / "01-requirements" / "SRS.md").write_text(srs, encoding="utf-8")
    (tmp_path / "02-architecture").mkdir(parents=True, exist_ok=True)
    (tmp_path / "02-architecture" / "TEST_SPEC.md").write_text(
        test_spec, encoding="utf-8")
    return tmp_path


def test_a_truncated_id_is_refused_by_both_readers() -> None:
    """Round 56's property, restated for the citation-side reader."""
    assert _AC_ID.findall("AC-1.1a") == []
    assert _AC_ID_CITED.findall("AC-1.1a") == [], (
        "the citation reader backtracked to `AC-1`, which collapses AC-1.1a "
        "and AC-1.1b onto one identifier a single citation then covers"
    )


def test_a_numeric_branch_suffix_survives_the_round_trip(tmp_path: Path) -> None:
    """taskq-renew's whole SRS is `AC-01-1`, `AC-01-2`, … verbatim."""
    srs = (
        "# SRS\n\n### FR-01: widget\n\n**Acceptance criteria**\n\n"
        "- **AC-01-1**: accepts input.\n"
        "- **AC-01-2**: rejects empty input.\n"
    )
    spec = (
        "# TEST_SPEC.md\n\n### FR-01: widget\n\n"
        "| Case | Test Function | Derivation |\n|---|---|---|\n"
        "| 1 | `test_accepts` | AC-01-1 |\n"
        "| 2 | `test_rejects` | AC-01-2 |\n"
    )
    assert check_ac_test_spec_coverage(_project(tmp_path, srs, spec)) == []


def test_a_predicate_suffix_matches_the_criterion_it_sits_under(
    tmp_path: Path,
) -> None:
    """`AC-P1.1-latency-p95` is a predicate name under the criterion
    `AC-P1.1`; the leading letter must not decide whether that is true."""
    srs = (
        "# SRS\n\n### NFR-01: latency\n\n**Acceptance criteria**\n\n"
        "- **AC-P1.1**: p95 under 200ms.\n"
    )
    spec = (
        "# TEST_SPEC.md\n\n### NFR-01: latency\n\n"
        "| rule_id | predicate |\n|---|---|\n"
        "| AC-P1.1-latency-p95 | p95 < 200 |\n"
    )
    assert check_ac_test_spec_coverage(_project(tmp_path, srs, spec)) == []


def test_there_is_one_ac_body_literal_in_the_module() -> None:
    """The two spellings must share their body, or Round 55's range rule and
    Round 56's terminator have to be fixed twice next time."""
    src = Path("core/quality_gate/artifact_consistency.py").read_text(
        encoding="utf-8")
    assert "_AC_ID_BROAD" not in src, (
        "a second, independently written identifier pattern is back"
    )
    assert "_normalise_ac_token" not in src, (
        "the zero-padding normaliser is what returned None for AC-9.1-2"
    )
    assert src.count(r"(?:\.\d+)*(?:-\d+)*") == 1, (
        "the identifier body is spelled more than once"
    )
