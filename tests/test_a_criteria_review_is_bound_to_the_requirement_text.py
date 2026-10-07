"""Round 115 站7 — a criteria review approval is bound to the requirement text it was shown.

`review-fr-tests` embeds the FR's requirement excerpt in the reviewer's
prompt and binds the approval to the assertions (AST digests) and the
declared tests — but to the requirement only by its PATH. Measured: change
"MUST drop every table" to "MUST keep every table" in SPEC.md, or 30 to 80,
and `approval_defects` still returns nothing; the approval describes a
requirement that no longer exists. The excerpt is now digested
(whitespace-normalised, so re-wrapping a line is not a change of meaning)
and compared. An approval written before the digest existed records nothing
to compare against, so it is not failed retroactively.
"""

from __future__ import annotations

from pathlib import Path

from core.quality_gate.criteria_review import REVIEW_BLOCK_KEY, approval_defects, review_sources
from tests.test_criteria_review import _SPEC, _approval, _project


def _bound(project: Path) -> dict:
    s = review_sources(project, "FR-01")
    approval = _approval(project, s)
    approval[REVIEW_BLOCK_KEY]["requirement_digest"] = s["requirement_digest"]
    return approval


def _defects(project: Path, approval: dict) -> list[str]:
    return approval_defects(project, "FR-01", approval)


def test_a_reversed_requirement_invalidates_the_approval(tmp_path):
    project = _project(tmp_path)
    approval = _bound(project)
    assert _defects(project, approval) == []
    (project / "SPEC.md").write_text(_SPEC.replace("MUST drop every table", "MUST keep every table"),
                                     encoding="utf-8")
    assert any("requirement text changed" in d for d in _defects(project, approval))


def test_a_changed_number_invalidates_the_approval(tmp_path):
    spec = _SPEC.replace("it created.", "it created within 30ms.")
    project = _project(tmp_path, spec=spec)
    approval = _bound(project)
    (project / "SPEC.md").write_text(spec.replace("30ms", "80ms"), encoding="utf-8")
    assert any("requirement text changed" in d for d in _defects(project, approval))


def test_rewrapping_the_requirement_is_not_a_change(tmp_path):
    project = _project(tmp_path)
    approval = _bound(project)
    (project / "SPEC.md").write_text(_SPEC.replace("MUST drop every", "MUST drop\n  every"), encoding="utf-8")
    assert _defects(project, approval) == []


def test_an_approval_from_before_the_digest_is_not_failed_for_lacking_it(tmp_path):
    project = _project(tmp_path)
    approval = _approval(project, review_sources(project, "FR-01"))
    assert "requirement_digest" not in approval[REVIEW_BLOCK_KEY]
    assert _defects(project, approval) == []


def test_the_block_review_fr_tests_writes_carries_the_digest(tmp_path):
    """`review-fr-tests` writes `review_block(sources)`; built from it, an
    approval is bound to the text and survives only while the text does."""
    from core.quality_gate.criteria_review import review_block

    project = _project(tmp_path)
    s = review_sources(project, "FR-01")
    approval = _approval(project, s)
    approval[REVIEW_BLOCK_KEY] = review_block(s)
    assert approval[REVIEW_BLOCK_KEY]["requirement_digest"] == s["requirement_digest"]
    assert _defects(project, approval) == []
    (project / "SPEC.md").write_text(_SPEC.replace("drop", "keep"), encoding="utf-8")
    assert _defects(project, approval)
