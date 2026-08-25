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

import re
from pathlib import Path
from typing import Iterable

from core.atomic_io import atomic_write_json
from core.utils.timefmt import utc_now_iso


# Default source directory; can be overridden per-project via the
# `active_src_dir` ProjectLayout property (P3+ may relocate src/).
_DEFAULT_SRC_DIR = "03-development/src"

# Floor for `resolve_phantom`'s --reason. Short enough that a real sentence
# clears it, long enough that "fix" / "wrong path" does not.
_MIN_REASON_CHARS = 20


def sab_module_candidate(mod: object) -> object:
    """Extract the physical-location candidate from a SAB ``modules`` entry.

    Dict-shaped entries (``{"name": <logical>, "implemented_in":
    <dotted-or-path, optional>}`` — the official schema form emitted by
    `sab_parser.render_canonical_sab_template()` for a module whose logical
    name differs from its physical location) prefer `implemented_in` when
    present and non-blank (it is the actual physical location); otherwise
    `name` is used. Non-dict values (including plain strings and malformed
    types) pass through unchanged.

    This is the single source of truth for unwrapping dict-shaped SAB
    entries: `normalize_sab_module_to_dotted` below feeds its result through
    the dotted-name normalization tail, and `scripts/generate_sab.py`'s
    path-rewrite step uses it directly so dict- and string-shaped entries
    are rewritten under the same logic. Note: `core.phase_hooks.
    preflight_sab_check` does NOT call this function — it has its own
    independent, already dict-aware inline unwrap.
    """
    if isinstance(mod, dict):
        candidate = mod.get("implemented_in")
        if not isinstance(candidate, str) or not candidate.strip():
            candidate = mod.get("name")
        return candidate
    return mod


def normalize_sab_module_to_dotted(mod: object, src_dir: str = _DEFAULT_SRC_DIR) -> str | None:
    """Normalise a SAB ``modules`` entry into a dotted module name.

    SAB entries may use either dotted notation (``taskq.cli``,
    ``core.utils``) or path notation (``taskq/cli.py``,
    ``03-development/src/taskq/cli.py``). Both forms map to the same
    dotted name after stripping the project-relative path prefix
    (``<src_dir>/`` or ``src/``) and the ``.py`` suffix. Dict-shaped entries
    are first unwrapped to a candidate string via `sab_module_candidate`.

    Returns ``None`` for directory markers (trailing ``/``) and entries with
    no usable string (non-dict, non-str, or a dict with no `name`/
    `implemented_in`).

    This is the single source of truth for SAB module-name normalization:
    `_flatten_registered` below and `harness_cli._check_sab_module_alignment`
    both call this function (via its `_normalize_sab_module_to_dotted`
    delegate), so `amend_sab` and the alignment gate can never silently
    disagree about which modules are "registered". `scripts/generate_sab.py`
    also calls it directly when filtering `__init__.py`-sourced entries.
    """
    mod = sab_module_candidate(mod)
    if not isinstance(mod, str):
        return None
    stripped = mod.strip().lstrip("./")
    src_prefix = src_dir if src_dir.endswith("/") else f"{src_dir}/"
    for prefix in (src_prefix, "src/"):
        if stripped.startswith(prefix):
            stripped = stripped[len(prefix):]
            break
    if stripped.endswith("/") or not stripped:
        return None
    if stripped.endswith(".py"):
        stripped = stripped[:-3]
    return stripped.replace("/", ".")


def discover_modules_at(src_path: Path) -> list[str]:
    """Return sorted list of dotted module names found directly under `src_path`.

    Mirrors `_check_sab_module_alignment` in cli/gate_cmds.py: emit dotted
    form (``taskq.core.models``), not the raw project-relative path. Both
    call sites must use the SAME representation, otherwise an amend run can
    never close the BLOCKED it was supposed to fix — the path strings
    written into SAB.json by amend would never equal the dotted strings
    checked against SAB.json by the gate. `gate_cmds.py` calls this
    directly (it already has the resolved `ProjectLayout(...).active_src_dir`
    in hand); `discover_modules` below is a thin project-root+src_dir-string
    wrapper for callers (like `amend_sab`'s CLI-facing ``--src-dir`` flag)
    that don't.

    A SAB entry may name either a leaf module (``taskq.cli`` → `taskq/cli.py`)
    or a PACKAGE (``taskq.cli`` → `taskq/cli/__init__.py`) — two distinct
    on-disk shapes sharing one dotted-name space (see
    `detection.drift_detector.sab_module_to_path_variants`, which already
    tries both). Round 6 station 3: ``__init__.py`` is still skipped as a
    *leaf-module* candidate (the marker file itself isn't a module), but the
    PACKAGE it marks is additionally registered under its own dotted name —
    omitting this made every package-style SAB registration look "phantom"
    to `phantom_modules` even though `preflight_sab_check` (P4+, via
    path-variant probing) correctly found it: a confirmed false-positive
    Gate 1 BLOCK on a legitimate registration.
    """
    if not src_path.is_dir():
        return []
    found = []
    for py in sorted(src_path.rglob("*.py")):
        if "__pycache__" in py.parts:
            continue
        if py.name == "__init__.py":
            pkg_rel = py.parent.relative_to(src_path)
            if pkg_rel.parts:  # not the src_path root itself
                found.append(".".join(pkg_rel.parts))
            continue
        rel = py.relative_to(src_path)
        parts = rel.with_suffix("").parts
        if not parts:
            continue
        found.append(".".join(parts))
    return sorted(set(found))


def discover_modules(project_root: Path, src_dir: str = _DEFAULT_SRC_DIR) -> list[str]:
    """Return sorted list of dotted module names under `<project>/<src_dir>/`.

    See `discover_modules_at` for the actual scan + the two-call-site contract.
    """
    return discover_modules_at(project_root / src_dir)


def _flatten_registered(sab: dict, src_dir: str = _DEFAULT_SRC_DIR) -> set[str]:
    """Union of every layer's modules list, normalised to dotted form.

    Delegates to `normalize_sab_module_to_dotted` so the comparison against
    `discover_modules` agrees with `_check_sab_module_alignment` in
    harness_cli.py.
    """
    out: set[str] = set()
    for layer in sab.get("layers", []):
        for m in layer.get("modules", []):
            dotted = normalize_sab_module_to_dotted(m, src_dir)
            if dotted is not None:
                out.add(dotted)
    return out


def missing_modules(sab: dict, discovered: Iterable[str], src_dir: str = _DEFAULT_SRC_DIR) -> list[str]:
    """Modules present on disk but not yet in any SAB layer."""
    registered = _flatten_registered(sab, src_dir)
    return [m for m in discovered if m not in registered]


def phantom_modules(sab: dict, discovered: Iterable[str], src_dir: str = _DEFAULT_SRC_DIR) -> list[str]:
    """Modules declared in SAB.json but with no on-disk implementation.

    P2 architecture planning often pre-registers modules in SAB layers before
    P3 implementation catches up. Without this check, the planning-vs-implementation
    drift goes undetected until Phase 4 preflight (`PhaseHooks.preflight_sab_check`
    at phase_hooks.py:341) — and by then P2 amendment is no longer reachable.

    Returns the dotted names of phantom modules in deterministic (sorted) order.
    Filters out:
      - directory markers (trailing ``/`` in raw entry form)
      - FR IDs (``FR-XX``) — those are traceability placeholders, not modules
      - dotted names that DO exist on disk (already implemented; not phantom)

    Companion to `missing_modules`: that one returns ``discovered - registered``
    (codebase has new modules not in SAB); this returns ``registered - discovered``
    (SAB claims modules the codebase lacks). Symmetric coverage closes the gap
    where P2 plans something P3 silently drops.
    """
    registered = _flatten_registered(sab, src_dir)
    discovered_set = set(discovered)
    phantoms: list[str] = []
    for raw in registered:
        # Normalize once more so FR-XX / path / dotted forms all compare equal.
        # _flatten_registered already normalized; defensive guard in case the
        # helper is ever called from a context that bypassed normalization.
        dotted = normalize_sab_module_to_dotted(raw, src_dir)
        if dotted is None:
            continue
        if dotted.startswith("FR-") or re.match(r"^FR-\d+$", dotted):
            continue
        if dotted not in discovered_set:
            phantoms.append(dotted)
    return sorted(set(phantoms))


def phantom_module_block(project_root: Path) -> list[str]:
    """This project's phantom modules, resolved against `project_root` alone.

    Round 78 站1. `cli/phase_cmds.py::_advance_prechecks` refuses to advance
    when the SAB declares a module the tree does not contain, and Plan F wrote
    that check inline. It resolved the source directory relative to the
    process's working directory:

        _src_dir_rel = str(src_dir.relative_to(project))
        _discovered = set(discover_modules_at(Path(_src_dir_rel)))

    `discover_modules_at` opens that path. From any directory but the project
    root it opens nothing, every registered module reads as missing, and the
    BLOCK sends the project to `amend-sab --resolve-phantom` for modules that
    are on disk. Measured across the nine corpus projects with a SAB: 0
    phantom from inside, 9–45 from outside — all nine.

    The defect is not the line, it is one name carrying two meanings.
    `discover_modules_at` wants a filesystem path; `phantom_modules` wants a
    RELATIVE prefix for stripping path-form SAB entries. `cli/gate_cmds.py`
    computes the same `_src_dir_rel` and states that rule in a comment, but
    hands it to `amend_sab(Path(project), src_dir=…)`, where the root travels
    as its own argument. Plan F carried the rule across to a call where it
    did not.

    So this uses `discover_modules(project_root, src_dir)` — the root and the
    prefix, named apart, the function `amend_sab` and `cli/project_cmds.py`
    already call. Nothing here reads `os.getcwd()`.

    A project with no `SAB.json` declares nothing and therefore has nothing
    declared-but-missing: `[]`, silently (pre-P2 projects have no SAB and this
    runs at every phase transition). A SAB that exists but will not parse gets
    `_safe_load`'s WARN, because an unreadable declaration is a fact worth
    saying rather than an empty one.
    """
    from core.utils.project_layout import ProjectLayout

    sab_path = Path(project_root) / ".methodology" / "SAB.json"
    if not sab_path.is_file():
        return []
    sab = _safe_load(sab_path)
    if not sab:
        return []

    src_dir = ProjectLayout(project_root).active_src_dir
    try:
        src_rel = str(src_dir.relative_to(Path(project_root)))
    except ValueError:
        src_rel = _DEFAULT_SRC_DIR
    discovered = discover_modules(Path(project_root), src_rel)
    return phantom_modules(sab, discovered, src_rel)


class PhantomResolutionError(ValueError):
    """`resolve_phantom` refused the amendment. The message says why."""


def resolve_phantom(
    project_root: Path,
    declared: str,
    *,
    to: str | None,
    reason: str,
    src_dir: str = _DEFAULT_SRC_DIR,
    drop: bool = False,
) -> str:
    """Retarget or drop a module SAB.json declares, and record why in ADR.md.

    Round 26 — the missing direction. `amend_sab` only ever ADDS what the codebase
    has (code -> SAB). `phantom_modules` detects the reverse (SAB -> code) and the
    gate BLOCKS on it, offering two exits: "implement them" or "amend SAB.json".
    Only the first had tooling, so a wrong Phase 2 guess about physical layout was
    resolved by rewriting production code to match it — taskq-plus P3 did exactly
    that twice, once relocating FR-02's executor and once restarting FR-05 from RED
    to turn a flat `cli.py` into the declared `cli/main.py` + `cli/commands.py`.

    This is deliberately NOT a sync command. If SAB could be rewritten to whatever
    the code happens to be, the Phase 2 architecture would mean nothing; the point
    is that changing it is possible, cheap, and ON THE RECORD. Hence:

      * `reason` is mandatory and must be substantive (>= _MIN_REASON_CHARS);
      * `to` must already exist on disk — otherwise the amendment would replace one
        phantom with another;
      * the amendment is appended to 02-architecture/ADR.md before SAB.json is
        written, so a crash cannot leave a changed architecture with no reason.

    Returns a human-readable summary. Raises PhantomResolutionError on refusal.
    """
    if not (to or drop) or (to and drop):
        raise PhantomResolutionError(
            "specify exactly one of --to <dotted> (retarget) or --drop (remove)"
        )
    reason = (reason or "").strip()
    if len(reason) < _MIN_REASON_CHARS:
        raise PhantomResolutionError(
            f"--reason must be at least {_MIN_REASON_CHARS} characters of actual "
            f"justification (got {len(reason)}). This text is the architecture "
            f"record; 'fix' or 'wrong' is not one."
        )

    sab_path = project_root / ".methodology" / "SAB.json"
    if not sab_path.is_file():
        raise PhantomResolutionError(f"no SAB.json at {sab_path}")
    sab = _safe_load(sab_path)
    if not isinstance(sab, dict) or not sab.get("layers"):
        raise PhantomResolutionError(f"{sab_path} has no layers to amend")

    declared_dotted = normalize_sab_module_to_dotted(declared, src_dir) or declared
    discovered = set(discover_modules(project_root, src_dir))
    if declared_dotted in discovered:
        raise PhantomResolutionError(
            f"{declared_dotted!r} exists on disk — it is not a phantom, and this "
            f"command is not a rename tool for working code"
        )

    to_dotted: str | None = None
    if to:
        to_dotted = normalize_sab_module_to_dotted(to, src_dir) or to
        if to_dotted not in discovered:
            raise PhantomResolutionError(
                f"--to {to_dotted!r} does not exist under {src_dir}/ either, so the "
                f"amendment would swap one phantom for another. Implement it first, "
                f"or use --drop."
            )

    replaced = _rewrite_module_reference(sab, declared_dotted, to_dotted, src_dir)
    if not replaced:
        raise PhantomResolutionError(
            f"{declared_dotted!r} appears in no SAB layer or fr_module_traceability "
            f"entry — nothing to amend"
        )

    action = f"retargeted to `{to_dotted}`" if to_dotted else "dropped"
    _append_adr_amendment(project_root, declared_dotted, action, reason, replaced)
    atomic_write_json(sab_path, sab)
    return (
        f"[amend-sab] architecture amended: `{declared_dotted}` {action} "
        f"({', '.join(replaced)}); reason recorded in 02-architecture/ADR.md"
    )


def _rewrite_module_reference(
    sab: dict, declared: str, to: str | None, src_dir: str
) -> list[str]:
    """Replace or remove `declared` in layers + fr_module_traceability, in place.

    Returns the list of places touched (for the ADR record). Both containers are
    rewritten together on purpose: leaving traceability pointing at a name the
    layers no longer carry is how a resolved phantom comes back as a
    _filter_phantoms_for_fr ownership miss.
    """
    touched: list[str] = []

    for layer in sab.get("layers") or []:
        modules = layer.get("modules")
        if not isinstance(modules, list):
            continue
        kept = []
        for entry in modules:
            if normalize_sab_module_to_dotted(entry, src_dir) == declared:
                touched.append(f"layer {layer.get('name')!r}")
                if to is not None:
                    kept.append(to)
                continue
            kept.append(entry)
        layer["modules"] = kept

    trace = sab.get("fr_module_traceability")
    if isinstance(trace, dict):
        for fr, entries in list(trace.items()):
            as_list = [entries] if isinstance(entries, str) else entries
            if not isinstance(as_list, list):
                continue
            kept = []
            hit = False
            for entry in as_list:
                if normalize_sab_module_to_dotted(entry, src_dir) == declared:
                    hit = True
                    if to is not None:
                        kept.append(to)
                    continue
                kept.append(entry)
            if hit:
                touched.append(f"fr_module_traceability[{fr}]")
                # Preserve the single-string shape when it stays single.
                trace[fr] = kept[0] if len(kept) == 1 and isinstance(entries, str) else kept

    return touched


def _append_adr_amendment(
    project_root: Path, declared: str, action: str, reason: str, touched: list[str]
) -> None:
    """Append the amendment to the project's ADR.md, creating it if absent."""
    from core.utils.project_layout import ProjectLayout

    adr = ProjectLayout(project_root).adr_path
    adr.parent.mkdir(parents=True, exist_ok=True)
    header = "" if adr.is_file() else "# Architecture Decision Records\n"
    entry = (
        f"\n## Architecture Amendment — `{declared}` {action}\n\n"
        f"- **When**: {utc_now_iso()}\n"
        f"- **Amended**: {', '.join(touched)}\n"
        f"- **Reason**: {reason}\n"
        f"- **Recorded by**: `harness_cli.py amend-sab --resolve-phantom` "
        f"(Gate 1 Architecture Amendment Protocol)\n"
    )
    with adr.open("a", encoding="utf-8") as fh:
        fh.write(header + entry)


def _layer_segments(module_path: str) -> list[str]:
    """Path/dotted-form segments of *module_path*, extension stripped.

    `taskq_plus/cli/main.py` and `taskq_plus.cli.main` both give
    ['taskq_plus', 'cli', 'main']. The trailing segment is kept: a SAB entry may
    name a PACKAGE by its own dotted name (`taskq_plus.cli`), in which case that
    last segment IS the layer.
    """
    stem = re.sub(r"\.py$", "", module_path)
    return [s for s in re.split(r"[./\\]", stem) if s]


def _heuristic_layer_choice(sab: dict, module_path: str) -> str:
    """Pick the layer for a new module, preferring what its own name says.

    Order:
      1. A dotted/path segment that matches a declared layer name. Projects name
         their packages after their layers — `taskq_plus/service/executor.py`
         belongs in the `service` layer — so the module states its own answer and
         nothing needs to be guessed. Deepest match wins, so
         `a/storage/cache/x.py` picks `cache` over `storage` when both exist.
      2. Private helpers (leading `_`) → the first `core`/`domain`/`business` layer.
      3. Last layer, as before.

    Round 26 — step 1 is new, and it is a bug fix rather than a refinement. The
    old first rule for every non-underscore module was "append to the LAST layer
    (least risky)", which is only least risky when the last layer is a catch-all.
    In taskq-plus the last layer is `config`, so an amend run filed
    `taskq_plus.cli`, `taskq_plus.service`, `taskq_plus.storage` and
    `taskq_plus.__main__` there — leaving SAB.json's own layer declaration in
    direct contradiction with the `cli > observability > service > storage >
    models` layering that project's NFR-06 enforces via `.importlinter`. Two
    architecture records, one codebase, silently disagreeing.

    Callers that want to see a guess: `amend_sab` reports which modules took the
    fallback, because a guess nobody is told about is indistinguishable from a
    decision.
    """
    layers = sab.get("layers") or []
    if not layers:
        return "core"
    declared = {str(layer.get("name")) for layer in layers if layer.get("name")}
    for segment in reversed(_layer_segments(module_path)):
        if segment in declared:
            return segment
    name = Path(module_path).name
    if name.startswith("_"):
        for layer in layers:
            if layer.get("name") in ("core", "domain", "business"):
                return layer["name"]
    return layers[-1]["name"]


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
    _guessed: list[str] = []
    _layer_names = {str(_l.get("name")) for _l in sab["layers"] if _l.get("name")}
    for module_path in added:
        layer_name = _heuristic_layer_choice(sab, module_path)
        if not (set(_layer_segments(module_path)) & _layer_names):
            _guessed.append(f"{module_path} -> {layer_name}")
        by_layer.setdefault(layer_name, []).append(module_path)

    # Round 26: name the guesses. A module whose own path names no declared layer
    # lands wherever the heuristic put it, and taskq-plus is the evidence that a
    # silent placement can leave SAB.json contradicting the project's own
    # .importlinter layering. Printing is the whole remedy — the operator can then
    # re-run scripts/generate_sab.py from SAD.md, which is authoritative.
    if _guessed:
        print(f"[amend-sab] {len(_guessed)} module(s) placed by fallback heuristic "
              f"(their path names no declared layer) — verify against SAD.md §2:")
        for line in _guessed:
            print(f"  ? {line}")

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
    except Exception as exc:
        print(f"[WARN] _safe_load: could not read {path}: {exc}")
        return {}