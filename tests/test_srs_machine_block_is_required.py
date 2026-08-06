"""Round 42 站0/站3 — a required deliverable section that is only ever warned about.

`templates/SRS.md:78` ships the section `## 7. FR Block (machine-readable)`.
`docs/P1_SOP.md:23` tells the agent to fill it and `:58` lists it on the Phase
1 exit checklist. `scripts/plangen/artifact_parsers.srs_machine_block` parses
it — and when it is absent prints

    [srs] WARNING: no machine-readable requirements block found — ...

and carries on. Measured across every SRS on disk: taskq and taskq-renew carry
the block, **taskq-plus and taskq-api do not**, and both passed Phase 1.

taskq-plus's requirements artifacts scored 98 while missing it; taskq-renew
wrote it and, until 站1, was charged an invented requirement for the heading it
needs. The framework fined the project that complied.

Round 30's rule is that abstaining is not passing, and Round 24's is that a
block which does not say what to do is half a block. A required section whose
absence produces one line on stdout is neither.

Scope is deliberately "present and parseable". Whether its JSON agrees with the
prose is `check-artifact-consistency`'s question, already answered there.

站0 wrote this file against the wrong contract — sentinel pairs and a
`{"requirements": ...}` key — and 站1 measured that neither is the rule:
`srs_machine_block` finds the block by CONTENT, a fenced JSON object carrying
`functional_requirements`, precisely because both heading- and sentinel-based
detection were tried on a live file and both missed it. The fixtures below use
the real key. A test written against a contract the framework does not have
tests nothing, which is the same error this round is about.
"""

from __future__ import annotations

from pathlib import Path

from core.quality_gate import srs_structure


_ANCHOR = "# Software Requirements Specification\n"

_BODY = """
## 3. Functional Requirements

### FR-01: task submission

The submitted command is validated before anything is written.
"""

_BLOCK = """
## 7. FR Block (machine-readable)

<!-- FR:START -->
```json
{"functional_requirements": [
  {"id": "FR-01", "title": "task submission",
   "implementation_modules": ["taskq_plus/cli/commands.py"],
   "acceptance_criteria": ["submit \\"echo hi\\" exits 0"]}
]}
```
<!-- FR:END -->
"""

# taskq-plus's actual shape: requirements in prose, no machine-readable block.
_TAIL_WITHOUT_BLOCK = """
## 7. Open Issues

None.
"""


def _project(tmp_path: Path, srs: "str | None") -> Path:
    if srs is not None:
        (tmp_path / "01-requirements").mkdir(parents=True)
        (tmp_path / "01-requirements" / "SRS.md").write_text(srs, encoding="utf-8")
    return tmp_path


def test_an_srs_without_its_fr_block_is_a_violation(tmp_path: Path):
    """The taskq-plus shape must be reported, not warned about."""
    violations = srs_structure.check_srs_structure(
        _project(tmp_path, _ANCHOR + _BODY + _TAIL_WITHOUT_BLOCK)
    )
    assert violations, "an SRS with no machine-readable FR Block reported nothing"
    assert violations[0].rule_id == srs_structure.RULE_MISSING_FR_BLOCK
    assert violations[0].severity == "error"
    message = violations[0].message
    assert "functional_requirements" in message
    assert "templates/SRS.md" in message, (
        "a block that does not say where the required shape lives is half a "
        "block (Round 24)"
    )


def test_an_srs_with_its_fr_block_is_clean(tmp_path: Path):
    """Positive control — the taskq-renew shape must pass."""
    assert srs_structure.check_srs_structure(
        _project(tmp_path, _ANCHOR + _BODY + _BLOCK)
    ) == []


def test_a_project_with_no_srs_at_all_is_not_this_check_s_business(tmp_path: Path):
    """Jurisdiction: only a project that has an SRS can have a malformed one.

    Round 40 站1 made the same mistake in the other direction — a check that
    fired on the golden-path fixture because the artifact it audits was absent
    rather than wrong.
    """
    assert srs_structure.check_srs_structure(_project(tmp_path, None)) == []


def test_an_srs_declaring_no_functional_requirements_is_out_of_jurisdiction(
    tmp_path: Path,
):
    """No FRs, no FR metadata for a block to carry.

    Found by this check firing on `test_preflight_nfr_coverage_only_checked_
    from_p3`'s NFR-only stub — the same over-reach in the same shape as Round
    40 站1's. taskq-plus's SRS has eight `### FR-NN` sections and no block, so
    the narrowing costs nothing the check exists for.
    """
    assert srs_structure.check_srs_structure(
        _project(tmp_path, _ANCHOR + "\n### NFR-01\n### NFR-06\n")
    ) == []


def test_the_block_is_found_without_any_sentinel(tmp_path: Path):
    """Sentinels are decoration; the key is the contract.

    `srs_machine_block`'s docstring records a live SRS with 8 FRs and 12 NFRs
    and no sentinels anywhere, which every heading- or sentinel-based path
    missed. A check that required the comments would fail that project for
    formatting.
    """
    no_sentinels = _BLOCK.replace("<!-- FR:START -->\n", "").replace(
        "<!-- FR:END -->\n", ""
    )
    assert srs_structure.check_srs_structure(
        _project(tmp_path, _ANCHOR + _BODY + no_sentinels)
    ) == []
