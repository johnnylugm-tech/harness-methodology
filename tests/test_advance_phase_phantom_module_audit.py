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

import inspect
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
        existing = next((l for l in layers if l["name"] == group), None)
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


def test_fr_coverage_record_returns_none_for_phantom(tmp_path: Path):
    """Phantom FR scope reaches ``fr_coverage_record -> None``.

    The gate uses ``fr_coverage_record`` (or its predecessor); ``None``
    propagates to ``_check_gate1_live_coverage`` as a BLOCK signal.
    """
    project = _write_project(
        tmp_path,
        sab_modules=["taskq_api.repository.session"],
        manifest_modules="taskq_api.repository.session",
        fr_id="FR-06",
    )
    assert fr_coverage_record(project, "FR-06") is None


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


def test_validate_fr_coverage_immediate_distinguishes_phantom():
    """Static delegation check: ``validate_fr_coverage_immediate`` must
    inspect ``_scope.is_phantom`` and refuse to return a coverage number
    for a phantom FR. Same style as ``test_advance_phase_pragma_guidance.py``.
    """
    from core.quality_gate import gate1_evidence as _ev
    src = inspect.getsource(_ev.validate_fr_coverage_immediate)
    assert "_scope.is_phantom" in src, (
        "validate_fr_coverage_immediate must distinguish phantom FR "
        "scope and refuse to return a coverage number. Without this "
        "guard the gate silently OKs a phantom deliverable (pre-Plan-F "
        "shape)."
    )
    assert "_coverage_for_paths" in src


def test_advance_prechecks_invoke_phantom_modules_for_early_block():
    """Plan F (Round 50+): ``_advance_prechecks`` must invoke
    ``phantom_modules`` BEFORE linting/typing/coverage — same early-fail
    pattern as Plan E for pragma.
    """
    from cli.phase_cmds import _advance_prechecks as _apc
    src = inspect.getsource(_apc)
    assert "phantom_modules" in src, (
        "Plan F: _advance_prechecks must call phantom_modules to BLOCK "
        "before linting/typing/coverage run — same early-fail pattern "
        "as Plan E for pragma."
    )
    # Marker comment anchors the audit's intent in source.
    assert "Plan F (Round 50+): early phantom module check" in src
