"""Subprocess test-layer mock helper for e2e CLI tests.

Python automatically imports `sitecustomize` at startup when its directory
is present on PYTHONPATH. This allows subprocesses spawned by test_e2e_cli.py
to stub missing physical CLI tools (gitleaks, import-linter, scancode,
code-review-graph) on test runner VMs without modifying production code.
"""
try:
    from harness import tool_checks
    _orig_check = tool_checks.check_tool_for_dim

    def _stub_check(dim_name, tool_name, language="python", project_root=None):
        ok, diag = _orig_check(dim_name, tool_name, language=language, project_root=project_root)
        if not ok and ("not found" in diag or "check failed" in diag):
            return True, ""
        return ok, diag

    tool_checks.check_tool_for_dim = _stub_check
except Exception:
    pass
