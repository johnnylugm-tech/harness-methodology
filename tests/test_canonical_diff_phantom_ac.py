"""Round 42 站0 — a section heading is not a requirement.

`scripts/canonical_diff.py` compares each acceptance clause in SRS.md against
the canonical SPEC.md and labels it verbatim / interpreted / invention. Its
clause splitter finds requirements with

    ^(#{1,6})\\s+(?P<label>(?:FR|NFR|AC)[-\\w]*)\\b[^\\n]*$

and `[-\\w]*` matches **zero** characters, so any heading whose first word
starts with FR, NFR or AC is taken for a requirement.

`templates/SRS.md:78` requires the section `## 7. FR Block (machine-readable)`;
`docs/P1_SOP.md:23` and `:58` list it as a Phase 1 must; `scripts/plangen/
artifact_parsers.py` parses it and warns when it is absent. taskq-renew wrote
it. Its SRS line 900 reads `## FR Block (machine-readable)`, the splitter took
it as a 21st acceptance clause labelled `FR`, its body (a JSON block) matched
no canonical prose, and `srs_vs_spec_diff.json` recorded

    {"label": "FR", "fr_id": "FR", "score": {"over_spec_score": 0.731,
     "best_match_ratio": 0.269, "derived_present": false,
     "verdict": "invention"}}

taskq-plus has no such heading — it never wrote the block at all — so it scored
`invention_count: 0`. The project that followed the template was charged with
inventing a requirement; the project that skipped a required deliverable was
not. Same 494-line SPEC.md on both sides.

Scanned across every SRS on disk (taskq 69 matched headings, taskq-plus 20,
taskq-renew 21, taskq-api 22), exactly one match has no digit in its label:
that one. So the rule is "a requirement label carries its number".

Tightening the regex alone is NOT the fix, and this file pins that too. The
splitter slices each clause's body from one match to the next, so dropping a
match **merges its body into the previous clause**: with a digit-requiring
regex and nothing else, taskq-renew's NFR-12 body grows from 9,773 to 13,960
characters — it swallows the JSON block, and NFR-12's own score moves. The
machine-readable block has to leave the text before the split, which is what
its `<!-- JSON:START -->` / `<!-- FR:START -->` sentinels are for.
"""

from __future__ import annotations

from scripts import canonical_diff


# The shape taskq-renew's SRS actually has, reduced to the two requirements
# that bracket the block. Written out rather than read from the project so the
# guard keeps working when that repo moves on.
_SRS_WITH_MACHINE_BLOCK = """\
# Software Requirements Specification

## 3. Functional Requirements

### FR-01: task submission

The submitted command is validated before anything is written.

- **AC-01-1:** `submit "echo hi"` exits 0 and prints an 8-hex id.

## 4. Non-Functional Requirements

### NFR-12: system verification target

`make verify-system` exits 0.

- **AC-12-1:** `make verify-system` exits 0.

---

## FR Block (machine-readable)

<!-- JSON:START -->
```json
{"functional_requirements": [{"id": "FR-01", "title": "task submission"}]}
```
<!-- JSON:END -->
"""


def _labels(text: str) -> list[str]:
    return [c["label"] for c in canonical_diff._split_ac_clauses(text)]


def _body_of(text: str, label: str) -> str:
    for c in canonical_diff._split_ac_clauses(text):
        if c["label"] == label:
            return c["body"]
    raise AssertionError(f"{label} not in {_labels(text)}")


def test_a_machine_readable_block_heading_is_not_a_requirement():
    """`## FR Block (machine-readable)` must not become an acceptance clause.

    It is the single phantom across every SRS on disk, and it is the one
    taskq-renew's `invention_count: 1` was made of.
    """
    labels = _labels(_SRS_WITH_MACHINE_BLOCK)
    assert "FR" not in labels, (
        "the machine-readable block's heading was taken for a requirement "
        f"labelled 'FR' — clauses found: {labels}"
    )
    assert labels == ["FR-01", "NFR-12"]


def test_the_last_requirement_does_not_swallow_the_machine_readable_block():
    """Excluding the heading must not hand its body to the clause before it.

    This is why the fix cannot be the regex alone: the splitter runs match to
    match, so a dropped match is an absorbed body, and the last requirement in
    every SRS is the one that absorbs it.
    """
    body = _body_of(_SRS_WITH_MACHINE_BLOCK, "NFR-12")
    assert "functional_requirements" not in body and '"FR-01"' not in body, (
        "NFR-12's body absorbed the machine-readable block once its heading "
        f"stopped being a clause boundary:\n{body}"
    )
    # The now-empty `<!-- JSON:START -->` / `<!-- JSON:END -->` pair is left
    # where it is. `srs_machine_block_span` locates the block by its fenced
    # JSON, and widening that span to swallow whatever comments happen to
    # bracket it would be the heading-and-sentinel rule
    # `scripts/plangen/artifact_parsers.srs_machine_block` was written to
    # replace. Two HTML comments are not requirements prose; the JSON was.


def test_a_real_requirement_heading_is_still_extracted():
    """Positive control: the numbered forms every SRS uses must survive.

    `### FR-01:` and `### NFR-12:` above, plus the `#### AC1` form
    `_split_ac_clauses` maps back to its parent requirement.
    """
    text = (
        "# Software Requirements Specification\n\n"
        "### FR-03: retry and circuit breaker\n\n"
        "Retries back off exponentially.\n\n"
        "#### AC1\n\nThe third consecutive failure opens the breaker.\n\n"
        "### NFR-07: dependency and licence compliance\n\n"
        "Every dependency is in the allowlist.\n"
    )
    clauses = canonical_diff._split_ac_clauses(text)
    labels = [c["label"] for c in clauses]
    assert labels == ["FR-03", "AC1", "NFR-07"], labels
    parents = {c["label"]: c["fr_id"] for c in clauses}
    assert parents["AC1"] == "FR-03", (
        "an AC must still resolve to the requirement it sits under"
    )
