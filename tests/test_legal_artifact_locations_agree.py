"""Round 113 站3 — one file, one location, wherever the framework states it.

`LEGAL_ARTIFACTS` is printed by `print-legal-artifacts` into the Phase 1
author prompt as "STAGE → {FILE…}". It listed `TEST_INVENTORY.yaml` under
`01-requirements` while every other statement in the framework — init-project,
`ProjectLayout.test_inventory_path`, `PHASE_DELIVERABLE_PATHS`, naming
authority, the handoff validator, the Phase 1 workflow — puts it at the project
root. taskq-sol's TRACEABILITY_MATRIX wrote `01-requirements/TEST_INVENTORY.yaml`
twice; six corpus projects wrote it eight times. The framework taught it.

Same shape for the Phase 6 release documents: the P6 workflow writes
`RELEASE_NOTES.md` / `FINAL_SIGN_OFF.md` at the project root
(scripts/workflowgen/spec_phase6.py, scripts/generate_release_notes.py, the
P6→P7 handoff and the phase auditor all agree), and all 13 corpus projects that
reached Phase 6 have them only there — yet `PHASE_DELIVERABLE_PATHS[6]` and
`LEGAL_ARTIFACTS["06-quality"]` placed them under `06-quality/`.

The two tables cover different domains (forward-reference legality by stage vs
approval-id → path for phases 1/2/6), so neither can be derived from the other.
This pins that where they overlap they say the same thing.
"""

from __future__ import annotations

from pathlib import Path

from core.quality_gate.legal_artifacts import LEGAL_ARTIFACTS, PHASE_DELIVERABLE_PATHS
from core.utils.project_layout import ProjectLayout


def _stage_and_name(rel: str) -> "tuple[str | None, str]":
    parts = rel.split("/")
    stage = parts[0] if len(parts) > 1 and parts[0][:2].isdigit() else None
    return stage, parts[-1]


def test_every_phase_deliverable_is_legal_exactly_where_it_lives():
    disagreements = []
    for phase, paths in PHASE_DELIVERABLE_PATHS.items():
        for ident, rel in paths.items():
            if rel.startswith(".methodology/"):
                continue  # quality_manifest: an internal file, never a forward-ref target
            stage, name = _stage_and_name(rel)
            legal_in = sorted(s for s, names in LEGAL_ARTIFACTS.items() if name in names)
            expected = [stage] if stage else []
            if legal_in != expected:
                disagreements.append(f"P{phase} {ident}: lives at {rel!r}, legal under {legal_in}")
    assert not disagreements, "\n".join(disagreements)


def test_test_inventory_lives_where_project_layout_says(tmp_path: Path):
    layout = ProjectLayout(tmp_path)
    rel = layout.test_inventory_path.relative_to(tmp_path).as_posix()
    assert PHASE_DELIVERABLE_PATHS[1]["TEST_INVENTORY.yaml"] == rel


def test_release_documents_live_at_the_project_root():
    # Where the P6 workflow writes them; see the module docstring.
    assert PHASE_DELIVERABLE_PATHS[6]["RELEASE_NOTES.md"] == "RELEASE_NOTES.md"
    assert PHASE_DELIVERABLE_PATHS[6]["FINAL_SIGN_OFF.md"] == "FINAL_SIGN_OFF.md"
