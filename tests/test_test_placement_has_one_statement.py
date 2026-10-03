"""Round 113 站D — where a test lives is stated once, by the rule the gate enforces.

The audit counted 21 integration test files flat in one directory of taskq-sol,
against the SAD's planned `integration/{http,migrations,runtime}/` split. The
count came from TEST_INVENTORY.yaml's `test_file` field — and that field has no
reader anywhere in the framework (the inventory parsers read tc_id / nfr /
layer / test_function; requirement_traceability keeps its own state). Measured
on the corpus, the declared paths are almost never where the tests ended up:
taskq-final 0/28, taskq-new 0/29, taskq-redo 0/46, taskq-cc 2/48. What decides
placement is Gate 1: `_check_fr_test_file_exists` requires
`<test dir>/test_frNN.py`, and `test_suite_run.select_fr_outcomes` attributes
any other test to the FR by its `test_frNN` name prefix.

Two statements contradicted that rule. The template asked P1 — before any
architecture exists — for a `test_file` "where it will live"; and the Phase 3
prompt named "TEST_SPEC.md §FR-NN (test file list)" as "the canonical source of
truth for test placement" (TEST_SPEC has no such list) and told the agent the
`test_frNN.py` convention "is no longer required" — which Gate 1 still blocks
on. The field goes; the prompt states the gate's rule.
"""

from __future__ import annotations

from pathlib import Path

import yaml

import harness_cli  # noqa: F401  entry-first load order

_TEMPLATE = Path(__file__).resolve().parent.parent / "templates" / "TEST_INVENTORY.yaml"


def test_the_template_asks_for_no_placement_nobody_reads():
    tests = yaml.safe_load(_TEMPLATE.read_text(encoding="utf-8"))["test_inventory"]["tests"]
    assert tests and all("test_file" not in t for t in tests)
    assert "test_file" not in _TEMPLATE.read_text(encoding="utf-8")


def test_the_phase_3_prompt_states_the_rule_gate_1_enforces():
    from scripts.workflowgen.spec_phase3 import generate_phase3

    js = generate_phase3()
    assert "canonical source of truth for test placement" not in js
    assert "is no longer required" not in js
    assert "test_fr' + frNum + '.py" in js


def test_gate_1_still_requires_the_fr_test_file(tmp_path):
    # The rule the prompt now states, read off the gate itself.
    from cli.gate_cmds import _check_fr_test_file_exists

    tests = tmp_path / "03-development" / "tests" / "integration"
    tests.mkdir(parents=True)
    (tests / "test_fr01_tasks.py").write_text("def test_fr01_x():\n    assert True\n")
    ok, _ = _check_fr_test_file_exists(tmp_path, "FR-01")
    assert not ok
    (tests.parent / "test_fr01.py").write_text("def test_fr01_y():\n    assert True\n")
    ok, _ = _check_fr_test_file_exists(tmp_path, "FR-01")
    assert ok
