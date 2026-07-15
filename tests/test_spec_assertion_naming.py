"""Regression tests for spec_assertion_naming (v2.13.0 — FR-05 P3 lesson).

The naming scan protects P3 from TEST_SPEC rows that would force a
TDD-RED agent to write ``foo = "bar"`` and silently shadow a stdlib
module. Each test exercises one branch of the predicate parser or one
collision class.
"""
from __future__ import annotations

from core.quality_gate.spec_assertion_naming import (
    BUILTIN_NAMES,
    RESERVED_NAMES,
    STDLIB_MODULE_NAMES,
    extract_predicate_lhs,
    scan_stdlib_name_collisions,
)


# ---------------------------------------------------------------------------
# extract_predicate_lhs
# ---------------------------------------------------------------------------

def test_lhs_simple_equality() -> None:
    assert extract_predicate_lhs('json == "true"') == "json"
    assert extract_predicate_lhs('command == "echo hi"') == "command"
    assert extract_predicate_lhs('subcommand == "status"') == "subcommand"


def test_lhs_inequality() -> None:
    assert extract_predicate_lhs('json != "false"') == "json"


def test_lhs_whitespace_tolerant() -> None:
    assert extract_predicate_lhs("  command  ==  'echo hi'  ") == "command"
    assert extract_predicate_lhs("json==\"true\"") == "json"


def test_lhs_attribute_chain_returns_head() -> None:
    # `obj.attr` → head is `obj`. (obj is not reserved.)
    assert extract_predicate_lhs("result.exit_code == 0") == "result"


def test_lhs_function_call_keeps_call_name() -> None:
    # `len(x) > N` returns `len` (the call target).
    assert extract_predicate_lhs("len(command) > 0") == "len"


def test_lhs_none_for_non_equality_predicate() -> None:
    # Predicates without == or != fall outside the scan's scope.
    assert extract_predicate_lhs("a in [1, 2, 3]") is None
    assert extract_predicate_lhs("True") is None
    assert extract_predicate_lhs("") is None
    assert extract_predicate_lhs("len(command) > 0") == "len"  # len is a function-call LHS, kept


# ---------------------------------------------------------------------------
# RESERVED_NAMES shape
# ---------------------------------------------------------------------------

def test_reserved_names_are_nonempty_and_disjoint() -> None:
    # Both sets must be non-empty (otherwise the gate would be a no-op).
    assert STDLIB_MODULE_NAMES, "stdlib set should not be empty"
    assert BUILTIN_NAMES, "builtin set should not be empty"
    # And they MUST be disjoint — no name should appear in both buckets.
    overlap = STDLIB_MODULE_NAMES & BUILTIN_NAMES
    assert not overlap, f"reserved sets overlap: {overlap}"
    # RESERVED is the union.
    assert RESERVED_NAMES == STDLIB_MODULE_NAMES | BUILTIN_NAMES


def test_reserved_contains_fr05_empirical_triggers() -> None:
    # Locked-in: the FR-05 P3 2026-07-16 incident was triggered by `json`.
    assert "json" in RESERVED_NAMES
    assert "os" in RESERVED_NAMES
    assert "sys" in RESERVED_NAMES
    assert "time" in RESERVED_NAMES
    assert "path" in RESERVED_NAMES
    assert "type" in RESERVED_NAMES
    assert "id" in RESERVED_NAMES


# ---------------------------------------------------------------------------
# scan_stdlib_name_collisions
# ---------------------------------------------------------------------------

def test_scan_flags_json_collision() -> None:
    """The exact FR-05 case-#2 trigger."""
    parsed = {
        "FR-05": ([], [
            type("A", (), {"rule_id": "FR05-json-flag-set", "predicate": 'json == "true"'})(),
        ]),
    }
    hits = scan_stdlib_name_collisions(parsed)
    assert len(hits) == 1
    fr_id, rule_id, predicate, suggested = hits[0]
    assert fr_id == "FR-05"
    assert rule_id == "FR05-json-flag-set"
    assert predicate == 'json == "true"'
    assert "flag" in suggested  # heuristic adds `_flag` for `json`


def test_scan_flags_each_collision_once() -> None:
    parsed = {
        "FR-99": ([], [
            type("A", (), {"rule_id": "x1", "predicate": "os == 'linux'"})(),
            type("A", (), {"rule_id": "x2", "predicate": "sys == 'Linux'"})(),
            type("A", (), {"rule_id": "x3", "predicate": "type == 'file'"})(),
        ]),
    }
    hits = scan_stdlib_name_collisions(parsed)
    assert {(r, p) for _, r, p, _ in hits} == {
        ("x1", "os == 'linux'"),
        ("x2", "sys == 'Linux'"),
        ("x3", "type == 'file'"),
    }


def test_scan_ignores_clean_predicates() -> None:
    parsed = {
        "FR-01": ([], [
            type("A", (), {"rule_id": "command-nonempty", "predicate": 'command != ""'})(),
            type("A", (), {"rule_id": "id-pattern", "predicate": "len(task_id) == 8"})(),
        ]),
        "FR-02": ([], [
            type("A", (), {"rule_id": "exit-zero", "predicate": "result.exit_code == 0"})(),
        ]),
    }
    assert scan_stdlib_name_collisions(parsed) == []


def test_scan_empty_parsed() -> None:
    assert scan_stdlib_name_collisions({}) == []
    assert scan_stdlib_name_collisions({"FR-X": ([], [])}) == []


def test_scan_skips_assertions_without_predicate_attr() -> None:
    """Defensive: SubAssertion dataclass might miss fields in tests."""
    parsed = {
        "FR-77": ([], [
            type("A", (), {})(),  # no rule_id / no predicate
        ]),
    }
    # Should NOT crash; should treat missing fields as non-collisions.
    assert scan_stdlib_name_collisions(parsed) == []


def test_scan_non_equality_predicate_lhs_not_collision() -> None:
    """`len(command) > 0` has LHS `len` which is NOT in RESERVED_NAMES."""
    parsed = {
        "FR-04": ([], [
            type("A", (), {"rule_id": "ttl-fresh", "predicate": "len(command) > 0"})(),
        ]),
    }
    assert scan_stdlib_name_collisions(parsed) == []