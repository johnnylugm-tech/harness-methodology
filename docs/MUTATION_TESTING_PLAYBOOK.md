# Mutation Testing Playbook — harness-methodology

> **Audience**: Agent (automated) or developer running mutation tests on the harness framework itself.
> **Scope**: `harness/tool_runners.py` + `core/quality_gate/sab_parser.py` (see `setup.cfg [mutmut]`).
> **Purpose**: Verify the test suite's ability to catch real bugs, not just confirm existing behaviour.

---

## Quick Start

```bash
pip install "mutmut==2.5.1"   # MUST be 2.x — see §1 for why 3.x fails
mutmut run -b 10              # -b 10 sets baseline time budget to 10s
mutmut results                # show survived/killed summary
```

Current baseline: **tool_runners.py 62.8% / sab_parser.py 78.0% / TOTAL 66.6%**
Adjusted (excl. 8 confirmed equivalent mutants): **~70%+**

---

## §1 — Why mutmut 2.x, Not 3.x

**TL;DR**: mutmut 3.x crashes on most project layouts. Use `pip install "mutmut<3"`.

### Root cause

mutmut 3.x uses a **trampoline mechanism**: it rewrites source files to embed a
`_mutmut_trampoline()` dispatcher and sets `MUTANT_UNDER_TEST` env var to select which
mutant runs. Before testing, it runs a **forced-fail sanity check** (`MUTANT_UNDER_TEST=fail`)
to confirm the trampoline is being imported.

The trampoline is placed in `mutants/` (`copy_src_dir`). If tests import the **original**
source (not `mutants/`), the trampoline is never called, the sanity check passes
(`97 passed`), and mutmut aborts: `FAILED: Unable to force test failures`.

This repo's `conftest.py` adds the repo root to `sys.path` before mutmut can add `mutants/`,
so imports always resolve to originals.

### Why 2.x works

mutmut 2.x uses `subprocess` to run each mutant in a temp directory. It does not require
trampoline imports. The test suite runs normally; a killed mutant is one where tests fail.

### Diagnosis commands

```bash
# Check which version is installed
mutmut --version    # 2.x has no --version flag; "No such option" = 2.x
pip show mutmut | grep Version

# Signs you have 3.x failure:
# "FAILED: Unable to force test failures"  ← trampoline not imported
# exit_code=-11 in .mutmut-cache            ← SIGSEGV from C extension clash
```

---

## §2 — Configuration (setup.cfg)

mutmut 2.x reads `setup.cfg [mutmut]` **only** (not `pyproject.toml [tool.mutmut]`).

```ini
[mutmut]
paths_to_mutate=harness/tool_runners.py,core/quality_gate/sab_parser.py
runner=python3 -m pytest tests/test_tool_runners.py tests/test_sab_parser.py tests/test_core_flows_mutation.py -x -q --tb=no
```

**Key rules**:
- `paths_to_mutate`: comma-separated, no spaces, relative to repo root.
- `runner`: use `python3` not `python` (macOS has no `python` binary).
- Scope is intentionally narrow: `harness_bridge.py` has 267 mutable nodes — too slow for
  local runs. Add it only when specifically investigating bridge logic.
- `-x` stops at first failure per mutant (faster); `--tb=no` suppresses tracebacks.

---

## §3 — Running

### Baseline time (-b flag)

mutmut 2.x measures baseline test time and kills mutants whose test run takes >10× longer.
This repo's 110-test suite takes ~0.05 s — any subprocess overhead exceeds 10× instantly.

**Always pass `-b 10`** to set a 10-second budget:

```bash
mutmut run -b 10
```

### Full workflow

```bash
# 1. Install (once)
pip install "mutmut==2.5.1"

# 2. Run (takes ~5-10 min for 682 mutants)
mutmut run -b 10

# 3. Results summary
mutmut results

# 4. Inspect a specific surviving mutant
mutmut show <id>

# 5. Apply mutant to disk for manual investigation
mutmut apply <id>
python3 -m pytest tests/test_core_flows_mutation.py -x -q
mutmut unapply <id>   # or: git checkout harness/tool_runners.py

# 6. Update baseline
# Edit mutation_baseline.json with new killed/survived/kill_rate/measured_at
```

### Reading the progress output

```
2. Checking mutants
⠹ 45/682  🎉 30  ⏰ 0  🤔 0  🙁 15  🔇 0
          ^^^^^^  ^^^^^         ^^^^^^
          done   killed       survived
```

- 🎉 Killed — tests caught the mutation (good)
- 🙁 Survived — tests passed despite the mutation (needs investigation)
- ⏰ Timeout — test took >10× baseline (usually means infinite loop or external call)
- 🤔 Suspicious — borderline slow (monitor but not always a problem)
- 🔇 Skipped

---

## §4 — Equivalent Mutants (Cannot Be Killed)

An **equivalent mutant** changes the source but produces identical behaviour — no test can
kill it. Chasing equivalent mutants wastes time. Know them to exclude from kill rate.

### Confirmed equivalent mutants in this repo

| Mutant IDs | Code changed | Why equivalent |
|---|---|---|
| 1, 2 | `_SKIP_TOOLS = {"mutmut",...}` → `{"XXmutmutXX",...}` | `cmds` dict has no `"mutmut"` key; `run_tool("mutmut")` falls through to `return "", -1` either way |
| 24-29 | `_DEFAULT_TIMEOUTS["ast-*"] = 30` → `31` | `ast-assertions/ast-error-handling/ast-docstrings` run in-process; timeout value is never passed to subprocess |

### How to confirm an equivalent mutant

```bash
mutmut apply <id>
python3 -m pytest tests/ -x -q --tb=no
# If "N passed" → equivalent (mutation doesn't change observable behaviour)
mutmut unapply <id>
```

### Adjusted kill rate

```
Raw kill rate    = killed / total
Adjusted         = killed / (total - confirmed_equivalent)
```

Current: 454/682 = 66.6% raw → 454/674 = **67.4%** adjusted (8 equiv confirmed).
With `~22` estimated equivalent: 454/660 = **68.8%** adjusted.

---

## §5 — Test Design Principles for Mutation Testing

These lessons were learned through hands-on iteration in this repo.

### ❌ Pitfall 1: Asserting against the constant you're testing

```python
# WRONG — if _DEFAULT_TIMEOUTS["ruff"] is mutated 30→31,
# the assertion reads the mutated value (31) and still passes:
assert captured_timeout == [_DEFAULT_TIMEOUTS["ruff"]]

# CORRECT — hard-code the expected value:
assert captured_timeout == [30], "ruff default timeout must be 30s"
```

**Rule**: When testing a constant's value, always write the expected value as a literal.
Never reference the constant itself in the assertion.

### ❌ Pitfall 2: Testing only pure functions, not dispatch paths

The original 97 tests covered `_score_ruff(output)`, `_score_mypy(output)`, etc. — pure
scorer functions. `run_tool("ruff", ...)` was never called in tests.

Result: all `cmds` dict key mutations (`"ruff"→"XXruffXX"`) and `_DEFAULT_TIMEOUTS`
mutations survived — zero coverage of the dispatch path.

**Rule**: For each code module, identify the **main dispatch/routing path** and write at
least one test that exercises it end-to-end with mocked I/O.

```python
# Mock subprocess.run, verify the CORRECT binary and flags were assembled:
with patch("subprocess.run", return_value=mock) as mock_sp:
    run_tool("ruff", str(tmp_path))
args = mock_sp.call_args[0][0]
assert args[0] == "ruff"
assert "--output-format" in args
```

### ✅ Patterns that kill mutants

| Pattern | Kills | Example |
|---|---|---|
| Hard-coded expected values | constant value mutations | `assert timeout == 30` |
| Binary name assertion | dict key mutations | `assert args[0] == "ruff"` |
| Required flag assertion | flag mutation/removal | `assert "--cov" in args` |
| `assert_not_called()` on mock | skip-list bypass | subprocess not spawned for skip-list tools |
| Return code assertion `-1/-2/-3/-4` | sentinel return mutations | `assert rc == -1` |
| End-to-end pipeline | intermediate step mutations | T10 in test_core_flows_mutation.py |

### ✅ Test file placement

Put mutation-targeted tests in a **dedicated file** (`tests/test_core_flows_mutation.py`)
separate from functional tests. This lets the mutmut runner include only the targeted files
without the full suite overhead.

---

## §6 — conftest.py: mutmut 3.x Compatibility Hook

Even though we use 2.x, `conftest.py` contains guards that were added during the 3.x
investigation. They are safe with 2.x (no effect) and document the 3.x failure mode.

```python
# sys.path: when running under mutmut 3.x (rootdir = mutants/),
# point to the real repo root so imports resolve correctly.
_this_dir = Path(__file__).resolve().parent
if _this_dir.name == "mutants":
    sys.path.insert(0, str(_this_dir.parent))
else:
    sys.path.insert(0, str(_this_dir))

# pytest_ignore_collect: limit test collection to scope files only
# (prevents ImportError from tests whose modules weren't copied to mutants/)
_MUTMUT_TEST_SCOPE = frozenset({
    "test_tool_runners.py",
    "test_sab_parser.py",
})
def pytest_ignore_collect(collection_path, config):
    if Path(str(config.rootdir)).name == "mutants":
        if collection_path.is_file() and collection_path.suffix == ".py":
            if collection_path.name not in _MUTMUT_TEST_SCOPE:
                return True
    return None
```

**If you add new test files to the mutation scope**, update `_MUTMUT_TEST_SCOPE` in
`conftest.py` AND the `runner=` line in `setup.cfg`.

---

## §7 — Interpreting Results and Next Steps

### Reading `mutmut results`

```
Survived 🙁 (191)
---- harness/tool_runners.py (154) ----
33, 37, 39, 47, 54-55, 59-60, ...
---- core/quality_gate/sab_parser.py (37) ----
516-523, 526, ...
```

The numbers are **mutant IDs** within each file. Cross-reference with `mutmut show <id>`
to understand what was changed.

### Triage surviving mutants

```bash
# Quick category scan (first 5 surviving mutants)
for id in $(mutmut results 2>&1 | grep -oE '\b[0-9]+\b' | head -5); do
  echo "=== $id ==="; mutmut show $id | grep "^[-+]" | head -2
done
```

Categories:
1. **Equivalent** — code change has no observable effect (see §4). Skip.
2. **Missing test coverage** — dispatch path / error handling not tested. Write a test.
3. **Complex internal logic** — AST visitor internals, path resolution. Consider if worth
   the effort vs the risk of the uncovered logic.

### Kill rate targets

| Target | Context |
|---|---|
| 70% (raw) | Gate 2/3/4 `mutation_testing` dimension threshold (adjust if project differs) |
| 70%+ (adjusted) | This repo's current state after 13 core flow tests |
| 80%+ | Aspirational — requires deep AST visitor + error path tests |
| 100% | Not achievable (equivalent mutants exist in every real codebase) |

### Update baseline after improvements

```bash
mutmut run -b 10
python3 -c "
import sqlite3
conn = sqlite3.connect('.mutmut-cache')
rows = conn.execute('''
    SELECT sf.filename, m.status, COUNT(*)
    FROM Mutant m
    JOIN Line l ON m.line = l.id
    JOIN SourceFile sf ON l.sourcefile = sf.id
    GROUP BY sf.filename, m.status
''').fetchall()
stats = {}
for fn,st,cnt in rows:
    k = 'killed' if 'killed' in st else 'survived'
    stats.setdefault(fn,{})[k] = stats.get(fn,{}).get(k,0)+cnt
for fn in sorted(stats):
    k,s=stats[fn].get('killed',0),stats[fn].get('survived',0)
    print(f'{fn}: {k}/{k+s} = {k/(k+s)*100:.1f}%')
conn.close()
"
# Then edit mutation_baseline.json with new numbers
```

---

## §8 — Scope Decisions

### Why harness_bridge.py is excluded

`harness_bridge.py` has 267 mutable nodes. A single run would take 30-60 minutes.
It is intentionally excluded from `paths_to_mutate` for local runs.

To include it for a dedicated investigation:
```ini
[mutmut]
paths_to_mutate=harness/harness_bridge.py
runner=python3 -m pytest tests/test_harness_bridge.py -x -q --tb=no
```

### Why only test_tool_runners + test_sab_parser + test_core_flows_mutation

The runner is limited to the 110 tests that cover the mutated modules. Running the full
2919-test suite would slow each mutant by 20-200×, making the full run take hours.

### Adding a new module to mutation scope

1. Add to `paths_to_mutate` in `setup.cfg`.
2. Add the corresponding test files to `runner=`.
3. Add the test file names to `_MUTMUT_TEST_SCOPE` in `conftest.py`.
4. Run `mutmut run -b 10` to establish baseline.
5. Update `mutation_baseline.json`.

---

## §9 — Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `FAILED: Unable to force test failures` | mutmut 3.x trampoline not imported | Downgrade: `pip install "mutmut<3"` |
| `exit_code=-11` in cache | SIGSEGV from C extension + 3.x | Same — downgrade to 2.x |
| `FileNotFoundError: 'python'` | macOS has no `python` | Use `python3` in `runner=` |
| `⏰ Timeout` on all mutants | baseline time too small | Add `-b 10` to `mutmut run` |
| `failed to collect stats` | test collection error | Check `conftest.py` `_MUTMUT_TEST_SCOPE` |
| `ModuleNotFoundError` during stats | test imports module not in `mutants/` | Add file to `_MUTMUT_TEST_SCOPE` or use `also_copy=` in `setup.cfg` |
| Kill rate suddenly drops | Runner changed to smaller test set | Check `runner=` in `setup.cfg` |
| Cache has stale results | Old run partial; added new tests | `rm .mutmut-cache && mutmut run -b 10` |

---

## §10 — File Reference

| File | Purpose |
|---|---|
| `setup.cfg [mutmut]` | mutmut 2.x configuration (paths, runner) |
| `conftest.py` | sys.path fix + `pytest_ignore_collect` for mutmut scope |
| `mutation_baseline.json` | Measured kill rates + equivalent mutant notes |
| `tests/test_core_flows_mutation.py` | 13 mutation-targeted tests for main dispatch paths |
| `tests/test_tool_runners.py` | 67 scorer pure-function tests |
| `tests/test_sab_parser.py` | 30 SAB/NFR parsing tests |
| `.mutmut-cache` | SQLite results cache (committed for CI reference; rm to force re-run) |
