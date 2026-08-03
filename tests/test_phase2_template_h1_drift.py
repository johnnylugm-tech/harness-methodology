"""Phase 2 templates' H1 must match the orchestrator's diskPrefix.

The Phase 2 orchestrator (generated from scripts/workflowgen/spec_phase2.py)
relays every deliverable through `loadFileViaPython(diskPath, diskPrefix, ...)`
which delegates to `harness_cli.py read-file --expect-prefix`. file_loader's
prefix check is a literal `first_line.startswith(expect_prefix)` — it does
NOT match by substring. So `templates/SAD.md`'s H1 must literally start with
the same string the spec sets as `diskPrefix`, otherwise the orchestrator
aborts every reload with `PREFIX_MISMATCH` → `LOADER_FAILED_AFTER_3_ATTEMPTS`
(see Round 28 station 2 — SAD heading kept the `# SAD - {Project Name}`
placeholder while the orchestrator demanded `# Software Architecture
Document`, and Agent A had no visual cue that the H1 itself had to be
replaced, only the rest of the body).

This test pins each P2 template's first line to the spec's diskPrefix string
so the next drift fails at edit time, not at orchestrator runtime.
"""
from __future__ import annotations

import re
from pathlib import Path

from scripts.workflowgen.spec_phase2 import _render_phase2_subtask1_sad


TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"


def _first_line(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    return text.splitlines()[0] if text else ""


def _disk_prefix_for_sad() -> str:
    """Extract the diskPrefix string from the rendered phase2 subtask1 JS.

    Pinned to the orchestrator-emitted code (not a hand-copied constant) so
    any future spec change automatically retargets this test.
    """
    js = _render_phase2_subtask1_sad()
    m = re.search(r"diskPrefix:\s*'([^']*)'", js)
    assert m is not None, "phase2 subtask1 JS must declare diskPrefix"
    return m.group(1)


def test_sad_template_h1_matches_orchestrator_disk_prefix():
    disk_prefix = _disk_prefix_for_sad()
    assert disk_prefix, "diskPrefix must not be empty"
    first = _first_line(TEMPLATES_DIR / "SAD.md")
    assert first.startswith(disk_prefix), (
        f"templates/SAD.md first line {first[:80]!r} does not start with "
        f"orchestrator diskPrefix {disk_prefix!r}. Agent A fills the body "
        f"but the template's H1 placeholder must itself satisfy the loader's "
        f"`first_line.startswith(expect_prefix)` check, or every reload "
        f"returns PREFIX_MISMATCH → LOADER_FAILED (Round 28 station 2)."
    )


def test_sad_template_first_line_has_project_name_placeholder():
    """The corrected H1 must still be templatable: Agent A replaces a
    `{Project Name}` placeholder, so the template still needs one.
    """
    first = _first_line(TEMPLATES_DIR / "SAD.md")
    assert "{Project Name}" in first or "{project_name}" in first, (
        f"templates/SAD.md H1 {first!r} lost the project-name placeholder — "
        f"Agent A would have nothing to substitute"
    )