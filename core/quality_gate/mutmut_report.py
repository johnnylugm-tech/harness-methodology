"""The one place that reads and writes mutmut's report formats (Round 31 站1).

Before this module there were four parsers of mutmut output in three files,
each with its own idea of the format, and three of them could not read
anything the system actually produces:

  ``_parse_mutmut_survivors``      accepted ``10, 24``
  ``_extract_mutmut_kill_rate``    accepted ``Killed 240``
  ``tool_runners._score_mutmut``   accepted ``Killed: 240``

while ``mutmut results`` prints ids as RANGES and ``compute_mutation_score``
prints ``killed=240 survived=308 score=43.8``. Every one of them returned the
value that means "nothing found" — 0, None, 0.0 — so on a real Gate 2 a run
reporting 308 surviving mutants was recorded as having none, and the
anti-fabrication cross-check that was supposed to catch a wrong score had
never once parsed a real input.

The formatter lives beside the parser on purpose: the score message is a
format contract between ``compute_mutation_score`` (producer) and the gate's
cross-check (consumer), and the last four rounds of this repo are mostly
stories about a producer and a consumer drifting apart in different files.
``test_mutmut_report.py`` round-trips them.
"""
from __future__ import annotations

import re
from typing import Optional

# ── the framework's own score message ───────────────────────────────────

_SCORE_RE = re.compile(
    r"killed=(\d+)\s+survived=(\d+)\s+score=(\d+(?:\.\d+)?)", re.IGNORECASE
)

# Legacy free-text shapes. Nothing in this repo emits them; they are kept
# because a human may paste `mutmut` output from another toolchain version
# into a tool_output file, and reading one more format costs two regexes.
_LEGACY_COUNTS_RE = (
    re.compile(r"\bKilled\s+(\d+)", re.IGNORECASE),
    re.compile(r"\bSurvived\s+(\d+)", re.IGNORECASE),
)
_LEGACY_PCT_RE = re.compile(
    r"mutation\s+score[:\s]+(\d+(?:\.\d+)?)\s*%", re.IGNORECASE
)


def format_score_message(
    *, killed: int, survived: int, score: float, scope: str = ""
) -> str:
    """The single spelling of the mutation score line.

    *scope* travels with the number because the number is meaningless without
    it: a Gate 2 recorded ``mutation_testing 0/70`` three times while no
    artifact anywhere said which directories had been mutated.
    """
    msg = f"killed={killed} survived={survived} score={score}"
    return f"{msg} [scope: {scope}]" if scope else msg


def parse_score(text: str) -> Optional[dict]:
    """Return ``{"killed", "survived", "score"}`` from a score line, or None.

    None means "no mutation statistics in this text" and callers must treat it
    as *unknown*, never as *clean* — that conflation is what this module was
    written to end.
    """
    m = _SCORE_RE.search(text or "")
    if m:
        killed, survived = int(m.group(1)), int(m.group(2))
        return {"killed": killed, "survived": survived,
                "score": float(m.group(3))}

    killed_m, survived_m = (r.search(text or "") for r in _LEGACY_COUNTS_RE)
    if killed_m and survived_m:
        killed, survived = int(killed_m.group(1)), int(survived_m.group(1))
        total = killed + survived
        if total:
            # Unrounded on purpose: the framework path carries an already-
            # rounded score, this one derives it, and a caller comparing
            # against a claimed score should see the full ratio.
            return {"killed": killed, "survived": survived,
                    "score": 100.0 * killed / total}

    pct = _LEGACY_PCT_RE.search(text or "")
    if pct:
        return {"killed": None, "survived": None, "score": float(pct.group(1))}
    return None


def kill_rate(text: str) -> Optional[float]:
    """The 0-100 kill rate carried by *text*, or None when it carries none."""
    parsed = parse_score(text)
    return None if parsed is None else parsed["score"]


# ── `mutmut results` ────────────────────────────────────────────────────

_FILE_HEADER_RE = re.compile(r"^-{2,}\s+(.+?)\s+\((\d+)\)\s+-{2,}$")
# mutmut's cache.ranges() collapses consecutive ids: "233-245, 248, 250-256".
_ID_LINE_RE = re.compile(r"^\d+(?:-\d+)?(?:\s*,\s*\d+(?:-\d+)?)*$")
_ID_OR_RANGE_RE = re.compile(r"(\d+)(?:-(\d+))?")
_BANNER_RE = re.compile(r"Survived[^(]*\((\d+)\)")

# A single range wider than this is a malformed line, not a mutant list; mutant
# ids are per-run counters and no real run approaches it. Bounded so a corrupt
# report cannot turn into an allocation.
_MAX_RANGE_WIDTH = 100_000


def parse_reported_total(results_output: str) -> Optional[int]:
    """The survivor count mutmut itself printed in its banner, or None.

    Recorded next to the parsed list so that "we parsed nothing" and "there was
    nothing to parse" stay distinguishable in the artifact.
    """
    m = _BANNER_RE.search(results_output or "")
    return int(m.group(1)) if m else None


def parse_survivors(results_output: str) -> list:
    """Parse ``mutmut results`` into ``[{file, line, mutant_id, mutator}]``.

    mutmut groups surviving ids under a per-file header carrying the count::

        ---- src/app/dag.py (3) ----

        318, 324-325

    Ranges are expanded. Unrecognised output yields ``[]``; pair it with
    :func:`parse_reported_total` before reading that as "clean".
    """
    survivors: list = []
    current_file: Optional[str] = None
    for line in (results_output or "").splitlines():
        stripped = line.strip()
        header = _FILE_HEADER_RE.match(stripped)
        if header:
            current_file = header.group(1)
            continue
        if not current_file or not _ID_LINE_RE.match(stripped):
            continue
        for start_s, end_s in _ID_OR_RANGE_RE.findall(stripped):
            start = int(start_s)
            end = int(end_s) if end_s else start
            if end < start or end - start > _MAX_RANGE_WIDTH:
                continue
            for mid in range(start, end + 1):
                survivors.append({
                    "file": current_file, "line": None,
                    "mutant_id": str(mid), "mutator": None,
                })
    return survivors
