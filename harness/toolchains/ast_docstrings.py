"""ast-docstrings — document-coverage scorer.

Implements the `ast-docstrings` gate in-process scorer (see
harness/toolchains/registry.py ToolSpec('ast-docstrings', in_process=True)).
Reads the same Python-AST signal the gate expects, returns a JSON
score on stdout so the framework can read it the same way it reads
every other in-process tool.

Output shape (orchestrator parses):
  {
    "score": 0..100,            # % of public callables with docstrings
    "total": int,               # public callables counted
    "documented": int,           # of those, the ones with a docstring
    "files_scanned": int,
    "missing": [str, ...]       # path:line of the undocumented ones
  }

Round 56 站1: this file was missing — `ast-docstrings` existed in the
ToolSpec registry but had no module behind it, so env-check could not
find a binary to probe and reported `ast_docstrings` missing on every
fresh project that inherited P3+. Filling that hole closes one false-
positive failure mode without changing gate semantics.
"""

import sys
import json
import ast
from pathlib import Path


def _is_public(name: str) -> bool:
    return not name.startswith("_")


def _walk_callables(tree: ast.AST):
    """Yield (lineno, name, has_docstring) for every public callable."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if not _is_public(node.name):
                continue
            ds = ast.get_docstring(node, clean=True)
            yield node.lineno, node.name, bool(ds)


def scan(root: Path) -> dict:
    root = root.resolve()
    total = 0
    documented = 0
    missing: list[str] = []
    files_scanned = 0

    for path in root.rglob("*.py"):
        # Skip venvs, build artefacts, the toolchains scanner itself.
        parts = path.parts
        if any(p in (".venv", "venv", "__pycache__", ".git", "node_modules") for p in parts):
            continue
        if path.resolve() == Path(__file__).resolve():
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        try:
            tree = ast.parse(text, filename=str(path))
        except SyntaxError:
            continue
        files_scanned += 1
        for lineno, name, has_doc in _walk_callables(tree):
            total += 1
            if has_doc:
                documented += 1
            else:
                rel = path.relative_to(root)
                missing.append(f"{rel}:{lineno}:{name}")

    score = round(100.0 * documented / total, 2) if total else 100.0
    return {
        "score": score,
        "total": total,
        "documented": documented,
        "files_scanned": files_scanned,
        "missing": missing,
    }


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python3 ast_docstrings.py <root_dir>", file=sys.stderr)
        return 1
    result = scan(Path(sys.argv[1]))
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
