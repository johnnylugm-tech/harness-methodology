# Core Bug Hunt — Fix All 25 Confirmed Bugs

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix all 25 CONFIRMED bugs identified in `docs/CORE_BUG_HUNT_REPORT.md` across 10 files in `core/`.

**Architecture:** Group fixes by file. Each task is one logical bug fix. Bugs that share the same root cause (e.g. multiple bugs in `_copy_setup_cfg_to_workdir`) are handled in a single task to avoid conflicting partial edits. Dead-code / comment-only bugs get a targeted removal or correction. No behavioral change beyond what the bug description demands.

**Tech Stack:** Python 3, `core/quality_gate/mutation_enforcer.py`, `core/auto_fix/`, `core/agent_spawner.py`, `core/phase_hooks.py`, `core/quality_gate/cross_artifact.py`, `core/quality_gate/spec_tracking_checker.py`, `core/quality_gate/phase_truth_verifier.py`, `core/submodule_sync.py`, `core/quality_gate/bug_hunt_verifier.py`, `core/quality_gate/constitution/profile.py`, `core/quality_gate/constitution/runner.py`

**Scope rules:**
- Only touch files listed in `docs/CORE_BUG_HUNT_REPORT.md`
- Do NOT modify `test/` files unless adding a regression test is part of the task
- All changes must be minimal: fix exactly the bug, touch nothing else
- Every bug fix must pass its own regression test (existing or new)

---

## Task 1: mutation_enforcer.py — Fix 1 of 8: paths_to_mutate used as single path (line 569)

**Files:**
- Modify: `core/quality_gate/mutation_enforcer.py:567-571`

**Bug:** `cwd / paths_to_mutate` joins a comma-separated string (e.g. `"core/traceability/auto_fix_propose.py,core/traceability/overlay.py"`) as one literal path, so `src_dir.exists()` is always False and the precheck silently returns `(True, "")` without ever running mutmut.

**Fix:** Split `paths_to_mutate` on commas and verify each path exists.

```python
# BEFORE (line 569-571):
src_dir = cwd / paths_to_mutate
if not src_dir.exists():
    return True, ""

# AFTER:
paths_list = [p.strip() for p in paths_to_mutate.split(",") if p.strip()]
missing = [p for p in paths_list if not (cwd / p).exists()]
if missing:
    return True, f"paths_to_mutate contains missing entries: {missing}"
```

- [ ] **Step 1: Write the failing test**

```python
# tests/test_mutation_enforcer.py — add to existing class
def test_run_mutation_precheck_rejects_missing_paths(tmp_path, monkeypatch):
    """paths_to_mutate with a missing entry should NOT silently return (True, '')."""
    import core.quality_gate.mutation_enforcer as me
    (tmp_path / "setup.cfg").write_text("[mutmut]\npaths_to_mutate=core/foo.py,core/bar.py\n")
    result = me.run_mutation_precheck(tmp_path)
    # Should NOT return (True, '') when foo.py and bar.py don't exist
    assert result != (True, ""), f"Expected failure for missing paths, got {result}"
```

Run: `pytest tests/test_mutation_enforcer.py::test_run_mutation_precheck_rejects_missing_paths -v`
Expected: PASS (bug present: returns `(True, '')` → test FAILS before fix)

- [ ] **Step 2: Apply the fix**

Edit `core/quality_gate/mutation_enforcer.py` lines 569-571 as shown above.

- [ ] **Step 3: Run test to verify it passes**

Run: `pytest tests/test_mutation_enforcer.py::test_run_mutation_precheck_rejects_missing_paths -v`
Expected: PASS

- [ ] **Step 4: Verify existing tests still pass**

Run: `pytest tests/test_mutation_enforcer.py -v -x`
Expected: all PASS

- [ ] **Step 5: Commit**

```
fix(mutation_enforcer): reject missing paths_to_mutate instead of silently passing

The comma-separated paths_to_mutate was joined as one literal path, so
src_dir.exists() was always False and the precheck returned (True, "")
without ever running mutmut — bypassing the TDD-PRECHECK gate entirely.

Split on commas and fail on any missing entry so the gate cannot be
bypassed with an unresolvable path.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
```

---

## Task 2: mutation_enforcer.py — Fix 2 of 8: mutmut results returncode never checked (line 656)

**Files:**
- Modify: `core/quality_gate/mutation_enforcer.py:656-674`

**Bug:** `res.returncode` from `mutmut results` subprocess is never checked. If the subprocess crashes, stdout is empty, `if out:` is False, `_precheck_ok` is set True and the precheck reports a clean pass.

**Fix:** Check `res.returncode != 0` before reading stdout.

```python
# BEFORE (line 656-665):
res = subprocess.run(
    ["mutmut", "results"], cwd=workdir, capture_output=True, text=True,
    timeout=30,
)

out = res.stdout.strip()
_write_survivors_artifact(...)
if out:
    ...

# AFTER:
res = subprocess.run(
    ["mutmut", "results"], cwd=workdir, capture_output=True, text=True,
    timeout=30,
)
if res.returncode != 0:
    return False, (
        f"mutmut results command failed (return code {res.returncode}).\n"
        f"STDERR:\n{res.stderr.strip()}"
    )
out = res.stdout.strip()
_write_survivors_artifact(...)
if out:
    ...
```

- [ ] **Step 1: Write the failing test** (mock subprocess to return non-zero returncode with empty stdout)

```python
def test_mutmut_results_crash_returns_false(monkeypatch):
    """mutmut results returning non-zero must be treated as a precheck failure."""
    import core.quality_gate.mutation_enforcer as me

    class FakeRes:
        returncode = 1
        stdout = ""
        stderr = "mutmut: error: no results yet"

    def fake_run(*args, **kwargs):
        return FakeRes()

    monkeypatch.setattr(me.subprocess, "run", fake_run)
    monkeypatch.setattr(me, "_write_survivors_artifact", lambda *a, **k: None)
    result = me.run_mutation_precheck(Path("/fake"))
    assert result[0] is False, f"Expected False for crashed mutmut results, got {result}"
```

Run: `pytest tests/test_mutation_enforcer.py::test_mutmut_results_crash_returns_false -v`
Expected: FAIL (bug: returns `(True, "")` → test FAILS)

- [ ] **Step 2: Apply the fix** — add returncode check before `out = res.stdout.strip()`.

- [ ] **Step 3: Run test to verify it passes**

Run: `pytest tests/test_mutation_enforcer.py::test_mutmut_results_crash_returns_false -v`
Expected: PASS

- [ ] **Step 4: Verify existing tests still pass**

Run: `pytest tests/test_mutation_enforcer.py -v -x`
Expected: all PASS

- [ ] **Step 5: Commit**

```
fix(mutation_enforcer): check mutmut results returncode before parsing stdout

A crashed mutmut results subprocess (non-zero returncode, empty stdout)
was treated as a clean precheck pass. Fail explicitly when returncode != 0.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
```

---

## Task 3: mutation_enforcer.py — Fix 3 of 8: compute_mutation_score can't distinguish zero mutants from corrupt cache (line 831)

**Files:**
- Modify: `core/quality_gate/mutation_enforcer.py:831-838`

**Bug:** `_count_mutmut_results` returns `(0, 0)` for both a genuinely mutant-free run AND a corrupt/missing sqlite cache. `compute_mutation_score` returns `(True, 0.0, "mutmut produced 0 mutants. Score = 0.")` in both cases.

**Fix:** Cross-check sqlite results against the `mutmut results` text output. If total==0 but the text output contains non-zero mutant counts, flag the discrepancy.

```python
# Find the score-computation block (lines 831-838) and add a cross-check.
# After line 831 (killed, survived = _count_mutmut_results(...)):
total = killed + survived

# Cross-check: if sqlite is 0 but text output says non-zero, sqlite may be corrupt.
text_total = 0
if total == 0 and out:
    m = re.search(r"TotalMutants\s*=\s*(\d+)", out)
    if m:
        text_total = int(m.group(1))
if total == 0 and text_total > 0:
    return False, 0.0, (
        f"mutmut produced 0 mutants in cache but text output shows {text_total} total mutants. "
        f"Cache may be corrupt or unreadable."
    )
```

- [ ] **Step 1: Write the failing test**

```python
def test_zero_mutants_from_corrupt_cache_returns_false(monkeypatch):
    """Zero mutants from sqlite but non-zero from text output should fail."""
    import core.quality_gate.mutation_enforcer as me

    def fake_count(*a, **k):
        return (0, 0)  # sqlite says 0

    class FakeRes:
        returncode = 0
        stdout = "TotalMutants = 42\nSurvived(3)"
        stderr = ""

    def fake_run(cmd, *args, **kwargs):
        if "results" in cmd:
            return FakeRes()
        class R:
            returncode = 0; stdout = ""; stderr = ""
        return R()

    monkeypatch.setattr(me, "_count_mutmut_results", fake_count)
    monkeypatch.setattr(me.subprocess, "run", fake_run)
    monkeypatch.setattr(me, "_write_survivors_artifact", lambda *a, **k: None)
    result = me.compute_mutation_score(Path("/fake"), Path("/fake/.mutmut"))
    assert result[0] is False, f"Expected False for corrupt-cache mismatch, got {result}"
```

Run: `pytest tests/test_mutation_enforcer.py::test_zero_mutants_from_corrupt_cache_returns_false -v`
Expected: FAIL (bug: returns `(True, 0.0, ...)` → test FAILS)

- [ ] **Step 2: Apply the fix** — add cross-check before the `if total == 0:` branch.

- [ ] **Step 3: Run test to verify it passes**

Run: `pytest tests/test_mutation_enforcer.py::test_zero_mutants_from_corrupt_cache_returns_false -v`
Expected: PASS

- [ ] **Step 4: Verify existing tests still pass**

Run: `pytest tests/test_mutation_enforcer.py -v -x`
Expected: all PASS

- [ ] **Step 5: Commit**

```
fix(mutation_enforcer): detect corrupt mutmut sqlite cache vs genuine zero mutants

Zero sqlite mutants with non-zero text-output total now fails explicitly,
preventing a corrupt cache from reporting a false clean pass.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
```

---

## Task 4: mutation_enforcer.py — Fix 4 of 8: stale project-root .mutmut-cache left untouched when workdir cache never materializes (line 845)

**Files:**
- Modify: `core/quality_gate/mutation_enforcer.py:844-846`

**Bug:** When all source files are excluded and no workdir cache is created, the code skips `shutil.copy2(workdir_cache, cache_file)` but never deletes a pre-existing project-root `.mutmut-cache`, leaving stale data for downstream LLM agents.

**Fix:** If no workdir cache was created AND a project-root cache exists, explicitly remove it so downstream sees a clean zero rather than stale data.

```python
# Find the block (lines 844-846):
# BEFORE:
if workdir_cache.exists():
    shutil.copy2(workdir_cache, cache_file)

# AFTER:
if workdir_cache.exists():
    shutil.copy2(workdir_cache, cache_file)
else:
    # workdir cache never created (all source excluded). Remove any
    # stale project-root cache so downstream sees a clean zero, not
    # a stale score from a prior run.
    if cache_file.exists():
        cache_file.unlink()
```

- [ ] **Step 1: Write the failing test**

```python
def test_stale_cache_removed_when_workdir_cache_absent(tmp_path, monkeypatch):
    """When workdir cache never materializes, a stale project-root cache must be deleted."""
    import core.quality_gate.mutation_enforcer as me

    stale_cache = tmp_path / ".mutmut-cache"
    stale_cache.write_text("old stale data")
    workdir = tmp_path / "workdir"
    workdir.mkdir()

    def fake_count(*a, **k):
        return (0, 0)

    class FakeRes:
        returncode = 0
        stdout = "TotalMutants = 0\n"
        stderr = ""

    def fake_run(cmd, *args, **kwargs):
        if "results" in cmd:
            return FakeRes()
        class R:
            returncode = 0; stdout = ""; stderr = ""
        return R()

    monkeypatch.setattr(me, "_count_mutmut_results", fake_count)
    monkeypatch.setattr(me.subprocess, "run", fake_run)
    monkeypatch.setattr(me, "_write_survivors_artifact", lambda *a, **k: None)
    me.compute_mutation_score(tmp_path, workdir)
    assert not stale_cache.exists(), f"Stale cache should be deleted, but still exists at {stale_cache}"
```

Run: `pytest tests/test_mutation_enforcer.py::test_stale_cache_removed_when_workdir_cache_absent -v`
Expected: FAIL (bug: stale cache remains → test FAILS)

- [ ] **Step 2: Apply the fix**

- [ ] **Step 3: Run test to verify it passes**

Run: `pytest tests/test_mutation_enforcer.py::test_stale_cache_removed_when_workdir_cache_absent -v`
Expected: PASS

- [ ] **Step 4: Verify existing tests still pass**

Run: `pytest tests/test_mutation_enforcer.py -v -x`
Expected: all PASS

- [ ] **Step 5: Commit**

```
fix(mutation_enforcer): delete stale project-root .mutmut-cache when workdir cache never materializes

When all source files are excluded and no workdir cache is created,
any pre-existing project-root cache is now explicitly removed so
downstream LLM agents cannot read stale mutation scores.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
```

---

## Task 5: mutation_enforcer.py — Fix 5+6+7 of 8: testpaths and pythonpath multi-value handling + nested cwd config (lines 228, 303, 318)

**Files:**
- Modify: `core/quality_gate/mutation_enforcer.py:228-335` (entire `_copy_setup_cfg_to_workdir` function)

**Bug 5 (line 303):** `testpaths = tests other_tests` (space-separated, valid pytest syntax) is joined as one path `"tests other_tests"` → non-existent.
**Bug 6 (line 318):** Same for `pythonpath = src lib`.
**Bug 7 (line 228):** `_find_source_setup_cfg(project)` always returns project-root setup.cfg, ignoring the nested cwd selected by `_resolve_mutmut_workdir`.

**Fix:** All three bugs are in `_copy_setup_cfg_to_workdir`. Fix by:
1. Pass `cwd` into `_copy_setup_cfg_to_workdir` and use it to find the right source config.
2. For testpaths and pythonpath: split on whitespace (shlex.split) before resolving each path individually; only include entries that resolve to existing directories.

```python
# Signature change:
# BEFORE:
def _copy_setup_cfg_to_workdir(
    project: Path,
    workdir: Path,
    abs_test_dir: Optional[Path] = None,
) -> None:

# AFTER (add cwd parameter):
def _copy_setup_cfg_to_workdir(
    project: Path,
    workdir: Path,
    abs_test_dir: Optional[Path] = None,
    cwd: Optional[Path] = None,  # NEW: the actual mutmut cwd; use instead of project
) -> None:
```

All call sites of `_copy_setup_cfg_to_workdir` must be updated to pass `cwd=cwd` from `_resolve_mutmut_workdir`.

For testpaths/patch pythonpath: use `shlex.split` to split multi-value strings and resolve each path separately.

- [ ] **Step 1: Write failing tests**

```python
def test_testpaths_multi_value_not_joined_as_single_path(tmp_path, monkeypatch):
    """Multi-value testpaths must not be joined as one bogus path."""
    import core.quality_gate.mutation_enforcer as me
    cfg = tmp_path / "setup.cfg"
    cfg.write_text("[tool:pytest]\ntestpaths = tests other_tests\n")
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    me._copy_setup_cfg_to_workdir(tmp_path, workdir)
    written = (workdir / "setup.cfg").read_text()
    # Must not contain the bogus joined path
    assert "tests other_tests" not in written, f"Multi-value testpaths wrongly joined: {written}"

def test_pythonpath_multi_value_not_left_broken(tmp_path, monkeypatch):
    """Multi-value pythonpath must be promoted to absolute or handled correctly."""
    import core.quality_gate.mutation_enforcer as me
    cfg = tmp_path / "setup.cfg"
    cfg.write_text("[tool:pytest]\npythonpath = src lib\n")
    (tmp_path / "src").mkdir()
    workdir = tmp_path / "workdir"
    workdir.mkdir()
    me._copy_setup_cfg_to_workdir(tmp_path, workdir)
    written = (workdir / "setup.cfg").read_text()
    # Must not leave the broken relative "src lib" unchanged
    assert "pythonpath = src lib" not in written, f"Multi-value pythonpath left unchanged: {written}"
```

Run both: expect both FAIL before fix.

- [ ] **Step 2: Apply the fix** — restructure `_copy_setup_cfg_to_workdir` with `cwd` parameter and `shlex.split` for multi-value paths. Update all call sites to pass `cwd`.

- [ ] **Step 3: Run tests to verify they pass**

Run: `pytest tests/test_mutation_enforcer.py -k "multi_value" -v`
Expected: both PASS

- [ ] **Step 4: Verify existing tests still pass**

Run: `pytest tests/test_mutation_enforcer.py -v -x`
Expected: all PASS

- [ ] **Step 5: Commit**

```
fix(mutation_enforcer): handle multi-value testpaths/pythonpath and pass cwd to _copy_setup_cfg_to_workdir

- Multi-value testpaths/pythonpath (space-separated) are now split via
  shlex.split and each path resolved individually before writing to the
  workdir setup.cfg.
- _copy_setup_cfg_to_workdir now accepts a cwd parameter so it reads
  the setup.cfg from the directory mutmut actually uses, not always the
  project root.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
```

---

## Task 6: mutation_enforcer.py — Fix 8 of 8: _count_mutmut_results bare except returns (0,0) for sqlite failures (implied by line 831 context)

**Files:**
- Modify: `core/quality_gate/mutation_enforcer.py` — locate `_count_mutmut_results` function

**Bug:** `_count_mutmut_results` has a bare `except Exception: return 0, 0` around sqlite reads. Any sqlite failure (missing table, locked db, corrupt file) returns `(0, 0)` — the same as a genuinely clean run. This is the root cause that makes the line 831 check impossible to distinguish.

**Fix:** Return a sentinel that distinguishes "db unreadable" from "zero mutants exist".

```python
# Find _count_mutmut_results. Change its return type annotation and
# bare except to return (0, 0, True) where third bool means "db_was_readable".
# Or: raise a custom exception from the sqlite read block and catch it
# at the call site in compute_mutation_score.

# Simpler fix: instead of bare `except Exception: return 0, 0`, return
# (0, 0, False) and have the caller check the third element.
# But this changes the call site's tuple unpacking...

# Alternative minimal fix: catch sqlite.Error specifically, log, and
# return a distinguishable (0, 0) while setting a module-level flag or
# raising an exception that compute_mutation_score can catch specifically.

# BEST: Change _count_mutmut_results to raise on sqlite errors instead
# of returning (0,0). The call site already has a broad try/except in
# compute_mutation_score (line 854: except Exception as e: return False, 0.0, f"Error...").
# So sqlite errors will be caught there and treated as a failure, not a clean pass.

# BEFORE:
# except Exception: return 0, 0

# AFTER:
import sqlite3 as _sqlite3
# ...
except (_sqlite3.Error, OSError, IOError) as exc:
    raise  # re-raise so compute_mutation_score's broad except catches it
```

- [ ] **Step 1: Write failing test** (mock sqlite3.read to raise OperationalError, verify it now propagates instead of returning (0,0))

```python
def test_sqlite_error_not_swallowed_as_zero_mutants(monkeypatch):
    """Sqlite failures must not return (0, 0) — they should propagate as errors."""
    import core.quality_gate.mutation_enforcer as me
    import sqlite3

    def fake_count_bad(*a, **k):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(me, "_count_mutmut_results", fake_count_bad)

    class FakeRes:
        returncode = 0; stdout = "TotalMutants = 5\n"; stderr = ""

    def fake_run(cmd, *args, **kwargs):
        if "results" in cmd:
            return FakeRes()
        class R:
            returncode = 0; stdout = ""; stderr = ""
        return R()

    monkeypatch.setattr(me.subprocess, "run", fake_run)
    monkeypatch.setattr(me, "_write_survivors_artifact", lambda *a, **k: None)
    # Before fix: returns (True, 0.0, ...) — silent pass
    # After fix: broad except in compute_mutation_score catches the re-raised
    # sqlite error and returns (False, 0.0, "Error running mutmut: ...")
    result = me.compute_mutation_score(Path("/fake"), Path("/fake/.mutmut"))
    assert result[0] is False, f"Expected False when sqlite fails, got {result}"
```

Run: `pytest tests/test_mutation_enforcer.py::test_sqlite_error_not_swallowed_as_zero_mutants -v`
Expected: FAIL (bug: returns `(True, 0.0, ...)` → test FAILS)

- [ ] **Step 2: Apply the fix** — change bare `except Exception` in `_count_mutmut_results` to re-raising specific sqlite errors.

- [ ] **Step 3: Run test to verify it passes**

Run: `pytest tests/test_mutation_enforcer.py::test_sqlite_error_not_swallowed_as_zero_mutants -v`
Expected: PASS

- [ ] **Step 4: Verify existing tests still pass**

Run: `pytest tests/test_mutation_enforcer.py -v -x`
Expected: all PASS

- [ ] **Step 5: Commit**

```
fix(mutation_enforcer): re-raise sqlite errors from _count_mutmut_results instead of returning (0,0)

A bare `except Exception` swallowed all sqlite failures, causing them to
be reported as a clean zero-mutant pass. Specific exceptions are now
re-raised and caught by compute_mutation_score's existing broad except,
producing a clear error instead of a false clean result.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
```

---

## Task 7: profile.py — Fix dead code (line 439) + stale comment (line 429)

**Files:**
- Modify: `core/quality_gate/constitution/profile.py:429-460` and `core/quality_gate/constitution/runner.py:382`

**Bug 1 (line 439):** `_p3_security_kw` and `_p4_security_kw` are computed but never wired into any PhaseProfile. `runner.py:382` unconditionally requests "security" keywords for all phases, falling back to the global (deprecated-term) list.

**Fix:** Either wire `_p3_security_kw`/`_p4_security_kw` into `phases[3]`/`phases[4].dimension_keywords`, OR remove the dead variables and make `runner.py:382` respect `active_dimensions` before requesting keywords. Since `phases[3].dimension_keywords` currently only has "correctness", the minimal fix is to update `runner.py:382` to skip dimensions not in `active_dimensions`.

```python
# core/quality_gate/constitution/runner.py, line ~382
# BEFORE:
security_keywords = dimension_keywords_for_phase("security", phase)

# AFTER:
# Only request keywords for dimensions that are actually active for this phase.
if "security" not in (phases.get(phase, {}).dimension_keywords or {}):
    security_keywords = []
else:
    security_keywords = dimension_keywords_for_phase("security", phase)
```

**Bug 2 (line 429):** Stale comment block contradicts the actual PhaseProfile values set later (Bug #35 fix changed phase 3 to threshold=30.0 and only "correctness" active).

**Fix:** Update the stale comment block at lines 429-435 to match the actual current values (composite_threshold=30.0, active_dimensions=["correctness"], no maintainability).

- [ ] **Step 1: Write failing tests**

```python
def test_runner_security_kw_not_requested_when_not_active():
    """runner.py must not request security keywords for phase 3."""
    from core.quality_gate.constitution import runner, profile
    # Phase 3 profile has active_dimensions = ["correctness"] only.
    # _scan_file_compliance should not call dimension_keywords_for_phase("security", 3)
    # Build a mock that tracks calls.
    calls = []
    orig_fn = runner.dimension_keywords_for_phase
    def tracking_fn(dim, phase):
        calls.append((dim, phase))
        return orig_fn(dim, phase)
    runner.dimension_keywords_for_phase = tracking_fn
    try:
        # Run a scan for phase 3
        p3_dir = Path("/tmp")  # doesn't need to exist for this check
        runner._scan_directory(p3_dir, phase=3, check_type="correctness")
        security_calls = [c for c in calls if c[0] == "security" and c[1] == 3]
        assert len(security_calls) == 0, f"security keywords requested for inactive phase 3: {security_calls}"
    finally:
        runner.dimension_keywords_for_phase = orig_fn
```

Run: `pytest tests/test_canonical_lint.py -k "security_kw" -v` (or create new test file)
Expected: FAIL before fix.

- [ ] **Step 2: Apply the fix**

- [ ] **Step 3: Run test to verify it passes**

- [ ] **Step 4: Fix stale comment** — update lines 429-435 to match actual P3 profile values.

- [ ] **Step 5: Commit**

```
fix(constitution): respect active_dimensions before requesting keywords; fix stale P3 comment

runner.py now skips dimension_keywords_for_phase for dimensions not in
the phase's active_dimensions set, preventing fallback to a deprecated
keyword list for inactive dimensions.

Also corrects the stale comment block for phase 3 (was claiming
threshold=80 and maintainability="kept"; actual values are
composite_threshold=30.0, active_dimensions=["correctness"]).

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
```

---

## Task 8: runner.py — Fix glob pattern for P5-P8 (line 497) and is_markdown flag for security dimension (line 384)

**Files:**
- Modify: `core/quality_gate/constitution/runner.py:497` and `runner.py:384`

**Bug 1 (line 497):** `_scan_directory` uses `rglob("*.py")` for phase≥3, so P5-P8 deliverables (VERIFICATION_REPORT.md etc.) are never found → vacuous 100.0 pass.

**Fix:** For P5-P8, glob the actual markdown deliverable names instead:
```python
# BEFORE:
files = list(cwd.rglob("*.py"))

# AFTER:
if phase >= 5:
    # P5-P8 deliverables are markdown files
    DELIVERABLE_MAP = {
        5: "VERIFICATION_REPORT.md",
        6: "QUALITY_REPORT.md",
        7: "RISK_REGISTER.md",
        8: "CONFIG_RECORDS.md",
    }
    name = DELIVERABLE_MAP.get(phase)
    files = [cwd / name] if name and (cwd / name).exists() else []
else:
    files = list(cwd.rglob("*.py"))
```

**Bug 2 (line 384):** `is_markdown` not passed to security/maintainability/coverage `_keyword_stuffing_penalty` calls.

**Fix:** Pass `is_markdown=is_markdown` to all four `_keyword_stuffing_penalty` call sites.

- [ ] **Step 1: Write failing test**

```python
def test_scan_directory_p5_returns_md_content_not_vacuous_pass(tmp_path):
    """P5 with a non-existent VERIFICATION_REPORT.md must NOT return 100.0."""
    from core.quality_gate.constitution import runner
    p5_dir = tmp_path
    # No VERIFICATION_REPORT.md exists
    score, passed, violations = runner._scan_directory(p5_dir, phase=5, check_type="correctness")
    assert score != 100.0 or violations, f"P5 with no deliverable got vacuous 100.0: score={score}, violations={violations}"
```

Run: `pytest tests/test_canonical_lint.py -k "p5" -v`
Expected: FAIL before fix (score=100.0 with violations=[])

- [ ] **Step 2: Apply the fix** — update `_scan_directory` glob logic and add `is_markdown` to all penalty call sites.

- [ ] **Step 3: Run test to verify it passes**

Run: `pytest tests/test_canonical_lint.py -k "p5" -v`
Expected: PASS

- [ ] **Step 4: Verify existing tests still pass**

Run: `pytest tests/test_canonical_lint.py -v -x`
Expected: all PASS

- [ ] **Step 5: Commit**

```
fix(constitution/runner): fix P5-P8 glob to match actual markdown deliverables; pass is_markdown to all penalty calls

_scan_directory now uses the actual deliverable filename (e.g. VERIFICATION_REPORT.md)
for phase 5+ instead of globbing non-existent *.py files.

_keyword_stuffing_penalty now receives is_markdown=is_markdown for all four
dimension call sites (correctness, security, maintainability, coverage),
preventing markdown documents from being penalized with code-level thresholds.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
```

---

## Task 9: auto_fix/__init__.py — Fix 1 of 2: classify() drops caller-supplied problem_type (line 135)

**Files:**
- Modify: `core/auto_fix/__init__.py:129-138`

**Bug:** `AutoFixEngine.fix()` calls `self.classify(context)` which only forwards `context.source` and `context.details` to `classify()`. The `problem_type` already set on `context` by the caller is silently dropped. The classifier falls back to source-prefix matching and frequently resolves the wrong problem_type.

**Fix:** Before calling `classify()`, inject the caller's `context.problem_type` into `context.details` so the classifier sees it:
```python
# BEFORE (line 137):
strategy, confidence, max_rounds, problem_type, error_class = self.classify(context)

# AFTER:
# Preserve caller's problem_type: inject it into details so classify() reads it.
_details = dict(context.details) if context.details else {}
if context.problem_type and "problem_type" not in _details:
    _details["problem_type"] = context.problem_type
context = dataclasses.replace(context, details=_details)
strategy, confidence, max_rounds, problem_type, error_class = self.classify(context)
```

Also verify `FixContext.details` is typed as `Optional[Dict[str, Any]]` — if the classifier's `classify(source, details)` reads `details.get("problem_type", "")`, an explicit `problem_type` in details takes precedence over source-prefix fallback.

- [ ] **Step 1: Write failing test**

```python
def test_fix_uses_caller_problem_type(tmp_path):
    """When caller sets context.problem_type, fix() must not silently drop it."""
    from core.auto_fix import AutoFixEngine, FixContext, FixStrategy
    engine = AutoFixEngine()
    ctx = FixContext(
        source="phase_hooks",
        details={"message": "FR-01 not tested"},  # no problem_type in details
        problem_type="missing_traceability",       # set by caller on the dataclass field
    )
    # Monkey-patch the classifier to record what it received
    received_details = []
    from core.auto_fix import classifier
    orig_classify = classifier.classify
    def tracking_classify(source, details):
        received_details.append(details)
        return FixStrategy.AUTO_FIX_GUARD, 0.8, 3, "low_constitution_score", "other"
    classifier.classify = tracking_classify
    try:
        engine.fix(ctx)
        assert received_details and "problem_type" in received_details[0], \
            f"classifier received details without problem_type: {received_details}"
        assert received_details[0]["problem_type"] == "missing_traceability", \
            f"problem_type was dropped; classifier got: {received_details[0]}"
    finally:
        classifier.classify = orig_classify
```

Run: `pytest tests/test_auto_fix.py -k "caller_problem_type" -v`
Expected: FAIL before fix.

- [ ] **Step 2: Apply the fix** — inject caller's problem_type into details before classify().

- [ ] **Step 3: Run test to verify it passes**

Run: `pytest tests/test_auto_fix.py -k "caller_problem_type" -v`
Expected: PASS

- [ ] **Step 4: Verify existing tests still pass**

Run: `pytest tests/test_auto_fix.py -v -x`
Expected: all PASS

- [ ] **Step 5: Commit**

```
fix(auto_fix): preserve caller-supplied problem_type through classify() dispatch

AutoFixEngine.fix() was dropping context.problem_type when re-classifying,
causing the source-prefix fallback to resolve the wrong problem_type and
dispatch the wrong strategy. The caller's problem_type is now injected into
context.details before classify() is invoked.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
```

---

## Task 10: auto_fix/__init__.py — Fix 2 of 2: post-fix AST guard uses files[0] only (line 236)

**Files:**
- Modify: `core/auto_fix/__init__.py` (guardrails logic near line 236) and `core/auto_fix/guardrails.py`

**Bug:** `allowed_node_name` is computed from `files[0]` AST only. When a fix legitimately modifies multiple files, files after `files[0]` are checked against `files[0]`'s top-level name and misjudged as unsafe.

**Fix:** For multi-file fixes, check each file's own top-level node name(s) against itself, not against `files[0]`. In `guardrails.py`'s `is_safe_ast_mutate` (or equivalent), change the signature to accept `path: Path` and compute `allowed_node_name` per-file, not global.

- [ ] **Step 1: Write failing test**

```python
def test_postfix_guard_accepts_legitimate_multi_file_fix(tmp_path):
    """A multi-file fix must not be rejected because file[1]'s top-level differs from files[0]'s."""
    from core.auto_fix import guardrails
    # Simulate: files[0]="a.py" (class A), files[1]="b.py" (class B)
    # Both are legitimately modified but have different top-level names.
    # guardrails.is_safe_ast_mutate should not fail for b.py just because
    # its top-level name differs from a.py's.
    ...
```

If the existing test infrastructure is complex, a simpler check: verify the guard function signature accepts a `path` parameter and computes allowed_node per-file.

- [ ] **Step 2: Apply the fix** — restructure `is_safe_ast_mutate` to be per-file.

- [ ] **Step 3: Run test to verify it passes**

- [ ] **Step 4: Verify existing tests still pass**

Run: `pytest tests/test_auto_fix.py -v -x`
Expected: all PASS

- [ ] **Step 5: Commit**

```
fix(auto_fix): compute allowed_node_name per-file in post-fix AST guardrail

Multi-file fixes were incorrectly rejected because only files[0]'s
top-level node name was used to validate all modified files.
The guardrail now checks each file against its own top-level names.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
```

---

## Task 11: agent_spawner.py — Fix 1 of 2: early return before regression-guard block (line 166)

**Files:**
- Modify: `core/agent_spawner.py:155-215`

**Bug:** `TimeoutExpired` and non-zero returncode branches return before the regression-guard block (lines 195-210). Destructive edits by crashed/timeout sub-agents are never checked or logged.

**Fix:** Move the regression-guard check BEFORE the early return statements, so timeout/non-zero branches still go through guard analysis before returning.

```python
# BEFORE (lines ~166-175):
if isinstance(exc, subprocess.TimeoutExpired):
    elapsed = getattr(exc, "stdout", None) or ""
    return {"status": "timeout", ...}   # regression guard MISSED

# AFTER:
# Regression guard runs FIRST, then return.
if isinstance(exc, subprocess.TimeoutExpired) or returncode != 0:
    guard_result = self._dispatch_diff_budget(...)
    if guard_result.get("has_destructive_edit"):
        # Log and flag, but still return the timeout/non-zero status
        self._log_dispatch(...)
```

Also update `_log_dispatch` call sites to pass `status="timeout"` or `status="error"` when returning early.

- [ ] **Step 1: Write failing test** (mock a timed-out subprocess, verify regression_guard is called)

```python
def test_timeout_triggers_regression_guard(monkeypatch):
    """TimeoutExpired must still run regression guard before returning."""
    import core.agent_spawner as ag
    guard_called = []
    orig = ag.AgentSpawner._dispatch_diff_budget
    def tracking_guard(self, *a, **k):
        guard_called.append(True)
        return orig(self, *a, **k)
    monkeypatch.setattr(ag.AgentSpawner, "_dispatch_diff_budget", tracking_guard, raising=False)
    # Mock subprocess.run to raise TimeoutExpired
    ...
    spawner = ag.AgentSpawner(project=Path("/tmp"), agent_config={...})
    result = spawner.spawn(...)
    assert guard_called, "regression guard was not called for timeout"
```

Run: `pytest tests/test_agent_spawner.py -k "timeout" -v`
Expected: FAIL before fix.

- [ ] **Step 2: Apply the fix** — reorder so regression guard runs before early return.

- [ ] **Step 3: Run test to verify it passes**

- [ ] **Step 4: Verify existing tests still pass**

Run: `pytest tests/test_agent_spawner.py -v -x`
Expected: all PASS

- [ ] **Step 5: Commit**

```
fix(agent_spawner): run regression guard before early timeout/non-zero return

Regression guard analysis was skipped when a sub-agent timed out or
crashed because the return statement preceded the guard block.
The guard is now evaluated before returning in all error branches.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
```

---

## Task 12: agent_spawner.py — Fix 2 of 2: _log_dispatch called before status override (line 202)

**Files:**
- Modify: `core/agent_spawner.py:195-215`

**Bug:** `_log_dispatch` is called while `parsed["status"]` is still `"complete"`, before being overridden to `"REGRESSION_GUARD"`. `sessions_spawn.log` therefore never records the actual guard-triggered status.

**Fix:** Call `_log_dispatch` AFTER building the final return dict with the correct status.

```python
# BEFORE (line ~202):
_log_dispatch(parsed, ...)   # status still "complete"
# ...
parsed["status"] = "REGRESSION_GUARD"  # too late

# AFTER:
# Build return dict first, then log it
return_dict = {
    "status": "REGRESSION_GUARD",
    ...
}
_log_dispatch(return_dict, ...)   # status is now REGRESSION_GUARD
return return_dict
```

- [ ] **Step 1: Write failing test** (mock regression trigger, verify log contains "REGRESSION_GUARD")

- [ ] **Step 2: Apply the fix**

- [ ] **Step 3: Run test to verify it passes**

- [ ] **Step 4: Verify existing tests still pass**

Run: `pytest tests/test_agent_spawner.py -v -x`
Expected: all PASS

- [ ] **Step 5: Commit**

```
fix(agent_spawner): log REGRESSION_GUARD status, not the pre-override "complete"

_log_dispatch was called before the status was overridden to
REGRESSION_GUARD, making sessions_spawn.log entries for guard-triggered
dispatches indistinguishable from normal dispatches.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
```

---

## Task 13: phase_hooks.py — Fix 1 of 2: re-verify skips overlay (line 469)

**Files:**
- Modify: `core/phase_hooks.py:469-475`

**Bug:** `check_traceability()` is called without re-applying the `TRACEABILITY_MATRIX.overlay.yaml`, so manually-VERIFIED FRs reappear as untested.

**Fix:** Load and apply the overlay before re-verifying:
```python
# AFTER (lines ~469-475):
from core.traceability.overlay import load_overlay, apply_overlay
overlay = load_overlay(self.project_path / "TRACEABILITY_MATRIX.overlay.yaml")
raw = self._check_traceability()   # or whatever the raw call is
if overlay:
    raw = apply_overlay(raw, overlay)
report2 = raw   # use the overlay-applied result for still_untested
```

If `check_traceability` doesn't support overlay application internally, the fix must be applied externally as shown.

- [ ] **Step 1: Write failing test** (set overlay to mark FR-07 VERIFIED, run re-verify, assert FR-07 not in still_untested)

- [ ] **Step 2: Apply the fix**

- [ ] **Step 3: Run test to verify it passes**

- [ ] **Step 4: Verify existing tests still pass**

Run: `pytest tests/test_phase_hooks.py -v -x`
Expected: all PASS

- [ ] **Step 5: Commit**

```
fix(phase_hooks): re-apply TRACEABILITY_MATRIX.overlay.yaml after auto-fix re-verify

Previously, re-verification after auto-fix was called without the overlay,
causing manually-VERIFIED FRs to reappear as untested. The overlay is now
re-applied to the raw traceability report before computing still_untested.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
```

---

## Task 14: phase_hooks.py — Fix 2 of 2: substring orphan detection (line 746)

**Files:**
- Modify: `core/phase_hooks.py:746`

**Bug:** `'KOKORO_BACKEND_URL' in decl_text` is True when the declared key is `LEGACY_KOKORO_BACKEND_URL_V1` (substring match, not exact match).

**Fix:** Use exact key comparison:
```python
# BEFORE:
orphaned = {k: v for k, v in env_vars.items() if k not in decl_text}

# AFTER:
declared_keys = set(decl_text.split())  # or parse .env format properly
orphaned = {k: v for k, v in env_vars.items() if k not in declared_keys}
```

Also parse `.env.example` properly (it uses `KEY=value` format, not just space-delimited tokens) using `python-dotenv` or a simple line parser.

- [ ] **Step 1: Write failing test**

```python
def test_orphan_detector_not_fooled_by_substring(tmp_path):
    """A key that is a substring of a declared key must be flagged as orphaned."""
    from core.phase_hooks import PhaseHooks
    # Create a fake .env.example with LEGACY_KOKORO_BACKEND_URL_V1
    # and code that reads KOKORO_BACKEND_URL
    # ...
    hooks = PhaseHooks(project_path=str(tmp_path), phase=1)
    # KOKORO_BACKEND_URL should be orphaned
    ...
```

- [ ] **Step 2: Apply the fix** — parse declared keys exactly.

- [ ] **Step 3: Run test to verify it passes**

- [ ] **Step 4: Verify existing tests still pass**

Run: `pytest tests/test_phase_hooks.py -v -x`
Expected: all PASS

- [ ] **Step 5: Commit**

```
fix(phase_hooks): use exact key matching for env var orphan detection

Substring matching caused an undeclared key that happened to be a
substring of a declared key to be incorrectly treated as declared.
Keys are now compared exactly against the parsed .env.example.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
```

---

## Task 15: spec_tracking_checker.py — Fix NFR fail-open (line 336)

**Files:**
- Modify: `core/quality_gate/spec_tracking_checker.py:330-345`

**Bug:** NFR scan exception handling leaves `nfr_pct = 100.0` (fail-open), unlike sibling dimensions 4a/4b which fail-closed.

**Fix:**
```python
# BEFORE (in the except block for NFR scan):
nfr_pct = 100.0   # silently pass

# AFTER (in the except block for NFR scan):
nfr_pct = 0.0     # fail-closed: treat unreadable/malformed as 0% coverage
result["passed"] = False
result["error"] = result.get("error", []) + [f"NFR scan failed: {exc}"]
```

Also add a comment explaining why this differs from 4a/4b's behavior and why fail-closed is correct here.

- [ ] **Step 1: Write failing test** (mock NFR scan to raise, verify result["passed"] is False, not True)

- [ ] **Step 2: Apply the fix**

- [ ] **Step 3: Run test to verify it passes**

- [ ] **Step 4: Verify existing tests still pass**

Run: `pytest tests/ -k "spec_tracking" -v -x`
Expected: all PASS

- [ ] **Step 5: Commit**

```
fix(spec_tracking_checker): make NFR-coverage (4c) fail-closed like 4a/4b

A raise during NFR scan was silently treated as 100% coverage (nfr_pct=100.0),
letting malformed/unreadable SRS scans pass Gate 2-4. Now sets nfr_pct=0.0
and result["passed"]=False, consistent with sibling dimension behavior.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
```

---

## Task 16: cross_artifact.py — Fix 1 of 2: regex matches wrong coverage figure (line 198)

**Files:**
- Modify: `core/quality_gate/cross_artifact.py:198`

**Bug:** `re.search(r"[^\\d]*([\\d]+)", ...)` greedily matches to the first percentage in the file, not the "line coverage" value.

**Fix:** Constrain the match to only the line containing "line coverage":
```python
# BEFORE:
m = re.search(r"Coverage[^\\d]*([\\d]+)", text)   # greedy

# AFTER:
# Match only on the "line coverage" line to avoid picking up target/other %
m = re.search(r"(?im)^Line coverage[^\\d]*([\\d]+)", text)
```

- [ ] **Step 1: Write failing test** (create a file with "Coverage target: 80%\nActual coverage: 95%\n" — verify 95% is extracted, not 80)

```python
def test_coverage_regex_picks_line_coverage_not_first_number(tmp_path):
    """Must extract the line-coverage figure, not the first percentage in the file."""
    from core.quality_gate.cross_artifact import check_coverage_report
    content = "Coverage target: 80%\nActual coverage achieved: 95%\n"
    (tmp_path / "COVERAGE_REPORT.md").write_text(content)
    violations = check_coverage_report(tmp_path / "COVERAGE_REPORT.md", actual_coverage=95.0)
    # The extracted claimed coverage must be 95, not 80
    assert not any("80" in str(v) for v in violations), f"Wrong coverage figure extracted: {violations}"
```

Run: `pytest tests/test_cross_artifact.py -k "coverage_regex" -v`
Expected: FAIL before fix (regex extracts 80 → violation raised).

- [ ] **Step 2: Apply the fix**

- [ ] **Step 3: Run test to verify it passes**

Run: `pytest tests/test_cross_artifact.py -k "coverage_regex" -v`
Expected: PASS

- [ ] **Step 4: Verify existing tests still pass**

Run: `pytest tests/test_cross_artifact.py -v -x`
Expected: all PASS

- [ ] **Step 5: Commit**

```
fix(cross_artifact: check_coverage_report: extract "line coverage" specifically

The regex was matching the first percentage in the file, which could be
a target value rather than the actual achieved line coverage. Now anchors
to the "Line coverage" line to extract the correct figure.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
```

---

## Task 17: cross_artifact.py — Fix 2 of 2: bare percentage claims never matched (line 196)

**Files:**
- Modify: `core/quality_gate/cross_artifact.py:196`

**Bug:** The regex requires "coverage"/"covered" before the number, so a bare "Overall: 85%" is silently ignored — no violation is ever raised even if fabricated.

**Fix:** Add a second pattern for bare percentages that appear in common report formats:
```python
# Keep the existing "coverage N%" pattern, and ADD:
# Bare percentage: "Overall: 85%" or "Total: 95%"
bare_m = re.search(r"(?im)^(?:Total|Overall|Average)[:\s]+(\d+)(?:\s*%)?", text)
if bare_m and not claimed_match:
    claimed = float(bare_m.group(1))
```

- [ ] **Step 1: Write failing test** (file with "Overall: 85%" as only coverage claim → should extract 85 and compare to actual)

- [ ] **Step 2: Apply the fix**

- [ ] **Step 3: Run test to verify it passes**

- [ ] **Step 4: Verify existing tests still pass**

Run: `pytest tests/test_cross_artifact.py -v -x`
Expected: all PASS

- [ ] **Step 5: Commit**

```
fix(cross_artifact): also match bare "Overall/ Total: N%" coverage claims

Bare percentages in common report formats (Overall: 85%) were silently
ignored, letting fabricated coverage claims pass the gate. A secondary
pattern now catches these bare-percentage formats.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
```

---

## Task 18: phase_truth_verifier.py — Fix self.results never assigned (line 56)

**Files:**
- Modify: `core/quality_gate/phase_truth_verifier.py:56`

**Bug:** `self.results` initialized to `{}` in `__init__` but `verify()` only creates a local `results` list, never assigning back to `self.results`. `to_fix_context()` always reads `{}`.

**Fix:**
```python
# BEFORE (inside verify()):
results = []
for check in self._checks:
    ...

# AFTER:
self.results = []   # assign to instance variable, not local
for check in self._checks:
    ...
```

Also add `from __future__ import annotations` if not present, and add a type annotation for `self.results`.

- [ ] **Step 1: Write failing test**

```python
def test_verify_assigns_self_results(tmp_path):
    """verify() must assign to self.results, not a local variable."""
    from core.quality_gate.phase_truth_verifier import PhaseTruthVerifier
    # Create a project with verifiable content
    verifier = PhaseTruthVerifier(project_root=tmp_path, phase=1)
    result = verifier.verify()
    assert hasattr(verifier, "results"), "verifier.results attribute missing"
    # If verify() returned non-empty failures, self.results must reflect them
    if result.get("failing_checks"):
        assert verifier.results, f"verify() returned failures but self.results={verifier.results}"
```

Run: `pytest tests/test_phase_truth_verifier.py -v`
Expected: FAIL before fix (verifier.results is empty)

- [ ] **Step 2: Apply the fix** — change `results = []` to `self.results = []` inside `verify()`.

- [ ] **Step 3: Run test to verify it passes**

Run: `pytest tests/test_phase_truth_verifier.py -v`
Expected: PASS

- [ ] **Step 4: Verify existing tests still pass**

Run: `pytest tests/test_phase_truth_verifier.py -v -x`
Expected: all PASS

- [ ] **Step 5: Commit**

```
fix(phase_truth_verifier): assign verify() results to self.results not a local variable

to_fix_context() reads self.results to build FixContext, but verify()
was using a local variable. Now correctly assigns to self.results so
the AutoFix integration path works as documented.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
```

---

## Task 19: submodule_sync.py — Fix 1 of 2: KeyError from message template (line 244)

**Files:**
- Modify: `core/submodule_sync.py:244`

**Bug:** `_cli` only catches `SubmoduleSyncError`. After `sync_submodule()` succeeds, `message_template.format(short_sha=...)` raises `KeyError` if the template contains unknown placeholders. This is not a `SubmoduleSyncError` and propagates uncaught, leaving the submodule bumped but parent commit unwritten.

**Fix:**
```python
# BEFORE:
try:
    new_sha = sync_submodule(...)
except SubmoduleSyncError:
    ...
# message_template.format() is outside the try/except

# AFTER:
try:
    new_sha = sync_submodule(...)
    if message_template:
        message_template.format(short_sha=new_sha)  # let KeyError propagate
except SubmoduleSyncError:
    ...
except KeyError as exc:
    print(f"ERROR: message template contains unknown placeholder: {exc}")
    return 19
```

- [ ] **Step 1: Write failing test** (pass `--message 'bump {version}'` which has unknown placeholder)

```python
def test_unknown_template_placeholder_returns_19():
    """Unknown {placeholder} in --message must return exit code 19, not raise KeyError."""
    import core.submodule_sync as ms
    result = ms._cli(argv=["--submodule", "harness", "--message", "bump {version}"])
    assert result == 19, f"Expected exit 19 for unknown placeholder, got {result}"
```

Run: `pytest tests/test_submodule_sync.py -k "template" -v`
Expected: FAIL before fix (raises KeyError, not exit 19)

- [ ] **Step 2: Apply the fix**

- [ ] **Step 3: Run test to verify it passes**

Run: `pytest tests/test_submodule_sync.py -k "template" -v`
Expected: PASS

- [ ] **Step 4: Verify existing tests still pass**

Run: `pytest tests/test_submodule_sync.py -v -x`
Expected: all PASS

- [ ] **Step 5: Commit**

```
fix(submodule_sync): catch KeyError from message template and return exit 19

Unknown {placeholder} in --message raised an uncaught KeyError after the
submodule was already bumped, leaving the parent-repo commit unwritten.
KeyError is now caught and reported as exit code 19.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
```

---

## Task 20: submodule_sync.py — Fix 2 of 2: TimeoutExpired not caught (line 268)

**Files:**
- Modify: `core/submodule_sync.py:268`

**Bug:** `_run()` has a 60s timeout, but parent-repo commit/push error handling only catches `subprocess.CalledProcessError`. `TimeoutExpired` is not a subclass of `CalledProcessError`, so it propagates uncaught.

**Fix:**
```python
# BEFORE:
except subprocess.CalledProcessError as exc:
    print("FAILED: parent-repo commit/push failed")

# AFTER:
except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
    if isinstance(exc, subprocess.TimeoutExpired):
        print("FAILED: parent-repo push timed out after 60s")
    else:
        print(f"FAILED: parent-repo commit/push failed: {exc}")
```

- [ ] **Step 1: Write failing test** (mock subprocess.run to raise TimeoutExpired for push)

```python
def test_push_timeout_returns_19_not_unhandled(tmp_path, monkeypatch):
    """git push timeout must return exit 19, not raise TimeoutExpired."""
    import core.submodule_sync as ms
    def fake_run(*a, **k):
        raise subprocess.TimeoutExpired(cmd="git push", timeout=60)
    monkeypatch.setattr(ms.subprocess, "run", fake_run)
    result = ms._cli(["--submodule", "harness", "--message", "bump {sha}"])
    assert result == 19, f"Expected 19 for timeout, got {result}"
```

Run: `pytest tests/test_submodule_sync.py -k "timeout" -v`
Expected: FAIL before fix

- [ ] **Step 2: Apply the fix** — add `subprocess.TimeoutExpired` to exception tuple.

- [ ] **Step 3: Run test to verify it passes**

Run: `pytest tests/test_submodule_sync.py -k "timeout" -v`
Expected: PASS

- [ ] **Step 4: Verify existing tests still pass**

Run: `pytest tests/test_submodule_sync.py -v -x`
Expected: all PASS

- [ ] **Step 5: Commit**

```
fix(submodule_sync): catch TimeoutExpired alongside CalledProcessError

git push exceeding the 60s hardcoded timeout raised TimeoutExpired,
which is not a subclass of CalledProcessError and propagated uncaught.
TimeoutExpired is now caught and reported as exit code 19.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
```

---

## Task 21: bug_hunt_verifier.py — Fix 1 of 2: TypeError when repro_test is not a string (line 130)

**Files:**
- Modify: `core/quality_gate/bug_hunt_verifier.py:130`

**Bug:** `(root / repro).is_file()` where `repro` is an int/list/dict raises `TypeError: unsupported operand type(s) for /: 'PosixPath' and 'int'`. Uncaught here, and swallowed by `harness_bridge.py`'s broad `except Exception`.

**Fix:**
```python
# BEFORE:
(root / repro).is_file()

# AFTER:
if not isinstance(repro, str):
    result["error"] = result.get("error", []) + [
        f"repro_test must be a string path, got {type(repro).__name__}: {repro!r}"
    ]
    return False
if not (root / repro).is_file():
    ...
```

- [ ] **Step 1: Write failing test**

```python
def test_repro_test_non_string_returns_error_not_typeerror():
    """repro_test of type int must be flagged as invalid, not raise TypeError."""
    from core.quality_gate.bug_hunt_verifier import verify_bug_hunt_report
    report = {
        "findings": [{
            "severity": "high",
            "status": "confirmed",
            "resolution": {"status": "resolved", "repro_test": 123}  # wrong type
        }]
    }
    # Must not raise TypeError; should return error in result
    result = verify_bug_hunt_report(Path("/tmp"), report)
    errors = result.get("error", [])
    assert any("repro_test" in str(e) for e in errors), f"Expected repro_test error, got: {errors}"
```

Run: `pytest tests/test_bug_hunt_verifier.py -k "repro_test" -v`
Expected: FAIL before fix (raises TypeError)

- [ ] **Step 2: Apply the fix** — validate repro_test type before path concatenation.

- [ ] **Step 3: Run test to verify it passes**

Run: `pytest tests/test_bug_hunt_verifier.py -k "repro_test" -v`
Expected: PASS

- [ ] **Step 4: Verify existing tests still pass**

Run: `pytest tests/test_bug_hunt_verifier.py -v -x`
Expected: all PASS

- [ ] **Step 5: Commit**

```
fix(bug_hunt_verifier): handle non-string repro_test without TypeError

repro_test of non-string type (int, list, etc.) caused a TypeError
when concatenating with root Path. Now validated before use and reported
as a structured error in the result.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
```

---

## Task 22: bug_hunt_verifier.py — Fix 2 of 2: AttributeError when resolution is truthy non-dict (line 111)

**Files:**
- Modify: `core/quality_gate/bug_hunt_verifier.py:111`

**Bug:** `resolution = finding.get("resolution") or {}` only substitutes falsy values. A truthy string `"see PR #42"` survives the `or {}` check, then `.get("status")` raises `AttributeError`.

**Fix:**
```python
# BEFORE:
resolution = finding.get("resolution") or {}

# AFTER:
_resolution = finding.get("resolution")
if not isinstance(_resolution, dict):
    result["error"] = result.get("error", []) + [
        f"resolution must be a dict, got {type(_resolution).__name__}: {_resolution!r}"
    ]
    resolution = {}
else:
    resolution = _resolution
```

- [ ] **Step 1: Write failing test**

```python
def test_resolution_truthy_string_not_cause_attributeerror():
    """resolution as a truthy string must be flagged as type error, not raise AttributeError."""
    from core.quality_gate.bug_hunt_verifier import verify_bug_hunt_report
    report = {
        "findings": [{
            "severity": "high",
            "status": "confirmed",
            "resolution": "see PR #42"  # wrong type: should be dict
        }]
    }
    result = verify_bug_hunt_report(Path("/tmp"), report)
    errors = result.get("error", [])
    assert any("resolution" in str(e) for e in errors), f"Expected resolution type error, got: {errors}"
```

Run: `pytest tests/test_bug_hunt_verifier.py -k "resolution" -v`
Expected: FAIL before fix (raises AttributeError)

- [ ] **Step 2: Apply the fix** — add isinstance check for resolution.

- [ ] **Step 3: Run test to verify it passes**

Run: `pytest tests/test_bug_hunt_verifier.py -k "resolution" -v`
Expected: PASS

- [ ] **Step 4: Verify existing tests still pass**

Run: `pytest tests/test_bug_hunt_verifier.py -v -x`
Expected: all PASS

- [ ] **Step 5: Commit**

```
fix(bug_hunt_verifier): validate resolution is a dict before accessing .get()

A truthy-string resolution ("see PR #42") bypassed the `or {}` check
and raised AttributeError when .get("status") was called. Now validated
with isinstance check and reported as a structured error.

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
```

---

## Task 23: harness_bridge.py — Suppress duplicate review: NFR fail-open caller-level mitigation

**Files:**
- Modify: `harness/harness_bridge.py` (if harness_bridge has a caller-level catch for `spec_tracking_checker` that also needs to be updated)

**Note:** This task is informational only — after Task 15 fixes `spec_tracking_checker.py:336` to be fail-closed, the caller-level mitigation in `harness_bridge.py` should be reviewed. If there is any code in `harness_bridge.py` that catches `spec_tracking_checker` exceptions broadly and treats them as advisory warnings rather than blocking errors, verify it now matches the fail-closed fix. **If no such caller exists, mark this task complete with no changes.**

- [ ] **Step 1: Check** `grep -n "spec_tracking\|spec.*check\|compute_trace_dimension" harness/harness_bridge.py`

- [ ] **Step 2:** If caller-level mitigation exists, update it to match Task 15's fail-closed behavior. If not, no action needed.

- [ ] **Step 3: Commit (if changes needed)** with description of the caller-level update.

---

## Task 24: Verify all 25 fixes pass together

**Files:**
- All files modified above

- [ ] **Step 1: Run full test suite**

Run: `pytest tests/test_mutation_enforcer.py tests/test_auto_fix.py tests/test_agent_spawner.py tests/test_phase_hooks.py tests/test_cross_artifact.py tests/test_submodule_sync.py tests/test_bug_hunt_verifier.py tests/test_canonical_lint.py -v --tb=short 2>&1 | tail -50`

Expected: all PASS, no regressions.

- [ ] **Step 2: Commit all remaining changes with a final summary commit**

```
fix(core): address all 25 CRG-confirmed bugs from docs/CORE_BUG_HUNT_REPORT.md

25 confirmed bugs across 10 core/ files, identified via CRG-guided
dynamic workflow sweep (2026-07-01):

- mutation_enforcer: paths_to_mutate validation, mutmut returncode check,
  sqlite corrupt cache detection, stale cache cleanup, multi-value
  testpaths/pythonpath, cwd-aware config resolution
- constitution/profile+runner: active_dimensions keyword filtering,
  P5-P8 deliverable glob, is_markdown flag propagation, stale P3 comment
- auto_fix: problem_type preservation through classify(), per-file AST guard
- agent_spawner: regression guard on timeout/error paths, REGRESSION_GUARD log
- phase_hooks: overlay re-application after auto-fix, exact env key matching
- spec_tracking_checker: NFR fail-closed instead of fail-open
- cross_artifact: line-coverage-specific regex, bare percentage matching
- phase_truth_verifier: self.results assigned from verify()
- submodule_sync: KeyError from message template, TimeoutExpired catch
- bug_hunt_verifier: repro_test type validation, resolution type validation

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>
```
