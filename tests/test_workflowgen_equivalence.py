"""Structural equivalence lock for workflowgen migrations (Round 11).

When a phase's hand-maintained `.claude/workflows/phaseN-*.js` is replaced
by workflowgen-generated output, this is the proof the migration didn't
silently change what the workflow DOES: not byte-equality (the generator
is explicitly allowed to simplify/consolidate prose — see
docs/WORKFLOW_ALIGNMENT_AUDIT.md's "not a full-fidelity diff" note), but
three structural assertions against the file as it existed immediately
BEFORE migration (read via `git show <pinned-sha>:<path>`, not a hardcoded
literal snapshot — the pinned commit is the actual historical record):

  (a) meta.phases titles — same set, same order
  (b) agent() label set — same set (a rename requires an explicit mapping
      entry here, not a silent drop)
  (c) harness_cli.py command set — same set (scripts/workflow_audit/
      extract.py's precision rules — see that module's docstring)

tests/test_workflow_plan_alignment.py's KNOWN_GAPS registry is
UNCHANGED by an equivalence-only migration (per the Round 11 plan: "遷移
不修 gap") — a gap present before migration is still present after, just
now living in generated code instead of hand-maintained code.
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
from scripts.workflowgen.generate_workflows import generate

# One entry per migrated phase: the commit SHA immediately BEFORE that
# phase's workflowgen migration landed, and the path the pre-migration
# content lived at (always .claude/workflows/<file> — paths don't move).
_PRE_MIGRATION_REF = {
    5: ("805f1d3fb3cb", ".claude/workflows/phase5-verification.js"),
    7: ("805f1d3fb3cb", ".claude/workflows/phase7-risk.js"),
    8: ("8a071fb4127ee363aaf604625d1e71e7684edba4", ".claude/workflows/phase8-config.js"),
}

# Label renames introduced by a migration: {phase: {old_label: new_label}}.
# Empty for phase5/7/8 — the generator reuses every original label verbatim.
_LABEL_RENAMES: dict[int, dict[str, str]] = {}


def _read_at_commit(sha: str, path: str) -> str:
    result = subprocess.run(
        ["git", "show", f"{sha}:{path}"],
        capture_output=True, text=True, check=True,
    )
    return result.stdout


def _subcommands():
    return known_subcommands()


def test_pre_migration_refs_are_readable():
    """Sanity: the pinned commit/path pairs must actually resolve — a typo
    here would make every other test in this file vacuously trivial."""
    for phase, (sha, path) in _PRE_MIGRATION_REF.items():
        text = _read_at_commit(sha, path)
        assert text.strip(), f"phase{phase}: {sha}:{path} resolved to empty content"


@pytest.mark.parametrize("phase", sorted(_PRE_MIGRATION_REF))
def test_meta_phases_unchanged(phase):
    sha, path = _PRE_MIGRATION_REF[phase]
    before = extract_js_phases(_read_at_commit(sha, path))
    after = extract_js_phases(generate(phase))
    assert after == before


@pytest.mark.parametrize("phase", sorted(_PRE_MIGRATION_REF))
def test_agent_labels_unchanged_modulo_renames(phase):
    sha, path = _PRE_MIGRATION_REF[phase]
    before = set(extract_js_agent_labels(_read_at_commit(sha, path)))
    after = set(extract_js_agent_labels(generate(phase)))
    renames = _LABEL_RENAMES.get(phase, {})
    before_renamed = {renames.get(label, label) for label in before}
    assert after == before_renamed, (
        f"phase{phase}: label set changed with no rename entry: "
        f"before-only={before_renamed - after}, after-only={after - before_renamed}"
    )


@pytest.mark.parametrize("phase", sorted(_PRE_MIGRATION_REF))
def test_cli_commands_unchanged(phase):
    subs = _subcommands()
    sha, path = _PRE_MIGRATION_REF[phase]
    before = extract_js_subcommands(_read_at_commit(sha, path), subs)
    after = extract_js_subcommands(generate(phase), subs)
    assert after == before
