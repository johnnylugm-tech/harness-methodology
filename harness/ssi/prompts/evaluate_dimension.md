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

> **If all variants return 0% or fail**: evaluation is **SUSPENDED** for `test_coverage`.
> Do NOT write a score file. Fix the pytest/coverage configuration and restart from Step 1.
> (`tool_score=null` is not accepted for Tier 1 — score.py R8 will block gate scoring.)

### test_assertion_quality (Tier 2)
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
# C2: verify availability BEFORE assigning any score
command -v mutmut >/dev/null 2>&1 || pip3 install mutmut --quiet

# Only run if now available:
if command -v mutmut >/dev/null 2>&1; then
  # Auto-configure paths_to_mutate so mutmut can find code in non-standard layouts
  # (e.g. 03-development/src/ instead of src/)
  _mutmut_needs_config=false
  if [ -f setup.cfg ]; then
    grep -q '\[mutmut\]' setup.cfg || _mutmut_needs_config=true
  else
    _mutmut_needs_config=true
  fi
  if [ "$_mutmut_needs_config" = true ] && ! grep -q '\[tool\.mutmut\]' pyproject.toml 2>/dev/null; then
    _paths=""
    for _d in 03-development/src src lib app; do
      [ -d "$_d" ] && _paths="${_paths},${_d}"
    done
    if [ -n "$_paths" ]; then
      printf '[mutmut]\npaths_to_mutate=%s\n' "${_paths#,}" >> setup.cfg
    fi
    unset _paths _d
  fi
  unset _mutmut_needs_config

  # Workaround: editable install (pip install -e) places a .pth file in
  # site-packages pointing to the original source directory. When mutmut
  # copies code to /tmp/mutmut-* and mutates it, Python resolves imports
  # via the .pth file back to the ORIGINAL (unmutated) code — mutations
  # are never tested. Temporarily switch to a regular install.
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

  timeout $TIME_BUDGET mutmut run 2>&1
  mutmut results 2>&1 | head -100

  # Restore editable install if we switched it
  # Note: $_editable_pkgs must be UNQUOTED here so shell word-splits it
  # into separate package name arguments (quoted form passes the whole
  # space-separated string as one argument, causing uninstall to fail).
  if [ "$_restore_editable" = true ]; then
    pip uninstall $_editable_pkgs -y --quiet 2>/dev/null
    pip install -e . --quiet 2>/dev/null
  fi
fi
```

> **If mutmut is unavailable after install attempt**: evaluation is **SUSPENDED** for
> `mutation_testing`. Do NOT write a score file with `tool_score=null` — score.py R8 will
> block gate scoring. Fix the install (check Python version, virtualenv, editable-install
> conflicts) and restart from Step 1. `run-gate` also pre-checks mutmut availability before
> printing the evaluation prompt — if that check passed, re-run `pip3 install mutmut`.
>
> **objective_primary**: mutation_testing has `objective_primary: true` in config. The mutmut
> score (survived/killed ratio) IS the authoritative score. When writing the score file,
> include `"objective_primary": true` in the JSON. The llm_score can only reduce the final
> score below the mutmut score (via R4 `min()`), never increase it. If llm_score deviates
> from tool_score by >10, score.py R8b emits a warning.
>
> Example: mutmut reports 25% survived → tool_score=75. llm_score=80 is capped to 75.
> llm_score=60 is accepted (score becomes 60) but R8b warns about the >10-point deviation.

### architecture (Tier 3)
```bash
radon cc src/ -j --min A 2>&1 | head -200
```
**Score formula:** `tool_score = max(0, 100 - count(CC > 10) × 5)` — count entries in JSON output where `complexity > 10`. Functions with CC ≤ 10 are acceptable (grade A/B); C+ (>10) each deduct 5 points.

### readability (Tier 3)
```bash
radon mi src/ -j 2>&1 | head -100
```
**Score formula:** `tool_score = avg(mi for all files)` — `radon mi -j` outputs `{"file.py": {"mi": 0-100, "rank": "A-F"}}`. Average the `mi` values across all files. This is already a 0-100 maintainability index.

### error_handling (Tier 3)
```bash
grep -rn --include="*.py" "except\s*:" src/ 2>&1 | head -100
```
**Score formula:** `tool_score = max(0, 100 - match_lines × 5)` — count non-empty output lines. Each bare `except:` (with no exception type) deducts 5 points.

### documentation (Tier 3)
```bash
pydocstyle src/ --count 2>&1 | head -100
interrogate src/ -v 2>&1 | head -100
```
**Score formula:** `tool_score = max(0, 100 - violations × 2)` — `--count` appends a final line `"N violations found"`; parse N. Each docstring violation deducts 2 points.

### performance (Tier 3)
```bash
radon cc src/ -j --min A 2>&1 | head -100
```
**Score formula:** `tool_score = max(0, 100 - count(CC > 10) × 5)` — same as `architecture`. Both dimensions use radon cc; the distinction lies in findings context (architecture focuses on coupling/layering, performance on hot-path bottlenecks).

---

## Step 2: Compute Score and Gather Findings

**`score = tool_score` for every dimension. LLM does not compute or adjust the numeric score.**

1. Apply the scoring formula from Step 1 to the tool output → `tool_score`
2. Extract findings directly from tool violations / output lines
3. (Optional) Query CRG for structural context to enrich findings *descriptions* — CRG data may not change the score

### Tier 1/2: parse tool output directly

Score = formula from Step 1. Findings = each tool violation becomes one finding entry.

### Tier 3: use CRG to enrich findings (annotation only — not scoring)

```bash
cat .sessi-work/crg_status.json
# {"available": true, ...} OR {"available": false, ...}
```

If `available: false` → skip CRG; extract findings from raw tool output only.

If `available: true`:

```
[USE mcp__code-review-graph__get_minimal_context_tool]
task: "evaluate <dimension> dimension"
```

Use CRG data to describe findings more precisely — cite `file:line`, `fan_in`, `cohesion` — but **do not change `tool_score`**. CRG context improves finding quality, not the numeric score.

**CRG tool → finding mapping (for annotation context):**

| Dimension | CRG tool | Finding context |
|-----------|----------|----------------|
| `architecture` | `get_hub_nodes`, `list_communities`, `get_community` | Name hub nodes / low-cohesion communities in findings evidence |
| `readability` | `find_large_functions`, `get_hub_nodes` | Cite function name + LOC + fan_in |
| `performance` | `get_hub_nodes`, `list_flows` | Cite hot-path depth + hub fan_in |
| `error_handling` | `get_affected_flows`, `semantic_search_nodes "except"` | Cite flow name + missing handler step |
| `documentation` | `get_hub_nodes`, `get_wiki_page` | Undocumented hub nodes = highest-priority gaps |

**CRG sub-score pull-down (score.py _apply_crg_subscores):**

`score.py` may further lower the tool score using CRG structural signals:
- `architecture` ← `min(tool_score, community_cohesion.score)`
- `error_handling` ← `min(tool_score, flow_coverage.score)`

This is applied automatically by `score.py`; you do not need to do it manually.

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
