"""Lint tests: CLI-layer dependency direction and the `_hc.` debt ratchet.

History: 方案六 extracted every cmd_* family from harness_cli.py into cli/,
but kept dependencies late-bound through `import harness_cli as _hc` so
existing monkeypatches on harness_cli attributes kept working. That left
409 `_hc.` references (mostly stdlib laundering: `_hc.Path`, `_hc.json`, …)
which force harness_cli.py to define helpers before its mid-file
`from cli.X import` blocks — the root cause of the 16 `noqa: E402` imports
and the `sys.modules.setdefault("harness_cli", …)` script-mode hack.

The strangler continuation removes those references stage by stage. These
tests make the direction one-way:

  * test_hc_ref_ratchet — per-file `_hc.` counts may only go DOWN. A parallel
    session adding new `_hc.` borrowing goes red immediately.
  * test_core_never_imports_cli — core/ and harness/ must never import the
    CLI layer (upgrades the docstring rule in cli/__init__.py to a gate).

cli/cr_cmds.py is the end-state exemplar: direct stdlib imports, zero `_hc.`.
"""

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# Ratchet snapshot: maximum allowed `_hc.` occurrences per cli/ module.
# Lower a value in the same commit that removes references; never raise one.
# When every value reaches 0, S5 deletes `import harness_cli as _hc` itself
# and this snapshot becomes the permanent all-zero architecture gate.
_HC_REF_CEILING = {
    "__init__.py": 0,
    "check_cmds.py": 2,    # S4b (was 95 → S1 8)
    "cr_cmds.py": 0,
    "fr_cmds.py": 31,      # S3 (was 73 → S1 33)
    "gate_cmds.py": 5,     # S4b (was 53 → S1 8)
    "phase_cmds.py": 12,   # S1 (was 81)
    "project_cmds.py": 4,  # S4a (was 68 → S1 15 → S2 14)
    "push_cmds.py": 6,     # S4b (was 39 → S1 7)
}

_HC_REF = re.compile(r"\b_hc\.")

# Import statements resolving the CLI layer (module path position only, so
# prose mentions of "harness_cli.py" in comments/docstrings don't match).
_CLI_LAYER_IMPORT = re.compile(r"^\s*(?:from|import)\s+(?:cli|harness_cli)\b")

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
        "`_hc.` late-binding debt increased — import the real module "
        "(stdlib / core.*) directly instead of borrowing through harness_cli:\n  "
        + "\n  ".join(over + unknown)
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
