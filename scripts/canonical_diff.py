#!/usr/bin/env python3
"""canonical_diff.py — word-level diff between a deliverable (e.g. SRS.md) and
the canonical spec (e.g. SPEC.md). Outputs an over_spec_score per AC.

Root cause (Bug D of 5-point plan): Canonical Interpretation Rule +
No-Prescription Rule + DERIVED tag lived only as prompt-level text in
generate_full_plan.py:572. B-2 reviewer was the only thing standing between
A and the framework, but B's grading was free-form text — over-interpretation
could be mis-classified as high severity (Bug B fix) OR pass unnoticed when B
returned "all low gaps". No framework-side diff existed to detect A adding
content not derivable from SPEC.

This module provides:
  - `compute_over_spec_score(ac_text, canonical_units)` → dict
        over_spec_score 0.0 = every token of the AC is in one canonical
                              block; 1.0 = none of them are
        The block is what `_split_canonical_units` produced, and on every
        specification measured that is the whole document — see that
        function.
        +0.3 penalty if AC contains interpretive choices without DERIVED tag
        verdict ∈ VERDICTS, plus the project's citation and whether the
                              location it names is in the canonical text
  - `build_diff_report(srs_path, spec_path, mode)` → dict
        Parses SRS into AC clauses, SPEC into sentences, scores each AC.
        Returns a structured report with per-AC records.
  - `write_report(report, out_path)` → Path
        Writes JSON report to disk for downstream B reviewer + framework.

The script can also run as a CLI:
    python3 canonical_diff.py --srs 01-requirements/SRS.md --spec SPEC.md \\
                              --out srs_vs_spec_diff.json

If SPEC.md is missing (Elicitation mode), the script prints a warning and
exits 0 — generate_full_plan.py §B-2 attach uses try/except so absence is
non-blocking.

Commonality: phase-agnostic. Designed for SRS↔SPEC but the same engine applies
to TESTSPEC↔SRS (P4), VERIFICATION↔SRS (P5), etc. via `--mode` argument.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.quality_gate import artifact_consistency  # noqa: E402
from core.quality_gate.parsers.nfr_id_pattern import (  # noqa: E402
    normalize_nfr_id,
)
from core.quality_gate.spec_alignment import (  # noqa: E402
    spec_config_keys, structural_fr_ids,
)
from core.traceability.scanner import NFR_PATTERN  # noqa: E402
from scripts.plangen.artifact_parsers import srs_machine_block_span  # noqa: E402


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Words ignored in similarity computation (English stopwords + spec-typical)
_STOPWORDS: frozenset[str] = frozenset(
    """a an the and or of in on at to for with by from as is are be been
    being has have had do does did will would shall should may might can
    could must this that these those it its their there here what which who
    whom whose if then else when where why how not no nor so than too very
    s t re ve ll just don don' """
    .split()
)

# Pattern matching an FR/NFR/AC header line in SRS.md (e.g. "### FR-01",
# "#### AC1", "### NFR-02 — Performance")
#
# Round 42 站1: the label must carry a number. `[-\w]*` used to match zero
# characters, so any heading whose first word merely STARTED with FR, NFR or
# AC became a requirement — and the one heading in the corpus that does is
# `## FR Block (machine-readable)`, which templates/SRS.md:78 and
# docs/P1_SOP.md:23 require the agent to write. taskq-renew wrote it and was
# charged `invention_count: 1` with `{"label": "FR", "fr_id": "FR"}`;
# taskq-plus never wrote the block and scored 0 inventions. Across every SRS
# on disk (taskq 69 matched headings, taskq-plus 20, taskq-renew 21,
# taskq-api 22) that is the only match with no digit in its label.
_FR_HEADER_RE = re.compile(
    r"^(#{1,6})\s+(?P<label>(?:FR|NFR|AC)[-\w]*\d[-\w]*)\b[^\n]*$",
    re.MULTILINE,
)

# Pattern matching a DERIVED tag (anywhere in an AC block)
_DERIVED_TAG_RE = re.compile(
    r"DERIVED:\s*[^\n]+",
    re.IGNORECASE,
)

# Pattern for splitting SPEC.md into sentences
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z\d])")

#: The four things this report can say about one clause. Each is named after
#: what was MEASURED or DECLARED, not after an intent (Round 106 站B).
#:
#:   transcribed           the framework measured a token overlap ≥ 0.85
#:   overlaps_canonical    the framework measured a token overlap ≥ 0.45
#:   cites_canonical       the PROJECT wrote a `DERIVED:` tag; the overlap did
#:                         not reach either threshold
#:   unmatched_and_uncited neither happened — an absence, not an invention
#:
#: `invention` was the old fourth name. It is a claim about the author's
#: intent that this file cannot support: 0.45 of token overlap is what it
#: measures, and Round 105 measured that 378 of 479 `interpreted` verdicts
#: were the third row above — a self-declaration reported in the same word as
#: a measurement. The counts in `summary` are derived from this tuple so the
#: report has one vocabulary and not two.
VERDICTS: tuple[str, ...] = (
    "transcribed", "overlaps_canonical", "cites_canonical",
    "unmatched_and_uncited",
)

#: The citation half of a `DERIVED: <canonical-line> — <rationale>` tag ends
#: at the em/en dash the Phase 1 prompt puts between the two. Without the
#: split, an id named in the RATIONALE ("…deferred to NFR-99") is read as a
#: citation target and fails to resolve — 42 of 412 corpus citations, every
#: one of them a false accusation.
_RATIONALE_SEPARATOR = re.compile(r"\s+[—–]\s+")
_CITED_SECTION = re.compile(r"§\s*(\d+(?:\.\d+)*)")
_CITED_REQUIREMENT = re.compile(r"\b(N?FR)-(\d+)\b", re.IGNORECASE)
_CITED_LINES = re.compile(r"\blines?\s+(\d+)(?:\s*[-–—]\s*(\d+))?", re.IGNORECASE)
_HEADING_LINE = re.compile(r"^\s*#")


# ---------------------------------------------------------------------------
# Text normalization
# ---------------------------------------------------------------------------


def _tokenize(text: str) -> list[str]:
    """Lowercase, strip punctuation, drop stopwords and very short tokens."""
    text = text.lower()
    text = re.sub(r"[^\w\s\-]", " ", text)
    tokens = text.split()
    return [t for t in tokens if t not in _STOPWORDS and len(t) >= 3]


def _split_canonical_units(text: str) -> list[str]:
    """The blocks an AC is matched against. On a specification: one block.

    Round 108 站B renamed this from `_split_sentences`. It has never returned
    a sentence. `_SENTENCE_SPLIT_RE` needs a full stop, whitespace and then an
    ASCII capital or digit; a specification writes one claim per bullet or
    table cell, so the character after a full stop is a newline and then `-`,
    `|` or `#`. Measured 2026-09-08 over every `SPEC.md` on this machine —
    eighteen projects:

        units == 1 : 3      units == 2 : 15      units > 2 : 0
        largest unit 8,310 chars; the smallest project, taskq (11,434 chars),
        yields ONE unit of 4,328
        regex hits on the RAW text of each: 0

    Where it does fire is not a rescue: on an English-headed document it
    fires at `## 1.`, `## 2.` — the section NUMBERING — so the unit is a
    SECTION. Either way the block spans many claims, which is why four
    shipped statements calling it a sentence were wrong; one of them was the
    Phase 1 prompt Agent B reads on every round.

    It is left as it is, deliberately. A document-sized unit RAISES overlap
    ratios — the AC is compared against the whole spec at once — so this is
    not a leniency that lets inventions through. Tightening it to markdown
    block units was measured across fifteen projects: thirteen medians fell
    (taskq-api 0.497 → 0.153, taskq-forever 0.441 → 0.178) and four projects
    dropped to zero clauses above the 0.45 threshold. That is a corpus-wide
    false accusation, and it is the second measurement in two rounds to veto
    the same change (Round 105 measured AC-against-its-own-cited-line at a
    median of 0.074). `build_diff_report` publishes `canonical_units` and
    `canonical_unit_max_chars` so the degeneracy is a number in the report
    rather than a fact only this docstring knows.
    """
    # Strip code fences first (they're not prose claims)
    text = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)
    text = re.sub(r"`[^`]+`", " ", text)
    raw = _SENTENCE_SPLIT_RE.split(text)
    return [s.strip() for s in raw if len(s.strip()) > 15]


def _without_machine_block(srs_text: str) -> str:
    """The SRS minus its machine-readable requirements block.

    Round 42 站1. Excluding the block's HEADING is not enough: clause bodies
    run from one heading match to the next, so a heading that stops being a
    boundary hands its section to the clause before it. Measured on
    taskq-renew's SRS with the tightened pattern and nothing else, NFR-12's
    body went from 9,773 to 13,960 characters — it absorbed the JSON — and the
    last requirement in every SRS would have been scored against it.

    The block is located by `scripts/plangen/artifact_parsers`, which finds it
    by content (a fenced JSON object carrying `functional_requirements`)
    rather than by sentinel or title. That module's docstring records why:
    both heading-based paths were tried and both missed a live file. A second
    detection rule here would be the same mistake a third time.

    No block, or an ambiguous one, leaves the text untouched — this function
    removes what it can positively identify and never guesses.
    """
    try:
        from scripts.plangen.artifact_parsers import srs_machine_block_span
    except ImportError:
        # canonical_diff is also run as a bare script from scripts/; without
        # the package on the path the block simply stays in, which is the
        # pre-Round-42 behaviour rather than a crash.
        return srs_text
    span = srs_machine_block_span(srs_text)
    if span is None:
        return srs_text
    start, end = span
    return srs_text[:start] + srs_text[end:]


def _split_ac_clauses(srs_text: str) -> list[dict]:
    """Parse SRS.md into AC clause records.

    Each FR/NFR/AC heading starts a new block; the block runs until the next
    heading of same or higher level. Returns list of:
        {label, fr_id, body, derived_present}
    """
    srs_text = _without_machine_block(srs_text)
    matches = list(_FR_HEADER_RE.finditer(srs_text))
    clauses: list[dict] = []
    for i, m in enumerate(matches):
        label = m.group("label")
        body_start = m.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(srs_text)
        body = srs_text[body_start:body_end].strip()

        # Map AC-X back to parent FR/NFR (if label starts with AC)
        parent_id = None
        if label.upper().startswith("AC"):
            # Walk backward through matches to find the most recent FR/NFR header
            for j in range(i - 1, -1, -1):
                prev = matches[j].group("label").upper()
                if prev.startswith(("FR", "NFR")):
                    parent_id = matches[j].group("label")
                    break

        # Drop leading list-bullet whitespace, keep prose
        clauses.append({
            "label": label,
            "fr_id": parent_id or label,
            "body": body,
            "derived_present": bool(_DERIVED_TAG_RE.search(body)),
        })
    return clauses


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def _best_match_ratio(ac_text: str, canonical_units: list[str]) -> float:
    """Return best AC-coverage ratio against any canonical block.

    Definition: ratio = |AC_tokens ∩ canonical_block_tokens| / |AC_tokens|.
    This answers 'what fraction of the AC's claims are backed by canonical
    block X?'. A verbatim transcription of canonical yields ratio = 1.0;
    a pure invention (zero token overlap) yields 0.0.

    The block is whatever `_split_canonical_units` returned — on every
    specification measured, one block holding the whole document, which is
    why this is a coverage reading and not a per-sentence attribution.

    We deliberately use |AC| as denominator (not max/|union|) because the
    anti-over-spec goal is to detect A adding content NOT in canonical — a
    short verbatim AC matching a longer canonical block is fine; the
    denominator choice does not penalize that.
    """
    if not ac_text.strip() or not canonical_units:
        return 0.0

    ac_tokens = set(_tokenize(ac_text))
    if not ac_tokens:
        return 0.0

    best = 0.0
    for unit in canonical_units:
        unit_tokens = set(_tokenize(unit))
        if not unit_tokens:
            continue
        intersection = len(ac_tokens & unit_tokens)
        r = intersection / len(ac_tokens)
        if r > best:
            best = r
    return best


def citation_in(ac_text: str) -> str | None:
    """The `DERIVED:` tag this clause carries, verbatim, or None.

    Round 106 站B. The tag is what the PROJECT declared about where the
    clause comes from, and until now the report reduced it to a boolean the
    verdict then absorbed. Agent B is asked to check A's derivation; it can
    only do that if it is shown the citation.
    """
    m = _DERIVED_TAG_RE.search(ac_text)
    return m.group(0).strip() if m else None


def _cited_locations(
    citation: str,
) -> tuple[list[str], list[str], list[tuple[int, int]]]:
    """`(sections, requirement_ids, line_ranges)` the citation names.

    Only the citation half of `DERIVED: <canonical-line> — <rationale>` is
    read. The rationale is prose about the clause and names ids that are not
    citations: "…the rest deferred to NFR-99" made 42 of 412 corpus citations
    look unresolvable when the whole line was scanned.
    """
    head = _RATIONALE_SEPARATOR.split(citation.split(":", 1)[-1], 1)[0]
    sections = [m.group(1) for m in _CITED_SECTION.finditer(head)]
    requirements = [
        f"{m.group(1).upper()}-{int(m.group(2)):02d}"
        for m in _CITED_REQUIREMENT.finditer(head)
    ]
    line_ranges = [
        (int(m.group(1)), int(m.group(2) or m.group(1)))
        for m in _CITED_LINES.finditer(head)
    ]
    return sections, requirements, line_ranges


def citation_resolves_in(citation: str | None, canonical_text: str) -> bool | None:
    """Does every location the citation names exist in the canonical text?

    True / False / **None**, and the None is the point: no tag, no canonical
    text to look in, or a citation that names no location this framework can
    resolve are three ways of not having measured — and a framework that
    reports an unmeasured citation as False accuses the project of a broken
    reference it never checked (Round 32/35, Round 46).

    Requirement ids are resolved through the readers that already answer
    "which requirements does this document declare" — `structural_fr_ids` and
    the shared `normalize_nfr_id` / `NFR_PATTERN` pair. A second regex here
    is how `NFR-1` and `NFR-01` come to be different requirements: written
    naively it charged taskq-new with 25 unresolvable citations, every one of
    them pointing at a section of SPEC.md that is really there.
    """
    if not citation or not canonical_text.strip():
        return None
    sections, requirements, line_ranges = _cited_locations(citation)
    if not (sections or requirements or line_ranges):
        return None

    lines = canonical_text.splitlines()
    headings = [ln.strip() for ln in lines if _HEADING_LINE.match(ln)]
    fr_ids = structural_fr_ids(canonical_text)
    nfr_ids = {
        nid for nid in (normalize_nfr_id(f"NFR-{m.group(1)}")
                        for m in NFR_PATTERN.finditer(canonical_text))
        if nid
    }

    for section in sections:
        pattern = rf"#+\s*§?\s*{re.escape(section)}[.\s:)]"
        if not any(re.match(pattern, h) for h in headings):
            return False
    for requirement in requirements:
        known = nfr_ids if requirement.startswith("NFR") else fr_ids
        if requirement not in known:
            return False
    for first, last in line_ranges:
        if not 1 <= first <= len(lines) or not 1 <= last <= len(lines):
            return False
    return True


def compute_over_spec_score(
    ac_text: str,
    canonical_units: list[str],
    derived_present: bool = False,
    canonical_text: str = "",
) -> dict:
    """Score a single AC against the canonical spec.

    Returns dict {over_spec_score, best_match_ratio, derived_present, verdict,
    citation, citation_resolves} where verdict ∈ `VERDICTS`.

    Round 106 站B renames the verdicts to what each one is a reading OF, and
    retires `verdict_basis` — the field Round 105 added one round ago to say
    which half of `interpreted` had fired. Two fields naming one event is the
    defect this round is closing, so the split moves into the vocabulary:
    `overlaps_canonical` is the framework's measurement and `cites_canonical`
    is the project's declaration, and neither needs a second field to say so.
    `invention` retires with it: this file measures token overlap, and a
    clause with none of it is unmatched and uncited — an absence, not a claim
    about what the author invented.

    The declaration is handed on rather than absorbed: `citation` carries the
    tag verbatim and `citation_resolves` says whether the location it names
    is in the canonical text. Measured across 412 corpus citations, 408
    resolve, 0 do not and 4 name no resolvable location — so this is a
    reading for Agent B to act on, NOT a threshold. Requiring a resolvable
    citation would change no verdict, which is the second measurement in two
    rounds to veto that idea.
    """
    ratio = _best_match_ratio(ac_text, canonical_units)
    # score = (1 - ratio) + penalty if interpretive choices without DERIVED
    # Cap at 1.0.
    penalty = 0.0
    if ratio < 0.95 and not derived_present:
        # Non-verbatim AND no DERIVED tag → interpretive without marker.
        # Only add penalty if the AC contains interpretive language markers.
        interp_markers = re.search(
            r"\b(must|should|will|requires?|including|excludes?|excluding|"
            r"only|never|always|via|using)\b",
            ac_text,
            re.IGNORECASE,
        )
        if interp_markers:
            penalty = 0.3

    score = min(1.0, (1.0 - ratio) + penalty)

    # Thresholds and order are Round 105's, unmoved: a clause the overlap
    # already decides keeps a measured verdict whether or not a tag is also
    # present, which `test_the_tag_does_not_change_a_verdict_the_overlap
    # _already_decided` pins.
    if ratio >= 0.85:
        verdict = "transcribed"
    elif ratio >= 0.45:
        verdict = "overlaps_canonical"
    elif derived_present:
        verdict = "cites_canonical"
    else:
        verdict = "unmatched_and_uncited"

    citation = citation_in(ac_text)
    return {
        "over_spec_score": round(score, 3),
        "best_match_ratio": round(ratio, 3),
        "derived_present": derived_present,
        "verdict": verdict,
        "citation": citation,
        "citation_resolves": citation_resolves_in(citation, canonical_text),
    }


# ---------------------------------------------------------------------------
# Report builder
# ---------------------------------------------------------------------------


def build_diff_report(
    srs_path: Path,
    spec_path: Path | None,
    mode: str = "srs_vs_spec",
) -> dict:
    """Build the full diff report for a deliverable vs canonical.

    spec_path=None triggers Elicitation-mode return (empty spec_sentences).
    """
    if not srs_path.exists():
        raise FileNotFoundError(f"Deliverable not found: {srs_path}")

    srs_text = srs_path.read_text(encoding="utf-8")
    clauses = _split_ac_clauses(srs_text)

    canonical_units: list[str] = []
    spec_text = ""
    spec_present = spec_path is not None and spec_path.exists()
    if spec_present and spec_path is not None:
        spec_text = spec_path.read_text(encoding="utf-8")
        canonical_units = _split_canonical_units(spec_text)

    per_ac: list[dict] = []
    high_count = 0
    for c in clauses:
        if not c["body"]:
            continue
        score = compute_over_spec_score(
            c["body"], canonical_units, c["derived_present"], spec_text,
        )
        record = {
            "label": c["label"],
            "fr_id": c["fr_id"],
            # Round 105 站2: which unit this score covers. The Phase 1 prompt
            # authorises two shapes for an acceptance criterion — its own
            # `#### AC-x.y` heading, or a bolded bullet under an
            # `**Acceptance criteria**` label — and `_split_ac_clauses` slices
            # on headings, so taskq-sn (95 AC headings) is scored once per
            # criterion and taskq-done (84 bullets, 0 headings) once per
            # REQUIREMENT, its FR-01 body running to 2,689 characters over
            # eight criteria. Both are read; the numbers are not comparable,
            # and a longer unit has more token overlap and needs one
            # `DERIVED:` for the whole section.
            "unit": ("criterion" if c["label"].upper().startswith("AC")
                     else "requirement"),
            "score": score,
        }
        per_ac.append(record)
        if score["verdict"] == "unmatched_and_uncited":
            high_count += 1

    summary = {
        "total_ac": len(per_ac),
        # What the SRS actually declares, from the parser that reads both
        # authorised shapes. `total_ac` is the count of units the splitter
        # produced and has been published under this name since the report
        # existed; for taskq-done that is 22 against 84 real criteria.
        "total_acceptance_criteria": sum(
            len(v) for v in
            artifact_consistency.acceptance_criteria_from_text(
                _without_machine_block(srs_text)).values()),
        # Round 108 站B: the size of the thing every ratio was measured
        # against. `_split_canonical_units` yields one or two document-sized
        # blocks on every real specification (eighteen measured, none above
        # two), so `best_match_ratio` is coverage against the whole canonical
        # text and not attribution to a sentence. Four shipped statements said
        # "sentence" and no artifact carried the number that contradicts them;
        # these two are that number.
        "canonical_units": len(canonical_units),
        "canonical_unit_max_chars": max(
            (len(u) for u in canonical_units), default=0),
        # One count per verdict, derived from the vocabulary rather than
        # written out beside it — four hand-written sums is how a renamed
        # verdict comes to have a count nobody moved.
        "verdict_counts": {
            v: sum(1 for r in per_ac if r["score"]["verdict"] == v)
            for v in VERDICTS
        },
        # How many clauses declared a derivation, and how many of those name
        # a location that is really in the canonical text. `None` — no
        # locator this framework can resolve — is counted in neither.
        "citation_counts": {
            "cited": sum(1 for r in per_ac if r["score"]["citation"]),
            "resolves": sum(
                1 for r in per_ac if r["score"]["citation_resolves"] is True),
            "does_not_resolve": sum(
                1 for r in per_ac if r["score"]["citation_resolves"] is False),
        },
        "over_spec_threshold": 0.7,
        "high_score_count": sum(
            1 for r in per_ac if r["score"]["over_spec_score"] > 0.7
        ),
    }

    # Round 86 站3 — the omission axis, beside the invention axis.
    #
    # `per_ac` scores what Agent A WROTE against the canonical text; nothing
    # in this report said which canonical requirements never arrived. Agent
    # B's first checklist question is exactly that ("did A transcribe ALL
    # features"), and for a spec too large to relay whole it can no longer be
    # answered by reading the DOC. `check_spec_alignment` already computes
    # this set difference deterministically — reused rather than re-derived,
    # so one document cannot have two answers to which FRs are in it.
    #
    # Placed BEFORE `per_ac`: when this file itself exceeds the relay ceiling
    # (taskq-new's is 27,762 bytes at 124 ACs) the index relays its head, and
    # `per_ac` is the part that grows without bound.
    fr_coverage: dict[str, Any] = {}
    config_keys: dict[str, Any] = {}
    if spec_present and spec_path is not None:
        spec_frs = structural_fr_ids(spec_text)
        srs_frs = structural_fr_ids(srs_text)
        fr_coverage = {
            "in_spec_only": sorted(spec_frs - srs_frs),
            "in_srs_only": sorted(srs_frs - spec_frs),
            "in_both": sorted(spec_frs & srs_frs),
        }
        # Round 87 站4: the FR axis asks whether a requirement's ID survived
        # ingestion. This asks whether the thing it is ABOUT did. Reported
        # here and enforced against the delivered source by
        # `spec_alignment._config_key_violations` — one extractor, two
        # readers, so the two cannot disagree about which keys the spec
        # declares.
        declared = spec_config_keys(spec_text)
        config_keys = {
            "declared": sorted(declared),
            "absent_from_srs": sorted(k for k in declared if k not in srs_text),
        }

    return {
        "deliverable": str(srs_path),
        "canonical": str(spec_path) if spec_present else None,
        "mode": mode,
        "spec_present": spec_present,
        "summary": summary,
        "fr_coverage": fr_coverage,
        "config_keys": config_keys,
        "machine_block_parity": machine_block_parity(srs_text),
        "per_ac": per_ac,
    }


#: A term specific enough that its absence means something: a hyphenated
#: compound (`per-token`, `deny-by-default`, `round-trip`) or an ALL-CAPS
#: identifier. Ordinary words differ between two descriptions of the same
#: requirement for reasons that are not omissions.
_SIGNIFICANT_TERM = re.compile(
    r"\b([a-z]+-[a-z]+(?:-[a-z]+)?|[A-Z][A-Z0-9_]{4,})\b")


def machine_block_parity(srs_text: str) -> list[dict]:
    """Requirements whose machine block says something their prose does not.

    Round 87 站4b. SRS.md has two readers and each reads a different half.
    `_without_machine_block` (Round 42 站1) STRIPS the fenced JSON before
    scoring conformance; `scripts/plangen/artifact_parsers` reads ONLY that
    JSON to build the FR registry. A requirement can therefore be complete in
    the half nobody scores and absent from the half everything downstream
    quotes.

    Two measured instances in taskq-redo, both live:

        FR-05   block: "per-token DB-backed token bucket"    prose: no
                `per-token` anywhere -> delivered as per-scope, one bucket
                shared by every holder of a read/write/admin key
        NFR-12  block: "chains upgrade -> tests -> health smoke -> migration
                round-trip"   prose AC-N12.1: "exits 0 and prints
                verify-system: PASS" -> Makefile chains two of the four, and
                `execute_verification_target` scored 100.0

    REPORTED, NOT BLOCKED, and the measurement is why: the same rule finds 5
    requirements in taskq-redo, 4 in taskq-cc and 11 in taskq-cc-new, and
    taskq-cc implemented `per-token` correctly regardless. It marks a hazard
    that exists in every project rather than a defect that distinguishes one,
    and Round 42's rule is that a project obeying the substance must not be
    blocked for the letter. It goes where the reader who can act on it already
    looks: `srs_vs_spec_diff.json` is Agent B's DOC 3.
    """
    span = srs_machine_block_span(srs_text)
    if span is None:
        return []
    start, end = span
    block, prose = srs_text[start:end], srs_text[:start] + srs_text[end:]
    match = re.search(r"\{.*\}", block, re.S)
    if match is None:
        return []
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return []
    out: list[dict] = []
    for req in ((data.get("functional_requirements") or [])
                + (data.get("non_functional_requirements") or [])):
        rid = req.get("id") or ""
        description = req.get("description") or ""
        if not rid or not description:
            continue
        heading = re.search(
            rf"^#+\s*(?:[\d.]+\s+)?{re.escape(rid)}\b", prose, re.MULTILINE)
        if heading is None:
            continue
        rest = prose[heading.end():]
        nxt = re.search(r"^#+\s", rest, re.MULTILINE)
        section = rest[: nxt.start()] if nxt else rest
        missing = sorted(
            {t for t in _SIGNIFICANT_TERM.findall(description) if t not in section})
        if missing:
            out.append({"id": rid, "terms_only_in_machine_block": missing})
    return out


def write_report(report: dict, out_path: Path) -> Path:
    """Write JSON report to disk. Creates parent dirs as needed."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return out_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _cli() -> int:
    parser = argparse.ArgumentParser(
        description="Word-level diff between deliverable and canonical spec. "
                    "Outputs an over_spec_score per AC clause.",
    )
    parser.add_argument(
        "--srs", "--deliverable", dest="deliverable", required=True,
        help="Path to the deliverable (SRS.md / TEST_SPEC.md / etc.)",
    )
    parser.add_argument(
        "--spec", "--canonical", dest="canonical", default=None,
        help="Path to canonical spec (SPEC.md). Optional — Elicitation mode if absent.",
    )
    parser.add_argument(
        "--mode", default="srs_vs_spec",
        choices=["srs_vs_spec", "testspec_vs_srs", "verification_vs_srs"],
        help="Diff mode (phase-agnostic naming). Default: srs_vs_spec.",
    )
    parser.add_argument(
        "--out", default=None,
        help="Output JSON path. Default: <deliverable-stem>_diff.json alongside deliverable.",
    )
    args = parser.parse_args()

    srs = Path(args.deliverable).resolve()
    spec = Path(args.canonical).resolve() if args.canonical else None

    if not srs.exists():
        print(f"[canonical_diff] deliverable not found: {srs}", file=sys.stderr)
        return 2

    if spec is not None and not spec.exists():
        print(f"[canonical_diff] WARNING: canonical spec missing ({spec}); "
              f"Elicitation mode — empty canonical_units, scores will be high.",
              file=sys.stderr)
        spec = None  # treat as Elicitation

    report = build_diff_report(srs, spec, mode=args.mode)
    out = Path(args.out) if args.out else srs.with_name(srs.stem + "_diff.json")
    write_report(report, out)

    s = report["summary"]
    print(f"[canonical_diff] {s['total_ac']} AC clauses analyzed")
    print("  " + "  ".join(f"{v}={s['verdict_counts'][v]}" for v in VERDICTS))
    c = s["citation_counts"]
    print(f"  cited={c['cited']}  citation resolves={c['resolves']}  "
          f"does not resolve={c['does_not_resolve']}")
    if s["high_score_count"]:
        print(f"  WARNING: {s['high_score_count']} AC(s) have over_spec_score > 0.7 "
              f"— see {out}")
    print(f"  Report: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
