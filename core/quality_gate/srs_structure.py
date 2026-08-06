"""The SRS's machine-readable requirements block is required, not encouraged.

Round 42 站3. `templates/SRS.md:78` ships the section
`## 7. FR Block (machine-readable)`. `docs/P1_SOP.md:23` tells the agent to
fill it and `:58` puts it on the Phase 1 exit checklist.
`scripts/plangen/artifact_parsers.srs_machine_block` parses it — and when it
is absent prints

    [srs] WARNING: no machine-readable requirements block found — no fenced
    JSON object in this SRS carries a `functional_requirements` key.
    Downstream consumers will see this SRS as declaring no FR metadata.

and carries on. Measured across every SRS on disk: taskq and taskq-renew have
the block; **taskq-plus and taskq-api do not**. Both passed Phase 1. Both are
read downstream as declaring no FR metadata, which is what that warning says
and nothing acts on.

The asymmetry is what makes it structural rather than cosmetic. taskq-renew
wrote the block and, until Round 42 站1, was charged an invented requirement
for the heading it needs (`## FR Block (machine-readable)` matched the
acceptance-clause splitter). taskq-plus omitted the deliverable and paid
nothing. Between the two, the framework fined the project that complied.

Round 30's rule is that abstaining is not passing; Round 24's is that a block
which does not say what to do is half a block. A required section whose
absence produces one line on stdout is a requirement in the template and an
option in the pipeline.

WHAT THIS CHECKS, AND WHAT IT DOES NOT
--------------------------------------
Present and parseable, by `srs_machine_block`'s rule — a fenced JSON object
carrying `functional_requirements`, found by CONTENT rather than by sentinel
or heading. That module's docstring records why heading- and sentinel-based
detection were both tried and both missed a live file; a second detection rule
here would be the same mistake a third time.

Whether the block's JSON agrees with the prose around it is
`check-artifact-consistency`'s question and is already answered there.

Jurisdiction: a project with no SRS is not a project with a malformed one, and
neither is an SRS that declares no functional requirements — there is no FR
metadata for the block to carry. Round 40 站1 made the same mistake in the
other direction: a check that fired on a fixture because the artifact it
audits was absent rather than wrong. This one found its own instance the same
way, on `test_preflight_nfr_coverage_only_checked_from_p3`'s NFR-only stub.
taskq-plus's SRS has eight `### FR-NN` sections and no block, so the narrowing
costs nothing that matters.
"""

from __future__ import annotations

import re
from pathlib import Path

from core.quality_gate import Violation
from core.utils.project_layout import ProjectLayout

__all__ = ["check_srs_structure", "RULE_MISSING_FR_BLOCK"]

RULE_MISSING_FR_BLOCK = "SRS-FR-BLOCK"

# An SRS that declares functional requirements. The same `### FR-NN` heading
# shape `scripts/canonical_diff` and `spec_coverage` read, and the same one
# templates/SRS.md renders.
_FR_SECTION_RE = re.compile(r"^#{2,6}\s+FR-\d+\b", re.MULTILINE)


def check_srs_structure(project: "str | Path") -> list[Violation]:
    """Violations for the SRS's required machine-readable block.

    Empty list when the SRS carries the block, when there is no SRS, and when
    the SRS declares no functional requirements for a block to carry.
    """
    srs_path = ProjectLayout(Path(project)).srs_path
    if not srs_path.is_file():
        return []

    from scripts.plangen.artifact_parsers import srs_machine_block

    text = srs_path.read_text(encoding="utf-8", errors="replace")
    if not _FR_SECTION_RE.search(text):
        return []
    if srs_machine_block(text) is not None:
        return []

    return [Violation(
        check_type="srs_structure",
        rule_id=RULE_MISSING_FR_BLOCK,
        message=(
            "SRS.md has no machine-readable FR Block: no fenced JSON object "
            "in it carries a `functional_requirements` key, so every "
            "downstream consumer reads this SRS as declaring no FR metadata. "
            "Fix: fill the block templates/SRS.md ships as "
            "`## 7. FR Block (machine-readable)` (docs/P1_SOP.md step 4), "
            "then re-run."
        ),
        file=str(srs_path),
        severity="error",
    )]
