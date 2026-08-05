"""Every `dimension(NN)` threshold stated in prose must equal the YAML that enforces it.

Round 18 站2. `harness/gate_configs/gate{N}_*.yaml` is what HarnessBridge scores
against; everything else that quotes a threshold is a copy. 35214a0 raised Gate
1's linting/type_safety from 90/85 to 100/100 and updated the YAML plus two
hand-maintained copies — and left five others saying 90/85: four P*_SOP.md
files and the phase flowchart. Nothing failed, because nothing compared them.

Round 17 站1 bound ONE consumer (the GATE1 dispatch prompt) to the SSOT. This
binds the rest. The registry is declarative and the completeness meta-test
below fails when a new file starts quoting thresholds without registering the
gate it is quoting — otherwise this guard rots the same way the copies did.

Why a guard and not render-from-SSOT for these sites: the prose is a
human-readable summary that also carries per-gate annotations, composite-score
tails, and a dimension ORDER that plangen's own build_gate_meta_for_features()
parses back out. Generating it would complicate that round-trip to remove a
drift risk that is entirely in the numbers — which this catches at author time
for strictly more sites than a renderer could reach (a renderer cannot reach a
hand-written .md at all, and 5 of the 6 stale copies were .md).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import NamedTuple

import pytest

from core.quality_gate.gate_thresholds import GATE_CONFIG_NAMES, load_gate_thresholds

REPO_ROOT = Path(__file__).resolve().parents[1]

# `name(NN)` where name is a real gate dimension. Bare `head(200)` / `zfill(2)`
# and similar call syntax cannot match because the name must be a dimension.
_DIM_THRESHOLD_RE = re.compile(r"\b([a-z_]+)\((\d{2,3})\)")


def _all_dimension_names() -> set[str]:
    names: set[str] = set()
    for gate in GATE_CONFIG_NAMES:
        names |= set(load_gate_thresholds(gate))
    return names


class ProseSite(NamedTuple):
    """A file quoting one gate's thresholds in prose.

    `gate` is which gate's YAML the file's `dim(NN)` pairs must match.
    """

    path: str
    gate: int


# Files that state gate thresholds in prose, and the gate each one describes.
PROSE_SITES: tuple[ProseSite, ...] = (
    # Workflow prose handed to the Gate 3 / Gate 4 orchestrator sub-agents.
    # (.claude/workflows/*.js is generated FROM these by workflowgen, so it is
    # not listed separately — fixing the generator fixes the artifact. Round
    # 36: this comment used to say the artifact was "kept in step by
    # generate_workflows.py --check"; nothing ran --check. What keeps it in
    # step is tests/test_workflowgen_shipped_parity.py.)
    ProseSite("scripts/workflowgen/spec_phase4.py", 3),
    ProseSite("scripts/workflowgen/spec_phase6.py", 4),
    # Hand-written operator SOPs. P5/P7/P8 re-run Gate 1 as GATE1-DELTA, so
    # they quote Gate 1's bar too.
    ProseSite("docs/P3_SOP.md", 1),
    ProseSite("docs/P5_SOP.md", 1),
    ProseSite("docs/P7_SOP.md", 1),
    ProseSite("docs/P8_SOP.md", 1),
    ProseSite("docs/superpowers/plans/harness_phase_flowchart.md", 1),
    ProseSite("SAD.md", 1),
)

# Files checked structurally rather than by a flat per-file scan, because one
# file states SEVERAL gates' thresholds and a single `gate` field cannot say
# which pairs belong to which gate.
STRUCTURAL_SITES: dict[str, str] = {
    "scripts/plangen/blocks.py": (
        "_GATE_META is a {gate: (score_gate, dim_count, prose)} dict — one "
        "file, four gates. Checked per-gate by "
        "test_gate_meta_plan_prose_matches_the_enforcing_yaml below."
    ),
}

# Files that legitimately contain `dimension(NN)` text that is NOT a threshold.
# A reason is required — an unexplained entry here is how a real copy escapes.
EXEMPT_SITES: dict[str, str] = {
    "docs/USER_MANUAL.md": (
        "worked example of the composite-score formula ('Example Gate 2 (10 "
        "dims): linting(90) × 0.12 + type_safety(88) × 0.12 + ...'). The "
        "numbers are illustrative dimension SCORES being multiplied by "
        "weights, not thresholds — type_safety(88) and test_coverage(82) "
        "match no gate's YAML by design."
    ),
    "docs/PROPOSAL_ADJUDICATIONS.md": (
        "the adjudication ledger records what a number WAS at the moment a "
        "defect was measured — Round 37's 'architecture 77.8 against a floor "
        "of 80', Round 38's 'architecture 16.7'. Those are historical "
        "measurements, and pinning them to the current YAML would make the "
        "record wrong the first time a threshold legitimately moves. The "
        "ledger is read by humans deciding what to do next, never by a gate."
    ),
}

# Directories the completeness scan does not walk, with the reason each is safe.
_SCAN_SKIP_REASONS: dict[str, str] = {
    "tests": "test fixtures and this registry itself",
    ".claude": "generated from scripts/workflowgen/* (covered at the generator)",
    ".methodology": "generated per-project by plangen (covered at the generator)",
    ".git": "not source",
    "node_modules": "not source",
    "__pycache__": "not source",
    ".venv": "not source",
}

_SCAN_SUFFIXES = {".py", ".md", ".js"}


def _iter_scannable_files():
    for path in sorted(REPO_ROOT.rglob("*")):
        if path.suffix not in _SCAN_SUFFIXES or not path.is_file():
            continue
        rel = path.relative_to(REPO_ROOT)
        if set(rel.parts) & set(_SCAN_SKIP_REASONS):
            continue
        yield rel, path


def _threshold_pairs(text: str, dims: set[str]) -> list[tuple[str, int]]:
    return [
        (name, int(val))
        for name, val in _DIM_THRESHOLD_RE.findall(text)
        if name in dims
    ]


@pytest.mark.parametrize("site", PROSE_SITES, ids=lambda s: s.path)
def test_prose_thresholds_match_the_enforcing_yaml(site: ProseSite):
    """Each registered file's `dim(NN)` pairs must equal that gate's YAML."""
    expected = load_gate_thresholds(site.gate)
    text = (REPO_ROOT / site.path).read_text(encoding="utf-8")
    mismatches = [
        f"{name}({val}) but gate{site.gate} yaml says {expected[name]:g}"
        for name, val in _threshold_pairs(text, set(expected))
        if float(val) != expected[name]
    ]
    assert not mismatches, (
        f"{site.path} states Gate {site.gate} thresholds that no longer match "
        f"{GATE_CONFIG_NAMES[site.gate]}:\n  " + "\n  ".join(mismatches) +
        "\nThe YAML is what the gate actually scores against — update the prose."
    )


def test_gate_meta_plan_prose_matches_the_enforcing_yaml():
    """plangen's _GATE_META is keyed by gate, so it is checked structurally
    rather than as a flat file scan: each entry's prose must match its own
    gate's YAML, which a per-file scan could not express (one file, 4 gates)."""
    from scripts.plangen.blocks import _GATE_META

    failures: list[str] = []
    for gate, (_score_gate, _dim_count, prose) in sorted(_GATE_META.items()):
        expected = load_gate_thresholds(gate)
        for name, val in _threshold_pairs(prose, set(expected)):
            if float(val) != expected[name]:
                failures.append(
                    f"_GATE_META[{gate}]: {name}({val}) but "
                    f"{GATE_CONFIG_NAMES[gate]} says {expected[name]:g}"
                )
    assert not failures, (
        "plan prose drifted from the gate YAML:\n  " + "\n  ".join(failures)
    )


def test_every_file_quoting_thresholds_is_registered_or_exempt():
    """Completeness: a new file that quotes `dim(NN)` must join PROSE_SITES
    (bound to a gate) or EXEMPT_SITES (with a reason). Without this, the
    registry above silently stops covering the codebase — the same way the
    six stale copies accumulated unnoticed in the first place."""
    dims = _all_dimension_names()
    registered = (
        {s.path for s in PROSE_SITES} | set(EXEMPT_SITES) | set(STRUCTURAL_SITES)
    )
    unregistered = sorted(
        str(rel)
        for rel, path in _iter_scannable_files()
        if str(rel) not in registered
        and str(rel) != "tests/test_gate_threshold_docs_parity.py"
        and _threshold_pairs(path.read_text(encoding="utf-8", errors="replace"), dims)
    )
    assert not unregistered, (
        "these files quote gate dimension thresholds but are in none of "
        "PROSE_SITES (with the gate they describe), STRUCTURAL_SITES, or "
        "EXEMPT_SITES (with a reason):\n  " + "\n  ".join(unregistered)
    )


@pytest.mark.parametrize("registry_name", ["EXEMPT_SITES", "STRUCTURAL_SITES"])
def test_registry_entries_still_exist_and_carry_a_reason(registry_name):
    """An entry for a deleted file, or a blank reason, is dead weight that
    makes the next reader trust the registry less than they should."""
    registry = {"EXEMPT_SITES": EXEMPT_SITES, "STRUCTURAL_SITES": STRUCTURAL_SITES}[
        registry_name
    ]
    for path, reason in registry.items():
        assert (REPO_ROOT / path).exists(), (
            f"{registry_name} lists a missing file: {path}"
        )
        assert len(reason.strip()) > 40, f"{registry_name}[{path}] needs a real reason"
