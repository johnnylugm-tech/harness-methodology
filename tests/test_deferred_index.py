"""The index may not say anything the ledger does not say.

Round 81 站5. `docs/PROPOSAL_ADJUDICATIONS.md` states its own protocol —
consult it before re-opening a question, reject by entry number when the
re-open condition is unmet — and Round 80 was its counterexample: three of that
round's opening findings had been adjudicated by R74 站3 and R77 站4, were
re-derived from scratch, and were withdrawn.

Round 80 declined to build this index, and the reason it recorded is the reason
this file exists:

    23+ 條散在 5500 行散文裡,提取有把話塞進前幾輪嘴裡的實際風險

That risk belongs to an extraction that REWRITES. `test_every_field_is_a_byte_
exact_slice_of_the_ledger` is what turns "this one only copies" from an
intention into a property: paraphrase a single character of any of the 162
entries and it goes red.

WHAT THIS DOES NOT CLAIM TO FIX

Both guards that already answered Round 80's re-derived questions
(tests/test_test_spec_parser_parity.py, tests/test_fr_test_filename_parity.py)
existed and were GREEN throughout that round. An index does not stop someone
from not looking. What it buys is that looking now has somewhere to land, and
docs/deferred_guards.yaml — the hand-written half — is the part that connects a
decision to the test that would notice its re-open condition. Said here rather
than left to be discovered, because overstating what a mechanism buys is how
the next round comes to trust it for something it does not do.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
import yaml

pytestmark = [pytest.mark.core]

REPO = Path(__file__).resolve().parents[1]
LEDGER = REPO / "docs" / "PROPOSAL_ADJUDICATIONS.md"
INDEX = REPO / "docs" / "deferred_index.yaml"
GUARDS = REPO / "docs" / "deferred_guards.yaml"

_VERBATIM_FIELDS = ("item", "reason", "reopen", "text")


def _index() -> "list[dict]":
    return yaml.safe_load(INDEX.read_text(encoding="utf-8"))["entries"]


def _guards() -> "list[dict]":
    return yaml.safe_load(GUARDS.read_text(encoding="utf-8"))["entries"]


def _key(entry: dict) -> "tuple[int, str]":
    """The `(round, item)` pair docs/deferred_guards.yaml addresses an entry by.

    ONE function, because there are two readers — guards → index and index →
    guards — and a key computed in two places is a key that can disagree with
    itself. Round 110 站3 added the second reader; giving it its own copy of
    `item or text-first-line` would have been this repository's most-repaired
    shape in the file whose whole job is to notice that shape elsewhere.

    Three sources, in order:
      * `item`      — table rows, and (since 站3) bullets
      * `heading`   — unstructured sections, which carry no `item` BY DESIGN:
                      `test_the_extractor_admits_the_sections_it_cannot_read`
                      forbids it, because an extractor that names a section it
                      could not parse has guessed at it (Round 46)
      * first line  — the pre-站3 fallback, kept so this stays total
    """
    return (entry["round"],
            entry.get("item") or entry.get("heading")
            or entry.get("text", "").split("\n")[0])


def test_every_field_is_a_byte_exact_slice_of_the_ledger():
    """The property that makes the extraction safe rather than careful."""
    ledger = LEDGER.read_text(encoding="utf-8")

    invented = [
        f"round {entry['round']} line {entry.get('line')} field {field}: "
        f"{entry[field][:80]!r}"
        for entry in _index()
        for field in _VERBATIM_FIELDS
        if field in entry and entry[field] not in ledger
    ]
    assert not invented, (
        "these index fields are not present verbatim in "
        "docs/PROPOSAL_ADJUDICATIONS.md, so the index is stating something the "
        "ledger does not — which is exactly the risk Round 80 declined this "
        "extraction over:\n  " + "\n  ".join(invented)
    )


def test_the_index_is_regenerable_and_current():
    """A generated file that drifts from its generator is a hand-edited one."""
    result = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "extract_deferred_index.py"), "--check"],
        capture_output=True, text=True, cwd=str(REPO), check=False,
    )
    assert result.returncode == 0, (
        f"docs/deferred_index.yaml is not what its generator produces. Either "
        f"the ledger gained a 不做 section and the index was not regenerated, or "
        f"the index was hand-edited — and the guard mapping that is meant to be "
        f"hand-written lives in docs/deferred_guards.yaml.\n{result.stderr}"
    )


def test_the_extractor_admits_the_sections_it_cannot_read():
    """Round 46: an absent witness is not a failed testimony.

    Three of the ledger's 57 不做 sections are running prose with no table and
    no bullets. The extractor emits them as `unstructured` with their line range
    and no fields. A parser reshaped until every section yields something is
    Round 55's shape, and this assertion is what makes that reshaping visible:
    dropping the unstructured branch would silently lose three rounds' decisions.

    The two numbers were 36 and two when this was written and were 57 and three
    when Round 110 站3 measured them. Nothing asserts them, which is why they
    drifted; they are here because the shape of the hole is the point, and a
    stale count is still better than a sentence that does not say how big it is.
    """
    entries = _index()
    unstructured = [e for e in entries if e["kind"] == "unstructured"]

    assert unstructured, (
        "no section is marked unstructured. Either the ledger's prose sections "
        "gained a parseable shape — in which case say so here — or the "
        "extractor started guessing at them."
    )
    for entry in unstructured:
        assert not any(f in entry for f in _VERBATIM_FIELDS), (
            f"an unstructured section carries extracted fields, which means the "
            f"extractor guessed: {entry}"
        )
        assert "spans_lines" in entry, (
            f"a hole has to say where it is, or it is just missing: {entry}"
        )


def test_the_reopen_column_is_the_one_the_table_says_it_is():
    """Round 109 站7 — the index read its columns by position.

    23 of the ledger's 24 不做 tables are `| 項目 | 理由 | re-open |`, and one
    (Round 72) is `| # | 事項 | 理由 | re-open |`. Reading `cells[2]` gave those
    four rows an `item` of "A".."D", a `reason` holding the real item, a
    `reopen` holding the real reason — and the real re-open condition, in
    `cells[3]`, reached the index not at all.

    Everything stayed byte-exact, so `test_every_field_is_a_byte_exact_slice_of_
    the_ledger` was green throughout: each cell WAS a verbatim slice of the
    ledger, just of the wrong cell. Round 24's shape — the field is present and
    its content is not what the name says.

    This asks the table's own header where its re-open column is, which is the
    property the extractor now has to hold rather than happen to.
    """
    import sys

    sys.path.insert(0, str(REPO / "scripts"))
    import extract_deferred_index as extractor

    lines = LEDGER.read_text(encoding="utf-8").splitlines()
    by_line = {e["line"]: e for e in _index() if e.get("kind") == "table_row"}

    wrong = []
    for _round, start, end in extractor._sections(lines):
        rows = [(k, lines[k]) for k in range(start + 1, end)
                if lines[k].startswith("|") and not extractor._TABLE_SEP.match(lines[k])]
        if not rows:
            continue
        head_idx = rows[0][0]
        if not (head_idx + 1 < end and extractor._TABLE_SEP.match(lines[head_idx + 1])):
            continue
        header = extractor._cells(rows[0][1])
        cols = [i for i, h in enumerate(header) if "re-open" in h or "再開" in h]
        if not cols:
            continue
        col = cols[0]
        for k, row in rows[1:]:
            entry = by_line.get(k + 1)
            if entry is None:
                continue
            cells = extractor._cells(row)
            expected = cells[col] if col < len(cells) else None
            if entry.get("reopen") != expected:
                wrong.append(
                    f"line {k + 1}: header names column {col} "
                    f"({header[col]!r}) but the index carries "
                    f"{str(entry.get('reopen'))[:45]!r} instead of "
                    f"{str(expected)[:45]!r}")

    assert not wrong, (
        f"{len(wrong)} rows carry something other than their table's re-open "
        f"column. A verdict written against this text is a verdict about the "
        f"wrong sentence:\n  " + "\n  ".join(wrong)
    )


def test_the_index_covers_every_round_that_recorded_a_decision():
    """The scan reads all four heading depths and eight spellings.

    tests/test_ledger_has_no_holes.py learned this the hard way: its first pass
    grepped `^## Round` and manufactured three false holes. The 不做 headings
    are spelled at least eight ways across 80 rounds, which is why the extractor
    matches on the text rather than enumerating them.
    """
    rounds = {e["round"] for e in _index()}
    assert len(rounds) >= 30, (
        f"only {len(rounds)} rounds are represented; the ledger has 不做 "
        f"sections in far more than that, so the section scan is missing a "
        f"heading shape"
    )
    assert 80 in rounds, "Round 80's own not-doing table is the one this round acts on"


# ── the hand-written half ────────────────────────────────────────────────────

def test_every_guard_entry_names_a_decision_the_ledger_actually_records():
    """Keyed by (round, item), because line numbers do not survive the ledger.

    Round 81's own section was inserted above Round 80's and moved all eight of
    its rows. `item` is verbatim ledger text, so it is stable for as long as the
    decision is.
    """
    known = {_key(e) for e in _index()}

    orphans = [
        f"round {g['round']}: {g['item'][:70]!r}"
        for g in _guards() if (g["round"], g["item"]) not in known
    ]
    assert not orphans, (
        "these docs/deferred_guards.yaml entries name a decision that is not in "
        "the index — the ledger text was edited, or the key was mistyped. A "
        "mapping that points at nothing guards air:\n  " + "\n  ".join(orphans)
    )


def test_every_named_guard_resolves_to_a_test_that_exists():
    """A deleted guard leaves a re-open condition nobody can check any more.

    Same failure `scripts/verify_regression_guards.py` was built for, applied to
    the other ledger.
    """
    named = [(g["item"], g["guard"]) for g in _guards() if g["guard"] != "manual"]
    assert named, "every entry is `manual`; this assertion is then vacuous"

    for item, node in named:
        # `file::test_x` and `file::Class::test_x` are both node ids pytest
        # accepts; the function name is the last segment either way. Reading
        # `partition` gave `Class::test_x` and looked for `def Class::test_x(`,
        # which no file can contain — so a class-scoped guard was unnameable
        # rather than unresolvable, and Round 110 站3 hit it naming a real one.
        path, _, rest = node.partition("::")
        test_name = rest.rpartition("::")[2]
        source = REPO / path
        assert source.is_file(), f"{item[:50]!r} names a missing file: {path}"
        assert f"def {test_name}(" in source.read_text(encoding="utf-8"), (
            f"{item[:50]!r} names {node}, which no longer exists. The decision "
            f"it maps to still has a re-open condition and now has nothing "
            f"watching for it."
        )


# ── the reverse direction (Round 109 站7) ────────────────────────────────────
#
# The three assertions above all read guards -> index: no row may point at a
# decision that is not there, and no row may name a test that is not there.
# None of them can see the hole this file was actually built over — an index
# entry that says "re-open this when X" and has NO row at all. That direction
# was unmeasured for 28 rounds, and the count when it was first measured was 91.

_VERDICTS = {"MET", "NOT_MET", "PREMISE_FALSE", "ALREADY_DONE", "NO_CONDITION"}


def test_every_recorded_decision_has_a_verdict():
    """Round 110 站3 — the denominator the guard had picked without saying so.

    Round 109 站7 built this in the direction nothing had measured for 28
    rounds: an index entry saying "re-open this when X" with no row at all. It
    scoped itself to table rows carrying a non-dash `re-open` cell — 106 of the
    288 entries — and was green over that. The other 182 were not excluded on
    a judgement; they were unreachable:

        bullet        136   carried only `text`, so `(round, item)` — the key
                            deferred_guards.yaml is written against — could not
                            address one
        unstructured    3   no `item` by design
        table rows     43   dash or no re-open column, excluded deliberately as
                            "the ledger says there is no condition"

    That last exclusion was the defensible one and it is what makes the shape
    visible: a guard whose denominator is its own choice reports full marks on
    the part it chose. Round 57's question — who declares the scope — asked of
    the file built to answer Round 45's.

    老闆 ruled full coverage. The rule is now one sentence with no carve-out:
    **every decision the ledger records carries a verdict.** A bullet with no
    re-open condition is not exempt from being read; it is `NO_CONDITION`, and
    that is a judgement someone made rather than a row a regex skipped.
    """
    verdicted = {(g["round"], g["item"]) for g in _guards()}
    missing = [
        f"R{rnd} [{e['kind']}] {item[:70]!r}"
        for e in _index()
        for rnd, item in [_key(e)]
        if (rnd, item) not in verdicted
    ]
    assert not missing, (
        f"{len(missing)} of the ledger's recorded decisions have no verdict. "
        f"Every entry needs a row in docs/deferred_guards.yaml carrying a "
        f"verdict and the evidence for it — `NO_CONDITION` when the decision "
        f"states no condition to re-open under, which is a judgement and not "
        f"a reason to leave it out of the count:\n  " + "\n  ".join(missing)
    )


def test_every_verdict_carries_evidence():
    """A verdict without evidence is an opinion with a schema.

    Round 45: the verdict outlives its proof. The whole point of walking 91
    conditions was that "I looked and it seemed fine" is not an answer anyone
    can re-check next round.
    """
    bad = []
    for g in _guards():
        key = f"R{g['round']} {g['item'][:50]!r}"
        if g.get("verdict") not in _VERDICTS:
            bad.append(f"{key}: verdict={g.get('verdict')!r} not in {sorted(_VERDICTS)}")
        elif not (g.get("evidence") or "").strip():
            bad.append(f"{key}: verdict {g['verdict']} with no evidence")
    assert not bad, "\n  ".join(["malformed guard rows:"] + bad)


def test_already_done_names_the_test_that_proves_it():
    """Round 45, in the one place it bites hardest.

    ALREADY_DONE says the ledger is stale — the thing was built rounds ago. That
    claim is the easiest of the five to be wrong about and the hardest to
    re-check later, because there is no diff to point at. It has to name the
    test that would go red if it were not true.
    """
    unproven = [
        f"R{g['round']} {g['item'][:60]!r}"
        for g in _guards()
        if g.get("verdict") == "ALREADY_DONE" and g.get("guard") == "manual"
    ]
    assert not unproven, (
        "these rows claim the work was already done and name no test that "
        "proves it, which is a verdict whose proof has to be re-derived by "
        "hand every time someone doubts it:\n  " + "\n  ".join(unproven)
    )
