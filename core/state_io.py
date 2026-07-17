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
        record_degradation(project, "state-io", f"{path.name} unreadable — treated as empty", why=str(exc))
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
