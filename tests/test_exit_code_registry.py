"""Round 13 站0 — every exit code cli/*.py or harness_cli.py can return must
be registered in cli/exit_codes.py, and harness_cli.py's docstring "Exit
codes" section must match that registry exactly.

The drift this guard closes: 8 codes (12/14/17/18/19/20/22/24) were already
in live use across cli/phase_cmds.py and cli/fr_cmds.py before this round,
with zero entry in harness_cli.py's docstring — a human or agent reading
the documented contract had no way to know what those codes meant.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from cli.exit_codes import REGISTRY

REPO = Path(__file__).resolve().parent.parent
_SCAN_FILES = sorted((REPO / "cli").glob("*.py")) + [REPO / "harness_cli.py"]


def _module_int_constants(tree: ast.Module) -> dict[str, int]:
    """Module-level ``NAME = <int literal>`` assignments (e.g.
    ``DISPATCH_STRUCTURALLY_BROKEN_EXIT_CODE = 23`` in cli/fr_cmds.py) —
    a ``return NAME`` site resolves through this table."""
    consts: dict[str, int] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if (
                isinstance(target, ast.Name)
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, int)
                and not isinstance(node.value.value, bool)
            ):
                consts[target.id] = node.value.value
    return consts


def _returned_exit_codes(path: Path) -> set[int]:
    """Every int this file's ``return`` / ``sys.exit()`` sites can produce —
    literal constants, or ``return NAME`` where NAME is a module-level int
    constant. (This module's own docstring mentions "sys.exit()" prose and
    cli/fr_cmds.py's dispatch prompt mentions "sys.exit()" as English text —
    neither is an actual exit call, and this scan only walks Return/Call
    nodes so plain string content can't produce a false hit.)"""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    consts = _module_int_constants(tree)
    codes: set[int] = set()
    for node in ast.walk(tree):
        value_node = None
        if isinstance(node, ast.Return) and node.value is not None:
            value_node = node.value
        elif isinstance(node, ast.Call) and node.args:
            func = node.func
            is_exit_call = (isinstance(func, ast.Attribute) and func.attr == "exit") or (
                isinstance(func, ast.Name) and func.id == "exit"
            )
            if is_exit_call:
                value_node = node.args[0]
        if value_node is None:
            continue
        if (
            isinstance(value_node, ast.Constant)
            and isinstance(value_node.value, int)
            and not isinstance(value_node.value, bool)
        ):
            codes.add(value_node.value)
        elif isinstance(value_node, ast.Name) and value_node.id in consts:
            codes.add(consts[value_node.id])
    return codes


def test_every_returned_exit_code_is_registered():
    unregistered: list[str] = []
    for path in _SCAN_FILES:
        for code in sorted(_returned_exit_codes(path)):
            if code not in REGISTRY:
                unregistered.append(f"{path.relative_to(REPO).as_posix()}: exit code {code}")
    assert not unregistered, (
        "exit code(s) returned by cli/*.py or harness_cli.py but missing from "
        "cli/exit_codes.py's REGISTRY — add a REGISTRY entry (and a docstring "
        "line in harness_cli.py's 'Exit codes:' section):\n  "
        + "\n  ".join(unregistered)
    )


def test_harness_cli_docstring_matches_registry():
    """The module docstring's ``Exit codes:`` section must list exactly the
    codes in REGISTRY (order-independent) — this parity is the guard
    against the exact drift this round found."""
    doc = (REPO / "harness_cli.py").read_text(encoding="utf-8")
    m = re.search(r"Exit codes\b(.*?)\n\"\"\"", doc, re.DOTALL)
    assert m, "harness_cli.py's module docstring must have an 'Exit codes' section"
    section = m.group(1)
    documented = {int(n) for n in re.findall(r"^\s*(\d+)\s", section, re.MULTILINE)}
    registered = set(REGISTRY)
    missing_from_doc = registered - documented
    extra_in_doc = documented - registered
    assert not missing_from_doc, (
        f"REGISTRY code(s) missing from harness_cli.py's docstring: {sorted(missing_from_doc)}"
    )
    assert not extra_in_doc, (
        f"harness_cli.py docstring lists code(s) not in REGISTRY: {sorted(extra_in_doc)}"
    )


def test_registry_has_no_duplicate_semantics_silently():
    """Sanity: REGISTRY values are non-empty strings (a blank description
    would defeat the whole point of the table)."""
    for code, desc in REGISTRY.items():
        assert isinstance(desc, str) and desc.strip(), f"exit code {code} has an empty description"


def test_scanner_resolves_named_constant_returns():
    """Negative-space check: the AST scanner must resolve `return NAME`
    through a module-level int constant, not just literal `return 23`
    (this is exactly cli/fr_cmds.py's DISPATCH_STRUCTURALLY_BROKEN_EXIT_CODE
    shape) — otherwise the registry could silently drift again without the
    guard noticing."""
    import tempfile

    src = (
        "MY_CODE = 42\n"
        "def f():\n"
        "    return MY_CODE\n"
    )
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as fh:
        fh.write(src)
        path = Path(fh.name)
    try:
        assert _returned_exit_codes(path) == {42}
    finally:
        path.unlink()
