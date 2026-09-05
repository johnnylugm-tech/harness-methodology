"""The one consumer of the architecture check discarded every architecture finding.

Round 98 station 3.

`cli/advance_prechecks.py` is the only production caller of
`detect_sab_drift`. Its filter read:

    if _item.severity.value in ("MEDIUM", "HIGH", "CRITICAL")
    and _item.actual == "not found"

Check 3 sets `actual = f"imports {imported} (layer {target_layer})"` and
Check 2 sets `"unregistered"`; only Check 1's missing-file item is
`"not found"`. So the block could only ever report missing files, under a
headline that says `[BLOCKED] SAB architecture violations`. Measured on
taskq-wow: a tree synthesised to produce 15 CRITICAL architecture violations
put 0 of them through that filter.

Two things follow from letting the other two kinds through.

**Provenance.** 57 of the 147 violations the corpus now produces have a source
layer `sab_amender._heuristic_layer_choice` invented — rule 3, "the module's
path names no declared layer, so put it in the last one". taskq-redo's
`taskq_api.app` is in `config`, taskq-cc-new's is in `models`. Round 26
decided to print that guess; it never reached a verdict. 老闆 adjudicated that
these are charged like any other, so the message says which they are rather
than discounting them — a project told `config -> api is not allowed` cannot
otherwise tell whether to fix the import or SAD.md §2.

**`conftest.py`.** Check 2's `unregistered` items include
`03-development/conftest.py` on two corpus projects. `discover_modules` only
ever scans `src_dir`, so a conftest can never legitimately be registered in a
layer — reporting it is a block with no way to clear it, which is exactly what
the `scripts/` exclusion beside it was added to prevent.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

pytestmark = [pytest.mark.core]


def _project(tmp_path, *, deliver_conftest: bool = False) -> Path:
    """api imports repository while `api: [independence]` is declared, plus a
    module whose path names no declared layer (the fallback-placed shape)."""
    (tmp_path / ".methodology").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".methodology" / "SAB.json").write_text(json.dumps({
        "layers": [
            {"name": "api", "modules": ["pkg.api", "pkg.api.metrics"]},
            {"name": "repository", "modules": ["pkg.repository",
                                               "pkg.repository.results"]},
            {"name": "independence", "modules": ["pkg", "pkg.app"]},
        ],
        "dependencies": {"api": ["independence"], "repository": [],
                         "independence": []},
    }), encoding="utf-8")
    src = tmp_path / "03-development" / "src" / "pkg"
    (src / "api").mkdir(parents=True, exist_ok=True)
    (src / "repository").mkdir(parents=True, exist_ok=True)
    (src / "__init__.py").write_text("", encoding="utf-8")
    (src / "api" / "__init__.py").write_text("", encoding="utf-8")
    (src / "api" / "metrics.py").write_text(
        "from pkg.repository import results\n", encoding="utf-8")
    (src / "repository" / "__init__.py").write_text("", encoding="utf-8")
    (src / "repository" / "results.py").write_text("Y = 1\n", encoding="utf-8")
    # `pkg.app` names no declared layer, so amend-sab's fallback filed it in
    # the last layer — the exact shape taskq-redo/cc/cc-new carry.
    (src / "app.py").write_text("from pkg.api import metrics\n", encoding="utf-8")
    if deliver_conftest:
        (tmp_path / "03-development" / "conftest.py").write_text(
            "import pytest\n", encoding="utf-8")
    return tmp_path


# ── the filter ───────────────────────────────────────────────────────────────

def test_an_architecture_violation_reaches_the_block(tmp_path, capsys):
    from cli.advance_prechecks import _precheck_sab_consistency

    rc = _precheck_sab_consistency(3, _project(tmp_path))
    out = capsys.readouterr().out
    assert rc == 12, f"architecture violations did not stop the advance: {out}"
    assert "not an allowed dependency" in out, out


def test_the_block_names_a_framework_placed_source_layer(tmp_path, capsys):
    """`pkg.app`'s layer was chosen by the fallback heuristic, not declared.
    The remedy differs, so the message has to say so."""
    from cli.advance_prechecks import _precheck_sab_consistency

    _precheck_sab_consistency(3, _project(tmp_path))
    out = capsys.readouterr().out
    assert "amend-sab" in out or "fallback" in out, (
        "a violation whose source layer the framework guessed reads the same "
        f"as one the project declared:\n{out}")


def test_a_declared_source_layer_is_not_labelled_a_guess(tmp_path, capsys):
    """Counter-control: `pkg.api.metrics` states its own layer, so its
    violation must NOT carry the provenance note."""
    from cli.advance_prechecks import _precheck_sab_consistency

    _precheck_sab_consistency(3, _project(tmp_path))
    lines = [ln for ln in capsys.readouterr().out.splitlines()
             if "api/metrics.py" in ln]
    assert lines, "metrics.py violation missing from the report"
    assert not any("fallback" in ln or "amend-sab" in ln for ln in lines), lines


def test_the_provenance_verdict_has_one_definition(tmp_path, capsys, monkeypatch):
    """The judgement 'this placement was a guess' must be read from
    sab_amender, not restated at the block. Round 17/33 — this round is about
    one contract with two statements; writing a second one here would be it.

    Driven by replacing sab_amender's verdict and watching the message follow.
    The first draft of this test asserted `"is_fallback_placement" in src`, and
    the counter-proof that rewrote the call site into a local copy of the same
    expression left it GREEN — the import line still carried the name. A guard
    satisfied by an import is a guard measuring an import.
    """
    from core.quality_gate import sab_amender
    from cli.advance_prechecks import _precheck_sab_consistency

    _precheck_sab_consistency(3, _project(tmp_path))
    assert [ln for ln in capsys.readouterr().out.splitlines()
            if "fallback" in ln], "fixture produced no provenance line to silence"

    monkeypatch.setattr(sab_amender, "is_fallback_placement",
                        lambda sab, module: False)
    _precheck_sab_consistency(3, _project(tmp_path))
    silenced = [ln for ln in capsys.readouterr().out.splitlines()
                if "fallback" in ln]
    assert not silenced, (
        "sab_amender's verdict was overridden to 'nothing was a guess' and the "
        f"block still said one was — it is deciding provenance itself:\n{silenced}")

    amender = (REPO / "core" / "quality_gate" / "sab_amender.py").read_text(
        encoding="utf-8")
    assert amender.count("def is_fallback_placement") == 1
    tree = ast.parse(amender)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "amend_sab")
    assert "is_fallback_placement" in (ast.get_source_segment(amender, fn) or ""), (
        "amend_sab still carries its own inline copy of the same judgement")


# ── conftest.py must not become an unclearable block ─────────────────────────

def test_a_conftest_is_not_an_unregistered_module(tmp_path):
    """`discover_modules` only scans src_dir, so no conftest can ever be
    registered in a layer. Same category as the `scripts/` exclusion."""
    from detection.drift_detector import DriftDetector

    result = DriftDetector(str(_project(tmp_path, deliver_conftest=True))
                           ).detect_sab_drift()
    assert not [i for i in result.drift_items
                if "conftest.py" in (i.description or "")], (
        "conftest.py reported as unregistered — a finding with no way to clear "
        f"it: {[i.description for i in result.drift_items]}")


def test_a_real_unregistered_module_is_still_reported(tmp_path):
    """Counter-control for the exclusion above."""
    from detection.drift_detector import DriftDetector

    project = _project(tmp_path)
    (project / "03-development" / "src" / "pkg" / "stray.py").write_text(
        "Z = 1\n", encoding="utf-8")
    result = DriftDetector(str(project)).detect_sab_drift()
    assert [i for i in result.drift_items
            if "stray.py" in (i.description or "")], result.drift_items


# ── the blocked-message contract lost its population ─────────────────────────

def test_the_blocked_contract_covers_where_the_messages_live():
    """Rounds 80/82 moved advance-phase's checks out of cli/phase_cmds.py into
    four files. `_TARGET_FILES` has not changed since Round 13, so 35 of the
    43 agent-facing [BLOCKED] messages left the contract's population while it
    stayed green — the same shape as this round's main finding, one layer up.
    """
    from tests.test_blocked_message_contract import _TARGET_FILES

    for moved in ("cli/advance_prechecks.py", "cli/advance_checks.py",
                  "cli/advance_steps.py", "cli/advance_commit.py"):
        assert moved in _TARGET_FILES, (
            f"{moved} holds agent-facing [BLOCKED] messages and is outside the "
            "contract")
