"""Round 106 站A — the marker was advice, and advice is not a mechanism.

Round 105 marked the example values this framework writes into a project's own
deliverables, on the strength of a measurement that said marking was the only
variable: the marked SAB `layers` example had leaked into 0 of 13 corpus
projects while the unmarked ones leaked into 2 and 3.

**That measurement was wrong.** It grepped `app.api.routes`, and the template's
literal is `render_canonical_sab_template(module_example="app.api.webhooks")`.
Re-measured against what the template actually emits:

    SAB layers example (4 module paths)   MARKED    1/13  taskq-forever
    quality_targets.min_coverage: 80      unmarked  2/13
    TEST_INVENTORY *_example_* names      unmarked  3/13  (sn 17 hits, wow 13,
                                                           forever 8)

taskq-forever's SAD.md:155 carries `layers:  # EXAMPLE — replace with your
project's layers` verbatim, and `app.api.webhooks` sits under it. Marking
reduces the leak; it does not stop it. Only a check does (Round 30/43).

WHAT MAY BE DECIDED, AND WHAT MAY NOT

Three candidate rules were measured. Two are false accusations and this file
pins them out:

  "the marker is still there, so the line was not replaced"
      taskq-renew's SAD.md:551 keeps the same marker line and the modules
      under it are `taskq_plus.cli.main` — its own. Refuted.

  "the value is still 80, so it was inherited"
      A project may consider the question and choose 80. Nothing here can
      tell that apart from never having looked.

  "a framework-invented identifier appears in a delivered artifact"
      Right in general, wrong as first written: `app.main` is the most common
      module path in any FastAPI project, and a project whose package IS `app`
      would be charged with copying the template.

What survives is two rules, each decidable on its own terms:

  Rule 1  an identifier carrying the word `example` — the four test-function
          names. No project names a test `test_security_example`.
  Rule 2  a module path the SAB template emits, whose ROOT PACKAGE the project
          does not deliver. A project with a real `app` package is never
          reported; taskq-forever, which delivers no package at all, is.

Measured across the thirteen corpus projects: forever 8+7, sn 17+0, wow 13+0,
and every other project — taskq-renew included — zero on both.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

pytestmark = [pytest.mark.core]

REPO = Path(__file__).resolve().parents[1]


def _project(tmp_path: Path, *, inventory: str = "", sad: str = "",
             packages: "tuple[str, ...]" = ()) -> Path:
    root = tmp_path / "proj"
    (root / "02-architecture").mkdir(parents=True)
    (root / ".methodology").mkdir(exist_ok=True)
    if inventory:
        (root / "TEST_INVENTORY.yaml").write_text(inventory, encoding="utf-8")
    if sad:
        (root / "02-architecture" / "SAD.md").write_text(sad, encoding="utf-8")
    for pkg in packages:
        d = root / "03-development" / "src" / pkg
        d.mkdir(parents=True, exist_ok=True)
        (d / "__init__.py").write_text("", encoding="utf-8")
        (d / "main.py").write_text("", encoding="utf-8")
    return root


def _check(project: Path, phase: int):
    from cli.advance_prechecks import _precheck_framework_examples_were_replaced

    return _precheck_framework_examples_were_replaced(phase, project)


# ── rule 1: an identifier that says it is an example ────────────────────────

def test_a_template_test_name_in_the_inventory_is_reported(tmp_path, capsys) -> None:
    """taskq-sn / taskq-wow / taskq-forever all shipped these four verbatim."""
    from cli.exit_codes import EX_ADVANCE_TEMPLATE_EXAMPLE_DELIVERED

    project = _project(tmp_path, inventory=(
        "test_inventory:\n  tests:\n"
        "    - tc_id: TC-FR01-01\n"
        "      test_function: test_fr01_example_integration\n"))
    assert _check(project, 1) == EX_ADVANCE_TEMPLATE_EXAMPLE_DELIVERED
    out = capsys.readouterr().out
    assert "test_fr01_example_integration" in out
    assert "TEST_INVENTORY.yaml" in out, out


def test_a_project_that_named_its_own_tests_is_not_reported(tmp_path) -> None:
    """Reverse control."""
    project = _project(tmp_path, inventory=(
        "test_inventory:\n  tests:\n"
        "    - tc_id: TC-FR01-01\n"
        "      test_function: test_fr01_submit_returns_an_id\n"))
    assert _check(project, 1) is None


# ── rule 2: a module path whose package the project does not deliver ────────

def test_a_template_module_path_with_no_such_package_is_reported(tmp_path) -> None:
    """taskq-forever's shape: the SAB layers block, unedited, and no `app`
    package anywhere in the delivered tree."""
    from cli.exit_codes import EX_ADVANCE_TEMPLATE_EXAMPLE_DELIVERED

    project = _project(tmp_path, sad=(
        "# Software Architecture Document\n\n"
        "  layers:  # EXAMPLE — replace with your project's layers\n"
        "    - name: api\n      modules:\n"
        '        - name: "app.api.webhooks"\n'),
        packages=("taskq_api",))
    assert _check(project, 2) == EX_ADVANCE_TEMPLATE_EXAMPLE_DELIVERED


def test_a_project_that_delivers_the_package_is_not_accused(tmp_path) -> None:
    """The rule this file exists to keep narrow.

    `app.main` is the most common module path in any FastAPI project. The
    first version of rule 2 matched the identifier alone and would have
    charged every project whose package is called `app` with copying the
    framework's example. The root package has to be one the project does NOT
    deliver — Round 46's line between an accusation and a reading.
    """
    project = _project(tmp_path, sad=(
        "# Software Architecture Document\n\n"
        "  layers:\n    - name: api\n      modules:\n"
        '        - name: "app.api.webhooks"\n'
        '          implemented_in: "app.main"\n'),
        packages=("app",))
    assert _check(project, 2) is None


def test_a_replaced_block_that_kept_the_marker_is_not_accused(tmp_path) -> None:
    """taskq-renew's shape, pinned.

    Its SAD.md:551 keeps `layers:  # EXAMPLE — replace with the project's
    layers` and lists `taskq_plus.cli.main` under it. "The marker is still
    there" was the obvious rule and it would have charged the one project that
    did the work and left a comment behind.
    """
    project = _project(tmp_path, sad=(
        "# Software Architecture Document\n\n"
        "  layers:  # EXAMPLE — replace with the project's layers\n"
        "    - name: cli\n      modules:\n"
        '        - name: "taskq_plus.cli.main"\n'),
        packages=("taskq_plus",))
    assert _check(project, 2) is None


# ── the registry ────────────────────────────────────────────────────────────

def test_the_module_paths_come_from_the_template_not_from_a_literal() -> None:
    """The direct pin on Round 105's mistake.

    That round hand-wrote `app.api.routes` and measured zero leaks with it.
    The paths are now read out of what `render_canonical_sab_template()`
    actually emits, parsed from the block's own YAML — so a change to the
    template's example moves this list, and no literal in this repository can
    disagree with the file the project is handed.
    """
    from core.quality_gate.legal_artifacts import sab_template_module_paths
    from core.quality_gate.sab_parser import render_canonical_sab_template

    paths = sab_template_module_paths()
    assert paths, "the SAB template emits no module path — the derivation broke"
    rendered = render_canonical_sab_template()
    for path in paths:
        assert path in rendered, (
            f"{path!r} is not in the rendered template; this list is supposed "
            f"to be derived from it, not written beside it")
    assert "app.api.routes" not in paths, (
        "`app.api.routes` was Round 105's hand-written guess and is not what "
        "the template emits")


def test_the_check_reads_the_template_and_not_a_copy_of_its_answer(
        tmp_path, monkeypatch) -> None:
    """The counter-proof above only moved the derivation; this one moves the
    TEMPLATE and asks whether the check followed.

    CP-4b: replacing `sab_template_module_paths()` inside
    `framework_examples_in` with the four correct literals left all twelve
    other tests green. A faithful re-implementation is invisible to a test
    that only inspects the SSOT — Round 97 CP-5b, Round 98 CP-11, Round 99
    CP-13b and Round 105 CP-8 are the same shape. So make the template say
    something else and require the check to report THAT.
    """
    from core.quality_gate import sab_parser
    from core.quality_gate.legal_artifacts import framework_examples_in

    monkeypatch.setattr(
        sab_parser, "render_canonical_sab_template",
        lambda *a, **k: (
            "sab:\n  layers:\n    - name: api\n      modules:\n"
            '        - name: "zzzsample.api.thing"\n'),
    )
    project = _project(tmp_path, sad=(
        "# Software Architecture Document\n\n"
        '        - name: "zzzsample.api.thing"\n'
        '        - name: "app.api.webhooks"\n'), packages=("taskq_api",))
    found = framework_examples_in(project, 2)
    assert any("zzzsample.api.thing" in row for row in found), (
        "the template now emits `zzzsample.api.thing` and the check did not "
        "report it — it is reading a copy of the template's answer, not the "
        f"template: {found}")


def test_every_example_test_name_is_in_the_shipped_template() -> None:
    """A registry entry naming something no template contains is a rule that
    cannot fail."""
    from core.quality_gate.legal_artifacts import TEMPLATE_EXAMPLE_TEST_NAMES

    text = (REPO / "templates" / "TEST_INVENTORY.yaml").read_text(encoding="utf-8")
    missing = [n for n in TEMPLATE_EXAMPLE_TEST_NAMES if n not in text]
    assert not missing, missing


# ── where it is asked ───────────────────────────────────────────────────────

def test_it_is_asked_at_the_phase_that_produced_the_artifact(tmp_path) -> None:
    """TEST_INVENTORY.yaml is Phase 1's; SAD.md / TEST_SPEC.md / SAB.json are
    Phase 2's. Each is read once, at the boundary that closes the phase which
    wrote it — asking every phase about every artifact is Round 20."""
    inventory = _project(tmp_path / "a", inventory=(
        "test_inventory:\n  tests:\n"
        "    - test_function: test_security_example\n"))
    assert _check(inventory, 1) is not None
    for phase in (2, 3, 4, 5, 6, 7, 8):
        assert _check(inventory, phase) is None, f"phase {phase} read P1's artifact"

    sad = _project(tmp_path / "b", sad=(
        "# Software Architecture Document\n\n"
        '        - name: "app.processing.pipeline"\n'), packages=("taskq_api",))
    assert _check(sad, 2) is not None
    for phase in (1, 3, 4, 5, 6, 7, 8):
        assert _check(sad, phase) is None, f"phase {phase} read P2's artifact"


def test_advance_phase_runs_the_check() -> None:
    """A check with no caller is Round 30's half-built mechanism.

    The first version of this station put the check in
    `cli/handoff_validators._validate_handoff_p1_to_p2`. That function's only
    caller is `cmd_validate_handoff`, which the framework never invokes — a
    line of Phase 2's prompt asks the AGENT to run it. An instruction is not
    an executor, which is the defect this round is closing, not one to add.
    """
    from tests.support.pipeline import inlined

    fn = inlined("cli/phase_cmds.py", "_advance_prechecks",
                 helper_prefix="_precheck_")
    called = {
        n.func.attr if isinstance(n.func, ast.Attribute) else n.func.id
        for n in ast.walk(fn)
        if isinstance(n, ast.Call) and isinstance(n.func, (ast.Name, ast.Attribute))
    }
    assert "framework_examples_in" in called, (
        "_advance_prechecks never asks whether the framework's own example "
        "values are still in the delivered artifacts")


def test_the_exit_code_is_registered_with_a_description() -> None:
    from cli.exit_codes import REGISTRY, EX_ADVANCE_TEMPLATE_EXAMPLE_DELIVERED

    assert EX_ADVANCE_TEMPLATE_EXAMPLE_DELIVERED in REGISTRY
    assert "example" in REGISTRY[EX_ADVANCE_TEMPLATE_EXAMPLE_DELIVERED].lower()


def test_nothing_to_read_is_not_a_violation(tmp_path) -> None:
    """Reverse control. A tree with no deliverable yet has nothing to report,
    and an absent artifact is the business of the checks that demand it."""
    bare = tmp_path / "bare"
    bare.mkdir()
    assert _check(bare, 1) is None and _check(bare, 2) is None


def test_a_json_sab_is_read_too(tmp_path) -> None:
    """`.methodology/SAB.json` is the rendered form of SAD.md §5 and is what
    every later check actually reads, so a template module surviving there is
    the same defect one file further on."""
    project = _project(tmp_path, packages=("taskq_api",))
    (project / ".methodology" / "SAB.json").write_text(
        json.dumps({"sab": {"layers": [
            {"name": "api", "modules": [{"name": "app.service.handlers"}]}]}}),
        encoding="utf-8")
    assert _check(project, 2) is not None
