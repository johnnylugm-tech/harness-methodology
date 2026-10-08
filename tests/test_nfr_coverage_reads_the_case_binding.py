"""Round 117 — an NFR is covered by the TEST_SPEC case bound to its criteria, delivered.

The trace dimension's 4c credited any passing test whose body contained the
string `NFR-XX`; the P3 prompt told implementers to write that string from an
FR↔NFR table (TRACEABILITY_MATRIX §5, SRS §2 `NFR Association`) that no
project had. The binding a reader can check is the one Phase 2 declares and
Agent B reviews: an AC id in the case's declaration row, or in a
sub-assertion whose `applies_to` names the case.
"""
from __future__ import annotations

import json
from pathlib import Path

from core.quality_gate.ac_case_binding import ac_case_bindings, nfr_case_coverage, resolve
from core.quality_gate.artifact_consistency import check_ac_test_spec_coverage
from tests.support.nfr_project import make_nfr_project

_DECL = "| # | Test Function | Inputs | Type | Derivation |\n|---|---|---|---|---|\n"


def _spec(root: Path, body: str) -> None:
    (root / "02-architecture" / "TEST_SPEC.md").write_text("# TEST_SPEC.md\n\n" + body, encoding="utf-8")


def test_a_citation_outside_every_case_binds_nothing(tmp_path):
    make_nfr_project(tmp_path, {"NFR-01": ["AC-N1.1"]}, [])
    _spec(tmp_path, "### NFR-01: perf\n\nCase 1 disposes AC-N1.1.\n\n" + _DECL
          + '| 1 | `test_nfr01_p95` | x="1" | nfr_pattern | NP-06 |\n')
    assert ac_case_bindings(tmp_path) == {}
    assert [v.check_type for v in check_ac_test_spec_coverage(tmp_path)] == ["ac_no_test_case"]


def test_a_sub_assertion_binds_the_case_its_applies_to_names(tmp_path):
    make_nfr_project(tmp_path, {"FR-10": ["AC-10.5"]}, [])
    _spec(tmp_path, "### FR-10: errors\n\n" + _DECL
          + '| 1 | `test_fr10_ok` | x="1" | happy_path | Q1 |\n'
          + '| 2 | `test_fr10_status_422` | x="1" | validation | Q2 |\n\n'
          + "| rule_id | predicate | applies_to |\n|---|---|---|\n"
          + '| AC10.5-422-status | trigger_status == "422" | 2 |\n')
    assert ac_case_bindings(tmp_path) == {"AC-10.5": {"test_fr10_status_422"}}


def test_a_sub_assertion_table_under_its_own_heading_binds_the_table_above(tmp_path):
    """taskq-api: `### NFR Sub-assertions` below `### NFR Integration`."""
    make_nfr_project(tmp_path, {"NFR-03": ["AC-N3.1"]}, [])
    _spec(tmp_path, "### NFR Integration\n\n" + _DECL
          + '| 1 | `test_nfr03_readyz` | x="1" | integration | NFR-03 |\n\n'
          + "### NFR Sub-assertions\n\n| rule_id | predicate | applies_to |\n|---|---|---|\n"
          + '| AC-N3.1-readyz-503 | status == "503" | 1 |\n')
    assert ac_case_bindings(tmp_path) == {"AC-N3.1": {"test_nfr03_readyz"}}


def test_applies_to_a_case_nobody_declared_binds_nothing(tmp_path):
    make_nfr_project(tmp_path, {"NFR-01": ["AC-N1.1"]}, [])
    _spec(tmp_path, "### NFR-01: perf\n\n" + _DECL
          + '| 1 | `test_nfr01_p95` | x="1" | nfr_pattern | NP-06 |\n\n'
          + "| rule_id | predicate | applies_to |\n|---|---|---|\n| AC-N1.1-p95 | p95 < 30 | 7 |\n")
    assert ac_case_bindings(tmp_path) == {}


def test_trailing_segments_are_dropped_only_to_reach_a_declared_id():
    assert resolve("AC-10.5-422", {"AC-10.5"}) == "AC-10.5"
    assert resolve("AC-04-3", {"AC-04-3"}) == "AC-04-3"
    assert resolve("AC-7", {"AC-10.5"}) == "AC-7"


def test_a_case_number_belongs_to_the_most_recent_declaration_table(tmp_path):
    """taskq-sol line 888: a sub-assertion table follows two declaration tables."""
    make_nfr_project(tmp_path, {"NFR-02": ["AC-N2.1", "AC-N2.2"]}, [])
    _spec(tmp_path, "### NFR Deferred\n\n" + _DECL
          + '| 1 | `test_nfr02_bandit` | x="1" | static | NFR-02 |\n'
          + '| 2 | `test_nfr02_sql_grep` | x="1" | static | NFR-02 |\n\n'
          + "### Infrastructure\n\n" + _DECL
          + '| 1 | `test_app_wires_database` | x="1" | integration | Q7 |\n\n'
          + "| rule_id | predicate | applies_to |\n|---|---|---|\n"
          + '| AC-N2.1-wiring | component == "db" | 1 |\n| AC-N2.2-x | x == "1" | 2 |\n')
    assert ac_case_bindings(tmp_path) == {"AC-N2.1": {"test_app_wires_database"}}


def test_a_typescript_project_delivers_by_test_title(tmp_path):
    make_nfr_project(tmp_path, {"NFR-01": ["AC-N1.1"]}, [("test_nfr01_p95", "AC-N1.1")])
    (tmp_path / "03-development" / "tests" / "test_nfr.py").unlink()
    (tmp_path / ".methodology").mkdir()
    (tmp_path / ".methodology" / "state.json").write_text(json.dumps({"language": "typescript"}))
    (tmp_path / "03-development" / "tests" / "nfr.test.ts").write_text(
        "it('test_nfr01_p95', () => { expect(1).toBe(1); });\n", encoding="utf-8")
    assert nfr_case_coverage(tmp_path)["pct"] == 100.0


def test_the_matrix_and_4c_read_one_join(tmp_path):
    """Round 33: the rendered NFR section and the gate number are one reading."""
    from scripts.build_traceability import build_traceability

    make_nfr_project(tmp_path, {"NFR-01": ["AC-N1.1"], "NFR-02": ["AC-N2.1"], "NFR-99": []},
                     [("test_nfr01_p95", "AC-N1.1"), ("test_nfr02_sql", "AC-N2.1")],
                     delivered=["test_nfr01_p95"])
    (tmp_path / "02-architecture" / "SAD.md").write_text("FR-01: stub\n", encoding="utf-8")
    per_nfr = nfr_case_coverage(tmp_path)["per_nfr"]
    nfr_data = build_traceability(tmp_path).nfr_data
    assert nfr_data["nfr_ids"] == ["NFR-01", "NFR-02", "NFR-99"], "every declared NFR keeps its row"
    assert nfr_data["nfr_test_coverage"] == {**{n: v["tests"] for n, v in per_nfr.items()}, "NFR-99": []}
    assert nfr_data["nfr_absent_witnesses"] == {**{n: v["absent"] for n, v in per_nfr.items()}, "NFR-99": []}


def test_no_workflow_or_plan_sends_an_agent_to_the_missing_association_source(tmp_path):
    from scripts.generate_full_plan import generate_full_plan
    from scripts.workflowgen.generate_workflows import generate
    from tests.test_plangen_golden import _fixture_project

    import re

    banned = re.compile(r"TRACEABILITY_MATRIX\.md §5|NFR Association|(?<!#)# NFR-(?:XX|\d)")
    texts = [generate(phase) for phase in range(1, 9)]
    proj = _fixture_project(tmp_path)
    texts += [generate_full_plan(phase, proj, None, dynamic=False) or "" for phase in range(1, 10)]
    hits = [m.group(0) for t in texts for m in banned.finditer(t)]
    assert not hits, hits
