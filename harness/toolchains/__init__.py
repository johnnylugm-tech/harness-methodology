"""harness/toolchains — language-aware tool resolution for gate scoring.

Public API:
  resolve_tool_id(dimension, language, yaml_tool=None, test_runner=None)
  get_tool_spec(tool_id) -> ToolSpec | None
  detect_language(project_root) / detect_test_runner(project_root)
  get_project_language(project_root) / get_project_test_runner(project_root)
  supported_languages()
  bootstrap                 (submodule) how each tool GETS installed — the
                            counterpart to ToolSpec.check_cmd's "is it here?"

See registry.py for the resolution contract and the R8 completeness invariant.
"""

from harness.toolchains import bootstrap  # noqa: F401  (submodule re-export)
from harness.toolchains.detect import (
    DEFAULT_LANGUAGE,
    detect_language,
    detect_test_runner,
    get_project_language,
    get_project_test_runner,
    supported_languages,
)
from harness.toolchains.registry import (
    DIMENSION_TOOLS,
    TOOL_SPECS,
    ToolSpec,
    get_tool_spec,
    resolve_tool_id,
)

__all__ = [
    "bootstrap",
    "DEFAULT_LANGUAGE",
    "DIMENSION_TOOLS",
    "TOOL_SPECS",
    "ToolSpec",
    "detect_language",
    "detect_test_runner",
    "get_project_language",
    "get_project_test_runner",
    "get_tool_spec",
    "resolve_tool_id",
    "supported_languages",
]
