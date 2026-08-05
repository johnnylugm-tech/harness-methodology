"""The dimension list a prompt states must be the one the gate scores.

Round 39 站3. Round 38 站2 收斂了 architecture 的門檻 *值*; the prose that
enumerates every dimension was left alone as R38-DEFER-2. Measuring it:

    generator            prose claims   prose lists   gate config has
    spec_phase3.py       "9 dims"       —             12
    spec_phase4.py (x4)  "15 dims"      13            16
    spec_phase6.py (x3)  "14 dims"      13            15

Every one of the 13 thresholds it does list matches the YAML exactly — the
numbers were never the problem. What is wrong is the *set* and the *count*:
the three omitted dimensions are `traceability`, `mutation_testing` and
`adversarial_review`, all framework-owned and all blocking. The agent was told
it would be judged on 15 dimensions, shown 13, and graded on 16.

(phase3's "9 dims" was already wrong at 11; Round 38 站1 added architecture to
gate 2 and made it 12, widening a gap this round closes.)

`scripts/plangen/blocks.py` carried a fourth statement — hand-written
`mutation_testing(70)` / `architecture(80)` / `adversarial_review(100)` strings
used to *remove* feature-disabled dimensions from the plan, in a file outside
the scan Round 38 站2 installed.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from core.phase_topology import EXIT_GATE_MAP
from core.quality_gate.gate_thresholds import load_gate_thresholds

pytestmark = [pytest.mark.core]

REPO = Path(__file__).resolve().parent.parent

# phase -> the generator whose prose describes that phase's exit gate.
_SPECS: dict[int, Path] = {
    3: REPO / "scripts" / "workflowgen" / "spec_phase3.py",
    4: REPO / "scripts" / "workflowgen" / "spec_phase4.py",
    6: REPO / "scripts" / "workflowgen" / "spec_phase6.py",
}

_DIM_WITH_THRESHOLD = re.compile(r"\b([a-z_]+)\((\d+)\)")
_COUNT_CLAIM = re.compile(r"\b(\d+)\s+dims\b")


def _prose_lines(path: Path) -> list[tuple[int, str]]:
    return [
        (i, ln) for i, ln in
        enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if "dims" in ln.lower()
    ]


@pytest.mark.parametrize("phase", sorted(_SPECS), ids=lambda p: f"phase{p}")
def test_prose_dimension_count_matches_the_gate_config(phase: int) -> None:
    gate = EXIT_GATE_MAP[phase]
    expected = len(load_gate_thresholds(gate))
    wrong: list[str] = []
    for lineno, line in _prose_lines(_SPECS[phase]):
        m = _COUNT_CLAIM.search(line)
        if m and int(m.group(1)) != expected:
            wrong.append(
                f"{_SPECS[phase].relative_to(REPO)}:{lineno} says "
                f"'{m.group(0)}', gate {gate} has {expected}")
    assert not wrong, (
        "the prompt tells the agent how many dimensions it will be judged on, "
        "and the number is wrong:\n  " + "\n  ".join(wrong))


@pytest.mark.parametrize("phase", sorted(_SPECS), ids=lambda p: f"phase{p}")
def test_prose_dimension_list_matches_the_gate_config(phase: int) -> None:
    """Every enumerated `name(threshold)` list must be the complete set.

    A partial list is worse than none: it reads as authoritative, and the
    dimensions it omits here are exactly the ones the agent does not score
    itself and therefore cannot discover by doing the work.
    """
    gate = EXIT_GATE_MAP[phase]
    yaml_dims = load_gate_thresholds(gate)
    problems: list[str] = []
    for lineno, line in _prose_lines(_SPECS[phase]):
        listed = {m.group(1): float(m.group(2))
                  for m in _DIM_WITH_THRESHOLD.finditer(line)
                  if m.group(1) in yaml_dims}
        if not listed:
            continue  # a line that mentions "dims" without enumerating them
        missing = sorted(set(yaml_dims) - set(listed))
        wrong = {k: (v, yaml_dims[k]) for k, v in listed.items()
                 if yaml_dims[k] != v}
        if missing or wrong:
            problems.append(
                f"{_SPECS[phase].relative_to(REPO)}:{lineno} — "
                f"missing {missing}, wrong {wrong}")
    assert not problems, (
        f"gate {gate}'s prompt enumerates its dimensions incompletely:\n  "
        + "\n  ".join(problems))


def test_plangen_does_not_restate_thresholds() -> None:
    """`scripts/plangen/blocks.py` hand-wrote three `dimension(threshold)`
    strings to filter feature-disabled dimensions out of the plan — a fourth
    copy of numbers the gate config owns, in a file Round 38 站2's scan did
    not reach."""
    text = (REPO / "scripts" / "plangen" / "blocks.py").read_text(encoding="utf-8")
    known = set(load_gate_thresholds(4)) | set(load_gate_thresholds(2))
    offenders = [
        f"line {i}: {m.group(0)}"
        for i, ln in enumerate(text.splitlines(), 1)
        for m in _DIM_WITH_THRESHOLD.finditer(ln)
        if m.group(1) in known
    ]
    assert not offenders, (
        "plangen restates gate thresholds instead of reading them:\n  "
        + "\n  ".join(offenders))


def test_the_scan_reads_real_gate_configs() -> None:
    """Positive control: if load_gate_thresholds started returning {}, every
    assertion above would pass vacuously."""
    for phase, gate in sorted(EXIT_GATE_MAP.items()):
        assert len(load_gate_thresholds(gate)) >= 9, (phase, gate)


def test_the_count_scan_would_catch_a_wrong_claim() -> None:
    """Negative control (Round 19: a checker that cannot fire is not one)."""
    m = _COUNT_CLAIM.search("   Evaluate all 14 dims inline per …")
    assert m and m.group(1) == "14"
