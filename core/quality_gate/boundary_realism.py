"""Which declared boundaries the test suite replaced before it started
(Round 51 站3).

Round 46 站1 settled the case where a requirement's witness never ran. This is
the layer under it: the witness ran, passed, and was a stand-in.

An `autouse=True` fixture is not an ordinary test double. It applies to every
test in its module (or, in `conftest.py`, every test in the directory) without
any test asking for it, so no test in that scope can observe the real thing.
When its target is a module the project's own `SAB.json` lists under
`high_risk_modules`, the architecture has named that module the risky one and
the suite has arranged for nothing to touch it.

Measured 2026-08-14 across the six projects on this machine:

    taskq                  high_risk=2   findings=0
    taskq-plus             high_risk=3   findings=0
    taskq-renew            high_risk=3   findings=0
    taskq-advance          high_risk=4   findings=0
    taskq-api              high_risk=4   findings=17
    run-all-by-workflow    high_risk=2   findings=0

taskq-advance is the control: same SPEC.md, same language, the same four
high-risk modules, zero findings. Its conftest says so in the first paragraph
— "Responsibilities kept deliberately narrow so that a missing implementation
surfaces as a normal ModuleNotFoundError" — and hands tests a real SQLite file
through an opt-in fixture. taskq-api's replaces
`repository.session.get_session` and `service.auth.verify_key` in seven test
modules and in both files named `*_e2e.py`, which are the files carrying the
NFR-10 integration evidence and the T-02/T-03 threat verification.

This is a report, not a prohibition. Patching a boundary is how unit tests
work and this module has no opinion about a function-scoped fixture. What it
refuses is the silent version: a dimension scored 100.0 over a suite that
replaced the module under test, with nothing in the artifact saying so.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path

__all__ = ["stubbed_boundaries"]

# The three ways a fixture takes a module out of the test's reach.
_PATCH_ATTRS = frozenset({"setattr", "delattr", "setitem", "delitem"})


def _high_risk_modules(project: Path) -> list[str]:
    sab = project / ".methodology" / "SAB.json"
    if not sab.is_file():
        return []
    try:
        data = json.loads(sab.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, dict):
        return []
    return [str(m) for m in (data.get("high_risk_modules") or [])]


def _is_autouse_fixture(fn: "ast.FunctionDef | ast.AsyncFunctionDef") -> bool:
    for dec in fn.decorator_list:
        if not isinstance(dec, ast.Call):
            continue
        for kw in dec.keywords:
            if (kw.arg == "autouse" and isinstance(kw.value, ast.Constant)
                    and kw.value.value is True):
                return True
    return False


def _module_aliases(nodes) -> dict[str, str]:
    """Local name -> dotted module, for `import x.y as z` / `from x import y as z`.

    Both spellings are in the corpus. taskq-api's fixtures use the second
    (`from taskq_api.repository import session as _session`) and resolving it
    is the difference between naming the module and naming a local variable.
    """
    aliases: dict[str, str] = {}
    for node in nodes:
        if isinstance(node, ast.Import):
            for a in node.names:
                aliases[a.asname or a.name.split(".")[0]] = a.name
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            for a in node.names:
                aliases[a.asname or a.name] = f"{node.module}.{a.name}"
    return aliases


def _patched_modules(fn, aliases: dict[str, str]) -> set[str]:
    """Dotted modules this fixture body patches, as far as the source says."""
    local = dict(aliases)
    local.update(_module_aliases(ast.walk(fn)))

    hit: set[str] = set()
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        # monkeypatch.setattr(module, "name", ...) / mp.setattr(...)
        if isinstance(func, ast.Attribute) and func.attr in _PATCH_ATTRS:
            if node.args:
                first = node.args[0]
                if isinstance(first, ast.Name) and first.id in local:
                    hit.add(local[first.id])
                elif isinstance(first, ast.Constant) and isinstance(first.value, str):
                    # setattr("pkg.mod.attr", ...) — the module is the head.
                    hit.add(first.value.rsplit(".", 1)[0])
            continue
        # patch("pkg.mod.attr") / patch.object(module, "name")
        name = (func.attr if isinstance(func, ast.Attribute)
                else func.id if isinstance(func, ast.Name) else "")
        if name not in {"patch", "object"}:
            continue
        if not node.args:
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            hit.add(first.value.rsplit(".", 1)[0])
        elif isinstance(first, ast.Name) and first.id in local:
            hit.add(local[first.id])
    return hit


def stubbed_boundaries(project: "str | Path") -> list[dict]:
    """Autouse fixtures that replace a SAB-declared high-risk module.

    Each row is ``{"module", "fixture", "file"}`` — a finding that cannot be
    acted on is a finding nobody acts on (Round 48). Sorted so two runs over
    the same tree produce the same list.

    Returns [] when the project declares no high-risk modules: with an empty
    input set every scan is vacuously clean, and Round 46's rule is that a
    guard whose input set is empty has not passed, it has abstained. The
    caller distinguishes the two by asking the SAB, not by reading [] as
    good news.
    """
    project = Path(project)
    targets = _high_risk_modules(project)
    if not targets:
        return []
    target_set = set(targets)

    rows: list[dict] = []
    for path in sorted(project.rglob("*.py")):
        parts = set(path.parts)
        if ".venv" in parts or "harness" in parts or "site-packages" in parts:
            continue
        if not (path.name == "conftest.py" or path.name.startswith("test_")):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        module_aliases = _module_aliases(tree.body)
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not _is_autouse_fixture(fn):
                continue
            for module in sorted(_patched_modules(fn, module_aliases) & target_set):
                rows.append({
                    "module": module,
                    "fixture": fn.name,
                    "file": path.relative_to(project).as_posix(),
                })
    return sorted(rows, key=lambda r: (r["file"], r["fixture"], r["module"]))
