"""Regression tests: Bug fixes P6-2026-07-07 (cwd-relative import drift + CRG kwarg filter).

Two qualitative failures surfaced during the Phase 6 E2E round (memory note
p6-quality-2026-07-07). Both were discovered in production, both can be
reproduced locally, and both are routed via absolute paths or deferred
introspection so they survive any future cwd.

Bug A: `harness_cli.py` (the `finalize-gate --gate 4` block) used
  `from scripts.generate_quality_report import generate_quality_report`.
  The helper module lives in `<harness_repo>/scripts/`, not the consumer
  project's working directory, so the import silently fell into the except
  branch and emitted a [WARN] instead of generating QUALITY_REPORT.md.
  Operators had to invoke `python scripts/generate_quality_report.py
  --project .` manually before advance-phase's C10 check would unblock.

Bug B: `crg_tool_runner.py` re-emitted kwargs verbatim into a CRG tool that
  had dropped named args across versions (e.g. `get_hub_nodes_func` no
  longer accepts `min_fan_in`). The bridge filtered kwargs upstream in
  `crg_bridge.py:_call_crg`, but only when the bound function did NOT
  take `**kwargs`. `crg_api.make_tool` returns a `_tool(repo_root=None,
  **kwargs)` wrapper, so every fallback-path call ended up raising
  `got an unexpected keyword argument` -> graceful `{}` -> CRG hub-penalty
  silently lost on every Gate 4 evaluation running outside a Claude Code
  session (i.e. most CI).

Both fixes load / introspect by absolute path; the design intent is that
the helper location is *discoverable from this file*, not from cwd.
"""

from __future__ import annotations

import importlib.util
import inspect
import subprocess
import sys
from pathlib import Path


HARNESS_REPO = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = HARNESS_REPO / "scripts"
CRG_RUNNER = HARNESS_REPO / "harness" / "ssi" / "scripts" / "crg_tool_runner.py"


def _load_quality_report():
    """Replicate the in-harness_cli.py helper: load by absolute file path."""
    spec = importlib.util.spec_from_file_location(
        "_test_gate4_quality_report", SCRIPTS_DIR / "generate_quality_report.py",
    )
    assert spec is not None and spec.loader is not None, "spec must load"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_release_notes():
    spec = importlib.util.spec_from_file_location(
        "_test_gate4_release_notes", SCRIPTS_DIR / "generate_release_notes.py",
    )
    assert spec is not None and spec.loader is not None, "spec must load"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestBugA_CwdRelativeImportDrift:
    """The original `from scripts.X` path was process-cwd-dependent. The fix
    loads by absolute file path so a chdir()-style project invocation can't
    silently skip generation."""

    def test_load_generate_quality_report_by_absolute_path_succeeds(self):
        mod = _load_quality_report()
        assert hasattr(mod, "generate_quality_report"), \
            "module must expose generate_quality_report"

    def test_load_generate_release_notes_by_absolute_path_succeeds(self):
        mod = _load_release_notes()
        assert hasattr(mod, "generate_release_notes"), \
            "module must expose generate_release_notes"

    def test_absolute_path_loader_is_independent_of_cwd(self):
        """Running the loader from /tmp (cwd far from scripts/) must still
        find the helper. Pre-fix would have raised ModuleNotFoundError."""
        scripts = str(SCRIPTS_DIR)
        # Build the test snippet with interpolation so the sub-process
        # inherits the absolute path verbatim and is hermetic.
        snippet = (
            "import importlib.util, pathlib; "
            f"spec = importlib.util.spec_from_file_location("
            f"'m', pathlib.Path('{scripts}/generate_quality_report.py')); "
            "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); "
            "print('OK' if hasattr(m, 'generate_quality_report') else 'NO')"
        )
        result = subprocess.run(
            [sys.executable, "-c", snippet],
            cwd="/tmp", capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, \
            f"stdout={result.stdout!r} stderr={result.stderr!r}"
        assert result.stdout.strip() == "OK", \
            f"absolute-path loader must work from /tmp cwd, got: {result.stdout!r}"


class TestBugB_CrgToolRunnerKwargsFilter:
    """The CRG subprocess runner must drop unknown kwargs before calling a
    tool, otherwise a forward-incompatible CRG version that drops named args
    crashes with `got an unexpected keyword argument` and the bridge falls
    back to `{}` silently - losing CRG hub-penalty on every Gate 4."""

    def test_kwargs_filter_helper_drops_unknown_kwargs(self):
        """Reproduce the filter logic inline; assert it preserves supported
        kwargs and drops unsupported ones."""
        def fake_tool(repo_root, limit=50):
            return {"ok": True, "limit": limit, "repo_root": repo_root}

        sig = inspect.signature(fake_tool)
        accepted = set(sig.parameters.keys())
        kwargs = {"repo_root": "/x", "limit": 7, "min_fan_in": 5, "ancient": 1}
        filtered = {k: v for k, v in kwargs.items() if k in accepted}
        # repo_root + limit kept; min_fan_in + ancient dropped
        assert filtered == {"repo_root": "/x", "limit": 7}
        # ...and the tool itself accepts the filtered bag
        assert fake_tool(**filtered)["limit"] == 7

    def test_crg_runner_file_filters_kwargs_before_calling_tool(self):
        """Source-level regression anchor: the runner source must introspect
        the tool's signature and DROP unsupported kwargs BEFORE the
        `fn(repo_root=..., **kwargs)` call. Without this ordering, the bridge
        downstream swallows `got an unexpected keyword argument` and returns
        {} silently, losing CRG hub-penalty on every Gate 4 outside Claude Code.
        """
        src = CRG_RUNNER.read_text(encoding="utf-8")
        assert "inspect.signature" in src, \
            "crg_tool_runner.py must introspect the tool's signature"
        assert "VAR_KEYWORD" in src, \
            "filter must distinguish **{}-style wrappers from real signatures"
        # Filter MUST run before the tool call — order is the regression anchor.
        filter_idx = src.find("accepted = set(_sig")
        call_idx = src.find("fn(repo_root=repo_root, **kwargs)")
        assert filter_idx > 0, "filter 'accepted' line must exist"
        assert call_idx > 0, "tool call must exist"
        assert filter_idx < call_idx, \
            "filter MUST run before the fn() call (filter ordering regression)"


class TestRegressionAnchor:
    """Both fixes carry inline docstrings dated 2026-07-07 explaining WHY
    the helper path exists. Those strings are the design contract — keeping
    the literal `Bug fix P6-2026-07-07` substring makes a revert obvious in
    the diff."""

    def test_design_intent_bug_a_inline_docstring_present(self):
        cli_src = (HARNESS_REPO / "harness_cli.py").read_text(encoding="utf-8")
        assert "Bug fix P6-2026-07-07" in cli_src
        assert "cwd-relative" in cli_src, \
            "must name the failure mode so a future reader recognises it"

    def test_design_intent_bug_b_inline_docstring_present(self):
        runner_src = CRG_RUNNER.read_text(encoding="utf-8")
        assert "Bug fix P6-2026-07-07" in runner_src
        assert "min_fan_in" in runner_src, \
            "the named-arg example is the WHY this filter exists"
