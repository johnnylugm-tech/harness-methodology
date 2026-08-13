"""Fix step prompt builders: TEST-FIX, COVERAGE-FIX, INFRA-FIX, LINT-FIX, CODE-FIX."""

from pathlib import Path

from core.phase_hooks import (
    PRAGMA_NO_COVER_ALLOWLIST as _pragma_allowlist,
    PRAGMA_NO_COVER_GUIDANCE as _pragma_guidance,
)
from core.state_io import load_quality_manifest
from core.quality_gate.cov_utils import resolve_fr_scoped_src_files

from cli.fr_prompts._shared import (
    _compute_fr_spec_data,
    _extract_srs_fr_section,
    _past_failures_block,
)


def build_test_fix_prompt(fr_id: str, phase: int, project: Path, srs_path: Path, test_file: str, src_dir: str, tool_snapshot: str | None = None) -> str:
    """Build prompt for TEST-FIX step."""
    return (
        f"You are a test isolation fixer for {fr_id}.\n\n"
        f"[FORBIDDEN — read first]\n"
        f"- Modifying source files in `{src_dir}/`\n"
        f"- Deleting or xfail-marking tests\n\n"
        f"[PROBLEM]\n"
        f"Gate 1 tests are failing because of EXTERNAL SIDE-EFFECTS, not because the "
        f"feature is missing. Tests call real infrastructure (HMAC verification, DB "
        f"connections, HTTP calls) that short-circuits before feature logic is reached. "
        f"Every test returns the same infrastructure error (e.g. 401 Unauthorized).\n\n"
        f"[ACTUAL TOOL OUTPUT]\n"
        f"{tool_snapshot or '(not available)'}\n\n"
        f"[TASK]\n"
        f"1. Identify the infrastructure call that intercepts (HMAC verifier, DB, HTTP).\n"
        f"2. Add a pytest autouse fixture to `{test_file}` (or `tests/conftest.py`) "
        f"that mocks it so tests reach the feature logic:\n"
        f"   @pytest.fixture(autouse=True)\n"
        f"   def _bypass_infra(monkeypatch):\n"
        f"       monkeypatch.setattr(InfraClass, 'verify', lambda *a, **kw: True)\n"
        f"3. Run `python3 -m pytest {test_file} -q` — tests must now fail for the RIGHT reason "
        f"(AssertionError or NameError from missing feature, NOT 401/auth error).\n"
        f"4. Commit: `git add {test_file} tests/conftest.py && "
        f"git commit -m 'test({fr_id}): fix test isolation — add autouse infra mock'`\n\n"
        f'[OUTPUT FORMAT]\nReturn JSON: {{"status": "DONE", "fixture_added": true, '
        f'"commit": "<hash>", "summary": "<under 50 chars>"}}'
    )


def build_coverage_fix_prompt(fr_id: str, phase: int, project: Path, srs_path: Path, test_file: str, src_dir: str, tool_snapshot: str | None = None) -> str:
    """Build prompt for COVERAGE-FIX step."""
    _cf_manifest = load_quality_manifest(project, lenient=True)
    _cf_src_files = resolve_fr_scoped_src_files(
        str(project), fr_id, test_file, src_dir, _cf_manifest
    )
    if _cf_src_files:
        _cf_include = ",".join(_cf_src_files)
        _cov_check_cmd = (
            f'python3 -m coverage run -m pytest {test_file} -q '
            f'&& python3 -m coverage report --include="{_cf_include}" -m'
        )
    else:
        _cov_check_cmd = f"python3 -m pytest {test_file} --cov={src_dir} --cov-report=term-missing -q"

    _cf_sp_warning = ""
    _tf_path = project / test_file
    if _tf_path.exists():
        _cf_test_text = _tf_path.read_text(encoding="utf-8")[:4000]
        if 'subprocess.run' in _cf_test_text:
            _cf_sp_warning = (
                f"\n[SUBPROCESS COVERAGE CEILING — READ THIS FIRST]\n"
                f"Your test file `{test_file}` drives the CLI via `subprocess.run`.\n"
                f"pytest-cov CANNOT measure coverage inside subprocesses — the CLI\n"
                f"entry-point modules (e.g. cli.py, __main__.py, config.py) will stay\n"
                f"at 0% no matter how many subprocess tests you add.\n"
                f"Adding MORE subprocess tests will NOT raise coverage — it wastes rounds.\n\n"
                f"INSTEAD, you MUST add in-process unit tests that call the CLI functions\n"
                f"directly. Pattern (ADD alongside existing subprocess tests, do NOT replace):\n"
                f"  import io, contextlib\n"
                f"  from <your_pkg> import cli\n"
                f"  buf = io.StringIO()\n"
                f"  with contextlib.redirect_stdout(buf):\n"
                f"      exit_code = cli.main(['submit', 'echo hi'])\n"
                f"  output = buf.getvalue()\n\n"
                f"Add in-process tests for the SAME validation paths (empty/too-long/\n"
                f"injection-blacklist/name-duplicate/atomic-write) covered by the existing\n"
                f"subprocess tests. Keep both — subprocess tests verify the real CLI entry\n"
                f"point; in-process tests provide measurable coverage for the internal logic.\n\n"
            )

    _pragma_allowlist_block = (
        "Allowed exemptions (rendered verbatim from Gate 1 audit's "
        "`PRAGMA_NO_COVER_ALLOWLIST` SSOT — adding a pattern that is not "
        "in this list WILL fail Gate 1 on the next dispatch):\n"
        + "".join(f"  - `{pat}`\n" for pat in _pragma_allowlist)
    )

    return (
        f"You are a coverage fixer for {fr_id}.\n\n"
        f"[FORBIDDEN — read first]\n"
        f"- Deleting or xfail-marking existing tests\n"
        f"- Adding `# pragma: no cover` to lines that CAN be tested (only use it as a "
        f"last resort for genuinely untestable lines — see ESCAPE HATCH below)\n\n"
        f"{_cf_sp_warning}"
        f"[SITUATION]\n"
        f"All Gate 1 tests currently PASS, but the test_coverage dimension is FAILING.\n"
        f"Coverage is below the 80% threshold. Two possible root causes:\n"
        f"  A. Existing tests don't cover enough source lines (code coverage < 80%).\n"
        f"  B. Required test functions from TEST_SPEC.md are absent from `{test_file}`.\n\n"
        f"[ACTUAL TOOL OUTPUT — from pre-run]\n"
        f"{tool_snapshot or '(not available)'}\n\n"
        f"[TASK]\n"
        f"1. Run `{_cov_check_cmd}` "
        f"to identify which source lines are not covered (Miss column).\n"
        f"2. Read `02-architecture/TEST_SPEC.md` section for {fr_id} to identify required "
        f"test function names. For each function missing from `{test_file}` — add it.\n"
        f"3. For each uncovered line: decide which approach applies:\n"
        f"   a. Line CAN be reached by a test → add a targeted unit test.\n"
        f"   b. Line is genuinely untestable → apply ESCAPE HATCH (see below).\n"
        f"4. Re-run until coverage reaches ≥ 80%: "
        f"`{_cov_check_cmd}`\n"
        f"5. Commit both `{test_file}` and any source changes from ESCAPE HATCH:\n"
        f"   `git add {src_dir}/ {test_file} && "
        f"git commit -m 'test({fr_id}): add coverage tests and pragma exclusions'`\n\n"
        f"[ESCAPE HATCH — pragma: no cover]\n"
        f"{_pragma_guidance}\n"
        f"{_pragma_allowlist_block}\n"
        f"`if __name__ == \"__main__\":` blocks are NOT a valid pragma target. If this "
        f"project has a dedicated entry-point module (`<pkg>/__main__.py`), exclude THAT "
        f"file at the file level via setup.cfg's `[coverage:run] omit`, and DELETE any "
        f"duplicate guard found elsewhere — do not pragma it. If no dedicated entry-point "
        f"module exists, extract one; that is an architecture fix, not a coverage "
        f"suppression.\n"
        f"Each `# pragma: no cover` annotation MUST be accompanied by a one-line comment "
        f"explaining WHY it is untestable. Example that PASSES Gate 1 "
        f"(matches the allowlist above):\n"
        f"  `except BaseException: pass  # pragma: no cover — atomic-write cleanup, see core.atomic_io`\n\n"
        f"[PARTIAL PROGRESS NOTE]\n"
        f"If there are many missing spec tests (>50), add as many as you can and commit.\n"
        f"The meta-loop will re-run if coverage is still insufficient — each session "
        f"reads the test file fresh and picks up where the previous session left off.\n"
        f"Do NOT stop early to 'leave some for next time' — add the maximum you can.\n\n"
        f'[OUTPUT FORMAT]\nReturn JSON: {{"status": "DONE", "coverage_pct": <number>, '
        f'"tests_added": <count>, "pragmas_added": <count>, '
        f'"commit": "<hash>", "summary": "<under 50 chars>"}}'
    )


def build_infra_fix_prompt(fr_id: str, phase: int, project: Path, srs_path: Path, test_file: str, src_dir: str, tool_snapshot: str | None = None) -> str:
    """Build prompt for INFRA-FIX step."""
    return (
        f"You are an infrastructure mock fixer for {fr_id}.\n\n"
        f"[FORBIDDEN — read first]\n"
        f"- Deleting or xfail-marking existing tests\n"
        f"- Removing skip markers without providing an alternative that actually runs\n\n"
        f"[SITUATION]\n"
        f"Gate 1 tests are being SKIPPED (not failing) because they depend on external "
        f"infrastructure (Docker, Redis, database, external HTTP) that is unavailable in "
        f"this environment. The skipped tests contribute 0 lines to coverage, causing "
        f"test_coverage to fail.\n\n"
        f"[ACTUAL TOOL OUTPUT — from pre-run]\n"
        f"{tool_snapshot or '(not available)'}\n\n"
        f"[TASK]\n"
        f"1. Identify which tests are skipped and WHY (read the skip condition: "
        f"`python3 -m pytest {test_file} -v --collect-only 2>&1 | grep -i skip`).\n"
        f"2. For each skipped test, choose ONE approach:\n"
        f"   a. ADD a parallel mock-based test that exercises the same logic without "
        f"real infra (e.g. monkeypatch Redis/Docker client). Keep the original skip "
        f"test as-is for integration runs.\n"
        f"   b. If the skipped code path is genuinely untestable without the real service "
        f"AND the source branch is an infrastructure-only fallback: annotate with "
        f"`# pragma: no cover` + reason comment in `{src_dir}/`.\n"
        f"3. Run `python3 -m pytest {test_file} -q` to verify no new failures are introduced.\n"
        f"4. Commit: `git add {src_dir}/ {test_file} && "
        f"git commit -m 'test({fr_id}): add mock tests for infra-skipped paths'`\n\n"
        f'[OUTPUT FORMAT]\nReturn JSON: {{"status": "DONE", "mocks_added": <count>, '
        f'"pragmas_added": <count>, "commit": "<hash>", "summary": "<under 50 chars>"}}'
    )


def build_lint_fix_prompt(fr_id: str, phase: int, project: Path, srs_path: Path, test_file: str, src_dir: str, tool_snapshot: str | None = None) -> str:
    """Build prompt for LINT-FIX step."""
    return (
        f"You are a linting fixer for {fr_id}.\n\n"
        f"[FORBIDDEN — read first]\n"
        f"- Modifying test files in `tests/`\n"
        f"- Suppressing violations with `# noqa` unless the violation is a false positive "
        f"(document why if you use noqa)\n\n"
        f"[SITUATION]\n"
        f"Gate 1 linting dimension is FAILING. Fix ALL ruff violations in `{src_dir}/` "
        f"so `python3 -m ruff check {src_dir}/ --extend-ignore RUF001,RUF002,RUF003` exits 0.\n\n"
        f"[ACTUAL TOOL OUTPUT — from pre-run]\n"
        f"{tool_snapshot or '(not available)'}\n\n"
        f"[TASK]\n"
        f"1. Run `python3 -m ruff check {src_dir}/ --extend-ignore RUF001,RUF002,RUF003 2>&1` to see the full violation list.\n"
        f"2. For N-series violations (naming conventions — N801, N802, N806, N816 etc.):\n"
        f"   - Rename constants/variables to follow PEP 8 naming (UPPER_CASE for module "
        f"constants, UpperCase for classes, lower_case for functions/variables).\n"
        f"   - Update ALL references to each renamed symbol (use `grep -rn '<old_name>'` "
        f"to find them, then rename systematically).\n"
        f"3. For E/W-series violations: fix in-place per ruff's suggestion.\n"
        f"4. Re-run `python3 -m ruff check {src_dir}/ --extend-ignore RUF001,RUF002,RUF003` — it MUST exit 0 before you commit.\n"
        f"5. Run `python3 -m pytest {test_file} -q` to confirm no tests broken by renames.\n"
        f"6. Commit: `git add {src_dir}/ && "
        f"git commit -m 'fix({fr_id}): resolve ruff linting violations'`\n\n"
        f"[NOTE] If BOTH linting AND test_coverage were failing, this session fixes "
        f"linting ONLY. The meta-loop will address coverage in the next round.\n\n"
        f'[OUTPUT FORMAT]\nReturn JSON: {{"status": "DONE", "violations_fixed": <count>, '
        f'"commit": "<hash>", "summary": "<under 50 chars>"}}'
    )


def build_code_fix_prompt(fr_id: str, phase: int, project: Path, srs_path: Path, test_file: str, src_dir: str, failing_dims: list | None = None, tool_snapshot: str | None = None) -> str:
    """Build prompt for CODE-FIX step."""
    if failing_dims is None:
        return (
            f"You are a code fixer. Gate 1 for {fr_id} could not complete "
            f"(sub-agent timeout or error — no gate1_result.json was written).\n\n"
            f"[TASK — diagnostic mode]\n"
            f"1. Run `python3 -m pytest tests/ -q` to identify failing / missing tests.\n"
            f"2. Run `python3 -m ruff check {src_dir}/ --extend-ignore RUF001,RUF002,RUF003` to identify lint errors.\n"
            f"3. Based on actual results:\n"
            f"   a. If tests are failing or missing → add/fix tests in `{test_file}` "
            f"AND fix source code in `{src_dir}/` as needed.\n"
            f"   b. If lint errors → fix source code only.\n"
            f"4. Run `python3 -m pytest tests/ -q` to confirm all tests pass.\n"
            f"5. Commit all changed files: "
            f"`git add {src_dir}/ {test_file} && "
            f"git commit -m 'fix({fr_id}): address Gate1 failures'`\n\n"
            f"[FORBIDDEN]\n"
            f"- Deleting or modifying existing passing tests\n"
            f"- app/infrastructure/ paths\n\n"
            f'[OUTPUT FORMAT]\nReturn JSON: {{"status": "DONE", "dims_fixed": [...], '
            f'"commit": "<hash>", "summary": "<under 50 chars>"}}'
        )

    srs_section = _extract_srs_fr_section(srs_path, fr_id) if srs_path else ""
    spec = _compute_fr_spec_data(project, fr_id, test_file)
    spec_test_names = spec["spec_test_names"]
    existing_spec_tests = spec["existing_spec_tests"]
    spec_summary = spec["spec_summary"]

    _fdims_lower = {str(d).lower() for d in failing_dims}
    _test_cov_failing = "test_coverage" in _fdims_lower
    _src_failing = bool(_fdims_lower - {"test_coverage"})

    dims_str = "\n".join(str(d) for d in failing_dims)

    test_cov_section = ""
    if _test_cov_failing:
        missing_spec = [fn for fn in spec_test_names if fn not in existing_spec_tests]
        present_spec = [fn for fn in spec_test_names if fn in existing_spec_tests]

        parts: list[str] = [
            f"\n[TEST COVERAGE FIX — required for test_coverage dimension]\n"
            f"{spec_summary}\n\n"
        ]

        if missing_spec:
            parts.append(
                f"MISSING ({len(missing_spec)} tests) — these required tests are NOT in `{test_file}`:\n"
                + "\n".join(f"  - {fn}" for fn in missing_spec)
                + "\n  → ADD ALL of them as real, passing tests in THIS session.\n"
                + "  IMPORTANT: write ALL missing tests in one go — do not stop after 1-2.\n"
                + "  The agent has enough max_turns (70) to add all remaining tests in one session.\n\n"
            )

        if present_spec:
            parts.append(
                f"PRESENT but failing ({len(present_spec)} tests) — these tests exist in `{test_file}`:\n"
                + "\n".join(f"  - {fn}" for fn in present_spec)
                + "\n  → Run `python3 -m pytest {test_file} -v` and fix each failing test.\n\n"
            )

        if not spec_test_names:
            parts.append(
                f"Read `02-architecture/TEST_SPEC.md` section for {fr_id} to get\n"
                "required test function names, then for each:\n"
                "  - NOT in test file → ADD as a real passing test\n"
                "  - In test file but FAILING → fix source code or assertion\n\n"
            )

        test_cov_section = "".join(parts)

    task_lines = [
        "1. Read `harness/ssi/prompts/evaluate_dimension.md` for each failing dimension's criteria.",
    ]
    n = 2
    if _src_failing:
        task_lines.append(
            f"{n}. Fix source code in `{src_dir}/` to address non-test-coverage failing dimensions."
        )
        n += 1
    if _test_cov_failing:
        task_lines.append(
            f"{n}. Resolve test_coverage failures (see TEST COVERAGE FIX above):\n"
            f"   a. ADD any missing required test functions to `{test_file}`.\n"
            f"   b. For tests that exist but FAIL: fix source code or the failing assertion."
        )
        n += 1
    task_lines.append(f"{n}. Run `python3 -m pytest tests/ -q` to confirm ALL tests pass.")
    n += 1
    git_paths = " ".join(filter(None, [
        f"{src_dir}/" if _src_failing else "",
        test_file if _test_cov_failing else "",
    ]))
    task_lines.append(
        f"{n}. Commit: `git add {git_paths} && "
        f"git commit -m 'fix({fr_id}): address Gate1 failing dims'`"
    )

    if _test_cov_failing:
        forbidden = (
            "- Deleting existing tests\n"
            "- Skipping or xfail-marking tests to make them 'pass'\n"
            "- app/infrastructure/ paths"
        )
    else:
        forbidden = (
            "- Modifying test files\n"
            "- app/infrastructure/ paths"
        )

    gap = "\n" if not test_cov_section else ""

    snapshot_section = ""
    if tool_snapshot:
        snapshot_section = (
            f"\n[ACTUAL TOOL OUTPUT — captured at orchestration time]\n"
            f"Use these exact errors as your fix targets. "
            f"Do NOT re-run the tools to re-discover them — fix what is shown here.\n"
            f"{tool_snapshot}\n\n"
        )

    # Round 27 站5a: what already failed here, recalled at the moment of the fix
    # rather than only at phase entry — and keyed on the dimension that blocked,
    # which the phase-entry recall never passes.
    _past = _past_failures_block(
        project, fr_id,
        dimension=(str(failing_dims[0]).split()[0].lower() if failing_dims else None),
    )
    past_section = f"\n{_past}\n\n" if _past else ""

    return (
        f"You are a code fixer. Gate 1 FAILED for {fr_id}. Fix the failing dimensions.\n\n"
        f"[FORBIDDEN — read before anything else]\n"
        f"{forbidden}\n\n"
        f"[FR REQUIREMENTS — the code you are about to change has to satisfy these]\n"
        f"{srs_section or f'See SRS.md for {fr_id} requirements'}\n\n"
        f"A fix that raises the failing dimension while moving the implementation "
        f"further from the requirement above is not a fix. Adding a test double so "
        f"a test stops touching a real dependency raises test_coverage and removes "
        f"the only thing that was checking the dependency exists.\n\n"
        f"[FAILING DIMENSIONS]\n"
        f"{dims_str}\n"
        f"{past_section}"
        f"{test_cov_section}"
        f"{snapshot_section}"
        f"{gap}"
        f"[TASK]\n"
        + "\n".join(task_lines) + "\n\n"
        + '[OUTPUT FORMAT]\nReturn JSON: {"status": "DONE", "dims_fixed": [...], '
        '"commit": "<hash>", "summary": "<under 50 chars>"}'
    )
