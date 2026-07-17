"""harness/toolchains/detect.py — project language / test-runner detection.

Detection happens once at init-project and is persisted into
.methodology/state.json (`language`, `test_runner`). Every later consumer
(run-gate, finalize-gate, preflight, cross-validation) reads the persisted
value via get_project_language()/get_project_test_runner() — never re-detects
— so a project cannot silently change toolchain mid-flight.

state.json without a `language` field means a pre-v2.8 project → "python".
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional, Union

from core.utils.lang_patterns import DEFAULT_LANGUAGE, project_language
from harness.toolchains.registry import DIMENSION_TOOLS

# Marker files, checked in order. tsconfig.json wins over package.json so a
# TypeScript project (which always also has package.json) detects as typescript.
_TS_MARKER = "tsconfig.json"
_JS_MARKER = "package.json"
_PY_MARKERS = ("pyproject.toml", "setup.cfg", "setup.py")


def supported_languages() -> tuple[str, ...]:
    """Languages with a registered, complete toolchain."""
    return tuple(sorted(DIMENSION_TOOLS))


def detect_language(project_root: Union[str, Path]) -> Optional[str]:
    """Detect the project language from manifest files.

    Returns None when BOTH a Python manifest and a JS/TS manifest exist —
    ambiguous, the caller must require an explicit --language flag. A project
    with no recognized manifest defaults to "python" (legacy behavior).
    """
    root = Path(project_root)
    has_py = any((root / m).exists() for m in _PY_MARKERS)
    has_ts = (root / _TS_MARKER).exists()
    has_js = (root / _JS_MARKER).exists()

    if has_py and (has_ts or has_js):
        return None  # ambiguous — require explicit --language
    if has_ts:
        return "typescript"
    if has_js:
        return "javascript"
    return DEFAULT_LANGUAGE


def detect_test_runner(project_root: Union[str, Path]) -> Optional[str]:
    """Detect vitest/jest from package.json deps and scripts.

    Returns None when neither (or both — ambiguous) is found; only meaningful
    for javascript/typescript projects.
    """
    pkg_path = Path(project_root) / _JS_MARKER
    if not pkg_path.exists():
        return None
    try:
        pkg = json.loads(pkg_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None

    deps: set[str] = set()
    for key in ("dependencies", "devDependencies"):
        section = pkg.get(key)
        if isinstance(section, dict):
            deps.update(section)
    scripts_text = " ".join(
        v for v in (pkg.get("scripts") or {}).values() if isinstance(v, str)
    )

    has_vitest = "vitest" in deps or "vitest" in scripts_text
    has_jest = "jest" in deps or "jest" in scripts_text
    if has_vitest and has_jest:
        return None  # ambiguous — require explicit flag
    if has_vitest:
        return "vitest"
    if has_jest:
        return "jest"
    return None


def _read_state(project_root: Union[str, Path]) -> dict:
    from core.state_io import load_state
    return load_state(project_root, lenient=True)


def get_project_language(project_root: Union[str, Path]) -> str:
    """Persisted project language from state.json; pre-v2.8 projects → python.

    Delegates to core.utils.lang_patterns.project_language — the single
    reader shared with core-side scanners (core must not import harness).
    """
    return project_language(project_root)


def get_project_test_runner(project_root: Union[str, Path]) -> Optional[str]:
    """Persisted test runner from state.json (None for python projects)."""
    runner = _read_state(project_root).get("test_runner")
    return runner if isinstance(runner, str) and runner else None
