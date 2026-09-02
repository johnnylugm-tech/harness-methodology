"""A check that scans a project must survive being handed a symlink to it.

Round 88 站1. Every scanner in this framework walks a directory that came out
of `ProjectLayout`, which resolves, and then relativises the results against a
root the CALLER supplied, which may not be resolved. On macOS `/tmp` is a
symlink to `/private/tmp`, and any project reached through a symlink has the
same shape, so `path.relative_to(root)` raises ValueError on a tree with
nothing wrong with it.

Round 87 站9 fixed exactly one instance, `runtime_test_seams`, and did not
sweep for its siblings. Pointing the framework's checks at nine real delivered
trees — the commits their own `phase_completed["3"].sha` name — found three
more:

    core/traceability/scanner.py::check_traceability          9 of 9 trees
    core/quality_gate/red_assertion_check.py::spec_ambiguity_notes   2 of 9
    core/quality_gate/artifact_consistency.py::check_ac_deferral_targets  1 of 9

WHY THIS TEST AND NOT AN AST LINT

97 `relative_to(` call sites in this repo. A lint over them has a false
positive rate nobody has measured, and Round 87's rule is that a rule's shape
is chosen by measurement. This asks the question the defect is actually about
— can this entry point be called with a symlinked project — of a registry of
entry points, and a new scanner joins the registry rather than the lint.

WHY THE REPLAY IS NOT THE EXECUTOR

The corpus replay is what FOUND these, and the first draft of this round's
plan made it their enforcer by passing an unresolved path. That is a
discovery tool, not a guard: once the sites are fixed the replay simply
passes, and a regression would only be caught if someone re-derived the same
unresolved-path trick. Round 43's shape, inverted.
"""
from __future__ import annotations

import importlib
import textwrap
from pathlib import Path

import pytest

pytestmark = [pytest.mark.core]

#: Public entry points that take a project (or project-root) path, walk the
#: tree under it, and report paths relative to it. A scanner added without an
#: entry here is a scanner nothing asks this question of.
#:
#: `kwarg` names the parameter that receives the ROOT to relativise against;
#: `dir_kwarg`, where present, is the directory to walk. Those two arrive
#: separately and the whole defect lives in the gap: the caller in production
#: (`scan_all`) passes `ProjectLayout(project).active_test_dir`, which IS
#: resolved, beside a `project` that is not. Handing both the same unresolved
#: path made three of these pass for the wrong reason on the first draft of
#: this file, so `dir_resolved` reproduces the real pairing.
_SCANNERS: tuple[dict, ...] = (
    {"module": "core.traceability.scanner", "func": "check_traceability", "kwarg": "project"},
    {"module": "core.traceability.scanner", "func": "scan_all", "kwarg": "project"},
    {"module": "core.traceability.scanner", "func": "scan_fr_annotations", "kwarg": "project"},
    {"module": "core.traceability.scanner", "func": "scan_test_fr_coverage",
     "kwarg": "project_root", "dir_kwarg": "tests_dir", "dir": "03-development/tests",
     "dir_resolved": True},
    {"module": "core.traceability.scanner", "func": "scan_test_nfr_coverage",
     "kwarg": "project_root", "dir_kwarg": "tests_dir", "dir": "03-development/tests",
     "dir_resolved": True},
    {"module": "core.quality_gate.red_assertion_check", "func": "spec_ambiguity_notes",
     "kwarg": "project"},
    {"module": "core.quality_gate.artifact_consistency", "func": "check_ac_deferral_targets",
     "kwarg": "project"},
    {"module": "core.quality_gate.test_seam_in_production", "func": "runtime_test_seams",
     "kwarg": "project"},
    {"module": "core.quality_gate.test_seam_in_production", "func": "check_test_seams",
     "kwarg": "project"},
    {"module": "core.quality_gate.criteria_review", "func": "review_sources",
     "kwarg": "project", "extra": {"fr_id": "FR-01"}},
    {"module": "core.quality_gate.spec_coverage", "func": "spec_coverage_report",
     "kwarg": "project"},
)

_SRC = '''
    """Module for [FR-01]."""
    from pkg import service

    _ORIGINAL = service.Task.create
    THRESHOLD_MS = 30


    def limit():
        if service.Task.create is not _ORIGINAL:
            reset_all()
'''

_TESTS = '''
    """Tests for [FR-01]."""
    # SPEC_AMBIGUITY: the criterion does not say which unit.


    def test_fr01_alpha():
        """[FR-01] alpha."""
        assert 1 == 1
'''

#: Carries a Deferred: line on purpose. Without one,
#: `check_ac_deferral_targets` returns before it relativises, and the guard
#: over it passes for the wrong reason — which is exactly what the first draft
#: of this file did.
_TEST_SPEC = """\
# TEST_SPEC.md

### FR-01: Alpha

| # | Test Function | Inputs | Type | Derivation |
|---|---|---|---|---|
| 1 | `test_fr01_alpha` | n=1 | happy_path | Q1 |

- Deferred: AC-1.2 — verified by `test_fr01_beta_not_written_yet`
"""

_SRS = """\
# Software Requirements Specification

### FR-01: Alpha

- AC-1.1 the thing holds.

---
"""

_SAD = """\
# Software Architecture Document

| FR | Module |
|---|---|
| FR-01 | pkg.service |
"""


@pytest.fixture(scope="module")
def symlinked_project(tmp_path_factory) -> Path:
    """A populated project, handed back through a symlink.

    Populated on purpose: an empty tree exits every scanner before it reaches
    a `relative_to`, which is how three of these four defects survived their
    own unit tests.
    """
    real = tmp_path_factory.mktemp("real")
    src = real / "03-development" / "src" / "pkg"
    src.mkdir(parents=True)
    (src / "__init__.py").write_text("", encoding="utf-8")
    (src / "service.py").write_text(textwrap.dedent(_SRC), encoding="utf-8")
    tests = real / "03-development" / "tests"
    tests.mkdir(parents=True)
    (tests / "test_fr01.py").write_text(textwrap.dedent(_TESTS), encoding="utf-8")
    arch = real / "02-architecture"
    arch.mkdir(parents=True)
    (arch / "TEST_SPEC.md").write_text(_TEST_SPEC, encoding="utf-8")
    (arch / "SAD.md").write_text(_SAD, encoding="utf-8")
    req = real / "01-requirements"
    req.mkdir(parents=True)
    (req / "SRS.md").write_text(_SRS, encoding="utf-8")
    (real / "SPEC.md").write_text(_SRS.replace(
        "# Software Requirements Specification", "# Spec"), encoding="utf-8")

    link = tmp_path_factory.mktemp("links") / "via_symlink"
    link.symlink_to(real, target_is_directory=True)
    return link


@pytest.mark.parametrize(
    "spec", _SCANNERS, ids=[f'{s["module"].rsplit(".", 1)[-1]}.{s["func"]}' for s in _SCANNERS])
def test_a_scanner_survives_a_symlinked_project(spec: dict, symlinked_project: Path) -> None:
    """`relative_to` against an unresolved root is the whole defect."""
    fn = getattr(importlib.import_module(spec["module"]), spec["func"])
    kwargs = dict(spec.get("extra") or {})
    if "dir_kwarg" in spec:
        walked = symlinked_project / spec["dir"]
        kwargs[spec["dir_kwarg"]] = walked.resolve() if spec.get("dir_resolved") else walked
    kwargs[spec["kwarg"]] = symlinked_project
    try:
        fn(**kwargs)
    except ValueError as exc:
        pytest.fail(
            f'{spec["module"]}.{spec["func"]} raises on a project reached '
            f"through a symlink — it relativises against a root it did not "
            f"resolve: {exc}"
        )


def test_the_fixture_would_have_caught_the_defect(symlinked_project: Path) -> None:
    """The fixture must actually reach a `relative_to`.

    A tree with no source and no tests exits every scanner early, and a guard
    over that tree passes for the wrong reason — which is how three of these
    four sites kept their own unit tests green while raising on nine real
    delivered trees. This pins that the fixture is populated enough to reach
    the code the guard is about.
    """
    from core.traceability.scanner import scan_fr_annotations

    found = scan_fr_annotations(symlinked_project)
    assert found.get("FR-01"), (
        f"the fixture no longer reaches a path-relativising branch: {found}"
    )
    assert not Path(found["FR-01"][0]).is_absolute(), (
        f"scanners must report project-relative paths, not absolute ones: "
        f"{found['FR-01'][0]}"
    )


def test_every_registered_scanner_exists() -> None:
    """A registry entry naming a function nobody has is a guard over nothing."""
    missing = [
        f'{s["module"]}.{s["func"]}' for s in _SCANNERS
        if not hasattr(importlib.import_module(s["module"]), s["func"])
    ]
    assert not missing, missing


def test_the_scanner_registry_does_not_shrink_unnoticed() -> None:
    """A registry entry removed is an entry point nobody asks the question of.

    Deliberately a count and a name set rather than an AST discovery of every
    project-scanning function: 97 `relative_to(` call sites, and a discovery
    rule's false-positive rate has not been measured. What this catches is a
    registry that shrinks; a NEW entry point that never joins it is recorded
    in the ledger as not done, with its re-open condition.
    """
    names = {f'{s["module"].rsplit(".", 1)[-1]}.{s["func"]}' for s in _SCANNERS}
    required = {
        "scanner.check_traceability", "scanner.scan_all",
        "scanner.scan_fr_annotations", "scanner.scan_test_fr_coverage",
        "scanner.scan_test_nfr_coverage",
        "red_assertion_check.spec_ambiguity_notes",
        "artifact_consistency.check_ac_deferral_targets",
        "test_seam_in_production.runtime_test_seams",
        "test_seam_in_production.check_test_seams",
        "criteria_review.review_sources", "spec_coverage.spec_coverage_report",
    }
    assert required <= names, f"registry lost: {sorted(required - names)}"
