"""TDD step prompt builders: TDD-RED, TDD-GREEN, TDD-IMPROVE."""

import sys
from pathlib import Path

from cli.fr_prompts._shared import (
    _extract_srs_fr_section,
    _extract_test_spec_names,
    _sab_binding_block,
)


def build_tdd_red_prompt(fr_id: str, phase: int, project: Path, srs_path: Path, test_file: str, src_dir: str) -> str:
    """Build prompt for TDD-RED step."""
    srs_section = _extract_srs_fr_section(srs_path, fr_id) if srs_path else ""
    _, spec_note = _extract_test_spec_names(project, fr_id)

    # CRG semantic search: find existing related code to avoid re-implementing
    _related_ctx = ""
    try:
        from harness.crg_bridge import CRGBridge as _CRGBridge
        _crg_sr = _CRGBridge()
        _sr = _crg_sr.semantic_search(str(project), fr_id, kind="Function", limit=5)
        _hits = (_sr or {}).get("results", [])
        if _hits:
            _related_ctx = (
                "[RELATED EXISTING CODE — CRG semantic search]\n"
                + "\n".join(
                    f"  - {h.get('name','?')} "
                    f"({(h.get('file_path') or '').split('/')[-1]})"
                    for h in _hits[:5]
                )
                + "\n\n"
            )
    except Exception as exc:
        print(f"[WARN] TDD-RED prompt: CRG semantic search unavailable "
              f"(prompt proceeds without related-code context): {exc}", file=sys.stderr)

    return (
        f"You are a TDD developer. Your ONLY task: write failing pytest tests for {fr_id}.\n\n"
        f"{spec_note}"
        # spec_note ends on a single newline; the block needs its own blank line.
        f"{chr(10) if spec_note else ''}"
        f"{_sab_binding_block(project, fr_id, src_dir)}"
        f"{_related_ctx}"
        f"[FORBIDDEN — read before anything else]\n"
        f"- Implementing any source code (test file only)\n"
        f"- app/infrastructure/ paths\n"
        f"- @covers: L1 Error | @type: edge annotations\n"
        f"- Using try/except ImportError or lazy imports to hide ModuleNotFoundError. It is EXPECTED and PERFECTLY FINE for pytest to crash with Collection Error (Exit Code 2) because the source code doesn't exist yet.\n\n"
        f"[UNIT TEST CONTRACT — avoid false-fail traps]\n"
        f"Tests must fail because the FEATURE is missing, not because of external side-effects.\n"
        f"- Use standard top-level imports (e.g. `from src.engines.xxx import yyy`). Do NOT use try/except ImportError. If pytest returns Exit Code 2 (Collection Error) due to missing modules, this is a VALID RED STATE. Do not try to \"fix\" it by hiding the import.\n"
        f"- If tests call methods that perform real external operations (HMAC signature\n"
        f"  verification, DB connections, HTTP calls), use a pytest autouse fixture in\n"
        f"  `tests/conftest.py` (or an inline @pytest.fixture) to mock them. This is\n"
        f"  NOT 'implementing the feature' — it is required test isolation.\n"
        f"- Example: a pipeline.process() call performs HMAC verification internally.\n"
        f"  Add an autouse fixture: monkeypatch.setattr(Verifier, 'verify', lambda *a: True)\n"
        f"  so the test fails because the pipeline logic is absent, not because of bad sig.\n"
        f"- If you use patch.object(obj, 'method_name', ...) in a test, add a comment\n"
        f"  directly above that test explaining what the GREEN agent must implement:\n"
        f"  # GREEN TODO: <ClassName> must have <method_name>(self, *args) -> <return_type>\n"
        f"  Do NOT add stubs to source files yourself — GREEN does that.\n\n"
        f"[INTEGRATION FR GUIDELINES — applies when this FR exercises CLI / subprocess / cross-process state]\n"
        f"(v2.13.0 — covers FR-05 P3 2026-07-16 lesson. Skip this block if your FR is\n"
        f"purely a library function; read it if test_file ever calls `subprocess.run`,\n"
        f"`cli.main(...)`, or exercises a stateful fixture like breaker/cache/store.)\n\n"
        f"- When using `subprocess.run([sys.executable, \"-m\", \"<your_package>\", ...])`:\n"
        f"  * Always propagate PYTHONPATH to the child env (pytest's `pythonpath = ...`\n"
        f"    in setup.cfg does NOT propagate to child processes):\n"
        f"        env = os.environ.copy()\n"
        f"        env[\"<PROJECT_HOME_VAR>\"] = str(child_home)\n"
        f"        src_root = Path(__file__).resolve().parent.parent / 'src'\n"
        f"        env['PYTHONPATH'] = str(src_root) + os.pathsep + env.get('PYTHONPATH','')\n"
        f"  * Decide in-process vs out-of-process explicitly; add a comment naming the choice.\n"
        f"- When one test function exercises multiple scenarios (e.g. exit code 0/1/2/3/4):\n"
        f"  * Split into N separate test functions, one per scenario. The TEST_SPEC may\n"
        f"    list N scenarios as ONE Inputs row when the prose AC enumerates them; you\n"
        f"    MUST translate that into N test_frNN_MM_* functions, each testing one\n"
        f"    scenario in isolation.\n"
        f"  * Use function-scoped fixtures (not module-scoped) so per-case state cannot\n"
        f"    leak (e.g. breaker.json OPEN from case 3 must not affect case 5).\n"
        f"  * NEVER rely on `monkeypatch` ordering to override earlier state mutations.\n"
        f"- Sub-assertion local-variable names must NOT shadow stdlib modules:\n"
        f"  FORBIDDEN as a local name in your test: json, os, sys, time, subprocess,\n"
        f"  pathlib, asyncio, typing, logging, path, file, id, type, dict, list, set,\n"
        f"  tuple, str, int, bool, bytes. If a TEST_SPEC sub-assertion predicate uses\n"
        f"  one of these (e.g. `json == \"true\"`), RENAME your local (e.g. `json_flag`)\n"
        f"  but preserve the rule_id comment intact. The check-test-spec-consistency\n"
        f"  gate would have rejected the spec already; if you see a collision here,\n"
        f"  use a domain-specific synonym.\n"
        f"- When TEST_SPEC Inputs + SRS.md prose AC seem inconsistent (e.g. AC says\n"
        f"  \"5 of which 3 done\" but Inputs lists 5 identical commands), DO NOT invent\n"
        f"  impossible assertions. Add `# SPEC_AMBIGUITY: <one-line>` comment in the\n"
        f"  test, prefer the prose AC's scenario, and write the test to construct it\n"
        f"  mechanically (e.g. mix success+failure commands to produce the desired\n"
        f"  distribution). If you truly cannot construct the scenario, write the test\n"
        f"  against the SIMPLER invariant (>= 1 instead of == 3) and note the deviation.\n"
        f"- SUBPROCESS COVERAGE CEILING — critical for GATE1 test_coverage:\n"
        f"  * pytest-cov CANNOT measure coverage of code running inside a subprocess.\n"
        f"    If your test uses `subprocess.run([sys.executable, \"-m\", \"<pkg>\", ...])`,\n"
        f"    the entry-point modules will show 0% coverage — no matter how many\n"
        f"    subprocess tests you write.\n"
        f"  * To achieve >= 80% GATE1 test_coverage, you MUST include in-process tests\n"
        f"    that import and call the handler functions directly. Import them at the\n"
        f"    module names the [SAB — BINDING MODULE PATHS] block above declares for this\n"
        f"    FR (if that block is absent, follow SAD.md §2) — a test that imports a\n"
        f"    module the SAB does not declare pulls the implementation to a name Gate 1\n"
        f"    will then BLOCK as a phantom. Capture stdout via\n"
        f"    `contextlib.redirect_stdout` + `io.StringIO`.\n"
        f"  * Keep your subprocess acceptance tests — they verify the REAL user-facing\n"
        f"    entry point. Add in-process unit tests for the INTERNAL logic as a separate\n"
        f"    test class/functions in the same file. Both test types coexist.\n"
        f"  * The in-process tests must exercise the SAME validation paths (empty/too-long/\n"
        f"    injection-blacklist/name-duplicate) as the subprocess tests, just via the\n"
        f"    Python API instead of subprocess.run.\n"
        f"  * Example in-process pattern (NOT replacing, ADDING to existing subprocess tests):\n"
        f"      import io, contextlib\n"
        f"      from <your_pkg> import cli\n"
        f"      def test_frNN_MM_happy_inprocess(tmp_path):\n"
        f"          buf = io.StringIO()\n"
        f"          with contextlib.redirect_stdout(buf):\n"
        f"              exit_code = cli.main(['submit', 'echo hi'])\n"
        f"          assert exit_code == 0\n"
        f"          assert re.match(r'^[0-9a-f]{{8}}$', buf.getvalue().strip())\n\n"
        f"[FR REQUIREMENTS]\n"
        f"{srs_section or f'See SRS.md for {fr_id} requirements'}\n\n"
        f"[TASK]\n"
        f"1. Create/edit `{test_file}` with failing tests covering the acceptance criteria above. "
        f"If the file already exists (e.g. from a prior interrupted run), verify its test names "
        f"match the TEST SPEC exactly, fix any mismatch, but do NOT skip step 5 — an existing-but-"
        f"uncommitted file is exactly the state this step must resolve, not something to leave as-is.\n"
        f"2. Every test function name MUST match the TEST SPEC names listed above exactly.\n"
        f"3. The tests MUST FAIL — do NOT implement the feature yet.\n"
        f"4. Run `python3 -m pytest {test_file} -q`. Tests failing or raising Collection Error (ModuleNotFoundError) means SUCCESS for this RED step.\n"
        f"5. Commit: `git add {test_file} && git commit -m 'test(RED): failing test for {fr_id}'`\n\n"
        f'[OUTPUT FORMAT]\nReturn JSON: {{"status": "DONE", "test_file": "{test_file}", '
        f'"commit": "<hash>", "summary": "<under 50 chars>"}}'
    )


def build_tdd_green_prompt(fr_id: str, phase: int, project: Path, srs_path: Path, test_file: str, src_dir: str) -> str:
    """Build prompt for TDD-GREEN step."""
    srs_section = _extract_srs_fr_section(srs_path, fr_id) if srs_path else ""
    test_content = ""
    tf = project / test_file
    if tf.exists():
        test_content = tf.read_text(encoding="utf-8")
    return (
        f"You are a TDD developer. Your task: implement {fr_id} until the failing test passes.\n\n"
        f"{_sab_binding_block(project, fr_id, src_dir)}"
        f"[FORBIDDEN — read before anything else]\n"
        f"- Modifying test files\n"
        f"- app/infrastructure/ paths\n\n"
        f"[IMPLEMENTATION CONTRACT]\n"
        f"Before writing any code, scan `{test_file}` for:\n"
        f"  1. patch.object(obj, 'method_name', ...) — every patched method_name MUST\n"
        f"     exist in your implementation (even as a stub returning {{}}). Missing\n"
        f"     attributes cause AttributeError before the test even runs.\n"
        f"  2. autouse fixtures that mock verifiers — means the test bypasses real HMAC/auth.\n"
        f"     Do NOT add HMAC bypass to production code; the fixture already handles it.\n"
        f"  3. Any test that asserts on status codes (200/500/429/401) from a top-level\n"
        f"     orchestrator or pipeline method — verify the implementation handles unexpected\n"
        f"     exceptions and returns a structured error response rather than propagating.\n"
        f"     Only add try/except if the tests actually require it; do not add for utilities.\n"
        f"  4. Do not write branches unreachable under the language/library's own\n"
        f"     guarantees (e.g. argparse `add_subparsers(required=True)` guarantees the\n"
        f"     dispatch attribute is always set — do not add a defensive\n"
        f"     `if handler is None:` fallback for it). If a dedicated entry-point module\n"
        f"     already exists (e.g. `<pkg>/__main__.py`), do not duplicate its\n"
        f"     `if __name__ == \"__main__\":` guard elsewhere. Write only what the\n"
        f"     failing test demands.\n\n"
        f"[FAILING TEST — {test_file}]\n"
        f"{test_content or f'(read from {test_file})'}\n\n"
        f"[FR REQUIREMENTS]\n"
        f"{srs_section or f'See SRS.md for {fr_id} requirements'}\n\n"
        f"[TASK]\n"
        f"1. Scan test file per [IMPLEMENTATION CONTRACT] above before writing any code.\n"
        f"2. Create/edit source files in `{src_dir}/` to make `{test_file}` pass.\n"
        f"3. Run `python3 -m pytest {test_file} -q` — all tests must pass.\n"
        f"4. Docstrings must include `[{fr_id}]` tag + `Citations:` with line numbers (HR-15).\n"
        f"5. Commit: `git add {src_dir}/ && git commit -m 'feat({fr_id}): GREEN'`\n\n"
        f'[OUTPUT FORMAT]\nReturn JSON: {{"status": "DONE", "files_changed": [...], '
        f'"commit": "<hash>", "summary": "<under 50 chars>"}}'
    )


def build_tdd_improve_prompt(fr_id: str, phase: int, project: Path, srs_path: Path, test_file: str, src_dir: str) -> str:
    """Build prompt for TDD-IMPROVE step."""
    test_content = ""
    tf = project / test_file
    if tf.exists():
        test_content = tf.read_text(encoding="utf-8")[:1500]
    return (
        f"You are a TDD refactorer. Your task: improve {fr_id} WITHOUT breaking tests.\n\n"
        f"{_sab_binding_block(project, fr_id, src_dir)}"
        f"[FORBIDDEN — read before anything else]\n"
        f"- Modifying test files (any file under tests/)\n"
        f"- Relocating or renaming any module the [SAB — BINDING MODULE PATHS] block "
        f"names: a refactor that moves a declared module makes it a phantom, and Gate 1 "
        f"blocks on that before it scores anything\n"
        f"- Setting enum values to None (e.g. STATUS = None, EXIT = None)\n"
        f"- Changing sys.exit() codes from their current values\n"
        f"- Injecting XX...XX placeholder markers into source files\n\n"
        f"[TEST INVARIANTS — {test_file} (first 1500 chars)]\n"
        f"{test_content or f'(read from {test_file})'}\n\n"
        f"[TASK]\n"
        f"1. Run `python3 -m pytest {test_file} -q` first — confirm all pass before any changes.\n"
        f"2. Refactor source code in `{src_dir}/` for clarity, remove duplication, improve naming.\n"
        f"3. Re-run `python3 -m pytest {test_file} -q` — must still pass.\n"
        f"4. If changes made: `git commit -m 'refactor({fr_id}): IMPROVE'`\n"
        f"5. If no refactor needed: no commit required.\n\n"
        f'[OUTPUT FORMAT]\nReturn JSON: {{"status": "DONE", "refactored": true/false, '
        f'"commit": "<hash or null>", "summary": "<under 50 chars>"}}'
    )
