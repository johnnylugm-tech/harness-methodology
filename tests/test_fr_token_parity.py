r"""FR-token phantom-match parity: no FR extractor may match inside an NFR id.

Round 8 station 1. Commit 90e35b2 (2026-07-11 clean-rerun bug 1) fixed the
NFR-substring bug in exactly the two sites the rerun surfaced —
DriftDetector.SAD_FR_PATTERN and _CoverageScorer.FR_PATTERN — by adding a
`(?<!N)` lookbehind. The sibling patterns were never swept; this round's
adversarial-corpus sweep of all 30+ FR-regex sites found three more with the
identical failure mode:

  core.traceability.scanner.SAD_ROW_PATTERN
      `| NFR-06 | ... | ``config.py`` |` parsed as "FR-06 owns config.py",
      feeding a phantom mapping into TRACEABILITY_MATRIX / attestation.
  core.traceability.spec_tracking_render._FR_CELL
      an NFR row in SPEC_TRACKING's Specification Status table is treated as
      the same-numbered FR: its Status cell gets overwritten with the FR's
      status and the genuine FR row is suppressed from the append step.
  core.quality_gate.spec_alignment._FR_DEFERRED
      `NFR-06-deferred` phantom-excuses FR-06 from the canonical-spec
      front-edge coverage check.

The parity suite imports the REAL compiled pattern objects (not re-declared
copies) and asserts none of them produces a match on a corpus whose only
requirement-id-like token is an NFR. Any future weakening of any registered
pattern trips this immediately.

Registry maintenance rule: module-level or class-level compiled patterns
whose purpose is extracting FR ids from prose/markdown belong here. Inline
(non-constant) regexes are outside the registry and were verified manually
this round: cross_artifact.py:140 `(?<![A-Za-z])FR-`, spec_coverage.py
`^#{2,3}\s+(?:SRS_SUBSECTION_PREFIX)?(FR-...)` and `\bFR-\d+\b`,
phase_cmds.py:1937 `\bFR-\d+\b`, phase_hooks.py `\bFR-(\d+)\b` /
`^#{1,6}\s*(?:SRS_SUBSECTION_PREFIX)?FR-` — all anchored or
boundary-protected (there is no word boundary between `N` and `F`, so
`\bFR` cannot match inside `NFR`; the optional SRS_SUBSECTION_PREFIX
(2026-07-14, core.quality_gate.parsers.fr_id_pattern) only ever consumes a
leading digit sequence, never an `N`, so it cannot open a path into `NFR-`
either).
"""
from __future__ import annotations

import pytest

from core.quality_gate import spec_alignment
from core.traceability import scanner, spec_tracking_render
from detection.drift_detector import DriftDetector
from detection.ensemble_scorer import _CoverageScorer


pytestmark = [pytest.mark.core]


# Every string's only requirement-id-like token is an NFR: a correct FR
# extractor must find nothing in any of them.
NFR_ONLY_CORPUS = [
    "NFR-06",
    "| NFR-06 | reliability | `config.py` mapping |",
    "### NFR-06: Reliability",
    "[NFR-06]",
    "see NFR-06 for details",
    '"id": "NFR-06"',
    "NFR-06-deferred",
]

# (label, compiled pattern) — real objects imported from production modules.
FR_PATTERN_REGISTRY = [
    ("scanner.FR_TAG_PATTERN", scanner.FR_TAG_PATTERN),
    ("scanner.FR_SAD_PATTERN", scanner.FR_SAD_PATTERN),
    ("scanner.SAD_ROW_PATTERN", scanner.SAD_ROW_PATTERN),
    ("spec_tracking_render._FR_CELL", spec_tracking_render._FR_CELL),
    ("DriftDetector.FR_PATTERN", DriftDetector.FR_PATTERN),
    ("DriftDetector.SAD_FR_PATTERN", DriftDetector.SAD_FR_PATTERN),
    ("_CoverageScorer.FR_PATTERN", _CoverageScorer.FR_PATTERN),
    ("spec_alignment._FR_HEADING", spec_alignment._FR_HEADING),
    ("spec_alignment._FR_TABLE", spec_alignment._FR_TABLE),
    ("spec_alignment._FR_JSON", spec_alignment._FR_JSON),
    # Round 69 站5 split `_FR_DEFERRED` into the three structural forms the
    # corpus writes. Each is registered separately so the (?<!N) lookbehind is
    # proved on every one of them, not on whichever the tuple happens to
    # start with.
    *(
        (f"spec_alignment._FR_DEFERRED_FORMS[{i}]", pat)
        for i, pat in enumerate(spec_alignment._FR_DEFERRED_FORMS)
    ),
]


@pytest.mark.parametrize(
    "label,pattern", FR_PATTERN_REGISTRY, ids=[label for label, _ in FR_PATTERN_REGISTRY]
)
def test_fr_pattern_never_matches_inside_nfr(label, pattern):
    phantoms = {s: pattern.findall(s) for s in NFR_ONLY_CORPUS if pattern.findall(s)}
    assert not phantoms, (
        f"{label} produced phantom FR matches on NFR-only input — the same "
        f"substring bug 90e35b2 fixed in drift_detector/ensemble_scorer: {phantoms}"
    )


def test_fr_patterns_still_match_genuine_frs():
    """The lookbehind must not weaken genuine FR extraction (anti-over-fix)."""
    assert scanner.SAD_ROW_PATTERN.findall(
        "| FR-01 | `store.py` handles persistence |"
    ) == [("01", "store.py")]
    assert spec_tracking_render._FR_CELL.findall("FR-03") == ["03"]
    # Round 69 站5: a deferral must be structural, so the genuine-match proof
    # uses the three shapes the corpus writes rather than a bare token.
    assert spec_alignment._deferred_fr_ids("### FR-07-deferred: out of scope\n") == {"FR-07"}
    assert spec_alignment._deferred_fr_ids("| FR-07-deferred | none |\n") == {"FR-07"}
    assert spec_alignment._deferred_fr_ids("- **FR-07-deferred** — out of scope\n") == {"FR-07"}


# ---------------------------------------------------------------------------
# Function-level proof on the real consumption paths of the two primary sites
# ---------------------------------------------------------------------------

def test_scan_sad_fr_modules_ignores_nfr_rows(tmp_path):
    """An NFR table row citing a file must not become an FR→module mapping."""
    sad = tmp_path / "SAD.md"
    sad.write_text(
        "# SAD\n"
        "| ID | Dimension | Modules |\n"
        "|---|---|---|\n"
        "| FR-01 | functional | `store.py` |\n"
        "| NFR-06 | reliability | `config.py` retry defaults |\n",
        encoding="utf-8",
    )
    mappings = scanner.scan_sad_fr_modules(sad)
    assert mappings == {"FR-01": ["store.py"]}, (
        f"NFR-06 row leaked into FR mappings: {mappings}"
    )


def test_refresh_status_table_leaves_nfr_rows_alone():
    """An NFR row must not be hijacked by the same-numbered FR's status, and
    the genuine FR row must still be appended."""
    markdown = (
        "| FR ID | Description | Status |\n"
        "|---|---|---|\n"
        "| NFR-03 | p95 latency budget | TRACKED |\n"
    )
    new, changed = spec_tracking_render.refresh_status_table(
        markdown, {"FR-03": "COMPLETE"}
    )
    assert "| NFR-03 | p95 latency budget | TRACKED |" in new, (
        "NFR-03 row's Status was overwritten by FR-03's status"
    )
    assert changed and "FR-03" in new.replace("NFR-03", ""), (
        "genuine FR-03 row was suppressed instead of appended"
    )


def test_deferred_excuse_requires_a_real_fr_marker():
    """`NFR-06-deferred` must not excuse FR-06 from front-edge coverage.

    Round 69 站5 added a second requirement on top of the lookbehind: the
    deferral must be STRUCTURAL. A bare token in a sentence excuses nothing
    now, in either namespace, so the FR side is asserted through a heading.
    """
    assert spec_alignment._deferred_fr_ids("### NFR-06-deferred: x\n") == set()
    assert spec_alignment._deferred_fr_ids("FR-06-deferred") == set()
    assert spec_alignment._deferred_fr_ids("### FR-06-deferred: x\n") == {"FR-06"}
