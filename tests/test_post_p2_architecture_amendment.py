"""After Phase 2, the architecture is amended in SAD.md §5, never beside it.

SAD.md §5 is the authority and `.methodology/SAB.json` is rendered from it by
`scripts/generate_sab.py`. Until this round nothing could amend the authority
after Phase 2:

  * a P3 module whose name states no declared layer (taskq-open FR-03 shipped
    `taskq_api/__main__.py`, which SPEC.md:54 requires) was refused by Gate 1
    with "declare a layer for them in SAD.md §5", and no tool and no workflow
    step could — FR-03..FR-10 parked on rc 25 and run-all halted;
  * following that remedy by hand (`generate_sab --overwrite`) re-filed every
    module amend-sab had registered into the LAST layer
    (`_preserve_amended_modules`), the guess Round 101 removed from
    `amend_sab` — on taskq-open, four packages landed in `migrations`;
  * `--resolve-phantom` wrote SAB.json only, so the next regeneration from
    SAD.md resurrected a dropped phantom.

Every amendment now edits SAD.md §5, regenerates SAB.json from it, and records
the reason in ADR.md. The project decides; the framework never guesses.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.core]

REPO = Path(__file__).resolve().parents[1]

_SAB = """\
sab:
  version: "1.0"
  phase: 2
  project: "amend-test"
  layers:
    - name: api
      modules:
        - "pkg.api.routes"
      allowed_dependencies: ["service"]
    - name: service
      modules: ["pkg.service.core"]
      allowed_dependencies: []
    - name: entry
      modules:
        - name: "pkg.cli"
      allowed_dependencies: ["api", "service"]
    - name: migrations
      modules:
        - "pkg.migrations.v1"
      allowed_dependencies: []
  allowed_dependencies: []
  fr_module_traceability:
    FR-01: "pkg.api.routes"
    FR-02: ["pkg.service.core", "pkg.service.ghost"]
  required_artifacts: []
"""

_REASON = "python -m pkg is required by SPEC; it wires the CLI entry point"


_SAD_REF = "02-architecture/SAD.md"
_DECISIONS = (
    '  decision_issues:\n'
    f'    - {{id: "D-1", status: resolved, blocks_phase: 3, resolution_ref: "{_SAD_REF}:@L(D-1: resolved — first)@"}}\n'
    f'    - {{id: "D-2", status: resolved, blocks_phase: 3, resolution_ref: "{_SAD_REF}:@L(D-2: resolved — second)@"}}\n'
    '    - {id: "D-3", status: resolved, blocks_phase: 3, resolution_ref: "02-architecture/NOTES.md:1"}\n'
)
_TAIL = "\nD-1: resolved — first\nD-2: resolved — second\n"
_SAB_D = _SAB.replace("  required_artifacts: []\n", _DECISIONS + "  required_artifacts: []\n")
_SAB_D_GHOST = _SAB_D.replace('        - "pkg.api.routes"\n',
                              '        - "pkg.api.routes"\n        - "pkg.api.ghost"\n')


def _sad_text(sab: str, tail: str = "", pad: int = 0) -> str:
    """A SAD.md; `@L(text)@` in it becomes the 1-based line number of the line `text`."""
    text = ("# SAD\n\n" + "filler\n" * pad + "## 5. SAB\n\n<!-- SAB:START -->\n```yaml\n"
            + sab + "```\n<!-- SAB:END -->\n" + tail)
    lines = [ln.strip() for ln in text.split("\n")]
    return re.sub(r"@L\((.+?)\)@", lambda m: str(lines.index(m.group(1)) + 1), text)


def _project(tmp_path: Path, sab: str = _SAB,
             files=("pkg/api/routes.py", "pkg/service/core.py", "pkg/cli.py",
                    "pkg/migrations/v1.py", "pkg/__main__.py"),
             tail: str = "", pad: int = 0) -> Path:
    root = tmp_path / "proj"
    sad = root / "02-architecture" / "SAD.md"
    sad.parent.mkdir(parents=True)
    sad.write_text(_sad_text(sab, tail, pad), encoding="utf-8")
    src = root / "03-development" / "src"
    for rel in files:
        p = src / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("", encoding="utf-8")
        init = p.parent / "__init__.py"
        while init.parent != src.parent and not init.exists():
            init.write_text("", encoding="utf-8")
            init = init.parent.parent / "__init__.py"
    (root / ".methodology").mkdir()
    _regenerate(root)
    return root


def _regenerate(root: Path) -> None:
    proc = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "generate_sab.py"),
         "--project", str(root), "--overwrite"],
        capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def _placements(root: Path) -> set:
    from core.quality_gate.sab_amender import normalize_sab_module_to_dotted as norm
    sab = json.loads((root / ".methodology" / "SAB.json").read_text(encoding="utf-8"))
    return {(layer["name"], norm(m)) for layer in sab["layers"] for m in layer["modules"]}


def _snapshot(root: Path) -> dict:
    files = [root / "02-architecture" / "SAD.md", root / ".methodology" / "SAB.json",
             root / "02-architecture" / "adr" / "ADR.md"]
    return {str(f): f.read_bytes() if f.exists() else None for f in files}


# ── regeneration keeps a registration where it was ──────────────────────────

def test_regeneration_keeps_an_amend_registration_in_its_own_layer(tmp_path) -> None:
    """RED before this round: the last declared layer (`migrations`) got it."""
    root = _project(tmp_path)
    sab_path = root / ".methodology" / "SAB.json"
    sab = json.loads(sab_path.read_text(encoding="utf-8"))
    next(lay for lay in sab["layers"] if lay["name"] == "service")["modules"].append("pkg.service.extra")
    sab_path.write_text(json.dumps(sab), encoding="utf-8")

    _regenerate(root)

    got = _placements(root)
    assert ("service", "pkg.service.extra") in got
    assert ("migrations", "pkg.service.extra") not in got


def test_regeneration_does_not_invent_a_layer_for_a_vanished_one(tmp_path) -> None:
    root = _project(tmp_path)
    sab_path = root / ".methodology" / "SAB.json"
    sab = json.loads(sab_path.read_text(encoding="utf-8"))
    sab["layers"].append({"name": "gone", "modules": ["pkg.orphan"]})
    sab_path.write_text(json.dumps(sab), encoding="utf-8")

    _regenerate(root)

    assert not any(m == "pkg.orphan" for _, m in _placements(root))


# ── declare: code -> SAB, through SAD ────────────────────────────────────────

@pytest.mark.parametrize("layer", ["api", "service", "entry"])
def test_declare_places_the_module_in_sad_and_sab(tmp_path, layer) -> None:
    """block-str (api), single-line flow (service), block-dict (entry)."""
    from core.quality_gate.sad_sab_edit import declare_module
    from core.quality_gate.sab_parser import extract_sab_from_sad

    root = _project(tmp_path)
    before = _placements(root)
    declare_module(root, "pkg.__main__", layer, _REASON)

    assert _placements(root) == before | {(layer, "pkg.__main__")}
    spec = extract_sab_from_sad(root / "02-architecture" / "SAD.md")
    sad_layer = next(lay for lay in spec.layers if lay["name"] == layer)
    assert any("pkg.__main__" in json.dumps(m) for m in sad_layer["modules"])
    adr = (root / "02-architecture" / "adr" / "ADR.md").read_text(encoding="utf-8")
    assert "pkg.__main__" in adr and _REASON in adr


def test_declare_leaves_nothing_undeclared_or_unplaceable(tmp_path) -> None:
    """Sub-packages (`pkg.api`, …) are still placed by plain amend-sab from
    their own names; what --declare must close is the module nothing could."""
    from core.quality_gate.sab_amender import (
        discover_modules, undeclared_layer_placements, unplaceable_modules)
    from core.quality_gate.sad_sab_edit import declare_module

    root = _project(tmp_path)
    declare_module(root, "pkg.__main__", "entry", _REASON)
    sab = json.loads((root / ".methodology" / "SAB.json").read_text(encoding="utf-8"))
    assert unplaceable_modules(sab, discover_modules(root)) == []
    assert undeclared_layer_placements(root) == []
    _regenerate(root)  # SAB.json rendered from the amended SAD agrees
    assert ("entry", "pkg.__main__") in _placements(root)


@pytest.mark.parametrize("module, layer, reason, why", [
    ("pkg.__main__", "nowhere", _REASON, "layer"),
    ("pkg.cli", "entry", _REASON, "unplaceable"),
    ("pkg.not_on_disk", "entry", _REASON, "unplaceable"),
    ("pkg.__main__", "entry", "too short", "reason"),
])
def test_declare_refuses_and_writes_nothing(tmp_path, module, layer, reason, why) -> None:
    from core.quality_gate.sab_amender import ArchitectureAmendmentError
    from core.quality_gate.sad_sab_edit import declare_module

    root = _project(tmp_path)
    before = _snapshot(root)
    with pytest.raises(ArchitectureAmendmentError, match=why):
        declare_module(root, module, layer, reason)
    assert _snapshot(root) == before


def test_declare_refuses_a_sab_block_shape_it_cannot_prove(tmp_path) -> None:
    """A multi-line flow list is not one of the three corpus shapes; an edit
    that cannot be verified by re-parsing is not written."""
    from core.quality_gate.sab_amender import ArchitectureAmendmentError
    from core.quality_gate.sad_sab_edit import declare_module

    weird = _SAB.replace('      modules: ["pkg.service.core"]\n',
                         '      modules: [\n        "pkg.service.core"\n      ]\n')
    root = _project(tmp_path, sab=weird)
    before = _snapshot(root)
    with pytest.raises(ArchitectureAmendmentError, match="shape"):
        declare_module(root, "pkg.__main__", "service", _REASON)
    assert _snapshot(root) == before


# ── resolve-phantom: SAB -> code, through SAD ───────────────────────────────

def test_a_dropped_phantom_stays_dropped_after_regeneration(tmp_path) -> None:
    """RED before this round: the drop lived in SAB.json only, and the next
    `generate_sab --overwrite` read the phantom back out of SAD.md."""
    from core.quality_gate.sab_amender import phantom_modules, discover_modules, resolve_phantom

    sab = _SAB.replace('      modules: ["pkg.service.core"]\n',
                       '      modules: ["pkg.service.core", "pkg.service.ghost"]\n')
    root = _project(tmp_path, sab=sab)
    resolve_phantom(root, "pkg.service.ghost", to=None, drop=True,
                    reason="ghost was never implemented and FR-02 does not need it")
    _regenerate(root)

    sab_now = json.loads((root / ".methodology" / "SAB.json").read_text(encoding="utf-8"))
    assert phantom_modules(sab_now, discover_modules(root)) == []
    assert "pkg.service.ghost" not in json.dumps(sab_now["fr_module_traceability"])


def test_a_retargeted_phantom_is_declared_in_sad(tmp_path) -> None:
    from core.quality_gate.sab_amender import resolve_phantom, undeclared_layer_placements

    sab = _SAB.replace('      modules: ["pkg.service.core"]\n',
                       '      modules: ["pkg.service.core", "pkg.service.ghost"]\n')
    root = _project(tmp_path, sab=sab, files=(
        "pkg/api/routes.py", "pkg/service/core.py", "pkg/service/real.py",
        "pkg/cli.py", "pkg/migrations/v1.py"))
    resolve_phantom(root, "pkg.service.ghost", to="pkg.service.real", drop=False,
                    reason="the ghost module was implemented as pkg.service.real")
    _regenerate(root)

    assert ("service", "pkg.service.real") in _placements(root)
    assert undeclared_layer_placements(root) == []


# ── a `path:line` citation inside SAD.md follows the line it cites ───────────

def _decision_project(tmp_path: Path, **kw) -> Path:
    root = _project(tmp_path, **kw)
    (root / "02-architecture" / "NOTES.md").write_text(
        "D-3: resolved — recorded in another file\n", encoding="utf-8")
    return root


def _decision_refs(root: Path) -> dict:
    from core.quality_gate.sab_parser import extract_sab_from_sad
    spec = extract_sab_from_sad(root / "02-architecture" / "SAD.md")
    return {row["id"]: row["resolution_ref"] for row in spec.decision_issues}


def _decision_findings(root: Path) -> list:
    from core.quality_gate.decision_issues import decision_issue_findings
    from core.quality_gate.sab_parser import extract_sab_from_sad
    spec = extract_sab_from_sad(root / "02-architecture" / "SAD.md")
    return decision_issue_findings(root, spec.decision_issues, entering_phase=3)


@pytest.mark.parametrize("layer", ["api", "entry", "service"])
def test_declare_keeps_decision_refs_resolving(tmp_path, layer) -> None:
    """An amendment above the decision records must not break their refs.

    78f79c15 met this by rebasing `SAD.md:N` through a line diff of each
    amendment — one writer's repair, while any other edit above the records
    still moved them out from under their refs. Round 114 站3 resolves a ref
    by the record's content (`<id>: resolved — …`), so the amendment leaves
    the refs exactly as written and they still resolve."""
    from core.quality_gate.sad_sab_edit import declare_module

    root = _decision_project(tmp_path, sab=_SAB_D, tail=_TAIL)
    assert _decision_findings(root) == []
    before = _decision_refs(root)

    declare_module(root, "pkg.__main__", layer, _REASON)

    assert _decision_refs(root) == before, "an amendment does not rewrite references"
    assert _decision_findings(root) == []
    _regenerate(root)
    sab = json.loads((root / ".methodology" / "SAB.json").read_text(encoding="utf-8"))
    assert {r["id"]: r["resolution_ref"] for r in sab["decision_issues"]} == before


def test_a_dropped_block_module_keeps_decision_refs_resolving(tmp_path) -> None:
    from core.quality_gate.sab_amender import resolve_phantom

    root = _decision_project(tmp_path, sab=_SAB_D_GHOST, tail=_TAIL)
    before = _decision_refs(root)
    assert _decision_findings(root) == []

    resolve_phantom(root, "pkg.api.ghost", to=None, drop=True,
                    reason="ghost was never implemented and no FR needs it")

    assert _decision_refs(root) == before
    assert _decision_findings(root) == []


# ── the remedy names the tool, everywhere it is printed ─────────────────────

def test_the_unplaceable_remedy_names_declare() -> None:
    from core.quality_gate.sab_amender import UNPLACEABLE_REMEDY
    assert "amend-sab" in UNPLACEABLE_REMEDY and "--declare" in UNPLACEABLE_REMEDY


def test_the_cli_declares(tmp_path) -> None:
    root = _project(tmp_path)
    proc = subprocess.run(
        [sys.executable, str(REPO / "harness_cli.py"), "amend-sab",
         "--project", str(root), "--declare", "pkg.__main__",
         "--layer", "entry", "--reason", _REASON],
        capture_output=True, text=True, cwd=str(REPO))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert ("entry", "pkg.__main__") in _placements(root)


def test_the_p3_workflow_tells_the_agent_to_declare() -> None:
    for js in ("phase3-implementation.js", "run-all.js"):
        text = (REPO / ".claude" / "workflows" / js).read_text(encoding="utf-8")
        rc25 = next(ln for ln in text.splitlines() if "RC=25 (INFRA" in ln)
        assert "--declare" in rc25, js
        assert "intervening FR may repair" not in rc25, js
