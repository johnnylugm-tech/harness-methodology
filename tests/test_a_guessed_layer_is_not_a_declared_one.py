"""Round 101 — the framework filled in an answer it did not have, then judged it.

`03-development/src/taskq_api/errors.py` was delivered and SAD.md §5's SAB
block placed it in no layer. Gate 1's `_check_sab_module_alignment` noticed and
called `amend_sab`, whose `layer_for_module` found no layer name in the
module's path and returned `layers[-1]["name"]` — `models`, because that is the
layer taskq-done happens to declare last. The same run filed the bare root
package `taskq_api` there too. Both went into `.methodology/SAB.json`, which
`scripts/generate_sab.py` renders from SAD.md and which SAD.md does not agree
with, and then `drift_detector`'s Check 3 charged import violations against
them. Measured on a copy of that tree:

    as amend-sab left it                  CRITICAL=11  unregistered=4
    drop only taskq_api.errors            CRITICAL=11  unregistered=5
    drop only taskq_api (root container)  CRITICAL=11  unregistered=4
    drop both                             CRITICAL= 4  unregistered=5

Seven of eleven, and only when both go — because `_resolve_import_layer`'s
ancestor tier resolves every module with no nearer claim through the root
package's entry. Round 98 introduced that tier to fix a real defect (62%–91% of
delivered modules were being skipped as ambiguous) and, in doing so, turned the
root-package guess into a working default.

Corpus scale, measured with the framework's own functions over the seventeen
projects:

    placements in SAB.json and not in SAD.md §5      117   (15 projects)
    of those, not derivable from the layer names      42   (14 are the root package)
    `layer_for_module` rule 1 / rule 2 / rule 3    394 / 0 / 84
    `unregistered` findings / at MEDIUM or above      21 / 0

The last line is its own defect: Round 98 gave the `unregistered` finding a
reporting branch in `_precheck_sab_consistency` and a remedy, above a filter
that keeps only MEDIUM and higher, while the only place that emits it emits
LOW. The branch had never run, and the remedy it carried — "re-run amend-sab" —
cannot work either: `amend_sab` reads SAB.json and never SAD.md.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.quality_gate.sab_amender import (
    UNPLACEABLE_REMEDY,
    amend_sab,
    container_packages,
    layer_for_module,
    undeclared_layer_placements,
    undeclared_placements_blocking_reason,
    unplaceable_modules,
)

pytestmark = [pytest.mark.core]

_LAYERS = [
    {"name": "api", "modules": ["pkg.api.routes"], "allowed_dependencies": ["core"]},
    {"name": "core", "modules": ["pkg.core.rules"], "allowed_dependencies": []},
]


def _sad(layers) -> str:
    body = "\n".join(
        f"    - name: {L['name']}\n      modules:\n"
        + "".join(f'        - "{m}"\n' for m in L.get("modules", []))
        + f"      allowed_dependencies: {json.dumps(L.get('allowed_dependencies', []))}"
        for L in layers
    )
    return (
        "# SAD\n\n## 5. SAB Block\n\n<!-- SAB:START -->\n```yaml\nsab:\n"
        '  version: "1.0"\n  phase: 2\n  project: "pkg"\n  layers:\n'
        f"{body}\n```\n<!-- SAB:END -->\n"
    )


def _project(tmp_path: Path, *, sad_layers=None, json_layers=None) -> Path:
    project = tmp_path / "proj"
    (project / ".methodology").mkdir(parents=True)
    arch = project / "02-architecture"
    arch.mkdir(parents=True)
    (arch / "SAD.md").write_text(
        _sad(sad_layers if sad_layers is not None else _LAYERS), encoding="utf-8")
    (project / ".methodology" / "SAB.json").write_text(
        json.dumps({"version": "1.0",
                    "layers": json_layers if json_layers is not None else _LAYERS}),
        encoding="utf-8")
    return project


# ── 站1: a layer nobody stated is not a layer ────────────────────────────────

def test_a_path_that_names_no_layer_answers_none() -> None:
    sab = {"layers": [{"name": "api"}, {"name": "core"}]}
    assert layer_for_module(sab, "pkg/util/misc.py") is None


def test_a_path_that_names_a_layer_still_answers_it() -> None:
    """Reverse control — the rule must not read as 'never place anything'."""
    sab = {"layers": [{"name": "api"}, {"name": "core"}]}
    assert layer_for_module(sab, "pkg/core/rules.py") == "core"


def test_unplaceable_modules_is_the_part_of_missing_nobody_can_answer() -> None:
    sab = {"layers": [{"name": "api", "modules": []}, {"name": "core", "modules": []}]}
    discovered = ["pkg.api.routes", "pkg.util.misc"]
    assert unplaceable_modules(sab, discovered) == ["pkg.util.misc"]


def test_amend_does_not_write_a_module_it_cannot_place(tmp_path, capsys) -> None:
    project = _project(tmp_path, json_layers=[
        {"name": "api", "modules": []}, {"name": "core", "modules": []}])
    src = project / "03-development" / "src" / "pkg"
    (src / "util").mkdir(parents=True)
    (src / "__init__.py").write_text("", encoding="utf-8")
    (src / "util" / "misc.py").write_text("", encoding="utf-8")

    added = amend_sab(project)
    assert added == []
    written = json.loads(
        (project / ".methodology" / "SAB.json").read_text(encoding="utf-8"))
    assert all(not layer["modules"] for layer in written["layers"]), written
    out = capsys.readouterr().out
    assert "pkg.util.misc" in out and "SAD.md" in out, out


def test_amend_reads_that_answer_rather_than_recomputing_it(
        tmp_path, monkeypatch) -> None:
    """Counter-proof CP-2, written after it found this missing.

    The tests above pin what `layer_for_module` answers. They say nothing
    about whether `amend_sab` reads the answer: a second implementation
    inside it, faithful to rule 1 and never calling the function, passed all
    88 tests in this file and `test_sab_amender.py`. That is the shape Round
    97 CP-5b, Round 98 CP-11 and Round 99 CP-13b all caught — a guard that
    checks the SSOT exists rather than that it is the thing being used — and
    this is its fourth appearance.

    Replacing the single definition must change what the writer does. The
    seam is public for the reason Round 99 made `contract_decides` public:
    `tests/test_patch_discipline.py` is right that the answer to "I need to
    replace this to test it" is a public seam, not a patched private name.

    Patched and called through the SAME module object rather than through
    this file's top-level import: `tests/cli/test_fr_cmds_cli.py` used to
    evict `core.quality_gate.sab_amender` from `sys.modules`, after which the
    two were different objects and this test passed alone and failed in the
    full suite. That eviction is fixed, and calling through `sa` means this
    guard does not depend on the fix holding.
    """
    import core.quality_gate.sab_amender as sa

    project = _project(tmp_path, json_layers=[
        {"name": "api", "modules": []}, {"name": "core", "modules": []}])
    src = project / "03-development" / "src" / "pkg"
    (src / "core").mkdir(parents=True)
    (src / "__init__.py").write_text("", encoding="utf-8")
    (src / "core" / "rules.py").write_text("", encoding="utf-8")
    assert sa.amend_sab(project) == ["pkg.core.rules"], "fixture drifted"

    project = _project(tmp_path / "second", json_layers=[
        {"name": "api", "modules": []}, {"name": "core", "modules": []}])
    src = project / "03-development" / "src" / "pkg"
    (src / "core").mkdir(parents=True)
    (src / "__init__.py").write_text("", encoding="utf-8")
    (src / "core" / "rules.py").write_text("", encoding="utf-8")
    monkeypatch.setattr(sa, "layer_for_module", lambda *a, **k: None)
    assert sa.amend_sab(project) == [], (
        "amend_sab did not follow `layer_for_module` — it is deciding for "
        "itself, and the two answers can now drift")


def test_the_placement_check_reads_that_answer_too(tmp_path, monkeypatch) -> None:
    """The other consumer, held to the same rule. `undeclared_layer_placements`
    calls it to decide whether a placement was transcribed or invented; if it
    re-derived that locally, station 3 and station 1 could disagree about the
    same module."""
    import core.quality_gate.sab_amender as sa

    project = _project(tmp_path, json_layers=[
        {"name": "api", "modules": ["pkg.api.routes", "pkg.api.deps"]},
        {"name": "core", "modules": ["pkg.core.rules"]},
    ])
    assert sa.undeclared_layer_placements(project) == [], "fixture drifted"

    monkeypatch.setattr(sa, "layer_for_module", lambda *a, **k: None)
    assert sa.undeclared_layer_placements(project) == [
        {"module": "pkg.api.deps", "layer": "api"}], (
        "undeclared_layer_placements did not follow `layer_for_module` — the "
        "writer and the check can now disagree about the same placement")


# ── 站1b: the root package is a container, not a member ──────────────────────

def test_the_root_package_is_a_container() -> None:
    discovered = ["pkg", "pkg.api.routes", "pkg.core.rules"]
    assert container_packages(discovered) == {"pkg"}


def test_a_flat_leaf_with_no_children_is_still_a_member() -> None:
    """Reverse control. taskq-super ships `sitecustomize` and taskq-wow
    `_p2_preflight_config_keys` — single-segment names with no `x.*` sibling,
    which are modules and must still be asked about."""
    discovered = ["sitecustomize", "pkg", "pkg.api.routes"]
    assert container_packages(discovered) == {"pkg"}


def test_the_container_is_exempt_from_being_demanded() -> None:
    from core.quality_gate.sab_amender import missing_modules

    sab = {"layers": [{"name": "api", "modules": ["pkg.api.routes"]}]}
    assert missing_modules(sab, ["pkg", "pkg.api.routes"]) == []


def test_the_container_is_not_removed_from_what_the_scan_reports(tmp_path) -> None:
    """`phantom_modules` is `registered - discovered`. If the exemption were
    applied to the scan instead of to the demand, every root package already
    sitting in a SAB layer would become a phantom — fourteen of seventeen.

    Driven through the real scan, not a hand-written `discovered` list: the
    first version of this test supplied the list itself and so could not have
    noticed the exemption moving into `discover_modules_at`.
    """
    from core.quality_gate.sab_amender import discover_modules_at, phantom_modules

    src = tmp_path / "src"
    (src / "pkg" / "api").mkdir(parents=True)
    for rel in ("pkg/__init__.py", "pkg/api/__init__.py", "pkg/api/routes.py"):
        (src / rel).write_text("", encoding="utf-8")

    discovered = discover_modules_at(src)
    assert "pkg" in discovered, (
        f"the scan stopped reporting the root package, so every project that "
        f"already has it in a SAB layer now has a phantom: {discovered}")
    sab = {"layers": [{"name": "api", "modules": ["pkg", "pkg.api.routes"]}]}
    assert phantom_modules(sab, discovered) == []


# ── 站2: the unregistered finding reaches the reader ─────────────────────────

def test_unregistered_is_emitted_at_a_severity_the_consumer_acts_on() -> None:
    """One constant, both ends. The consumer's threshold used to be a literal
    tuple and the emission a literal severity, and they disagreed."""
    import detection.drift_detector as dd

    assert dd.DriftSeverity.MEDIUM.value in dd.ADVANCE_BLOCKING_SEVERITIES
    src = Path(dd.__file__).read_text(encoding="utf-8")
    marker = 'actual="unregistered",'
    assert marker in src, "the unregistered emission moved"
    before = src[:src.index(marker)]
    emitted = before.rsplit("severity=DriftSeverity.", 1)[1].split(",")[0].strip()
    assert emitted in {s for s in ("MEDIUM", "HIGH", "CRITICAL")
                       if s in dd.ADVANCE_BLOCKING_SEVERITIES}, (
        f"`unregistered` is emitted at {emitted}, which "
        f"_precheck_sab_consistency's filter ({dd.ADVANCE_BLOCKING_SEVERITIES}) "
        f"drops — the branch Round 98 wrote for it cannot run")


def test_the_advance_block_actually_reports_an_unregistered_module(
        tmp_path, capsys) -> None:
    """The behavioural half. A severity constant proves nothing about whether
    the finding travels; this drives the real precheck."""
    from cli.advance_prechecks import _precheck_sab_consistency

    project = _project(tmp_path)
    src = project / "03-development" / "src" / "pkg"
    (src / "api").mkdir(parents=True)
    (src / "util").mkdir(parents=True)
    (src / "__init__.py").write_text("", encoding="utf-8")
    (src / "api" / "routes.py").write_text("", encoding="utf-8")
    (src / "util" / "misc.py").write_text("", encoding="utf-8")
    (project / ".methodology" / "state.json").write_text(
        json.dumps({"current_phase": 3}), encoding="utf-8")

    rc = _precheck_sab_consistency(3, project)
    out = capsys.readouterr().out
    assert rc is not None, f"an unregistered module did not block:\n{out}"
    assert "util/misc.py" in out, out
    assert "re-run `python3 harness_cli.py amend-sab" not in out, (
        "the remedy still tells the project to re-run amend-sab, which reads "
        f"SAB.json and never SAD.md:\n{out}")


# ── 站3: SAB.json may not place what SAD.md never placed ─────────────────────

def test_a_placement_only_sab_json_has_is_reported(tmp_path) -> None:
    project = _project(tmp_path, json_layers=[
        {"name": "api", "modules": ["pkg.api.routes"]},
        {"name": "core", "modules": ["pkg.core.rules", "pkg"]},
    ])
    assert undeclared_layer_placements(project) == [
        {"module": "pkg", "layer": "core"}]


def test_a_placement_sad_declares_is_not_reported(tmp_path) -> None:
    """False-accusation control, and it is taskq-final's real shape: it
    declares `taskq_api.app` in a layer called `support`, which that module's
    path does not name. A check that only asked "is this derivable" would
    charge every deliberate placement in the corpus."""
    layers = [
        {"name": "api", "modules": ["pkg.api.routes"]},
        {"name": "support", "modules": ["pkg.app"]},
    ]
    project = _project(tmp_path, sad_layers=layers, json_layers=layers)
    assert undeclared_layer_placements(project) == []


def test_a_derivable_placement_is_not_reported(tmp_path) -> None:
    """The other control: transcribing what the project's own layer names say
    is not an invention, and `amend_sab` is allowed to do it."""
    project = _project(tmp_path, json_layers=[
        {"name": "api", "modules": ["pkg.api.routes", "pkg.api.deps"]},
        {"name": "core", "modules": ["pkg.core.rules"]},
    ])
    assert undeclared_layer_placements(project) == []


def test_no_sab_block_in_the_sad_abstains(tmp_path) -> None:
    project = _project(tmp_path)
    (project / "02-architecture" / "SAD.md").write_text(
        "# SAD\n\nno machine-readable block here\n", encoding="utf-8")
    assert undeclared_layer_placements(project) == []


def test_the_blocking_reason_names_every_placement() -> None:
    reason = undeclared_placements_blocking_reason(
        [{"module": "pkg", "layer": "core"}])
    assert reason and "pkg" in reason and "core" in reason
    assert undeclared_placements_blocking_reason([]) is None


def test_the_advance_block_stops_before_the_drift_findings(tmp_path, capsys) -> None:
    """Order is the finding. Seven of taskq-done's eleven CRITICAL drift
    findings are consequences of the placement, so reporting them first sends
    the project to fix an import its own SAD.md never forbade."""
    from cli.advance_prechecks import _precheck_sab_consistency
    from cli.exit_codes import EX_ADVANCE_SAB_PLACEMENT_UNDECLARED

    project = _project(tmp_path, json_layers=[
        {"name": "api", "modules": ["pkg.api.routes"]},
        {"name": "core", "modules": ["pkg.core.rules", "pkg"]},
    ])
    rc = _precheck_sab_consistency(3, project)
    out = capsys.readouterr().out
    assert rc == EX_ADVANCE_SAB_PLACEMENT_UNDECLARED, out
    assert "SAB architecture violations" not in out, (
        "the drift findings were reported before the project was told who "
        f"wrote the placement they come from:\n{out}")


# ── 站4: the manifest must install what the project says it needs ────────────

_PROBE = {
    "dists": ["pytest", "pytest-asyncio", "coverage", "pytest-cov", "fastapi"],
    "i2d": {"pytest_asyncio": ["pytest-asyncio"], "coverage": ["coverage"],
            "fastapi": ["fastapi"]},
    "one_hop": ["coverage"],          # pytest-cov requires it
}
_SEEDS = {"pytest", "pytest-cov", "ruff"}


def _undeclared(tools, declared):
    from harness.ssot_manifest import undeclared_tool_distributions

    return undeclared_tool_distributions(tools, _PROBE, _SEEDS, set(declared))


def test_a_declared_tool_no_manifest_installs_is_reported() -> None:
    assert _undeclared(["pytest_asyncio"], []) == ["pytest-asyncio"]


def test_a_declared_tool_the_manifest_names_is_not_reported() -> None:
    assert _undeclared(["pytest_asyncio"], ["pytest-asyncio"]) == []


def test_this_frameworks_own_tools_are_not_charged_to_the_project() -> None:
    """Reverse control (Round 42): `ruff` is our toolchain, not their code."""
    assert _undeclared(["ruff", "pytest"], []) == []


def test_a_direct_requirement_of_our_toolchain_is_not_charged_either() -> None:
    """`coverage` arrives with `pytest-cov`. One hop and not the transitive
    closure: measured, the closure from this framework's seeds covers 149 of
    the ~150 distributions in a corpus venv and swallows `pytest-asyncio`."""
    assert _undeclared(["coverage"], []) == []


def test_a_name_that_is_not_an_installed_distribution_is_not_reported() -> None:
    """`make`, `sqlite3`, `python3.11` are in every env_contract's cli_tools."""
    assert _undeclared(["make", "sqlite3", "python3.11"], []) == []


def test_a_venv_bootstrap_package_is_not_reported() -> None:
    probe = dict(_PROBE, dists=_PROBE["dists"] + ["pip"])
    from harness.ssot_manifest import undeclared_tool_distributions

    assert undeclared_tool_distributions(["pip"], probe, _SEEDS, set()) == []


def test_no_env_contract_abstains(tmp_path) -> None:
    from harness.ssot_manifest import manifest_missing_declared_tools

    project = tmp_path / "proj"
    (project / ".methodology").mkdir(parents=True)
    assert manifest_missing_declared_tools(project) == []


def test_no_project_venv_abstains(tmp_path) -> None:
    """Rounds 32/35: a comparison with one side missing was not made. The
    project's own interpreter is what knows which distributions are installed;
    without it there is no reading, and no reading is not a finding."""
    from harness.ssot_manifest import manifest_missing_declared_tools

    project = tmp_path / "proj"
    (project / ".methodology").mkdir(parents=True)
    (project / ".methodology" / "env_contract.json").write_text(
        json.dumps({"cli_tools": ["pytest_asyncio"]}), encoding="utf-8")
    assert manifest_missing_declared_tools(project) == []


def test_a_project_with_no_manifest_file_declares_nothing(tmp_path) -> None:
    """Not an abstention. If "no manifest" abstained, deleting
    requirements.txt would clear the check — five corpus projects reached P9
    with no dependency manifest at all."""
    from harness.ssot_manifest import _declared_manifest_names

    project = tmp_path / "proj"
    project.mkdir()
    assert _declared_manifest_names(project) == set()


def test_setup_cfg_is_not_read_as_a_dependency_manifest(tmp_path) -> None:
    """Measured: every corpus project with a setup.cfg uses it for
    import-linter contracts, and reading its section keys as package names
    invented thirteen declarations on taskq-advance, which ships none."""
    from harness.ssot_manifest import _declared_manifest_names

    project = tmp_path / "proj"
    project.mkdir()
    (project / "setup.cfg").write_text(
        "[importlinter]\nroot_package = pkg\n", encoding="utf-8")
    assert _declared_manifest_names(project) == set()


def test_pyproject_dependencies_count_as_declared(tmp_path) -> None:
    from harness.ssot_manifest import _declared_manifest_names

    project = tmp_path / "proj"
    project.mkdir()
    (project / "pyproject.toml").write_text(
        '[project]\nname = "x"\ndependencies = ["pytest-asyncio==1.4.0"]\n'
        '[project.optional-dependencies]\ndev = ["ruff>=0.5"]\n',
        encoding="utf-8")
    assert _declared_manifest_names(project) == {"pytest-asyncio", "ruff"}


def test_the_remedy_names_the_document_that_decides_layers() -> None:
    assert "SAD.md" in UNPLACEABLE_REMEDY
    assert "generate_sab.py" in UNPLACEABLE_REMEDY
    assert "amend-sab" not in UNPLACEABLE_REMEDY, (
        "amend_sab reads .methodology/SAB.json and never SAD.md, so telling "
        "the project to re-run it produces the same refusal")
