"""harness/lang_scanners — in-process quality scanners per language.

RUNNERS maps in-process tool ids (ToolSpec.in_process=True in the toolchain
registry) to runner callables with the uniform contract
``(project_root: str) -> (json_output: str, returncode: int)``.

The Python and JS/TS runners for the same dimension emit the SAME output JSON
schema, so one scorer in harness/tool_runners.py serves both languages.
"""

from typing import Callable

from harness.lang_scanners import python_ast, treesitter_js

RUNNERS: dict[str, Callable[[str], tuple[str, int]]] = {
    # Python (ast module)
    "ast-assertions":     python_ast.run_assertions,
    "ast-error-handling": python_ast.run_error_handling,
    "ast-docstrings":     python_ast.run_docstrings,
    # JavaScript / TypeScript (tree-sitter; grammars pinned in requirements.txt)
    "js-assertions":      treesitter_js.run_assertions,
    "js-error-handling":  treesitter_js.run_error_handling,
    "js-doc-coverage":    treesitter_js.run_doc_coverage,
    "js-mi":              treesitter_js.run_mi,
}

__all__ = ["RUNNERS", "python_ast", "treesitter_js"]
