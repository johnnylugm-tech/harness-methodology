import ast
from pathlib import Path
from typing import Any, Optional

def extract_minimal_viable_context(
    file_path: Path,
    error_line: Optional[int],
    crg_bridge: Any = None,
    project_root: Optional[Path] = None
) -> tuple[str, Optional[str]]:
    """
    Construct the Minimal Viable Context (MVC) for a given file and error line.
    Returns (mvc_text, allowed_node_name).
    Uses AST segment slicing to extract the affected function/class,
    and optionally integrates with Code Review Graph (CRG) to include dependencies.
    """
    if not file_path.exists() or file_path.suffix != ".py":
        return "", None

    content = file_path.read_text(encoding="utf-8")
    if not error_line:
        # If no error line is specified, fall back to returning the whole file
        return content, None

    try:
        tree = ast.parse(content)
    except Exception:
        return content, None

    target_node = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.ClassDef, ast.AsyncFunctionDef)):
            start = node.lineno
            end = getattr(node, "end_lineno", start)
            if not hasattr(node, "end_lineno"):
                max_line = start
                for child in ast.walk(node):
                    if hasattr(child, "lineno"):
                        max_line = max(max_line, child.lineno)  # type: ignore[reportAttributeAccessIssue]
                end = max_line
            
            if start <= int(error_line) <= end:
                target_node = node
                break

    if not target_node:
        return content, None

    lines = content.splitlines()
    start_line = max(0, target_node.lineno - 1)
    end_line = getattr(target_node, "end_lineno", len(lines))
    sliced_content = "\n".join(lines[start_line:end_line])
    
    mvc_text = f"--- Sliced Context from {file_path.name} (Lines {start_line+1}-{end_line}) ---\n"
    mvc_text += sliced_content + "\n"

    # Deep integration with Code Review Graph (CRG)
    if crg_bridge and project_root:
        try:
            # We use the dimension 'auto_fix' to retrieve related context
            crg_data = crg_bridge.get_minimal_context(str(project_root), "auto_fix")
            if crg_data:
                mvc_text += "\n--- CRG Minimal Context (Dependencies & Rules) ---\n"
                import json
                mvc_text += json.dumps(crg_data, indent=2) + "\n"
        except Exception:
            pass

    return mvc_text, target_node.name
