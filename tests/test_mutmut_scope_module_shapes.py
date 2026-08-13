"""A module that is a file is still a module (Round 50 站0).

`resolve_mutation_scope` turns each SAB module name into a path by replacing
dots with slashes, and `_regenerate_mutmut_scope` then requires every result
to be a DIRECTORY. A leaf module — `taskq.repository.task_repo`, on disk as
`task_repo.py` — fails that test, so the whole scope is discarded and the
ledger reports paths the project "does not have".

Measured 2026-08-13 on a real P2→P3 handoff: eight scope paths, eight ledger
entries, and all eight modules present on disk as `.py` files. setup.cfg was
left unwritten, so mutation testing ran against the entire source tree rather
than the scope the SAB declared.

The two on-disk shapes of one dotted name are not news to this codebase:
`detection.drift_detector.sab_module_to_path_variants` exists precisely
because a SAB entry may be a leaf module or a package, and Round 6 站3 fixed
the same confusion in `discover_modules_at` after it made every package-style
registration look phantom. This is that fix's third consumer.
"""

from __future__ import annotations

import json

from core.quality_gate.mutmut_scope import resolve_mutation_scope

_SRC = "03-development/src"


def _sab(*module_names: str) -> dict:
    return {
        "layers": [{"name": "service", "modules": list(module_names)}],
        "nfr_traceability": {
            "NFR-05": {"dimension": "mutation_testing", "scope_layers": ["service"]},
        },
    }


def _write_leaf(root, dotted: str) -> None:
    """Create `dotted` as a leaf module: <src>/a/b/c.py."""
    parts = dotted.split(".")
    pkg = root / _SRC
    for part in parts[:-1]:
        pkg = pkg / part
        pkg.mkdir(parents=True, exist_ok=True)
        (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / f"{parts[-1]}.py").write_text("x = 1\n", encoding="utf-8")


def test_leaf_modules_resolve_to_paths_that_exist(tmp_path):
    """The scope a project declares must survive contact with its own tree."""
    _write_leaf(tmp_path, "taskq.service.auth")
    _write_leaf(tmp_path, "taskq.service.runner")

    paths = resolve_mutation_scope(
        _sab("taskq.service.auth", "taskq.service.runner"), _SRC,
        project_root=tmp_path,
    )

    assert paths, "a scope of two real modules resolved to nothing"
    for p in (x.strip() for x in paths.split(",")):
        assert (tmp_path / p).exists(), (
            f"{p!r} is not on disk — the scope names something the project "
            f"does not have, and the caller will discard the whole scope"
        )


def test_package_modules_still_resolve(tmp_path):
    """The package shape that already worked keeps working."""
    pkg = tmp_path / _SRC / "taskq" / "service"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "impl.py").write_text("x = 1\n", encoding="utf-8")

    paths = resolve_mutation_scope(_sab("taskq.service"), _SRC,
                                   project_root=tmp_path)
    assert paths
    for p in (x.strip() for x in paths.split(",")):
        assert (tmp_path / p).exists()


def test_a_module_that_is_absent_is_still_reported_absent(tmp_path):
    """Widening the shapes must not turn the existence check into a rubber stamp.

    `taskq.service.ghost` is on no disk anywhere; the resolver must not
    invent a path for it.
    """
    _write_leaf(tmp_path, "taskq.service.auth")

    paths = resolve_mutation_scope(
        _sab("taskq.service.auth", "taskq.service.ghost"), _SRC,
        project_root=tmp_path,
    )
    resolved = [x.strip() for x in (paths or "").split(",") if x.strip()]
    assert not any((tmp_path / p).exists() is False for p in resolved), (
        "the resolver emitted a path for a module that is not on disk"
    )
    assert any("auth" in p for p in resolved)


def test_json_round_trip_of_a_real_sab_shape(tmp_path):
    """Dict-shaped module entries (`{"name": ...}`) resolve the same way."""
    _write_leaf(tmp_path, "taskq.service.auth")
    sab = json.loads(json.dumps({
        "layers": [{"name": "service",
                    "modules": [{"name": "taskq.service.auth"}]}],
        "nfr_traceability": {
            "NFR-05": {"dimension": "mutation_testing",
                       "scope_layers": ["service"]},
        },
    }))
    paths = resolve_mutation_scope(sab, _SRC, project_root=tmp_path)
    assert paths
    assert (tmp_path / paths.split(",")[0].strip()).exists()
