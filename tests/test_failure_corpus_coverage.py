"""Round 19 站1 — the failure classifier, checked against real run data.

core/failure_modes.py's own suite (tests/test_failure_modes.py) proves every
rule has a hit fixture and a miss fixture. That is a completeness check on the
RULES. It cannot say anything about whether real failures are covered, because
its fixtures are hand-authored by whoever wrote the rules: both sides of the
comparison come from the same head, so the suite stays green no matter how far
the rules drift from the data.

Two live defects sat behind that closed loop until real logs were read:

  * `_is_missing_required_commit` read `entry["output"]`. The logger writes that
    value under `error_output`. The rule therefore returned False for all 91 of
    taskq's entries, including the one starting "Commit-required step". Its
    fixture used `output`, so the rule and its test agreed with each other and
    neither agreed with the log.
  * `_is_semantic_noop` read `entry["inner_status"]`, which _log_dispatch never
    wrote out at all. Same result: a rule that could not fire.

This module closes the loop by testing against corpora exported from real runs
(`harness_cli.py export-failure-corpus`), which carry de-identified failure
SHAPES — raw signals only, no verdicts, no session ids, no paths. See
cli/report_cmds.py's _CORPUS_FIELDS for why `error_class` is deliberately absent:
keeping it would replay the verdict a past dispatch stamped, so a fix to the
signature registry could never be exercised end-to-end.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from core.failure_modes import UNCLASSIFIED, classify_entry

CORPUS_DIR = Path(__file__).resolve().parent / "fixtures" / "failure_corpus"

# Every corpus file, with where it came from. Adding a run's corpus here is the
# whole point of the mechanism — see docs/OBSERVABILITY.md.
CORPORA: dict[str, str] = {
    "taskq_p3.jsonl": "taskq P3 (2026-07-24..26), 19 failed dispatches -> 6 shapes",
    "integration_test.jsonl": "integration-test harness E2E, 3 shapes",
}

# The ratchet. 0 means every real failure shape on record is explained by some
# rule. It may only ever go DOWN; a rise must be justified in the same commit,
# the same contract tests/test_file_size_ratchet.py uses.
#
# Importing a corpus with an unseen failure phrasing WILL fail this test. That
# is the mechanism working, not a flake: the fix is to add a rule (or widen a
# signature) so the new shape classifies, exactly as Round 19 站1 did for
# "stream idle timeout" and "session limit".
MAX_UNCLASSIFIED = 0

# Round 26 — the ratchet above only asks "did SOME rule match?". It cannot see a
# WRONG match, and one had been sitting here since Round 19: integration_test's
# `status='INFRA_BLOCKED'` entry classified as `commit_required_step_no_commit`
# (MAST specification), which sends a reader — and the fix loop — looking for an
# agent-logic defect when the truth is an unmet precondition no code change can
# resolve. The corpus held the evidence for seven rounds and read green the whole
# time, because "classified" and "classified correctly" are different questions
# and only the first was being asked. taskq-plus FR-05 then paid for it live on
# 2026-07-30: a 51-turn CODE-FIX dispatched at an unresolvable SAB phantom.
#
# So the judgement a human made about each shape is written down, per entry, in
# file order. Corpora are append-only (export-failure-corpus appends), so index
# alignment is stable and importing a new shape forces someone to state what it
# means — the same forcing function MAX_UNCLASSIFIED applies to coverage.
EXPECTED_MODES: dict[str, list[str]] = {
    "taskq_p3.jsonl": [
        "dispatch_timeout",                     # subtype=error_max_turns
        "dispatch_timeout",                     # Agent timed out after 600s
        "destructive_edit_or_mutator_marker",   # regression_flags set
        "commit_required_step_no_commit",       # TDD-IMPROVE, inner status DONE
        "infra_error_transient",                # stream idle timeout
        "infra_error_transient",                # session limit
    ],
    "integration_test.jsonl": [
        "dispatch_timeout",                     # subtype=error_max_turns
        "infra_precondition_blocked",           # GATE1, inner status INFRA_BLOCKED
        "commit_required_step_no_commit",       # GATE1, inner status <unset>
    ],
}


def _load(name: str) -> list[dict]:
    path = CORPUS_DIR / name
    assert path.is_file(), f"missing corpus {path} — regenerate with export-failure-corpus"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _all_entries() -> list[dict]:
    return [entry for name in CORPORA for entry in _load(name)]


@pytest.mark.parametrize("name", sorted(CORPORA))
def test_corpus_file_is_present_and_non_empty(name: str):
    assert _load(name), f"{name} is empty — an empty corpus is a dead guard"


def test_real_failure_shapes_are_classified():
    entries = _all_entries()
    unexplained = [
        e for e in entries if classify_entry(e)["mode_id"] == UNCLASSIFIED
    ]
    assert len(unexplained) <= MAX_UNCLASSIFIED, (
        f"{len(unexplained)} real failure shape(s) match no rule "
        f"(ratchet allows {MAX_UNCLASSIFIED}). Add or widen a rule in "
        f"core/failure_modes.py — do NOT raise the ratchet to make this pass "
        f"unless the shape genuinely cannot be classified deterministically:\n"
        + "\n".join(f"  - {e.get('status')}: {str(e.get('error_output'))[:110]}" for e in unexplained)
    )


@pytest.mark.parametrize("name", sorted(CORPORA))
def test_real_failure_shapes_are_classified_CORRECTLY(name: str):
    """The other question: not "did a rule match" but "did the RIGHT one".

    A wrong match is worse than no match — UNCLASSIFIED is honest, while
    `specification` on an infrastructure blocker actively misdirects the reader
    and the fix loop. Round 19's ratchet could not tell them apart.
    """
    expected = EXPECTED_MODES[name]
    entries = _load(name)
    assert len(entries) == len(expected), (
        f"{name} has {len(entries)} entries but {len(expected)} expectations. A newly "
        f"imported shape needs its mode_id appended to EXPECTED_MODES — state what the "
        f"shape MEANS, do not pad the list to make this pass."
    )
    wrong = [
        (i, expected[i], classify_entry(e)["mode_id"], str(e.get("error_output"))[:90])
        for i, e in enumerate(entries)
        if classify_entry(e)["mode_id"] != expected[i]
    ]
    assert not wrong, "\n".join(
        f"  {name}[{i}]: expected {exp!r}, got {got!r} — {evidence}"
        for i, exp, got, evidence in wrong
    )


def test_every_corpus_has_per_entry_expectations():
    """A corpus with no expectations would pass the test above vacuously."""
    missing = sorted(set(CORPORA) - set(EXPECTED_MODES))
    assert not missing, (
        f"corpus file(s) {missing} carry no EXPECTED_MODES entry, so nothing checks "
        f"whether their shapes classify correctly — only that they classify."
    )


def test_a_successful_dispatch_is_never_classified_as_a_failure():
    """The other half of the ratchet, and a real regression pin.

    _log_dispatch writes `error_output` on EVERY entry — on success it holds the
    sub-agent's ordinary reply. When _effective_error_class first learned to
    re-derive EXECUTION_ERROR from that text, it did so for all entries, and
    three ordinary taskq successes (including a plain "Committed successfully.")
    matched an INFRA signature by accident. A classifier that invents failures
    is worse than one that misses them: the corpus ratchet above would still
    read 0, while the report grew phantom infra incidents.
    """
    successes = [
        {"status": "complete", "error_output": "Committed successfully.\n\n"
                                               '```json\n{"status": "DONE", "commit": "d401f5a"}```'},
        {"status": "complete", "error_output": "RED state confirmed (Collection Error: "
                                               "ModuleNotFoundError for `pkg.executor`)."},
        {"status": "COMPLETED", "error_output": ""},
        {"status": "PREFLIGHT_OK", "error_output": "pytest_ok=True git_ok=True canary_ok=True"},
    ]
    for entry in successes:
        assert classify_entry(entry)["mode_id"] == UNCLASSIFIED, (
            f"a successful dispatch was classified as a failure mode: {entry}"
        )


def test_corpus_carries_no_identifying_fields():
    """The corpus records shapes, not logs. Anything that names a session, a
    person, a path or a moment must not be in here — that is what makes it safe
    to keep one project's observed failures in another repo's fixtures."""
    banned = {"session_id", "task", "timestamp", "phase", "fr_id", "project"}
    for name in CORPORA:
        for entry in _load(name):
            leaked = banned & set(entry)
            assert not leaked, f"{name} leaks identifying field(s): {sorted(leaked)}"


def test_corpus_omits_error_class_so_the_signature_registry_is_exercised():
    """`error_class` is a verdict, not a signal. If corpora carried it, the
    fixtures would replay each dispatch's original classification and a fix to
    _INFRA_ERROR_RE would be invisible here — which is precisely what happened
    before Round 19 站1: after teaching the regex "stream idle timeout", the 12
    stream-idle log entries still read UNCLASSIFIED, because each was stamped
    EXECUTION_ERROR when it was written."""
    for name in CORPORA:
        for entry in _load(name):
            assert "error_class" not in entry, (
                f"{name} carries error_class — re-export with export-failure-corpus"
            )


def _fields_read_by_rules() -> set[str]:
    """Every `entry.get("X")` / `entry["X"]` key read inside core/failure_modes.py."""
    source = (Path(__file__).resolve().parent.parent / "core" / "failure_modes.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    fields: set[str] = set()
    for node in ast.walk(tree):
        # entry.get("X")
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "entry"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            fields.add(node.args[0].value)
        # entry["X"]
        elif (
            isinstance(node, ast.Subscript)
            and isinstance(node.value, ast.Name)
            and node.value.id == "entry"
            and isinstance(node.slice, ast.Constant)
            and isinstance(node.slice.value, str)
        ):
            fields.add(node.slice.value)
    return fields


# Fields a rule may read that the corpus cannot contain, each with why. Kept
# tiny on purpose: every entry here is a field whose real-world shape this
# module cannot verify, so it is exactly where the next dead rule would hide.
FIELDS_ABSENT_FROM_CORPUS: dict[str, str] = {
    "error_class": "a dispatch-time verdict, deliberately stripped on export so "
                   "the signature registry is re-run instead of replayed",
    "inner_status": "the write side landed in Round 19 站1 (_log_dispatch now "
                    "emits it); every corpus on record predates that commit, so "
                    "no exported shape carries it yet. REMOVE this entry once a "
                    "post-Round-19 run is exported — until then _is_semantic_noop "
                    "remains unverified against real data.",
    "transport_error": "the write side landed in Round 41 站3 (_log_dispatch "
                       "emits it, and cli/report_cmds._CORPUS_FIELDS exports "
                       "it); every corpus on record predates that commit. Those "
                       "entries take _effective_error_class's documented "
                       "fallback to `error_output`, which is why the classifier "
                       "still explains all of them. REMOVE this entry once a "
                       "post-Round-41 run is exported — until then the "
                       "provenance split is verified only by unit tests "
                       "(tests/test_transport_vs_semantic_failure.py), not "
                       "against real data.",
}


def test_rules_only_read_fields_that_real_log_entries_carry():
    """The structural check that would have caught both dead rules on day one.

    A rule reading a key no log entry has is not a subtle bug — it is a rule
    that can never fire. The authority for "what a log entry carries" here is
    the corpus itself (real exported entries), NOT a hand-written list, so this
    assertion cannot drift the way the rules did.
    """
    corpus_fields = {k for entry in _all_entries() for k in entry}
    allowed = corpus_fields | set(FIELDS_ABSENT_FROM_CORPUS)
    unknown = _fields_read_by_rules() - allowed
    assert not unknown, (
        f"core/failure_modes.py reads entry field(s) that no real log entry "
        f"carries: {sorted(unknown)}. Such a rule can never match. Either fix "
        f"the field name (the log writes `error_output`, not `output`), make "
        f"core/agent_spawner._log_dispatch actually write the field, or "
        f"register it in FIELDS_ABSENT_FROM_CORPUS with a reason."
    )


def test_every_absent_field_entry_carries_a_reason():
    for field, reason in FIELDS_ABSENT_FROM_CORPUS.items():
        assert reason.strip(), f"{field} registered with no reason"
