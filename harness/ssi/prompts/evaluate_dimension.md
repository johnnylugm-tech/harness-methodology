# Evaluate Dimension Protocol

Evaluate a single quality dimension using **pure tool scoring**.
`score = tool_score` for every dimension and every tier. LLM does not compute or adjust the numeric score.

---

## Execution Contract (強制，每次執行前確認)

> **這是行為紅線宣告，不可跳過。違反任一項，本步驟結果視為無效。**
>
> ❌ **禁止行為：**
> - 未執行工具指令就填寫 `tool_score`（估分 = 造假）
> - findings[] 中填入無 `file:line` 或工具輸出支撐的項目
> - 任何維度以 `tool_score: null` 提交 score 文件（工具未安裝 → 評估 SUSPENDED）
> - 用 LLM 評估結果覆蓋或調整 `score`（score 只能等於 tool_score）
>
> ✅ **每個 score 文件必須滿足：**
> - `tool_outputs`: 指向實際執行工具的輸出檔（不得為 null）
> - `tool_score`: 工具輸出計算出的 0-100 分數
> - `score`: 必須等於 `tool_score`（score.py R4 機器驗證）
> - **若工具未安裝 → 評估 SUSPENDED。禁止寫入 score 文件。修復環境後從 Step 1 重新開始。**

---

## Step 1: Run Tools

Run all tools for this dimension. Save raw output:

```bash
# Output path: .sessi-work/round_<n>/tools/<dimension>.txt
```

**Tool commands by dimension:**

### linting (Tier 1)
```bash
pylint src/ --output-format=json 2>&1 | head -200
eslint src/ --format json 2>&1 | head -200
```

### type_safety (Tier 1)
```bash
pyright src/ --outputjson 2>&1 | head -200
```
**Score formula:** `tool_score = max(0, 100 - summary.errorCount × 5)` — parse `summary.errorCount` from JSON output.

### test_coverage (Tier 1)
```bash
# C1: retry with PYTHONPATH=. if default run returns 0% or fails (import errors)
coverage run -m pytest && coverage report --format=json \
  || PYTHONPATH=. coverage run -m pytest && coverage report --format=json \
  || PYTHONPATH=. python3 -m pytest --cov=. --cov-report=term-missing

# JS/TS:
nyc --reporter=json npm test
```

**Automatic CRG enrichment** (added to `test_coverage.issues` at finalize-gate):
- `get_knowledge_gaps` → severity: medium (untested critical paths detected by CRG)
- `query_graph(tests_for)` on hub functions (fan_in≥8) → severity: high (hub with no test linkage)
- `tier3_context.test_coverage.knowledge_gaps` also injected into prepare_gate prompt context
  for Gates 3/4 where `crg.tier3_guidance` or `crg.reconnaissance` is enabled.

These are **advisory findings** — they do NOT change the tool-scored coverage percentage.

> **If all variants return 0% or fail**: evaluation is **SUSPENDED** for `test_coverage`.
> Do NOT write a score file. Fix the pytest/coverage configuration and restart from Step 1.
> (`tool_score=null` is not accepted for Tier 1 — score.py R8 will block gate scoring.)

### test_assertion_quality (Tier 2 — framework tool: ast-assertions)

> Framework-scored: during S4 the harness runs `ast-assertions` and scores
> `100 × asserted_tests / total_tests` — a test function with NO substantive
> assertion (`assert` / `self.assertXxx` / `pytest.raises`) is "zero-assert" and
> lowers the score. Your recorded score is cross-checked; fabrication is blocked.
> The snippet below reproduces the analysis for findings (its density value is
> diagnostic only — the gate uses asserted/total).

```bash
# Assertion density & zero-assert detection via Python AST.
# No external tool needed — uses stdlib ast.
python3 -c "
import ast, sys
from pathlib import Path
test_dir = Path('tests')
zero_assert = []
total_funcs = 0
total_asserts = 0
for f in sorted(test_dir.rglob('*.py')):
    try:
        tree = ast.parse(f.read_text())
        funcs = [n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name.startswith('test_')]
        total_funcs += len(funcs)
        for n in funcs:
            ca = sum(1 for s in ast.walk(n) if isinstance(s, ast.Assert))
            total_asserts += ca
            if ca == 0:
                zero_assert.append(f'{f.name}::{n.name}')
    except SyntaxError:
        pass
density = total_asserts / max(total_funcs, 1)
zr = len(zero_assert) / max(total_funcs, 1)
print(f'assertion_density={density:.2f} zero_assert_ratio={zr:.3f}')
if zero_assert:
    for z in zero_assert[:20]:
        print(f'  ZERO-ASSERT: {z}')
score = min(100, density * 25 + (1 - zr) * 50)
print(f'tool_score={score:.0f}')
"
```

### security (Tier 2)
```bash
bandit -r src/ -f json --exit-zero 2>&1 | head -300
npm audit --json 2>&1 | head -200
```
**Score formula:** `tool_score = max(0, 100 - HIGH×10 - MEDIUM×3 - LOW×1)` — count items in `results[]` by `issue_severity`.

### secrets_scanning (Tier 1)
```bash
gitleaks detect --source . --report-format json
detect-secrets scan . --baseline .secrets.baseline
```

### license_compliance (Tier 1)
```bash
scancode --license --json-pp - src/ | head -300
```

### mutation_testing (Tier 1)
```bash
# mutmut was pre-verified by run-gate (_verify_gate_tools). No install needed here.
# REQUIRED: mutmut 2.x (pip install 'mutmut<3').
# mutmut 3.x uses a trampoline mechanism incompatible with most project layouts
# (projects with src/ layout or editable installs crash or produce all exit_code=-11).

_PROJECT_ROOT=$(pwd)

# Auto-configure paths_to_mutate with ABSOLUTE paths so mutmut can find source
# code from any working directory (we cd to a temp dir to avoid sys.path[0]
# contamination — see workaround 2 below).
# mutmut 2.x reads setup.cfg [mutmut] only (NOT pyproject.toml [tool.mutmut]).
_mutmut_needs_config=false
if [ -f setup.cfg ]; then
  grep -q '\[mutmut\]' setup.cfg || _mutmut_needs_config=true
else
  _mutmut_needs_config=true
fi
if [ "$_mutmut_needs_config" = true ]; then
  _paths=""
  for _d in 03-development/src src lib app; do
    # Skip symlinks — a symlink src→03-development/src would double-count
    # the same files, causing every mutation to run twice.
    [ -d "$_d" ] && [ ! -L "$_d" ] && _paths="${_paths},$_PROJECT_ROOT/$_d"
  done
  if [ -n "$_paths" ]; then
    printf '[mutmut]\npaths_to_mutate=%s\n' "${_paths#,}" >> setup.cfg
  fi
  unset _paths _d
fi
unset _mutmut_needs_config

# Auto-configure [tool:pytest] testpaths with ABSOLUTE paths. Workaround 2
# (cwd isolation) runs pytest from _MUTMUT_WORKDIR — a temp dir with no test
# files. Without absolute testpaths, pytest discovers 0 tests → every mutant
# "survives" → kill rate = 0%. Also fixes harness-internal test discovery
# (e.g. harness/tests/) which would cause runner failures from project root.
# If testpaths exists but is relative (no leading '/'), overwrite with absolute.
_tp_val=$(python3 -c "
import configparser, sys
c = configparser.RawConfigParser()
c.read('setup.cfg')
try:
    print(c.get('tool:pytest', 'testpaths').strip())
except (configparser.NoSectionError, configparser.NoOptionError):
    print('')
" 2>/dev/null)
if [ -z "$_tp_val" ] || ! echo "$_tp_val" | grep -q '^/'; then
  _tpaths=""
  for _td in tests 03-development/tests test; do
    [ -d "$_td" ] && _tpaths="${_tpaths:+$_tpaths }$_PROJECT_ROOT/$_td"
  done
  if [ -n "$_tpaths" ]; then
    python3 -c "
import re, sys
tpaths = sys.argv[1]
text = open('setup.cfg').read()
if re.search(r'testpaths\s*=', text):
    text = re.sub(r'(testpaths\s*=\s*)[^\n]*', r'\g<1>' + tpaths, text, count=1)
elif re.search(r'\[tool:pytest\]', text):
    text = re.sub(r'(\[tool:pytest\])', r'\1\ntestpaths=' + tpaths, text, count=1)
else:
    text += '\n[tool:pytest]\ntestpaths=' + tpaths + '\n'
open('setup.cfg', 'w').write(text)
" "$_tpaths"
  fi
  unset _tpaths _td
fi
unset _tp_val

# mutmut 2.x workaround 1: editable install (pip install -e) places a .pth file in
# site-packages pointing to the original source directory. When mutmut 2.x
# copies mutated code to a temp dir, Python resolves imports via the .pth file
# back to the ORIGINAL (unmutated) source — mutations are never tested.
# Temporarily switch to a regular (non-editable) install before running mutmut.
_editable_pkgs=$(pip list --editable --format json 2>/dev/null | python3 -c \
"import sys,json; data=json.load(sys.stdin); print(' '.join(d['name'] for d in data))")
_restore_editable=false
if [ -n "$_editable_pkgs" ]; then
  for _pkg in $_editable_pkgs; do
    pip uninstall "$_pkg" -y --quiet 2>/dev/null
  done
  pip install . --quiet 2>&1 || true
  _restore_editable=true
fi

# mutmut 2.x workaround 2: sys.path[0] = cwd always wins the first import
# lookup. If cwd contains src/, Python resolves imports to the ORIGINAL source
# before mutmut's mutated copy — every mutant survives. Run from a temp dir
# that does NOT contain src/ so mutmut's mutated copy (on sys.path) resolves
# first. Requires namespace packages (no src/__init__.py) so Python walks all
# sys.path entries rather than locking to a regular package.
_MUTMUT_WORKDIR=$(mktemp -d /tmp/_mutmut_run.XXXXXX)
cp "$_PROJECT_ROOT/setup.cfg" "$_MUTMUT_WORKDIR/"
cd "$_MUTMUT_WORKDIR"

# -b 10 sets baseline time budget to 10s. Without it, mutmut 2.x measures the
# test suite's own runtime as baseline (~0.05-0.5s), then marks ANY mutant whose
# test run exceeds 10× baseline as timeout. Subprocess overhead alone exceeds
# that threshold → every mutant times out → kill rate = 0%.
timeout $TIME_BUDGET mutmut run -b 10 2>&1
mutmut results 2>&1 | head -100
# Legend: 🎉=killed (good)  🙁=survived (needs investigation)  ⏰=timeout  🤔=suspicious  🔇=skipped

# Data-only files (constants, dictionaries, Pydantic models) have no logic
# to mutate — every mutant survives, diluteing the kill rate. Exclude them
# via paths_to_exclude so the score reflects real mutation resistance.
# Add AFTER the auto-config block above (mutmut reads setup.cfg top-to-bottom;
# the [mutmut] section must already exist before paths_to_exclude is appended):
if ! grep -q 'paths_to_exclude' setup.cfg; then
  echo "  Review setup.cfg [mutmut] paths_to_mutate and add paths_to_exclude for:"
  echo "  - config.py, constants.py (pure data, no logic to mutate)"
  echo "  - Pydantic/attrs model files (field declarations only)"
  echo "  Example: add 'paths_to_exclude=config.py,taiwan_linguistic.py' under [mutmut]"
  echo "  Then re-run: mutmut run -b 10 && mutmut results"
fi

# Return to project root and clean up temp dir
cd "$_PROJECT_ROOT"
rm -rf "$_MUTMUT_WORKDIR"
unset _MUTMUT_WORKDIR _PROJECT_ROOT

# Restore editable install if we switched it
# Note: $_editable_pkgs must be UNQUOTED here so shell word-splits it
# into separate package name arguments (quoted form passes the whole
# space-separated string as one argument, causing uninstall to fail).
if [ "$_restore_editable" = true ]; then
  pip uninstall $_editable_pkgs -y --quiet 2>/dev/null
  pip install -e . --quiet 2>/dev/null
fi

```

> **If mutmut is somehow unavailable at execution time**: evaluation is **SUSPENDED** for
> `mutation_testing`. Do NOT write a score file with `tool_score=null` — score.py R8 will
> block gate scoring. Re-run `run-gate` which will detect the missing tool and block before
> printing the evaluation prompt. All required tools must be installed via `init-project`
> before starting the project.
>
> **objective_primary**: mutation_testing has `objective_primary: true` in config. The mutmut
> score (survived/killed ratio) IS the authoritative score. When writing the score file,
> include `"objective_primary": true` in the JSON. The `score` field must equal `tool_score`
> (score.py R4 enforces this — LLM annotation cannot adjust the numeric score). The
> `llm_score` field is recorded for annotation purposes only; it does not affect gate scoring.
> score.py R8b deviation warning was removed when LLM scoring was abolished.
>
> **Score formula**: `tool_score = round(killed / (killed + survived) × 100, 1)`. Parse
> `mutmut results` output: sum 🎉 across all files as `killed`, sum 🙁 as `survived`.
> ⏰ (timeout) and 🤔 (suspicious) count as survived — they were not killed by tests.
> If no mutants were produced (0 killed + 0 survived), score = 0 (not 100).
>
> **Equivalent mutants**: some code changes produce identical behaviour — no test can kill
> them. Do NOT chase every survived mutant as a coverage gap. Signs of equivalence:
> string literal changes in dead-code paths, constant values never read, timeout values
> passed to in-process functions. If `mutmut apply <id> && pytest` still passes, the
> mutant is equivalent. Exclude confirmed equivalent mutants from the denominator:
> `adjusted = round(killed / (killed + survived − confirmed_equivalent) × 100, 1)`.
> Use the raw score for the gate; note the adjusted score and equivalent IDs in `gaps`.

### architecture (Tier 3 — CRG-ONLY, framework-owned)

**Scored by the framework's OWN independent CRG run — not the LLM, not the agent.**
At finalize-gate the harness itself runs `code-review-graph build` + `postprocess`
(via `harness/crg_independent.py`) and computes `community_cohesion.score` — percent of
healthy communities (cohesion >= 0.3 AND size <= 50). Test-only communities
(name starts with `tests`/`test`) are automatically excluded from scoring —
test files have no structural dependency edges with each other and always form
an oversized zero-cohesion blob under directory-based grouping. Whatever value
you record is **overwritten** by this framework-computed score, so do not
fabricate it.

`code-review-graph` is a REQUIRED component (like ruff/mypy); a missing binary BLOCKS
the gate (verified at run-phase preflight — no graceful degradation).

Findings enrichment (via CRG MCP tools — does not affect the score):
```bash
# list_communities / get_community — name low-cohesion communities in findings evidence
```

**Automatic framework enrichment** (added to `architecture.issues` by `_crg_enrich_gate_findings`
at finalize-gate — visible in `.sessi-work/gate{N}_result.json`):
- `find_large_functions` (≥300 lines) → severity: medium (refactoring advisory)
- `get_hub_nodes` (fan_in≥15) → severity: high (single-point failure risk)
- `refactor_tool(dead_code)` (>10 items) → severity: medium (dead code ratio)
- `get_review_context` / `get_impact_radius` / `get_affected_flows` → `crg_review_context` /
  `crg_impact_radius` / `crg_affected_flows` fields (review context for the current gate)

These are **advisory findings only** — they enrich the evidence but do NOT change the
`community_cohesion.score`. The architecture score remains fully framework-controlled.

#### ⚠ Orchestrator Pattern False Positive (Expected CRG Score: 0)

If your project has a single hub module (`pipeline.py`, `main.py`, `app.py`, etc.)
that imports from ≥5 sub-packages, CRG Leiden algorithm will report `score = 0` because:
- The hub creates a star topology → Leiden treats the whole codebase as one large community
- `cohesion < 0.4` is expected (hub fan_out >> leaf fan_in ≤ 1)
- This is **NOT** architectural debt — it is a valid hub-and-spoke / orchestrator design

**Detection**: `list_communities` shows 1 large community (size > 50) AND
`get_hub_nodes` shows 1 node with fan_out > 8, all other nodes fan_in ≤ 2.

**Gate 3 path**: Complete the Devil's Advocate challenge (verify_round.md Step 4) and
document that the orchestrator pattern is intentional. Gate 3 will block on score — note
the justification in findings and proceed to Gate 4 with DA evidence.

**Gate 4 path**: Set both fields in `gate4_result.json`:
```json
"devil_advocate": {"architecture": true, ...},
"da_waiver":      {"architecture": true}
```
The harness `finalize_gate()` will bypass the architecture score threshold when both
`devil_advocate.architecture` and `da_waiver.architecture` are `true`.

> **Structural defence only (A3):** the `devil_advocate.architecture` evidence
> (challenge + response prose) is agent-authored — the harness checks it is present and
> non-trivial, not that the reasoning is *true*. The bypass is nonetheless safe because
> the architecture **score itself** comes from the framework independently re-running CRG
> (`harness/crg_independent.py`), not from the agent; the waiver only zeroes the threshold
> for a known orchestrator false-positive, and `finalize_gate` marks it
> `da_waiver_needs_human_review = true`. The prose is a documentation artifact, not a
> correctness guarantee.

### readability (Tier 3 — proxy metric: radon-mi)

```bash
radon mi src/ -j 2>&1 | head -100
```
**Score formula:** `tool_score = avg(mi for all files)` — `radon mi -j` outputs `{"file.py": {"mi": 0-100, "rank": "A-F"}}`. Average the `mi` values across all files.

> **Proxy caveat:** maintainability-index (MI) is an *approximation* of readability, not
> a direct measure — it weighs Halstead volume / cyclomatic complexity / LOC, which
> correlate with but do not equal human readability. It is kept as the best automatable
> signal. When there is **no analysable source file**, the harness returns *no score*
> (not 100), and S4 cross-validation blocks an agent score it cannot independently
> reproduce — a missing metric is never silently treated as a pass.

### error_handling (Tier 3 — tool-scored: ast-error-handling)

**Scored by the framework, not the LLM and not CRG.** The harness scans the source
tree (`03-development/src`, `src`) and computes **file-level error-handling coverage**:
the percentage of source files (that contain code) with at least one real `try/except`
handler block.

`error_handling.score = round(100 × files_with_handler / total_source_files, 1)`

This replaces the former CRG flow `has_error_handler` path — that field does **not
exist** in the installed code-review-graph package. S4 cross-validation re-runs this
AST scan independently, so a fabricated score is blocked.

- Files with no functions/classes (e.g. empty `__init__.py`) are excluded.
- A file counts as handled if it contains any `try/except` with handlers (bare-except
  *quality* is a separate concern; this metric is coverage of error handling).
- Files containing `# pragma: no error-handling` are EXEMPT — excluded from the
  denominator entirely. Use for Pydantic models, data-only classes, and pure
  pass-through files that legitimately have no I/O or external calls to handle.

**Fix priority**: source files performing I/O, network, database, or external-service
calls with no `try/except` anywhere in the file. For files that genuinely cannot fail
(data models, config constants), add `# pragma: no error-handling` instead of adding
pointless try/except blocks.

### documentation (Tier 3 — tool-scored: ast-docstrings)

**Scored by the framework, not the LLM and not CRG.** The harness scans the source
tree (`03-development/src`, `src`) and computes **public-API docstring coverage**:
the percentage of public `def`/`class` (names not starting with `_`) that carry a
docstring (`ast.get_docstring`).

`documentation.score = round(100 × public_with_docstring / total_public, 1)`

This replaces the former `pydocstyle`/`interrogate` proxy (a style/existence count the
agent could satisfy with trivial one-line stubs). S4 cross-validation re-runs this AST
scan independently, so a fabricated score is blocked.

- A project with no public API (`total_public == 0`) scores 100 (nothing to document).
- `_`-prefixed (private) symbols and nested defs are excluded.

### performance (Tier 3 — tool-scored: pytest-benchmark)

**Scored by the framework via `pytest-benchmark` (measured latency), not radon.** The
harness runs the benchmark suite and scores real mean latencies:
```bash
pytest 03-development/tests --benchmark-only --benchmark-disable-gc --benchmark-columns mean,max --tb no -q
```
**Score formula:** start at 100; per benchmark, `mean > 3000 ms → −50`, `mean > 1000 ms → −25`, otherwise no penalty. **No benchmark tests collected** (pytest exit 5) → score is *None* (dimension not yet applicable — not a free 100). S4 cross-validation re-runs this independently, so a fabricated score is blocked.

> Add `pytest-benchmark` micro-benchmarks (functions taking the `benchmark` fixture) for hot-path code so this dimension produces a real score rather than being skipped.

---

## Step 2: Compute Score and Gather Findings

**`score = tool_score` for every dimension. LLM does not compute or adjust the numeric score.**

1. Apply the scoring formula from Step 1 to the tool output → `tool_score`
2. Extract findings directly from tool violations / output lines
3. (Optional) Query CRG for structural context to enrich findings *descriptions* — CRG data may not change the score

### Tier 1/2: parse tool output directly

Score = formula from Step 1. Findings = each tool violation becomes one finding entry.

### Tier 3: CRG-scored dimensions vs tool-scored dimensions

**CRG-ONLY dimension** (architecture):
- Score comes from the framework's OWN independent CRG run (`harness/crg_independent.py`
  at finalize) → `community_cohesion.score`. It OVERWRITES any agent-recorded value.
- LLM does NOT compute or adjust the numeric score for this dimension
- `code-review-graph` is a required component; missing → gate BLOCKED (no degradation)

**Tool-scored Tier 3 dimensions** (readability, documentation, performance, error_handling):
- `error_handling` → `ast-error-handling` (file-level try/except coverage, framework AST)
- `documentation` → `ast-docstrings` (public-API docstring coverage, framework AST)
- `readability` → `radon-mi` (maintainability-index *proxy*; no analysable file → no score, not 100)
- `performance` → `pytest-benchmark` (measured benchmark latency; no benchmark tests → no score, not 100)
- Score = formula from Step 1 tool output
- Optionally query CRG (`get_minimal_context_tool`) to enrich finding descriptions
- CRG data enriches findings but does not change the score

> **NFR-backed dimensions (SAB)**: when the project's SAB maps an NFR to a dimension —
> `performance` / `security` / `readability`(maintainability) / `error_handling`(reliability) /
> `test_assertion_quality`(testability) — that dimension carries a `gate_score_overrides`
> threshold *floor* (raised, never lowered, not waivable). Treat NFR-mapped dimensions as
> non-negotiable: clear at least the floor. NFR types `deployability`/`scalability`/`usability`
> have no scoring tool → advisory-only (human review, not gated).

**For all Tier 3 dimensions**, first check CRG is available:
```bash
cat .sessi-work/crg_status.json
# {"available": true, ...}  — required; if available: false, BLOCK (CRG is mandatory)
```

CRG tool → finding mapping (for annotation context):

| Dimension | CRG tool | Finding context |
|-----------|----------|----------------|
| `architecture` | `get_hub_nodes`, `list_communities`, `get_community` | Name hub nodes / low-cohesion communities in findings evidence |
| `readability` | `find_large_functions`, `get_hub_nodes` | Cite function name + LOC + fan_in |
| `performance` | `get_hub_nodes`, `list_flows` | Cite hot-path depth + hub fan_in |
| `error_handling` | `get_affected_flows`, `semantic_search_nodes "except"` | Cite flow name + missing handler step |
| `documentation` | `get_hub_nodes`, `get_wiki_page` | Undocumented hub nodes = highest-priority gaps |

---

## Step 3: Write Score File

Save to `.sessi-work/round_<n>/scores/<dimension>.json`:

```json
{
  "dimension": "<name>",
  "round": <n>,
  "tool_score": <0-100>,
  "score": <equals tool_score>,
  "tool_outputs": "<path to raw tool output file>",
  "findings": [
    {
      "file": "<path|null>",
      "line": <int|null>,
      "severity": "critical|high|medium|low|info",
      "message": "<description>",
      "evidence": "<tool output excerpt or file:line>"
    }
  ],
  "gaps": ["<gap 1>", "<gap 2>"]
}
```

**Required fields (R1 enforced by score.py):** `dimension`, `round`, `tool_score`, `score`, `tool_outputs`. Missing any → `ScoreProtocolError` raised, gate score cannot be computed.

**Field contract:**
- `tool_score: null` → **REJECTED** by score.py R8 for **all** tiers. If tool is unavailable, evaluation is SUSPENDED — fix the tool environment, then re-evaluate.
- `score` → MUST equal `tool_score` (R4 auto-fixed and machine-enforced)
- `findings[].evidence` → MUST be non-empty for every finding (R5 enforced)

**Optional annotation fields** (not used in scoring, kept for human review):
- `llm_score`, `llm_tier`, `llm_provider` — may be included as annotations; score.py ignores them for scoring

**Severity canonicalization:** the registry requires one of `critical|high|medium|low|info`.
Map legacy tool outputs as: `error/critical → critical`, `warning → medium`, `info/note → info`,
known CVE high severities → `high`.

---

## Step 4: Register Findings in the Issue Registry

**Every finding from Step 3 MUST be written to the persistent issue registry.**
This is what makes the tool issue-driven (not score-driven): issues persist across rounds
until explicitly `fixed`, `deferred` (with reason), or `wontfix` (with reason).

For each finding in the score file:

```bash
# Write the finding to a temp JSON file with the exact keys: severity, message, file, line, evidence
echo '{"severity":"high","message":"...","file":"src/foo.py","line":42,"evidence":"..."}' \
  > /tmp/finding.json

python3 scripts/issue_tracker.py add \
  .sessi-work/issue_registry.json \
  <dimension> \
  <round_num> \
  /tmp/finding.json
```

Or equivalently, batch via a small loop over the `findings[]` array in `<dimension>.json`.

**Idempotency guarantee:** the registry hashes `(dimension, file, line, message[:80])` into a
deterministic ID, so repeating the same finding in round 2 updates `last_seen_round` rather than
duplicating the entry.

After Step 4 completes for all dimensions, print a registry summary:

```bash
python3 scripts/issue_tracker.py summary .sessi-work/issue_registry.json
```

The `open_critical` / `open_high` / `open_medium` counts feed directly into
`score.py` and the Step 3e early-stop decision in `SKILL.md`.

---

## Anti-Bias Rules (All Tiers)

1. `score = tool_score` — LLM cannot adjust the numeric score, no exceptions
2. Every finding needs `evidence` field — no bare assertions
3. If tool is missing or gives no output → evaluation **SUSPENDED** for this dimension, **all tiers**. Do not write score file. Fix the tool environment and restart from Step 1. (score.py R8 enforces this at machine level.)
4. Δ > 10 from previous round requires tool evidence or ≥ 3 lines of git diff
5. Trust the tool output — findings descriptions may be enriched with CRG context, but the score is the tool score
