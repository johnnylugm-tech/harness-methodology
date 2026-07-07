# Integration Guide — harness-methodology

> **Scope**: How to maintain the framework itself, and how to wire it into a target development project.
> For gate embedding and SSI evaluation model, see [`docs/HARNESS_INTEGRATION.md`](docs/HARNESS_INTEGRATION.md).
>
> **Last verified**: 2026-05-12 &nbsp;|&nbsp; **Synced with**: SAD.md v2.8.0

---

## 1. Two-Context Model

```
harness-methodology (this repo)          Your Target Project (any repo)
──────────────────────────────────       ─────────────────────────────────
Framework source + CI self-tests         Your code + harness installed as dep
.github/workflows/harness_ci.yml  ←→    .github/workflows/harness_quality_gate.yml
scripts/ (tools to run elsewhere)  →     .git/hooks/ (installed via setup.sh)
```

**Rule**: Never mix the two. harness-methodology's CI tests the framework. Your project's CI runs the framework against your code.

---

## 2. Context A — Framework Self-Maintenance (this repo)

### 2.1 GitHub Actions: `.github/workflows/harness_ci.yml`

Triggers on push/PR to `main`.

| Job | What it does |
|---|---|
| `framework-self-tests` | `ruff check .` → `pytest tests/ -v --tb=short` |

Both steps are blocking — lint or test failure blocks the PR.

### 2.2 Release Scripts

```bash
# Run pre-release validation
python scripts/list-modules.py --validate
python scripts/validate_cross_refs.py

# Run full test suite
python -m pytest tests/ -v --tb=short

# Tag and push (CI takes over — see .github/workflows/release.yml)
git tag v2.7.0
git push origin v2.7.0

# Regenerate machine-readable SAD block
python scripts/generate_sab.py --project .
```

---

## 3. Context B — Target Project Integration

### 3.0 JS/TS Target Projects (v2.8.0+)

針對 javascript / typescript 目標專案,在 §3.1 之前先完成:

```bash
# 1. init-project 會偵測語言(tsconfig.json → typescript;package.json → javascript;
#    歧義時 --language 明示)並寫入 .methodology/state.json,同時:
#    - merge 釘版 devDependencies(templates/js_toolchain/package.json;既有版本優先)
#    - 複製 eslint.config.mjs / vitest.config.ts / stryker.conf.json /
#      benchmarks/run.mjs / tsconfig(.checkjs).json(已存在則跳過)
python3 harness/harness_cli.py init-project --project . --phase 1

# 2. 安裝釘版工具鏈 — gate 命令一律 `npx --no-install`,沒裝就 fail-loud:
npm ci

# 3. harness 端 Python 依賴(semgrep + tree-sitter 掃描器):
pip install -r harness/requirements.txt

# 4. 驗證工具齊備(per-language 分流):
python3 harness/ssi/scripts/verify_tools.py --core --project .
```

硬性慣例(gate 會 enforce):
- 測試標題 `it('test_frNN_xxx', ...)` / `test('test_frNN_xxx', ...)`;
  檔案 `tests/test_frNN_*.test.<ext>`(D4 spec-coverage 與 trace 都靠這個)
- 純 JS 專案必須留 `tsconfig.checkjs.json`(type_safety 維度跑 `tsc --checkJs`)
- `stryker.conf.json` 必須保留 json reporter(mutation precheck 讀
  `reports/mutation/mutation.json`)
- TEST_SPEC.md 的 sub-assertion 謂詞仍是 Python 表達式語法(spec 層慣例)

### 3.1 Step 1: Make harness Importable in Target Repo

The git hooks and CI workflow call `harness_cli.py` — the `core/` and `harness/` packages must be importable. Three options:

**Option A — Git submodule (recommended for teams)**
```bash
cd your-project
git submodule add https://github.com/johnnylugm-tech/harness-methodology harness
pip install -r harness/requirements.txt
```
Entry point: `harness/harness_cli.py`. Python path: `harness/` root on `sys.path`.

> **`requirements.txt` contents** (repo-bundled, not generated): `pyyaml>=6.0` (gate config loading — required), `pytest>=7.0` (framework self-tests). The harness operates inline as a zero-dependency system. If `requirements.txt` is missing (e.g. Option C copy forgot to include it), `pip install -r ... || true` in CI will silently succeed, and `run-gate` will later fail with `ModuleNotFoundError: No module named 'yaml'`.

**Option B — Direct clone alongside project**
```bash
git clone https://github.com/johnnylugm-tech/harness-methodology /opt/harness
export PYTHONPATH="/opt/harness:$PYTHONPATH"
```
Entry point: `/opt/harness/harness_cli.py`.

**Option C — Copy `core/` and `harness/` into project**
```bash
cp -r harness-methodology/core your-project/core
cp -r harness-methodology/harness your-project/harness
cp -r harness-methodology/scripts your-project/scripts
cp harness-methodology/harness_cli.py your-project/
```
Simplest for single-developer setups; requires manual updates when framework changes.

> **Why copy `scripts/`**: CI Option C YAML runs `python scripts/check_fr_full.py` for FR traceability checks. `check_fr_full.py` lives in the repo's root `scripts/` directory — it is **not** inside `harness/`. Without copying `scripts/`, the CI step silently skips (masked by `continue-on-error: true`) and FR traceability is never verified.

### 3.2 Step 2: Install Git Hooks

Run from the **target project** root (not from harness-methodology):

```bash
bash /path/to/harness-methodology/scripts/setup-git-hooks.sh
```

Interactive prompts:
- Current phase (1-8) -> stored in `.methodology/state.json` (`current_phase` field)
- Enable block on failure? (y/n)

Installed hooks:

| Hook | Trigger | Behavior |
|---|---|---|
| `prepare-commit-msg` | `git commit` | **Blocks** if `harness_cli.py pre-commit-check --phase $PHASE` fails |
| `post-merge` | `git merge` | Warns only — runs `pre-commit-check --phase $PHASE` (non-blocking) |
| `pre-push` | `git push` | **Blocks** — runs full `run-phase --phase $PHASE` (no bypass) |

**Phase management**:
```bash
# Advance phase (updates state.json):
python harness_cli.py advance-phase --completed 2 --project .
# Check current phase:
python3 -c "import json; print(json.load(open('.methodology/state.json'))['current_phase'])"
```

> **No bypass mechanism exists for git hooks.** If `run-phase` fails before push, fix the underlying issue. Use `git commit --no-verify` only as a last resort for emergency hotfixes; CI will detect the missing sentinel on the next push audit.

### 3.3 Drift Protection

Drift detection runs automatically at every push: `preflight_drift_detection`
(run-phase preflight) and `postflight_drift_check`. The former hourly cron
monitor (`scripts/cron_drift_monitor.py`) was removed in 減法 T4 — it fully
overlapped the per-push checks.

### 3.4 Step 4 (Optional): On-Demand Analysis Scripts

Run from target project root with harness on `PYTHONPATH` (or via submodule).

| Task | CLI command (preferred) | Direct script |
|---|---|---|
| Phase exit audit | `python harness_cli.py audit-phase --phase 3 --repo owner/repo` | `python harness/scripts/phase_auditor.py --phase 3` |
| FR completeness check | — | `python harness/scripts/check_fr_full.py --phase 3` |
| FR quality check | — | `python harness/scripts/check_fr_quality.py` |
| FR -> code trace matrix | — | `python harness/scripts/generate_fr_mapping.py` |
| Generate all 8 phase plans | `python harness_cli.py plan-all --project .` | `python harness/scripts/generate_full_plan.py --phase 3` |
| Load phase context (execution) | `python harness_cli.py load-context --phase 3 --json` | — |
| Generate phase plan (debug only) | `python harness_cli.py plan-phase --phase 3` | `python harness/scripts/generate_full_plan.py --phase 3` |
| Spec compliance (ASPICE) | `python harness_cli.py verify-spec` | `python harness/scripts/verify_spec_compliance.py` |
| Logic correctness check | `python harness_cli.py check-logic --srs SRS.md` | `python harness/scripts/spec_logic_checker.py` |
| M3 gap analysis | `python harness_cli.py run-gap-analysis` | — |
| Path consistency | (enforced by tests/test_no_hardcoded_paths.py + topology anchors) | — |
| State inspection | `python harness_cli.py status` / `doctor` | — |
| Dev log check | — | `python harness/scripts/dev_log_checker.py` |

**Full pipeline execution**:
```bash
# One-time setup (project init)
python harness_cli.py init-project --project . --phase 1
python harness_cli.py plan-all --project .

# Manual step-by-step (recommended)
python harness_cli.py load-context --phase 1 --project . --json > .sessi-work/phase1_ctx.json
python harness_cli.py run-phase --phase 1 --project .
python harness_cli.py load-context --phase 2 --project . --json > .sessi-work/phase2_ctx.json
python harness_cli.py run-phase --phase 2 --project .
python harness_cli.py load-context --phase 3 --project . --json > .sessi-work/phase3_ctx.json
python harness_cli.py run-phase --phase 3 --project .
python harness_cli.py finalize-gate --gate 2 --phase 3 --project .
# Continue for P4–P8 with same pattern
```

**One-shot project initialization** (automates Steps 1-4 above):
```bash
python harness_cli.py init-project --project /path/to/target --phase 3
```

---

## 4. Target Project CI (Recommended GitHub Actions)

> **The YAML below targets Option A (submodule).** For Option B (global clone) and Option C (copy), see the variant blocks at the end of this section. Running `harness_cli.py init-project` auto-generates the correct YAML for your install option.
>
> **CI scope — structural enforcement only**: CI runs `run-phase` (FSM / constitution / drift / traceability) and gate score variance check. Gate score evaluation (LLM-based, `run-gate` → Claude → `finalize-gate`) requires an interactive Claude session and is **always local**.
>
> **Single source of truth**: The YAML below is kept in sync with `templates/harness_quality_gate.yml` and `_harness_workflow_template()` in `harness_cli.py` (used by `init-project`).

> **Gate 4 is a phase-exit gate.** Run Gate 4 manually at P6 exit: `python harness_cli.py run-gate --gate 4 --phase 6 --project .` followed by `python harness_cli.py finalize-gate --gate 4 --phase 6 --project .`.

> **Branch protection** — configure in GitHub repo Settings → Branches → Add rule (branch: `main`):
> - ✅ Block force pushes
> - ✅ Block deletions
> - ❌ Do NOT enable "Require a pull request" — harness uses Agent B review + direct push, incompatible with PR gating
> - ❌ Do NOT enable "Require status checks" — only gates PR merges, not direct pushes

### Option A — Submodule (recommended)

The canonical workflow is at `templates/harness_quality_gate.yml` — `init-project` deploys it directly.
The template contains these jobs (all trigger on `push: branches: [main]`):

| Job | Purpose |
|-----|---------|
| `gate-check` | Phase preflight + gate score variance + FR traceability |
| `push-milestone-enforcement` | Blocks raw `git push` for P3+ (requires `push-milestone`) |
| `p1p2-enforcement` | Blocks raw `git push` for P1/P2 (requires `push-checkpoint`) |
| `agent-b-approval-check` | Verifies Agent B APPROVE files for P3+ |
| `p8-archive-check` | Validates `.methodology-archive/` + no Phase 9 refs |

> See `templates/harness_quality_gate.yml` for the full YAML. The table above is a structural reference only — the template is the single source of truth.

### Option B — Global clone

```yaml
      - name: Install harness
        run: |
          git clone --depth 1 https://github.com/johnnylugm-tech/harness-methodology /opt/harness
          pip install -r /opt/harness/requirements.txt || true
          pip install pyyaml 2>/dev/null || true

      - name: Auto-detect phase from state.json
        id: phase
        run: |
          PHASE=$(python3 -c "
          import json, sys
          try:
              d = json.load(open('.methodology/state.json'))
              print(d.get('current_phase', d.get('phase', 1)))
          except Exception:
              print(1)
          " 2>/dev/null || echo "1")
          echo "PHASE=$PHASE" >> $GITHUB_OUTPUT

      - name: Run Quality Gate (current phase)
        if: steps.phase.outputs.PHASE != '6'
        env:
          PHASE: ${{ steps.phase.outputs.PHASE }}
          PYTHONPATH: /opt/harness
        run: python /opt/harness/harness_cli.py run-phase --phase $PHASE --project .

      - name: FR Traceability Check
        env:
          PYTHONPATH: /opt/harness
          PHASE: ${{ steps.phase.outputs.PHASE }}
        run: python /opt/harness/scripts/check_fr_full.py --phase $PHASE
        continue-on-error: true
```

> **Option B differences from template**: Replace `harness/harness_cli.py` → `python /opt/harness/harness_cli.py` and `harness/requirements.txt` → `/opt/harness/requirements.txt` in all jobs. `push-milestone-enforcement` and `p8-archive-check` are unchanged — they only access `.methodology/` state files.

### Option C — Copy into project

```yaml
      # harness_cli.py and harness/ are already in repo root — no extra install step

      - name: Auto-detect phase from state.json
        id: phase
        run: |
          PHASE=$(python3 -c "
          import json, sys
          try:
              d = json.load(open('.methodology/state.json'))
              print(d.get('current_phase', d.get('phase', 1)))
          except Exception:
              print(1)
          " 2>/dev/null || echo "1")
          echo "PHASE=$PHASE" >> $GITHUB_OUTPUT

      - name: Run Quality Gate (current phase)
        if: steps.phase.outputs.PHASE != '6'
        env:
          PHASE: ${{ steps.phase.outputs.PHASE }}
        run: python harness_cli.py run-phase --phase $PHASE --project .

      - name: FR Traceability Check
        env:
          PHASE: ${{ steps.phase.outputs.PHASE }}
        run: python scripts/check_fr_full.py --phase $PHASE
        continue-on-error: true
```

> **Option C differences from template**: Replace `harness/harness_cli.py` → `python harness_cli.py` and `harness/scripts/` → `scripts/` in all jobs. `push-milestone-enforcement` and `p8-archive-check` are unchanged — they only access `.methodology/` state files.

Phase is auto-detected from `.methodology/state.json` — no GitHub Variable required. `CURRENT_PHASE` Actions variable is no longer used.

---

## 5. Environment Variables Reference

| Variable | Used by | Default | Purpose |
|---|---|---|---|
| `DRIFT_PROJECT_PATH` | `cron_drift_monitor.py` | cwd | Path to target project for drift analysis |
| `PYTHONPATH` | All scripts | — | Must include harness-methodology root if not using submodule |
| `SSI_ROOT` | All scripts | `harness/ssi` | Path to embedded SSI package (auto-detected from harness_cli.py location) |
| `HARNESS_CLAUDE_MODEL` | `llm_router.py` | `claude-sonnet-4-5` | Override Claude model for all dimension evaluation and review (all tiers use Claude). |

> **Reviewer backend**: `harness/reviewer_router.py` uses Claude sub-agent for all A/B reviews (all phases). No Hermes MCP or Gemini CLI MCP configuration required — only the `claude` CLI must be installed.

---

## 5.1 `enforcement.json` Configuration Keys

`.methodology/enforcement.json` (in the **target project**) supports the following keys in addition to the standard gate thresholds:

| Key path | Type | Default | Purpose |
|---|---|---|---|
| `phase_truth.pytest_timeout_seconds` | `int` | `300` | Maximum seconds for pytest subprocess in `PhaseTruthVerifier.check_pytest()` (SG-5). Raise for large test suites; floor is 30s. |
| `hr_overrides.HR-11_phase_truth_threshold` | `int` | `90` | Override the Phase Truth gate threshold (%) for this project (SG-7). Only lower if the project has a documented waiver. |

**Example** (`.methodology/enforcement.json`):
```json
{
  "phase_truth": {
    "pytest_timeout_seconds": 600
  },
  "hr_overrides": {
    "HR-11_phase_truth_threshold": 85
  }
}
```

---

## 5.2 Migration Notes

### CV-1 — `sessions_spawn.log` canonical path (harness v2.7.0+)

The canonical location of the A/B session log changed from `sessions_spawn.log` (project root) to `.methodology/sessions_spawn.log`.

**If your project has an existing `sessions_spawn.log` at the root**:
```bash
mkdir -p .methodology
mv sessions_spawn.log .methodology/sessions_spawn.log
git add .methodology/sessions_spawn.log sessions_spawn.log
git commit -m "chore: migrate sessions_spawn.log to .methodology/ (CV-1)"
```

`SessionsSpawnLogger` writes exclusively to `.methodology/sessions_spawn.log`. The root-level file is no longer consulted.

> **Superseded (anti-fabrication hardening):** the HR-10 entry-count audit and the `PhaseTruthVerifier` session-log check were **removed** — the log is agent-writable and not tamper-evident, so it never independently verified A/B collaboration. The file is now a **non-blocking debug trail**; no gate, finalize, or phase-advance path consults its contents. The path migration above is still worth doing for tooling consistency, but is no longer required for any check to pass.

### SG-11 — ~~`session_id` required in sessions_spawn.log~~ **REMOVED**

This rule is obsolete. `sessions_spawn.log` is no longer enforced (see the Superseded note above), so entries missing `session_id` no longer count as malformed and no repair is required.

---

## 6. Phase Transition Checklist

When moving to the next phase in a target project:

```bash
# 1. Run preflight checks
python harness_cli.py run-phase --phase <current> --project .

# 2. Verify current phase gate passes
python harness_cli.py run-gate --gate <N> --phase <current>

# 3. (P3+) Run M3 gap analysis
python harness_cli.py run-gap-analysis --project .

# 4. (Optional) Run full 8-dimension audit
python harness_cli.py audit-phase --phase <current> --repo owner/repo

# 5. (Optional) Verify spec compliance + logic
python harness_cli.py verify-spec --project .
python harness_cli.py check-logic --project .

# 6. Advance phase — updates .methodology/state.json (single source of truth)
python harness_cli.py advance-phase --completed <current>

# 7. Verify next phase plan exists (pre-generated by plan-all)
ls .methodology/phase$((<next>))_plan.md 2>/dev/null || python harness_cli.py plan-all --project .
```

---

## 8. Bypass-Proof Enforcement (Local + Server-Side)

The harness uses a three-layer defense against `git --no-verify` bypass:

### 8.1 Layer 1 — Local Git Hooks
Installed by `init-project` step [3/11] via `scripts/setup-git-hooks.sh`.  
Blocks commits and pushes that don't meet phase requirements.  
**Bypassable**: `git push --no-verify` skips local hooks. Commits prefixed with `chore(harness):` automatically skip local gate checks to prevent circular blocking when updating the harness submodule.

### 8.2 Layer 2 — ECC Hooks (Claude Code Session Layer)
Installed via `scripts/setup-ecc-hooks.sh`. Blocks `git --no-verify` at the
Claude Code tool-call level before it reaches the shell:
```bash
bash scripts/setup-ecc-hooks.sh            # install
bash scripts/setup-ecc-hooks.sh --verify   # check status
bash scripts/setup-ecc-hooks.sh --uninstall  # remove
```
`init-project` step [9/11] automatically checks ECC hook presence and
offers to run the setup script if missing.

### 8.3 Layer 3 — GitHub Branch Protection (Server-Side — Bypass-Proof)
`init-project` step [10/11] auto-detects `gh` CLI and configures branch
protection on `main`:
- Block force pushes
- Block branch deletions
- No PR requirement (compatible with push-checkpoint direct-push model)

Without `gh`, prints a manual setup guide. This is the **only truly
bypass-proof layer** — GitHub rejects protected branch violations at
the server, before the push is accepted.

Manual setup:
```
GitHub repo → Settings → Branches → Add branch protection rule
  Branch name: main
  ✅ Block force pushes
  ✅ Block deletions
  ❌ Require a pull request (OFF)
  ❌ Require status checks (OFF)
```

Or with `gh` CLI:
```bash
python harness_cli.py init-project --project . --setup-branch-protection
```

---
*See [SAD.md](SAD.md) for full module architecture. See [SKILL.md](SKILL.md) for HR rules and gate thresholds.*
