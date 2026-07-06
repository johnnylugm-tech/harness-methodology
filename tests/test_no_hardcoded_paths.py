"""Lint tests: path resolution goes through ProjectLayout, and framework code
carries no target-project assumptions.

History: hand-built `project / "0X-…"` paths coexisted with ProjectLayout in
15 files, producing the path-doubling, three-way SRS.md location mismatch,
and src-layout blindness (9feafc0) bug class. And verify_spec_compliance.py
shipped for months hardcoding another project's module names
(text_processor.py / retry_handler.py / prosody_manager.py), false-positive
failing every other project (E2E round 2 HIGH finding).
"""

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# Path-arithmetic on phase directories: `... / "0X-name"`. Plain path STRINGS
# inside generated-document templates are display content, not resolution,
# and are deliberately out of scope.
_PHASE_DIR_DIVISION = re.compile(
    r'/\s*"0[1-9]-(?:requirements|architecture|development|testing|'
    r'verification|quality|risk|config|maintenance)"'
)

# Identifiers of past target projects that must never appear in framework
# code as module references.
_FOREIGN_MODULES = re.compile(r"(?:text_processor|retry_handler|prosody_manager)\.py")

_ALLOWED_PATH_FILES = {
    "core/utils/project_layout.py",  # the path SSOT itself
    "core/phase_topology.py",        # phase-dir name registry
}

_SKIP_PREFIXES = ("tests/", ".venv/", ".git/", ".sessi-work/", "node_modules/")


def _framework_sources():
    for path in sorted(REPO.rglob("*.py")):
        rel = path.relative_to(REPO).as_posix()
        if rel.startswith(_SKIP_PREFIXES):
            continue
        yield rel, path


def test_no_phase_dir_path_arithmetic_outside_layout():
    offenders = []
    for rel, path in _framework_sources():
        if rel in _ALLOWED_PATH_FILES:
            continue
        for lineno, line in enumerate(
            path.read_text(encoding="utf-8", errors="replace").splitlines(), 1
        ):
            if _PHASE_DIR_DIVISION.search(line):
                offenders.append(f"{rel}:{lineno}: {line.strip()}")
    assert not offenders, (
        "Hand-built phase-directory paths found — use ProjectLayout "
        "(core/utils/project_layout.py) instead:\n  " + "\n  ".join(offenders)
    )


def test_no_foreign_project_module_references():
    offenders = []
    for rel, path in _framework_sources():
        for lineno, line in enumerate(
            path.read_text(encoding="utf-8", errors="replace").splitlines(), 1
        ):
            if _FOREIGN_MODULES.search(line):
                offenders.append(f"{rel}:{lineno}: {line.strip()}")
    assert not offenders, (
        "Framework code references another project's modules — checks must "
        "derive their targets from the project's own SAD.md/SRS.md:\n  "
        + "\n  ".join(offenders)
    )
