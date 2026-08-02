"""Round 29 Station 2b — derive mutmut paths_to_mutate from the SAB.

Before this module, ``_resolve_mutmut_workdir`` read ``setup.cfg``'s
``[mutmut] paths_to_mutate`` and fell back to the entire ``03-development/src``
when the key was absent.  That fallback was silent and mutated ~1.83x the scope
declared in the SAB/SPEC (e.g. cli/models/observability were never meant to go
through mutation testing per SPEC §10 NFR-08).

This module bridges the gap: it reads the SAB's ``nfr_traceability`` entry for
the NFR mapped to ``mutation_testing``, extracts its ``scope_layers``, and
resolves those layer names to filesystem paths via the SAB's module registry.
The result is written as ``[mutmut] paths_to_mutate`` in ``setup.cfg`` during
the P2→P3 handoff (advance-phase), so by the time Gate 2 runs the scope is
already correct and reviewable in the commit.

When the SAB declares no mutation scope, this module returns ``None`` — the
caller should log a degradation and fall back to its existing default.

Round 30 站2 — two defects in the Round 29 form of this module, both live:

1. ``resolve_mutation_scope`` returned MODULE paths (``taskq_plus/service``)
   with no source root, while every consumer resolves them against the PROJECT
   root. Probed on a fixture matching taskq-advance's SAB:

       paths            = 'taskq_plus/service, taskq_plus/storage'
       cwd / paths      = <project>/taskq_plus/service, taskq_plus/storage
       src_dir.exists() = False

   ``compute_mutation_score`` aborts on exactly that check, so a populated
   ``scope_layers`` would still have scored mutation_testing 0 — the same
   verdict, a different message. The station-2 fix would have looked applied
   and changed nothing.
2. The value is a comma-separated LIST, and both callers also did
   ``src_dir = cwd / paths_to_mutate`` on the whole string.
   ``run_mutation_precheck`` at least split it first for its existence check;
   ``compute_mutation_score`` did not. :func:`mutate_dirs` is now the one place
   that turns the config value into real directories.

Nothing caught either one because the Round 29 test asserted the returned
STRING and never resolved it against a filesystem.
"""
from __future__ import annotations

import configparser
from pathlib import Path
from typing import Optional


def resolve_mutation_scope(
    sab: dict,
    src_root: str,
) -> Optional[str]:
    """Return a comma-separated ``paths_to_mutate`` string derived from the SAB,
    or ``None`` when the SAB declares no mutation scope.

    *src_root* is the PROJECT-RELATIVE source root (e.g. ``03-development/src``)
    that the returned paths are prefixed with. It is a required argument, not a
    default, because omitting the prefix is precisely the Round 29 defect this
    signature exists to make impossible: every consumer resolves the result
    against the project root, so a module-relative path silently names a
    directory that does not exist.

    Resolution steps:
    1. Find the NFR whose ``dimension`` (or type→dimension mapping) is
       ``mutation_testing``.
    2. If that NFR carries a ``scope_layers`` list, resolve each layer name
       to the module prefix(es) registered for that layer in the SAB.
    3. Convert module prefixes to project-relative paths
       (``taskq_plus.service`` → ``<src_root>/taskq_plus/service``).
    4. Return a comma-joined string suitable for ``setup.cfg``'s
       ``[mutmut] paths_to_mutate``.

    When ``scope_layers`` is absent/empty, or the NFR is not found, returns
    ``None`` — the caller should fall back to its own default.

    Existence is deliberately NOT checked here: this function is string maths
    over the SAB, and the caller is the one that owns a project path and can
    record a degradation naming what is missing.
    """
    # 1. Find the mutation_testing NFR
    nfr_traceability: dict = sab.get("nfr_traceability", {})
    nfr_dim_mapping: dict = sab.get("nfr_dimension_mapping", {})

    mutation_nfr_id: Optional[str] = None
    for nfr_id, dim in nfr_dim_mapping.items():
        if dim == "mutation_testing":
            mutation_nfr_id = nfr_id
            break

    if mutation_nfr_id is None:
        # Try to find it by dimension field on the NFR entry itself
        for nfr_id, nfr_entry in nfr_traceability.items():
            if isinstance(nfr_entry, dict) and nfr_entry.get("dimension") == "mutation_testing":
                mutation_nfr_id = nfr_id
                break

    if mutation_nfr_id is None:
        return None

    nfr_entry = nfr_traceability.get(mutation_nfr_id, {})
    if not isinstance(nfr_entry, dict):
        return None

    scope_layers: list[str] = nfr_entry.get("scope_layers", [])
    if not scope_layers:
        return None

    # 2. Resolve layer names → module prefixes
    layers: list[dict] = sab.get("layers", [])
    layer_by_name: dict[str, dict] = {lyr.get("name", ""): lyr for lyr in layers}

    module_prefixes: list[str] = []
    for layer_name in scope_layers:
        layer = layer_by_name.get(layer_name)
        if layer is None:
            continue
        for mod in layer.get("modules", []):
            if isinstance(mod, dict):
                mod_name = mod.get("name", "")
            else:
                mod_name = str(mod)
            if mod_name:
                module_prefixes.append(mod_name)

    if not module_prefixes:
        return None

    # 3. Convert module prefixes to project-relative paths.
    #    taskq_plus.service → 03-development/src/taskq_plus/service
    #    Use the shortest unique prefix per module to avoid double-counting
    #    nested paths (e.g. taskq_plus.service AND taskq_plus.service.executor
    #    → we only want the package-level path taskq_plus/service).
    _root = src_root.strip("/")
    unique_paths: set[str] = set()
    for prefix in module_prefixes:
        path = prefix.replace(".", "/")
        unique_paths.add(f"{_root}/{path}" if _root else path)

    # Remove child paths when a parent is already present.
    paths = sorted(unique_paths)
    result: list[str] = []
    for p in paths:
        is_child = any(
            p != other and p.startswith(other + "/")
            for other in paths
        )
        if not is_child:
            result.append(p)

    return ", ".join(result)


def mutate_dirs(cwd: Path, paths_to_mutate: str) -> list[Path]:
    """Turn a ``[mutmut] paths_to_mutate`` config value into real directories.

    The value is a COMMA-SEPARATED list — ``cwd / paths_to_mutate`` on the whole
    string names one directory that cannot exist as soon as there is more than
    one entry. Both callers did exactly that (Round 30 站2); this is the one
    place that splits it, so the next caller cannot get it wrong differently.

    Returns absolute paths in declaration order, including ones that do not
    exist — existence is the caller's judgement to make and to report.
    """
    return [
        cwd / p.strip()
        for p in paths_to_mutate.split(",")
        if p.strip()
    ]


def write_paths_to_mutate(
    project_root: str | Path,
    paths: str,
) -> "tuple[bool, str | None]":
    """Write (or update) ``[mutmut] paths_to_mutate`` in ``setup.cfg``.

    Preserves existing sections and keys.  Only the ``paths_to_mutate`` key
    under ``[mutmut]`` is touched.  Existing ``paths_to_exclude`` values are
    left intact.

    Returns ``(wrote, previous_value)``:

    * ``wrote`` is False when the on-disk value already matched — the caller
      must not stage a file it did not change.
    * ``previous_value`` is the value that was replaced, or None when the key
      did not exist. A non-None previous value means someone hand-edited the
      scope (or the SAB changed); the caller records that in the degradation
      ledger rather than overwriting in silence. The header comment claims
      human edits get overwritten, so the overwrite has to leave a trace.
    """
    cfg_path = Path(project_root) / "setup.cfg"
    cfg = configparser.ConfigParser()
    if cfg_path.exists():
        cfg.read(cfg_path, encoding="utf-8")

    if not cfg.has_section("mutmut"):
        cfg.add_section("mutmut")

    old = cfg.get("mutmut", "paths_to_mutate", fallback=None)
    if old == paths:
        return False, old

    cfg.set("mutmut", "paths_to_mutate", paths)

    with open(cfg_path, "w", encoding="utf-8") as f:
        f.write(
            "# [mutmut] paths_to_mutate — auto-generated from SAB scope_layers\n"
            "# by core.quality_gate.mutmut_scope during advance-phase P2→P3 handoff.\n"
            "# Human edits will be overwritten on the next P2→P3 advance.\n"
        )
        cfg.write(f)
    return True, old
