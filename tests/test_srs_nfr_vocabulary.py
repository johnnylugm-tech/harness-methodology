"""Round 33 站0/站3 — the vocabulary an artefact is written in is checked where
it is written, not two phases later where it is refused.

`485c05f` diagnosed this exactly right and then fixed it in the wrong layer.
Its own commit message: SRS.md's `non_functional_requirements[].type` is
"parsed and legality-checked **nowhere** upstream of generate_sab.py --validate
in Phase 2". Verified:

  * `scripts/plangen/artifact_parsers.py::_parse_srs_fr_block_json` reads only
    `functional_requirements`; it never touches `non_functional_requirements`.
  * `ALL_NFR_TYPES` has exactly one enforcement site in the tree,
    `core/quality_gate/sab_parser.py:532`, and that is the Phase 2 SAB block.

The fix it shipped is a prose bullet in Phase 1's B-checklist plus a corrected
template example. Both are improvements, and the template half is genuinely
right — it is now the full 14-value vocabulary, in `ALL_NFR_TYPES` order, with
a drift test pinning it. But the checklist half leaves the verdict with the
party being judged: an agent that does not run the check produces the same
illegal value, gets it approved in Phase 1, and finds out in Phase 2 — after
the value is locked into a peer-reviewed, verbatim-transcribe deliverable.
Observed cost on a real project: 5 B-review rounds to the HR-12 hard cap
(taskq-full SAD.md, 2026-08-03).

Two things this file also pins, both found while measuring the above:

`traceability` is scored by `harness/gate_configs/gate4_p6_full.yaml` but has
no `### traceability` section in `harness/ssi/prompts/evaluate_dimension.md` —
and evaluate_dimension.md is the file Phase 1's prompt (spec_phase1.py:451,
:503) names as the authoritative roster for the sibling `dimension:` field. So
the roster has two sources too, the prompt cites one, and the gate uses the
other.

`_parse_srs_fr_block_json` returns `{}` with no diagnostic when it cannot find
the block at all — it only warns when the JSON is malformed. Measured: none of
the five real projects use the `<!-- FR:START -->` sentinel the template ships,
and taskq-full puts its machine-readable block under
`## 10. AC ↔ Module Traceability (machine-readable)`, which matches neither
detection path. The parser silently reports zero FRs for a file that has eight.
That is the fourth silent abstention of this class (Round 30 站3 cleared three).
"""
from __future__ import annotations

import json

import pytest

pytestmark = [pytest.mark.core]

_LEGAL_SRS = {
    "functional_requirements": [
        {"id": "FR-01", "title": "t", "implementation_modules": ["a.py"]},
    ],
    "non_functional_requirements": [
        {"id": "NFR-01", "type": "performance", "dimension": "performance",
         "description": "d", "test_method": "m"},
    ],
}


def _srs_text(payload: dict) -> str:
    return (
        "# Software Requirements Specification (SRS) — fixture\n\n"
        "## 7. Appendix A\n\n"
        "<!-- FR:START -->\n```json\n"
        + json.dumps(payload, indent=2)
        + "\n```\n<!-- FR:END -->\n"
    )


def _project(tmp_path, payload: dict):
    proj = tmp_path / "proj"
    (proj / ".methodology").mkdir(parents=True)
    (proj / "01-requirements").mkdir(parents=True)
    (proj / ".methodology" / "quality_manifest.json").write_text(
        json.dumps({"fr_ids": ["FR-01"], "frs": [{"id": "FR-01"}]}), encoding="utf-8"
    )
    (proj / ".methodology" / "state.json").write_text(
        json.dumps({"state": "RUNNING", "current_phase": 1, "language": "python"}),
        encoding="utf-8",
    )
    (proj / "01-requirements" / "SRS.md").write_text(_srs_text(payload), encoding="utf-8")
    return proj


# ── the vocabulary is checked by the framework, not by the checklist ─────

def test_an_illegal_nfr_type_is_refused_by_the_framework(tmp_path):
    """`error_handling` is legal as a `dimension:` name and illegal as a
    `type:` name — precisely the semantically-plausible value 485c05f's
    commit message names as the one that sailed through Phase 1."""
    from core.quality_gate import srs_nfr_validate

    payload = json.loads(json.dumps(_LEGAL_SRS))
    payload["non_functional_requirements"][0]["type"] = "error_handling"
    proj = _project(tmp_path, payload)

    findings = srs_nfr_validate.illegal_nfr_vocabulary(proj)
    assert findings, (
        "an NFR whose `type:` is outside ALL_NFR_TYPES reached Phase 2 "
        "unchallenged; the only enforcement site is sab_parser.py:532, two "
        "phases downstream of where the value is written"
    )
    assert any("error_handling" in f for f in findings), findings


def test_a_legal_nfr_vocabulary_produces_no_findings(tmp_path):
    """The discriminating half — a checker that always fires is not a checker."""
    from core.quality_gate import srs_nfr_validate

    proj = _project(tmp_path, _LEGAL_SRS)
    assert srs_nfr_validate.illegal_nfr_vocabulary(proj) == []


def test_an_illegal_dimension_is_refused_too(tmp_path):
    """Phase 1's checklist has carried the `dimension:` legality bullet since
    before 485c05f, and it has never had a machine behind it either."""
    from core.quality_gate import srs_nfr_validate

    payload = json.loads(json.dumps(_LEGAL_SRS))
    payload["non_functional_requirements"][0]["dimension"] = "throughput"
    proj = _project(tmp_path, payload)

    findings = srs_nfr_validate.illegal_nfr_vocabulary(proj)
    assert any("throughput" in f for f in findings), findings


def test_an_srs_with_no_machine_block_is_not_an_illegal_srs(tmp_path):
    """Round 31's parse-failure rule: "could not read it" must never be
    rendered as "it says nothing", and must never BLOCK. Measured: none of the
    five real projects carry the sentinel this template ships."""
    from core.quality_gate import srs_nfr_validate

    proj = _project(tmp_path, _LEGAL_SRS)
    (proj / "01-requirements" / "SRS.md").write_text(
        "# Software Requirements Specification (SRS) — fixture\n\nprose only\n",
        encoding="utf-8",
    )
    assert srs_nfr_validate.illegal_nfr_vocabulary(proj) == []


def test_advance_phase_refuses_a_p1_exit_carrying_an_illegal_vocabulary(tmp_path):
    """Through the command, not just the helper.

    The counter-proof that mattered in Round 32: a helper-level test leaves
    the call site uncovered, and the call site is the whole point of moving
    the verdict to the framework. The legal fixture must reach a LATER check
    (the Phase Auditor's exit 8 for missing deliverables) — that is what
    proves this check is discriminating rather than universally blocking.
    """
    from cli import phase_cmds
    from cli.exit_codes import EX_ADVANCE_SRS_VOCABULARY_ILLEGAL

    bad = json.loads(json.dumps(_LEGAL_SRS))
    bad["non_functional_requirements"][0]["type"] = "error_handling"
    assert phase_cmds._advance_prechecks(_project(tmp_path / "bad", bad), 1) == (
        EX_ADVANCE_SRS_VOCABULARY_ILLEGAL
    )

    ok = _project(tmp_path / "ok", _LEGAL_SRS)
    assert phase_cmds._advance_prechecks(ok, 1) != EX_ADVANCE_SRS_VOCABULARY_ILLEGAL


# ── the roster the prompt names is not the roster the gate uses ─────────

def test_the_dimension_roster_has_one_source():
    """Measured split: `traceability` is a scored dimension in
    gate4_p6_full.yaml and has no `### traceability` section in
    evaluate_dimension.md — the file spec_phase1.py:451 tells the agent to
    grep for "the current roster". An NFR mapped to it would be flagged by
    Phase 1's own checklist as naming a nonexistent dimension."""
    from core.quality_gate import srs_nfr_validate

    roster = srs_nfr_validate.dimension_roster()
    assert "traceability" in roster, (
        "the framework scores `traceability` but the roster the Phase 1 prompt "
        "cites does not list it; a roster that omits a scored dimension turns "
        "a correct NFR into a checklist violation"
    )
    # Every dimension any gate config scores must be in the roster, or the
    # roster is not the roster.
    assert srs_nfr_validate.dimension_roster_split() == {"traceability"}, (
        "the set of dimensions known to the gate configs but absent from "
        "evaluate_dimension.md changed; that divergence is now deliberate or "
        "it is a new drift — decide which, in the commit that changes it"
    )


# ── a block that could not be found is not a block that is empty ────────

def test_an_unfindable_fr_block_is_reported_not_silently_empty(capsys):
    """`_parse_srs_fr_block_json` warns on malformed JSON and says nothing at
    all when neither detection path matches. Measured on taskq-full: eight FRs
    in the file, zero returned, no output."""
    from scripts.plangen.artifact_parsers import _parse_srs_fr_block_json

    content = (
        "# Software Requirements Specification (SRS) — fixture\n\n"
        "## 10. AC ↔ Module Traceability (machine-readable)\n\n"
        "```json\n" + json.dumps(_LEGAL_SRS, indent=2) + "\n```\n"
    )
    result = _parse_srs_fr_block_json(content)
    captured = capsys.readouterr()
    assert result or captured.err.strip() or captured.out.strip(), (
        "the parser found no machine-readable block and returned {} without "
        "saying so; every consumer reads that as 'this SRS declares no FR "
        "metadata' (measured: taskq-full has 8 FRs and gets 0, silently)"
    )
