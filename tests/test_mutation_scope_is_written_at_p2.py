"""Round 113 站6 — the SAB's mutation scope reaches setup.cfg at the real P2 exit.

`_regenerate_mutmut_scope` runs once, at `advance-phase --completed-phase 2`,
and refused to write any path that did not exist on disk. At the P2 exit no
source code exists — Phase 3 writes it — so the refusal was certain: across
the corpus the harness has never once written a `[mutmut]` section (every one
is hand-written), and every scoped project's ledger carries one
"resolve to non-existent director(ies)" row timestamped before its first
`03-development/src` commit. taskq-sol's row told the operator to "fix the
layer→module mapping in the SAB"; its SAB was right.

The scope needs no filesystem when the SAB itself says what a layer is: a
dotted name with modules declared beneath it is a package, so a layer whose
modules share such a package — and no other layer reaches into it — is that
package's directory. Every scoped layer in the corpus has that shape. Only a
layer that cannot be read that way still needs the tree to tell a `.py` leaf
from a package, and that one is still refused rather than guessed.

The Gate 2 drift check derived the scope WITHOUT the project root while the
generator derived it with it, so a leaf scope the generator wrote as `.py`
paths would read as drift forever. One derivation now, called the same way.
"""

from __future__ import annotations

import json
from pathlib import Path

import harness_cli  # noqa: F401  entry-first load order
from cli.phase_cmds import _regenerate_mutmut_scope
from core.quality_gate.mutmut_scope import read_paths_to_mutate, resolve_mutation_scope, scope_drift

_SRC = "03-development/src"

# taskq-sol's SAB as it was at its P2 exit, trimmed to what the chain reads:
# leaf modules only, the layer package itself not listed.
_SOL = {
    "nfr_traceability": {"NFR-08": {"dimension": "mutation_testing",
                                    "scope_layers": ["service", "repository"]}},
    "layers": [
        {"name": "api", "modules": ["taskq_api.api.tasks", "taskq_api.api.runs"]},
        {"name": "service", "modules": ["taskq_api.service.auth", "taskq_api.service.facade",
                                        "taskq_api.service.runner"]},
        {"name": "repository", "modules": ["taskq_api.repository.tasks",
                                           "taskq_api.repository.session"]},
        {"name": "models", "modules": ["taskq_api.models"]},
    ],
}


def _p2_exit_project(tmp_path: Path, sab: dict) -> Path:
    """What the tree looks like at `advance-phase --completed-phase 2`: no src."""
    (tmp_path / ".methodology").mkdir()
    (tmp_path / ".methodology" / "SAB.json").write_text(json.dumps(sab), encoding="utf-8")
    return tmp_path


def test_the_p2_exit_writes_the_layer_packages(tmp_path):
    project = _p2_exit_project(tmp_path, _SOL)
    assert _regenerate_mutmut_scope(project) is True
    assert read_paths_to_mutate(project) == (
        f"{_SRC}/taskq_api/repository, {_SRC}/taskq_api/service")


def test_a_package_another_layer_reaches_into_is_not_collapsed():
    sab = json.loads(json.dumps(_SOL))
    sab["layers"][0]["modules"].append("taskq_api.service.http_glue")  # api owns one
    paths = resolve_mutation_scope(sab, _SRC)
    assert f"{_SRC}/taskq_api/service," not in paths + ","
    assert f"{_SRC}/taskq_api/service/auth" in paths


def test_a_scope_that_cannot_be_read_without_code_is_refused_not_guessed(tmp_path):
    sab = {"nfr_traceability": {"NFR-08": {"dimension": "mutation_testing",
                                           "scope_layers": ["core"]}},
           "layers": [{"name": "core", "modules": ["app.engine"]}]}
    project = _p2_exit_project(tmp_path, sab)
    assert _regenerate_mutmut_scope(project) is False
    assert not (project / "setup.cfg").exists()
    ledger = (project / ".methodology" / "degradations.jsonl").read_text(encoding="utf-8")
    assert "fix the layer→module mapping" not in ledger
    assert "app.engine" in ledger


def test_a_written_leaf_scope_does_not_read_as_drift(tmp_path):
    sab = {"nfr_traceability": {"NFR-08": {"dimension": "mutation_testing",
                                           "scope_layers": ["core"]}},
           "layers": [{"name": "core", "modules": ["app.engine"]}]}
    project = _p2_exit_project(tmp_path, sab)
    leaf = project / _SRC / "app"
    leaf.mkdir(parents=True)
    (leaf / "engine.py").write_text("x = 1\n", encoding="utf-8")
    assert _regenerate_mutmut_scope(project) is True
    assert read_paths_to_mutate(project) == f"{_SRC}/app/engine.py"
    assert scope_drift(project) is None


def test_the_drift_remedy_is_one_a_phase_3_project_can_take(tmp_path):
    project = _p2_exit_project(tmp_path, _SOL)
    (project / "setup.cfg").write_text("[mutmut]\npaths_to_mutate = 03-development/src\n")
    drift = scope_drift(project)
    assert drift and "--completed-phase 2" not in drift
    assert f"paths_to_mutate = {_SRC}/taskq_api/repository, {_SRC}/taskq_api/service" in drift
