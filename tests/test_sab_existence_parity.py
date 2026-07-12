"""SAB existence-decision parity: Gate 1 vs P4 preflight must agree.

Round 6 station 3 (SAB mechanism audit). Two independent, genuinely
different algorithms answer the same question — "does this SAB `modules`
entry exist on disk?":

  Algorithm A (Gate 1, per-FR, cheap/early):
    core.quality_gate.sab_amender.discover_modules_at() + phantom_modules()
    — dotted-name SET-DIFF: enumerate every .py file under src/, normalise
    both sides to dotted names, compare sets.

  Algorithm B (P4 preflight, project-wide, full information):
    core.phase_hooks.PhaseHooks.preflight_sab_check(), via
    detection.drift_detector.sab_module_to_path_variants() — PATH-VARIANT
    PROBING: expand the entry into candidate filesystem paths and check
    `.exists()` directly.

History shows these two have only ever been reconciled piecemeal (Bug
#30/#31/#119, and Round 6 station 1's blank-``implemented_in`` fix) —
never swept as a matrix. This suite invokes the REAL production functions
for a shared fixture-scenario matrix, not a reimplementation of their
logic (see c38a9fe's lesson, quoted in this round's plan: "tests passed
because they replicated the path calculation rather than invoking the
real inlined helper").

Deliberately excluded from this parity requirement: the "unregistered"
direction (.py files on disk not declared in SAB) and drift_detector's
whole-repo-hygiene "Check 2"/"Check 3" — those answer scoped-differently
questions (see Round 6 plan's "risks and limits" section) and are not
claimed to agree.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.phase_hooks import PhaseHooks
from core.quality_gate.sab_amender import discover_modules_at, phantom_modules
from core.utils.project_layout import ProjectLayout


pytestmark = [pytest.mark.core]


def _algo_a_says_missing(tmp_path: Path, sab: dict) -> bool:
    """Gate 1's answer: dotted-name set-diff (sab_amender)."""
    src_dir = ProjectLayout(str(tmp_path)).active_src_dir
    discovered = discover_modules_at(src_dir)
    return bool(phantom_modules(sab, discovered))


def _algo_b_says_missing(tmp_path: Path, sab: dict) -> bool:
    """P4 preflight's answer: path-variant probing (phase_hooks/drift_detector).

    Writes SAB.json and invokes the real PhaseHooks.preflight_sab_check —
    not a reimplementation. Each scenario declares exactly one module so a
    single passed/failed result unambiguously answers "is it missing".
    """
    method_dir = tmp_path / ".methodology"
    method_dir.mkdir(exist_ok=True)
    (method_dir / "SAB.json").write_text(json.dumps(sab), encoding="utf-8")
    hooks = PhaseHooks(str(tmp_path), phase=4)
    result = hooks.preflight_sab_check()
    return not result["passed"]


def _one_module_sab(name: str, implemented_in: str | None = None) -> dict:
    mod: dict | str
    if implemented_in is not None:
        mod = {"name": name, "implemented_in": implemented_in}
    else:
        mod = name
    return {
        "layers": [{"name": "L1", "modules": [mod], "allowed_dependencies": []}],
        "dependencies": {},
    }


class TestSabExistenceParity:
    def test_both_agree_present_dotted_string(self, tmp_path):
        src = tmp_path / "03-development" / "src" / "app"
        src.mkdir(parents=True)
        (src / "core.py").write_text("x = 1")
        sab = _one_module_sab("app.core")
        assert _algo_a_says_missing(tmp_path, sab) is False
        assert _algo_b_says_missing(tmp_path, sab) is False

    def test_both_agree_missing_dotted_string(self, tmp_path):
        (tmp_path / "03-development" / "src").mkdir(parents=True)
        sab = _one_module_sab("app.nonexistent")
        assert _algo_a_says_missing(tmp_path, sab) is True
        assert _algo_b_says_missing(tmp_path, sab) is True

    def test_both_agree_present_path_form_string(self, tmp_path):
        src = tmp_path / "03-development" / "src" / "app"
        src.mkdir(parents=True)
        (src / "core.py").write_text("x = 1")
        sab = _one_module_sab("03-development/src/app/core.py")
        assert _algo_a_says_missing(tmp_path, sab) is False
        assert _algo_b_says_missing(tmp_path, sab) is False

    def test_both_agree_present_dict_shaped_entry(self, tmp_path):
        src = tmp_path / "03-development" / "src" / "app" / "interface"
        src.mkdir(parents=True)
        (src / "cli.py").write_text("x = 1")
        sab = _one_module_sab("app.cli", implemented_in="app.interface.cli")
        assert _algo_a_says_missing(tmp_path, sab) is False
        assert _algo_b_says_missing(tmp_path, sab) is False

    def test_both_agree_present_blank_implemented_in_falls_back_to_name(
        self, tmp_path
    ):
        """Round 6 station 1 regression, re-confirmed from the parity angle:
        implemented_in: "" (present but blank) must fall back to `name`."""
        src = tmp_path / "03-development" / "src" / "app"
        src.mkdir(parents=True)
        (src / "core.py").write_text("x = 1")
        sab = _one_module_sab("app.core", implemented_in="")
        assert _algo_a_says_missing(tmp_path, sab) is False
        assert _algo_b_says_missing(tmp_path, sab) is False

    def test_both_agree_package_style_module(self, tmp_path):
        """CONFIRMED live bug (Round 6 station 3): a dotted SAB entry naming
        a PACKAGE (taskq/cli/__init__.py exists, no taskq/cli.py leaf file)
        was found by Algorithm B (drift_detector.sab_module_to_path_variants
        explicitly expands an `__init__.py` candidate) but NOT by Algorithm A
        (sab_amender.discover_modules_at excluded __init__.py entirely,
        treating it as "package marker, not a SAB module" with no
        compensating package-level entry) — Gate 1 would false-positive
        BLOCK a legitimate package registration that P4 preflight correctly
        allows. Empirically reproduced against the real functions before
        this test was written; fixed by making discover_modules_at also
        register the package's own dotted name when it contains __init__.py.
        """
        src = tmp_path / "03-development" / "src" / "taskq" / "cli"
        src.mkdir(parents=True)
        (src / "__init__.py").write_text("# package\n")
        (tmp_path / "03-development" / "src" / "taskq" / "__init__.py").write_text(
            "# package\n"
        )
        sab = _one_module_sab("taskq.cli")
        assert _algo_a_says_missing(tmp_path, sab) is False, (
            "Gate 1 must not flag a legitimate package-style SAB module as phantom"
        )
        assert _algo_b_says_missing(tmp_path, sab) is False

    def test_both_agree_package_style_module_genuinely_missing(self, tmp_path):
        """Negative companion to the package-style fix above: a dotted entry
        naming a package that truly doesn't exist must still be flagged by
        both algorithms — the fix must be additive, not silently permissive."""
        (tmp_path / "03-development" / "src").mkdir(parents=True)
        sab = _one_module_sab("taskq.nonexistent_pkg")
        assert _algo_a_says_missing(tmp_path, sab) is True
        assert _algo_b_says_missing(tmp_path, sab) is True
