"""Plan F (Round 50+) — advance-phase phantom module audit.

Plan F distinguishes three FR-scope resolution states that previously
collapsed into a single ``None`` return:

- ``NoScope``     — FR has no ``fr_module_traceability`` entry.
                    Legitimate: the gate falls through to whole-project
                    coverage (pre-Plan-F behaviour preserved).
- ``PhantomScope``— FR declared a module but the file is missing on
                    disk (and no package fallback resolved). Must BLOCK —
                    reporting a coverage number here would silently OK
                    a non-existent deliverable.
- Concrete       — declared module resolves to a file on disk.

These tests pin each of the three states and verify the new BLOCK path
in ``validate_fr_coverage_immediate`` and the early-fail mirror Plan F
placement in ``_advance_prechecks``.

Before Plan F, the silent fall-through meant a phantom FR's Gate 1
reported a whole-project number unrelated to its declared deliverable.
The central test in this file (``test_fr_module_paths_phantom_when_declared_-
but_missing``) exercises that exact failure shape.
"""

import json
from pathlib import Path

from core.quality_gate.gate1_evidence import (
    ModuleScope,
    _fr_module_paths,
    fr_coverage_record,
)


def _write_project(
    root: Path, *, sab_modules, manifest_modules, fr_id: str = "FR-99"
) -> Path:
    """Build a minimal project layout for ``_fr_module_paths`` tests.

    Writes ``03-development/src/`` (empty), ``.methodology/SAB.json``
    (with ``sab_modules`` registered in any layer), and
    ``.methodology/quality_manifest.json`` (with ``manifest_modules``
    mapped to ``fr_id``).
    """
    (root / "03-development" / "src").mkdir(parents=True, exist_ok=True)
    methodology = root / ".methodology"
    methodology.mkdir(exist_ok=True)

    layers = []
    for m in sab_modules:
        group = m.split(".", 1)[0]
        # Replace existing layer if same group; otherwise append.
        existing = next((layer for layer in layers if layer["name"] == group), None)
        module_entry = {"name": m}
        if existing is not None:
            existing["modules"].append(module_entry)
        else:
            layers.append({
                "name": group,
                "modules": [module_entry],
                "allowed_dependencies": [],
            })
    (methodology / "SAB.json").write_text(
        json.dumps({"sab": {"layers": layers}}), encoding="utf-8",
    )
    trace = {fr_id: manifest_modules} if manifest_modules is not None else {}
    (methodology / "quality_manifest.json").write_text(
        json.dumps({
            "fr_ids": [fr_id],
            "quality_targets": {"min_coverage": 80.0},
            "fr_module_traceability": trace,
        }),
        encoding="utf-8",
    )
    return root


def test_fr_module_paths_no_scope_when_undeclared(tmp_path: Path):
    """FR with no ``fr_module_traceability`` entry → ``NoScope``.

    Pre-Plan-F behaviour preserved: ``validate_fr_coverage_immediate``
    falls through to whole-project coverage in this case.
    """
    project = _write_project(
        tmp_path, sab_modules=[], manifest_modules=None, fr_id="FR-99",
    )
    scope = _fr_module_paths(project, "FR-99")
    assert isinstance(scope, ModuleScope)
    assert scope.is_no_scope
    assert not scope.is_phantom
    assert scope.has_paths is False
    assert scope.declared == ()


def test_fr_module_paths_phantom_when_declared_but_missing(tmp_path: Path):
    """FR declared but file missing on disk → ``is_phantom``.

    This is the silent-pass shape Plan F closes: previously
    ``_fr_module_paths`` returned ``None`` for both this case and the
    no-scope case, and ``validate_fr_coverage_immediate`` fell through
    to whole-project coverage — silently OKing a phantom deliverable.
    """
    project = _write_project(
        tmp_path,
        sab_modules=["taskq_api.repository.session"],
        manifest_modules="taskq_api.repository.session",
        fr_id="FR-06",
    )
    # NB: file is intentionally NOT created on disk. The src/ dir exists
    # but contains no .py file matching the declared module.
    scope = _fr_module_paths(project, "FR-06")
    assert isinstance(scope, ModuleScope)
    assert scope.is_phantom
    assert not scope.is_no_scope
    assert scope.has_paths is False
    assert scope.declared == ("taskq_api.repository.session",)


def test_fr_module_paths_concrete_when_declared_and_present(tmp_path: Path):
    """FR scope resolves to a real file on disk → ``has_paths``.

    The positive control that proves the new SSOT three-state contract
    still resolves the ordinary shape correctly.
    """
    src_dir = tmp_path / "03-development" / "src"
    src_dir.mkdir(parents=True)
    mod_dir = src_dir / "taskq_api" / "repository"
    mod_dir.mkdir(parents=True)
    (mod_dir / "session.py").write_text(
        "def get_session():\n    return None\n", encoding="utf-8",
    )
    project = _write_project(
        tmp_path,
        sab_modules=["taskq_api.repository.session"],
        manifest_modules="taskq_api.repository.session",
        fr_id="FR-06",
    )
    scope = _fr_module_paths(project, "FR-06")
    assert isinstance(scope, ModuleScope)
    assert scope.has_paths
    assert not scope.is_phantom
    assert not scope.is_no_scope
    assert any("session.py" in p for p in scope.paths)


def test_fr_coverage_record_returns_none_for_no_scope(tmp_path: Path):
    """FR with no declared scope: ``fr_coverage_record`` returns ``None``.

    Pre-Plan-F contract preserved — both no-scope and phantom return
    ``None``. The three-state distinction lives in ``ModuleScope`` itself;
    ``fr_coverage_record`` keeps the single "could not measure" contract.
    """
    project = _write_project(
        tmp_path, sab_modules=[], manifest_modules=None, fr_id="FR-99",
    )
    assert fr_coverage_record(project, "FR-99") is None


# ── the behaviour Plan F actually changed ────────────────────────────────────
#
# Round 78 站5. Plan F's only behaviour change is here: a phantom FR used to
# receive the whole-project coverage number and now receives None. It was
# pinned by `assert "_scope.is_phantom" in inspect.getsource(...)`, which
# survives any rewrite that keeps the spelling and any behaviour that loses
# the meaning.
#
# Its sibling `test_fr_coverage_record_returns_none_for_phantom` was deleted
# rather than kept: measured, pre-Plan-F `_fr_module_paths` returned
# `paths or None`, so a phantom gave None there too and
# `fr_coverage_record` returned None. That test passes identically on the
# code before the change it claimed to pin. `..._for_no_scope` above is kept
# because its docstring says exactly that — it asserts a preserved contract.


def _suite(**kwargs):
    """A SuiteResult with the fields `validate_fr_coverage_immediate` reads.

    `run_suite` is public, so patching it is not implementation-detail
    mocking (tests/test_patch_discipline.py) — and it is the seam
    tests/test_gate1_live_coverage.py already uses for this function.
    """
    from core.quality_gate.test_suite_run import SuiteResult

    base = dict(
        passed=True, coverage=62.5, test_target="03-development/tests",
        cov_target="03-development/src", returncode=0, output="", ran=True,
    )
    base.update(kwargs)
    return SuiteResult(**base)  # type: ignore[arg-type]


def test_a_phantom_fr_gets_no_coverage_number_at_all(tmp_path: Path):
    """The gate must not report a number for a deliverable that is not there.

    Pre-Plan-F this returned 62.5 — the whole project's figure, which has
    nothing to do with the module FR-06 declared and did not write.
    """
    from unittest import mock

    from core.quality_gate.gate1_evidence import validate_fr_coverage_immediate

    project = _write_project(
        tmp_path,
        sab_modules=["taskq_api.repository.session"],
        manifest_modules="taskq_api.repository.session",
        fr_id="FR-06",
    )
    with mock.patch("core.quality_gate.test_suite_run.run_suite",
                    return_value=_suite()):
        assert validate_fr_coverage_immediate(project, fr_id="FR-06") is None, (
            "a declared-but-missing module must yield 'could not measure', "
            "not the whole-project percentage")


def test_an_fr_that_declares_no_scope_still_gets_the_whole_project_number(tmp_path: Path):
    """The other side of the same branch, and the reason Plan F is narrow.

    An FR with no `fr_module_traceability` entry is not a phantom — it made
    no claim. Its pre-Plan-F fall-through is preserved deliberately, and
    without this test nothing distinguishes "Plan F scoped the block
    correctly" from "Plan F blocks everything that resolves to no paths".
    """
    from unittest import mock

    from core.quality_gate.gate1_evidence import validate_fr_coverage_immediate

    project = _write_project(
        tmp_path, sab_modules=[], manifest_modules=None, fr_id="FR-99",
    )
    with mock.patch("core.quality_gate.test_suite_run.run_suite",
                    return_value=_suite()):
        assert validate_fr_coverage_immediate(project, fr_id="FR-99") == 62.5


def _inlined_prechecks():
    """The pipeline as one function — Round 81 站6 moved these into helpers."""
    from tests.support.pipeline import inlined

    return inlined("cli/phase_cmds.py", "_advance_prechecks",
                   helper_prefix="_precheck_")


def test_advance_prechecks_runs_the_phantom_audit_before_the_slow_stages():
    """The wiring and its position, read off the AST rather than the text.

    What this replaces asserted `"phantom_modules" in inspect.getsource(...)`
    and `"Plan F (Round 50+): early phantom module check" in src` — a
    substring and a COMMENT. Round 78 站1 renamed nothing but the callee and
    the first went red while the behaviour improved; the second stayed green
    the whole time the check was blocking all nine corpus projects.

    An `ast.Call` to a name is a structural fact: a comment cannot satisfy it
    and a rename cannot smuggle past it. The DECISION itself is covered by
    behaviour in tests/test_phantom_audit_is_cwd_invariant.py; what is left
    for this test is that advance-phase invokes it, and invokes it before the
    stages it is meant to save (ruff / mypy / the suite all run through
    `subprocess.run`).
    """
    import ast

    fn = next(
        n for n in [_inlined_prechecks()]
        if isinstance(n, ast.FunctionDef) and n.name == "_advance_prechecks"
    )

    audit_lines = [
        n.lineno for n in ast.walk(fn)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        and n.func.id == "phantom_module_block"
    ]
    assert audit_lines, (
        "_advance_prechecks no longer calls phantom_module_block — a SAB "
        "declaring a module the tree does not contain would advance")

    # The claim is "before linting/typing/coverage", not "first thing in the
    # function" — `_advance_prechecks` shells out to git and gitleaks well
    # before this. Anchor on the ruff stage by its argv, which is the first
    # of the three.
    ruff_lines = [
        n.lineno for n in ast.walk(fn)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        and n.func.attr == "run" and n.args
        and isinstance(n.args[0], ast.List) and n.args[0].elts
        and isinstance(n.args[0].elts[0], ast.Constant)
        and n.args[0].elts[0].value == "ruff"
    ]
    assert ruff_lines, (
        "expected the ruff stage inside _advance_prechecks — this test "
        "anchors the audit's position on it")
    assert min(audit_lines) < min(ruff_lines), (
        f"the phantom audit runs at line {min(audit_lines)}, after the ruff "
        f"stage at {min(ruff_lines)} — Plan F's whole point is failing "
        f"before lint/type/coverage, not after them")
