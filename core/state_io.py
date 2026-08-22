"""Round 14 站2: single entry point for reading .methodology/state.json and
quality_manifest.json.

Before this module, ~60 read sites across cli/core/harness disagreed on
what "the file is unreadable" means: some let json.JSONDecodeError raise
uncaught (which the Round 13 crash boundary then classifies as a
[HARNESS-BUG] — the WRONG classification, since a corrupt PROJECT file is
not a bug in harness's own code), some silently `except: pass` and return
{} (a defensible degrade, but with no record it happened), some
`except Exception` fail-open more broadly still. Two named choices
replace all three shapes: strict (raise StateCorruptError — the caller
decides what "the project's state is corrupt" means for that call site)
or lenient (degrade to {} with a degradation-ledger entry).
"""

from __future__ import annotations

import json
from pathlib import Path

from core.degradation_ledger import record_degradation
from core.utils.project_layout import ProjectLayout


class StateCorruptError(Exception):
    """.methodology/state.json or quality_manifest.json exists but isn't
    readable/parseable JSON. This is project data corruption, not a
    harness-methodology bug — harness_cli.py's _dispatch() catches this
    class specifically and reports [FATAL] exit 26, never routing it
    through the [HARNESS-BUG] crash boundary."""

    def __init__(self, path: Path, original: Exception):
        self.path = path
        self.original = original
        super().__init__(
            f"{path} exists but could not be read as JSON: {original}. "
            f"Fix: git restore {path} if it's tracked, or inspect/repair "
            f"it by hand, then re-run. harness_cli.py doctor may help "
            f"diagnose further."
        )


def _load_json_object(path: Path, *, lenient: bool, project: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"expected a JSON object, got {type(data).__name__}")
    except (json.JSONDecodeError, OSError, ValueError) as exc:
        if not lenient:
            raise StateCorruptError(path, exc) from exc
        record_degradation(project, "state-io", f"{path.name} unreadable — treated as empty", why=str(exc), owner="project")
        return {}
    return data


def load_state(project: "str | Path", *, lenient: bool = False) -> dict:
    """Read .methodology/state.json. A missing file (no state written yet
    — the common case at early phases) returns {}. A corrupt file raises
    StateCorruptError unless lenient=True, in which case it degrades to
    {} and records why on the degradation ledger."""
    project = Path(project)
    return _load_json_object(ProjectLayout(project).state_json_path, lenient=lenient, project=project)


def load_quality_manifest(project: "str | Path", *, lenient: bool = False) -> dict:
    """Read .methodology/quality_manifest.json. Same missing/corrupt rules
    as load_state."""
    project = Path(project)
    return _load_json_object(ProjectLayout(project).quality_manifest_path, lenient=lenient, project=project)


def sync_missing_fr_traceability(project: "str | Path", fr_id: str, manifest: dict) -> dict:
    """Backfill one `fr_module_traceability[fr_id]` entry from SAB.json into
    quality_manifest.json, additively, if the manifest lacks it and SAB.json
    has it. Returns the (possibly updated) manifest dict.

    `quality_manifest.json`'s `fr_module_traceability` is written once, at
    Phase-2 exit, from the SAB.json read at that moment (harness_bridge.py
    `generate_quality_manifest`, `force=False`, never regenerated after). An
    FR whose concrete module path is only decided during Phase 3 — the
    documented "framework-owned, path decided at P3" placeholder pattern
    (SRS.md §7 / TRACEABILITY_MATRIX.md, e.g. FR-99) — gets its module
    recorded into SAB.json's `fr_module_traceability` after that snapshot,
    so the manifest's copy silently falls behind for exactly that FR. Every
    per-FR coverage-scope reader here (`_fr_module_paths`, `fr_coverage_record`,
    `_gate1_per_fr_coverage_verdict`) reads ONLY the manifest, so a scope
    that genuinely exists in SAB.json is reported as unresolvable
    (`None`) and the FR is scored against the whole project instead of its
    own module.

    This does NOT touch the "cannot resolve scope at all" behavior any of
    those readers document (Round 46: score the harsher whole-project
    number rather than abstain) — it only fires when SAB.json actually HAS
    an entry the manifest doesn't, i.e. only for genuinely resolvable
    scope the manifest is simply out of date on. Never overwrites or
    removes an existing manifest key (the curated, Phase-2 baseline for
    every other FR is untouched); never raises (a missing/corrupt SAB.json
    is "sync unavailable", same as an unresolvable scope today, matching
    the defensive-read pattern already used for SAB.json in
    `boundary_realism.py`/`required_artifacts.py`).
    """
    existing = manifest.get("fr_module_traceability")
    if isinstance(existing, dict) and fr_id in existing:
        return manifest  # already present — no-op, never overrides

    sab_path = ProjectLayout(Path(project)).methodology_dir / "SAB.json"
    if not sab_path.is_file():
        return manifest
    try:
        sab = json.loads(sab_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return manifest
    if not isinstance(sab, dict):
        return manifest
    sab_entry = sab.get("fr_module_traceability", {}).get(fr_id) if isinstance(
        sab.get("fr_module_traceability"), dict) else None
    if not sab_entry:
        return manifest

    from core.atomic_io import atomic_write_json, file_lock, state_lock_path

    manifest_path = ProjectLayout(Path(project)).quality_manifest_path
    try:
        with file_lock(state_lock_path(Path(project))):
            # Re-read under the lock: another process may have raced us
            # (or already performed this exact backfill) since the caller's
            # copy was loaded.
            on_disk = _load_json_object(manifest_path, lenient=True, project=Path(project))
            trace = on_disk.setdefault("fr_module_traceability", {})
            if fr_id not in trace:
                trace[fr_id] = sab_entry
                atomic_write_json(manifest_path, on_disk)
                record_degradation(
                    project, "state-io.sync_missing_fr_traceability",
                    f"quality_manifest.json fr_module_traceability missing "
                    f"'{fr_id}' — backfilled from SAB.json ({sab_entry!r})",
                    why="quality_manifest.json's fr_module_traceability is a "
                        "Phase-2-exit snapshot; SAB.json is amended later in "
                        "Phase 3 for framework-owned/placeholder FRs whose "
                        "module path is only decided post-P2",
                    owner="harness",
                )
    except OSError:
        return manifest

    updated = dict(manifest)
    updated_trace = dict(existing) if isinstance(existing, dict) else {}
    updated_trace[fr_id] = sab_entry
    updated["fr_module_traceability"] = updated_trace
    return updated
