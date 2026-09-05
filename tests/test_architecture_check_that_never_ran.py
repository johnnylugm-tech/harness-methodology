"""The framework's only import-based architecture check, silent on 12/12 projects.

Round 98.

`detect_sab_drift`'s Check 3 compares every delivered module's real imports
against the SAB's `dependencies` matrix. It has two independent breaks, and
either alone is enough to make it report nothing.

**Break 1 — the resolver cannot name the source layer.**
`_resolve_import_layer` returns None when a path matches more than one layer,
which is right, and the caller then does `continue`, which turns "could not
resolve" into "no violation". Every project in the corpus declares its bare
top-level package as a module of some layer, and that entry matches EVERY
module in the project under rule 3 (`normalized.startswith(mod + ".")`), so
every module is ambiguous:

    taskq_api.api.metrics   -> None   matched=['api', 'independence']
    taskq_api.service.auth  -> None   matched=['independence', 'service']

Measured over the twelve corpus projects with a SAB: 62%-91% of delivered
source modules resolve to None. taskq-wow: 21 of its 23 application modules.

The bare entry is the framework's own. `sab_amender.discover_modules_at`
registers a package under its own dotted name (Round 6 station 3, to stop
phantom-module false positives), so `src/taskq_api/__init__.py` emits
`"taskq_api"` — one entry the framework writes switches off the framework's
own architecture check.

The fix is not to drop the ambiguity guard. It is to rank by specificity
first, and only call a tie ambiguous: exact match, then the LONGEST ancestor,
then a unique descendant. A layer registered as a whole top-level package
(`{"Bridge": {"harness"}}`, which
`test_drift_detector.py::test_resolve_import_layer_directory` pins) still
resolves through the ancestor tier, and a genuine tie still abstains.

**Break 2 (station 2 here) — the abstention leaves no reading.**
`score = 1 - drifted / checked` and a skipped module enters neither term, so
a project whose layering half never ran reads 100.0%. After the resolver fix
the delivered-source abstention count is 0 on all twelve projects; this is
the tripwire that makes the next occurrence visible instead of silent
(Round 32/35: could-not-measure is not zero; Round 46: an absent witness is
not a passing one).
"""

from __future__ import annotations

import json

import pytest

from detection.drift_detector import DriftDetector

pytestmark = [pytest.mark.core]


# ── station 1: the resolver ──────────────────────────────────────────────────

def _sab_shaped_like_the_corpus() -> dict:
    """Every corpus project's shape: layered modules plus the bare root package
    the framework's own `discover_modules_at` registers."""
    return {
        "layers": [
            {"name": "api", "modules": ["pkg.api", "pkg.api.metrics"]},
            {"name": "repository", "modules": ["pkg.repository",
                                               "pkg.repository.results"]},
            {"name": "independence", "modules": ["pkg", "pkg.config"]},
        ],
        "dependencies": {"api": ["independence"],
                         "repository": ["independence"],
                         "independence": []},
    }


def _layer_map(sab: dict) -> dict:
    return {lay["name"]: set(lay["modules"]) for lay in sab["layers"]}


def test_a_module_is_named_by_its_own_layer_not_by_the_root_package(tmp_path):
    """`pkg.api.metrics` is declared in `api`. The bare `pkg` entry in
    `independence` must not make that ambiguous."""
    d = DriftDetector(str(tmp_path))
    lm = _layer_map(_sab_shaped_like_the_corpus())
    assert d._resolve_import_layer("pkg.api.metrics", lm) == "api"
    assert d._resolve_import_layer("pkg.repository.results", lm) == "repository"


def test_the_nearest_declared_ancestor_wins(tmp_path):
    """`pkg.api.__init__` is not declared; `pkg.api` (api) and `pkg`
    (independence) both are. The nearer one is the answer."""
    d = DriftDetector(str(tmp_path))
    lm = _layer_map(_sab_shaped_like_the_corpus())
    assert d._resolve_import_layer("pkg.api.__init__", lm) == "api"


def test_a_layer_registered_as_a_whole_top_level_package_still_resolves(tmp_path):
    """Regression guard. `{"Bridge": {"harness"}}` is a legitimate declaration
    — the whole package IS the layer — and is pinned by
    test_drift_detector.py::test_resolve_import_layer_directory. A rule that
    simply refused single-segment entries would redden it, which is how the
    first draft of this station was caught."""
    d = DriftDetector(str(tmp_path))
    assert d._resolve_import_layer(
        "harness.git_strategy", {"Bridge": {"harness"}}) == "Bridge"


def test_a_genuine_tie_still_abstains(tmp_path):
    """Counter-control: the ambiguity guard is narrowed, not removed. Two
    layers declaring the same ancestor is not resolvable from the path."""
    d = DriftDetector(str(tmp_path))
    assert d._resolve_import_layer("p.q.r", {"A": {"p.q"}, "B": {"p.q"}}) is None


def test_a_bare_package_import_shared_by_two_layers_still_abstains(tmp_path):
    """`import pkg` names no layer when two layers hold its submodules."""
    d = DriftDetector(str(tmp_path))
    assert d._resolve_import_layer(
        "pkg", {"a": {"pkg.one"}, "b": {"pkg.two"}}) is None


# ── station 1, end to end: the check reports the violation ───────────────────

def _project_shaped_like_the_corpus(tmp_path) -> None:
    (tmp_path / ".methodology").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".methodology" / "SAB.json").write_text(
        json.dumps(_sab_shaped_like_the_corpus()), encoding="utf-8")
    src = tmp_path / "03-development" / "src" / "pkg"
    (src / "api").mkdir(parents=True, exist_ok=True)
    (src / "repository").mkdir(parents=True, exist_ok=True)
    (src / "__init__.py").write_text("", encoding="utf-8")
    (src / "config.py").write_text("X = 1\n", encoding="utf-8")
    (src / "api" / "__init__.py").write_text("", encoding="utf-8")
    (src / "api" / "metrics.py").write_text(
        "from pkg.repository import results\n", encoding="utf-8")
    (src / "repository" / "__init__.py").write_text("", encoding="utf-8")
    (src / "repository" / "results.py").write_text("Y = 1\n", encoding="utf-8")


def test_the_violation_the_corpus_hid_is_reported(tmp_path):
    """api -> repository, with `api: [independence]` declared. Today the bare
    `pkg` entry makes every module ambiguous and Check 3 returns nothing."""
    _project_shaped_like_the_corpus(tmp_path)
    result = DriftDetector(str(tmp_path)).detect_sab_drift()
    arch = [i for i in result.drift_items
            if "Architecture violation" in (i.description or "")]
    assert arch, (
        "api imports repository while `api: [independence]` is declared, and "
        f"the check reported no architecture violation: {result.drift_items}")
    assert any("pkg.repository" in i.description for i in arch), arch


# ── station 2: the abstention leaves a reading ───────────────────────────────

def _project_with_an_unresolvable_delivered_module(tmp_path, *,
                                                   deliver: bool) -> None:
    """Both layers declare `thing.deep.mod`, so it is a genuine tie — the one
    case the resolver must still abstain on. `deliver` decides whether the
    file itself exists, which is the only difference between the two trees the
    counter-control below compares."""
    (tmp_path / ".methodology").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".methodology" / "SAB.json").write_text(json.dumps({
        "layers": [
            {"name": "a", "modules": ["thing.deep", "thing.deep.mod"]},
            {"name": "b", "modules": ["thing.deep", "thing.deep.mod"]},
        ],
        "dependencies": {"a": [], "b": []},
    }), encoding="utf-8")
    src = tmp_path / "03-development" / "src" / "thing" / "deep"
    src.mkdir(parents=True, exist_ok=True)
    (src / "__init__.py").write_text("", encoding="utf-8")
    if deliver:
        (src / "mod.py").write_text("X = 1\n", encoding="utf-8")


def test_a_delivered_module_the_check_could_not_judge_is_reported(tmp_path):
    """The whole defect this round found was invisible because a skipped
    module produced no output at all."""
    _project_with_an_unresolvable_delivered_module(tmp_path, deliver=True)
    result = DriftDetector(str(tmp_path)).detect_sab_drift()
    assert any("architecture check" in (i.description or "").lower()
               and "abstain" in (i.description or "").lower()
               for i in result.drift_items), (
        "a delivered source module whose layer could not be resolved left no "
        f"record: {result.drift_items}")


def test_an_abstention_does_not_improve_the_score(tmp_path):
    """Counter-control, and the reason the record is an item rather than a
    `checked` increment: Round 32/35 is that a measurement which could not be
    taken must not be counted as one that passed.

    The two trees declare the same modules and differ only in whether the
    unjudgeable file is on disk, so any change in `checked` is the abstention
    entering the denominator.
    """
    without = tmp_path / "without"
    with_ = tmp_path / "with"
    _project_with_an_unresolvable_delivered_module(without, deliver=False)
    _project_with_an_unresolvable_delivered_module(with_, deliver=True)

    base = DriftDetector(str(without)).detect_sab_drift()
    result = DriftDetector(str(with_)).detect_sab_drift()

    assert [i for i in result.drift_items
            if "abstain" in (i.description or "").lower()], (
        "fixture produced no abstention to control against")
    assert result.checked == base.checked, (
        "the abstained module entered the score's denominator — "
        f"checked {base.checked} -> {result.checked}")


def test_a_project_the_check_fully_judged_reports_no_abstention(tmp_path):
    """Negative control: the tripwire must be silent when nothing was skipped.
    Measured after station 1, this is all twelve corpus projects."""
    _project_shaped_like_the_corpus(tmp_path)
    result = DriftDetector(str(tmp_path)).detect_sab_drift()
    assert not [i for i in result.drift_items
                if "abstain" in (i.description or "").lower()], result.drift_items
