import json
from pathlib import Path
from typing import Any

def pytest_exception_interact(node: Any, call: Any, report: Any) -> None:
    """
    Pytest hook that captures runtime variable states upon test failure.
    Writes the trace to .methodology/runtime_trace.json for the AutoFixEngine to consume.
    """
    if not call.excinfo:
        return

    trace_data = []
    # Extract locals from the traceback frames
    for frameinfo in call.excinfo.traceback:
        locals_dict = {}
        for k, v in frameinfo.frame.f_locals.items():
            if k.startswith('@'):
                continue
            try:
                # Keep representation bounded to avoid massive dumps
                rep = repr(v)
                if len(rep) > 500:
                    rep = rep[:500] + "... [truncated]"
                locals_dict[k] = rep
            except Exception:
                locals_dict[k] = "<unreprable>"

        trace_data.append({
            "file": str(frameinfo.path),
            "line": frameinfo.lineno,
            "locals": locals_dict,
            "statement": str(frameinfo.statement)
        })

    out_dir = Path.cwd() / ".methodology"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "runtime_trace.json"
    
    # If the file exists, we might want to append, but usually we just want the latest trace.
    # We will just overwrite with the most recent failure.
    out_file.write_text(json.dumps({
        "test_id": node.nodeid,
        "error": str(call.excinfo.value),
        "frames": trace_data
    }, indent=2))
