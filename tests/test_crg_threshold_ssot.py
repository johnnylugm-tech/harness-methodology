"""The architecture floor is stated once — in the gate config (Round 38 站0/站2).

Round 18 站2 made ``harness/gate_configs/*.yaml`` the only authority on a
dimension's threshold. The architecture floor never got the memo. Counted at
the start of Round 38, the number 80 was stated in **nine** places:

  1-2. ``gate3_p4_exit.yaml`` / ``gate4_p6_full.yaml``      (the authority)
  3.   ``gate2_p3_exit.yaml``                                — *absent*, which is
       its own defect: CI's ``CRG Architecture Gate (P3+)`` job enforces the
       floor from phase 3 onward while gate 2's config never declared it
       (see tests/test_gate_config_registry.py)
  4-6. ``crg_threshold=80.0`` in ``scripts/workflowgen/spec_phase{3,4,6}.py``
  7.   ``--threshold 80.0`` spelled out in those same files' prose
  8.   ``--threshold 80`` in ``templates/harness_quality_gate.yml``
  9.   ``default=80.0`` on ``crg-arch-check``'s argparse

Only #1-2 are read by anything that scores. The rest are restatements that a
threshold change would silently leave behind — the exact shape Round 33
(one contract, five statements) and Round 36 (one default, six statements)
each cost a round to unwind.

The fix is subtraction: nobody passes ``--threshold`` at all. ``crg-arch-check``
resolves the floor from the project's phase via ``EXIT_GATE_MAP`` and the gate
config. The flag survives only as an explicit override for ad-hoc probing.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.core]

REPO = Path(__file__).resolve().parent.parent
GATE_CONFIGS = REPO / "harness" / "gate_configs"

# Files allowed to state the architecture floor. Exactly one directory: the
# YAML that scores against it.
_AUTHORITY = GATE_CONFIGS

# Where restatements have historically accumulated.
_SCANNED = (
    REPO / "scripts" / "workflowgen",
    REPO / "templates",
    REPO / "cli" / "check_cmds.py",
    REPO / ".claude" / "workflows",
)

_SUFFIXES = {".py", ".yml", ".yaml", ".js"}


def _scanned_files() -> list[Path]:
    files: list[Path] = []
    for target in _SCANNED:
        if target.is_file():
            files.append(target)
        else:
            files.extend(
                p for p in target.rglob("*")
                if p.is_file() and p.suffix in _SUFFIXES
            )
    return sorted(files)


def test_the_authority_actually_states_the_floor() -> None:
    """Positive control: if the gate configs stopped declaring architecture,
    the scan below would pass vacuously and prove nothing."""
    from core.quality_gate.gate_thresholds import load_gate_thresholds

    for gate in (2, 3, 4):
        thresholds = load_gate_thresholds(gate)
        assert "architecture" in thresholds, (
            f"gate {gate}'s config declares no architecture threshold — the "
            "authority is empty, so the SSOT scan proves nothing"
        )


def test_no_file_outside_the_gate_config_passes_a_crg_threshold() -> None:
    """A ``--threshold`` on a ``crg-arch-check`` invocation is a second source.

    Matches the flag anywhere in the same statement/line as the command, which
    is how every one of the historical restatements was written.
    """
    offenders: list[str] = []
    for path in _scanned_files():
        if _AUTHORITY in path.parents:
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "crg-arch-check" in line and "--threshold" in line:
                offenders.append(f"{path.relative_to(REPO)}:{lineno}")
    assert not offenders, (
        "these call sites pass an architecture floor of their own instead of "
        "letting crg-arch-check read it from the gate config:\n  "
        + "\n  ".join(offenders)
    )


def test_the_workflow_generators_carry_no_crg_threshold_parameter() -> None:
    """``crg_threshold=80.0`` in three spec_phase*.py files was statements 4-6.

    Removing the parameter is what makes the restatement impossible rather
    than merely absent today. Matched by AST — as a function parameter or a
    call keyword — not by text: prose explaining why the parameter is gone is
    not a restatement of the number, and a scan that cannot tell the two apart
    would push a later reader to delete the explanation.
    """
    offenders: list[str] = []
    for path in sorted((REPO / "scripts" / "workflowgen").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        rel = path.relative_to(REPO)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                names = [a.arg for a in node.args.args + node.args.kwonlyargs]
                if "crg_threshold" in names:
                    offenders.append(f"{rel}:{node.lineno} (parameter)")
            elif isinstance(node, ast.Call):
                if any(kw.arg == "crg_threshold" for kw in node.keywords):
                    offenders.append(f"{rel}:{node.lineno} (argument)")
    assert not offenders, (
        "the workflow generators still carry a crg_threshold parameter:\n  "
        + "\n  ".join(offenders)
    )


def _crg_arch_parser_var(tree: ast.Module) -> str:
    """The local name `sub.add_parser("crg-arch-check", ...)` was bound to.

    Scoping by variable rather than by flag name matters: `check_cmds.py`
    defines a `--threshold` on `spec-coverage-check` too, and that one is a
    different dimension with its own authority. A scan that matched every
    `--threshold` would report it and push a later reader toward "fixing" an
    unrelated command.
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
            continue
        call = node.value
        if getattr(call.func, "attr", None) != "add_parser":
            continue
        if not (call.args and isinstance(call.args[0], ast.Constant)
                and call.args[0].value == "crg-arch-check"):
            continue
        target = node.targets[0]
        if isinstance(target, ast.Name):
            return target.id
    raise AssertionError(
        "no `<var> = sub.add_parser('crg-arch-check', ...)` in cli/check_cmds.py — "
        "this scan can no longer find the parser it is meant to police"
    )


def test_crg_arch_check_has_no_hard_coded_default_floor() -> None:
    """``default=80.0`` was statement 9 — the one that would survive even
    after every caller stopped passing the flag."""
    tree = ast.parse((REPO / "cli" / "check_cmds.py").read_text(encoding="utf-8"))
    parser_var = _crg_arch_parser_var(tree)
    defaults: list[str] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "add_argument"
                and getattr(node.func.value, "id", None) == parser_var):
            continue
        args = [a.value for a in node.args
                if isinstance(a, ast.Constant) and isinstance(a.value, str)]
        if "--threshold" not in args:
            continue
        for kw in node.keywords:
            if kw.arg == "default" and isinstance(kw.value, ast.Constant):
                if kw.value.value is not None:
                    defaults.append(repr(kw.value.value))
    assert not defaults, (
        f"crg-arch-check's --threshold carries a hard-coded default {defaults} — "
        "the floor must come from the gate config, and an unpassed flag must "
        "mean 'resolve it', not 'use this number'"
    )


def test_the_resolved_floor_equals_the_gate_config_for_every_phase() -> None:
    """Behavioural side: whatever phase a project is in, the floor it is
    measured against is the one its exit gate's YAML states."""
    from core.phase_topology import EXIT_GATE_MAP
    from core.quality_gate.crg_baseline import floor_for_phase
    from core.quality_gate.gate_thresholds import load_gate_thresholds

    for phase, gate in sorted(EXIT_GATE_MAP.items()):
        if phase < 3:
            continue  # CI's crg job only applies from phase 3
        assert floor_for_phase(phase) == load_gate_thresholds(gate)["architecture"]


def test_a_phase_without_its_own_exit_gate_inherits_the_last_one() -> None:
    """Phases 5, 7 and 8 have no exit gate of their own, but CI's ``PHASE >= 3``
    condition still measures them. They inherit the most recent exit gate's
    floor rather than falling back to a literal."""
    from core.quality_gate.crg_baseline import floor_for_phase
    from core.quality_gate.gate_thresholds import load_gate_thresholds

    assert floor_for_phase(5) == load_gate_thresholds(3)["architecture"]
    assert floor_for_phase(7) == load_gate_thresholds(4)["architecture"]
    assert floor_for_phase(8) == load_gate_thresholds(4)["architecture"]


def test_an_unknown_phase_uses_the_strictest_gate_not_a_literal() -> None:
    """A project whose state.json is unreadable must not silently get a
    lenient floor — Round 35's rule, applied to the threshold rather than
    the score."""
    from core.quality_gate.crg_baseline import floor_for_phase
    from core.quality_gate.gate_thresholds import load_gate_thresholds

    assert floor_for_phase(None) == load_gate_thresholds(4)["architecture"]
    assert floor_for_phase(0) == load_gate_thresholds(4)["architecture"]


def test_scan_would_catch_a_reintroduced_restatement(tmp_path: Path) -> None:
    """Negative control for the line scan: a checker that never fires is not
    a checker (Round 19)."""
    fake = tmp_path / "revived.yml"
    fake.write_text(
        "      - run: python harness_cli.py crg-arch-check --project . --threshold 80\n",
        encoding="utf-8",
    )
    hits = [
        lineno
        for lineno, line in enumerate(fake.read_text(encoding="utf-8").splitlines(), 1)
        if "crg-arch-check" in line and "--threshold" in line
    ]
    assert hits == [1]
