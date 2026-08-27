"""Function length ratchet — the long ones are named, and may only shrink.

Round 80 站6. This repo guards file growth (tests/test_file_size_ratchet.py),
implementation-detail mocking (tests/test_patch_discipline.py), unlogged broad
excepts (tests/test_exception_swallow_ratchet.py), source-reading tests
(tests/test_source_reading_discipline.py) and unreaped subprocess spawns
(tests/test_subprocess_group.py). Nothing could see a function.

MEASURED at dff609e6, over the same five directories the file ratchet scans:

    harness/harness_bridge.py::HarnessBridge.finalize_gate     1150 lines
    cli/fr_cmds.py::cmd_run_fr_step                             940
    cli/phase_cmds.py::cmd_advance_phase                        845
    cli/phase_cmds.py::_advance_prechecks                       818
    cli/gate_cmds.py::_cmd_finalize_gate_impl                   606

and the churn sits exactly there. Counting hunk headers across the whole
history: 94 hunks landed in `cmd_advance_phase`, 81 in
`_run_harness_cross_validation`, 51 in `_advance_prechecks`. Meanwhile the file
ratchet's ceilings were raised 298 times and lowered 5 — `harness_bridge.py`
alone 56 times — because raising a ceiling is what you do when the thing that
grew is a function and nothing is asking about functions.

Of 2187 functions in scope, 24 are over 200 lines (1.1%). Those 24 are named
below with the length they had when this file was written; every other function
has a ceiling of 200 and no allowlist, the same shape
tests/test_patch_discipline.py uses ("a file not listed here has a ceiling of
0").

WHY THE CEILING MUST EQUAL THE LENGTH

Round 78 站3 rewrote a file-ratchet entry because two commits had moved the
integer and left the note, leaving a file sitting 101 lines below its own limit
— "headroom nobody reviewed", pre-authorising the growth the ratchet exists to
make visible. Here that convention is mechanical: a ceiling above the measured
length fails, so a function that shrinks has to have its number lowered in the
commit that shrank it. Same two-sided assert as
tests/test_subprocess_group.py's site count.

WHAT THIS IS NOT

It is not a decomposition plan. Round 80 deliberately does not break up the
five functions at the top of that list: extracting blocks changes the
function's own text, so the byte-equality rule that made the Round 49-B god
file splits safe does not apply, and the measured behavioural coverage of
`harness/harness_bridge.py` is 81% (1490 statements, 282 unreached) — not
enough to prove an extraction equivalent. Freezing the debt and leaving the
rewrite for a round that can prove it is the same judgement
tests/test_patch_discipline.py made about 400 private-seam patches.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytestmark = [pytest.mark.core]

REPO = Path(__file__).resolve().parents[1]

#: Same five directories tests/test_file_size_ratchet.py walks, so "production
#: code" means one thing in this repo rather than two.
_SCAN_DIRS = ("cli", "core", "harness", "scripts", "detection")

#: A function not named below may not exceed this. No allowlist: adding an
#: entry here is a decision that has to be argued in the commit that adds it,
#: which is the point.
_DEFAULT_CEILING = 200

#: qualified name -> ceiling, EQUAL to the length measured when the entry was
#: written. Only decreases. Each entry says why it is this size.
_CEILINGS: dict[str, int] = {
    # The five the churn concentrates in. Frozen, not split — see the module
    # docstring for why Round 80 did not attempt the decomposition.
    "harness/harness_bridge.py::HarnessBridge.finalize_gate": 1150,
    "cli/fr_cmds.py::cmd_run_fr_step": 940,
    "cli/phase_cmds.py::cmd_advance_phase": 845,
    "cli/phase_cmds.py::_advance_prechecks": 818,
    "cli/gate_cmds.py::_cmd_finalize_gate_impl": 606,
    "harness/harness_bridge.py::_run_harness_cross_validation": 475,

    "cli/project_cmds.py::cmd_init_project": 417,
    "core/quality_gate/security_design.py::check_security_design": 322,
    "core/agent_spawner.py::AgentSpawner.spawn": 316,
    "detection/drift_detector.py::DriftDetector.detect_sab_drift": 310,
    "core/quality_gate/constitution/profile.py::_build_defaults": 304,
    # 302 at Round 80 站2, up from 261: the mutmut version precondition and the
    # zero-mutant refusal, both of which are comment-heavy because they record
    # what the branch used to return and why that was wrong.
    "core/quality_gate/mutation_enforcer.py::_compute_mutation_score": 302,
    "core/auto_fix/__init__.py::AutoFixEngine.fix": 248,
    "cli/push_cmds.py::cmd_push_milestone": 241,
    "harness/harness_bridge.py::_crg_enrich_gate_findings": 234,
    "cli/gate_cmds.py::_check_gate4_prerequisites": 232,
    "cli/phase_cmds.py::_verify_entry_gate": 230,
    "cli/project_cmds.py::cmd_audit_structure": 216,
    "harness/harness_bridge.py::HarnessBridge.prepare_gate": 215,
    "harness/gate_checks.py::_check_tool_evidence": 206,
    "harness/ssi/scripts/crg_analysis.py::compute_community_cohesion_score": 206,
    "scripts/plangen/blocks.py::_gate_exit_checkpoint": 204,
    "core/doctor.py::run_doctor": 202,
    "core/quality_gate/spec_tracking_checker.py::compute_trace_dimension": 201,
}


def _functions_in(path: Path) -> "list[tuple[str, int]]":
    """(dotted-within-file name, line count) for every def, methods included.

    Methods are the point: the longest function in the repo is one
    (`HarnessBridge.finalize_gate`), and a scan that only saw module level
    would have reported the repo's worst case as 940 lines rather than 1150.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:  # pragma: no cover - ruff owns syntax
        return []

    out: list[tuple[str, int]] = []

    def walk(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                walk(child, f"{prefix}{child.name}.")
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                end = child.end_lineno or child.lineno
                out.append((f"{prefix}{child.name}", end - child.lineno + 1))
                walk(child, f"{prefix}{child.name}.")

    walk(tree, "")
    return out


def _measured() -> "dict[str, int]":
    sizes: dict[str, int] = {}
    for directory in _SCAN_DIRS:
        root = REPO / directory
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            rel = path.relative_to(REPO).as_posix()
            for name, length in _functions_in(path):
                sizes[f"{rel}::{name}"] = length
    return sizes


def test_no_function_exceeds_its_ceiling():
    sizes = _measured()
    over = [
        f"{key}: {length} lines > ceiling "
        f"{_CEILINGS.get(key, _DEFAULT_CEILING)}"
        for key, length in sorted(sizes.items())
        if length > _CEILINGS.get(key, _DEFAULT_CEILING)
    ]
    assert not over, (
        "these functions are longer than they are allowed to be:\n  "
        + "\n  ".join(over)
        + f"\n\nA function not named in _CEILINGS has a ceiling of "
          f"{_DEFAULT_CEILING}. Split it, or — if the length is deliberate — "
          f"add it to _CEILINGS in THIS commit with the reason, the way "
          f"tests/test_file_size_ratchet.py's entries carry theirs."
    )


def test_no_ceiling_sits_above_the_function_it_covers():
    """A ceiling above the count is growth nobody reviewed, pre-authorised.

    Round 78 站3 rewrote a file-ratchet entry for exactly this: two commits
    moved the integer and left the note, and the file sat 101 lines below its
    own limit. Here it is mechanical rather than a convention — a function that
    shrinks has its number lowered in the commit that shrank it.
    """
    sizes = _measured()
    slack = [
        f"{key}: ceiling {ceiling}, actual {sizes[key]} "
        f"(harvest it — set the ceiling to {sizes[key]})"
        for key, ceiling in sorted(_CEILINGS.items())
        if key in sizes and ceiling > sizes[key]
    ]
    assert not slack, (
        "these ceilings are above the function they cover, which pre-"
        "authorises the growth this ratchet exists to make visible:\n  "
        + "\n  ".join(slack)
    )


def test_no_ceiling_names_a_function_that_is_gone():
    """A split or a rename leaves its entry behind; the entry then guards air."""
    sizes = _measured()
    stale = sorted(key for key in _CEILINGS if key not in sizes)
    assert not stale, (
        "these _CEILINGS entries name nothing that exists — a function that "
        "was split, renamed or moved leaves its ceiling behind, and the next "
        "reader inherits a number that guards air:\n  " + "\n  ".join(stale)
    )


def test_the_scan_sees_methods_not_only_module_level_functions():
    """The repo's longest function is a method; a scan that missed it would
    have reported the worst case as 940 lines instead of 1150."""
    sizes = _measured()
    key = "harness/harness_bridge.py::HarnessBridge.finalize_gate"
    assert key in sizes, (
        f"{key} is not in the scan — methods are being missed, and the "
        f"largest function in this repository is one"
    )
    assert sizes[key] > _DEFAULT_CEILING


def test_the_ratchet_can_see_a_function_that_is_too_long():
    """The detector's own witness, read off the AST rather than off text."""
    long_body = "def f():\n" + "".join(f"    x{i} = {i}\n" for i in range(250))
    parsed = _functions_in_source(long_body)
    assert parsed == [("f", 251)], parsed
    assert parsed[0][1] > _DEFAULT_CEILING


def _functions_in_source(source: str) -> "list[tuple[str, int]]":
    """`_functions_in` against a string, for the witness above."""
    import tempfile

    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as handle:
        handle.write(source)
        tmp = Path(handle.name)
    try:
        return _functions_in(tmp)
    finally:
        tmp.unlink(missing_ok=True)
