"""Round 108 站B — four shipped statements say "sentence"; nothing ever split one.

`scripts/canonical_diff.py` scores every acceptance criterion by intersecting
its tokens with the tokens of the best-matching *canonical unit*. Four shipped
statements told the reader — a human, and Agent B — that the unit is a
sentence:

  * the module docstring's definition of `over_spec_score`
  * `_best_match_ratio`'s docstring ("against any canonical sentence")
  * the Phase 1 prompt's DOC 3 line, which reaches Agent B on every P1 round
  * `R-SEVERITY-RUBRIC-001`, which tells B that `high` means "not derivable
    from any canonical sentence"

Measured 2026-09-08 over every `SPEC.md` on this machine — eighteen projects:

    units == 1 : 3        units == 2 : 15       units > 2 : 0
    largest single unit  : 8,310 characters
    smallest project     : taskq, 11,434 chars -> ONE unit of 4,328
    `_SENTENCE_SPLIT_RE` hits on the RAW text of each: 0

`_SENTENCE_SPLIT_RE` is `(?<=[.!?])\\s+(?=[A-Z\\d])`: a full stop, whitespace,
then an ASCII capital or digit. A specification does not write that. Its
prose is one claim per bullet or table cell, so the character after a full
stop is a newline and then `-`, `|` or `#`. The corpus is 14–17% CJK and its
section headings read `## 0. 文件元資料`, which is why not one of the eighteen
produced a single hit.

Where the regex DOES fire is worth stating, because it is not a rescue: on an
English-headed document it fires at `## 1.`, `## 2.` — the section NUMBERING.
The fixture below is 1,291 characters with ten bulleted claims and splits
into five units at exactly the four numbered headings. Sections, not
sentences. Whichever document it is handed, the unit `_best_match_ratio`
scores against spans many claims, and that is the property this file pins.

This matters in the direction opposite to the one an auditor assumes. A
document-sized unit RAISES every overlap ratio: an AC is compared against
every token in the spec at once. Splitting it properly would lower them —
measured over fifteen projects with markdown block units, thirteen medians
fell and `>= 0.45` went to zero on four projects at a stroke. The splitter is
therefore left exactly as it is; what changes is that it no longer claims to
do something it does not do, and the report now carries the two readings that
make the degeneracy visible instead of invisible.

WHY THE FIXTURE HERE IS SHAPED LIKE A SPECIFICATION

`TestSplitSentences` in `tests/test_canonical_diff.py` fed the splitter three
sentences of English prose punctuated `". " + capital` and asserted it
returned three. That fixture was written to the regex (R19): it passes on
input the regex was built for, and on none of the eighteen real documents the
function is ever called with. The fixture below is a specification — headings,
bullets, a table, a fenced block — and pins the real behaviour.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.canonical_diff import _split_canonical_units, build_diff_report

pytestmark = [pytest.mark.core]

REPO = Path(__file__).resolve().parents[1]

# Shaped like the corpus, and shaped that way on purpose: headings, bullets
# and tables, one claim per line. A full stop here is always the last
# character of its line, so the next character the regex sees is a newline
# followed by `#`, `-`, `|` or a blank — never a space and a capital. That is
# not a contrivance; `_SENTENCE_SPLIT_RE` scores ZERO hits on the raw text of
# every corpus SPEC.md, measured 2026-09-08, for exactly this reason.
_SPEC_SHAPED = """# taskq — Specification (single source of truth)

## 0. Document metadata

| Field | Value |
|-------|-------|
| Version | v4.0.0 |
| Baseline | v3.0.0, adopted 2026-07-11 |
| Owner | the project, not the harness |

## 1. Overview

- **Name**: `taskq`
- **Purpose**: a local task queue that submits shell commands as jobs and executes them under timeout, retry and circuit-breaker control
- **Language**: Python 3.11 with no runtime dependencies outside the standard library
- **Shape**: a command-line tool entered through `python -m taskq`

## 2. Functional Requirements

### FR-01 Job submission

- The CLI accepts a job name and an optional payload
- A submitted job is persisted to the queue file before the command returns
- A duplicate job name is rejected with a non-zero exit code

### FR-02 Worker pool

| Setting | Default | Notes |
|---------|---------|-------|
| workers | 4 | per host |
| timeout | 30s | wall clock including fork and exec |

```python
queue.submit("build", payload={"ref": "main"})
```

## 3. Non-Functional Requirements

### NFR-01 Latency

- p95 submission latency stays under 50ms on the reference host

### NFR-02 Durability

- The runner survives a worker crash without losing a queued job
- The queue file is fsynced before the submitting process exits
"""


def test_no_unit_a_specification_yields_is_a_sentence() -> None:
    """The counter-fixture. Prose splits into sentences; a specification does not.

    Swap `_SPEC_SHAPED` for three `". " + capital` sentences and this test
    stops holding — which is the whole point: the old unit test asserted
    exactly that swap and so could never have caught the degeneracy.

    The assertion is the property, not the count. On this repo's corpus the
    splitter returns one or two whole-document units; on an English-headed
    document it returns one unit per numbered SECTION. Both are multi-claim
    blocks, and both make "matched against a canonical sentence" false.
    """
    units = _split_canonical_units(_SPEC_SHAPED)
    claims = [ln for ln in _SPEC_SHAPED.splitlines() if ln.startswith("- ")]

    single_line = [u for u in units if "\n" not in u]
    assert not single_line, (
        f"{len(single_line)} unit(s) fit on one line, so the splitter has "
        f"started returning something sentence-shaped: {single_line}. If the "
        f"splitter was deliberately tightened, re-measure the corpus first — "
        f"it was tried and dropped because thirteen of fifteen projects' "
        f"median overlap ratios fell and four went to zero clauses above the "
        f"0.45 threshold, a corpus-wide false accusation")
    assert len(units) < len(claims), (
        f"{len(units)} units for {len(claims)} bulleted claims — at or above "
        f"one unit per claim is per-sentence matching, which is what the "
        f"retired wording promised and the corpus measurement vetoed")
    assert max(len(u) for u in units) > 250, (
        f"largest unit is {max(len(u) for u in units)} characters; the "
        f"corpus's smallest whole-document unit is 4,328. A small maximum "
        f"here means the fixture stopped being shaped like a specification")


def test_the_report_publishes_how_many_units_the_canonical_became(
    tmp_path: Path,
) -> None:
    """A degeneracy nobody can read is a degeneracy nobody will fix.

    `best_match_ratio` is only interpretable next to the size of the thing it
    was matched against. Before this round the report published the ratio and
    not the unit, so an 18KB spec collapsing to one block was invisible in
    every artifact the framework produced.
    """
    spec = tmp_path / "SPEC.md"
    spec.write_text(_SPEC_SHAPED, encoding="utf-8")
    srs = tmp_path / "SRS.md"
    srs.write_text(
        "# SRS\n\n### FR-01 Job submission\n\n"
        "The CLI accepts a job name and an optional payload.\n",
        encoding="utf-8",
    )

    summary = build_diff_report(srs, spec)["summary"]
    units = _split_canonical_units(_SPEC_SHAPED)

    assert summary["canonical_units"] == len(units), (
        f"the report says {summary['canonical_units']} units; the splitter "
        f"that produced the scores says {len(units)}. Two answers to one "
        f"question is the defect this repository keeps closing")
    assert summary["canonical_unit_max_chars"] == max(len(u) for u in units), (
        f"largest unit reported as {summary['canonical_unit_max_chars']}, "
        f"measured as {max(len(u) for u in units)} — the reading exists but "
        f"is not the measurement")
    assert summary["canonical_unit_max_chars"] > 250, (
        "a ratio is only interpretable beside the size of the text it was "
        "matched against; a tiny maximum means the fixture stopped being "
        "shaped like a specification")


def test_no_generator_still_calls_the_comparison_unit_a_sentence() -> None:
    """The three sources the four statements are rendered from.

    Scoped to sources on purpose. A whole-tree scan for `canonical sentence`
    also hits `docs/PROPOSAL_ADJUDICATIONS.md`, which quotes the retired
    wording in order to record it — the same false-accusation shape that made
    Round 108 站A drop its document-vocabulary scan. The generated JS is not
    scanned here either: `generate_workflows.py --check` already proves the
    shipped copies are what these sources render.
    """
    sources = [
        Path("scripts/canonical_diff.py"),
        Path("scripts/workflowgen/spec_phase1.py"),
        Path("harness/prompts/rules/R-SEVERITY-RUBRIC-001.md"),
    ]

    offenders = []
    for rel in sources:
        text = (REPO / rel).read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), 1):
            if "canonical sentence" in line:
                offenders.append(f"{rel}:{lineno}: {line.strip()[:90]}")

    assert not offenders, (
        "these statements tell their reader the AC is matched against a "
        "sentence. Measured over eighteen corpus SPEC.md files, the splitter "
        "produced one or two document-sized blocks and never a sentence:\n  "
        + "\n  ".join(offenders))
