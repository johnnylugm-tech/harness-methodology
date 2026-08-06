"""Round 42 站0 — a required deliverable section that is only ever warned about.

`templates/SRS.md:78` ships the section `## 7. FR Block (machine-readable)`.
`docs/P1_SOP.md:23` tells the agent to fill it and `:58` lists it on the Phase 1
exit checklist. `scripts/plangen/artifact_parsers.py` parses it — and when it
is absent prints

    [srs] WARNING: no machine-readable requirements block found — no ...

and carries on.

taskq-plus's SRS has no such block: zero occurrences of `FR:START` or
`JSON:START`, and its §7 is "Open Issues". Phase 1 passed; the project scored
98 on its requirements artifacts. taskq-renew wrote the block and was charged
an invented requirement for it (see `test_canonical_diff_phantom_ac.py`).
Between the two, the framework paid the project that skipped a required
deliverable and fined the one that produced it.

Round 30's rule is that abstaining is not passing, and Round 24's is that a
block which does not say what to do is half a block. A required section whose
absence produces one line on stdout is neither: it is a requirement in the
template and an option in the pipeline.

Scope is deliberately "the block is present and parseable". Whether its JSON
agrees with the prose is `check-artifact-consistency`'s question, already
answered elsewhere.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from core.quality_gate import srs_structure


_ANCHOR = "# Software Requirements Specification\n"

_WITH_BLOCK = _ANCHOR + """
## 3. Functional Requirements

### FR-01: task submission

The submitted command is validated before anything is written.

## 7. FR Block (machine-readable)

<!-- FR:START -->
```json
{"requirements": [{"id": "FR-01", "title": "task submission"}]}
```
<!-- FR:END -->
"""

# taskq-plus's actual shape: requirements in prose, no machine-readable block.
_WITHOUT_BLOCK = _ANCHOR + """
## 3. Functional Requirements

### FR-01: task submission

The submitted command is validated before anything is written.

## 7. Open Issues

None.
"""


def _project(tmp_path: Path, srs: str | None) -> Path:
    if srs is not None:
        (tmp_path / "01-requirements").mkdir(parents=True)
        (tmp_path / "01-requirements" / "SRS.md").write_text(srs, encoding="utf-8")
    return tmp_path


def test_an_srs_without_its_fr_block_is_a_violation(tmp_path: Path):
    """The taskq-plus shape must be reported, not warned about."""
    violations = srs_structure.check_srs_structure(_project(tmp_path, _WITHOUT_BLOCK))
    assert violations, "an SRS with no machine-readable FR Block reported nothing"
    message = " ".join(v.message for v in violations)
    assert "FR Block" in message
    assert "templates/SRS.md" in message, (
        "a block that does not say where the required shape lives is half a "
        "block (Round 24)"
    )


def test_an_srs_with_its_fr_block_is_clean(tmp_path: Path):
    """Positive control — the taskq-renew shape must pass."""
    assert srs_structure.check_srs_structure(_project(tmp_path, _WITH_BLOCK)) == []


def test_a_project_with_no_srs_at_all_is_not_this_check_s_business(tmp_path: Path):
    """Jurisdiction: only a project that has an SRS can have a malformed one.

    Round 40 站1 made the same mistake in the other direction — a check that
    fired on the golden-path fixture because the artifact it audits was absent
    rather than wrong.
    """
    assert srs_structure.check_srs_structure(_project(tmp_path, None)) == []


@pytest.mark.parametrize("sentinel", ["FR:START", "JSON:START"])
def test_either_sentinel_pair_satisfies_the_check(tmp_path: Path, sentinel: str):
    """Both spellings on disk are accepted.

    `artifact_parsers.py` reads `<!-- FR:START -->` and `<!-- JSON:START -->`;
    taskq-renew shipped the JSON spelling. A check that knew only one of them
    would fail the project that complied.
    """
    srs = _WITH_BLOCK.replace("FR:START", sentinel).replace("FR:END", sentinel.replace("START", "END"))
    assert srs_structure.check_srs_structure(_project(tmp_path, srs)) == []
