"""Golden byte-equal pinning of workflowgen's generated output.

Mirrors tests/test_plangen_golden.py's exact shape and rationale: any
deliberate change to a migrated phase's generated JS must regenerate the
golden IN THE SAME COMMIT, so prose/structure changes are visible in diff
review instead of hiding inside a JS string-literal sea.

Regenerate after a deliberate change:

    REGEN_WORKFLOWS=1 python3 -m pytest tests/test_workflowgen_golden.py -q

Only phases migrated to workflowgen (scripts/workflowgen/generate_workflows.
GENERATORS) are locked here — un-migrated phases are untouched
hand-maintained files with no golden.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from scripts.workflowgen.generate_workflows import GENERATORS, generate

GOLDEN_DIR = Path(__file__).parent / "golden" / "workflowgen"


@pytest.mark.parametrize("phase", sorted(GENERATORS))
def test_generated_output_matches_golden(phase):
    text = generate(phase)
    golden_path = GOLDEN_DIR / f"phase{phase}.js"

    if os.environ.get("REGEN_WORKFLOWS") == "1":
        golden_path.parent.mkdir(parents=True, exist_ok=True)
        golden_path.write_text(text, encoding="utf-8")

    golden = golden_path.read_text(encoding="utf-8")
    assert text == golden, (
        f"phase{phase} workflowgen output drifted from its golden. If the "
        f"change is deliberate, regenerate in THIS commit: "
        f"REGEN_WORKFLOWS=1 python3 -m pytest tests/test_workflowgen_golden.py -q"
    )


def test_generation_is_deterministic():
    for phase in GENERATORS:
        assert generate(phase) == generate(phase)
