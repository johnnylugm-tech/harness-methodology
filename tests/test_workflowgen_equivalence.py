"""Structural equivalence lock for workflowgen migrations (Round 11).

When a phase's hand-maintained `.claude/workflows/phaseN-*.js` is replaced
by workflowgen-generated output, this is the proof the migration didn't
silently change what the workflow DOES: not byte-equality (the generator
is explicitly allowed to simplify/consolidate prose — see
docs/WORKFLOW_ALIGNMENT_AUDIT.md's "not a full-fidelity diff" note), but
three structural assertions comparing the file as it existed immediately
BEFORE migration against the file as it existed immediately AFTER the
equivalence-only migration commit (both read via `git show <sha>:<path>` —
actual historical commits, not hardcoded literal snapshots, and not
`generate(phase)` against live HEAD):

  (a) meta.phases titles — same set, same order
  (b) agent() label set — same set (a rename requires an explicit mapping
      entry here, not a silent drop)
  (c) harness_cli.py command set — same set (scripts/workflow_audit/
      extract.py's precision rules — see that module's docstring)

Both ends are pinned to fixed commits, not `generate(phase)`, because this
test's job is to prove a SPECIFIC migration commit was equivalence-only —
a historical fact, permanently true once proven. It deliberately does NOT
re-check that property against every later commit: the Round 11 plan's
two-commit discipline ("遷移不修 gap") means the very next commit after a
migration is often a deliberate gap-fix (e.g. station2b added ORCH-POST/
ENV-CHECK commands+labels that never existed in either the pre-migration
file or the equivalence-locked migration) — comparing against live HEAD
would make this test permanently red the moment any phase's known gaps
start closing. Drift in the generator's current output is instead caught
by tests/test_workflowgen_golden.py (mechanical) and plan-conformance by
tests/test_workflow_plan_alignment.py (semantic, shrinks as gaps close).
"""
from __future__ import annotations

import subprocess

import pytest

from scripts.workflow_audit.extract import (
    extract_js_agent_labels,
    extract_js_phases,
    extract_js_subcommands,
    known_subcommands,
)

# {phase: (pre_migration_sha, post_migration_sha, path)}. post_migration_sha
# is the commit where this phase's workflowgen migration landed as an
# equivalence-only change (no gap-fixing mixed in — that's always a
# separate, later commit). Paths don't move across migration.
_MIGRATION: dict[int, tuple[str, str, str]] = {
    1: ("bb1b9b74923c", "e2324f5a1f88", ".claude/workflows/phase1-requirements.js"),
    2: ("bb1b9b74923c", "e2324f5a1f88", ".claude/workflows/phase2-architecture.js"),
    3: ("5acc9a33357f", "840d637fbcd6", ".claude/workflows/phase3-implementation.js"),
    4: ("5acc9a33357f", "840d637fbcd6", ".claude/workflows/phase4-testing.js"),
    5: ("805f1d3fb3cb", "581c6360e1ee", ".claude/workflows/phase5-verification.js"),
    6: ("bb1b9b74923c", "e2324f5a1f88", ".claude/workflows/phase6-quality.js"),
    7: ("805f1d3fb3cb", "581c6360e1ee", ".claude/workflows/phase7-risk.js"),
    8: ("8a071fb4127ee363aaf604625d1e71e7684edba4", "805f1d3fb3cb", ".claude/workflows/phase8-config.js"),
}

# Label renames introduced by a migration: {phase: {old_label: new_label}}.
# Empty for phase5/7/8 — each generator reused every original label verbatim.
_LABEL_RENAMES: dict[int, dict[str, str]] = {}


def _read_at_commit(sha: str, path: str) -> str:
    result = subprocess.run(
        ["git", "show", f"{sha}:{path}"],
        capture_output=True, text=True, check=True,
    )
    return result.stdout


def _subcommands():
    return known_subcommands()


def test_migration_refs_are_readable():
    """Sanity: every pinned commit/path pair must actually resolve — a typo
    here would make every other test in this file vacuously trivial."""
    for phase, (pre_sha, post_sha, path) in _MIGRATION.items():
        before = _read_at_commit(pre_sha, path)
        after = _read_at_commit(post_sha, path)
        assert before.strip(), f"phase{phase}: pre-migration {pre_sha}:{path} resolved to empty content"
        assert after.strip(), f"phase{phase}: post-migration {post_sha}:{path} resolved to empty content"


@pytest.mark.parametrize("phase", sorted(_MIGRATION))
def test_meta_phases_unchanged(phase):
    pre_sha, post_sha, path = _MIGRATION[phase]
    before = extract_js_phases(_read_at_commit(pre_sha, path))
    after = extract_js_phases(_read_at_commit(post_sha, path))
    assert after == before


@pytest.mark.parametrize("phase", sorted(_MIGRATION))
def test_agent_labels_unchanged_modulo_renames(phase):
    pre_sha, post_sha, path = _MIGRATION[phase]
    before = set(extract_js_agent_labels(_read_at_commit(pre_sha, path)))
    after = set(extract_js_agent_labels(_read_at_commit(post_sha, path)))
    renames = _LABEL_RENAMES.get(phase, {})
    before_renamed = {renames.get(label, label) for label in before}
    assert after == before_renamed, (
        f"phase{phase}: label set changed with no rename entry: "
        f"before-only={before_renamed - after}, after-only={after - before_renamed}"
    )


@pytest.mark.parametrize("phase", sorted(_MIGRATION))
def test_cli_commands_unchanged(phase):
    subs = _subcommands()
    pre_sha, post_sha, path = _MIGRATION[phase]
    before = extract_js_subcommands(_read_at_commit(pre_sha, path), subs)
    after = extract_js_subcommands(_read_at_commit(post_sha, path), subs)
    assert after == before
