"""Lint tests: CLI-layer dependency direction — the strangler end-state gates.

History: 方案六 extracted every cmd_* family from harness_cli.py into cli/,
but kept dependencies late-bound through `import harness_cli as _hc` so
existing monkeypatches on harness_cli attributes kept working. That left
409 `_hc.` references (mostly stdlib laundering: `_hc.Path`, `_hc.json`, …)
which forced harness_cli.py to define helpers before its mid-file
`from cli.X import` blocks — the root cause of the 16 `noqa: E402` mid-file
imports and the `sys.modules.setdefault("harness_cli", …)` script-mode hack.

絞殺者續章 S0-S5 removed every borrow (409 → 0), moved each helper to its
real home (cli family module / cli/_shared.py / core/*), deleted the
sys.modules hack, and consolidated harness_cli.py to a ~290-line
entrypoint + re-export facade. These tests pin that end state:

  * test_hc_ref_ratchet — `_hc.` borrowing stays at ZERO everywhere.
  * test_cli_never_imports_harness_cli — no cli/ module may import
    harness_cli again (the borrow's delivery vehicle stays dead).
  * test_core_never_imports_cli — core/ and harness/ must never import the
    CLI layer (upgrades the docstring rule in cli/__init__.py to a gate).
  * test_harness_cli_no_midfile_imports — every harness_cli import stays in
    the top import block; a module-level import after the first def is the
    circular-import dance coming back.
"""

import ast
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# All-zero since S4h — the permanent architecture gate. The per-file history
# (was-N comments) documents how much borrowing each family once had.
_HC_REF_CEILING = {
    "__init__.py": 0,
    "_shared.py": 0,
    "check_cmds.py": 0,    # was 95
    "cr_cmds.py": 0,
    "fr_cmds.py": 0,       # was 73
    "gate_cmds.py": 0,     # was 53
    "phase_cmds.py": 0,    # was 81
    "project_cmds.py": 0,  # was 68
    "push_cmds.py": 0,     # was 39
}

_HC_REF = re.compile(r"\b_hc\.")

# Import statements resolving the CLI layer (module path position only, so
# prose mentions of "harness_cli.py" in comments/docstrings don't match).
_CLI_LAYER_IMPORT = re.compile(r"^\s*(?:from|import)\s+(?:cli|harness_cli)\b")
_HARNESS_CLI_IMPORT = re.compile(r"^\s*(?:from|import)\s+harness_cli\b")

_LOWER_LAYER_DIRS = ("core", "harness")


def test_hc_ref_ratchet():
    over, unknown = [], []
    for path in sorted((REPO / "cli").glob("*.py")):
        count = len(_HC_REF.findall(path.read_text(encoding="utf-8")))
        ceiling = _HC_REF_CEILING.get(path.name)
        if ceiling is None:
            if count:
                unknown.append(f"cli/{path.name}: {count} `_hc.` refs (new module — must be 0)")
            continue
        if count > ceiling:
            over.append(f"cli/{path.name}: {count} `_hc.` refs > ceiling {ceiling}")
    assert not (over + unknown), (
        "`_hc.` late-binding debt reintroduced — import the real module "
        "(stdlib / core.* / cli._shared) directly instead of borrowing "
        "through harness_cli:\n  " + "\n  ".join(over + unknown)
    )


def test_cli_never_imports_harness_cli():
    offenders = []
    for path in sorted((REPO / "cli").glob("*.py")):
        for lineno, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), 1
        ):
            if _HARNESS_CLI_IMPORT.match(line):
                offenders.append(f"cli/{path.name}:{lineno}: {line.strip()}")
    assert not offenders, (
        "cli/ modules must not import harness_cli — dependencies live in "
        "stdlib/core/harness/cli._shared; importing the entrypoint recreates "
        "the circular-import class S5 removed:\n  " + "\n  ".join(offenders)
    )


def test_core_never_imports_cli():
    offenders = []
    for base in _LOWER_LAYER_DIRS:
        for path in sorted((REPO / base).rglob("*.py")):
            rel = path.relative_to(REPO).as_posix()
            for lineno, line in enumerate(
                path.read_text(encoding="utf-8", errors="replace").splitlines(), 1
            ):
                if _CLI_LAYER_IMPORT.match(line):
                    offenders.append(f"{rel}:{lineno}: {line.strip()}")
    assert not offenders, (
        "core/ and harness/ must never import the CLI layer "
        "(cli/, harness_cli) — the CLI sits on top (cli/__init__.py rule):\n  "
        + "\n  ".join(offenders)
    )


def test_harness_cli_no_midfile_imports():
    """Module-level imports after the first def/class are the old mid-file
    circular-import dance — everything must stay in the top import block
    (function-local lazy imports are fine and out of scope here)."""
    src = (REPO / "harness_cli.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    first_def = next(
        (n.lineno for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))),
        None,
    )
    assert first_def is not None, "harness_cli.py lost build_parser/main?"
    offenders = [
        f"harness_cli.py:{n.lineno}"
        for n in tree.body
        if isinstance(n, (ast.Import, ast.ImportFrom)) and n.lineno > first_def
    ]
    assert not offenders, (
        "module-level import below the first def — keep every import in the "
        "top block (S5 end state):\n  " + "\n  ".join(offenders)
    )
