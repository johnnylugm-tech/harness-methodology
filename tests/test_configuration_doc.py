"""Round 9 站4 — docs/CONFIGURATION.md is the operator-facing registry mirror.

The audit's P3 finding was a configuration surface with zero user docs; the
P1 finding was tunables sprouting new homes because none was designated.
Both fixes rot without enforcement, so two completeness meta-tests (the
preflight/postflight/push-path registry pattern):

  A. every key in the harness_config registries (_DEFAULTS, _VALUE_DEFAULTS,
     the crg_* top-level pair) appears in CONFIGURATION.md — adding a knob
     without documenting it is a red build;
  B. every env var the framework reads (AST scan for os.environ.get /
     os.getenv literals across production packages) appears in
     CONFIGURATION.md — an unregistered env var was exactly how the zombie
     HARNESS_CLAUDE_MODEL / SSI_ROOT / DRIFT_PROJECT_PATH rows rotted in
     INTEGRATION.md (station 0).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from core.harness_config import _DEFAULTS, _VALUE_DEFAULTS

pytestmark = [pytest.mark.core]

REPO = Path(__file__).resolve().parent.parent
DOC = REPO / "docs" / "CONFIGURATION.md"

_PRODUCTION_BASES = ("cli", "core", "harness", "scripts", "detection")


def test_configuration_doc_exists():
    assert DOC.is_file(), "docs/CONFIGURATION.md is the configuration SSOT mirror"


def test_every_registry_key_is_documented():
    doc = DOC.read_text(encoding="utf-8")
    registry_keys = (
        set(_DEFAULTS) | set(_VALUE_DEFAULTS) | {"crg_cohesion_healthy", "crg_excludes"}
    )
    missing = sorted(k for k in registry_keys if f"`{k}`" not in doc)
    assert not missing, (
        f"config keys missing from docs/CONFIGURATION.md: {missing} — "
        f"a knob nobody can discover is half a zombie"
    )


def _env_keys_read_by_production_code() -> dict[str, list[str]]:
    keys: dict[str, list[str]] = {}
    for base in _PRODUCTION_BASES:
        root = REPO / base
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                f = node.func
                is_environ_get = (
                    isinstance(f, ast.Attribute) and f.attr == "get"
                    and isinstance(f.value, ast.Attribute)
                    and f.value.attr == "environ"
                )
                is_getenv = isinstance(f, ast.Attribute) and f.attr == "getenv"
                if ((is_environ_get or is_getenv)
                        and node.args and isinstance(node.args[0], ast.Constant)
                        and isinstance(node.args[0].value, str)):
                    rel = f"{path.relative_to(REPO).as_posix()}:{node.lineno}"
                    keys.setdefault(node.args[0].value, []).append(rel)
    return keys


def test_every_env_var_read_is_documented():
    doc = DOC.read_text(encoding="utf-8")
    undocumented = {
        k: sites for k, sites in _env_keys_read_by_production_code().items()
        if f"`{k}`" not in doc
    }
    assert not undocumented, (
        f"env vars read by production code but absent from "
        f"docs/CONFIGURATION.md: {undocumented} — register the switch or "
        f"don't read it"
    )


def test_env_scanner_actually_sees_known_reads():
    """Negative control: the AST scanner must find the known HARNESS_NO_GIT
    reads — an empty scan result would green-light anything."""
    keys = _env_keys_read_by_production_code()
    assert "HARNESS_NO_GIT" in keys and len(keys["HARNESS_NO_GIT"]) >= 2


# ══════════════════════════════════════════════════════════════════════════════
# Round 36 — the row exists is not the row is true
# ══════════════════════════════════════════════════════════════════════════════
#
# Check A above asks whether a key HAS a row. It never reads the Default
# column. 47ec3fd flipped _DEFAULTS["mutation_testing"] False -> True and
# left the doc saying `false`; both checks stayed green for three days while
# the operator-facing registry stated the opposite of the loader.

_DOC_ROW = re.compile(r"^\|\s*`([a-z_0-9]+)`\s*\|\s*(.*?)\s*\|")


def _documented_defaults() -> dict[str, str]:
    """key -> the Default cell, stripped of markdown emphasis and backticks."""
    found: dict[str, str] = {}
    for line in DOC.read_text(encoding="utf-8").splitlines():
        m = _DOC_ROW.match(line)
        if m and m.group(1) not in found:
            found[m.group(1)] = m.group(2).replace("*", "").strip().strip("`")
    return found


def _as_doc_literal(value: object) -> str:
    """Render a registry default the way a JSON-flavoured doc cell spells it."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return f'"{value}"'
    return str(value)


def test_every_registry_default_matches_the_doc():
    """The Default column is a statement about _DEFAULTS / _VALUE_DEFAULTS —
    it has to agree with them.

    Scope is those two registries: the crg_* top-level pair documents its
    default in prose ("unset (scorer default 0.3)") because the value lives
    in the scorer, not in a registry dict, so there is nothing here to
    compare it against.
    """
    rows = _documented_defaults()
    wrong = {
        key: (_as_doc_literal(value), rows.get(key, "<no table row>"))
        for key, value in {**_DEFAULTS, **_VALUE_DEFAULTS}.items()
        if rows.get(key) != _as_doc_literal(value)
    }
    assert not wrong, (
        f"docs/CONFIGURATION.md Default column disagrees with the registry "
        f"{{key: (code, doc)}}: {wrong} — the loader is authoritative; fix "
        f"the doc, or fix the registry if the doc is what you meant"
    )


def test_doc_row_scanner_actually_parses_the_tables():
    """Negative control for the regex: a parse that silently returns nothing
    would make the check above pass for any doc, including an empty one."""
    rows = _documented_defaults()
    assert len(rows) >= 10, f"only parsed {len(rows)} rows out of CONFIGURATION.md"
    assert rows.get("permission_mode") == '"bypassPermissions"'
