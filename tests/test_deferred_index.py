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

    Two of the ledger's 36 不做 sections are running prose with no table and no
    bullets. The extractor emits them as `unstructured` with their line range
    and no fields. A parser reshaped until every section yields something is
    Round 55's shape, and this assertion is what makes that reshaping visible:
    dropping the unstructured branch would silently lose two rounds' decisions.
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
    index = _index()
    known = {(e["round"], e.get("item") or e.get("text", "").split("\n")[0])
             for e in index}

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
        path, _, test_name = node.partition("::")
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

_NO_CONDITION_CELLS = {"", "—", "-", "–"}


def _has_reopen_condition(entry: dict) -> bool:
    """A dash in the re-open column is the ledger saying "there is no condition".

    43 of the 139 table rows are in that shape (29 with no column at all, 14
    with a dash). They are excluded on purpose: requiring a verdict for a
    decision with nothing to verdict ON manufactures 43 rows of noise, and
    noise is what makes the other 96 stop being read.
    """
    return (entry.get("reopen") or "").strip() not in _NO_CONDITION_CELLS


def test_every_reopen_condition_has_a_verdict():
    """Round 109 站7 — the hole the guard file could not see.

    `docs/deferred_guards.yaml`'s own header says an absent key means "nobody
    has checked", and then nothing anywhere measured how many absent keys there
    were. 91 of 96, when 老闆 asked. This assertion is what makes the answer
    stay answered: a new 不做 row with a re-open condition now lands red until
    someone writes down what they decided about it.
    """
    verdicted = {(g["round"], g["item"]) for g in _guards()}
    missing = [
        f"R{e['round']} {e['item'][:60]!r} — reopen: {e['reopen'][:60]!r}"
        for e in _index()
        if e.get("kind") == "table_row" and _has_reopen_condition(e)
        and (e["round"], e["item"]) not in verdicted
    ]
    assert not missing, (
        f"{len(missing)} deferred decisions state a condition under which they "
        f"should be re-opened, and nobody has recorded whether that condition "
        f"holds. Each needs a row in docs/deferred_guards.yaml with a verdict "
        f"and the evidence for it:\n  " + "\n  ".join(missing)
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
