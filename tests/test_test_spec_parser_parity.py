"""TEST_SPEC declaration-row parity: every reader gives the same answer.

Round 74 站3, and Round 8 站1's registry shape applied to a second population.

There are two readers of TEST_SPEC.md's declaration tables:

  core.quality_gate.spec_coverage._parse_test_spec   D4 spec-coverage,
                                                     `spec_undelivered`,
                                                     the 4b ratio
  harness.harness_bridge._parse_spec_names_for_fr    the Gate 1 per-FR
                                                     test_coverage cap

Round 73 fixed the first one's hardcoded `cols[1]`. Round 74 站1 replaced its
keyword-based header test. Both defects sat untouched in the second one for
the whole of it — in a function whose own docstring read

    Canonical parser used by both prepare_gate() and _parse_test_spec().

which had not been true for as long as `_parse_test_spec` has lived in
spec_coverage.py. A sibling of a fixed defect, wearing the fixed one's name
(Rounds 20 and 39).

Measured across the nine projects on this machine before the change: zero
difference. Every `### FR-xx` table in all nine puts Test Function in column
1, and the one row whose Title prose reads as a header sits under an H2 that
has already cleared `current_fr`. Latent, not live, and recorded as such —
what this file stops is the two answers drifting apart again.

Registry maintenance rule: a function that walks TEST_SPEC.md lines and
decides which of them declare a test belongs here. `SpecAssertionParser`
(core/quality_gate/parsers/spec_assertion_parser.py) does NOT: it reads the
assertion-level schema — `Inputs` values and sub-assertion predicates — and
never answers "which tests are declared", which is why it has its own
`_split_row_cells` honouring the `\\|` escape.
"""

from __future__ import annotations

import re

import pytest

pytestmark = [pytest.mark.core]


def _via_spec_coverage(text: str, fr_id: str, tmp_path) -> list:
    from core.quality_gate.spec_coverage import _parse_test_spec

    path = tmp_path / "TEST_SPEC.md"
    path.write_text(text, encoding="utf-8")
    return [i["test_fn"] for i in _parse_test_spec(path) if i["fr_id"] == fr_id]


def _via_harness_bridge(text: str, fr_id: str, _tmp_path) -> list:
    from harness.harness_bridge import _parse_spec_names_for_fr

    return _parse_spec_names_for_fr(text, fr_id)


# (label, reader) — the real functions, imported from production modules.
TEST_SPEC_READERS = [
    ("spec_coverage._parse_test_spec", _via_spec_coverage),
    ("harness_bridge._parse_spec_names_for_fr", _via_harness_bridge),
]


# Both readers slugify or key sections differently, so the corpus below uses
# `### FR-xx` headings only — the one section form both agree on, and the
# only form the Gate 1 per-FR cap ever asks for.
_SHAPES = {
    "four_column": """\
### FR-01: Thing

| # | Test Function | Type | Derivation |
|---|---|---|---|
| 1 | `test_fr01_a` | unit | AC-1.1 |
""",
    "five_column_with_inputs": """\
### FR-01: Thing

| # | Test Function | Inputs | Type | Derivation |
|---|---|---|---|---|
| 1 | `test_fr01_a` | body={} | integration | AC-1.1 |
""",
    "name_not_in_column_one": """\
### FR-01: Thing

| # | NFR | Test Function | Layer | Title |
|---|---|---|---|---|
| 1 | NFR-02 | `test_fr01_a` | static | bandit |
""",
    "header_disagrees_with_its_rows": """\
### FR-01: Thing

| # | NFR | Test Function | Layer | Title |
|---|---|---|---|---|
| 9 | `test_fr01_a` | NFR-02 | static | bandit |
""",
    "prose_title_naming_test_function": """\
### FR-01: Thing

| # | NFR | Test Function | Layer | Title |
|---|---|---|---|---|
| 1 | NFR-09 | `test_fr01_a` | unit | every test function ≥ 1 assert |
""",
    "alignment_separator": """\
### FR-01: Thing

| # | Test Function | Inputs | Type | Derivation |
|:--|:---------------|:------|:-----|----------:|
| 1 | `test_fr01_a` | x | unit | AC-1.1 |
""",
    "row_declaring_no_test": """\
### FR-01: Thing

| # | Test Function | Inputs | Type | Derivation |
|---|---|---|---|---|
| 1 | (cross-cutting tooling) | — | static | pip-licenses |
| 2 | `test_fr01_a` | x | unit | AC-1.1 |
""",
    "sub_assertion_table_follows": """\
### FR-01: Thing

| # | Test Function | Inputs | Type | Derivation |
|---|---|---|---|---|
| 1 | `test_fr01_a` | x | unit | AC-1.1 |

| rule_id | predicate | phase |
|---|---|---|
| AC1.1-status | `status == 201` | 3 |
""",
}


@pytest.mark.parametrize("shape", sorted(_SHAPES), ids=sorted(_SHAPES))
def test_both_readers_declare_the_same_tests(shape, tmp_path):
    """One document, two readers, one answer.

    Every shape here is transcribed from a corpus TEST_SPEC.md, including the
    two that used to divide the readers: a name outside column 1 (which the
    bridge read as the NFR id) and a Title cell containing the words "test
    function" (which both read as a header).
    """
    text = _SHAPES[shape]
    answers = {}
    for label, reader in TEST_SPEC_READERS:
        workdir = tmp_path / label.replace(".", "_")
        workdir.mkdir()
        answers[label] = reader(text, "FR-01", workdir)

    values = list(answers.values())
    assert values[0] == values[1] == ["test_fr01_a"], answers


def test_the_shape_the_framework_asks_for_is_a_shape_it_can_read(tmp_path):
    """The producer of the five-column table is this repo's own P2 prompt.

    `scripts/workflowgen/spec_phase2.py` tells every project:

        For all other NFRs (Unit/Static), isolate them in a `Deferred to
        Downstream Phases` table with columns: #, NFR, Test Function, Layer,
        Title.

    That instruction is why `cols[1]` was the NFR id in every project on this
    machine, and why every one of those tables has a Title column carrying
    English prose. The framework specified the header and its own readers
    hardcoded a different one — the prompt↔gate drift class of Round 17, on
    an artifact instead of a threshold.

    The columns are read out of the generator rather than copied here, so the
    day that sentence changes this test asks the readers about the new shape
    instead of the old one.
    """
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1]
              / "scripts" / "workflowgen" / "spec_phase2.py"
              ).read_text(encoding="utf-8")
    stated = re.search(
        r"Deferred to Downstream Phases`? table with columns:\s*([^\\\\'\"]+)",
        source)
    assert stated, ("the P2 prompt no longer states the Deferred table's "
                    "columns — this test can no longer check what it asks for")
    columns = [c.strip().rstrip(".") for c in stated.group(1).split(",")]
    assert "Test Function" in columns, columns

    header = "| " + " | ".join(columns) + " |"
    text = ("### FR-01: Thing\n\n"
            + header + "\n"
            + "|" + "---|" * len(columns) + "\n"
            + "| " + " | ".join(
                "`test_fr01_a`" if c == "Test Function"
                else "every test function ≥ 1 assert" if c == "Title"
                else "NFR-09" if c == "NFR" else "1"
                for c in columns) + " |\n")

    for label, reader in TEST_SPEC_READERS:
        workdir = tmp_path / label.replace(".", "_")
        workdir.mkdir()
        assert reader(text, "FR-01", workdir) == ["test_fr01_a"], (
            f"{label} cannot read the header its own framework asks for: "
            f"{header}")


# Every function in the tree that splits a markdown table row into cells,
# with what it splits and why it is or is not a declaration reader. The first
# version of this guard looked for the string `Test Function` in a function's
# code — which is the string station 3 REMOVED from the bridge, so the guard
# could only ever find readers that had not been fixed. Its counter-proof
# deleted the bridge from the registry and the test stayed green. Splitting
# cells is what a reader does whether or not anyone fixed it.
_TABLE_CELL_SPLITTERS = {
    # ── TEST_SPEC.md declaration rows: registered in TEST_SPEC_READERS ──
    ("core/quality_gate/spec_coverage.py", "_header_columns"): "reader",
    ("core/quality_gate/spec_coverage.py", "_is_header_row"): "reader",
    ("core/quality_gate/spec_coverage.py", "_parse_test_spec"): "reader",
    ("harness/harness_bridge.py", "_parse_spec_names_for_fr"): "reader",
    # ── other tables, other questions ──
    ("core/quality_gate/parsers/spec_assertion_parser.py", "_split_row_cells"):
        "TEST_SPEC assertion schema (Inputs values, sub-assertion predicates); "
        "never answers which tests are declared, and honours the \\| escape "
        "that a declaration name cannot contain",
    ("core/quality_gate/parsers/spec_tracking_parser.py", "split_row"):
        "SPEC_TRACKING.md status rows",
    ("core/quality_gate/property_check.py", "_parse_invariant_table"):
        "SAD property tables (invariant / applies_to / fulfill_phase)",
    ("harness/harness_bridge.py", "_parse_nfr_fr_xref"):
        "SRS NFR→FR cross-reference rows",
    ("harness/git_strategy.py", "_gap_register_summary"):
        "the gap register, not a spec",
    ("harness/ssi/scripts/verify.py", "count_diff_lines"):
        "unified-diff hunk headers, not markdown",
    ("scripts/plangen/artifact_parsers.py", "parse_srs_fr_nfr_xref"):
        "SRS NFR→FR cross-reference rows",
    ("scripts/extract_deferred_index.py", "_cells"):
        "the 明列不做 tables in docs/PROPOSAL_ADJUDICATIONS.md. Never a spec: "
        "it reads adjudication decisions, and it only ever `.strip()`s, so "
        "every cell it emits stays a substring of the ledger line it came from "
        "— which is the property tests/test_deferred_index.py asserts",
}


def test_the_registry_names_every_table_reader_in_the_tree():
    """Exemption-by-omission: a third declaration reader must land here.

    Round 36 站2's completeness pair, applied to parsers instead of workflow
    files: the parity test above only reaches the readers it was told about,
    so a new hand-rolled one would be exactly as unguarded as the bridge was
    before this station. Every cell-splitting function in the tree is either
    a registered reader or carries a one-line statement of what other table
    it reads.
    """
    import ast
    import subprocess
    from pathlib import Path

    repo = Path(__file__).resolve().parents[1]
    files = subprocess.run(
        ["git", "ls-files", "*.py"], cwd=repo, capture_output=True, text=True,
    ).stdout.split()

    splitter = re.compile(r"""split\(\s*r?["']\\?\|["']\s*\)""")
    found = set()
    for rel in files:
        if rel.startswith("tests/"):
            continue
        try:
            source = (repo / rel).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if not splitter.search(source):
            continue
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        lines = source.splitlines()
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            body = "\n".join(lines[node.lineno - 1:node.end_lineno])
            code = re.sub(r'("""|\'\'\')(?:.|\n)*?\1', "", body)
            if splitter.search(code):
                found.add((rel, node.name))

    unregistered = found - set(_TABLE_CELL_SPLITTERS)
    assert not unregistered, (
        f"unregistered markdown-table reader(s): {sorted(unregistered)}. If it "
        f"reads TEST_SPEC declaration rows, add it to TEST_SPEC_READERS so the "
        f"parity test holds it to the same answer; otherwise register it in "
        f"_TABLE_CELL_SPLITTERS with the table it reads."
    )

    stale = set(_TABLE_CELL_SPLITTERS) - found
    assert not stale, (
        f"registry names function(s) that no longer split table cells: "
        f"{sorted(stale)} — a stale entry is a free pass for whatever takes "
        f"the name next (Round 36 站2)."
    )

    declared_readers = {
        name.split(".")[-1] for name, _ in TEST_SPEC_READERS
    } | {"_via_spec_coverage", "_via_harness_bridge"}
    for (rel, fn), note in _TABLE_CELL_SPLITTERS.items():
        if note != "reader":
            continue
        assert fn in declared_readers or rel.endswith("spec_coverage.py"), (
            f"{rel}::{fn} is marked a declaration reader but nothing in "
            f"TEST_SPEC_READERS exercises it")
