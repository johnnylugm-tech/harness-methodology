"""Round 16 站2: core/failure_modes.py — MAST-aligned failure classifier.

Every rule in FAILURE_MODE_RULES gets one hit-fixture (predicate True) and
one miss-fixture (predicate False on an otherwise-plausible neighbor entry).
The completeness meta-test at the bottom fails loudly if a rule is added to
the registry without a matching fixture pair here — the same "registry vs.
test" parity discipline used by tests/test_preflight_registry.py.
"""

from core.failure_modes import (
    ALL_CATEGORIES,
    FAILURE_MODE_RULES,
    UNCLASSIFIED,
    MastCategory,
    classify_entry,
    summarize,
)

# One (hit_entry, miss_entry) pair per mode_id, keyed for the completeness
# meta-test below. miss_entry must be a plausible spawn-log entry that does
# NOT trip this rule (not just an empty dict — that would pass trivially).
_FIXTURES_BY_MODE_ID = {
    "destructive_edit_or_mutator_marker": (
        {"status": "REGRESSION_GUARD", "regression_flags": {"lines_removed>50": [["a.py", 80]]}},
        {"status": "ERROR", "error_class": "EXECUTION_ERROR"},
    ),
    "semantic_noop_termination": (
        {"status": "ERROR", "error_class": "EXECUTION_ERROR", "inner_status": "AWAITING_CONFIRMATION"},
        {"status": "ERROR", "error_class": "EXECUTION_ERROR", "inner_status": "complete"},
    ),
    "commit_required_step_no_commit": (
        {"status": "ERROR", "error_class": "EXECUTION_ERROR",
         "output": "Commit-required step 'TDD-GREEN' returned empty commit (status='complete')"},
        {"status": "ERROR", "error_class": "EXECUTION_ERROR", "output": "some other failure text"},
    ),
    "structural_env_breakage": (
        {"status": "ERROR", "error_class": "STRUCTURAL"},
        {"status": "ERROR", "error_class": "INFRA_ERROR"},
    ),
    "infra_error_transient": (
        {"status": "ERROR", "error_class": "INFRA_ERROR"},
        {"status": "ERROR", "error_class": "EXECUTION_ERROR"},
    ),
    "dispatch_timeout": (
        {"status": "TIMEOUT"},
        {"status": "ERROR", "error_class": "EXECUTION_ERROR"},
    ),
}


class TestPerRuleFixtures:
    def test_every_rule_has_a_hit_and_miss_fixture(self):
        registry_mode_ids = {rule.mode_id for rule in FAILURE_MODE_RULES}
        assert registry_mode_ids == set(_FIXTURES_BY_MODE_ID)

    def test_hit_fixtures_classify_to_their_own_mode(self):
        for mode_id, (hit_entry, _miss_entry) in _FIXTURES_BY_MODE_ID.items():
            result = classify_entry(hit_entry)
            assert result["mode_id"] == mode_id, (
                f"hit fixture for {mode_id!r} classified as {result['mode_id']!r}"
            )

    def test_miss_fixtures_do_not_classify_to_that_mode(self):
        for mode_id, (_hit_entry, miss_entry) in _FIXTURES_BY_MODE_ID.items():
            result = classify_entry(miss_entry)
            assert result["mode_id"] != mode_id, (
                f"miss fixture for {mode_id!r} unexpectedly classified as {mode_id!r}"
            )


class TestUnclassifiedFloor:
    def test_empty_entry_is_unclassified(self):
        result = classify_entry({})
        assert result["mode_id"] == UNCLASSIFIED
        assert result["mast_category"] is None

    def test_unclassified_preserves_original_fields_for_triage(self):
        entry = {"status": "complete", "error_class": None}
        result = classify_entry(entry)
        assert result["mode_id"] == UNCLASSIFIED
        assert result["original_error_class"] is None
        assert result["original_status"] == "complete"

    def test_unrecognized_error_class_is_unclassified_not_miscounted(self):
        # A future error_class value the registry doesn't know about yet must
        # not silently fall into an existing bucket.
        entry = {"status": "ERROR", "error_class": "SOME_FUTURE_CLASS"}
        result = classify_entry(entry)
        assert result["mode_id"] == UNCLASSIFIED


class TestRegistryShape:
    def test_every_rule_mast_category_is_one_of_the_four_constants(self):
        for rule in FAILURE_MODE_RULES:
            assert rule.mast_category in ALL_CATEGORIES

    def test_mode_ids_are_unique(self):
        mode_ids = [rule.mode_id for rule in FAILURE_MODE_RULES]
        assert len(mode_ids) == len(set(mode_ids))

    def test_inter_agent_and_verification_have_no_rules_yet(self):
        # Honest documentation of the current gap (see module docstring):
        # neither B-review escalation outcomes nor gate PASS/FAIL verdicts
        # are persisted onto a spawn-log entry today. This test pins that
        # fact so a future round that adds such a rule updates this test
        # deliberately instead of it going unnoticed.
        categories_in_use = {rule.mast_category for rule in FAILURE_MODE_RULES}
        assert MastCategory.INTER_AGENT not in categories_in_use
        assert MastCategory.VERIFICATION not in categories_in_use


class TestSummarize:
    def test_empty_list_yields_none_percentage_not_zero(self):
        result = summarize([])
        assert result["total"] == 0
        assert result["unclassified_count"] == 0
        assert result["unclassified_pct"] is None

    def test_mixed_entries_counts_and_rolls_up_by_category(self):
        entries = [
            {"status": "ERROR", "error_class": "INFRA_ERROR"},
            {"status": "ERROR", "error_class": "INFRA_ERROR"},
            {"status": "TIMEOUT"},
            {"status": "REGRESSION_GUARD", "regression_flags": {"xx_markers_introduced": ["b.py"]}},
            {"status": "complete"},  # unclassified — a plain success entry
        ]
        result = summarize(entries)
        assert result["total"] == 5
        assert result["mode_counts"]["infra_error_transient"] == 2
        assert result["mode_counts"]["dispatch_timeout"] == 1
        assert result["mode_counts"]["destructive_edit_or_mutator_marker"] == 1
        assert result["mode_counts"][UNCLASSIFIED] == 1
        assert result["category_counts"][MastCategory.INFRA] == 3
        assert result["category_counts"][MastCategory.SPECIFICATION] == 1
        assert result["category_counts"][UNCLASSIFIED] == 1
        assert result["unclassified_count"] == 1
        assert result["unclassified_pct"] == 20.0
