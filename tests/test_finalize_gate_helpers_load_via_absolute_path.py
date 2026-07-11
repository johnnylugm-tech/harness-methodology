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
        cli_src = (HARNESS_REPO / "cli" / "gate_cmds.py").read_text(encoding="utf-8")
        assert "Bug fix P6-2026-07-07" in cli_src
        assert "cwd-relative" in cli_src, \
            "must name the failure mode so a future reader recognises it"

    def test_design_intent_bug_b_inline_docstring_present(self):
        runner_src = CRG_RUNNER.read_text(encoding="utf-8")
        assert "Bug fix P6-2026-07-07" in runner_src
        assert "min_fan_in" in runner_src, \
            "the named-arg example is the WHY this filter exists"

    def test_design_intent_a1_completion_docstring_present(self):
        """A1-2026-07-07 (completion): the helper is now shared by 3 sites;
        a revert that drops the docstring makes the path-bug regression
        silently come back. The literal substring anchors the design contract.
        """
        cli_src = (HARNESS_REPO / "cli" / "gate_cmds.py").read_text(encoding="utf-8")
        assert "A1-2026-07-07" in cli_src, \
            "A1 docstring MUST persist in harness_cli.py at module scope"
        project_src = (
            HARNESS_REPO / "cli" / "project_cmds.py"
        ).read_text(encoding="utf-8")
        assert "A1-2026-07-07" in project_src, \
            "A1 docstring MUST persist in project_cmds.py at Site 3"


class TestA1_HelperPathFix:
    """A1-2026-07-07 critical regression: the original P6 inlined helper used
    `Path(__file__).resolve().parent / "scripts"` — wrong (resolves to a
    non-existent `<harness_repo>/harness/scripts/`). Tests replicated the
    path calc using `.parent.parent` and passed without invoking the real
    helper. This class invokes the REAL module-level helper from `/tmp`
    cwd to catch the path fix in production code, not just the test mirror.
    """

    def _call_real_helper_from_cwd(self, module_filename: str, cwd: str) -> subprocess.CompletedProcess:
        """Run the real `load_harness_script` defined in harness_cli.py from
        a foreign cwd. The harness repo path is propagated via env so the
        subprocess can import it."""
        harness_repo = str(HARNESS_REPO)
        snippet = (
            "import sys, json; "
            f"sys.path.insert(0, {harness_repo!r}); "
            "from harness_cli import load_harness_script; "
            f"mod = load_harness_script({module_filename!r}); "
            "attrs = [a for a in dir(mod) if not a.startswith('_')]; "
            "print(json.dumps(sorted(attrs)))"
        )
        return subprocess.run(
            [sys.executable, "-c", snippet],
            cwd=cwd, capture_output=True, text=True, timeout=30,
        )

    def test_load_harness_script_resolves_correct_scripts_dir(self):
        """Real module-level `load_harness_script('generate_quality_report.py')`
        from /tmp cwd MUST succeed — this is the regression anchor for the
        `.parent.parent` path fix."""
        result = self._call_real_helper_from_cwd(
            "generate_quality_report.py", cwd="/tmp",
        )
        assert result.returncode == 0, (
            f"real helper must succeed from /tmp cwd; "
            f"stdout={result.stdout!r} stderr={result.stderr!r}"
        )
        import json as _json
        attrs = _json.loads(result.stdout.strip())
        assert "generate_quality_report" in attrs, (
            f"module must expose generate_quality_report callable, got {attrs!r}"
        )

    def test_load_harness_script_phase_auditor_real_path(self):
        """Real helper loads `phase_auditor.py` from foreign cwd and exposes
        all three classes used by Site 2 + Site 3."""
        result = self._call_real_helper_from_cwd(
            "phase_auditor.py", cwd="/tmp",
        )
        assert result.returncode == 0, (
            f"real helper must succeed for phase_auditor.py from /tmp cwd; "
            f"stdout={result.stdout!r} stderr={result.stderr!r}"
        )
        import json as _json
        attrs = _json.loads(result.stdout.strip())
        for cls in ("PhaseAuditor", "LocalFetcher", "GitHubFetcher"):
            assert cls in attrs, (
                f"phase_auditor.py must expose {cls}, got {attrs!r}"
            )

    def test_load_harness_script_missing_module_raises_import_error(self):
        """A non-existent module name raises ImportError whose message carries
        the resolved target path — debugging affordance for the path fix."""
        sys_path = str(HARNESS_REPO)
        # Multiline snippet via heredoc-equivalent (exec on a single compile
        # unit) — Python -c cannot parse `try/except` on a single line.
        snippet = (
            "import sys\n"
            f"sys.path.insert(0, {sys_path!r})\n"
            "from harness_cli import load_harness_script\n"
            "try:\n"
            "    load_harness_script('does_not_exist_xyz.py')\n"
            "except ImportError as e:\n"
            "    print(repr(str(e)))\n"
            "else:\n"
            "    print('NO_RAISE')\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", snippet],
            cwd="/tmp", capture_output=True, text=True, timeout=30,
        )
        assert result.returncode == 0, (
            f"subprocess must finish; stderr={result.stderr!r}"
        )
        out = result.stdout.strip()
        assert out != "NO_RAISE", "missing module must raise ImportError"
        assert "does_not_exist_xyz.py" in out, (
            f"ImportError must reference the missing filename, got: {out!r}"
        )


class TestA1_ThreeSitesInvokeSameHelper:
    """A1-2026-07-07 invariant: the 3 sites that previously inline-imported
    `from scripts.X` must all call the shared `load_harness_script()`. A
    regression that re-inlines one site is caught here at the source level.
    """

    def test_exactly_one_definition_of_load_harness_script(self):
        """S4e: the single definition lives in core/utils/script_loader.py;
        harness_cli re-exports it (public compat name) and must NOT grow a
        second definition or an inlined copy."""
        loader_src = (HARNESS_REPO / "core" / "utils" / "script_loader.py").read_text(encoding="utf-8")
        assert sum(
            1 for line in loader_src.splitlines()
            if line.startswith("def load_harness_script(")
        ) == 1, "script_loader.py must hold the single definition"
        cli_src = (HARNESS_REPO / "cli" / "gate_cmds.py").read_text(encoding="utf-8")
        assert "def load_harness_script(" not in cli_src, (
            "harness_cli.py must only re-export load_harness_script, not define it"
        )
        assert "from core.utils.script_loader import load_harness_script" in cli_src, (
            "harness_cli.py must keep the public re-export"
        )

    def test_site_2_run_phase_auditor_calls_helper(self):
        """Site 2 (`_run_phase_auditor`) source must invoke
        `load_harness_script('phase_auditor.py')`, NOT a real cwd-relative
        `from scripts.phase_auditor import …` (an explanation in a docstring
        that mentions the old import name is allowed)."""
        import re
        cli_src = (HARNESS_REPO / "cli" / "_shared.py").read_text(encoding="utf-8")
        # Locate the function source region (simple text scan works because
        # the function body is contiguous).
        fn_start = cli_src.find("def _run_phase_auditor(")
        assert fn_start > 0, "_run_phase_auditor must exist"
        # Find next top-level def or end-of-file to bound the function source.
        tail = cli_src[fn_start:]
        next_def = tail.find("\n\ndef ", 1)
        fn_src = tail if next_def < 0 else tail[:next_def]
        assert "load_harness_script(\"phase_auditor.py\")" in fn_src, (
            "Site 2 must call load_harness_script(\"phase_auditor.py\")"
        )
        executable_imports = re.findall(
            r"^[ \t]*from scripts\.phase_auditor import",
            fn_src, flags=re.MULTILINE,
        )
        assert executable_imports == [], (
            f"Site 2 must NOT use cwd-relative import as executable code; "
            f"got: {executable_imports!r}"
        )

    def test_site_3_cmd_audit_phase_calls_helper(self):
        """Site 3 (`cmd_audit_phase`) source must invoke
        `load_harness_script('phase_auditor.py')` (direct import since S4e), NOT a real
        cwd-relative `from scripts.phase_auditor import …` (an explanation
        in the docstring that mentions the old import name is allowed)."""
        import re
        pc_path = HARNESS_REPO / "cli" / "project_cmds.py"
        pc_src = pc_path.read_text(encoding="utf-8")
        fn_start = pc_src.find("def cmd_audit_phase(")
        assert fn_start > 0, "cmd_audit_phase must exist"
        tail = pc_src[fn_start:]
        next_def = tail.find("\n\ndef ", 1)
        fn_src = tail if next_def < 0 else tail[:next_def]
        assert "load_harness_script(\"phase_auditor.py\")" in fn_src, (
            f"Site 3 must call load_harness_script(\"phase_auditor.py\"); "
            f"got fn_src head: {fn_src[:400]!r}"
        )
        # Check *executable* imports only — match lines whose leading
        # whitespace is followed by `from scripts.phase_auditor import`
        # (i.e. NOT inside a docstring).
        executable_imports = re.findall(
            r"^[ \t]*from scripts\.phase_auditor import",
            fn_src, flags=re.MULTILINE,
        )
        assert executable_imports == [], (
            f"Site 3 must NOT use cwd-relative import as executable code; "
            f"got: {executable_imports!r}"
        )


class TestGenerateSabJsonPathBug:
    """Round 5: `cli/check_cmds.py:_generate_sab_json` built its own
    `Path(__file__).parent / "scripts"` — `cli/` has no `scripts/`
    subdirectory (scripts/ is a sibling at the repo root), so
    `sab_script.exists()` was always False. Every `harness_cli.py manifest`
    run printed a false "[SAB] ERROR: generate_sab.py not found" and the
    caller (`cmd_generate_quality_manifest`) discarded the return value, so
    the failure was invisible. Same `.parent` vs `.parent.parent` bug class
    as P6-2026-07-07 — never swept by the P6/A1 fixes because those only
    grepped for `from scripts.X import`, not a manually-built subprocess
    Path. Fix: `_generate_sab_json` now resolves the script via the shared
    `harness_scripts_dir()` SSOT (core/utils/script_loader.py) instead of
    its own path arithmetic.
    """

    def test_generate_sab_json_finds_the_real_script_from_foreign_cwd(self, tmp_path):
        """Invoke the REAL `_generate_sab_json` (not a replicated path calc —
        c38a9fe's own lesson: 'tests passed because they replicated the path
        calculation rather than invoking the real inlined helper') from /tmp
        cwd against an empty consumer project. Pre-fix, this always printed
        the false "not found" message regardless of project contents;
        post-fix, the script IS found (generation may still legitimately
        fail for an unrelated reason — no SAD.md in this empty fixture —
        but that is a different, honest error message).
        """
        harness_repo = str(HARNESS_REPO)
        project = tmp_path / "consumer_project"
        project.mkdir()
        snippet = (
            "import sys; "
            f"sys.path.insert(0, {harness_repo!r}); "
            "from pathlib import Path; "
            "from cli.check_cmds import _generate_sab_json; "
            f"_generate_sab_json(Path({str(project)!r}))"
        )
        result = subprocess.run(
            [sys.executable, "-c", snippet],
            cwd="/tmp", capture_output=True, text=True, timeout=30,
        )
        combined = result.stdout + result.stderr
        assert "generate_sab.py not found" not in combined, (
            "the real generate_sab.py must be found via harness_scripts_dir(), "
            f"regardless of caller cwd; got: {combined[:600]!r}"
        )


class TestA1_PhaseAuditorAbsolutePathLoad:
    """A1-2026-07-07 site coverage: each site that previously inline-imported
    `from scripts.phase_auditor import …` now loads via the helper. These tests
    confirm both Site 2 and Site 3 work from a foreign cwd.
    """

    def test_phase_auditor_loadable_directly_via_helper(self):
        """In-process check: import harness_cli then call load_harness_script
        for phase_auditor, attribute lookup must succeed."""
        sys.path.insert(0, str(HARNESS_REPO))
        try:
            # Force fresh import each time so the helper resolves at runtime
            # rather than via a cached module attribute.
            import importlib
            if "harness_cli" in sys.modules:
                importlib.reload(sys.modules["harness_cli"])
            import harness_cli  # noqa: F401
            mod = harness_cli.load_harness_script("phase_auditor.py")
            assert hasattr(mod, "PhaseAuditor")
            assert hasattr(mod, "LocalFetcher")
            assert hasattr(mod, "GitHubFetcher")
        finally:
            sys.path.pop(0)

    def test_run_phase_auditor_invokable_from_consumer_cwd(self):
        """Smoke: invoke `_run_phase_auditor` from /tmp cwd via subprocess.
        Without A1 fix, this would have triggered the
        `from scripts.phase_auditor import …` ImportError and printed
        `[WARN] PhaseAuditor unavailable … skipping`. With A1, the helper
        resolves the absolute path and the function runs end-to-end.

        NOTE: real LocalFetcher walks filesystem in `advance-phase` flow; we
        only assert NO `[WARN] PhaseAuditor unavailable` emission, which is
        the A1 regression anchor. Other runtime exceptions are acceptable
        for this scope.
        """
        scripts = str(HARNESS_REPO)
        snippet = (
            "import sys, pathlib; "
            f"sys.path.insert(0, {scripts!r}); "
            # Use a non-existent path arg so PhaseAuditor.run_all_checks bails
            # before it would have walked a real filesystem. We only assert
            # that the import-line does NOT silent-skip with [WARN].
            "import harness_cli as _hc; "
            "mod = _hc.load_harness_script('phase_auditor.py'); "
            "fetcher = mod.LocalFetcher(project_root='/tmp/__a1_smoke_does_not_exist__'); "
            "auditor = mod.PhaseAuditor(fetcher=fetcher, phase=1); "
            "res = auditor.run_all_checks(); "
            "print('A1_LOAD_OK', len(res.findings))"
        )
        result = subprocess.run(
            [sys.executable, "-c", snippet],
            cwd="/tmp", capture_output=True, text=True, timeout=30,
        )
        # We don't care about returncode — we care about WARN emission
        combined = (result.stdout + result.stderr).strip()
        assert "[WARN] PhaseAuditor unavailable" not in combined, (
            f"A1 fix should prevent silent-skip warning; got: {combined[:600]!r}"
        )
        # Sanity: the import succeeded and PhaseAuditor produced output
        assert "A1_LOAD_OK" in combined, (
            f"PhaseAuditor should run and produce A1_LOAD_OK marker; got: {combined[:600]!r}"
        )
