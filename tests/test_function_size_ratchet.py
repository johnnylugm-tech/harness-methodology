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

and the churn sits exactly there. Meanwhile the file ratchet's ceilings were
raised 298 times and lowered 5 — `harness_bridge.py` alone 56 times — because
raising a ceiling is what you do when the thing that grew is a function and
nothing is asking about functions.

THE CHURN NUMBERS THIS PARAGRAPH USED TO CARRY WERE MEASURED WITH A BLIND RULER

Round 80 wrote "94 hunks landed in `cmd_advance_phase`, 81 in
`_run_harness_cross_validation`, 51 in `_advance_prechecks`" and stopped there,
because those were the three largest counts a hunk-header scan could produce.
It could not produce a fourth: without `.gitattributes`, git's default diff
driver matches a definition starting in column 0, so all 11442 hunk headers in
this repo's history named a top-level `def` or a `class` and **not one of them
named a method** — 626 methods in 175 classes, invisible. Round 81 站1 set
`*.py diff=python`; re-measured over the same five directories:

    155  harness/harness_bridge.py::finalize_gate      <- rank 1, previously 0
    102  scripts/generate_full_plan.py::generate_phase4_tasks
     94  cli/phase_cmds.py::cmd_advance_phase
     87  core/agent_spawner.py::AgentSpawner.spawn     <- previously 0
     ...
     52  cli/phase_cmds.py::_advance_prechecks

The entry at the top of the size list above is also the most-edited function in
the repository, and the old ruler scored it zero. The claim was not wrong; it
was three names short of its own strongest case. Eight of the corrected top 40
are methods that could not previously appear at all.

Neither ruler is exact — the corrected one attributes a hunk to the NEAREST
preceding definition, so `finalize_gate`'s 155 excludes the 11 in its nested
`_effective_threshold` / `_dim_passes`, and `cmd_run_fr_step`'s count is split
across its four nested defs. The decision this file encodes does not rest on
these numbers: the ceilings come from the AST size scan in `_functions_in`,
which has always seen methods, which is why `HarnessBridge.finalize_gate` is
first in `_CEILINGS` and why `test_the_scan_sees_methods_not_only_module_level_
functions` exists.

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
    # 899 at Round 81 站8, from 1150: sixteen runs extracted to `_stage_*`
    # methods. Still the longest function here, and still first in this
    # dict — 275 of its lines were safely extractable and the rest binds
    # what follows it. Harvested in the same commit, because
    # test_no_ceiling_sits_above_the_function_it_covers does not allow
    # otherwise.
    "harness/harness_bridge.py::HarnessBridge.finalize_gate": 899,
    # 770 at Round 81 站9, from 940: four runs extracted. The smallest
    # harvest of the four, because only 182 of its lines are extractable
    # under the rule — the rest threads state through the dispatch loop.
    "cli/fr_cmds.py::cmd_run_fr_step": 770,
    # 413 at Round 81 站7, from 845: seven runs extracted. Harvested in the
    # same commit because test_no_ceiling_sits_above_the_function_it_covers
    # does not allow otherwise.
    "cli/phase_cmds.py::cmd_advance_phase": 413,
    # The tail of the advance: git commit, submodule bump, push, and the
    # rollback each of those needs. One run, because every prefix of it
    # binds something the rest reads — the same shape as
    # `_precheck_p3_security_and_quality`, and the same decision.
    # Round 82 站3 moved it to cli/advance_steps.py with the other six and
    # `_run_doctor_after_advance`. Same 257: the move was a move, and its
    # god-file fingerprint did not change.
    "cli/advance_steps.py::_advance_step_commit_and_push": 257,
    # 238 at Round 81 站6, from 818: nine runs extracted. The harvest is
    # forced rather than remembered — test_no_ceiling_sits_above_the_
    # function_it_covers fails until this number is lowered in the same
    # commit as the shrink, which is exactly what Round 78 站3 rewrote a
    # file-ratchet entry for.
    "cli/phase_cmds.py::_advance_prechecks": 244,
    # The one run with NO safe cut point under the extraction rule: every
    # prefix of it binds something the rest reads, so it comes out whole or
    # not at all. 276 lines in a helper beats 276 lines inside an 818-line
    # function, and naming it here is how the next round finds it.
    # Round 82 站2 moved it to cli/advance_prechecks.py with the other eight.
    # Re-keyed rather than re-measured: the number is the same 279 because the
    # move was a move — tests/test_god_file_split_safety.py's fingerprint for
    # it did not change, and this key changing while that digest does not is
    # what a relocation looks like from here.
    "cli/advance_prechecks.py::_precheck_p3_security_and_quality": 279,
    "cli/gate_cmds.py::_cmd_finalize_gate_impl": 606,
    "harness/harness_bridge.py::_run_harness_cross_validation": 475,

    "cli/project_cmds.py::cmd_init_project": 417,
    "core/quality_gate/security_design.py::check_security_design": 322,
    "core/agent_spawner.py::AgentSpawner.spawn": 316,
    "detection/drift_detector.py::DriftDetector.detect_sab_drift": 310,
    "core/quality_gate/constitution/profile.py::_build_defaults": 304,
    # 310 at Round 80 站11, from 302 at 站2 and 261 before that: the mutmut
    # version precondition, the zero-mutant refusal, and 站11's correction that
    # an UNKNOWN version is not an unsupported one. Comment-heavy on purpose —
    # each records what the branch used to return and why that was wrong.
    "core/quality_gate/mutation_enforcer.py::_compute_mutation_score": 310,
    "core/auto_fix/__init__.py::AutoFixEngine.fix": 248,
    "cli/push_cmds.py::cmd_push_milestone": 241,
    # Moved to harness/gate_crg.py by Round 81 站3, byte-identical — the number
    # is unchanged because the function is. Round 80 could not move it: its
    # closure pulled in `_atomic_write_gate_result`, and 站2 moved that first.
    "harness/gate_crg.py::_crg_enrich_gate_findings": 234,
    "cli/gate_cmds.py::_check_gate4_prerequisites": 232,
    "cli/phase_cmds.py::_verify_entry_gate": 230,
    "cli/project_cmds.py::cmd_audit_structure": 216,
    "harness/harness_bridge.py::HarnessBridge.prepare_gate": 215,
    "harness/gate_checks.py::_check_tool_evidence": 206,
    "harness/ssi/scripts/crg_analysis.py::compute_community_cohesion_score": 206,
    "scripts/plangen/blocks.py::_gate_exit_checkpoint": 204,
    # 210 at Round 81 站4, from 202: check 14b (`_check_hook_wiring`) and the
    # comment recording why it sits beside check 14 rather than at the end —
    # `init-project` installs a CI workflow and git hooks, and until now this
    # function asked about only one of them. A raise, and named as one: this
    # function is a registry of sixteen extends and every round that adds a
    # check adds to it.
    "core/doctor.py::run_doctor": 210,
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
