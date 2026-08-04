"""Round 36 — the artifact that is CONSUMED must be the artifact that is checked.

`.claude/workflows/*.js` is what the Workflow tool actually loads and what
every orchestrator agent reads. Since Round 11 it is generator output, and
`generate_workflows.py --check` exists to say so. Nothing ran it.

tests/test_workflowgen_golden.py compares `generate(phase)` against
`tests/golden/workflowgen/phaseN.js` — generator output against a snapshot of
generator output. It never opens `.claude/workflows/`. So a hand-edit landing
directly on a shipped file leaves all 94 workflowgen tests green while
`--check` reports DRIFT, and the edit survives exactly until the next
`--write` silently overwrites it.

That is not hypothetical twice over:

  * 883e9ca (Round 35 follow-up) hand-edited the mutation_testing NOTE into
    phase3/phase4/phase6/run-all without touching spec_phase{3,4,6}.py.
  * Round 20 站4's own guard entry records that `--check` "had been reporting
    5/8 DRIFT since e2b98b6" and that "the two commits after it did not run
    --check".

REGRESSION_GUARDS.yaml had the golden test registered as the guard for
precisely this bug. This module is that guard; the golden test keeps its real
job (making a deliberate prose change visible in the diff of the commit that
caused it).

Regenerate after a deliberate generator change — in THAT commit:

    python3 scripts/workflowgen/generate_workflows.py --write
"""
from __future__ import annotations

from pathlib import Path

import pytest

from scripts.workflowgen.generate_workflows import (
    GENERATORS,
    WORKFLOWS_DIR,
    _composites,
    _target_path,
    generate,
    generate_composite,
)

pytestmark = [pytest.mark.core]

_REGEN = "python3 scripts/workflowgen/generate_workflows.py --write"


def _assert_shipped_matches(expected: str, target: Path) -> None:
    assert target.is_file(), (
        f"{target} is missing — it is a committed build artifact, run: {_REGEN}"
    )
    assert target.read_text(encoding="utf-8") == expected, (
        f"{target.name} on disk differs from generator output. The shipped "
        f"file is generated — edit scripts/workflowgen/ and regenerate in "
        f"THIS commit: {_REGEN}"
    )


@pytest.mark.parametrize("phase", sorted(GENERATORS))
def test_shipped_workflow_matches_generator(phase):
    _assert_shipped_matches(generate(phase), _target_path(phase))


@pytest.mark.parametrize("name", sorted(_composites()))
def test_shipped_composite_matches_generator(name):
    _, filename = _composites()[name]
    _assert_shipped_matches(generate_composite(name), WORKFLOWS_DIR / filename)


# Shipped workflow JS that is deliberately hand-maintained: standalone
# utilities, not phases of the 8-phase pipeline, so no spec module produces
# them and `--check` has never covered them. Editing these by hand is
# correct. The set is pinned so a NEW file cannot join it silently — an
# un-generated phase workflow is where the drift class would move next.
_HAND_MAINTAINED = {"bug-hunt-crg.js", "standalone-mutmut.js"}


def test_shipped_directory_holds_nothing_unaccounted_for():
    """Completeness: every .js under .claude/workflows/ is either generated
    (and therefore checked above) or on the hand-maintained list."""
    accounted = {filename for _, filename in GENERATORS.values()}
    accounted |= {filename for _, filename in _composites().values()}
    accounted |= _HAND_MAINTAINED
    unaccounted = sorted(
        p.name for p in WORKFLOWS_DIR.glob("*.js") if p.name not in accounted
    )
    assert not unaccounted, (
        f"shipped workflow JS that is neither generated nor declared "
        f"hand-maintained: {unaccounted} — migrate it to scripts/workflowgen/, "
        f"or add it to _HAND_MAINTAINED with the reason it stays hand-written"
    )


def test_hand_maintained_list_names_only_files_that_exist():
    """Negative control: a stale name on the list would silently widen the
    exemption for whatever file later takes that name."""
    missing = sorted(n for n in _HAND_MAINTAINED if not (WORKFLOWS_DIR / n).is_file())
    assert not missing, f"_HAND_MAINTAINED names files that do not exist: {missing}"
