"""SAB auto-amender.

P3-P6 introduces new modules in `03-development/src/` (e.g. `parser.py`,
`injection_guard.py`, `_atomic.py`). Previously the user had to hand-edit
`.methodology/SAB.json` to add them to `layers[*].modules`, otherwise
`_check_sab_module_alignment` (harness_cli.py:2227) would BLOCK the gate.

This module automates that step:
  - discover_modules(project) → list of module path-format strings found
                                 in 03-development/src/ (or the project's
                                 configured active_src_dir).
  - missing_modules(sab_dict, discovered) → which discovered modules are
                                            not yet in any layer.
  - amend_sab(project_root, dry_run=False) → atomically append missing
                                            modules to the last layer
                                            (heuristic: most modules
                                            belong there) and return the
                                            added list.

Design choice: we always append to the LAST layer to avoid silently
misclassifying modules. The output prints each addition with its source
path so the user can run `extract_sab_from_sad` later to regenerate from
SAD.md (the authoritative source) if a different layer is desired.

This module deliberately does NOT touch SAD.md, `fr_module_traceability`,
`allowed_dependencies`, or any other field — those are still curated by
humans and re-derived from SAD.md by `scripts/generate_sab.py`.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable

from core.atomic_io import atomic_write_json


# Default source directory; can be overridden per-project via the
# `active_src_dir` ProjectLayout property (P3+ may relocate src/).
_DEFAULT_SRC_DIR = "03-development/src"


def discover_modules(project_root: Path, src_dir: str = _DEFAULT_SRC_DIR) -> list[str]:
    """Return sorted list of dotted module names under `<project>/<src_dir>/`.

    Mirrors `_check_sab_module_alignment` in harness_cli.py: skip
    ``__pycache__`` and ``__init__.py`` (package marker, not a SAB module)
    and emit dotted form (``taskq.core.models``), not the raw project-relative
    path. The two functions must use the SAME representation, otherwise an
    amend run can never close the BLOCKED it was supposed to fix — the path
    strings written into SAB.json by amend would never equal the dotted
    strings checked against SAB.json by the gate.
    """
    src_path = project_root / src_dir
    if not src_path.is_dir():
        return []
    found = []
    for py in sorted(src_path.rglob("*.py")):
        if "__pycache__" in py.parts:
            continue
        if py.name == "__init__.py":
            continue
        rel = py.relative_to(src_path)
        parts = rel.with_suffix("").parts
        if not parts:
            continue
        found.append(".".join(parts))
    return found


def _flatten_registered(sab: dict, src_dir: str = _DEFAULT_SRC_DIR) -> set[str]:
    """Union of every layer's modules list, normalised to dotted form.

    SAB entries may be expressed in either dotted (``taskq.cli``) or path
    (``taskq/cli.py``, ``03-development/src/taskq/cli.py``) form; both are
    normalised to dotted here so the comparison against `discover_modules`
    agrees with `_check_sab_module_alignment` in harness_cli.py.
    """
    out: set[str] = set()
    src_prefix = src_dir if src_dir.endswith("/") else f"{src_dir}/"
    for layer in sab.get("layers", []):
        for m in layer.get("modules", []):
            if not isinstance(m, str):
                continue
            stripped = m.strip().lstrip("./")
            for prefix in (src_prefix, "src/"):
                if stripped.startswith(prefix):
                    stripped = stripped[len(prefix):]
                    break
            if stripped.endswith("/") or not stripped:
                continue
            if stripped.endswith(".py"):
                stripped = stripped[:-3]
            out.add(stripped.replace("/", "."))
    return out


def missing_modules(sab: dict, discovered: Iterable[str], src_dir: str = _DEFAULT_SRC_DIR) -> list[str]:
    """Modules present on disk but not yet in any SAB layer."""
    registered = _flatten_registered(sab, src_dir)
    return [m for m in discovered if m not in registered]


def _heuristic_layer_choice(sab: dict, module_path: str) -> str:
    """Pick the most likely layer for a new module.

    Strategy: private helpers (leading `_`) → `core` (business logic).
    Anything else → last layer (least risky — user can re-categorise by
    re-running `extract_sab_from_sad` later).
    """
    if not sab.get("layers"):
        return "core"
    name = Path(module_path).name
    if name.startswith("_"):
        # Pick the first layer named "core" or "domain" if present.
        for layer in sab["layers"]:
            if layer.get("name") in ("core", "domain", "business"):
                return layer["name"]
    # Fallback: last layer (e.g. "infra" or "integration").
    return sab["layers"][-1]["name"]


def amend_sab(project_root: Path, src_dir: str = _DEFAULT_SRC_DIR,
              dry_run: bool = False) -> list[str]:
    """Discover missing modules, append them to the heuristic layer,
    atomically write SAB.json. Returns the list of added modules.

    Idempotent: running twice adds nothing on the second call.
    No-op (returns []) when SAB.json is absent or no modules are missing.
    """
    sab_path = project_root / ".methodology" / "SAB.json"
    if not sab_path.exists():
        return []

    sab = _safe_load(sab_path)
    if not isinstance(sab, dict) or not sab.get("layers"):
        return []

    discovered = discover_modules(project_root, src_dir)
    added = missing_modules(sab, discovered, src_dir)
    if not added:
        return []

    if dry_run:
        return added

    # Group additions by chosen layer to keep modules ordered within layer.
    by_layer: dict[str, list[str]] = {}
    for module_path in added:
        layer_name = _heuristic_layer_choice(sab, module_path)
        by_layer.setdefault(layer_name, []).append(module_path)

    for layer in sab["layers"]:
        if layer["name"] in by_layer:
            existing = layer.setdefault("modules", [])
            for m in by_layer[layer["name"]]:
                if m not in existing:
                    existing.append(m)

    atomic_write_json(sab_path, sab)
    return added


def _safe_load(path: Path) -> dict:
    """Load JSON, returning {} on any failure (corrupt file, permission, etc)."""
    import json
    try:
        with path.open(encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}