"""Round 77 站4 — "which test file is FR-NN's?" has one answer.

Twenty-two places in the production tree build a test filename out of an FR
id, across ten files, using FOUR different derivations of the number:

    core/canonical_form.fr_num_str(fr_id)            the SSOT
    re.match(r"FR-(\\d+)", fr_id, I).group(1).zfill(2)   gate_cmds ×3,
                                                     cov_utils, gate1_evidence
    re.match(r"N?FR-(\\d+)", fr_id).group(1).zfill(2)    red_assertion_check
    re.search(r"(\\d+)", fr_id) → f"{n:02d}"             property_check

Round 76 added a fifth (`f"test_fr{int(fr_num):02d}"` inside
`_check_tests_failed`) and got three real shapes wrong with it — `test_fr7.py`
read as another FR's file, `test_fr100.py` read as FR-10's, and
`src/test_fr08_util.py` read as FR-08's own test. Round 77 站1 removed that
copy by routing S4-B through `test_suite_run.select_fr_outcomes`. This module
is what stops a sixth from appearing unnoticed.

MEASURED, and the reason this is a registry rather than a refactor: the four
derivations agree on every FR id `canonical_form` can produce. They differ on
exactly one input, `FR-008` — SSOT and property_check say `08`, the other two
say `008` — and `canonical_form("FR-008")` is `FR-08`, so no framework
producer emits that spelling. Measured across the nine corpus projects on
this machine that have FR test files at all (taskq, taskq-plus, taskq-renew,
taskq-api, taskq-advance, taskq-super, taskq-cc, taskq-new,
run-all-by-workflow; taskq-mm has none): every FR test file is
`test_frNN.py`, two-digit padded — zero `test_fr008.py`, zero
`test_fr_08.py`, zero uppercase.

So this is a latent divergence, not a live wound, and it is pinned rather than
rewritten: changing `cov_utils` or `gate_cmds` to the SSOT would change what
they resolve for a `test_fr008.py` that no project has, which is a behaviour
change bought with no measured benefit (Round 74's honest-labelling rule — a
latent sibling is recorded as one).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from core.canonical_form import canonical_form, fr_id_to_test_filename, fr_num_str

pytestmark = [pytest.mark.core]

REPO = Path(__file__).resolve().parents[1]
_SCAN_DIRS = ("cli", "core", "harness", "scripts", "detection")


# ── the four derivations, transcribed from their call sites ──────────────────

def _ssot(fr_id: str) -> "str | None":
    """core/canonical_form.py::fr_num_str — via canonical_form() first."""
    return fr_num_str(fr_id)


def _fr_anchored(fr_id: str) -> "str | None":
    """cli/gate_cmds.py::_check_fr_test_file_exists / _check_red_phase_ordering /
    _cmd_run_gate_impl, core/quality_gate/cov_utils.py::shared_owner_test_files,
    core/quality_gate/gate1_evidence.py::fr_code_changed_since_last_gate1."""
    m = re.match(r"FR-(\d+)", fr_id, re.IGNORECASE)
    return m.group(1).zfill(2) if m else None


def _nfr_tolerant(fr_id: str) -> "str | None":
    """core/quality_gate/red_assertion_check.py::_extract_parametrize."""
    m = re.match(r"N?FR-(\d+)", fr_id)
    return m.group(1).zfill(2) if m else None


def _first_digits_anywhere(fr_id: str) -> "str | None":
    """core/quality_gate/property_check.py::_fr_tokens."""
    m = re.search(r"(\d+)", fr_id)
    return f"{int(m.group(1)):02d}" if m else None


DERIVATIONS = {
    "canonical_form.fr_num_str": _ssot,
    "gate_cmds/cov_utils/gate1_evidence inline zfill": _fr_anchored,
    "red_assertion_check N?FR- inline zfill": _nfr_tolerant,
    "property_check first-digits-anywhere": _first_digits_anywhere,
}

# Every production file that interpolates an FR number straight after the
# literal `test_fr`. Keys are checked against an AST scan below, so a new one
# cannot be added without an entry here saying which derivation it uses.
FR_TEST_FILENAME_SITES: dict[str, str] = {
    "cli/fr_cmds.py": "canonical_form.fr_num_str",
    "cli/fr_prompts/__init__.py": "canonical_form.fr_num_str",
    # Round 82 站4: `_fr_step_already_done` and the rest of the idempotence
    # family moved here out of cli/fr_cmds.py. Same derivation, because the
    # move was byte-identical — this entry exists so the scan is not silently
    # narrower than the code it covers.
    "cli/fr_step_stages.py": "canonical_form.fr_num_str",
    "cli/gate_cmds.py": "gate_cmds/cov_utils/gate1_evidence inline zfill",
    "core/canonical_form.py": "canonical_form.fr_num_str",
    "core/quality_gate/cov_utils.py": "gate_cmds/cov_utils/gate1_evidence inline zfill",
    "core/quality_gate/property_check.py": "property_check first-digits-anywhere",
    "core/quality_gate/red_assertion_check.py": "red_assertion_check N?FR- inline zfill",
    "core/quality_gate/spec_coverage.py": "caller-supplied",
    "core/quality_gate/test_suite_run.py": "canonical_form.fr_num_str",
    "scripts/plangen/blocks.py": "canonical_form.fr_num_str",
}

# `_git_test_patterns(project, num, num_raw)` takes the number from its caller
# — cli/gate_cmds.py::_check_red_phase_ordering and
# core/quality_gate/gate1_evidence.py::fr_code_changed_since_last_gate1, both
# of which spell the inline zfill themselves. It is not a fifth derivation.
_CALLER_SUPPLIED = "caller-supplied"


def _every_id_the_framework_can_produce() -> list[str]:
    """`canonical_form`'s own output for one- through three-digit FRs.

    The population that matters: a spelling no producer emits cannot reach a
    consumer through any sanctioned path, so agreeing on it buys nothing.
    """
    return [canonical_form(f"FR-{n}") for n in list(range(1, 121)) + [150, 999]]


def test_every_derivation_agrees_on_every_id_the_framework_can_produce():
    disagreements = []
    for fr_id in _every_id_the_framework_can_produce():
        answers = {name: fn(fr_id) for name, fn in DERIVATIONS.items()}
        distinct = {v for v in answers.values() if v is not None}
        if len(distinct) > 1:
            disagreements.append((fr_id, answers))
    assert not disagreements, (
        "two production sites resolve the same FR to different test files:\n  "
        + "\n  ".join(f"{fr}: {ans}" for fr, ans in disagreements[:5])
    )


def test_the_one_input_they_disagree_on_is_the_one_recorded():
    """Pinned so the module docstring cannot rot into a claim nobody checks.

    If this starts failing because a NEW input diverges, that input belongs in
    the docstring with its measurement — or the derivations belong merged.
    """
    diverging = [
        fr for fr in ("FR-008", "FR-0008", "FR-08", "FR-8", "NFR-02", "fr01",
                      "FR_08", "FR-08: Login", "TASK-3")
        if len({fn(fr) for fn in DERIVATIONS.values() if fn(fr) is not None}) > 1
    ]
    assert diverging == ["FR-008", "FR-0008"], diverging
    assert _ssot("FR-008") == "08" and _fr_anchored("FR-008") == "008"
    assert canonical_form("FR-008") == "FR-08", (
        "the divergence is only latent while no framework producer emits the "
        "zero-padded-three spelling")


def test_abstaining_is_not_the_same_as_disagreeing():
    """Two of the four return None for `fr01` / `FR_08` / `TASK-3` rather than
    a different answer. Their call sites all treat None as "not an FR I can
    scope" and skip, which is a different failure mode from resolving to the
    wrong file — worth keeping visible, not worth collapsing."""
    for fr_id in ("fr01", "FR_08", "TASK-3"):
        assert _fr_anchored(fr_id) is None
        assert _ssot(fr_id) is not None, (
            "the SSOT canonicalises first, which is why S4-B routes through it")


def test_the_registry_names_every_site_that_builds_a_test_filename():
    """Scans for the BEHAVIOUR — an FR number interpolated straight after the
    literal `test_fr` — not for a string one of them happens to contain.

    Round 74 站3's third self-correction was a completeness scan that looked
    for the exact string that station had just removed, so it could only ever
    find the unfixed readers. An f-string shape survives the fix.
    """
    found: set[str] = set()
    for directory in _SCAN_DIRS:
        for path in sorted((REPO / directory).rglob("*.py")):
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except SyntaxError:  # pragma: no cover — not our files
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.JoinedStr):
                    continue
                parts = node.values
                for i, part in enumerate(parts):
                    if (isinstance(part, ast.Constant)
                            and isinstance(part.value, str)
                            and part.value.endswith("test_fr")
                            and i + 1 < len(parts)
                            and isinstance(parts[i + 1], ast.FormattedValue)):
                        found.add(path.relative_to(REPO).as_posix())

    unregistered = found - set(FR_TEST_FILENAME_SITES)
    assert not unregistered, (
        f"{sorted(unregistered)} builds a test filename from an FR number and "
        f"is not in FR_TEST_FILENAME_SITES. Say which derivation it uses — or "
        f"call core.canonical_form.fr_num_str and register it as the SSOT. "
        f"Round 76 added the fifth copy without saying so and got test_fr7.py, "
        f"test_fr100.py and src/test_fr08_util.py wrong."
    )
    gone = set(FR_TEST_FILENAME_SITES) - found
    assert not gone, (
        f"{sorted(gone)} no longer builds a test filename — drop it from the "
        f"registry in the same commit, so this list stays a census rather "
        f"than a memory")


def test_every_registered_derivation_exists():
    for site, derivation in FR_TEST_FILENAME_SITES.items():
        assert derivation in DERIVATIONS or derivation == _CALLER_SUPPLIED, (
            f"{site} names a derivation this module does not transcribe: "
            f"{derivation}")


# ── the two mappers a caller can reach, checked by behaviour ─────────────────

def test_the_ssot_filename_and_the_ownership_predicate_agree():
    """`fr_id_to_test_filename` says where an FR's test file goes;
    `select_fr_outcomes` says whether a nodeid is that FR's. S4-B's verdict
    depends on the second matching the first."""
    from core.quality_gate.test_suite_run import select_fr_outcomes

    for fr_id in _every_id_the_framework_can_produce():
        path = fr_id_to_test_filename(fr_id, "tests")
        nodeid = f"{path}::test_x"
        assert select_fr_outcomes({nodeid: "failed"}, fr_id) == {nodeid: "failed"}, (
            f"{fr_id}: the file the framework tells the project to create "
            f"({path}) is not one S4-B recognises as that FR's")


def test_the_tdd_precheck_finds_both_spellings_it_documents(tmp_path):
    """cli/gate_cmds.py's docstring promises "test_fr07.py or test_fr7.py".
    Behavioural, because that promise is what made Round 76's padded-only
    pattern waive FR-07's own red test as somebody else's."""
    from cli.gate_cmds import _check_fr_test_file_exists

    tests = tmp_path / "tests"
    tests.mkdir()
    assert _check_fr_test_file_exists(tmp_path, "FR-07")[0] is False

    (tests / "test_fr7.py").write_text("def test_fr07_x(): pass\n", encoding="utf-8")
    assert _check_fr_test_file_exists(tmp_path, "FR-07")[0] is True

    (tests / "test_fr7.py").unlink()
    (tests / "test_fr07.py").write_text("def test_fr07_x(): pass\n", encoding="utf-8")
    assert _check_fr_test_file_exists(tmp_path, "FR-07")[0] is True
